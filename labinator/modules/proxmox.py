"""
labinator.proxmox — Proxmox API helpers.
"""

import base64
import os
import sys
import time
from pathlib import Path

import paramiko
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


# ─────────────────────────────────────────────
# Storage queries
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


def get_lxc_disk_storages(proxmox: ProxmoxAPI, node: str) -> list[str]:
    """Return storage pools that can hold LXC root filesystems (content type: rootdir)."""
    pools = []
    try:
        for s in proxmox.nodes(node).storage.get(enabled=1):
            if "rootdir" in s.get("content", ""):
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


def get_lxc_templates(proxmox: ProxmoxAPI, node: str) -> list[dict]:
    """
    Query all storage pools on the node for LXC templates (vztmpl).
    Returns list sorted Ubuntu-first.
    """
    templates = []
    try:
        storages = proxmox.nodes(node).storage.get()
    except Exception as e:
        console.print(f"[yellow]Warning: Could not query storages on {node}: {e}[/yellow]")
        return templates

    for storage in storages:
        if "vztmpl" not in storage.get("content", ""):
            continue
        storage_name = storage["storage"]
        try:
            content = proxmox.nodes(node).storage(storage_name).content.get(content="vztmpl")
            for item in content:
                volid = item["volid"]
                name = volid.split("/")[-1]
                templates.append({
                    "volid": volid,
                    "name": name,
                    "storage": storage_name,
                    "size": item.get("size", 0),
                })
        except Exception as e:
            console.print(f"[yellow]  Warning: Could not list templates in {storage_name}: {e}[/yellow]")

    ubuntu = sorted([t for t in templates if "ubuntu" in t["name"].lower()], key=lambda x: x["name"], reverse=True)
    others = sorted([t for t in templates if "ubuntu" not in t["name"].lower()], key=lambda x: x["name"])
    return ubuntu + others


# ─────────────────────────────────────────────
# Resource verification
# ─────────────────────────────────────────────


def check_node_resources(proxmox: ProxmoxAPI, node_name: str,
                          memory_mb: int, disk_gb: int, storage: str,
                          cpu_threshold: float = 0.85,
                          ram_threshold: float = 0.95) -> tuple[bool, str]:
    """Re-verify a node still has sufficient resources before creating a VM or container."""
    try:
        node_data = next(
            (n for n in proxmox.nodes.get() if n["node"] == node_name), None
        )
        if node_data is None:
            return False, f"Node {node_name} not found"
        if node_data.get("cpu", 0) >= cpu_threshold:
            return False, f"CPU now at {node_data['cpu']*100:.0f}% (≥{cpu_threshold*100:.0f}%)"
        maxmem = node_data.get("maxmem", 0)
        mem = node_data.get("mem", 0)
        if maxmem > 0 and (mem + memory_mb * 1024 * 1024) / maxmem >= ram_threshold:
            free_gb = bytes_to_gb(maxmem - mem)
            return False, f"Only {free_gb} GB RAM free — insufficient for {memory_mb/1024:.1f} GB"
        for s in proxmox.nodes(node_name).storage.get(enabled=1):
            if s["storage"] == storage:
                if s.get("type") == "lvmthin":
                    break
                avail = s.get("avail", 0)
                needed = disk_gb * 1024 ** 3
                if avail < needed:
                    avail_gb = bytes_to_gb(avail)
                    return False, f"Storage '{storage}' only has {avail_gb} GB free — need {disk_gb} GB"
                break
    except Exception as e:
        return False, f"Could not verify resources: {e}"
    return True, ""


# ─────────────────────────────────────────────
# SSH helpers
# ─────────────────────────────────────────────


