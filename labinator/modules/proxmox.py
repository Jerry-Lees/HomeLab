"""
labinator.proxmox — Proxmox API helpers.
"""

import sys
import time
from pathlib import Path

from rich.console import Console

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

_ROOT = Path(__file__).parent.parent


def wait_for_task(proxmox: ProxmoxAPI, node: str, taskid: str, timeout: int = 180) -> None:
    """Poll until a Proxmox task completes. Raises on failure or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = proxmox.nodes(node).tasks(taskid).status.get()
            if status["status"] == "stopped":
                exit_status = status.get("exitstatus", "")
                if exit_status != "OK" and not exit_status.startswith("WARNINGS"):
                    raise RuntimeError(f"Proxmox task failed: {exit_status}")
                return
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Proxmox task {taskid} did not complete within {timeout}s")


def get_nodes_with_load(proxmox: ProxmoxAPI, storage_content: str = "images") -> list[dict]:
    """Return online nodes sorted by free RAM (descending).

    storage_content: Proxmox content type to sum for free disk display.
      'images'  — VM disk storage (deploy_vm.py)
      'rootdir' — LXC root disk storage (deploy_lxc.py)
    """
    nodes = []
    for node in proxmox.nodes.get():
        if node.get("status") == "online":
            maxmem = node.get("maxmem", 0)
            mem = node.get("mem", 0)
            name = node["node"]
            local_disk = 0
            shared_disk = 0
            try:
                for s in proxmox.nodes(name).storage.get(enabled=1):
                    if storage_content in s.get("content", ""):
                        avail = s.get("avail", 0)
                        if s.get("shared", 0):
                            shared_disk += avail
                        else:
                            local_disk += avail
            except Exception:
                pass
            nodes.append({
                "name": name,
                "free_mem": maxmem - mem,
                "maxmem": maxmem,
                "mem": mem,
                "cpu": node.get("cpu", 0),
                "maxcpu": node.get("maxcpu", 1),
                "local_disk": local_disk,
                "shared_disk": shared_disk,
            })
    return sorted(nodes, key=lambda x: -x["free_mem"])


def get_next_vmid(proxmox: ProxmoxAPI) -> int:
    return int(proxmox.cluster.nextid.get())


def node_ssh_host(cfg: dict, node_name: str) -> str:
    """Construct the SSH hostname for a Proxmox node."""
    domain = cfg["proxmox"].get("node_domain", "")
    return f"{node_name}.{domain}" if domain else node_name


def stop_and_destroy(proxmox: ProxmoxAPI, resource: dict) -> bool:
    """Stop (if running) and permanently destroy a VM or LXC container.
    Returns True if destroyed, False if already gone."""
    node       = resource["node"]
    vmid       = resource["vmid"]
    hostname   = resource["hostname"]
    kind       = resource["kind"]
    api        = proxmox.nodes(node).lxc(vmid) if kind == "lxc" else proxmox.nodes(node).qemu(vmid)
    kind_label = "Container" if kind == "lxc" else "VM"

    try:
        status = api.status.current.get()
    except Exception:
        console.print(f"  [yellow]{kind_label} {vmid} not found on {node} — may already be deleted.[/yellow]")
        return False

    if status.get("status") == "running":
        console.print(f"  [dim]Stopping {kind_label.lower()} {vmid} ({hostname})...[/dim]")
        try:
            task = api.status.stop.post()
            wait_for_task(proxmox, node, task, timeout=60)
            console.print(f"  [green]✓ {kind_label} stopped[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: could not stop cleanly: {e}[/yellow]")

    console.print(f"  [dim]Destroying {kind_label.lower()} {vmid}...[/dim]")
    try:
        task = api.delete(**{"purge": 1, "destroy-unreferenced-disks": 1})
        wait_for_task(proxmox, node, task, timeout=120)
        console.print(f"  [green]✓ {kind_label} {vmid} destroyed[/green]")
    except Exception as e:
        console.print(f"  [red]✗ Failed to destroy {kind_label.lower()}: {e}[/red]")
        raise
    return True


def promote_resource(proxmox: ProxmoxAPI, resource: dict) -> None:
    """Remove the matched tag from a resource in Proxmox (promotes it to production)."""
    node     = resource["node"]
    vmid     = resource["vmid"]
    kind     = resource["kind"]
    tag      = resource.get("matched_tag", "auto-deploy")
    tags_raw = resource.get("tags", "") or ""

    existing = [t.strip() for t in tags_raw.replace(";", ",").split(",") if t.strip()]
    updated  = [t for t in existing if t != tag]
    new_tags = ";".join(updated)

    api = proxmox.nodes(node).lxc(vmid) if kind == "lxc" else proxmox.nodes(node).qemu(vmid)
    api.config.put(tags=new_tags)
    console.print(f"  [green]✓ Tag '{tag}' removed — {resource['hostname']} promoted to production[/green]")


def retag_resource(proxmox: ProxmoxAPI, resource: dict) -> None:
    """Replace matched_tag with retag_tag on a resource in Proxmox."""
    node     = resource["node"]
    vmid     = resource["vmid"]
    kind     = resource["kind"]
    old_tag  = resource.get("matched_tag", "auto-deploy")
    new_tag  = resource.get("retag_tag", "")
    tags_raw = resource.get("tags", "") or ""

    existing = [t.strip() for t in tags_raw.replace(";", ",").split(",") if t.strip()]
    updated  = [t for t in existing if t != old_tag]
    if new_tag and new_tag not in updated:
        updated.append(new_tag)
    new_tags = ";".join(updated)

    api = proxmox.nodes(node).lxc(vmid) if kind == "lxc" else proxmox.nodes(node).qemu(vmid)
    api.config.put(tags=new_tags)
    console.print(f"  [green]✓ Tag '{old_tag}' → '{new_tag}' — {resource['hostname']} retagged[/green]")


def apply_tag_colors(proxmox, tag_colors: dict) -> None:
    """Apply tag color-map entries to the Proxmox cluster tag-style setting.

    Merges new colors into the existing color-map; does not overwrite colors
    for tags that are not in tag_colors. Falls back gracefully on API failure.
    """
    if not tag_colors:
        return
    try:
        opts = proxmox.cluster.options.get()
        raw = opts.get("tag-style", "")
        # Parse existing tag-style into key=value parts
        parts = {}
        for part in raw.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                parts[k.strip()] = v.strip()
        # Parse existing color-map
        existing_colors = {}
        if "color-map" in parts:
            for entry in parts["color-map"].split(";"):
                if ":" in entry:
                    t, c = entry.split(":", 1)
                    if t.strip():
                        existing_colors[t.strip()] = c.strip()
        # Merge in new colors (only add; don't overwrite existing manual colors)
        for tag, color in tag_colors.items():
            if tag not in existing_colors:
                existing_colors[tag] = color
        # Rebuild color-map and tag-style
        color_map_str = ";".join(f"{t}:{c}" for t, c in sorted(existing_colors.items()))
        parts["color-map"] = color_map_str
        new_raw = ",".join(f"{k}={v}" for k, v in parts.items())
        proxmox.cluster.options.put(tag_style=new_raw)
    except Exception as e:
        console.print(f"  [yellow]⚠ Tag color update skipped: {e}[/yellow]")


def smart_size(b: int) -> str:
    """Convert bytes to a human-readable size string with auto-scaling units."""
    if b <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    val = float(b)
    for unit in units[:-1]:
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    # val is now in PB
    if val > 10:
        return f"{val:.1f} PB!!! 😱"
    return f"{val:.1f} PB"


def bytes_to_gb(b: int) -> str:
    return f"{b / (1024 ** 3):.1f}"


def preflight_checks(cfg: dict, kind: str) -> list:
    """Return Proxmox-specific preflight check results."""
    from modules.preflight import _pf_proxmox_reachable, _pf_proxmox_auth, _pf_proxmox_ssh
    return [_pf_proxmox_reachable(cfg), _pf_proxmox_auth(cfg), _pf_proxmox_ssh(cfg)]
