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
import socket
import time
import subprocess
import tempfile
import textwrap
from datetime import datetime
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
# Config
# ─────────────────────────────────────────────

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]ERROR: config.yaml not found at {config_path}[/red]")
        sys.exit(1)
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    if cfg["proxmox"]["token_secret"] == "CHANGEME-PASTE-YOUR-TOKEN-SECRET-HERE":
        console.print("[red]ERROR: Edit config.yaml and set proxmox.token_secret[/red]")
        sys.exit(1)
    return cfg


# ─────────────────────────────────────────────
# Proxmox helpers
# ─────────────────────────────────────────────

def connect_proxmox(cfg: dict) -> ProxmoxAPI:
    pve = cfg["proxmox"]
    return ProxmoxAPI(
        pve["host"],
        user=pve["user"],
        token_name=pve["token_name"],
        token_value=pve["token_secret"],
        verify_ssl=pve.get("verify_ssl", False),
    )


def get_nodes_with_load(proxmox: ProxmoxAPI) -> list[dict]:
    """Return online nodes sorted by free RAM (descending)."""
    nodes = []
    for node in proxmox.nodes.get():
        if node.get("status") == "online":
            maxmem = node.get("maxmem", 0)
            mem = node.get("mem", 0)
            nodes.append({
                "name": node["node"],
                "free_mem": maxmem - mem,
                "maxmem": maxmem,
                "mem": mem,
                "cpu": node.get("cpu", 0),
                "maxcpu": node.get("maxcpu", 1),
            })
    return sorted(nodes, key=lambda x: -x["free_mem"])


def bytes_to_gb(b: int) -> str:
    return f"{b / (1024 ** 3):.1f}"


def get_next_vmid(proxmox: ProxmoxAPI) -> int:
    return int(proxmox.cluster.nextid.get())


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

        storage_choices = []
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
        if selected_storage is None:
            sys.exit(0)

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
            sys.exit(0)
        if selected == _BACK:
            continue  # Back to storage selection

        image_refresh = selected["action"] == "download"
        return selected_storage, selected["filename"], selected.get("url"), image_refresh


def wait_for_task(proxmox: ProxmoxAPI, node: str, taskid: str, timeout: int = 180) -> None:
    """Poll until a Proxmox task completes. Raises on failure or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = proxmox.nodes(node).tasks(taskid).status.get()
            if status["status"] == "stopped":
                exit_status = status.get("exitstatus", "")
                if exit_status != "OK":
                    raise RuntimeError(f"Proxmox task failed: {exit_status}")
                return
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Proxmox task {taskid} did not complete within {timeout}s")


def wait_for_ssh(host: str, timeout: int = 300) -> None:
    """Poll until SSH port (22) accepts TCP connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, 22), timeout=5):
                return
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(5)
    raise TimeoutError(f"SSH on {host}:22 did not become reachable within {timeout}s")