def run_ssh_cmd(ssh: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    """Run a command over SSH, blocking until completion. Returns (exit_code, stdout, stderr)."""
    _, stdout, stderr = ssh.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, stdout.read().decode().strip(), stderr.read().decode().strip()


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


def import_cloud_image(cfg: dict, proxmox: ProxmoxAPI, node_name: str, vmid: int,
                       disk_storage: str, image_storage_name: str,
                       image_filename: str, image_url: str | None,
                       image_refresh: bool) -> None:
    """
    Ensure the cloud image is present on image_storage_name, then import as VM disk.

    image_refresh=True  — Always re-download (user explicitly chose "Download:" in UI
                          or set image_refresh: true in deployment file).
    image_refresh=False — Use existing file; auto-download only if missing.

    image_url: fully resolved URL (caller is responsible for catalog lookup before calling).

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

            if not image_url:
                raise RuntimeError(
                    f"Cannot download '{image_filename}': no URL provided. "
                    f"Add it to cloud-images.yaml or re-run interactively to fix this."
                )

            console.print(f"  [dim](this may take 1–2 minutes for a ~600 MB image)[/dim]")
            run_ssh_cmd(ssh, f"mkdir -p {iso_path}")
            exit_code, out, err = run_ssh_cmd(ssh, f'wget -q -O {image_file} "{image_url}"')
            if exit_code != 0:
                raise RuntimeError(f"wget failed (exit {exit_code}): {err or out}")
            console.print(f"  [green]✓ Image ready at {image_storage_name}:{image_filename}[/green]")
        else:
            console.print(f"  [dim]Using existing image: {image_storage_name}:{image_filename}[/dim]")

        console.print(f"  [dim]Importing disk into VM {vmid} on storage '{disk_storage}'...[/dim]")
        exit_code, out, err = run_ssh_cmd(ssh, f"qm importdisk {vmid} {image_file} {disk_storage}")
        if exit_code != 0:
            raise RuntimeError(f"qm importdisk failed (exit {exit_code}): {err or out}")
        console.print(f"  [green]✓ Disk imported[/green]")
    finally:
        ssh.close()


def write_guest_agent_snippet(cfg: dict, node_name: str, vmid: int) -> str:
    """
    Write a minimal cloud-init vendor-data snippet to the Proxmox node's local
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
# VM creation and configuration
# ─────────────────────────────────────────────


def wait_for_guest_agent_ip(proxmox: ProxmoxAPI, node: str, vmid: int,
                             timeout: int = 300) -> str:
    """
    Poll the QEMU guest agent until it reports a non-loopback IPv4 address.
    Used for DHCP VMs where the IP isn't known at deploy time.
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


def create_vm(proxmox: ProxmoxAPI, node_name: str, create_params: dict) -> int:
    """Create a QEMU VM, retrying up to 3 times on VMID collision.
    Returns the actual vmid used. Raises RuntimeError on unrecoverable failure."""
    next_vmid = create_params["vmid"]
    for _attempt in range(3):
        create_params["vmid"] = next_vmid
        try:
            with console.status(f"[bold green]Creating VM {next_vmid} ({create_params.get('name', '')}) on {node_name}..."):
                task = proxmox.nodes(node_name).qemu.post(**create_params)
                wait_for_task(proxmox, node_name, task, timeout=60)
            console.print(f"[green]✓ VM {next_vmid} created[/green]")
            return next_vmid
        except Exception as e:
            retryable = "already exists" in str(e) or "can't lock file" in str(e)
            if retryable and _attempt < 2:
                old_vmid = next_vmid
                next_vmid = get_next_vmid(proxmox)
                console.print(
                    f"[yellow]⚠ VMID {old_vmid} already in use (race condition) — "
                    f"retrying with VMID {next_vmid}[/yellow]"
                )
            else:
                raise RuntimeError(f"VM creation failed: {e}") from e
    raise RuntimeError("VM creation failed after 3 attempts")


def configure_vm_disk_and_cloudinit(proxmox: ProxmoxAPI, node_name: str, vmid: int,
                                     storage: str, disk_gb: str, ci_params: dict) -> None:
    """
    Attach the imported disk (unused0) as scsi0, add cloud-init drive on ide2,
    enable serial console, set boot order, resize scsi0, and apply cloud-init config.

    ci_params: dict of cloud-init keys (ciuser, cipassword, ipconfig0, sshkeys, cicustom, etc.)
    """
    vm_config = proxmox.nodes(node_name).qemu(vmid).config.get()
    unused_disk = None
    for key in sorted(vm_config.keys()):
        if key.startswith("unused"):
            unused_disk = vm_config[key]
            break
    if not unused_disk:
        raise RuntimeError("Imported disk not found in VM config (no unused0 key)")

    proxmox.nodes(node_name).qemu(vmid).config.put(scsi0=unused_disk)
    console.print(f"  [dim]Attached {unused_disk} as scsi0[/dim]")

    proxmox.nodes(node_name).qemu(vmid).config.put(ide2=f"{storage}:cloudinit")
    console.print(f"  [dim]Added cloud-init drive (ide2)[/dim]")

    proxmox.nodes(node_name).qemu(vmid).config.put(serial0="socket", vga="serial0")
    proxmox.nodes(node_name).qemu(vmid).config.put(boot="order=scsi0")

    proxmox.nodes(node_name).qemu(vmid).resize.put(disk="scsi0", size=f"{disk_gb}G")
    console.print(f"  [dim]Resized scsi0 to {disk_gb} GB[/dim]")

    proxmox.nodes(node_name).qemu(vmid).config.put(**ci_params)


def start_vm(proxmox: ProxmoxAPI, node_name: str, vmid: int) -> None:
    """Start a QEMU VM and wait for the task to complete. Raises on failure."""
    with console.status("[bold green]Starting VM..."):
        task = proxmox.nodes(node_name).qemu(vmid).status.start.post()
        wait_for_task(proxmox, node_name, task, timeout=60)
    console.print("[green]✓ VM started[/green]")


# ─────────────────────────────────────────────
# LXC creation and configuration
# ─────────────────────────────────────────────


def wait_for_lxc_ip(proxmox: ProxmoxAPI, node: str, vmid: int,
                    timeout: int = 300) -> tuple[str, str]:
    """
    Poll the container's interface list until a non-loopback IPv4 appears.
    Returns (ip, prefix_len) e.g. ("10.20.20.133", "24").
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ifaces = proxmox.nodes(node).lxc(vmid).interfaces.get()
            for iface in ifaces:
                inet = iface.get("inet", "")
                if inet and not inet.startswith("127."):
                    parts = inet.split("/")
                    ip = parts[0]
                    prefix = parts[1] if len(parts) > 1 else "24"
                    if ip:
                        return ip, prefix
        except Exception:
            pass
        time.sleep(5)
    raise TimeoutError("Could not obtain DHCP IP address within timeout. "
                       "Check that the container started and VLAN/DHCP is reachable.")


def run_pct_exec(ssh: paramiko.SSHClient, vmid: int, cmd: str, check: bool = True) -> tuple[int, str, str]:
    """Run a command inside an LXC container via pct exec on the proxmox node."""
    full_cmd = f"pct exec {vmid} -- bash -c {cmd!r}"
    stdin, stdout, stderr = ssh.exec_command(full_cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if check and exit_code != 0:
        raise RuntimeError(f"pct exec failed (exit {exit_code}): {err or out}")
    return exit_code, out, err


def bootstrap_lxc_ssh(cfg: dict, node_name: str, vmid: int, password: str) -> None:
    """
    SSH to the Proxmox node and use pct exec to:
      1. Update apt cache
      2. Install openssh-server
      3. Enable SSH daemon
      4. Allow PermitRootLogin and PasswordAuthentication
      5. Set root password
    This enables Ansible to then SSH directly into the container.
    LXC containers always use DHCP — no network reconfiguration is done here.
    """
    pve = cfg["proxmox"]
    ssh_host = node_ssh_host(cfg, node_name)
    ssh_key = os.path.expanduser(pve.get("ssh_key", "~/.ssh/id_rsa"))

    console.print(f"  [dim]Connecting to Proxmox node {ssh_host} for bootstrap...[/dim]")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(ssh_host, username="root", key_filename=ssh_key, timeout=30)
    except paramiko.AuthenticationException:
        raise RuntimeError(
            f"SSH key auth to {ssh_host} failed. Ensure {ssh_key} is authorized on the node."
        )

    steps = [
        ("Updating apt cache in container",     "apt-get update -qq"),
        ("Installing openssh-server",            "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server"),
        ("Enabling and starting SSH",            "systemctl enable --now ssh"),
        ("Allowing root SSH login",
            "sed -i 's/^#\\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && "
            "sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config && "
            "systemctl restart ssh"),
    ]

    for label, cmd in steps:
        console.print(f"  [dim]{label}...[/dim]")
        try:
            run_pct_exec(ssh, vmid, cmd)
        except RuntimeError as e:
            console.print(f"  [yellow]Warning: {e}[/yellow]")

    console.print("  [dim]Setting root password...[/dim]")
    stdin, stdout, stderr = ssh.exec_command(
        f"echo 'root:{password}' | pct exec {vmid} -- chpasswd"
    )
    stdout.channel.recv_exit_status()

    # LXC containers always use DHCP (ip=dhcp is hardcoded at creation).
    # No static network config is needed or safe here — applying a static
    # netplan during bootstrap causes the DHCP lease to be abandoned while
    # other containers are still acquiring leases, leading to IP conflicts.

    ssh.close()
    console.print("  [green]✓ Bootstrap complete — SSH is ready[/green]")


def create_lxc(proxmox: ProxmoxAPI, node_name: str, create_params: dict) -> int:
    """Create an LXC container, retrying up to 3 times on VMID collision.
    Returns the actual vmid used. Raises RuntimeError on unrecoverable failure."""
    next_vmid = create_params["vmid"]
    for _attempt in range(3):
        create_params["vmid"] = next_vmid
        try:
            with console.status(f"[bold green]Creating container {next_vmid} ({create_params.get('hostname', '')}) on {node_name}..."):
                task = proxmox.nodes(node_name).lxc.post(**create_params)
                wait_for_task(proxmox, node_name, task, timeout=180)
            console.print(f"[green]✓ Container {next_vmid} created[/green]")
            return next_vmid
        except Exception as e:
            retryable = "already exists" in str(e) or "can't lock file" in str(e)
            if retryable and _attempt < 2:
                old_vmid = next_vmid
                next_vmid = get_next_vmid(proxmox)
                console.print(
                    f"[yellow]⚠ VMID {old_vmid} already in use (race condition) — "
                    f"retrying with VMID {next_vmid}[/yellow]"
                )
            else:
                raise RuntimeError(f"Container creation failed: {e}") from e
    raise RuntimeError("Container creation failed after 3 attempts")


def apply_lxc_features_ssh(cfg: dict, node_name: str, vmid: int, features_str: str) -> None:
    """Apply LXC feature flags via pct set over SSH.
    Non-fatal: logs a warning if SSH fails (Proxmox API rejects non-nesting flags via token auth)."""
    pve = cfg["proxmox"]
    ssh_host = node_ssh_host(cfg, node_name)
    ssh_key = os.path.expanduser(pve.get("ssh_key", "~/.ssh/id_rsa"))
    console.print(f"  [dim]Applying LXC feature flags via SSH ({features_str})...[/dim]")
    try:
        _ssh = paramiko.SSHClient()
        _ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        _ssh.connect(ssh_host, username="root", key_filename=ssh_key, timeout=30)
        _, _out, _err = _ssh.exec_command(f"pct set {vmid} -features '{features_str}'")
        _exit = _out.channel.recv_exit_status()
        _ssh.close()
        if _exit != 0:
            console.print(f"[yellow]⚠ pct set features returned exit {_exit} — check Proxmox GUI[/yellow]")
        else:
            console.print(f"[green]✓ Feature flags applied: {features_str}[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Could not apply feature flags via SSH: {e}[/yellow]")


def start_lxc(proxmox: ProxmoxAPI, node_name: str, vmid: int) -> None:
    """Start an LXC container and wait for the task to complete. Raises on failure."""
    with console.status("[bold green]Starting container..."):
        task = proxmox.nodes(node_name).lxc(vmid).status.start.post()
        wait_for_task(proxmox, node_name, task, timeout=60)
    console.print("[green]✓ Container started[/green]")


def find_lxc_by_hostname(proxmox: ProxmoxAPI, hostname: str) -> dict | None:
    """Search all nodes for an LXC container whose name matches hostname.
    Returns {"node": ..., "vmid": ..., "status": ...} or None."""
    for node_info in proxmox.nodes.get():
        node = node_info["node"]
        try:
            for ct in proxmox.nodes(node).lxc.get():
                if ct.get("name") == hostname:
                    return {
                        "node":   node,
                        "vmid":   int(ct["vmid"]),
                        "status": ct.get("status", "unknown"),
                    }
        except Exception:
            pass
    return None


def get_running_vmids(proxmox: ProxmoxAPI) -> set[int]:
    """Return set of VMIDs currently running across all nodes (both lxc and qemu)."""
    running = set()
    for node_info in proxmox.nodes.get():
        node = node_info["node"]
        try:
            for ct in proxmox.nodes(node).lxc.get():
                if ct.get("status") == "running":
                    running.add(int(ct["vmid"]))
        except Exception:
            pass
        try:
            for vm in proxmox.nodes(node).qemu.get():
                if vm.get("status") == "running":
                    running.add(int(vm["vmid"]))
        except Exception:
            pass
    return running


def preflight_checks(cfg: dict, kind: str) -> list:
    """Return Proxmox-specific preflight check results."""
    from modules.preflight import _pf_proxmox_reachable, _pf_proxmox_auth, _pf_proxmox_ssh
    return [_pf_proxmox_reachable(cfg), _pf_proxmox_auth(cfg), _pf_proxmox_ssh(cfg)]
