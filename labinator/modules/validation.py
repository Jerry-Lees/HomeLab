"""
labinator.validation — Config and deployment file validation.
"""

import argparse
import ipaddress
import json
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

_ROOT = Path(__file__).parent.parent


def _check_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def validate_config(cfg_path: Path) -> list[str]:
    """Return a list of error strings; empty means config is valid."""
    errors = []
    if not cfg_path.exists():
        return [f"config.yaml not found at {cfg_path}"]
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"config.yaml is not valid YAML: {e}"]
    if not isinstance(cfg, dict):
        return ["config.yaml is empty or not a YAML mapping"]

    px = cfg.get("proxmox") or {}
    if not px.get("host") and not px.get("hosts"):
        errors.append("proxmox.host (or proxmox.hosts list) is required")
    if not px.get("user"):
        errors.append("proxmox.user is required")
    if not px.get("token_name"):
        errors.append("proxmox.token_name is required")
    secret = str(px.get("token_secret", ""))
    if not secret:
        errors.append("proxmox.token_secret is required")
    elif "CHANGEME" in secret:
        errors.append("proxmox.token_secret still contains a placeholder value")

    defaults = cfg.get("defaults") or {}
    if not defaults.get("addusername"):
        errors.append("defaults.addusername is required")

    snmp = cfg.get("snmp") or {}
    if not snmp.get("community"):
        errors.append("snmp.community is required")

    ntp = cfg.get("ntp") or {}
    servers = ntp.get("servers")
    if not servers or not isinstance(servers, list):
        errors.append("ntp.servers must be a non-empty list")

    if not cfg.get("timezone"):
        errors.append("timezone is required")

    return errors


def validate_deployment_common(d: dict, required_string_fields: tuple | list) -> list[str]:
    """Validate deployment JSON fields common to both LXC and VM types.

    Checks: required string fields, cpus, memory_gb, disk_gb, vlan,
    ip_address/prefix_len, extra_packages.
    Caller is responsible for loading the file and the type-guard check.
    Returns list of error strings.
    """
    errors = []
    for field in required_string_fields:
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


def run_validate_common(args: argparse.Namespace, validate_fn) -> None:
    """Run --validate checks, print a rich report, and exit 0 or 1.

    validate_fn: callable(Path) -> list[str] for deployment-file validation.
    """
    cfg_path = _ROOT / "config.yaml"
    all_errors: list[tuple[str, str]] = []

    cfg_errors = validate_config(cfg_path)
    for e in cfg_errors:
        all_errors.append(("config.yaml", e))

    if args.deploy_file:
        deploy_errors = validate_fn(Path(args.deploy_file))
        for e in deploy_errors:
            all_errors.append((args.deploy_file, e))

    console.print()
    console.print(Panel.fit(
        Text("Labinator Validate", style="bold yellow"),
        border_style="yellow",
    ))
    console.print()

    if not all_errors:
        console.print("[green]✓ config.yaml[/green]  OK")
        if args.deploy_file:
            console.print(f"[green]✓ {args.deploy_file}[/green]  OK")
        console.print()
        console.print("[bold green]All checks passed.[/bold green]")
        sys.exit(0)

    table = Table(show_header=True, header_style="bold red")
    table.add_column("File", style="dim")
    table.add_column("Error")
    for section, msg in all_errors:
        table.add_row(section, msg)
    console.print(table)
    console.print()
    console.print(f"[bold red]{len(all_errors)} error(s) found. Fix them before deploying.[/bold red]")
    sys.exit(1)


