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

import argparse
import ipaddress
import socket
import time
import subprocess
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import json
import yaml
import paramiko
import questionary
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

from modules.lib import (
    load_config,
    connect_proxmox,
    wait_for_task,
    health_check,
    _check_ipv4,
    validate_config,
    resolve_profile,
    dns_precheck,
    run_ansible_add_dns,
    run_ansible_inventory_update,
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


# ─────────────────────────────────────────────
# Validation (--validate flag)
# ─────────────────────────────────────────────


def validate_vm_deployment(deploy_path: Path) -> list[str]:
    """Return a list of error strings; empty means deployment JSON is valid."""
    errors = []
    try:
        with open(deploy_path) as f:
            d = json.load(f)
    except FileNotFoundError:
        return [f"File not found: {deploy_path}"]
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]
    if not isinstance(d, dict):
        return ["Deployment file is not a JSON object"]

    if d.get("type") not in (None, "vm") or "template_name" in d:
        return ["This looks like an LXC deployment file — use deploy_lxc.py instead"]

    for field in ("hostname", "node", "cloud_image_storage", "cloud_image_filename",
                  "storage", "bridge", "password"):
        val = d.get(field)
        if not val or not isinstance(val, str) or not val.strip():
            errors.append(f"'{field}' is required and must be a non-empty string")

    cpus = d.get("cpus")
    if cpus is None:
        errors.append("'cpus' is required")
    elif not isinstance(cpus, int) or cpus <= 0:
        errors.append(f"'cpus' must be a positive integer (got {cpus!r})")

    mem = d.get("memory_gb")
    if mem is None:
        errors.append("'memory_gb' is required")
    elif not isinstance(mem, (int, float)) or mem <= 0:
        errors.append(f"'memory_gb' must be a positive number (got {mem!r})")

    disk = d.get("disk_gb")
    if disk is None:
        errors.append("'disk_gb' is required")
    elif not isinstance(disk, (int, float)) or disk <= 0:
        errors.append(f"'disk_gb' must be a positive number (got {disk!r})")

    vlan = d.get("vlan")
    if vlan is None:
        errors.append("'vlan' is required")
    elif not isinstance(vlan, int) or not (1 <= vlan <= 4094):
        errors.append(f"'vlan' must be an integer 1–4094 (got {vlan!r})")

    ip = d.get("ip_address")
    if ip is None:
        errors.append("'ip_address' is required")
    elif ip != "dhcp":
        if not _check_ipv4(str(ip)):
            errors.append(f"'ip_address' must be 'dhcp' or a valid IPv4 address (got {ip!r})")
        prefix = d.get("prefix_len")
        if prefix is None or str(prefix) == "":
            errors.append("'prefix_len' is required when ip_address is a static IP")
        elif not str(prefix).isdigit() or not (1 <= int(prefix) <= 32):
            errors.append(f"'prefix_len' must be 1–32 (got {prefix!r})")

    ep = d.get("extra_packages")
    if ep is not None:
        if not isinstance(ep, list):
            errors.append("'extra_packages' must be a list")
        elif not all(isinstance(p, str) for p in ep):
            errors.append("'extra_packages' entries must all be strings")

    return errors


