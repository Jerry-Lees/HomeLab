#!/usr/bin/env python3
"""
Proxmox VM Deploy Wizard
========================
Interactive wizard to provision, configure, and onboard new QEMU VMs
in a Proxmox cluster using cloud-init images. Handles:
  - Cloud-init OS image selection (Ubuntu 24.04 / 22.04)
  - Node selection (auto picks least-loaded node)
  - Static IP or DHCP via cloud-init
  - SSH key injection via cloud-init (enables key-based Ansible auth)
  - VM creation via Proxmox API (q35, SeaBIOS, x86-64-v2-AES, virtio-scsi-pci)
  - Cloud image download + disk import via SSH to Proxmox node
  - Post-deploy Ansible playbook (tools, SNMP, NTP, qemu-guest-agent, etc.)
  - Ansible inventory update on development server

Requirements:
  pip install -r requirements.txt
  ansible (system package or pip)
  SSH key authorized on all Proxmox nodes (root@proxmoxXX)
"""

# Auto-activate virtualenv so `python3 deploy_vm.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

# ── Deployment log tee ────────────────────────────────────────────────────────
# Must be set up BEFORE importing Rich so every Console() picks it up.
# Skipped for validate/dry-run/preflight — those are read-only checks.
import re as _re, pathlib as _pathlib, datetime as _dt
_SKIP_LOG = {"--validate", "--dry-run", "--preflight", "--help", "--?"}
_deploy_log_path = None
if not any(a in sys.argv for a in _SKIP_LOG):
    _ANSI = _re.compile(r'\x1b(?:\[[0-9;?]*[a-zA-Z]|\][^\x07]*\x07|.)')
    _CR   = _re.compile(r'\r(?!\n)')
    def _clean(s):
        return _CR.sub('', _ANSI.sub('', s))
    class _TeeIO:
        def __init__(self, stream, path):
            self._stream = stream
            self._file   = open(path, "w")
            self._file.write(
                f"Labinator VM Deploy — {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Command: {' '.join(sys.argv)}\n\n"
            )
        def write(self, data):
            self._stream.write(data)
            if not self._file.closed:
                self._file.write(_clean(data))
        def flush(self):
            self._stream.flush()
            if not self._file.closed:
                self._file.flush()
        def isatty(self):   return self._stream.isatty()
        def fileno(self):   return self._stream.fileno()
    _log_dir = _pathlib.Path(__file__).parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _deploy_log_path = _log_dir / "last-deployment.log"
    sys.stdout = _TeeIO(sys.stdout, _deploy_log_path)
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import ipaddress
import socket
import time
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import json
import yaml
import questionary
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from modules.lib import (
    load_config,
    connect_proxmox,
    wait_for_task,
    health_check,
    _check_ipv4,
    validate_config,
    validate_deployment_common,
    run_validate_common,
    resolve_profile,
    dns_precheck,
    run_ansible_add_dns,
    run_ansible_inventory_update,
    run_ansible_post_deploy,
    get_nodes_with_load,
    bytes_to_gb,
    get_next_vmid,
    wait_for_ssh,
    node_ssh_host,
    run_preflight,
    parse_ttl,
    expires_at_from_ttl,
    q,
    pt_text,
    select_nav,
    BACK,
    SKIP,
    run_wizard_steps,
    load_deployment_file,
    prompt_package_profile,
    prompt_extra_packages,
    prompt_node_selection,
    write_history,
    check_vlan_exists,
    resolve_tag_colors,
    apply_tag_colors,
    add_common_deploy_args,
    print_dry_run_header,
    print_dry_run_footer,
    dry_run_validate_and_load,
    write_deployment_file,
    make_common_wizard_steps,
    validate_vm_deployment,
    get_vm_disk_storages,
    get_iso_capable_storages,
    get_storage_iso_path,
    list_cloud_images_on_storage,
    import_cloud_image,
    write_guest_agent_snippet,
    wait_for_guest_agent_ip,
    create_vm,
    configure_vm_disk_and_cloudinit,
    start_vm,
    check_node_resources,
)

console = Console()

# ─────────────────────────────────────────────
# Cloud image catalog
# ─────────────────────────────────────────────

_BUILTIN_CLOUD_IMAGES = [
    {
        "name": "Ubuntu 24.04 LTS (Noble Numbat)",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "filename": "noble-server-cloudimg-amd64.img",
    },
    {
        "name": "Ubuntu 22.04 LTS (Jammy Jellyfish)",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "filename": "jammy-server-cloudimg-amd64.img",
    },
]


def load_cloud_images() -> list[dict]:
    """Load cloud images from cloud-images.yaml next to this script, falling back to built-ins."""
    images_file = Path(__file__).parent / "cloud-images.yaml"
    if images_file.exists():
        with open(images_file) as f:
            data = yaml.safe_load(f)
        if data and "cloud_images" in data and data["cloud_images"]:
            return data["cloud_images"]
    return _BUILTIN_CLOUD_IMAGES


