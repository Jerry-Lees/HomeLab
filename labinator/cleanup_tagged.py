#!/usr/bin/env python3
"""
Proxmox Tagged Resource Cleanup
================================
Finds all VMs and LXC containers tagged 'auto-deploy' across the entire
Proxmox cluster and offers to decommission them interactively.

  - Displays a table of all tagged resources (node, type, VMID, status, IP)
  - Lets you select which ones to decommission (multi-select)
  - Runs the same scary confirmation challenge for each selection
  - Stops and destroys the VM/container, removes DNS and inventory entries
  - --dry-run: list matching resources and exit without touching anything

THIS IS IRREVERSIBLE. Use with extreme caution.
"""

# Auto-activate virtualenv so `python3 cleanup_tagged.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import json
import re
import socket
import subprocess
import questionary
from pathlib import Path
from proxmoxer import ProxmoxAPI
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from modules.lib import (
    load_config,
    connect_proxmox,
    wait_for_task,
    remove_dns,
    remove_from_inventory,
    confirm_destruction,
    flush_stdin,
    SKULL,
)

console = Console()

_ROOT = Path(__file__).parent
_TAG_RE = re.compile(r'^[a-zA-Z0-9._-]{1,64}$')
DEFAULT_TAG = "auto-deploy"


def _validate_tag(value: str) -> str:
    value = value.strip()
    if not _TAG_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"Invalid tag '{value}': tags must be 1–64 characters, "
            "alphanumeric, hyphens, underscores, and dots only"
        )
    return value


# ─────────────────────────────────────────────
# Proxmox scanning
# ─────────────────────────────────────────────

