#!/usr/bin/env python3
"""
Deployment Expiry Manager
=========================
Scans all deployment JSON files for an 'expires_at' field and reports on or
acts on expired / expiring-soon deployments.

  --check               Print a table of expired and expiring-soon hosts (default)
  --reap                Decommission all expired hosts (stop/destroy, DNS, inventory)
  --renew HOSTNAME      Extend the TTL of an existing deployment
  --ttl TTL             New TTL for --renew (e.g. 7d, 24h, 2w, 30m)
  --warning-hours N     Hours ahead to flag as expiring-soon (default: 48)
  --silent              Skip interactive prompts and confirmation challenge
  --yolo                Continue through warnings; blocked by failures (like deploy scripts)

Deployments without an 'expires_at' field are ignored.
"""

# Auto-activate virtualenv so `python3 expire.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from modules.lib import (
    load_config,
    connect_proxmox,
    parse_ttl,
    expires_at_from_ttl,
    decomm_resource,
    process_action_list,
    confirm_destruction,
    flush_stdin,
    SKULL,
)

console = Console()
_ROOT = Path(__file__).parent


# ─────────────────────────────────────────────
# Scanning deployment JSONs
# ─────────────────────────────────────────────

def scan_expiring(warning: timedelta = timedelta(hours=48)) -> tuple[list[dict], list[dict]]:
    """Scan all deployment JSONs and return (expired, expiring_soon) lists."""
    now               = datetime.now(timezone.utc)
    warning_threshold = now + warning

    expired      = []
    expiring_soon = []

    for kind, folder in (("lxc", "lxc"), ("vm", "vms")):
        deploy_dir = _ROOT / "deployments" / folder
        if not deploy_dir.exists():
            continue
        for path in sorted(deploy_dir.glob("*.json")):
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception:
                continue

            expires_str = data.get("expires_at")
            if not expires_str:
                continue

            try:
                expires_at = datetime.fromisoformat(expires_str)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except ValueError:
                console.print(f"  [yellow]Warning: invalid expires_at in {path.name} — skipped.[/yellow]")
                continue

            entry = {
                "hostname":    data.get("hostname", path.stem),
                "kind":        kind,
                "vmid":        str(data.get("vmid", "?")),
                "node":        data.get("node", "?"),
                "ip":          data.get("assigned_ip") or data.get("ip_address", ""),
                "expires_at":  expires_at,
                "ttl":         data.get("ttl", "?"),
                "deploy_file": path,
                "tags":        "",
                "matched_tag": "auto-deploy",
                "status":      "unknown",
                "action":      "decomm",
            }

            if expires_at <= now:
                expired.append(entry)
            elif expires_at <= warning_threshold:
                expiring_soon.append(entry)

    return expired, expiring_soon


# ─────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────

def _fmt_expires(entry: dict) -> str:
    now      = datetime.now(timezone.utc)
    expires  = entry["expires_at"]
    delta    = expires - now
    if delta.total_seconds() < 0:
        hours_ago = abs(delta.total_seconds()) / 3600
        if hours_ago < 48:
            return f"[red]{expires.strftime('%Y-%m-%d %H:%M')} UTC  ({hours_ago:.0f}h ago)[/red]"
        return f"[red]{expires.strftime('%Y-%m-%d %H:%M')} UTC  ({abs(delta.days)}d ago)[/red]"
    hours_left = delta.total_seconds() / 3600
    if hours_left < 48:
        return f"[yellow]{expires.strftime('%Y-%m-%d %H:%M')} UTC  ({hours_left:.0f}h left)[/yellow]"
    return f"{expires.strftime('%Y-%m-%d %H:%M')} UTC  ({delta.days}d left)"


def print_expiry_table(expired: list[dict], expiring_soon: list[dict]) -> None:
    if not expired and not expiring_soon:
        console.print("[green]No expiring or expired deployments found.[/green]")
        return

    t = Table(border_style="red")
    t.add_column("Hostname",  style="bold")
    t.add_column("Type",      style="cyan",    width=6)
    t.add_column("VMID",      style="yellow",  width=7)
    t.add_column("Node",      style="magenta")
    t.add_column("TTL",       style="dim",     width=5)
    t.add_column("Expires / Expired",          width=36)
    t.add_column("Status",    style="dim")

    for entry in expired:
        t.add_row(
            entry["hostname"],
            entry["kind"].upper(),
            entry["vmid"],
            entry["node"],
            entry["ttl"],
            _fmt_expires(entry),
            "[red]EXPIRED[/red]",
        )
    for entry in expiring_soon:
        t.add_row(
            entry["hostname"],
            entry["kind"].upper(),
            entry["vmid"],
            entry["node"],
            entry["ttl"],
            _fmt_expires(entry),
            "[yellow]expiring soon[/yellow]",
        )

    console.print(t)


# ─────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────