def lookup_url_in_catalog(catalog: list[dict], filename: str) -> str | None:
    """Find the download URL for a cloud image by filename in the loaded catalog."""
    for img in catalog:
        if img.get("filename") == filename:
            return img.get("url")
    return None


def run_validate(args) -> None:
    """Run --validate checks, print a rich report, and exit 0 or 1."""
    run_validate_common(args, validate_vm_deployment)


def run_dry_run(args) -> None:
    """--dry-run: validate config + deployment file, print what would happen, exit 0/1."""
    print_dry_run_header("vm")
    cfg, d = dry_run_validate_and_load(args, validate_vm_deployment)

    # ── Derive display values ──
    hostname      = d.get("hostname", "?")
    node          = d.get("node", "?")
    storage       = d.get("storage", "?")
    img_storage   = d.get("cloud_image_storage", "?")
    img_filename  = d.get("cloud_image_filename", "?")
    cpus          = d.get("cpus", "?")
    memory_gb     = d.get("memory_gb", "?")
    disk_gb       = d.get("disk_gb", "?")
    ip            = d.get("ip_address", "dhcp")
    extra_pkgs    = d.get("extra_packages", [])

    profiles = cfg.get("package_profiles", {})
    profile_packages, profile_tags = resolve_profile(d.get("package_profile", ""), profiles)
    tags = ";".join(["auto-deploy"] + profile_tags)

    domain = cfg.get("proxmox", {}).get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname

    ansible_enabled = cfg.get("ansible", {}).get("enabled", True)
    dns_cfg         = cfg.get("dns", {})
    dns_enabled     = dns_cfg.get("enabled", False)
    inv_cfg         = cfg.get("ansible_inventory", {})
    inv_enabled     = bool(inv_cfg) and inv_cfg.get("enabled", True)

    # ── Summary table ──
    tbl = Table(show_header=False, box=None, padding=(0, 1))
    tbl.add_column(style="bold")
    tbl.add_column()
    tbl.add_row("Hostname",    hostname)
    tbl.add_row("Node",        node)
    tbl.add_row("Image",       f"{img_storage}:{img_filename}")
    tbl.add_row("vCPUs",       str(cpus))
    tbl.add_row("Memory",      f"{memory_gb} GB")
    tbl.add_row("Disk",        f"{disk_gb} GB → {storage}")
    tbl.add_row("IP",          ip if ip != "dhcp" else "DHCP (assigned at boot)")
    tbl.add_row("Profile pkgs", ", ".join(profile_packages) if profile_packages else "(none)")
    tbl.add_row("Extra pkgs",  ", ".join(extra_pkgs) if extra_pkgs else "(none)")
    tbl.add_row("Tags",        tags)
    console.print()
    console.print(Panel(tbl, title="[bold]VM Deployment Summary[/bold]", border_style="dim"))
    console.print()

    # ── Step-by-step plan ──
    DRY = "[bold yellow][DRY RUN][/bold yellow]"
    ip_display = ip if ip != "dhcp" else "<DHCP — assigned at boot>"

    console.print("[bold]Steps that would execute:[/bold]")
    console.print()
    console.print(f"  {DRY} Step 1/7  Create VM (next available VMID) — {hostname} on {node}")
    console.print(f"  {DRY} Step 2/7  Import {img_storage}:{img_filename} → {storage} (scsi0)")
    console.print(f"  {DRY} Step 3/7  Start VM")
    if ip == "dhcp":
        console.print(f"  {DRY} Step 4/7  Poll qemu-guest-agent for DHCP-assigned IP")
    else:
        console.print(f"  {DRY} Step 4/7  Wait for SSH on {ip}")

    if ansible_enabled:
        console.print(f"  {DRY} Step 5/7  Run Ansible post-deploy playbook")
        if profile_packages:
            console.print(f"             [dim]└─ Profile packages : {', '.join(profile_packages)}[/dim]")
        if extra_pkgs:
            console.print(f"             [dim]└─ Extra packages   : {', '.join(extra_pkgs)}[/dim]")
    else:
        console.print(f"  {DRY} Step 5/7  [dim]Ansible post-deploy SKIPPED (ansible.enabled: false)[/dim]")

    if dns_enabled:
        console.print(f"  {DRY} Step 6/7  Register DNS: {fqdn} → {ip_display} on {dns_cfg.get('server', '?')}")
    else:
        console.print(f"  {DRY} Step 6/7  [dim]DNS registration SKIPPED (dns.enabled: false)[/dim]")

    if inv_enabled:
        console.print(f"  {DRY} Step 7/7  Update Ansible inventory on {inv_cfg.get('server', '?')}")
    else:
        console.print(f"  {DRY} Step 7/7  [dim]Inventory update SKIPPED (ansible_inventory.enabled: false)[/dim]")

    print_dry_run_footer()


_BACK = "__back__"