def _is_valid_ipv4(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _extract_ip_from_config(config: dict) -> str:
    """Step 1: Pull IP from Proxmox VM/LXC config (static IPs only)."""
    # VMs: ipconfig0 = "ip=x.x.x.x/24,gw=..."
    # LXCs: net0 = "name=eth0,bridge=vmbr0,ip=x.x.x.x/24,..."
    for key in ("ipconfig0", "net0", "net1"):
        val = config.get(key, "")
        for part in val.split(","):
            if part.startswith("ip=") and part[3:] != "dhcp":
                return part[3:].split("/")[0]
    return ""


def _ip_from_deploy_json(hostname: str, kind: str) -> str:
    """Step 2: Check the local deployment JSON for a stored IP (assigned_ip / ip_address)."""
    folder = "lxc" if kind == "lxc" else "vms"
    path = _ROOT / "deployments" / folder / f"{hostname}.json"
    if not path.exists():
        return ""
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("assigned_ip") or data.get("ip_address", "")
    except Exception:
        return ""


def _ip_from_proxmox_api(proxmox: ProxmoxAPI, node: str, vmid: str, kind: str) -> str:
    """Step 2: Ask Proxmox for live interface data (works for running containers/VMs)."""
    try:
        if kind == "lxc":
            ifaces = proxmox.nodes(node).lxc(vmid).interfaces.get()
            for iface in ifaces:
                inet = iface.get("inet", "")
                if inet:
                    ip = inet.split("/")[0]
                    if not ip.startswith("127.") and not ip.startswith("169.254."):
                        return ip
        else:
            result = proxmox.nodes(node).qemu(vmid).agent("network-get-interfaces").get()
            for iface in result.get("result", []):
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4":
                        ip = addr.get("ip-address", "")
                        if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                            return ip
    except Exception:
        pass
    return ""


def _ip_from_dns(hostname: str, cfg: dict) -> str:
    """Step 3: DNS lookup — try configured DNS server first, then system resolver."""
    dns_server = cfg.get("dns", {}).get("server", "")

    if dns_server:
        try:
            result = subprocess.run(
                ["dig", f"@{dns_server}", hostname, "+short", "+time=3", "+tries=1"],
                capture_output=True, text=True, timeout=5,
            )
            ip = result.stdout.strip().split("\n")[0]
            if ip and _is_valid_ipv4(ip):
                return ip
        except Exception:
            pass

    try:
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return ""


def _resolve_ip(proxmox: ProxmoxAPI, node: str, vmid: str, kind: str,
                hostname: str, config: dict, cfg: dict) -> str:
    """Resolve a resource's IP using every available method in order:
    1. Proxmox config (static IP)
    2. Local deployment JSON (assigned_ip from deploy time)
    3. Proxmox live interfaces API (running containers/VMs)
    4. DNS lookup (configured server, then system resolver)
    """
    return (
        _extract_ip_from_config(config)
        or _ip_from_deploy_json(hostname, kind)
        or _ip_from_proxmox_api(proxmox, node, vmid, kind)
        or _ip_from_dns(hostname, cfg)
        or ""
    )


def _has_tag(resource: dict, tag: str) -> bool:
    tags_raw = resource.get("tags", "") or ""
    return tag in [t.strip() for t in tags_raw.replace(";", ",").split(",")]


def scan_tagged_resources(proxmox: ProxmoxAPI, cfg: dict, tag: str = DEFAULT_TAG) -> list[dict]:
    """Return list of dicts describing every auto-deploy tagged VM/LXC in the cluster."""
    found = []

    for node_info in proxmox.nodes.get():
        node = node_info["node"]

        # LXC containers
        try:
            for ct in proxmox.nodes(node).lxc.get():
                if not _has_tag(ct, tag):
                    continue
                vmid = ct["vmid"]
                hostname = ct.get("name", str(vmid))
                try:
                    px_cfg = proxmox.nodes(node).lxc(vmid).config.get()
                except Exception:
                    px_cfg = {}
                found.append({
                    "kind":        "lxc",
                    "node":        node,
                    "vmid":        str(vmid),
                    "hostname":    hostname,
                    "status":      ct.get("status", "?"),
                    "ip":          _resolve_ip(proxmox, node, str(vmid), "lxc", hostname, px_cfg, cfg),
                    "tags":        ct.get("tags", ""),
                    "matched_tag": tag,
                })
        except Exception as e:
            console.print(f"  [yellow]Warning: could not list LXCs on {node}: {e}[/yellow]")

        # QEMU VMs
        try:
            for vm in proxmox.nodes(node).qemu.get():
                if not _has_tag(vm, tag):
                    continue
                vmid = vm["vmid"]
                hostname = vm.get("name", str(vmid))
                try:
                    px_cfg = proxmox.nodes(node).qemu(vmid).config.get()
                except Exception:
                    px_cfg = {}
                found.append({
                    "kind":        "vm",
                    "node":        node,
                    "vmid":        str(vmid),
                    "hostname":    hostname,
                    "status":      vm.get("status", "?"),
                    "ip":          _resolve_ip(proxmox, node, str(vmid), "vm", hostname, px_cfg, cfg),
                    "tags":        vm.get("tags", ""),
                    "matched_tag": tag,
                })
        except Exception as e:
            console.print(f"  [yellow]Warning: could not list VMs on {node}: {e}[/yellow]")

    return found


# ─────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────

def print_resource_table(resources: list[dict], title: str = "") -> None:
    t = Table(title=title or f"Resources tagged '{TAG}'", border_style="red")
    t.add_column("#",        style="dim",          width=4)
    t.add_column("Hostname", style="bold")
    t.add_column("Type",     style="cyan",         width=6)
    t.add_column("VMID",     style="yellow",       width=7)
    t.add_column("Node",     style="magenta")
    t.add_column("Status",   style="green")
    t.add_column("IP",       style="blue")

    for idx, r in enumerate(resources, 1):
        status_fmt = (
            "[green]running[/green]" if r["status"] == "running"
            else "[dim]stopped[/dim]" if r["status"] == "stopped"
            else r["status"]
        )
        t.add_row(
            str(idx),
            r["hostname"],
            r["kind"].upper(),
            r["vmid"],
            r["node"],
            status_fmt,
            r["ip"] or "[dim]unknown/DHCP[/dim]",
        )

    console.print(t)


# ─────────────────────────────────────────────
# Decommission helpers
# ─────────────────────────────────────────────

def stop_and_destroy(proxmox: ProxmoxAPI, resource: dict) -> None:
    node     = resource["node"]
    vmid     = resource["vmid"]
    hostname = resource["hostname"]
    kind     = resource["kind"]

    api = proxmox.nodes(node).lxc(vmid) if kind == "lxc" else proxmox.nodes(node).qemu(vmid)
    kind_label = "Container" if kind == "lxc" else "VM"

    # Check existence
    try:
        status = api.status.current.get()
    except Exception:
        console.print(f"  [yellow]{kind_label} {vmid} not found on {node} — may already be deleted.[/yellow]")
        return

    # Stop if running
    if status.get("status") == "running":
        console.print(f"  [dim]Stopping {kind_label.lower()} {vmid} ({hostname})...[/dim]")
        try:
            task = api.status.stop.post()
            wait_for_task(proxmox, node, task, timeout=60)
            console.print(f"  [green]✓ {kind_label} stopped[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: could not stop cleanly: {e}[/yellow]")

    # Destroy
    console.print(f"  [dim]Destroying {kind_label.lower()} {vmid}...[/dim]")
    try:
        task = api.delete(**{"purge": 1, "destroy-unreferenced-disks": 1})
        wait_for_task(proxmox, node, task, timeout=120)
        console.print(f"  [green]✓ {kind_label} {vmid} destroyed[/green]")
    except Exception as e:
        console.print(f"  [red]✗ Failed to destroy {kind_label.lower()}: {e}[/red]")
        raise


def promote_resource(proxmox: ProxmoxAPI, resource: dict) -> None:
    """Remove the matched tag from a resource, promoting it to a regular managed host."""
    node     = resource["node"]
    vmid     = resource["vmid"]
    kind     = resource["kind"]
    tag      = resource["matched_tag"]
    tags_raw = resource.get("tags", "") or ""

    # Rebuild tag string without the matched tag
    existing = [t.strip() for t in tags_raw.replace(";", ",").split(",") if t.strip()]
    updated  = [t for t in existing if t != tag]
    new_tags = ";".join(updated)

    api = proxmox.nodes(node).lxc(vmid) if kind == "lxc" else proxmox.nodes(node).qemu(vmid)
    api.config.put(tags=new_tags)
    console.print(f"  [green]✓ Tag '{TAG}' removed — {resource['hostname']} promoted to production[/green]")


def decomm_resource(proxmox: ProxmoxAPI, cfg: dict, resource: dict, idx: int, total: int) -> None:
    hostname = resource["hostname"]
    console.print()
    console.print(f"[bold red]── Decommissioning {idx}/{total}: {hostname} ──[/bold red]")

    # Build a minimal deploy dict compatible with remove_dns / remove_from_inventory
    deploy = {
        "hostname":    hostname,
        "node":        resource["node"],
        "vmid":        resource["vmid"],
        "assigned_ip": resource["ip"],
        "ip_address":  resource["ip"],
    }

    # Step 1: destroy
    console.print("[bold red]─── Step 1/3: Destroying Proxmox resource ───[/bold red]")
    try:
        stop_and_destroy(proxmox, resource)
    except Exception as e:
        console.print(f"[red]✗ Destruction failed: {e}[/red]")
        console.print("[yellow]Continuing with DNS and inventory cleanup anyway...[/yellow]")

    # Step 2: DNS
    console.print("[bold red]─── Step 2/3: Removing DNS records ───[/bold red]")
    remove_dns(cfg, deploy)

    # Step 3: inventory
    console.print("[bold red]─── Step 3/3: Removing from Ansible inventory ───[/bold red]")
    remove_from_inventory(cfg, deploy)

    console.print(f"  [green]✓ {hostname} decommissioned[/green]")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cleanup_tagged.py",
        description=f"Find and decommission all Proxmox resources matching a tag (default: '{DEFAULT_TAG}')",
        epilog=(
            "Examples:\n"
            "  python3 cleanup_tagged.py\n"
            "  python3 cleanup_tagged.py --dry-run\n"
            f"  python3 cleanup_tagged.py --tag {DEFAULT_TAG}\n"
            "  python3 cleanup_tagged.py --tag my-custom-tag --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List all matching resources and exit — no changes made",
    )
    parser.add_argument(
        "--tag", metavar="TAG", default=DEFAULT_TAG, type=_validate_tag,
        help=(
            f"Proxmox tag to search for (default: {DEFAULT_TAG}). "
            "Alphanumeric, hyphens, underscores, and dots only, max 64 chars."
        ),
    )
    args = parser.parse_args()
    tag = args.tag

    console.print()
    console.print(Panel.fit(
        Text(
            f"Proxmox Tagged Resource Cleanup\n"
            f"Finds and decommissions resources tagged '{tag}'",
            style="bold red",
            justify="center",
        ),
        border_style="red",
    ))
    console.print()

    cfg = load_config()

    with console.status("[bold]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    with console.status(f"[bold]Scanning cluster for '{tag}' tagged resources..."):
        resources = scan_tagged_resources(proxmox, cfg, tag)

    if not resources:
        console.print(f"[green]No resources tagged '{tag}' found. Nothing to do.[/green]")
        sys.exit(0)

    print_resource_table(resources, title=f"Resources tagged '{tag}'")
    console.print()

    if args.dry_run:
        console.print(f"[yellow]Dry run — {len(resources)} resource(s) found. No changes made.[/yellow]")
        sys.exit(0)

    # Per-resource action selection
    decommissioned = []
    promoted = []
    kept = []
    aborted = []

    decomm_queue = []  # collect decomm targets, then run in one pass

    for resource in resources:
        hostname = resource["hostname"]
        console.print(
            f"[bold]{hostname}[/bold]  "
            f"[cyan]{resource['kind'].upper()}[/cyan]  "
            f"vmid=[yellow]{resource['vmid']}[/yellow]  "
            f"node=[magenta]{resource['node']}[/magenta]  "
            f"ip=[blue]{resource['ip'] or 'DHCP/unknown'}[/blue]"
        )
        flush_stdin()
        action = questionary.select(
            f"What do you want to do with {hostname}?",
            choices=[
                questionary.Choice("Keep    — leave it alone, come back later", value="keep"),
                questionary.Choice("Promote — remove the auto-deploy tag (it's prod now)", value="promote"),
                questionary.Choice("Decomm  — permanently destroy it", value="decomm"),
            ],
        ).ask()

        if action is None or action == "keep":
            kept.append(hostname)
        elif action == "promote":
            promoted.append(resource)
        else:
            decomm_queue.append(resource)
        console.print()

    # Promote
    if promoted:
        console.print("[bold]── Promoting resources ──[/bold]")
        for resource in promoted:
            try:
                promote_resource(proxmox, resource)
            except Exception as e:
                console.print(f"  [red]✗ Failed to promote {resource['hostname']}: {e}[/red]")
                kept.append(resource["hostname"])
                continue
        console.print()

    # Decomm
    if decomm_queue:
        console.print(f"[bold red]{len(decomm_queue)} resource(s) queued for decommission.[/bold red]")
        console.print()

    decomm_count = 0
    for resource in decomm_queue:
        flush_stdin()
        if not confirm_destruction(resource, kind=resource["kind"]):
            aborted.append(resource["hostname"])
            continue
        try:
            decomm_count += 1
            decomm_resource(proxmox, cfg, resource, decomm_count, len(decomm_queue))
            decommissioned.append(resource["hostname"])
        except Exception as e:
            console.print(f"[red]Unexpected error decommissioning {resource['hostname']}: {e}[/red]")
            aborted.append(resource["hostname"])

    # Summary
    console.print()
    lines = [f"[bold red]Cleanup Complete[/bold red]\n"]
    if decommissioned:
        lines.append("[green]Decommissioned:[/green]")
        for h in decommissioned:
            lines.append(f"  [green]✓ {h}[/green]")
    if promoted:
        lines.append("\n[cyan]Promoted to production:[/cyan]")
        for r in promoted:
            if r["hostname"] not in kept:
                lines.append(f"  [cyan]✓ {r['hostname']}[/cyan]")
    if kept:
        lines.append("\n[yellow]Kept (no changes):[/yellow]")
        for h in kept:
            lines.append(f"  [yellow]- {h}[/yellow]")
    if aborted:
        lines.append("\n[red]Aborted (confirmation failed or error):[/red]")
        for h in aborted:
            lines.append(f"  [red]✗ {h}[/red]")
    console.print(Panel(
        "\n".join(lines),
        border_style="red",
        title=f"[bold red]{SKULL}  Done[/bold red]",
    ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