def do_renew(hostname: str, ttl: str, kind: str = "") -> None:
    """Update expires_at in the deployment JSON for the given hostname."""
    folders = {"lxc": "lxc", "vm": "vms"}
    search = [folders[kind]] if kind else ["lxc", "vms"]

    for folder in search:
        path = _ROOT / "deployments" / folder / f"{hostname}.json"
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                old = data.get("expires_at", "(none)")
                data["ttl"] = ttl
                data["expires_at"] = expires_at_from_ttl(ttl)
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                console.print(
                    f"  [green]✓ {hostname}[/green] renewed: "
                    f"[dim]{old}[/dim] → [green]{data['expires_at'][:19]} UTC[/green]"
                )
                return
            except Exception as e:
                console.print(f"[red]ERROR: Could not update {path}: {e}[/red]")
                sys.exit(1)

    location = f"deployments/{search[0]}/" if kind else "deployments/lxc/ or deployments/vms/"
    console.print(f"[red]ERROR: No deployment file found for '{hostname}' in {location}[/red]")
    sys.exit(1)


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="expire.py",
        description="Manage deployment TTLs — check, reap, or renew expiring deployments",
        epilog=(
            "Examples:\n"
            "  python3 expire.py --check\n"
            "  python3 expire.py --reap\n"
            "  python3 expire.py --reap --silent\n"
            "  python3 expire.py --renew test-lxc --ttl 7d\n"
            "  python3 expire.py --check --warning 3d"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check",  action="store_true",
                      help="Print expired and expiring-soon deployments (default if no mode given)")
    mode.add_argument("--reap",   action="store_true",
                      help="Decommission all expired deployments")
    mode.add_argument("--renew",  metavar="HOSTNAME",
                      help="Extend the TTL of a deployment (requires --ttl)")

    parser.add_argument("--ttl", metavar="TTL",
                        help="TTL for --renew or display (e.g. 7d, 24h, 2w, 30m)")
    parser.add_argument("--kind", choices=["lxc", "vm"],
                        help="Disambiguate --renew when both lxc/ and vms/ have the same hostname")
    parser.add_argument("--warning", metavar="TTL", default="48h",
                        help="How far ahead to flag as expiring-soon (default: 48h, e.g. 2d, 6h, 30m)")
    parser.add_argument("--silent", action="store_true",
                        help="Skip confirmation challenge when reaping")
    parser.add_argument("--yolo",   action="store_true",
                        help="Continue through warnings; blocked by failures")

    args = parser.parse_args()

    # Parse --warning TTL
    try:
        warning_delta = parse_ttl(args.warning)
    except ValueError as e:
        console.print(f"[red]ERROR: --warning: {e}[/red]")
        sys.exit(1)

    # Default mode: --check
    if not args.reap and not args.renew:
        args.check = True

    console.print()
    console.print(Panel.fit(
        Text("Deployment Expiry Manager", style="bold red", justify="center"),
        border_style="red",
    ))
    console.print()

    # ── Renew ──
    if args.renew:
        if not args.ttl:
            console.print("[red]ERROR: --renew requires --ttl (e.g. --ttl 7d)[/red]")
            sys.exit(1)
        try:
            parse_ttl(args.ttl)
        except ValueError as e:
            console.print(f"[red]ERROR: {e}[/red]")
            sys.exit(1)
        do_renew(args.renew, args.ttl, kind=args.kind or "")
        sys.exit(0)

    # ── Check / Reap: scan files ──
    with console.status("[bold]Scanning deployment files for expiry data..."):
        expired, expiring_soon = scan_expiring(warning=warning_delta)

    print_expiry_table(expired, expiring_soon)
    console.print()

    if args.check:
        if expired:
            console.print(
                f"[red]{len(expired)} expired deployment(s).[/red]  "
                f"Run [bold]./expire.py --reap[/bold] to decommission them."
            )
        if expiring_soon:
            console.print(
                f"[yellow]{len(expiring_soon)} deployment(s) expiring within "
                f"{args.warning}.[/yellow]  "
                f"Run [bold]./expire.py --renew HOSTNAME --ttl Xd[/bold] to extend."
            )
        sys.exit(0)

    # ── Reap ──
    if not expired:
        console.print("[green]No expired deployments to reap.[/green]")
        sys.exit(0)

    console.print(f"[bold red]{len(expired)} expired deployment(s) to decommission.[/bold red]")
    console.print()

    cfg = load_config()

    with console.status("[bold red]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    result = process_action_list(
        expired, proxmox, cfg,
        skip_confirmation=args.silent,
    )

    # Summary
    console.print()
    lines = ["[bold red]Reap Complete[/bold red]\n"]
    if result["decommissioned"]:
        lines.append("[green]Decommissioned:[/green]")
        for h in result["decommissioned"]:
            lines.append(f"  [green]✓ {h}[/green]")
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
