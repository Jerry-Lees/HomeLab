#!/usr/bin/env python3
"""
Proxmox LXC Deploy Wizard
=========================
Interactive wizard to provision, configure, and onboard new LXC containers
in a Proxmox cluster. Handles:
  - Interactive prompts with sensible defaults
  - Node selection (auto picks least-loaded node)
  - Template listing (Ubuntu-first)
  - Container creation via Proxmox API
  - Bootstrap via pct exec (installs SSH in container)
  - Post-deploy Ansible playbook (tools, SNMP, NTP, users, etc.)
  - Ansible inventory update on development server

Requirements:
  pip install -r requirements.txt
  ansible (system package or pip)
  sshpass (system package, needed for Ansible password auth)
"""

# Auto-activate virtualenv so `python3 deploy_lxc.py` works without sourcing .venv
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
                f"Labinator LXC Deploy — {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
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
import os
import socket
import sys
import time
import textwrap
from datetime import datetime, timezone
from pathlib import Path

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
    checkbox_nav,
    BACK,
    SKIP,
    run_wizard_steps,
    load_deployment_file,
    prompt_package_profile,
    prompt_extra_packages,
    prompt_node_selection,
    write_history,
    check_vlan_exists,
    resolve_lxc_features,
    resolve_tag_colors,
    features_list_to_proxmox_str,
    apply_tag_colors,
    add_common_deploy_args,
    print_dry_run_header,
    print_dry_run_footer,
    dry_run_validate_and_load,
    write_deployment_file,
    make_common_wizard_steps,
    validate_lxc_deployment,
    get_lxc_templates,
    get_lxc_disk_storages,
    wait_for_lxc_ip,
    run_pct_exec,
    bootstrap_lxc_ssh,
    check_node_resources,
    create_lxc,
    apply_lxc_features_ssh,
    start_lxc,
    find_lxc_by_hostname,
)

console = Console()


def derive_gateway(ip: str) -> str:
    """Derive gateway as the .1 address of the subnet."""
    parts = ip.rsplit(".", 1)
    return f"{parts[0]}.1"


# LXC feature flag choices shown in the interactive checkbox prompt.
# Values match the Proxmox API feature string format.
LXC_FEATURE_CHOICES = [
    ("nesting=1",  "nesting=1   — nested containers (Docker, Podman, LXC-in-LXC)"),
    ("keyctl=1",   "keyctl=1    — kernel keyring (required by some container runtimes)"),
    ("fuse=1",     "fuse=1      — FUSE filesystem mounts (rclone, sshfs, etc.)"),
    ("mknod=1",    "mknod=1     — create block/character device nodes"),
    ("mount=nfs",  "mount=nfs   — NFS mounts inside the container"),
    ("mount=cifs", "mount=cifs  — CIFS/SMB mounts inside the container"),
]


def run_validate(args) -> None:
    """Run --validate checks, print a rich report, and exit 0 or 1."""
    run_validate_common(args, validate_lxc_deployment)


