"""
labinator.ansible — Ansible playbook runners for post-deploy and inventory management.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console

console = Console()

_ROOT = Path(__file__).parent.parent


def run_ansible_post_deploy(ip: str, password: str, hostname: str, cfg: dict, kind: str,
                             nameserver: str = "", searchdomain: str = "",
                             ssh_key: str = "",
                             profile_packages: list = (),
                             extra_packages: list = ()) -> None:
    """Run the post-deploy Ansible playbook against a new LXC container or VM.

    kind='lxc' — password auth, post-deploy.yml, container_hostname var.
    kind='vm'  — SSH key auth, post-deploy-vm.yml, vm_hostname var.
    """
    ansible_dir = _ROOT / "ansible"
    snmp = (cfg or {}).get("snmp", {})
    addusername = (cfg or {}).get("defaults", {}).get("addusername", "admin")
    tz = (cfg or {}).get("timezone", "UTC")
    ntp_servers = (cfg or {}).get("ntp", {}).get("servers", ["pool.ntp.org", "time.nist.gov"])

    if kind == "lxc":
        inv_extras = f"ansible_password={password} "
        prefix = "deploy_inv_"
    else:
        inv_extras = "ansible_python_interpreter=auto "
        prefix = "deploy_vm_inv_"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, prefix=prefix) as f:
        f.write("[all]\n")
        f.write(
            f"{ip} ansible_user=root {inv_extras}"
            "ansible_ssh_extra_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'\n"
        )
        inv_path = f.name

    playbook = "post-deploy.yml" if kind == "lxc" else "post-deploy-vm.yml"
    hostname_var = "container_hostname" if kind == "lxc" else "vm_hostname"

    try:
        cmd = [
            "ansible-playbook",
            "-i", inv_path,
            str(ansible_dir / playbook),
            "-e", f"{hostname_var}={hostname}",
            "-e", f"password={password}",
            "-e", f"addusername={addusername}",
        ]
        if kind == "lxc":
            cmd += [
                "-e", f"container_nameserver={nameserver}",
                "-e", f"container_searchdomain={searchdomain}",
            ]
        cmd += [
            "-e", f"snmp_community={snmp.get('community', 'your-snmp-community')}",
            "-e", f"snmp_source={snmp.get('source', 'default')}",
            "-e", f"snmp_location={snmp.get('location', 'Homelab')}",
            "-e", f"snmp_contact={snmp.get('contact', 'admin@example.com')}",
            "-e", f"timezone={tz}",
            "-e", json.dumps({"ntp_servers": ntp_servers}),
        ]
        if kind == "vm" and ssh_key:
            cmd += ["--private-key", ssh_key]
        cmd += ["--timeout", "60"]
        if profile_packages:
            cmd += ["-e", json.dumps({"profile_packages": list(profile_packages)})]
        if extra_packages:
            cmd += ["-e", json.dumps({"extra_packages": list(extra_packages)})]
        cmd_display = [
            arg.split("=")[0] + "=**REDACTED**" if arg.startswith("password=") else arg
            for arg in cmd
        ]
        console.print(f"  [dim]Running: {' '.join(cmd_display)}[/dim]")
        result = subprocess.run(cmd, cwd=str(ansible_dir))
        if result.returncode != 0:
            raise RuntimeError("Ansible post-deploy playbook failed (see output above)")
    finally:
        os.unlink(inv_path)


def run_ansible_inventory_update(cfg: dict, hostname: str, ip: str, password: str) -> None:
    """Run the inventory-update playbook against the development server."""
    inv_cfg = cfg.get("ansible_inventory", {})
    if not inv_cfg:
        console.print("  [dim]Inventory update skipped (not configured)[/dim]")
        return
    if not inv_cfg.get("enabled", True):
        console.print("  [dim]Inventory update skipped (ansible_inventory.enabled: false)[/dim]")
        return

    ansible_dir = _ROOT / "ansible"
    dev_server = inv_cfg["server"]
    dev_user = inv_cfg.get("user", "root")

    cmd = [
        "ansible-playbook",
        "-i", f"{dev_server},",
        str(ansible_dir / "update-inventory.yml"),
        "-e", f"new_hostname={hostname}",
        "-e", f"new_ip={ip}",
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
            f"Add manually: {hostname} ansible_host={ip} "
            f"to [{inv_cfg['group']}][/yellow]"
        )
    else:
        console.print(f"  [green]✓ Inventory updated on {dev_server}[/green]")


def remove_from_inventory(cfg: dict, deploy: dict) -> None:
    """Remove a host from the Ansible inventory via playbook (skipped if not configured)."""
    inv_cfg = cfg.get("ansible_inventory", {})
    if not inv_cfg:
        console.print("  [dim]Inventory removal skipped (not configured)[/dim]")
        return

    ansible_dir = _ROOT / "ansible"
    hostname = deploy["hostname"]
    dev_server = inv_cfg["server"]
    dev_user = inv_cfg.get("user", "root")

    cmd = [
        "ansible-playbook",
        "-i", f"{dev_server},",
        str(ansible_dir / "remove-from-inventory.yml"),
        "-e", f"hostname={hostname}",
        "-e", f"inventory_file={inv_cfg['file']}",
        "-u", dev_user,
        "--timeout", "30",
    ]
    console.print(f"  [dim]Removing {hostname} from Ansible inventory on {dev_server}...[/dim]")
    result = subprocess.run(cmd, cwd=str(ansible_dir))
    if result.returncode != 0:
        console.print(f"  [yellow]Warning: Inventory removal failed. Remove manually: {hostname}[/yellow]")
    else:
        console.print(f"  [green]✓ Removed from inventory[/green]")


def _ansible_inventory_reachable(cfg: dict):
    """Check whether the Ansible inventory server is reachable on port 22."""
    from modules.preflight import _PF
    inv = cfg.get("ansible_inventory", {})
    if not inv or not inv.get("enabled", True):
        return _PF("Inventory server reachable", True, "ansible_inventory not configured — skipped", fatal=False)
    server = inv.get("server", "")
    try:
        with socket.create_connection((server, 22), timeout=5):
            pass
        return _PF("Inventory server reachable", True, f"{server}:22 OK", fatal=False)
    except OSError as e:
        return _PF("Inventory server reachable", False, f"{server}: {e}", fatal=False)


def _ansible_inventory_ssh_auth(cfg: dict):
    """Check SSH key-based auth to the Ansible inventory server."""
    from modules.preflight import _PF
    inv = cfg.get("ansible_inventory", {})
    if not inv or not inv.get("enabled", True):
        return _PF("Inventory SSH auth", True, "ansible_inventory not configured — skipped", fatal=False)
    server = inv.get("server", "")
    user = inv.get("user", "root")
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", f"{user}@{server}", "echo", "OK"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and "OK" in result.stdout:
            return _PF("Inventory SSH auth", True, f"{user}@{server} OK", fatal=False)
        return _PF("Inventory SSH auth", False, f"Auth failed: {user}@{server}", fatal=False)
    except Exception as e:
        return _PF("Inventory SSH auth", False, str(e), fatal=False)


def preflight_checks(cfg: dict, kind: str) -> list:
    """Return Ansible inventory preflight check results."""
    return [_ansible_inventory_reachable(cfg), _ansible_inventory_ssh_auth(cfg)]