def run_validate(args) -> None:
    """Run --validate checks, print a rich report, and exit 0 or 1."""
    from rich.table import Table as RichTable
    cfg_path = Path(__file__).parent / "config.yaml"
    all_errors: list[tuple[str, str]] = []  # (section, message)

    # ── Config checks ──
    cfg_errors = validate_config(cfg_path)
    for e in cfg_errors:
        all_errors.append(("config.yaml", e))

    # ── Deployment file checks ──
    if args.deploy_file:
        deploy_errors = validate_vm_deployment(Path(args.deploy_file))
        for e in deploy_errors:
            all_errors.append((args.deploy_file, e))

    # ── Output ──
    console.print()
    console.print(Panel.fit(
        Text("Labinator Validate", style="bold yellow"),
        border_style="yellow",
    ))
    console.print()

    if not all_errors:
        if args.deploy_file:
            console.print(f"[green]✓ config.yaml[/green]  OK")
            console.print(f"[green]✓ {args.deploy_file}[/green]  OK")
        else:
            console.print(f"[green]✓ config.yaml[/green]  OK")
        console.print()
        console.print("[bold green]All checks passed.[/bold green]")
        sys.exit(0)

    table = RichTable(show_header=True, header_style="bold red")
    table.add_column("File", style="dim")
    table.add_column("Error")
    for section, msg in all_errors:
        table.add_row(section, msg)
    console.print(table)
    console.print()
    console.print(f"[bold red]{len(all_errors)} error(s) found. Fix them before deploying.[/bold red]")
    sys.exit(1)


def run_dry_run(args) -> None:
    """--dry-run: validate config + deployment file, print what would happen, exit 0/1."""
    cfg_path = Path(__file__).parent / "config.yaml"

    console.print()
    console.print(Panel.fit(
        Text("Labinator Dry Run — VM Deploy", style="bold yellow"),
        border_style="yellow",
    ))
    console.print()

    # ── Validate config ──
    cfg_errors = validate_config(cfg_path)
    if cfg_errors:
        for e in cfg_errors:
            console.print(f"[red]✗ config.yaml: {e}[/red]")
        sys.exit(1)
    console.print("[green]✓ config.yaml[/green]  OK")

    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    if not args.deploy_file:
        console.print()
        console.print("[yellow]No --deploy-file provided. Config is valid.[/yellow]")
        console.print("[dim]Provide --deploy-file for a full step-by-step dry-run.[/dim]")
        sys.exit(0)

    # ── Validate deployment file ──
    deploy_errors = validate_vm_deployment(Path(args.deploy_file))
    if deploy_errors:
        for e in deploy_errors:
            console.print(f"[red]✗ {args.deploy_file}: {e}[/red]")
        sys.exit(1)
    console.print(f"[green]✓ {args.deploy_file}[/green]  OK")

    with open(args.deploy_file) as f:
        d = json.load(f)

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

    console.print()
    console.print("[bold green]Dry run complete — no changes made.[/bold green]")
    sys.exit(0)


# ─────────────────────────────────────────────
# Proxmox helpers
# ─────────────────────────────────────────────


def get_vm_disk_storages(proxmox: ProxmoxAPI, node: str) -> list[str]:
    """Return storage pools that can hold VM disk images (content type: images)."""
    pools = []
    try:
        for s in proxmox.nodes(node).storage.get(enabled=1):
            if "images" in s.get("content", ""):
                pools.append(s["storage"])
    except Exception:
        pass
    return pools if pools else ["local-lvm"]


def get_iso_capable_storages(proxmox: ProxmoxAPI, node: str) -> list[dict]:
    """Return storages on this node that support ISO content, with free space info."""
    result = []
    try:
        for s in proxmox.nodes(node).storage.get(enabled=1):
            if "iso" in s.get("content", "").split(","):
                try:
                    status = proxmox.nodes(node).storage(s["storage"]).status.get()
                    s["avail"] = status.get("avail", 0)
                    s["total"] = status.get("total", 0)
                except Exception:
                    s["avail"] = 0
                    s["total"] = 0
                result.append(s)
    except Exception:
        pass
    return result


def get_storage_iso_path(proxmox: ProxmoxAPI, storage_name: str) -> str:
    """
    Return the filesystem path of the cloud-images directory for a given storage.
    Files are stored under {storage_path}/cloud-images/ — NOT under template/iso/ —
    so they are invisible to the Proxmox GUI ISO picker and can't be accidentally
    attached as a CD-ROM during manual VM creation.
    Falls back to /mnt/pve/{storage_name}/cloud-images for unknown storage types.
    """
    try:
        configs = proxmox.storage.get()
        for s in configs:
            if s.get("storage") == storage_name:
                path = s.get("path", f"/mnt/pve/{storage_name}")
                return f"{path}/cloud-images"
    except Exception:
        pass
    return f"/mnt/pve/{storage_name}/cloud-images"