def wait_for_guest_agent_ip(proxmox: ProxmoxAPI, node: str, vmid: int,
                             timeout: int = 300) -> str:
    """
    Poll the QEMU guest agent until it reports a non-loopback IPv4 address.
    Used for DHCP VMs where the IP isn't known at deploy time.
    Requires qemu-guest-agent to be installed and running inside the VM
    (cloud-init installs it during first boot via post-deploy-vm.yml is too late;
    it must be in the cloud image or cloud-init user-data — Ubuntu cloud images
    include it by default since 22.04).
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


def node_ssh_host(cfg: dict, node_name: str) -> str:
    """Construct the SSH hostname for a proxmox node."""
    domain = cfg["proxmox"].get("node_domain", "")
    return f"{node_name}.{domain}" if domain else node_name


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


# ─────────────────────────────────────────────
# Ansible runners
# ─────────────────────────────────────────────

def check_ansible() -> None:
    """Ensure ansible-playbook is available."""
    result = subprocess.run(["which", "ansible-playbook"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("ansible-playbook not found. Install Ansible: apt install ansible")


def run_ansible_post_deploy_vm(vm_ip: str, ssh_key: str, password: str, hostname: str, cfg: dict = None) -> None:
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
            f"ansible_python_interpreter=/usr/bin/python3 "
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


def run_ansible_add_dns(cfg: dict, hostname: str, vm_ip: str) -> None:
    """Register A and PTR records on the BIND DNS server (skipped if dns.enabled is false)."""
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        console.print("  [dim]DNS registration skipped (disabled in config)[/dim]")
        return

    ansible_dir = Path(__file__).parent / "ansible"
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname

    ip_parts = vm_ip.split(".")
    reverse_zone = f"{ip_parts[2]}.{ip_parts[1]}.{ip_parts[0]}.in-addr.arpa"
    zone_dir = str(Path(dns["forward_zone_file"]).parent)
    reverse_zone_file = f"{zone_dir}/{reverse_zone}.hosts"

    cmd = [
        "ansible-playbook",
        "-i", f"{dns['server']},",
        str(ansible_dir / "add-dns.yml"),
        "-e", f"new_hostname={hostname}",
        "-e", f"new_ip={vm_ip}",
        "-e", f"new_fqdn={fqdn}",
        "-e", f"forward_zone_file={dns['forward_zone_file']}",
        "-e", f"reverse_zone_file={reverse_zone_file}",
        "-u", dns.get("ssh_user", "root"),
        "--timeout", "30",
    ]
    console.print(f"  [dim]Registering {fqdn} → {vm_ip} on {dns['server']}...[/dim]")
    result = subprocess.run(cmd, cwd=str(ansible_dir))
    if result.returncode != 0:
        console.print(
            f"  [yellow]Warning: DNS registration failed. "
            f"Add manually: {fqdn} A {vm_ip} and PTR.[/yellow]"
        )
    else:
        console.print(f"  [green]✓ DNS registered: {fqdn} → {vm_ip} (+ PTR)[/green]")


def run_ansible_inventory_update(cfg: dict, hostname: str, vm_ip: str, password: str) -> None:
    """Run the inventory-update playbook against the development server."""
    inv_cfg = cfg.get("ansible_inventory", {})
    if not inv_cfg:
        console.print("  [dim]Inventory update skipped (not configured)[/dim]")
        return

    ansible_dir = Path(__file__).parent / "ansible"
    dev_server = inv_cfg["server"]
    dev_user = inv_cfg.get("user", "root")

    cmd = [
        "ansible-playbook",
        "-i", f"{dev_server},",
        str(ansible_dir / "update-inventory.yml"),
        "-e", f"new_hostname={hostname}",
        "-e", f"new_ip={vm_ip}",
        "-e", f"inventory_file={inv_cfg['file']}",
        "-e", f"inventory_group={inv_cfg['group']}",
        "-e", f"password={password}",
        "-e", f"node_domain={cfg['proxmox'].get('node_domain', '')}",
        "-u", dev_user,
        "--timeout", "30",
    ]
    console.print(f"  [dim]Connecting to {dev_server} to update inventory...[/dim]")
    result = subprocess.run(cmd, cwd=str(ansible_dir))
    if result.returncode != 0:
        console.print(
            f"  [yellow]Warning: Inventory update failed. "
            f"Add manually: {hostname} ansible_host={vm_ip} "
            f"to [{inv_cfg['group']}][/yellow]"
        )
    else:
        console.print(f"  [green]✓ Inventory updated on {dev_server}[/green]")


# ─────────────────────────────────────────────
# Deployment file helpers
# ─────────────────────────────────────────────

def load_deployment_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        console.print(f"[red]ERROR: Deployment file not found: {path}[/red]")
        sys.exit(1)
    with open(p) as f:
        return json.load(f) or {}


def save_vm_deployment_file(hostname: str, vmid: int, node_name: str,
                             image_storage_name: str, image_filename: str,
                             image_url: str | None, image_refresh: bool,
                             cpus_str: str, memory_gb_str: str,
                             disk_gb_str: str, storage: str, vlan_str: str,
                             bridge: str, password: str, ip_address: str,
                             prefix_len: str, gateway: str, assigned_ip: str,
                             cfg: dict) -> Path:
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
        "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if assigned_ip and assigned_ip != ip_address:
        data["assigned_ip"] = assigned_ip  # DHCP-assigned address for reference
    with open(deploy_file, "w") as f:
        json.dump(data, f, indent=2)
    console.print(f"  [dim]Deployment file saved: {deploy_file}[/dim]")
    return deploy_file


def q(widget_fn, *args, d: dict | None = None, key: str | None = None,
      silent: bool = False, cast=str, **kwargs):
    """Ask a question, using deployment file value as default or skipping in silent mode."""
    val = cast(d[key]) if (d and key and key in d and d[key] is not None) else None
    if val is not None and silent:
        return val
    if val is not None:
        kwargs["default"] = val
    result = widget_fn(*args, **kwargs).ask()
    if result is None:
        sys.exit(0)
    return result


def derive_gateway(ip: str) -> str:
    """Derive gateway as the .1 address of the subnet."""
    parts = ip.rsplit(".", 1)
    return f"{parts[0]}.1"


# ─────────────────────────────────────────────
# Main wizard
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deploy_vm.py",
        description="Proxmox VM Deploy Wizard — cloud-init VM provisioning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 deploy_vm.py
              python3 deploy_vm.py --deploy-file deployments/vms/myvm.json
              python3 deploy_vm.py --deploy-file deployments/vms/myvm.json --silent
        """),
    )
    parser.add_argument(
        "--deploy-file", metavar="FILE",
        help="JSON deployment file to pre-fill defaults (saved from a previous run)",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Non-interactive mode: use all values from --deploy-file without prompting",
    )
    args = parser.parse_args()

    if args.silent and not args.deploy_file:
        parser.error("--silent requires --deploy-file")

    cfg = load_config()
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
        Text("Proxmox VM Deploy Wizard", style="bold green"),
        subtitle="[dim]github: vm-onboard[/dim]",
        border_style="green",
    ))
    console.print()

    if deploy and not silent:
        console.print(f"[dim]Loaded deployment file: {args.deploy_file}[/dim]\n")
    elif deploy and silent:
        console.print(f"[dim]Silent mode — deploying from: {args.deploy_file}[/dim]\n")

    # Pre-flight
    check_ansible()

    # ── Connect to Proxmox ──
    with console.status("[bold green]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
            nodes = get_nodes_with_load(proxmox)
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    if not nodes:
        console.print("[red]No online nodes found in the cluster.[/red]")
        sys.exit(1)

    console.print(f"[green]✓ Connected.[/green] {len(nodes)} node(s) online.\n")

    # ═══════════════════════════════════════════
    # Interactive prompts
    # ═══════════════════════════════════════════

    hostname = q(
        questionary.text,
        "Hostname for the new VM:",
        instruction="(short name only — domain suffix from config will be appended in inventory)",
        validate=lambda v: True if v.strip() else "Hostname cannot be empty",
        d=deploy, key="hostname", silent=silent,
    ).strip().lower()

    cpus_str = q(
        questionary.text,
        "Number of vCPUs:",
        default=str(defaults.get("cpus", 2)),
        validate=lambda v: True if v.isdigit() and int(v) > 0 else "Must be a positive integer",
        d=deploy, key="cpus", silent=silent,
    )

    memory_gb_str = q(
        questionary.text,
        "Memory (GB):",
        default=str(defaults.get("memory_gb", 4)),
        validate=lambda v: (True if v.replace(".", "", 1).isdigit() and float(v) > 0
                            else "Must be a positive number"),
        d=deploy, key="memory_gb", silent=silent,
    )

    disk_gb_str = q(
        questionary.text,
        "Disk size (GB):",
        default=str(defaults.get("disk_gb", 100)),
        validate=lambda v: True if v.isdigit() and int(v) > 0 else "Must be a positive integer",
        d=deploy, key="disk_gb", silent=silent,
    )

    vlan_str = q(
        questionary.text,
        "VLAN tag (bridge: vmbr0.<vlan>):",
        default=str(defaults.get("vlan", 220)),
        validate=lambda v: (True if v.isdigit() and 1 <= int(v) <= 4094
                            else "Must be a valid VLAN ID (1–4094)"),
        d=deploy, key="vlan", silent=silent,
    )

    password = q(
        questionary.text,
        f"Root / {addusername} user password:",
        default=defaults.get("root_password", "changeme"),
        d=deploy, key="password", silent=silent,
    )

    # ── IP address — blank or "dhcp" = DHCP mode ──
    deploy_ip = str(deploy.get("ip_address", ""))
    ip_default = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip

    if silent:
        ip_address = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
    else:
        ip_answer = questionary.text(
            "IP address for VM:",
            instruction="(e.g. 10.20.20.200  —  leave blank for DHCP)",
            default=ip_default,
            validate=lambda v: (
                True if v.strip() == "" or v.strip().count(".") == 3
                else "Enter a valid IPv4 address or leave blank for DHCP"
            ),
        ).ask()
        if ip_answer is None:
            sys.exit(0)
        ip_address = ip_answer.strip()

    use_dhcp = (ip_address == "")

    if not use_dhcp:
        prefix_len = q(
            questionary.text,
            "Prefix length (subnet mask bits):",
            default="24",
            validate=lambda v: True if v.isdigit() and 1 <= int(v) <= 32 else "Must be 1–32",
            d=deploy, key="prefix_len", silent=silent,
        ).strip()

        auto_gw = derive_gateway(ip_address)
        gateway = q(
            questionary.text,
            "Gateway:",
            default=auto_gw,
            validate=lambda v: True if v.count(".") == 3 else "Enter a valid IPv4 address",
            d=deploy, key="gateway", silent=silent,
        ).strip()
    else:
        prefix_len = ""
        gateway = ""

    # ── Node selection (filtered by resources) ──
    memory_mb = int(float(memory_gb_str) * 1024)
    filtered_nodes = [n for n in nodes if node_passes_filter(n, memory_mb, cpu_threshold, ram_threshold)]

    if not filtered_nodes:
        console.print(
            f"[yellow]Warning: No nodes pass the resource filter "
            f"(CPU <85%, RAM after +{memory_gb_str} GB <95%). Showing all nodes.[/yellow]"
        )
        filtered_nodes = nodes

    best_node = filtered_nodes[0]

    if silent:
        node_name = str(deploy.get("node", best_node["name"]))
        if not any(n["name"] == node_name for n in nodes):
            console.print(f"[red]ERROR: Node '{node_name}' from deployment file is not online.[/red]")
            sys.exit(1)
        console.print(f"  [dim]Node (from deployment file): {node_name}[/dim]")
    else:
        deploy_node = str(deploy.get("node", ""))
        node_choices = []
        for n in filtered_nodes:
            is_best = n["name"] == best_node["name"]
            suffix = " [deploy file]" if n["name"] == deploy_node else ""
            node_choices.append(questionary.Choice(
                title=(
                    f"{'★ ' if is_best else '  '}"
                    f"{n['name']}  —  "
                    f"{bytes_to_gb(n['free_mem'])} GB free / "
                    f"{bytes_to_gb(n['maxmem'])} GB RAM  "
                    f"(CPU: {n['cpu'] * 100:.0f}%){suffix}"
                ),
                value=n["name"],
            ))
        default_node = (
            deploy_node if any(n["name"] == deploy_node for n in filtered_nodes)
            else best_node["name"]
        )
        hidden = len(nodes) - len(filtered_nodes)
        hint = f" ({hidden} node(s) hidden — over resource threshold)" if hidden else ""
        node_name = questionary.select(
            f"Select Proxmox node (★ = most free RAM){hint}:",
            choices=node_choices,
            default=default_node,
        ).ask()
        if node_name is None:
            sys.exit(0)

    # ── Cloud image storage + image selection ──
    catalog = load_cloud_images()
    image_storage_name, image_filename, image_url, image_refresh = select_image_with_storage(
        proxmox, node_name, cfg, deploy, silent, catalog,
    )

    # ── Storage pool ──
    with console.status(f"[bold green]Querying storage pools on {node_name}..."):
        storage_pools = get_vm_disk_storages(proxmox, node_name)

    if len(storage_pools) > 1:
        storage = q(
            questionary.select,
            "Select storage pool for VM disk:",
            choices=storage_pools,
            d=deploy, key="storage", silent=silent,
        )
    else:
        storage = storage_pools[0]
        console.print(f"  [dim]Storage pool: {storage}[/dim]")

    # ── Read SSH public key for cloud-init injection ──
    pve = cfg["proxmox"]
    ssh_key = os.path.expanduser(pve.get("ssh_key", "~/.ssh/id_rsa"))
    pub_key_path = ssh_key + ".pub"
    pub_key_encoded = None
    if os.path.exists(pub_key_path):
        pub_key = open(pub_key_path).read().strip()
        pub_key_encoded = quote(pub_key, safe="")
    else:
        console.print(
            f"[yellow]Warning: SSH public key not found at {pub_key_path}. "
            f"Key injection skipped — Ansible will attempt password auth.[/yellow]"
        )

    # ═══════════════════════════════════════════
    # Summary & confirmation
    # ═══════════════════════════════════════════
    next_vmid = get_next_vmid(proxmox)
    bridge = defaults.get("bridge", "vmbr0")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    net_display = (
        f"{bridge}.{vlan_str}  (DHCP — IP assigned at boot)"
        if use_dhcp else
        f"{bridge}.{vlan_str}  (static {ip_address}/{prefix_len}  gw {gateway})"
    )

    console.print()
    table = Table(title="VM Deployment Summary", show_header=False, border_style="green", padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("VMID",      str(next_vmid))
    table.add_row("Hostname",  hostname)
    table.add_row("Node",      node_name)
    table.add_row("Image",     f"{image_storage_name}:{image_filename}")
    table.add_row("Machine",   f"{machine} / {bios} / {cpu_type} / {scsihw}")
    table.add_row("vCPUs",     cpus_str)
    table.add_row("Memory",    f"{memory_gb_str} GB ({memory_mb} MB)")
    table.add_row("Disk",      f"{disk_gb_str} GB  →  {storage}  (scsi0)")
    table.add_row("Network",   net_display)
    table.add_row("SSH key",   pub_key_path if pub_key_encoded else "[yellow]not found — password only[/yellow]")
    table.add_row("Tags",      "auto-deploy")
    table.add_row("Users",     f"root, {addusername} (same password)")
    table.add_row("Timezone",  cfg.get("timezone", "UTC"))
    table.add_row("NTP",       ", ".join(cfg.get("ntp", {}).get("servers", ["pool.ntp.org"])))
    table.add_row("SNMP",      f"community='{cfg['snmp']['community']}' (rw) on :161")
    console.print(table)
    console.print()

    if not silent:
        confirm = questionary.confirm("Proceed with deployment?", default=True).ask()
        if not confirm:
            console.print("[yellow]Deployment cancelled.[/yellow]")
            sys.exit(0)

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
        "tags":        "auto-deploy",
        "description": vm_note,
    }

    try:
        with console.status(f"[bold green]Creating VM {next_vmid} ({hostname}) on {node_name}..."):
            task = proxmox.nodes(node_name).qemu.post(**create_params)
            wait_for_task(proxmox, node_name, task, timeout=60)
        console.print(f"[green]✓ VM {next_vmid} created[/green]")
    except Exception as e:
        console.print(f"[red]✗ VM creation failed: {e}[/red]")
        sys.exit(1)

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
        proxmox.nodes(node_name).qemu(next_vmid).config.put(**ci_params)
        console.print(
            f"  [dim]Cloud-init: {'DHCP' if use_dhcp else f'{ip_address}/{prefix_len} gw {gateway}'}"
            f"{' + SSH key' if pub_key_encoded else ''}[/dim]"
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
    try:
        run_ansible_post_deploy_vm(vm_ip, ssh_key, password, hostname, cfg=cfg)
        console.print("[green]✓ Post-deployment configuration complete[/green]")
    except Exception as e:
        console.print(f"[red]✗ Post-deploy failed: {e}[/red]")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Step 6/7: Register DNS
    # ═══════════════════════════════════════════
    console.print("[bold green]─── Step 6/7: Registering DNS ───[/bold green]")
    run_ansible_add_dns(cfg, hostname, vm_ip)

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
    )

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