def run_dry_run(args) -> None:
    """--dry-run: validate config + deployment file, print what would happen, exit 0/1."""
    print_dry_run_header("lxc")
    cfg, d = dry_run_validate_and_load(args, validate_lxc_deployment)

    # ── Derive display values ──
    hostname     = d.get("hostname", "?")
    node         = d.get("node", "?")
    storage      = d.get("storage", "?")
    template     = d.get("template_name", "?")
    cpus         = d.get("cpus", "?")
    memory_gb    = d.get("memory_gb", "?")
    disk_gb      = d.get("disk_gb", "?")
    extra_pkgs   = d.get("extra_packages", [])

    profiles = cfg.get("package_profiles", {})
    profile_packages, profile_tags = resolve_profile(d.get("package_profile", ""), profiles)
    tags = ";".join(["auto-deploy"] + profile_tags)
    lxc_features = d.get("lxc_features", resolve_lxc_features(d.get("package_profile", ""), profiles))

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
    tbl.add_row("Template",    template)
    tbl.add_row("vCPUs",       str(cpus))
    tbl.add_row("Memory",      f"{memory_gb} GB")
    tbl.add_row("Disk",        f"{disk_gb} GB → {storage}")
    ip           = d.get("ip_address", "dhcp")
    ip_display   = ip if ip != "dhcp" else "DHCP (assigned at boot)"
    tbl.add_row("IP",          ip_display)
    tbl.add_row("Profile pkgs", ", ".join(profile_packages) if profile_packages else "(none)")
    tbl.add_row("Extra pkgs",  ", ".join(extra_pkgs) if extra_pkgs else "(none)")
    tbl.add_row("Tags",        tags)
    if lxc_features:
        tbl.add_row("Features",    features_list_to_proxmox_str(lxc_features))
    console.print()
    console.print(Panel(tbl, title="[bold]LXC Deployment Summary[/bold]", border_style="dim"))
    console.print()

    # ── Step-by-step plan ──
    DRY = "[bold yellow][DRY RUN][/bold yellow]"

    console.print("[bold]Steps that would execute:[/bold]")
    console.print()
    console.print(f"  {DRY} Step 1/7  Create LXC container (next available VMID) — {hostname} on {node}")
    console.print(f"  {DRY} Step 2/7  Start container")
    if ip == "dhcp":
        console.print(f"  {DRY} Step 3/7  Wait for DHCP IP address")
    else:
        console.print(f"  {DRY} Step 3/7  [dim]DHCP wait SKIPPED — static IP {ip}[/dim]")
    console.print(f"  {DRY} Step 4/7  Bootstrap SSH via pct exec on {node}")

    if ansible_enabled:
        console.print(f"  {DRY} Step 5/7  Run Ansible post-deploy playbook")
        if profile_packages:
            console.print(f"             [dim]└─ Profile packages : {', '.join(profile_packages)}[/dim]")
        if extra_pkgs:
            console.print(f"             [dim]└─ Extra packages   : {', '.join(extra_pkgs)}[/dim]")
    else:
        console.print(f"  {DRY} Step 5/7  [dim]Ansible post-deploy SKIPPED (ansible.enabled: false)[/dim]")

    if dns_enabled:
        ip_note = "<DHCP-assigned>" if ip == "dhcp" else ip
        console.print(f"  {DRY} Step 6/7  Register DNS: {fqdn} → {ip_note} on {dns_cfg.get('server', '?')}")
    else:
        console.print(f"  {DRY} Step 6/7  [dim]DNS registration SKIPPED (dns.enabled: false)[/dim]")

    if inv_enabled:
        console.print(f"  {DRY} Step 7/7  Update Ansible inventory on {inv_cfg.get('server', '?')}")
    else:
        console.print(f"  {DRY} Step 7/7  [dim]Inventory update SKIPPED (ansible_inventory.enabled: false)[/dim]")

    print_dry_run_footer()


