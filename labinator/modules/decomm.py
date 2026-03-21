"""
labinator.decomm — Decommission pipeline shared with cleanup_tagged and expire.
"""

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

console = Console()

_ROOT = Path(__file__).parent.parent


def decomm_resource(proxmox, cfg: dict, resource: dict,
                    idx: int = 1, total: int = 1) -> str:
    """Full decommission: stop+destroy in Proxmox, remove DNS, remove from inventory.
    Returns 'decommissioned' if destroyed, 'already_gone' if not found in Proxmox."""
    from modules.proxmox import stop_and_destroy
    from modules.bind import remove_dns
    from modules.ansible import remove_from_inventory
    from modules.io import write_history

    hostname = resource["hostname"]
    _decomm_start = time.time()
    console.print()
    console.print(f"[bold red]── Decommissioning {idx}/{total}: {hostname} ──[/bold red]")

    deploy = {
        "hostname":    hostname,
        "node":        resource["node"],
        "vmid":        resource["vmid"],
        "assigned_ip": resource.get("ip", ""),
        "ip_address":  resource.get("ip", ""),
    }

    console.print("[bold red]─── Step 1/3: Destroying Proxmox resource ───[/bold red]")
    already_gone = False
    try:
        destroyed = stop_and_destroy(proxmox, resource)
        if not destroyed:
            already_gone = True
    except Exception as e:
        console.print(f"[red]✗ Destruction failed: {e}[/red]")
        console.print("[yellow]Continuing with DNS and inventory cleanup anyway...[/yellow]")

    console.print("[bold red]─── Step 2/3: Removing DNS records ───[/bold red]")
    remove_dns(cfg, deploy)

    console.print("[bold red]─── Step 3/3: Removing from Ansible inventory ───[/bold red]")
    remove_from_inventory(cfg, deploy)

    result = "already_gone" if already_gone else "decommissioned"
    write_history({
        "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "user":             os.getenv("USER") or os.getenv("LOGNAME") or "unknown",
        "action":           "decomm",
        "type":             resource.get("kind", "unknown"),
        "hostname":         hostname,
        "node":             resource.get("node", "?"),
        "vmid":             resource.get("vmid", "?"),
        "ip":               resource.get("ip", ""),
        "result":           result,
        "duration_seconds": round(time.time() - _decomm_start),
    })
    if already_gone:
        console.print(f"  [yellow]⚠ {hostname} was already gone — DNS and inventory cleaned up[/yellow]")
        return "already_gone"
    console.print(f"  [green]✓ {hostname} decommissioned[/green]")
    return "decommissioned"


def process_action_list(resources: list, proxmox, cfg: dict,
                        skip_confirmation: bool = False) -> dict:
    """Execute keep/promote/decomm actions on a list of resource dicts.

    Each resource must have an 'action' key: 'keep' | 'promote' | 'decomm'.
    Returns a summary dict with keys: decommissioned, already_gone, promoted, kept, aborted.
    skip_confirmation=True skips the scary challenge (for --silent / automated modes).
    """
    from modules.proxmox import promote_resource, retag_resource
    from modules.ui import flush_stdin, confirm_destruction

    decommissioned: list = []
    already_gone:   list = []
    promoted:       list = []
    retagged:       list = []
    kept:           list = []
    aborted:        list = []

    # Promote first (non-destructive)
    promote_list = [r for r in resources if r.get("action") == "promote"]
    if promote_list:
        console.print("[bold]── Promoting resources ──[/bold]")
        for resource in promote_list:
            try:
                promote_resource(proxmox, resource)
                promoted.append(resource["hostname"])
            except Exception as e:
                console.print(f"  [red]✗ Failed to promote {resource['hostname']}: {e}[/red]")
                aborted.append(resource["hostname"])
        console.print()

    # Retag (non-destructive)
    retag_list = [r for r in resources if r.get("action") == "retag"]
    if retag_list:
        console.print("[bold]── Retagging resources ──[/bold]")
        for resource in retag_list:
            try:
                retag_resource(proxmox, resource)
                retagged.append(resource["hostname"])
            except Exception as e:
                console.print(f"  [red]✗ Failed to retag {resource['hostname']}: {e}[/red]")
                aborted.append(resource["hostname"])
        console.print()

    # Kept
    for r in resources:
        if r.get("action") == "keep":
            kept.append(r["hostname"])

    # Decomm
    decomm_queue = [r for r in resources if r.get("action") == "decomm"]
    if decomm_queue:
        console.print(f"[bold red]{len(decomm_queue)} resource(s) queued for decommission.[/bold red]")
        console.print()

    decomm_count = 0
    for resource in decomm_queue:
        if not skip_confirmation:
            flush_stdin()
            if not confirm_destruction(resource, kind=resource["kind"]):
                aborted.append(resource["hostname"])
                continue
        try:
            decomm_count += 1
            status = decomm_resource(proxmox, cfg, resource, decomm_count, len(decomm_queue))
            if status == "already_gone":
                already_gone.append(resource["hostname"])
            else:
                decommissioned.append(resource["hostname"])
        except Exception as e:
            console.print(f"[red]Unexpected error decommissioning {resource['hostname']}: {e}[/red]")
            aborted.append(resource["hostname"])

    return {
        "decommissioned": decommissioned,
        "already_gone":   already_gone,
        "promoted":       promoted,
        "retagged":       retagged,
        "kept":           kept,
        "aborted":        aborted,
    }