def dry_run_validate_and_load(args: argparse.Namespace, validate_fn) -> tuple[dict, dict]:
    """Validate config + deploy file for --dry-run; print errors and exit on failure.

    Returns (cfg, d) where d is the loaded deployment dict.
    Exits 0 if no --deploy-file (config is valid but nothing more to report).
    validate_fn: callable(Path) -> list[str]
    """
    cfg_path = _ROOT / "config.yaml"
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

    deploy_errors = validate_fn(Path(args.deploy_file))
    if deploy_errors:
        for e in deploy_errors:
            console.print(f"[red]✗ {args.deploy_file}: {e}[/red]")
        sys.exit(1)
    console.print(f"[green]✓ {args.deploy_file}[/green]  OK")

    with open(args.deploy_file) as f:
        d = json.load(f)

    return cfg, d


def node_passes_filter(n: dict, memory_mb: int, cpu_threshold: float = 0.85,
                       ram_threshold: float = 0.95) -> bool:
    """Return True if a node can accommodate the requested resources."""
    if n["cpu"] >= cpu_threshold:
        return False
    if n["maxmem"] > 0:
        used_after = n["mem"] + memory_mb * 1024 * 1024
        if used_after / n["maxmem"] >= ram_threshold:
            return False
    return True


def check_vlan_exists(proxmox, node: str, bridge: str, vlan: int | str,
                      silent: bool = False) -> None:
    """Verify that the requested VLAN is reachable on the target node before deploying.

    Checks for either:
      - An interface named {bridge}.{vlan} in the node's network list, OR
      - A VLAN-aware bridge named {bridge} (Proxmox VLAN-aware mode — individual
        sub-interfaces don't appear, but any VLAN tag is valid)

    Non-fatal by design:
      - silent=True : warn and continue
      - silent=False: prompt the user to continue or abort
    On any API error the check is silently skipped.
    """
    import questionary
    vlan_str = str(vlan)
    iface_name = f"{bridge}.{vlan_str}"
    try:
        interfaces = proxmox.nodes(node).network.get()
    except Exception:
        console.print(f"  [dim]VLAN check skipped (could not query network interfaces on {node})[/dim]")
        return

    iface_names = {i.get("iface", "") for i in interfaces}
    # VLAN-aware bridge: the bridge itself accepts any 802.1q tag — sub-interfaces won't appear
    vlan_aware = any(
        i.get("iface") == bridge and i.get("bridge_vlan_aware")
        for i in interfaces
    )

    if iface_name in iface_names or vlan_aware:
        reason = "VLAN-aware bridge" if vlan_aware and iface_name not in iface_names else iface_name
        console.print(f"  [green]✓ VLAN {vlan_str} verified on {node} ({reason})[/green]")
        return

    # Not found — advisory warning
    console.print(
        f"  [yellow]⚠  VLAN {vlan_str} ({iface_name}) not found on {node}.[/yellow]\n"
        f"  [dim]The container may boot with no network. Verify the VLAN exists on the node.[/dim]"
    )
    if silent:
        console.print("  [dim]--silent: continuing despite missing VLAN.[/dim]")
        return

    proceed = questionary.confirm(
        f"VLAN {vlan_str} was not found on {node}. Continue anyway?",
        default=False,
    ).ask()
    if not proceed:
        console.print("[yellow]Deployment aborted.[/yellow]")
        sys.exit(0)


def validate_lxc_deployment(deploy_path: Path) -> list[str]:
    """Return a list of error strings; empty means the LXC deployment JSON is valid."""
    try:
        with open(deploy_path) as f:
            d = json.load(f)
    except FileNotFoundError:
        return [f"File not found: {deploy_path}"]
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]
    if not isinstance(d, dict):
        return ["Deployment file is not a JSON object"]
    if d.get("type") == "vm":
        return ['This looks like a VM deployment file ("type": "vm") — use deploy_vm.py instead']
    return validate_deployment_common(
        d, ("hostname", "node", "template_name", "storage", "bridge", "password")
    )


def validate_vm_deployment(deploy_path: Path) -> list[str]:
    """Return a list of error strings; empty means the VM deployment JSON is valid."""
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
    return validate_deployment_common(
        d, ("hostname", "node", "cloud_image_storage", "cloud_image_filename",
            "storage", "bridge", "password")
    )