def _save_lxc_deployment_file(hostname: str, vmid: int, node_name: str,
                               template_volid: str, template_name: str,
                               cpus_str: str, memory_gb_str: str, disk_gb_str: str,
                               storage: str, vlan_str: str, bridge: str,
                               password: str, ip_address: str, assigned_ip: str,
                               prefix_len: str, gateway: str,
                               cfg: dict, package_profile: str = "",
                               extra_packages: list = (), lxc_features: list = (),
                               ttl: str = "") -> None:
    """ip_address: 'dhcp' or a static IP. assigned_ip: actual IP received (DHCP case only)."""
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname
    data = {
        "hostname": hostname,
        "fqdn": fqdn,
        "node": node_name,
        "vmid": vmid,
        "template_volid": template_volid,
        "template_name": template_name,
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
        "lxc_features": list(lxc_features),
        "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if assigned_ip and assigned_ip != ip_address:
        data["assigned_ip"] = assigned_ip  # DHCP-assigned address for DNS/decomm
    if ttl:
        data["ttl"] = ttl
        data["expires_at"] = expires_at_from_ttl(ttl)
    write_deployment_file(data, hostname, "lxc", cfg)



# ─────────────────────────────────────────────
# Main wizard
# ─────────────────────────────────────────────

def main() -> None:
    _start_time = time.time()
    # ── Parse CLI arguments ──
    if "--?" in sys.argv:
        sys.argv[sys.argv.index("--?")] = "--help"
    parser = argparse.ArgumentParser(
        prog="deploy_lxc.py",
        description="Proxmox LXC Deploy Wizard — interactive provisioning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 deploy_lxc.py
              python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json
              python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
              python3 deploy_lxc.py --validate
              python3 deploy_lxc.py --validate --deploy-file deployments/lxc/myserver.json
              python3 deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json
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
        run_dry_run(args)  # exits 0 or 1

    if args.preflight:
        cfg = load_config(args.config)
        deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}
        run_preflight(cfg, kind="lxc", silent=args.silent, verbose=True,
                      deploy=deploy if args.deploy_file else None, yolo=args.yolo,
                      config_path=Path(args.config) if args.config else None)
        sys.exit(0)

    if args.silent and not args.deploy_file:
        parser.error("--silent requires --deploy-file")

    cfg = load_config(args.config)
    defaults = cfg["defaults"]
    profiles = cfg.get("package_profiles", {})
    addusername = defaults.get("addusername", "admin")
    cpu_threshold = float(defaults.get("cpu_threshold", 0.85))
    ram_threshold = float(defaults.get("ram_threshold", 0.95))
    unprivileged = 1 if defaults.get("unprivileged", True) else 0
    firewall_enabled = 1 if defaults.get("firewall_enabled", False) else 0

    # Load deployment file if given (provides defaults or silent values)
    deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}
    silent = args.silent

    console.print()
    console.print(Panel.fit(
        Text("Proxmox LXC Deploy Wizard\n", style="bold cyan", justify="center") +
        Text("github.com: Jerry-Lees/HomeLab/labinator", style="dim cyan", justify="center"),
        border_style="cyan",
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
        run_preflight(cfg, kind="lxc", silent=silent, verbose=True,
                      deploy=deploy if args.deploy_file else None, yolo=args.yolo,
                      config_path=Path(args.config) if args.config else None)

    # ── Connect to Proxmox ──
    with console.status("[bold green]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
            nodes = get_nodes_with_load(proxmox, storage_content="rootdir")
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
                                    hostname_label="container")

    def step_ip(s):
        deploy_ip = str(deploy.get("ip_address", ""))
        ip_default = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
        if silent:
            ip_address = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
        else:
            r = pt_text(
                "IP address for container:",
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
        if silent and "prefix_len" not in deploy:
            default_pl = s.get("prefix_len", "24")
            console.print(f"  [dim]Prefix length (default): {default_pl}[/dim]")
            return {**s, "prefix_len": default_pl}
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
        if silent and "gateway" not in deploy:
            console.print(f"  [dim]Gateway (derived): {auto_gw}[/dim]")
            return {**s, "gateway": auto_gw}
        r = pt_text(
            "Gateway:",
            default=s.get("gateway", auto_gw),
            validate=lambda v: True if v.count(".") == 3 else "Enter a valid IPv4 address",
            d=deploy, key="gateway", silent=silent,
        )
        if r is BACK:
            return BACK
        return {**s, "gateway": r.strip()}

    def step_lxc_features(s):
        profile_features = resolve_lxc_features(s.get("package_profile", ""), profiles)
        deploy_features  = deploy.get("lxc_features", profile_features)
        current_features = s.get("lxc_features", deploy_features)
        if silent:
            console.print(
                f"  [dim]LXC features: "
                f"{features_list_to_proxmox_str(current_features) or '(none)'}[/dim]"
            )
            return {**s, "lxc_features": current_features}
        feature_choices = [
            questionary.Choice(title=title, value=key)
            for key, title in LXC_FEATURE_CHOICES
        ]
        r = checkbox_nav(
            "LXC feature flags (optional):",
            feature_choices,
            defaults=current_features,
        )
        if r is BACK:
            return BACK
        return {**s, "lxc_features": r}

    def step_template(s):
        with console.status(f"[bold green]Fetching templates from {s['node_name']}..."):
            templates = get_lxc_templates(proxmox, s["node_name"])
        if not templates:
            console.print(f"[red]No LXC templates found on {s['node_name']}.[/red]")
            console.print("Download templates in Proxmox: local storage > CT Templates > Templates")
            sys.exit(1)
        if silent:
            template_volid = str(deploy.get("template_volid", ""))
            if not template_volid:
                template_volid = templates[0]["volid"]
            elif not any(t["volid"] == template_volid for t in templates):
                console.print(
                    f"[yellow]Warning: Template '{template_volid}' not found on "
                    f"{s['node_name']}. Using first available.[/yellow]"
                )
                template_volid = templates[0]["volid"]
            template_name = template_volid.split("/")[-1]
            console.print(f"  [dim]Template (from deployment file): {template_name}[/dim]")
        else:
            template_choices = [
                questionary.Choice(title=f"[{t['storage']}] {t['name']}", value=t["volid"])
                for t in templates
            ]
            deploy_volid = str(deploy.get("template_volid", ""))
            default_tmpl_name = defaults.get("template", "")
            default_volid = (
                deploy_volid if deploy_volid and any(t["volid"] == deploy_volid for t in templates)
                else next(
                    (t["volid"] for t in templates if t["name"] == default_tmpl_name),
                    templates[0]["volid"],
                )
            )
            r = select_nav(
                "Select OS template (Ubuntu templates listed first):",
                choices=template_choices,
                default=s.get("template_volid", default_volid),
            )
            if r is BACK:
                return BACK
            template_volid = r
            template_name = template_volid.split("/")[-1]
        return {**s, "template_volid": template_volid, "template_name": template_name}

    def step_storage(s):
        with console.status(f"[bold green]Querying storage pools on {s['node_name']}..."):
            storage_pools = get_lxc_disk_storages(proxmox, s["node_name"])
        if silent:
            storage = str(deploy.get("storage", storage_pools[0] if storage_pools else "local-lvm"))
            console.print(f"  [dim]Storage (from deployment file): {storage}[/dim]")
        elif len(storage_pools) > 1:
            deploy_storage = str(deploy.get("storage", ""))
            default_storage = deploy_storage if deploy_storage in storage_pools else storage_pools[0]
            r = select_nav(
                "Select storage pool for container root disk:",
                choices=storage_pools,
                default=s.get("storage", default_storage),
            )
            if r is BACK:
                return BACK
            storage = r
        else:
            storage = storage_pools[0] if storage_pools else "local-lvm"
            console.print(f"  [dim]Storage pool: {storage}[/dim]")
        return {**s, "storage": storage}

    def step_confirm(s):
        next_vmid = get_next_vmid(proxmox)
        bridge = defaults.get("bridge", "vmbr0")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory_mb = int(float(s["memory_gb_str"]) * 1024)
        console.print()
        table = Table(title="Deployment Summary", show_header=False,
                      border_style="cyan", padding=(0, 2))
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("VMID",        str(next_vmid))
        table.add_row("Hostname",    s["hostname"])
        table.add_row("Node",        s["node_name"])
        table.add_row("Template",    s["template_name"])
        table.add_row("vCPUs",       s["cpus_str"])
        table.add_row("Memory",      f"{s['memory_gb_str']} GB ({memory_mb} MB)")
        table.add_row("Swap",        f"{s['memory_gb_str']} GB ({memory_mb} MB)")
        table.add_row("Disk",        f"{s['disk_gb_str']} GB  →  {s['storage']}")
        _use_dhcp = s.get("use_dhcp", True)
        net_detail = (
            f"(DHCP — IP assigned at boot)"
            if _use_dhcp else
            f"(static {s.get('ip_address', '')}/"
            f"{s.get('prefix_len', '24')}  gw {s.get('gateway', '')})"
        )
        table.add_row("Network",     f"{bridge}.{s['vlan_str']}  {net_detail}")
        tags_display = (";".join(["auto-deploy"] + s["profile_tags"])
                        if s["profile_tags"] else "auto-deploy")
        table.add_row("Tags",        tags_display)
        lxc_features = s.get("lxc_features", [])
        if lxc_features:
            table.add_row("Features",    features_list_to_proxmox_str(lxc_features))
        if ttl:
            table.add_row("TTL / Expires",
                          f"{ttl}  (expires {expires_at_from_ttl(ttl)[:19]} UTC)")
        table.add_row("Users",       f"root, {addusername} (same password)")
        table.add_row("Timezone",    cfg.get("timezone", "UTC"))
        table.add_row("NTP",         ", ".join(cfg.get("ntp", {}).get("servers", ["pool.ntp.org"])))
        table.add_row("SNMP",        f"community='{cfg['snmp']['community']}' (rw) on :161")
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
        return {**s, "next_vmid": next_vmid, "bridge": bridge, "now_str": now_str}

    ws = run_wizard_steps([
        _ws["hostname"], _ws["cpus"], _ws["memory"], _ws["disk"], _ws["vlan"], _ws["password"],
        step_ip, step_prefix, step_gateway,
        _ws["package_profile"], _ws["extra_packages"], step_lxc_features,
        _ws["node"], step_template, step_storage, step_confirm,
    ])

    # Unpack wizard state into local variables for the rest of the deploy flow
    hostname         = ws["hostname"]
    cpus_str         = ws["cpus_str"]
    memory_gb_str    = ws["memory_gb_str"]
    disk_gb_str      = ws["disk_gb_str"]
    vlan_str         = ws["vlan_str"]
    password         = ws["password"]
    use_dhcp         = ws.get("use_dhcp", True)
    ip_address       = "" if use_dhcp else ws.get("ip_address", "")
    prefix_len       = "" if use_dhcp else ws.get("prefix_len", "24")
    gateway          = "" if use_dhcp else ws.get("gateway", "")
    package_profile  = ws["package_profile"]
    profile_packages = ws["profile_packages"]
    profile_tags     = ws["profile_tags"]
    extra_packages   = ws["extra_packages"]
    lxc_features     = ws.get("lxc_features", [])
    node_name        = ws["node_name"]
    template_volid   = ws["template_volid"]
    template_name    = ws["template_name"]
    storage          = ws["storage"]
    next_vmid        = ws["next_vmid"]
    bridge           = ws["bridge"]
    now_str          = ws["now_str"]
    memory_mb        = int(float(memory_gb_str) * 1024)

    # ── Auto-nesting: silently add nesting=1 for templates that require it ──
    _auto_nesting_patterns = cfg.get("lxc_auto_nesting_templates", ["ubuntu-24.04"])
    if (
        any(pat in template_name for pat in _auto_nesting_patterns)
        and "nesting=1" not in lxc_features
    ):
        lxc_features = list(lxc_features) + ["nesting=1"]
        console.print(
            f"  [dim]Auto-enabled nesting=1 for {template_name} "
            f"(systemd 255 requires nesting — see lxc_auto_nesting_templates in config.yaml)[/dim]"
        )

    # ── Pre-creation resource re-check ──
    console.print("[dim]Pre-creation resource check...[/dim]")
    ok, reason = check_node_resources(proxmox, node_name, memory_mb, int(disk_gb_str), storage, cpu_threshold, ram_threshold)
    if not ok and reason.startswith("Could not verify resources:"):
        # API connection dropped while wizard was open — reconnect and retry once
        console.print("[dim]API connection dropped — reconnecting...[/dim]")
        try:
            proxmox = connect_proxmox(cfg)
            ok, reason = check_node_resources(proxmox, node_name, memory_mb, int(disk_gb_str), storage, cpu_threshold, ram_threshold)
        except Exception as e:
            ok, reason = False, f"Could not reconnect: {e}"
    if not ok:
        console.print(f"[red]✗ Resource check failed: {reason}[/red]")
        console.print("[red]Deployment aborted. Resources may have changed since node selection.[/red]")
        sys.exit(1)
    console.print("[green]✓ Resources verified[/green]")

    # ── VLAN existence check ──
    check_vlan_exists(proxmox, node_name, bridge, vlan_str, silent=silent)

    # ── Duplicate hostname check ──
    existing = find_lxc_by_hostname(proxmox, hostname)
    if existing:
        console.print(
            f"[red]✗ A container named '{hostname}' already exists on the cluster:[/red]\n"
            f"  VMID {existing['vmid']} on {existing['node']} (status: {existing['status']})\n"
            f"  Decomm it first, or remove it manually."
        )
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Create the container
    # ═══════════════════════════════════════════
    console.print()
    console.print("[bold cyan]─── Step 1/7: Creating LXC container ───[/bold cyan]")

    container_note = textwrap.dedent(f"""\
        Auto-deployed by deploy_lxc.py
        ─────────────────────────────────────
        Created    : {now_str}
        Node       : {node_name}
        Template   : {template_name}
        vCPUs      : {cpus_str}
        Memory     : {memory_gb_str} GB
        Swap       : {memory_gb_str} GB
        Disk       : {disk_gb_str} GB ({storage})
        VLAN       : {vlan_str}
        Network    : {bridge}.{vlan_str} / {'DHCP' if use_dhcp else f'{ip_address}/{prefix_len} gw {gateway}'}
        Timezone   : {cfg.get('timezone', 'UTC')}
        NTP        : {', '.join(cfg.get('ntp', {}).get('servers', ['pool.ntp.org']))}
        SNMP       : community={cfg.get('snmp', {}).get('community', 'your-snmp-community')} (rw)
        ─────────────────────────────────────
        Users: root, {addusername} (same password)
    """)

    create_params = {
        "vmid":         next_vmid,
        "hostname":     hostname,
        "ostemplate":   template_volid,
        "cores":        int(cpus_str),
        "memory":       memory_mb,
        "swap":         memory_mb,
        "rootfs":       f"{storage}:{disk_gb_str}",
        "net0":         (
            f"name=eth0,bridge={bridge},tag={vlan_str},ip=dhcp,firewall={firewall_enabled}"
            if use_dhcp else
            f"name=eth0,bridge={bridge},tag={vlan_str},ip={ip_address}/{prefix_len},gw={gateway},firewall={firewall_enabled}"
        ),
        "password":     password,
        "unprivileged": unprivileged,
        "onboot":       1 if defaults.get("onboot", True) else 0,
        "start":        0,   # We start it explicitly after tagging
        "nameserver":   defaults.get("nameserver", "8.8.8.8 8.8.4.4"),
        "searchdomain": defaults.get("searchdomain", ""),
        "description":  container_note,
        "tags":         ";".join(["auto-deploy"] + profile_tags),
    }
    features_str = features_list_to_proxmox_str(lxc_features)
    # nesting=1 is allowed via API; all other flags require root@pam SSH (Proxmox restriction).
    # Apply nesting via create_params if present; apply remaining flags via pct set over SSH.
    nesting_only = "nesting=1" if "nesting=1" in lxc_features else ""
    if nesting_only:
        create_params["features"] = nesting_only

    try:
        next_vmid = create_lxc(proxmox, node_name, create_params)
    except Exception as e:
        console.print(f"[red]✗ Container creation failed: {e}[/red]")
        sys.exit(1)

    # Write a stub deployment file immediately so this container is tracked even if
    # a later step fails (DHCP timeout, Ansible error, etc.).  The file is overwritten
    # with full data at the end of a successful deployment.
    _save_lxc_deployment_file(
        hostname, next_vmid, node_name, template_volid, template_name,
        cpus_str, memory_gb_str, disk_gb_str, storage, vlan_str, bridge,
        password, "dhcp" if use_dhcp else ip_address, "", prefix_len, gateway, cfg,
        package_profile=package_profile,
        extra_packages=extra_packages,
        lxc_features=lxc_features,
        ttl=ttl or "",
    )

    if features_str:
        apply_lxc_features_ssh(cfg, node_name, next_vmid, features_str)

    # Apply tag colors to cluster (non-fatal if it fails)
    tag_colors = resolve_tag_colors(package_profile, profiles)
    apply_tag_colors(proxmox, tag_colors)

    # ═══════════════════════════════════════════
    # Start the container
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 2/7: Starting container ───[/bold cyan]")
    try:
        start_lxc(proxmox, node_name, next_vmid)
    except Exception as e:
        console.print(f"[red]✗ Failed to start container: {e}[/red]")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Wait for IP (DHCP) or use static
    # ═══════════════════════════════════════════
    if use_dhcp:
        console.print("[bold cyan]─── Step 3/7: Waiting for DHCP IP address ───[/bold cyan]")
        try:
            with console.status("[bold green]Polling for DHCP lease (up to 5 min)..."):
                container_ip, prefix_len = wait_for_lxc_ip(proxmox, node_name, next_vmid, timeout=300)
            console.print(f"[green]✓ Container IP: [bold]{container_ip}[/bold] /{prefix_len}[/green]")
        except TimeoutError as e:
            console.print(f"[red]✗ {e}[/red]")
            console.print(f"  You can still SSH to the Proxmox node and run: pct exec {next_vmid} -- ip addr")
            sys.exit(1)
    else:
        console.print("[bold cyan]─── Step 3/7: Static IP — no DHCP wait needed ───[/bold cyan]")
        container_ip = ip_address
        console.print(f"  [dim]Using static IP: {container_ip}/{prefix_len}  gw {gateway}[/dim]")

    # ═══════════════════════════════════════════
    # Bootstrap SSH via pct exec
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 4/7: Bootstrapping SSH in container ───[/bold cyan]")
    try:
        bootstrap_lxc_ssh(cfg, node_name, next_vmid, password)
    except Exception as e:
        console.print(f"[red]✗ Bootstrap failed: {e}[/red]")
        sys.exit(1)

    # Wait for SSH to be ready before handing off to Ansible
    console.print("  [dim]Waiting for SSH to become reachable...[/dim]")
    try:
        wait_for_ssh(container_ip, timeout=60)
        console.print("  [green]✓ SSH is ready[/green]")
    except TimeoutError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Ansible post-deploy
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 5/7: Running post-deployment configuration (Ansible) ───[/bold cyan]")
    if cfg.get("ansible", {}).get("enabled", True):
        post_deploy_password = password
        for attempt in range(2):
            try:
                run_ansible_post_deploy(
                    container_ip, post_deploy_password, hostname, cfg, kind="lxc",
                    nameserver=defaults.get("nameserver", "8.8.8.8 8.8.4.4"),
                    searchdomain=defaults.get("searchdomain", ""),
                    profile_packages=profile_packages,
                    extra_packages=extra_packages,
                )
                console.print("[green]✓ Post-deployment configuration complete[/green]")
                break
            except Exception as e:
                console.print(f"[red]✗ Post-deploy failed: {e}[/red]")
                if attempt == 0 and not silent:
                    console.print("[yellow]This may be a password mismatch. "
                                  "Enter the container's current root password to retry.[/yellow]")
                    retry_pw = questionary.password("Container root password:").ask()
                    if retry_pw:
                        post_deploy_password = retry_pw
                        continue
                sys.exit(1)
    else:
        console.print("  [dim]Skipped (ansible.enabled: false) — configure host manually[/dim]")

    # ═══════════════════════════════════════════
    # Register DNS
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 6/7: Registering DNS records ───[/bold cyan]")
    dns_action = dns_precheck(cfg, hostname, container_ip, silent=silent)
    if dns_action == "abort":
        sys.exit(1)
    elif dns_action == "proceed":
        run_ansible_add_dns(cfg, hostname, container_ip)
    else:
        console.print("  [dim]DNS registration skipped — existing record kept.[/dim]")

    # ═══════════════════════════════════════════
    # Update Ansible inventory
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 7/7: Updating Ansible inventory ───[/bold cyan]")
    run_ansible_inventory_update(cfg, hostname, container_ip, password)

    # ── Save deployment file ──
    _save_lxc_deployment_file(
        hostname, next_vmid, node_name, template_volid, template_name,
        cpus_str, memory_gb_str, disk_gb_str, storage, vlan_str, bridge,
        password,
        "dhcp" if use_dhcp else ip_address,  # ip_address field
        container_ip if use_dhcp else "",     # assigned_ip (DHCP-discovered IP)
        prefix_len, gateway, cfg,
        package_profile=package_profile,
        extra_packages=extra_packages,
        lxc_features=lxc_features,
        ttl=ttl or "",
    )

    # Health check (optional — runs if health_check.enabled in config)
    health_check(container_ip, password, addusername, cfg)

    write_history({
        "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user":             os.getenv("USER") or os.getenv("LOGNAME") or "unknown",
        "action":           "deploy",
        "type":             "lxc",
        "hostname":         hostname,
        "fqdn":             f"{hostname}.{cfg['proxmox'].get('node_domain', '')}".strip("."),
        "node":             node_name,
        "vmid":             next_vmid,
        "ip":               container_ip,
        "result":           "success",
        "duration_seconds": round(time.time() - _start_time),
    })

    # ═══════════════════════════════════════════
    # Done!
    # ═══════════════════════════════════════════
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname
    console.print()
    console.print(Panel(
        textwrap.dedent(f"""\
            [bold green]Deployment Complete![/bold green]

            [bold]Hostname   :[/bold]  {hostname}
            [bold]FQDN       :[/bold]  {fqdn}
            [bold]IP Address :[/bold]  {container_ip}{"  (DHCP-assigned)" if use_dhcp else ""}
            [bold]VMID       :[/bold]  {next_vmid}  (on {node_name})
            [bold]SSH        :[/bold]  ssh root@{container_ip}
                      ssh {addusername}@{container_ip}

            [dim]Deployment file: deployments/lxc/{hostname}.json[/dim]
            [dim]Tagged 'auto-deploy' with specs note in Proxmox.[/dim]
            [dim]DNS: A + PTR records registered on {cfg.get('dns', {}).get('server', 'N/A')}.[/dim]
            [dim]Added to Ansible inventory group [{cfg['ansible_inventory']['group']}].[/dim]
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