def list_cloud_images_on_storage(cfg: dict, proxmox: ProxmoxAPI,
                                  node_name: str, storage_name: str) -> list[dict]:
    """
    List cloud image files in the storage's cloud-images directory via SSH.
    Returns list of dicts with 'filename' and 'size' (bytes).
    Returns empty list if directory doesn't exist yet or SSH fails.
    """
    cloud_path = get_storage_iso_path(proxmox, storage_name)
    ssh_host   = node_ssh_host(cfg, node_name)
    ssh_key    = os.path.expanduser(cfg["proxmox"].get("ssh_key", "~/.ssh/id_rsa"))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ssh_host, username="root", key_filename=ssh_key, timeout=15)
        _, out, _ = run_ssh_cmd(
            ssh,
            f'find {cloud_path} -maxdepth 1 -type f -printf "%f\\t%s\\n" 2>/dev/null || true',
        )
        files = []
        for line in out.splitlines():
            if "\t" in line:
                fname, size_str = line.split("\t", 1)
                fname = fname.strip()
                size  = int(size_str.strip()) if size_str.strip().isdigit() else 0
                if fname:
                    files.append({"filename": fname, "size": size})
        return sorted(files, key=lambda x: x["filename"])
    except Exception:
        return []
    finally:
        try:
            ssh.close()
        except Exception:
            pass


_BACK = "__back__"