def select_image_with_storage(
    proxmox, node_name: str, cfg: dict,
    deploy: dict, silent: bool, catalog: list[dict],
    nav: bool = False,
) -> tuple[str, str, str | None, bool]:
    """
    Two-level interactive browser: storage → image.

    Returns (storage_name, filename, url_or_none, image_refresh).

    image_refresh is True when the user explicitly chose "Download:" from the catalog
    (meaning they want a fresh copy), False when they selected an already-present file.

    In silent mode reads cloud_image_storage / cloud_image_filename / image_refresh
    from the deploy dict without prompting.
    """
    vm_cfg = cfg.get("vm", {})

    if silent:
        storage_name  = deploy.get("cloud_image_storage")
        filename      = deploy.get("cloud_image_filename")
        url           = deploy.get("cloud_image_url")
        image_refresh = bool(deploy.get("image_refresh", False))

        # Backward-compat: old deployment files pre-date the storage browser.
        # Derive filename from URL if missing (last path component).
        if not filename and url:
            filename = url.rstrip("/").split("/")[-1]
            console.print(
                f"  [yellow]Deployment file has no cloud_image_filename — "
                f"derived '{filename}' from cloud_image_url.[/yellow]"
            )
        # Fall back to config default storage if not recorded in deployment file.
        if not storage_name:
            storage_name = vm_cfg.get("default_cloud_image_storage")
            if storage_name:
                console.print(
                    f"  [yellow]Deployment file has no cloud_image_storage — "
                    f"using default '{storage_name}' from config.yaml.[/yellow]"
                )
        if not storage_name or not filename:
            raise RuntimeError(
                "Cannot determine cloud image storage or filename.\n"
                "  Deployment file is missing cloud_image_storage / cloud_image_filename\n"
                "  and no default_cloud_image_storage is set in config.yaml.\n"
                "  Re-run interactively (without --silent) to update the deployment file."
            )
        console.print(
            f"  [dim]Cloud image: {storage_name}:{filename} "
            f"({'refresh' if image_refresh else 'use cached'})[/dim]"
        )
        return storage_name, filename, url, image_refresh

    default_storage  = deploy.get("cloud_image_storage") or vm_cfg.get("default_cloud_image_storage")
    default_filename = deploy.get("cloud_image_filename")

    while True:
        # ── Step 1: Storage selection ──────────────────────────
        with console.status(f"[bold green]Querying ISO-capable storages on {node_name}..."):
            storages = get_iso_capable_storages(proxmox, node_name)

        if not storages:
            console.print(
                f"[red]No ISO-capable storage found on {node_name}. "
                "Configure a storage with content type 'iso' in Proxmox.[/red]"
            )
            sys.exit(1)

        storage_choices = (
            [questionary.Choice(title="← Go Back", value=BACK)] if nav else []
        )
        for s in storages:
            avail = s.get("avail", 0)
            total = s.get("total", 0)
            free_str = f"  ({bytes_to_gb(avail)} GB free / {bytes_to_gb(total)} GB)" if total else ""
            storage_choices.append(questionary.Choice(
                title=f"{s['storage']}{free_str}",
                value=s["storage"],
            ))

        default_storage_val = default_storage if any(
            s["storage"] == default_storage for s in storages
        ) else None

        console.print("  [bold yellow]Cloud image storage[/bold yellow] [dim](directory storage for the image file — VM disk storage is a separate step)[/dim]")
        selected_storage = questionary.select(
            "Select storage for cloud image:",
            choices=storage_choices,
            default=default_storage_val,
        ).ask()
        if selected_storage is None or selected_storage is BACK:
            return BACK if nav else sys.exit(0)

        if selected_storage == "local":
            console.print(
                "  [yellow]Warning: 'local' storage is limited to the OS disk of the Proxmox node. "
                "Cloud images can be several hundred MB each and will consume space shared with the "
                "OS and VM disks. Consider selecting a dedicated or shared storage volume instead.[/yellow]"
            )

        # ── Step 2: Image selection (with Back) ────────────────
        with console.status(f"[bold green]Listing images on {selected_storage}..."):
            existing_images = list_cloud_images_on_storage(cfg, proxmox, node_name, selected_storage)

        image_choices = []

        if existing_images:
            for img in existing_images:
                size_mb = img["size"] // (1024 ** 2) if img.get("size") else 0
                size_str = f"  ({size_mb} MB)" if size_mb else ""
                image_choices.append(questionary.Choice(
                    title=f"{img['filename']}{size_str}",
                    value={"filename": img["filename"], "url": None, "action": "existing"},
                ))
            image_choices.append(questionary.Separator("─── Download from catalog ───"))

        for img in catalog:
            image_choices.append(questionary.Choice(
                title=f"Download: {img['name']}",
                value={"filename": img["filename"], "url": img["url"], "action": "download"},
            ))

        image_choices.append(questionary.Separator())
        image_choices.append(questionary.Choice(
            title="← Back to storage selection",
            value=_BACK,
        ))

        # Pre-select default image if it appears in the list
        default_img_val = None
        if default_filename:
            default_img_val = next(
                (c.value for c in image_choices
                 if hasattr(c, "value") and isinstance(c.value, dict)
                 and c.value.get("filename") == default_filename),
                None,
            )

        selected = questionary.select(
            f"Select image from {selected_storage}:",
            choices=image_choices,
            default=default_img_val,
        ).ask()
        if selected is None:
            return BACK if nav else sys.exit(0)
        if selected == _BACK:
            continue  # Back to storage selection

        image_refresh = selected["action"] == "download"
        return selected_storage, selected["filename"], selected.get("url"), image_refresh


