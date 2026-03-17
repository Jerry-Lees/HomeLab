#!/usr/bin/env python3
"""
Proxmox Tagged Resource Cleanup
================================
Finds all VMs and LXC containers tagged with a given Proxmox tag (default:
'auto-deploy') across the entire cluster and offers to decommission them
interactively.

  - Displays a table of all tagged resources (node, type, VMID, status, IP)
  - Per-resource action: Keep / Promote / Decomm
  - --dry-run: list matching resources and exit without touching anything
  - --list-file: read a pre-built action list from a JSON file
  - --silent: skip interactive prompts and scary confirmation challenge
  - --tag TAG: scan for a different tag (validated — alphanumeric, -, _, .)

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
    remove_dns,
    remove_from_inventory,
    confirm_destruction,
    flush_stdin,
    stop_and_destroy,
    promote_resource,
    decomm_resource,
    process_action_list,
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
# IP resolution
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
    for key in ("ipconfig0", "net0", "net1"):
        val = config.get(key, "")
        for part in val.split(","):
            if part.startswith("ip=") and part[3:] != "dhcp":
                return part[3:].split("/")[0]
    return ""


def _ip_from_deploy_json(hostname: str, kind: str) -> str:
    """Step 2: Check the local deployment JSON for a stored IP."""
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
    """Step 3: Ask Proxmox for live interface data (works for running containers/VMs)."""
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
    """Step 4: DNS lookup — try configured DNS server first, then system resolver."""
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
    return (
        _extract_ip_from_config(config)
        or _ip_from_deploy_json(hostname, kind)
        or _ip_from_proxmox_api(proxmox, node, vmid, kind)
        or _ip_from_dns(hostname, cfg)
        or ""
    )


# ─────────────────────────────────────────────
# Proxmox scanning
# ─────────────────────────────────────────────

def _has_tag(resource: dict, tag: str) -> bool:
    tags_raw = resource.get("tags", "") or ""
    return tag in [t.strip() for t in tags_raw.replace(";", ",").split(",")]


def scan_tagged_resources(proxmox: ProxmoxAPI, cfg: dict, tag: str = DEFAULT_TAG) -> list[dict]:
    """Return list of dicts describing every tagged VM/LXC in the cluster."""
    found = []

    for node_info in proxmox.nodes.get():
        node = node_info["node"]

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
                    "action":      "keep",
                })
        except Exception as e:
            console.print(f"  [yellow]Warning: could not list LXCs on {node}: {e}[/yellow]")

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
                    "action":      "keep",
                })
        except Exception as e:
            console.print(f"  [yellow]Warning: could not list VMs on {node}: {e}[/yellow]")

    return found


# ─────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────

def print_resource_table(resources: list[dict], title: str = "") -> None:
    t = Table(title=title or f"Resources tagged '{DEFAULT_TAG}'", border_style="red")
    t.add_column("#",        style="dim",    width=4)
    t.add_column("Hostname", style="bold")
    t.add_column("Type",     style="cyan",   width=6)
    t.add_column("VMID",     style="yellow", width=7)
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


def print_summary(result: dict) -> None:
    lines = ["[bold red]Cleanup Complete[/bold red]\n"]
    if result["decommissioned"]:
        lines.append("[green]Decommissioned:[/green]")
        for h in result["decommissioned"]:
            lines.append(f"  [green]✓ {h}[/green]")
    if result["promoted"]:
        lines.append("\n[cyan]Promoted to production:[/cyan]")
        for h in result["promoted"]:
            lines.append(f"  [cyan]✓ {h}[/cyan]")
    if result["kept"]:
        lines.append("\n[yellow]Kept (no changes):[/yellow]")
        for h in result["kept"]:
            lines.append(f"  [yellow]- {h}[/yellow]")
    if result.get("already_gone"):
        lines.append("\n[yellow]Already gone (DNS + inventory cleaned up):[/yellow]")
        for h in result["already_gone"]:
            lines.append(f"  [yellow]⚠ {h}[/yellow]")
    if result["aborted"]:
        lines.append("\n[red]Aborted (confirmation failed or error):[/red]")
        for h in result["aborted"]:
            lines.append(f"  [red]✗ {h}[/red]")
    console.print(Panel(
        "\n".join(lines),
        border_style="red",
        title=f"[bold red]{SKULL}  Done[/bold red]",
    ))


# ─────────────────────────────────────────────
# List-file loading
# ─────────────────────────────────────────────

def load_list_file(path: Path) -> dict:
    """Load --list-file JSON and return a dict keyed by hostname -> action.
    Each entry must have 'hostname' and 'action' (keep/promote/decomm).
    'vmid' is optional but used to disambiguate duplicate hostnames.
    """
    VALID_ACTIONS = {"keep", "promote", "decomm"}
    try:
        with open(path) as f:
            entries = json.load(f)
    except Exception as e:
        console.print(f"[red]ERROR: Could not read list file '{path}': {e}[/red]")
        sys.exit(1)

    if not isinstance(entries, list):
        console.print("[red]ERROR: List file must be a JSON array of objects.[/red]")
        sys.exit(1)

    result = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            console.print(f"[red]ERROR: List file entry {i} is not an object.[/red]")
            sys.exit(1)
        hostname = entry.get("hostname", "").strip()
        action   = entry.get("action",   "").strip().lower()
        vmid     = str(entry.get("vmid", "")).strip()
        if not hostname:
            console.print(f"[red]ERROR: List file entry {i} missing 'hostname'.[/red]")
            sys.exit(1)
        if action not in VALID_ACTIONS:
            console.print(f"[red]ERROR: List file entry {i} has invalid action '{action}'. "
                          f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}[/red]")
            sys.exit(1)
        key = f"{hostname}:{vmid}" if vmid else hostname
        result[key] = action

    return result


def apply_list_file(resources: list[dict], action_map: dict) -> None:
    """Set 'action' on each resource based on the list file. Unmatched = keep."""
    for r in resources:
        # Try hostname:vmid first, then hostname alone
        key_full = f"{r['hostname']}:{r['vmid']}"
        key_host = r["hostname"]
        if key_full in action_map:
            r["action"] = action_map[key_full]
        elif key_host in action_map:
            r["action"] = action_map[key_host]
        # else stays "keep" (default set during scan)

    # Warn about list file entries that didn't match anything
    matched_keys = {f"{r['hostname']}:{r['vmid']}" for r in resources} | {r["hostname"] for r in resources}
    for key, action in action_map.items():
        base = key.split(":")[0]
        if key not in matched_keys and base not in matched_keys:
            console.print(f"  [yellow]Warning: list file entry '{key}' not found in cluster — skipped.[/yellow]")


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
            "  python3 cleanup_tagged.py --list-file cleanup-plan.json\n"
            "  python3 cleanup_tagged.py --list-file cleanup-plan.json --silent"
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
    parser.add_argument(
        "--list-file", metavar="FILE",
        help="JSON file pre-specifying actions per resource. "
             "Format: [{\"hostname\": \"x\", \"action\": \"decomm|promote|keep\", \"vmid\": \"111\"}]",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Skip interactive prompts and scary confirmation challenge. Requires --list-file.",
    )
    parser.add_argument(
        "--config", metavar="FILE",
        help="Path to an alternate config file (default: config.yaml in project root)",
    )
    args = parser.parse_args()
    tag = args.tag

    if args.silent and not args.list_file:
        console.print("[red]ERROR: --silent requires --list-file (no interactive selection in silent mode)[/red]")
        sys.exit(1)

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

    cfg = load_config(args.config)

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

    # ── Assign actions ──
    if args.list_file:
        action_map = load_list_file(Path(args.list_file))
        apply_list_file(resources, action_map)
        console.print(f"[dim]Actions loaded from: {args.list_file}[/dim]")
        console.print()
        # Show what will happen
        for r in resources:
            action_color = {"decomm": "red", "promote": "cyan", "keep": "yellow"}.get(r["action"], "white")
            console.print(
                f"  [{action_color}]{r['action']:<8}[/{action_color}]  "
                f"[bold]{r['hostname']}[/bold]  "
                f"[cyan]{r['kind'].upper()}[/cyan]  vmid=[yellow]{r['vmid']}[/yellow]"
            )
        console.print()
    else:
        # Interactive per-resource selection
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
                    questionary.Choice("Promote — remove the tag (it's prod now)",  value="promote"),
                    questionary.Choice("Decomm  — permanently destroy it",           value="decomm"),
                ],
            ).ask()
            resource["action"] = action if action is not None else "keep"
            console.print()

    # ── Execute ──
    result = process_action_list(
        resources, proxmox, cfg,
        skip_confirmation=args.silent,
    )

    console.print()
    print_summary(result)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