def select_image_with_storage(
    proxmox: ProxmoxAPI, node_name: str, cfg: dict,
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


def wait_for_guest_agent_ip(proxmox: ProxmoxAPI, node: str, vmid: int,
                             timeout: int = 300) -> str:
    """
    Poll the QEMU guest agent until it reports a non-loopback IPv4 address.
    Used for DHCP VMs where the IP isn't known at deploy time.
    Requires qemu-guest-agent to be installed and running inside the VM
    For DHCP VMs a cloud-init vendor-data snippet pre-installs qemu-guest-agent
    so it is running before this function is called. See write_guest_agent_snippet().
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = proxmox.nodes(node).qemu(vmid).agent("network-get-interfaces").get()
            for iface in result.get("result", []):
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4":
                        ip = addr.get("ip-address", "")
                        if ip and not ip.startswith("127."):
                            return ip
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError(
        f"Guest agent did not report a non-loopback IP within {timeout}s. "
        "Ensure qemu-guest-agent is available in the cloud image and the VM booted successfully."
    )


# ─────────────────────────────────────────────
# Cloud image import via SSH to Proxmox node
# ─────────────────────────────────────────────

def run_ssh_cmd(ssh: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    """Run a command over SSH, blocking until completion. Returns (exit_code, stdout, stderr)."""
    _, stdout, stderr = ssh.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, stdout.read().decode().strip(), stderr.read().decode().strip()


def import_cloud_image(cfg: dict, proxmox: ProxmoxAPI, node_name: str, vmid: int,
                       disk_storage: str, image_storage_name: str,
                       image_filename: str, image_url: str | None,
                       image_refresh: bool, catalog: list[dict]) -> None:
    """
    Ensure the cloud image is present on image_storage_name, then import as VM disk.

    image_refresh=True  — Always re-download (user explicitly chose "Download:" in UI
                          or set image_refresh: true in deployment file).
    image_refresh=False — Use existing file; auto-download only if missing.

    Auto-recovery: if the file is gone (e.g. someone cleaned up the storage),
    the URL is resolved from catalog first (handles URL changes), then falls
    back to image_url from the deployment file.

    After import the disk appears as unused0 in the VM config.
    """
    pve = cfg["proxmox"]
    iso_path   = get_storage_iso_path(proxmox, image_storage_name)
    image_file = f"{iso_path}/{image_filename}"
    ssh_host   = node_ssh_host(cfg, node_name)
    ssh_key    = os.path.expanduser(pve.get("ssh_key", "~/.ssh/id_rsa"))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ssh_host, username="root", key_filename=ssh_key, timeout=30)
    except paramiko.AuthenticationException:
        raise RuntimeError(
            f"SSH key auth to {ssh_host} failed. "
            f"Ensure {ssh_key} is authorized on the node."
        )

    try:
        # Check if image is already on the storage
        _, out, _ = run_ssh_cmd(ssh, f'test -f {image_file} && echo exists || echo missing')
        file_exists = "exists" in out

        need_download = image_refresh or not file_exists

        if need_download:
            if not file_exists:
                console.print(
                    f"  [yellow]Image not found on {image_storage_name} — downloading...[/yellow]"
                )
            else:
                console.print(f"  [dim]Downloading fresh copy of {image_filename}...[/dim]")

            # Resolve URL: catalog is authoritative (handles stale URLs), fall back to stored URL
            url = lookup_url_in_catalog(catalog, image_filename) or image_url
            if not url:
                raise RuntimeError(
                    f"Cannot download '{image_filename}': URL not found in cloud-images.yaml "
                    f"and none stored in deployment file. Add it to cloud-images.yaml to fix this."
                )

            console.print(f"  [dim](this may take 1–2 minutes for a ~600 MB image)[/dim]")
            run_ssh_cmd(ssh, f"mkdir -p {iso_path}")
            exit_code, out, err = run_ssh_cmd(ssh, f'wget -q -O {image_file} "{url}"')
            if exit_code != 0:
                raise RuntimeError(f"wget failed (exit {exit_code}): {err or out}")
            console.print(f"  [green]✓ Image ready at {image_storage_name}:{image_filename}[/green]")
        else:
            console.print(f"  [dim]Using existing image: {image_storage_name}:{image_filename}[/dim]")

        # Import disk into VM
        console.print(f"  [dim]Importing disk into VM {vmid} on storage '{disk_storage}'...[/dim]")
        exit_code, out, err = run_ssh_cmd(ssh, f"qm importdisk {vmid} {image_file} {disk_storage}")
        if exit_code != 0:
            raise RuntimeError(f"qm importdisk failed (exit {exit_code}): {err or out}")
        console.print(f"  [green]✓ Disk imported[/green]")
    finally:
        ssh.close()


def write_guest_agent_snippet(cfg: dict, node_name: str, vmid: int) -> str:
    """
    Write a minimal cloud-init user-data snippet to the Proxmox node's local
    snippets directory (/var/lib/vz/snippets/) that pre-installs and starts
    qemu-guest-agent during first boot.

    Required for DHCP VMs: the agent must be running before we can poll the
    guest agent API to discover the dynamically assigned IP address.

    Returns the cicustom value to pass to the Proxmox VM config, e.g.:
      "vendor=local:snippets/vm-113-userdata.yaml"
    """
    snippet = (
        "#cloud-config\n"
        "package_update: false\n"
        "package_upgrade: false\n"
        "packages:\n"
        "  - qemu-guest-agent\n"
        "runcmd:\n"
        "  - systemctl enable --now qemu-guest-agent\n"
    )
    snippet_name = f"vm-{vmid}-userdata.yaml"
    snippet_path = f"/var/lib/vz/snippets/{snippet_name}"

    pve = cfg["proxmox"]
    ssh_host = node_ssh_host(cfg, node_name)
    ssh_key = os.path.expanduser(pve.get("ssh_key", "~/.ssh/id_rsa"))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ssh_host, username="root", key_filename=ssh_key, timeout=15)
        run_ssh_cmd(ssh, "mkdir -p /var/lib/vz/snippets")
        sftp = ssh.open_sftp()
        try:
            with sftp.open(snippet_path, "w") as f:
                f.write(snippet)
        finally:
            sftp.close()
    except Exception as e:
        raise RuntimeError(f"Failed to write cloud-init snippet on {ssh_host}: {e}")
    finally:
        ssh.close()

    return f"vendor=local:snippets/{snippet_name}"


# ─────────────────────────────────────────────
# Ansible runners
# ─────────────────────────────────────────────

def run_ansible_post_deploy_vm(vm_ip: str, ssh_key: str, password: str, hostname: str, cfg: dict = None, profile_packages: list = (), extra_packages: list = ()) -> None:
    """Run the post-deploy Ansible playbook against the new VM using SSH key auth."""
    ansible_dir = Path(__file__).parent / "ansible"
    snmp = (cfg or {}).get("snmp", {})
    addusername = (cfg or {}).get("defaults", {}).get("addusername", "admin")
    timezone = (cfg or {}).get("timezone", "UTC")
    ntp_servers = (cfg or {}).get("ntp", {}).get("servers", ["pool.ntp.org", "time.nist.gov"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, prefix="deploy_vm_inv_") as f:
        f.write("[all]\n")
        f.write(
            f"{vm_ip} "
            f"ansible_user=root "
            f"ansible_python_interpreter=auto "
            f"ansible_ssh_extra_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'\n"
        )
        inv_path = f.name

    try:
        cmd = [
            "ansible-playbook",
            "-i", inv_path,
            str(ansible_dir / "post-deploy-vm.yml"),
            "-e", f"vm_hostname={hostname}",
            "-e", f"password={password}",
            "-e", f"addusername={addusername}",
            "-e", f"snmp_community={snmp.get('community', 'your-snmp-community')}",
            "-e", f"snmp_source={snmp.get('source', 'default')}",
            "-e", f"snmp_location={snmp.get('location', 'Homelab')}",
            "-e", f"snmp_contact={snmp.get('contact', 'admin@example.com')}",
            "-e", f"timezone={timezone}",
            "-e", json.dumps({"ntp_servers": ntp_servers}),
            "--private-key", ssh_key,
            "--timeout", "60",
        ]
        if profile_packages:
            cmd += ["-e", json.dumps({"profile_packages": list(profile_packages)})]
        if extra_packages:
            cmd += ["-e", json.dumps({"extra_packages": list(extra_packages)})]
        cmd_display = [
            arg.split("=")[0] + "=**REDACTED**" if arg.startswith("password=") else arg
            for arg in cmd
        ]
        console.print(f"  [dim]Running: {' '.join(cmd_display)}[/dim]")
        result = subprocess.run(cmd, cwd=str(ansible_dir))
        if result.returncode != 0:
            raise RuntimeError("Ansible post-deploy-vm playbook failed (see output above)")
    finally:
        os.unlink(inv_path)




# ─────────────────────────────────────────────
# Deployment file helpers
# ─────────────────────────────────────────────

def save_vm_deployment_file(hostname: str, vmid: int, node_name: str,
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
    deployments_dir = Path(__file__).parent / "deployments" / "vms"
    deployments_dir.mkdir(parents=True, exist_ok=True)
    deploy_file = deployments_dir / f"{hostname}.json"
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
    with open(deploy_file, "w") as f:
        json.dump(data, f, indent=2)
    console.print(f"  [dim]Deployment file saved: {deploy_file}[/dim]")
    return deploy_file


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
    parser.add_argument(
        "--deploy-file", metavar="FILE",
        help="JSON deployment file to pre-fill defaults (saved from a previous run)",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Non-interactive mode: use all values from --deploy-file without prompting",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate config.yaml and deployment file without connecting to Proxmox or deploying",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate config + deployment file and print what would happen without making any changes",
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="Run preflight connectivity and dependency checks then exit",
    )
    parser.add_argument(
        "--yolo", action="store_true",
        help="Skip preflight checks and deploy immediately",
    )
    parser.add_argument(
        "--ttl", metavar="TTL",
        help="Time-to-live for this deployment (e.g. 7d, 24h, 2w, 30m). "
             "Stores 'expires_at' in the deployment JSON for use with expire.py.",
    )
    parser.add_argument(
        "--config", metavar="FILE",
        help="Path to an alternate config file (default: config.yaml in project root)",
    )
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

    def step_hostname(s):
        r = pt_text(
            "Hostname for the new VM:",
            default=s.get("hostname", ""),
            instruction="short name only — domain suffix appended in inventory",
            validate=lambda v: True if v.strip() else "Hostname cannot be empty",
            d=deploy, key="hostname", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "hostname": r.strip().lower()}

    def step_cpus(s):
        r = pt_text(
            "Number of vCPUs:",
            default=s.get("cpus_str", str(defaults.get("cpus", 2))),
            validate=lambda v: True if v.isdigit() and int(v) > 0 else "Must be a positive integer",
            d=deploy, key="cpus", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "cpus_str": r}

    def step_memory(s):
        r = pt_text(
            "Memory (GB):",
            default=s.get("memory_gb_str", str(defaults.get("memory_gb", 4))),
            validate=lambda v: (True if v.replace(".", "", 1).isdigit() and float(v) > 0
                                else "Must be a positive number"),
            d=deploy, key="memory_gb", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "memory_gb_str": r}

    def step_disk(s):
        r = pt_text(
            "Disk size (GB):",
            default=s.get("disk_gb_str", str(defaults.get("disk_gb", 100))),
            validate=lambda v: True if v.isdigit() and int(v) > 0 else "Must be a positive integer",
            d=deploy, key="disk_gb", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "disk_gb_str": r}

    def step_vlan(s):
        r = pt_text(
            "VLAN tag (bridge: vmbr0.<vlan>):",
            default=s.get("vlan_str", str(defaults.get("vlan", 220))),
            validate=lambda v: (True if v.isdigit() and 1 <= int(v) <= 4094
                                else "Must be a valid VLAN ID (1–4094)"),
            d=deploy, key="vlan", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "vlan_str": r}

    def step_password(s):
        r = pt_text(
            f"Root / {addusername} user password:",
            default=s.get("password", defaults.get("root_password", "changeme")),
            d=deploy, key="password", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "password": r}

    def step_package_profile(s):
        r = prompt_package_profile(cfg, deploy, silent, nav=True,
                                   current=s.get("package_profile"))
        if r is BACK:
            return BACK
        package_profile, profile_packages, profile_tags = r
        return {**s, "package_profile": package_profile,
                "profile_packages": profile_packages, "profile_tags": profile_tags}

    def step_extra_packages(s):
        r = prompt_extra_packages(deploy, silent, nav=True,
                                  current=s.get("extra_packages"))
        if r is BACK:
            return BACK
        return {**s, "extra_packages": r}

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

    def step_node(s):
        memory_mb = int(float(s["memory_gb_str"]) * 1024)
        r = prompt_node_selection(nodes, deploy, silent, memory_mb, s["memory_gb_str"],
                                  cpu_threshold, ram_threshold, nav=True)
        if r is BACK:
            return BACK
        return {**s, "node_name": r}

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
                "image_refresh": image_refresh}

    def step_storage(s):
        with console.status(f"[bold green]Querying storage pools on {s['node_name']}..."):
            storage_pools = get_vm_disk_storages(proxmox, s["node_name"])
        if len(storage_pools) > 1:
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
            r = questionary.confirm("Proceed with deployment?", default=True).ask()
            if r is None:
                return BACK
            if not r:
                console.print("[yellow]Deployment cancelled.[/yellow]")
                sys.exit(0)
        return {**s, "next_vmid": next_vmid, "bridge": bridge, "now_str": now_str,
                "pub_key_path": pub_key_path, "pub_key_encoded": pub_key_encoded}

    ws = run_wizard_steps([
        step_hostname, step_cpus, step_memory, step_disk, step_vlan, step_password,
        step_package_profile, step_extra_packages,
        step_ip, step_prefix, step_gateway,
        step_node, step_image, step_storage, step_confirm,
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
    storage            = ws["storage"]
    next_vmid          = ws["next_vmid"]
    bridge             = ws["bridge"]
    now_str            = ws["now_str"]
    pub_key_path       = ws["pub_key_path"]
    pub_key_encoded    = ws["pub_key_encoded"]
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

    for _vmid_attempt in range(3):
        create_params["vmid"] = next_vmid
        try:
            with console.status(f"[bold green]Creating VM {next_vmid} ({hostname}) on {node_name}..."):
                task = proxmox.nodes(node_name).qemu.post(**create_params)
                wait_for_task(proxmox, node_name, task, timeout=60)
            console.print(f"[green]✓ VM {next_vmid} created[/green]")
            break
        except Exception as e:
            if "already exists" in str(e) and _vmid_attempt < 2:
                old_vmid = next_vmid
                next_vmid = get_next_vmid(proxmox)
                console.print(
                    f"[yellow]⚠ VMID {old_vmid} already in use (race condition) — "
                    f"retrying with VMID {next_vmid}[/yellow]"
                )
            else:
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
        import_cloud_image(
            cfg, proxmox, node_name, next_vmid, storage,
            image_storage_name, image_filename, image_url,
            image_refresh, catalog,
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
        # Find unused disk (appears as unused0 after qm importdisk)
        vm_config = proxmox.nodes(node_name).qemu(next_vmid).config.get()
        unused_disk = None
        for key in sorted(vm_config.keys()):
            if key.startswith("unused"):
                unused_disk = vm_config[key]
                break
        if not unused_disk:
            raise RuntimeError("Imported disk not found in VM config (no unused0 key)")

        # Attach as scsi0
        proxmox.nodes(node_name).qemu(next_vmid).config.put(scsi0=unused_disk)
        console.print(f"  [dim]Attached {unused_disk} as scsi0[/dim]")

        # Add cloud-init drive on ide2
        proxmox.nodes(node_name).qemu(next_vmid).config.put(ide2=f"{storage}:cloudinit")
        console.print(f"  [dim]Added cloud-init drive (ide2)[/dim]")

        # Enable serial console (required for Ubuntu cloud images)
        proxmox.nodes(node_name).qemu(next_vmid).config.put(serial0="socket", vga="serial0")

        # Set boot order
        proxmox.nodes(node_name).qemu(next_vmid).config.put(boot="order=scsi0")

        # Resize disk to requested size
        proxmox.nodes(node_name).qemu(next_vmid).resize.put(
            disk="scsi0", size=f"{disk_gb_str}G"
        )
        console.print(f"  [dim]Resized scsi0 to {disk_gb_str} GB[/dim]")

        # Configure cloud-init
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
            # Pre-install qemu-guest-agent via cloud-init snippet so it is
            # running when we poll the guest agent API for the DHCP-assigned IP.
            cicustom = write_guest_agent_snippet(cfg, node_name, next_vmid)
            ci_params["cicustom"] = cicustom
        proxmox.nodes(node_name).qemu(next_vmid).config.put(**ci_params)
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
        with console.status("[bold green]Starting VM..."):
            task = proxmox.nodes(node_name).qemu(next_vmid).status.start.post()
            wait_for_task(proxmox, node_name, task, timeout=60)
        console.print("[green]✓ VM started[/green]")
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
            run_ansible_post_deploy_vm(vm_ip, ssh_key, password, hostname, cfg=cfg, profile_packages=profile_packages, extra_packages=extra_packages)
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
    save_vm_deployment_file(
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(1)
