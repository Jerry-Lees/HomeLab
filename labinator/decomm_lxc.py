#!/usr/bin/env python3
"""
Proxmox LXC Decommission Script
================================
PERMANENTLY destroys an LXC container and removes all traces:
  - Stops and deletes the container via Proxmox API
  - Removes DNS A + PTR records from BIND
  - Removes host from Ansible inventory
  - Deletes the local deployment file

THIS IS IRREVERSIBLE. Use with extreme caution.
"""

# Auto-activate virtualenv so `python3 decomm_lxc.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import json
import questionary
from pathlib import Path
from proxmoxer import ProxmoxAPI
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from modules.lib import (
    load_config,
    connect_proxmox,
    wait_for_task,
    remove_dns,
    remove_from_inventory,
    random_caps,
    flush_stdin,
    confirm_destruction,
    SKULL,
)

console = Console()


# ─────────────────────────────────────────────
# Deployment file helpers
# ─────────────────────────────────────────────

def list_deployment_files() -> list[Path]:
    deployments_dir = Path(__file__).parent / "deployments" / "lxc"
    if not deployments_dir.exists():
        return []
    return sorted(deployments_dir.glob("*.json"))


def load_deployment_file(path: Path) -> dict:
    with open(path) as f:
        return json.load(f) or {}


# ─────────────────────────────────────────────
# Decommission steps
# ─────────────────────────────────────────────

def stop_and_destroy_container(proxmox: ProxmoxAPI, deploy: dict) -> None:
    node = deploy["node"]
    vmid = deploy["vmid"]
    hostname = deploy["hostname"]

    # Check if container exists
    try:
        status = proxmox.nodes(node).lxc(vmid).status.current.get()
    except Exception:
        console.print(f"  [yellow]Container {vmid} not found on {node} — may already be deleted.[/yellow]")
        return

    # Stop if running
    if status.get("status") == "running":
        console.print(f"  [dim]Stopping container {vmid} ({hostname})...[/dim]")
        try:
            task = proxmox.nodes(node).lxc(vmid).status.stop.post()
            wait_for_task(proxmox, node, task, timeout=60)
            console.print(f"  [green]✓ Container stopped[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not stop container cleanly: {e}[/yellow]")

    # Destroy
    console.print(f"  [dim]Destroying container {vmid}...[/dim]")
    try:
        task = proxmox.nodes(node).lxc(vmid).delete(**{"purge": 1, "destroy-unreferenced-disks": 1})
        wait_for_task(proxmox, node, task, timeout=120)
        console.print(f"  [green]✓ Container {vmid} destroyed[/green]")
    except Exception as e:
        console.print(f"  [red]✗ Failed to destroy container: {e}[/red]")
        raise


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="decomm_lxc.py",
        description="Proxmox LXC Decommission Wizard — permanently destroys a container",
        epilog="Examples:\n  python3 decomm_lxc.py\n  python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--deploy-file", metavar="FILE",
        help="Path to deployment JSON file — skips interactive selection",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="Also delete the local deployment file after decommission",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Non-interactive mode: skip confirmation challenge (requires --deploy-file)",
    )
    args = parser.parse_args()

    if args.silent and not args.deploy_file:
        console.print("[red]ERROR: --silent requires --deploy-file (no interactive selection in silent mode)[/red]")
        sys.exit(1)

    console.print()
    console.print(Panel.fit(
        Text(
            "Proxmox LXC Decommission Wizard\n"
            "Permanently destroys a container and removes all traces",
            style="bold red",
            justify="center",
        ),
        border_style="red",
    ))
    console.print()

    if args.deploy_file:
        # Load directly from specified file — skip interactive list
        deploy_path = Path(args.deploy_file)
        if not deploy_path.exists():
            console.print(f"[red]ERROR: Deployment file not found: {deploy_path}[/red]")
            sys.exit(1)
        deploy = load_deployment_file(deploy_path)
    else:
        # List available deployment files and let user pick
        deploy_files = list_deployment_files()
        if not deploy_files:
            console.print("[red]No deployment files found in deployments/lxc/ folder.[/red]")
            console.print("Only containers deployed via deploy_lxc.py can be decommissioned this way.")
            sys.exit(1)

        choices = []
        for f in deploy_files:
            d = load_deployment_file(f)
            ip = d.get("assigned_ip") or d.get("ip_address", "?")
            label = (
                f"{d.get('hostname', f.stem):<20}  "
                f"node={d.get('node', '?'):<12}  "
                f"ip={ip:<16}  "
                f"deployed={d.get('deployed_at', '?')}"
            )
            choices.append(questionary.Choice(title=label, value=f))

        deploy_path = questionary.select(
            "Select container to decommission:",
            choices=choices,
        ).ask()

        if deploy_path is None:
            console.print("\n[yellow]Aborted.[/yellow]")
            sys.exit(0)

        deploy = load_deployment_file(deploy_path)

    # Scary confirmation (skipped in silent mode)
    if args.silent:
        hostname = deploy.get("hostname", "unknown")
        console.print(f"[yellow]Silent mode: skipping confirmation challenge for {hostname}.[/yellow]")
    elif not confirm_destruction(deploy, kind="container"):
        sys.exit(0)

    # Load config and connect to Proxmox
    cfg = load_config()

    with console.status("[bold red]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    # ── Step 1: Stop and destroy container ──
    console.print("[bold red]─── Step 1/4: Destroying Proxmox container ───[/bold red]")
    try:
        stop_and_destroy_container(proxmox, deploy)
    except Exception as e:
        console.print(f"[red]✗ Container destruction failed: {e}[/red]")
        console.print("[yellow]Continuing with DNS and inventory cleanup anyway...[/yellow]")

    # ── Step 2: Remove DNS records ──
    console.print("[bold red]─── Step 2/4: Removing DNS records ───[/bold red]")
    remove_dns(cfg, deploy)

    # ── Step 3: Remove from Ansible inventory ──
    console.print("[bold red]─── Step 3/4: Removing from Ansible inventory ───[/bold red]")
    remove_from_inventory(cfg, deploy)

    # ── Step 4: Deployment file ──
    # SAFETY: NEVER delete the deployment file unless --purge was explicitly passed by
    # the user on the command line. Do NOT add automatic deletion logic here.
    # The file is the permanent record of the deployment and may be needed for recovery.
    console.print("[bold red]─── Step 4/4: Deployment file ───[/bold red]")
    if args.purge:
        try:
            deploy_path.unlink()
            console.print(f"  [green]✓ Deleted {deploy_path}[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not delete {deploy_path}: {e}[/yellow]")
    else:
        console.print(f"  [yellow]Deployment file NOT deleted: {deploy_path}[/yellow]")
        console.print(f"  [dim]Run with --purge to delete it, or remove it manually.[/dim]")

    # Done
    hostname = deploy.get("hostname", "unknown")
    console.print()
    console.print(Panel(
        f"[bold red]Decommission Complete[/bold red]\n\n"
        f"[bold]{hostname}[/bold] has been permanently destroyed.\n"
        f"Container deleted, DNS removed, inventory updated.",
        border_style="red",
        title=f"[bold red]{SKULL}  Done[/bold red]",
    ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