def _save_vm_deployment_file(hostname: str, vmid: int, node_name: str,
                              image_storage_name: str, image_filename: str,
                              image_url: str | None, image_refresh: bool,
                              cpus_str: str, memory_gb_str: str,
                              disk_gb_str: str, storage: str, vlan_str: str,
                              bridge: str, password: str, ip_address: str,
                              prefix_len: str, gateway: str, assigned_ip: str,
                              cfg: dict, package_profile: str = "",
                              extra_packages: list = (), ttl: str = "") -> Path:
    """
    ip_address: "dhcp" or the configured static IP
    assigned_ip: actual IP the VM received (same as ip_address for static;
                 DHCP-assigned address for DHCP mode)
    """
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname
    data = {
        "type": "vm",
        "hostname": hostname,
        "fqdn": fqdn,
        "node": node_name,
        "vmid": vmid,
        "cloud_image_storage": image_storage_name,
        "cloud_image_filename": image_filename,
        "cloud_image_url": image_url,        # fallback URL for auto-recovery
        "image_refresh": image_refresh,
        "cpus": int(cpus_str),
        "memory_gb": float(memory_gb_str),
        "disk_gb": int(disk_gb_str),
        "storage": storage,
        "vlan": int(vlan_str),
        "bridge": bridge,
        "password": password,
        "ip_address": ip_address,    # "dhcp" or static IP
        "prefix_len": prefix_len,    # "" if DHCP
        "gateway": gateway,          # "" if DHCP
        "package_profile": package_profile,
        "extra_packages": list(extra_packages),
        "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if assigned_ip and assigned_ip != ip_address:
        data["assigned_ip"] = assigned_ip  # DHCP-assigned address for reference
    if ttl:
        data["ttl"] = ttl
        data["expires_at"] = expires_at_from_ttl(ttl)
    return write_deployment_file(data, hostname, "vms", cfg)


def derive_gateway(ip: str) -> str:
    """Derive gateway as the .1 address of the subnet."""
    parts = ip.rsplit(".", 1)
    return f"{parts[0]}.1"


# ─────────────────────────────────────────────
# Main wizard
# ─────────────────────────────────────────────

def main() -> None:
    _start_time = time.time()
    if "--?" in sys.argv:
        sys.argv[sys.argv.index("--?")] = "--help"
    parser = argparse.ArgumentParser(
        prog="deploy_vm.py",
        description="Proxmox VM Deploy Wizard — cloud-init VM provisioning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 deploy_vm.py
              python3 deploy_vm.py --deploy-file deployments/vms/myvm.json
              python3 deploy_vm.py --deploy-file deployments/vms/myvm.json --silent
              python3 deploy_vm.py --validate
              python3 deploy_vm.py --validate --deploy-file deployments/vms/myvm.json
        """),
        add_help=False,
    )
    parser.add_argument("--help", action="help", default=argparse.SUPPRESS,
                        help="show this help message and exit")
    add_common_deploy_args(parser)
    args = parser.parse_args()

    # Validate --ttl early so we fail fast before any Proxmox work
    ttl = None
    if args.ttl:
        try:
            parse_ttl(args.ttl)
            ttl = args.ttl
        except ValueError as e:
            console.print(f"[red]ERROR: {e}[/red]")
            sys.exit(1)

    if args.validate:
        run_validate(args)  # exits 0 or 1
    if args.dry_run:
        run_dry_run(args)   # exits 0 or 1

    if args.preflight:
        cfg = load_config(args.config)
        deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}
        run_preflight(cfg, kind="vm", silent=args.silent, verbose=True,
                      deploy=deploy if args.deploy_file else None, yolo=args.yolo,
                      config_path=Path(args.config) if args.config else None)
        sys.exit(0)

    if args.silent and not args.deploy_file:
        parser.error("--silent requires --deploy-file")

    cfg = load_config(args.config)
    defaults = cfg["defaults"]
    addusername = defaults.get("addusername", "admin")
    cpu_threshold = float(defaults.get("cpu_threshold", 0.85))
    ram_threshold = float(defaults.get("ram_threshold", 0.95))
    firewall_enabled = 1 if defaults.get("firewall_enabled", False) else 0
    vm_cfg = cfg.get("vm", {})
    cpu_type = vm_cfg.get("cpu_type", "x86-64-v2-AES")
    machine  = vm_cfg.get("machine",  "q35")
    bios     = vm_cfg.get("bios",     "seabios")
    scsihw   = vm_cfg.get("storage_controller", "virtio-scsi-pci")
    nic_driver = vm_cfg.get("nic_driver", "virtio")

    deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}
    silent = args.silent

    console.print()
    console.print(Panel.fit(
        Text("Proxmox VM Deploy Wizard\n", style="bold green", justify="center") +
        Text("github.com: Jerry-Lees/HomeLab/labinator", style="dim green", justify="center"),
        border_style="green",
    ))
    console.print()

    if deploy and not silent:
        console.print(f"[dim]Loaded deployment file: {args.deploy_file}[/dim]\n")
    elif deploy and silent:
        console.print(f"[dim]Silent mode — deploying from: {args.deploy_file}[/dim]\n")

    # Pre-flight checks
    if not deploy.get("preflight", True):
        console.print("[yellow]⚡ preflight: false in deploy file — checks skipped.[/yellow]")
    else:
        run_preflight(cfg, kind="vm", silent=silent, verbose=True,
                      deploy=deploy if args.deploy_file else None, yolo=args.yolo)

    # ── Connect to Proxmox ──
    with console.status("[bold green]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
            nodes = get_nodes_with_load(proxmox, storage_content="images")
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    if not nodes:
        console.print("[red]No online nodes found in the cluster.[/red]")
        sys.exit(1)

    console.print(f"[green]✓ Connected.[/green] {len(nodes)} node(s) online.\n")

    # ═══════════════════════════════════════════
    # Interactive wizard — step functions with ESC back-navigation
    # ESC goes back one step at any prompt.
    # ESC at the first prompt exits cleanly ("Aborted.").
    # Ctrl+C exits immediately at any point.
    # ═══════════════════════════════════════════

    _ws = make_common_wizard_steps(cfg, deploy, silent, nodes, cpu_threshold, ram_threshold,
                                    hostname_label="VM")

    def step_ip(s):
        deploy_ip = str(deploy.get("ip_address", ""))
        ip_default = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
        if silent:
            ip_address = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
        else:
            r = pt_text(
                "IP address for VM:",
                default=s.get("ip_address", ip_default),
                instruction="leave blank for DHCP",
                validate=lambda v: (
                    True if v.strip() == "" or v.strip().count(".") == 3
                    else "Enter a valid IPv4 address or leave blank for DHCP"
                ),
            )
            if r is BACK:
                return BACK
            ip_address = r.strip()
        return {**s, "ip_address": ip_address, "use_dhcp": (ip_address == "")}

    def step_prefix(s):
        if s.get("use_dhcp"):
            return SKIP
        r = pt_text(
            "Prefix length (subnet mask bits):",
            default=s.get("prefix_len", "24"),
            validate=lambda v: True if v.isdigit() and 1 <= int(v) <= 32 else "Must be 1–32",
            d=deploy, key="prefix_len", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "prefix_len": r.strip()}

    def step_gateway(s):
        if s.get("use_dhcp"):
            return SKIP
        auto_gw = derive_gateway(s["ip_address"])
        r = pt_text(
            "Gateway:",
            default=s.get("gateway", auto_gw),
            validate=lambda v: True if v.count(".") == 3 else "Enter a valid IPv4 address",
            d=deploy, key="gateway", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "gateway": r.strip()}

    def step_image(s):
        catalog = load_cloud_images()
        r = select_image_with_storage(
            proxmox, s["node_name"], cfg, deploy, silent, catalog, nav=True,
        )
        if r is BACK:
            return BACK
        image_storage_name, image_filename, image_url, image_refresh = r
        return {**s, "image_storage_name": image_storage_name,
                "image_filename": image_filename, "image_url": image_url,
                "image_refresh": image_refresh, "catalog": catalog}

    def step_storage(s):
        with console.status(f"[bold green]Querying storage pools on {s['node_name']}..."):
            storage_pools = get_vm_disk_storages(proxmox, s["node_name"])
        if silent:
            storage = str(deploy.get("storage", storage_pools[0] if storage_pools else "local-lvm"))
            console.print(f"  [dim]Storage (from deployment file): {storage}[/dim]")
        elif len(storage_pools) > 1:
            deploy_storage = str(deploy.get("storage", ""))
            default_storage = deploy_storage if deploy_storage in storage_pools else storage_pools[0]
            r = select_nav(
                "Select storage pool for VM disk:",
                choices=storage_pools,
                default=s.get("storage", default_storage),
            )
            if r is BACK:
                return BACK
            storage = r
        else:
            storage = storage_pools[0]
            console.print(f"  [dim]Storage pool: {storage}[/dim]")
        return {**s, "storage": storage}

    def step_confirm(s):
        # Read SSH public key (non-interactive file read)
        pve = cfg["proxmox"]
        ssh_key_path = os.path.expanduser(pve.get("ssh_key", "~/.ssh/id_rsa"))
        pub_key_path = ssh_key_path + ".pub"
        pub_key_encoded = None
        if os.path.exists(pub_key_path):
            pub_key = open(pub_key_path).read().strip()
            pub_key_encoded = quote(pub_key, safe="")
        else:
            console.print(
                f"[yellow]Warning: SSH public key not found at {pub_key_path}. "
                f"Key injection skipped — Ansible will attempt password auth.[/yellow]"
            )
        next_vmid = get_next_vmid(proxmox)
        bridge = defaults.get("bridge", "vmbr0")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory_mb = int(float(s["memory_gb_str"]) * 1024)
        use_dhcp = s.get("use_dhcp", True)
        net_display = (
            f"{bridge}.{s['vlan_str']}  (DHCP — IP assigned at boot)"
            if use_dhcp else
            f"{bridge}.{s['vlan_str']}  (static {s['ip_address']}/{s.get('prefix_len', '')} "
            f" gw {s.get('gateway', '')})"
        )
        console.print()
        table = Table(title="VM Deployment Summary", show_header=False,
                      border_style="green", padding=(0, 2))
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("VMID",      str(next_vmid))
        table.add_row("Hostname",  s["hostname"])
        table.add_row("Node",      s["node_name"])
        table.add_row("Image",     f"{s['image_storage_name']}:{s['image_filename']}")
        table.add_row("Machine",   f"{machine} / {bios} / {cpu_type} / {scsihw}")
        table.add_row("vCPUs",     s["cpus_str"])
        table.add_row("Memory",    f"{s['memory_gb_str']} GB ({memory_mb} MB)")
        table.add_row("Disk",      f"{s['disk_gb_str']} GB  →  {s['storage']}  (scsi0)")
        table.add_row("Network",   net_display)
        table.add_row("SSH key",   pub_key_path if pub_key_encoded
                      else "[yellow]not found — password only[/yellow]")
        tags_display = (";".join(["auto-deploy"] + s["profile_tags"])
                        if s["profile_tags"] else "auto-deploy")
        table.add_row("Tags",      tags_display)
        if ttl:
            table.add_row("TTL / Expires",
                          f"{ttl}  (expires {expires_at_from_ttl(ttl)[:19]} UTC)")
        table.add_row("Users",     f"root, {addusername} (same password)")
        table.add_row("Timezone",  cfg.get("timezone", "UTC"))
        table.add_row("NTP",       ", ".join(cfg.get("ntp", {}).get("servers", ["pool.ntp.org"])))
        table.add_row("SNMP",      f"community='{cfg['snmp']['community']}' (rw) on :161")
        console.print(table)
        console.print()
        if not silent:
            try:
                r = questionary.confirm("Proceed with deployment?", default=True).unsafe_ask()
            except KeyboardInterrupt:
                console.print("\n[yellow]Aborted.[/yellow]")
                sys.exit(0)
            if r is None:
                return BACK
            if not r:
                console.print("[yellow]Deployment cancelled.[/yellow]")
                sys.exit(0)
        return {**s, "next_vmid": next_vmid, "bridge": bridge, "now_str": now_str,
                "pub_key_path": pub_key_path, "pub_key_encoded": pub_key_encoded}

    ws = run_wizard_steps([
        _ws["hostname"], _ws["cpus"], _ws["memory"], _ws["disk"], _ws["vlan"], _ws["password"],
        _ws["package_profile"], _ws["extra_packages"],
        step_ip, step_prefix, step_gateway,
        _ws["node"], step_image, step_storage, step_confirm,
    ])

    # Unpack wizard state into local variables for the rest of the deploy flow
    hostname           = ws["hostname"]
    cpus_str           = ws["cpus_str"]
    memory_gb_str      = ws["memory_gb_str"]
    disk_gb_str        = ws["disk_gb_str"]
    vlan_str           = ws["vlan_str"]
    password           = ws["password"]
    package_profile    = ws["package_profile"]
    profile_packages   = ws["profile_packages"]
    profile_tags       = ws["profile_tags"]
    extra_packages     = ws["extra_packages"]
    ip_address         = ws["ip_address"]
    use_dhcp           = ws["use_dhcp"]
    prefix_len         = ws.get("prefix_len", "")
    gateway            = ws.get("gateway", "")
    node_name          = ws["node_name"]
    image_storage_name = ws["image_storage_name"]
    image_filename     = ws["image_filename"]
    image_url          = ws["image_url"]
    image_refresh      = ws["image_refresh"]
    catalog            = ws["catalog"]
    storage            = ws["storage"]
    next_vmid          = ws["next_vmid"]
    bridge             = ws["bridge"]
    now_str            = ws["now_str"]
    pub_key_path       = ws["pub_key_path"]
    pub_key_encoded    = ws["pub_key_encoded"]
    ssh_key            = os.path.expanduser(cfg["proxmox"].get("ssh_key", "~/.ssh/id_rsa"))
    memory_mb          = int(float(memory_gb_str) * 1024)
    profiles           = cfg.get("package_profiles", {})

    # ── VLAN existence check ──
    check_vlan_exists(proxmox, node_name, bridge, vlan_str, silent=silent)

    # ═══════════════════════════════════════════
    # Step 1/6: Create VM
    # ═══════════════════════════════════════════
    console.print()
    console.print("[bold green]─── Step 1/7: Creating VM ───[/bold green]")

    ip_note = "DHCP" if use_dhcp else f"{ip_address}/{prefix_len}  gw {gateway}"
    vm_note = textwrap.dedent(f"""\
        Auto-deployed by deploy_vm.py
        ─────────────────────────────────────
        Created    : {now_str}
        Node       : {node_name}
        Image      : {image_storage_name}:{image_filename}
        Machine    : {machine} / {bios} / {cpu_type}
        vCPUs      : {cpus_str}
        Memory     : {memory_gb_str} GB
        Disk       : {disk_gb_str} GB ({storage}) scsi0 / {scsihw}
        VLAN       : {vlan_str}
        IP         : {ip_note}
        Timezone   : {cfg.get('timezone', 'UTC')}
        NTP        : {', '.join(cfg.get('ntp', {}).get('servers', ['pool.ntp.org']))}
        SNMP       : community={cfg.get('snmp', {}).get('community', 'your-snmp-community')} (rw)
        ─────────────────────────────────────
        Users: root, {addusername} (same password)
    """)

    create_params = {
        "vmid":        next_vmid,
        "name":        hostname,
        "cores":       int(cpus_str),
        "memory":      memory_mb,
        "cpu":         cpu_type,
        "machine":     machine,
        "bios":        bios,
        "scsihw":      scsihw,
        "agent":       "enabled=1",
        "net0":        f"{nic_driver},bridge={bridge},tag={vlan_str},firewall={firewall_enabled}",
        "onboot":      1 if defaults.get("onboot", True) else 0,
        "tags":        ";".join(["auto-deploy"] + profile_tags),
        "description": vm_note,
    }

    try:
        next_vmid = create_vm(proxmox, node_name, create_params)
    except Exception as e:
        console.print(f"[red]✗ VM creation failed: {e}[/red]")
        sys.exit(1)

    # Apply tag colors to cluster (non-fatal if it fails)
    tag_colors = resolve_tag_colors(package_profile, profiles)
    apply_tag_colors(proxmox, tag_colors)

    # ═══════════════════════════════════════════
    # Step 2/6: Import cloud image + configure disk + cloud-init
    # ═══════════════════════════════════════════
    console.print("[bold green]─── Step 2/7: Importing cloud image and configuring VM ───[/bold green]")

    try:
        resolved_url = lookup_url_in_catalog(catalog, image_filename) or image_url
        import_cloud_image(
            cfg, proxmox, node_name, next_vmid, storage,
            image_storage_name, image_filename, resolved_url,
            image_refresh,
        )
    except Exception as e:
        console.print(f"[red]✗ Cloud image import failed: {e}[/red]")
        console.print(
            f"[yellow]VM {next_vmid} was created but has no disk. "
            f"Delete it manually: qm destroy {next_vmid} --purge[/yellow]"
        )
        sys.exit(1)

    # Attach imported disk and configure cloud-init
    console.print("  [dim]Attaching disk and configuring cloud-init...[/dim]")
    try:
        ci_params = {
            "ciuser":       "root",
            "cipassword":   password,
            "ipconfig0":    "ip=dhcp" if use_dhcp else f"ip={ip_address}/{prefix_len},gw={gateway}",
            "nameserver":   defaults.get("nameserver", "8.8.8.8"),
            "searchdomain": defaults.get("searchdomain", ""),
        }
        if pub_key_encoded:
            ci_params["sshkeys"] = pub_key_encoded
        if use_dhcp:
            ci_params["cicustom"] = write_guest_agent_snippet(cfg, node_name, next_vmid)
        configure_vm_disk_and_cloudinit(proxmox, node_name, next_vmid, storage, disk_gb_str, ci_params)
        console.print(
            f"  [dim]Cloud-init: {'DHCP' if use_dhcp else f'{ip_address}/{prefix_len} gw {gateway}'}"
            f"{' + SSH key' if pub_key_encoded else ''}"
            f"{' + guest-agent snippet' if use_dhcp else ''}[/dim]"
        )
        console.print("[green]✓ VM configured[/green]")
    except Exception as e:
        console.print(f"[red]✗ VM configuration failed: {e}[/red]")
        console.print(
            f"[yellow]VM {next_vmid} may be in a partial state. "
            f"Inspect or delete: qm destroy {next_vmid} --purge[/yellow]"
        )
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Step 3/6: Start VM
    # ═══════════════════════════════════════════
    console.print("[bold green]─── Step 3/7: Starting VM ───[/bold green]")
    try:
        start_vm(proxmox, node_name, next_vmid)
    except Exception as e:
        console.print(f"[red]✗ Failed to start VM: {e}[/red]")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Step 4/6: Wait for SSH (discover IP first if DHCP)
    # ═══════════════════════════════════════════
    if use_dhcp:
        console.print("[bold green]─── Step 4/7: Discovering DHCP IP via guest agent ───[/bold green]")
        console.print("  [dim](cloud-init installs qemu-guest-agent during first boot — this may take 2–4 min)[/dim]")
        try:
            with console.status("[bold green]Waiting for guest agent to report IP (up to 5 min)..."):
                vm_ip = wait_for_guest_agent_ip(proxmox, node_name, next_vmid, timeout=300)
            console.print(f"[green]✓ DHCP assigned IP: [bold]{vm_ip}[/bold][/green]")
        except TimeoutError as e:
            console.print(f"[red]✗ {e}[/red]")
            console.print("  Check the VM console in Proxmox — cloud-init or guest agent may have failed.")
            sys.exit(1)
        try:
            with console.status(f"[bold green]Waiting for SSH on {vm_ip}..."):
                wait_for_ssh(vm_ip, timeout=60)
            console.print(f"[green]✓ SSH is up on {vm_ip}[/green]")
        except TimeoutError as e:
            console.print(f"[red]✗ {e}[/red]")
            sys.exit(1)
    else:
        vm_ip = ip_address
        console.print("[bold green]─── Step 4/7: Waiting for SSH (cloud-init first-boot may take 1–3 min) ───[/bold green]")
        try:
            with console.status(f"[bold green]Polling {vm_ip}:22 (up to 5 min)..."):
                wait_for_ssh(vm_ip, timeout=300)
            console.print(f"[green]✓ SSH is up on {vm_ip}[/green]")
        except TimeoutError as e:
            console.print(f"[red]✗ {e}[/red]")
            console.print("  Check the VM console in Proxmox — cloud-init may have failed.")
            sys.exit(1)

    # Brief pause to let cloud-init finish the tail end of first-boot work
    time.sleep(5)

    # ═══════════════════════════════════════════
    # Step 5/6: Ansible post-deploy
    # ═══════════════════════════════════════════
    console.print("[bold green]─── Step 5/7: Running post-deployment configuration (Ansible) ───[/bold green]")
    if cfg.get("ansible", {}).get("enabled", True):
        try:
            run_ansible_post_deploy(vm_ip, password, hostname, cfg, kind="vm",
                                    ssh_key=ssh_key,
                                    profile_packages=profile_packages,
                                    extra_packages=extra_packages)
            console.print("[green]✓ Post-deployment configuration complete[/green]")
        except Exception as e:
            console.print(f"[red]✗ Post-deploy failed: {e}[/red]")
            sys.exit(1)
    else:
        console.print("  [dim]Skipped (ansible.enabled: false) — configure host manually[/dim]")

    # ═══════════════════════════════════════════
    # Step 6/7: Register DNS
    # ═══════════════════════════════════════════
    console.print("[bold green]─── Step 6/7: Registering DNS ───[/bold green]")
    dns_action = dns_precheck(cfg, hostname, vm_ip, silent=silent)
    if dns_action == "abort":
        sys.exit(1)
    elif dns_action == "proceed":
        run_ansible_add_dns(cfg, hostname, vm_ip)
    else:
        console.print("  [dim]DNS registration skipped — existing record kept.[/dim]")

    # ═══════════════════════════════════════════
    # Step 7/7: Update Ansible inventory
    # ═══════════════════════════════════════════
    console.print("[bold green]─── Step 7/7: Updating Ansible inventory ───[/bold green]")
    run_ansible_inventory_update(cfg, hostname, vm_ip, password)

    # Save deployment file
    _save_vm_deployment_file(
        hostname, next_vmid, node_name,
        image_storage_name, image_filename, image_url, image_refresh,
        cpus_str, memory_gb_str, disk_gb_str, storage,
        vlan_str, bridge, password,
        "dhcp" if use_dhcp else ip_address,
        prefix_len, gateway, vm_ip, cfg,
        package_profile=package_profile,
        extra_packages=extra_packages,
        ttl=ttl or "",
    )

    # Health check (optional — runs if health_check.enabled in config)
    health_check(vm_ip, password, addusername, cfg)

    write_history({
        "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user":             os.getenv("USER") or os.getenv("LOGNAME") or "unknown",
        "action":           "deploy",
        "type":             "vm",
        "hostname":         hostname,
        "fqdn":             f"{hostname}.{cfg['proxmox'].get('node_domain', '')}".strip("."),
        "node":             node_name,
        "vmid":             next_vmid,
        "ip":               vm_ip,
        "result":           "success",
        "duration_seconds": round(time.time() - _start_time),
    })

    # ═══════════════════════════════════════════
    # Done!
    # ═══════════════════════════════════════════
    inv_group = cfg.get("ansible_inventory", {}).get("group", "linux")
    dhcp_note = "  (DHCP-assigned)" if use_dhcp else ""
    console.print()
    console.print(Panel(
        textwrap.dedent(f"""\
            [bold green]Deployment Complete![/bold green]

            [bold]Hostname   :[/bold]  {hostname}
            [bold]IP Address :[/bold]  {vm_ip}{dhcp_note}
            [bold]VMID       :[/bold]  {next_vmid}  (on {node_name})
            [bold]SSH        :[/bold]  ssh root@{vm_ip}
                      ssh {addusername}@{vm_ip}

            [dim]Tagged 'auto-deploy' with specs note in Proxmox.[/dim]
            [dim]Added to Ansible inventory group [{inv_group}].[/dim]
        """),
        border_style="green",
        title="[bold green]✓ All Done[/bold green]",
    ))
    if _deploy_log_path:
        console.print(f"[dim]Log: {_deploy_log_path}[/dim]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(1)
