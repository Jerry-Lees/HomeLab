#!/usr/bin/env python3
"""
Proxmox LXC Deploy Wizard
=========================
Interactive wizard to provision, configure, and onboard new LXC containers
in a Proxmox cluster. Handles:
  - Interactive prompts with sensible defaults
  - Node selection (auto picks least-loaded node)
  - Template listing (Ubuntu-first)
  - Container creation via Proxmox API
  - Bootstrap via pct exec (installs SSH in container)
  - Post-deploy Ansible playbook (tools, SNMP, NTP, users, etc.)
  - Ansible inventory update on development server

Requirements:
  pip install -r requirements.txt
  ansible (system package or pip)
  sshpass (system package, needed for Ansible password auth)
"""

# Auto-activate virtualenv so `python3 deploy_lxc.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import base64
import ipaddress
import os
import socket
import sys
import time
import subprocess
import tempfile
import textwrap
from datetime import datetime
from pathlib import Path

import json
import yaml
import paramiko
import questionary
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

# Proxmoxer import with friendly error
try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

from modules.lib import (
    load_config,
    connect_proxmox,
    wait_for_task,
    health_check,
    _check_ipv4,
    validate_config,
    resolve_profile,
    dns_precheck,
    run_ansible_add_dns,
    run_ansible_inventory_update,
    get_nodes_with_load,
    bytes_to_gb,
    get_next_vmid,
    wait_for_ssh,
    node_ssh_host,
    run_preflight,
    parse_ttl,
    expires_at_from_ttl,
    q,
    load_deployment_file,
    prompt_package_profile,
    prompt_extra_packages,
    prompt_node_selection,
)

console = Console()


# ─────────────────────────────────────────────
# Validation (--validate flag)
# ─────────────────────────────────────────────


def validate_lxc_deployment(deploy_path: Path) -> list[str]:
    """Return a list of error strings; empty means deployment JSON is valid."""
    errors = []
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
        return ["This looks like a VM deployment file (\"type\": \"vm\") — use deploy_vm.py instead"]

    for field in ("hostname", "node", "template_name", "storage", "bridge", "password"):
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


def run_validate(args) -> None:
    """Run --validate checks, print a rich report, and exit 0 or 1."""
    from rich.table import Table as RichTable
    cfg_path = Path(__file__).parent / "config.yaml"
    all_errors: list[tuple[str, str]] = []  # (section, message)

    cfg_errors = validate_config(cfg_path)
    for e in cfg_errors:
        all_errors.append(("config.yaml", e))

    if args.deploy_file:
        deploy_errors = validate_lxc_deployment(Path(args.deploy_file))
        for e in deploy_errors:
            all_errors.append((args.deploy_file, e))

    console.print()
    console.print(Panel.fit(
        Text("Labinator Validate", style="bold yellow"),
        border_style="yellow",
    ))
    console.print()

    if not all_errors:
        console.print(f"[green]✓ config.yaml[/green]  OK")
        if args.deploy_file:
            console.print(f"[green]✓ {args.deploy_file}[/green]  OK")
        console.print()
        console.print("[bold green]All checks passed.[/bold green]")
        sys.exit(0)

    table = RichTable(show_header=True, header_style="bold red")
    table.add_column("File", style="dim")
    table.add_column("Error")
    for section, msg in all_errors:
        table.add_row(section, msg)
    console.print(table)
    console.print()
    console.print(f"[bold red]{len(all_errors)} error(s) found. Fix them before deploying.[/bold red]")
    sys.exit(1)


def run_dry_run(args) -> None:
    """--dry-run: validate config + deployment file, print what would happen, exit 0/1."""
    cfg_path = Path(__file__).parent / "config.yaml"

    console.print()
    console.print(Panel.fit(
        Text("Labinator Dry Run — LXC Deploy", style="bold yellow"),
        border_style="yellow",
    ))
    console.print()

    # ── Validate config ──
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

    # ── Validate deployment file ──
    deploy_errors = validate_lxc_deployment(Path(args.deploy_file))
    if deploy_errors:
        for e in deploy_errors:
            console.print(f"[red]✗ {args.deploy_file}: {e}[/red]")
        sys.exit(1)
    console.print(f"[green]✓ {args.deploy_file}[/green]  OK")

    with open(args.deploy_file) as f:
        d = json.load(f)

    # ── Derive display values ──
    hostname     = d.get("hostname", "?")
    node         = d.get("node", "?")
    storage      = d.get("storage", "?")
    template     = d.get("template_name", "?")
    cpus         = d.get("cpus", "?")
    memory_gb    = d.get("memory_gb", "?")
    disk_gb      = d.get("disk_gb", "?")
    extra_pkgs   = d.get("extra_packages", [])

    profiles = cfg.get("package_profiles", {})
    profile_packages, profile_tags = resolve_profile(d.get("package_profile", ""), profiles)
    tags = ";".join(["auto-deploy"] + profile_tags)

    domain = cfg.get("proxmox", {}).get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname

    ansible_enabled = cfg.get("ansible", {}).get("enabled", True)
    dns_cfg         = cfg.get("dns", {})
    dns_enabled     = dns_cfg.get("enabled", False)
    inv_cfg         = cfg.get("ansible_inventory", {})
    inv_enabled     = bool(inv_cfg) and inv_cfg.get("enabled", True)

    # ── Summary table ──
    tbl = Table(show_header=False, box=None, padding=(0, 1))
    tbl.add_column(style="bold")
    tbl.add_column()
    tbl.add_row("Hostname",    hostname)
    tbl.add_row("Node",        node)
    tbl.add_row("Template",    template)
    tbl.add_row("vCPUs",       str(cpus))
    tbl.add_row("Memory",      f"{memory_gb} GB")
    tbl.add_row("Disk",        f"{disk_gb} GB → {storage}")
    tbl.add_row("IP",          "DHCP (assigned at boot)")
    tbl.add_row("Profile pkgs", ", ".join(profile_packages) if profile_packages else "(none)")
    tbl.add_row("Extra pkgs",  ", ".join(extra_pkgs) if extra_pkgs else "(none)")
    tbl.add_row("Tags",        tags)
    console.print()
    console.print(Panel(tbl, title="[bold]LXC Deployment Summary[/bold]", border_style="dim"))
    console.print()

    # ── Step-by-step plan ──
    DRY = "[bold yellow][DRY RUN][/bold yellow]"

    console.print("[bold]Steps that would execute:[/bold]")
    console.print()
    console.print(f"  {DRY} Step 1/7  Create LXC container (next available VMID) — {hostname} on {node}")
    console.print(f"  {DRY} Step 2/7  Start container")
    console.print(f"  {DRY} Step 3/7  Wait for DHCP IP address")
    console.print(f"  {DRY} Step 4/7  Bootstrap SSH via pct exec on {node}")

    if ansible_enabled:
        console.print(f"  {DRY} Step 5/7  Run Ansible post-deploy playbook")
        if profile_packages:
            console.print(f"             [dim]└─ Profile packages : {', '.join(profile_packages)}[/dim]")
        if extra_pkgs:
            console.print(f"             [dim]└─ Extra packages   : {', '.join(extra_pkgs)}[/dim]")
    else:
        console.print(f"  {DRY} Step 5/7  [dim]Ansible post-deploy SKIPPED (ansible.enabled: false)[/dim]")

    if dns_enabled:
        console.print(f"  {DRY} Step 6/7  Register DNS: {fqdn} → <DHCP> on {dns_cfg.get('server', '?')}")
    else:
        console.print(f"  {DRY} Step 6/7  [dim]DNS registration SKIPPED (dns.enabled: false)[/dim]")

    if inv_enabled:
        console.print(f"  {DRY} Step 7/7  Update Ansible inventory on {inv_cfg.get('server', '?')}")
    else:
        console.print(f"  {DRY} Step 7/7  [dim]Inventory update SKIPPED (ansible_inventory.enabled: false)[/dim]")

    console.print()
    console.print("[bold green]Dry run complete — no changes made.[/bold green]")
    sys.exit(0)


# ─────────────────────────────────────────────
# Proxmox helpers
# ─────────────────────────────────────────────


def get_templates(proxmox: ProxmoxAPI, node: str) -> list[dict]:
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

    # Ubuntu first, then alphabetical within each group
    ubuntu = sorted([t for t in templates if "ubuntu" in t["name"].lower()], key=lambda x: x["name"], reverse=True)
    others = sorted([t for t in templates if "ubuntu" not in t["name"].lower()], key=lambda x: x["name"])
    return ubuntu + others


def get_disk_storages(proxmox: ProxmoxAPI, node: str) -> list[str]:
    """Return storage pools that can hold container root filesystems."""
    pools = []
    try:
        for s in proxmox.nodes(node).storage.get(enabled=1):
            content = s.get("content", "")
            if "rootdir" in content:
                pools.append(s["storage"])
    except Exception:
        pass
    return pools if pools else ["local-lvm"]


def wait_for_ip(proxmox: ProxmoxAPI, node: str, vmid: int, timeout: int = 120) -> tuple[str, str]:
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


# ─────────────────────────────────────────────
# Bootstrap via pct exec
# ─────────────────────────────────────────────

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


def bootstrap_container(cfg: dict, node_name: str, vmid: int, password: str,
                        container_ip: str, prefix_len: str,
                        nameserver: str, searchdomain: str) -> None:
    """
    SSH to the proxmox node and use pct exec to:
      1. Update apt cache
      2. Install openssh-server
      3. Enable SSH daemon
      4. Allow PermitRootLogin and PasswordAuthentication
      5. Set root password
      6. Write static netplan config and apply it (network-safe via pct exec)
    This enables Ansible to then SSH directly into the container.
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

    # Set root password (done separately to avoid quoting issues)
    console.print("  [dim]Setting root password...[/dim]")
    stdin, stdout, stderr = ssh.exec_command(
        f"echo 'root:{password}' | pct exec {vmid} -- chpasswd"
    )
    stdout.channel.recv_exit_status()

    # Set static IP via pct exec (immune to network restarts unlike Ansible SSH)
    console.print("  [dim]Configuring static IP...[/dim]")
    try:
        # Write a script to the container via base64 to avoid quoting nightmares
        net_script = (
            "import subprocess, json\n"
            "d = json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'default']))\n"
            "r = d[0]\n"
            "print(r.get('gateway', '') + '|' + r.get('dev', 'eth0'))\n"
        )
        encoded = base64.b64encode(net_script.encode()).decode()
        run_pct_exec(ssh, vmid, f"echo {encoded!r} | base64 -d > /tmp/_getnet.py")
        _, info, _ = run_pct_exec(ssh, vmid, "python3 /tmp/_getnet.py")
        run_pct_exec(ssh, vmid, "rm -f /tmp/_getnet.py", check=False)
        parts = info.strip().split('|', 1)
        gateway = parts[0].strip() if parts else ''
        iface = parts[1].strip() if len(parts) > 1 else 'eth0'
        iface = iface or 'eth0'
        ns_list = ", ".join(nameserver.split())

        netplan = (
            f"network:\n"
            f"  version: 2\n"
            f"  ethernets:\n"
            f"    {iface}:\n"
            f"      dhcp4: false\n"
            f"      addresses:\n"
            f"        - {container_ip}/{prefix_len}\n"
            f"      routes:\n"
            f"        - to: default\n"
            f"          via: {gateway}\n"
            f"      nameservers:\n"
            f"        addresses: [{ns_list}]\n"
            f"        search: [{searchdomain}]\n"
        )
        encoded = base64.b64encode(netplan.encode()).decode()
        run_pct_exec(ssh, vmid,
            f"echo {encoded!r} | base64 -d > /etc/netplan/01-static.yaml")
        run_pct_exec(ssh, vmid,
            "find /etc/netplan -name '*.yaml' ! -name '01-static.yaml' -delete")
        run_pct_exec(ssh, vmid, "netplan apply")
    except Exception as e:
        console.print(f"  [yellow]Warning: static IP config failed: {e}[/yellow]")

    ssh.close()
    console.print("  [green]✓ Bootstrap complete — SSH is ready[/green]")


# ─────────────────────────────────────────────
# Ansible runners
# ─────────────────────────────────────────────

def run_ansible_post_deploy(container_ip: str, password: str, hostname: str, nameserver: str, searchdomain: str, cfg: dict = None, profile_packages: list = (), extra_packages: list = ()) -> None:
    """Run the post-deploy Ansible playbook against the new container."""
    ansible_dir = Path(__file__).parent / "ansible"
    snmp = (cfg or {}).get("snmp", {})
    addusername = (cfg or {}).get("defaults", {}).get("addusername", "admin")
    timezone = (cfg or {}).get("timezone", "UTC")
    ntp_servers = (cfg or {}).get("ntp", {}).get("servers", ["pool.ntp.org", "time.nist.gov"])

    # Write a temp inventory file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False, prefix="deploy_inv_") as f:
        f.write(f"[all]\n")
        f.write(
            f"{container_ip} "
            f"ansible_user=root "
            f"ansible_password={password} "
            f"ansible_ssh_extra_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'\n"
        )
        inv_path = f.name

    try:
        cmd = [
            "ansible-playbook",
            "-i", inv_path,
            str(ansible_dir / "post-deploy.yml"),
            "-e", f"container_hostname={hostname}",
            "-e", f"password={password}",
            "-e", f"addusername={addusername}",
            "-e", f"container_nameserver={nameserver}",
            "-e", f"container_searchdomain={searchdomain}",
            "-e", f"snmp_community={snmp.get('community', 'your-snmp-community')}",
            "-e", f"snmp_source={snmp.get('source', 'default')}",
            "-e", f"snmp_location={snmp.get('location', 'Homelab')}",
            "-e", f"snmp_contact={snmp.get('contact', 'admin@example.com')}",
            "-e", f"timezone={timezone}",
            "-e", json.dumps({"ntp_servers": ntp_servers}),
            "--timeout", "60",
        ]
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




# ─────────────────────────────────────────────
# Deployment file helpers
# ─────────────────────────────────────────────

def save_deployment_file(hostname: str, vmid: int, node_name: str,
                         template_volid: str, template_name: str,
                         cpus_str: str, memory_gb_str: str, disk_gb_str: str,
                         storage: str, vlan_str: str, bridge: str,
                         password: str, container_ip: str, prefix_len: str,
                         cfg: dict, package_profile: str = "",
                         extra_packages: list = (), ttl: str = "") -> None:
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname
    deployments_dir = Path(__file__).parent / "deployments" / "lxc"
    deployments_dir.mkdir(parents=True, exist_ok=True)
    deploy_file = deployments_dir / f"{hostname}.json"
    data = {
        "hostname": hostname,
        "fqdn": fqdn,
        "node": node_name,
        "vmid": vmid,
        "template_volid": template_volid,
        "template_name": template_name,
        "cpus": int(cpus_str),
        "memory_gb": float(memory_gb_str),
        "disk_gb": int(disk_gb_str),
        "storage": storage,
        "vlan": int(vlan_str),
        "bridge": bridge,
        "password": password,
        "ip_address": container_ip,
        "assigned_ip": container_ip,
        "prefix_len": prefix_len,
        "package_profile": package_profile,
        "extra_packages": list(extra_packages),
        "deployed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if ttl:
        data["ttl"] = ttl
        data["expires_at"] = expires_at_from_ttl(ttl)
    with open(deploy_file, "w") as f:
        json.dump(data, f, indent=2)
    console.print(f"  [dim]Deployment file saved: {deploy_file}[/dim]")



def check_node_resources(proxmox: ProxmoxAPI, node_name: str,
                          memory_mb: int, disk_gb: int, storage: str,
                          cpu_threshold: float = 0.85,
                          ram_threshold: float = 0.95) -> tuple[bool, str]:
    """Re-verify a node still has sufficient resources before creating the container."""
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
        # Check storage space
        for s in proxmox.nodes(node_name).storage.get(enabled=1):
            if s["storage"] == storage:
                # lvmthin pools report avail=0 (thin-provisioned — no hard free space limit)
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
# Main wizard
# ─────────────────────────────────────────────

def main() -> None:
    # ── Parse CLI arguments ──
    parser = argparse.ArgumentParser(
        prog="deploy_lxc.py",
        description="Proxmox LXC Deploy Wizard — interactive provisioning tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 deploy_lxc.py
              python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json
              python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
              python3 deploy_lxc.py --validate
              python3 deploy_lxc.py --validate --deploy-file deployments/lxc/myserver.json
              python3 deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json
        """),
    )
    parser.add_argument(
        "--deploy-file", metavar="FILE",
        help="YAML deployment file to pre-fill defaults (saved from a previous run)",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Non-interactive mode: use all values from --deploy-file without prompting",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate config.yaml and deployment file without connecting to Proxmox or deploying",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate config + deployment file and print what would happen without making any changes",
    )
    parser.add_argument(
        "--preflight", action="store_true",
        help="Run preflight connectivity and dependency checks then exit",
    )
    parser.add_argument(
        "--yolo", action="store_true",
        help="Skip preflight checks and deploy immediately",
    )
    parser.add_argument(
        "--ttl", metavar="TTL",
        help="Time-to-live for this deployment (e.g. 7d, 24h, 2w, 30m). "
             "Stores 'expires_at' in the deployment JSON for use with expire.py.",
    )
    args = parser.parse_args()

    # Validate --ttl early so we fail fast before any Proxmox work
    ttl = None
    if args.ttl:
        try:
            parse_ttl(args.ttl)
            ttl = args.ttl
        except ValueError as e:
            console.print(f"[red]ERROR: {e}[/red]")
            sys.exit(1)

    if args.validate:
        run_validate(args)  # exits 0 or 1

    if args.dry_run:
        run_dry_run(args)  # exits 0 or 1

    if args.preflight:
        cfg = load_config()
        deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}
        run_preflight(cfg, kind="lxc", silent=args.silent, verbose=True,
                      deploy=deploy if args.deploy_file else None, yolo=args.yolo)
        sys.exit(0)

    if args.silent and not args.deploy_file:
        parser.error("--silent requires --deploy-file")

    cfg = load_config()
    defaults = cfg["defaults"]
    addusername = defaults.get("addusername", "admin")
    cpu_threshold = float(defaults.get("cpu_threshold", 0.85))
    ram_threshold = float(defaults.get("ram_threshold", 0.95))
    unprivileged = 1 if defaults.get("unprivileged", True) else 0
    firewall_enabled = 1 if defaults.get("firewall_enabled", False) else 0

    # Load deployment file if given (provides defaults or silent values)
    deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}
    silent = args.silent

    console.print()
    console.print(Panel.fit(
        Text("Proxmox LXC Deploy Wizard\n", style="bold cyan", justify="center") +
        Text("github.com: Jerry-Lees/HomeLab/labinator", style="dim cyan", justify="center"),
        border_style="cyan",
    ))
    console.print()

    if deploy and not silent:
        console.print(f"[dim]Loaded deployment file: {args.deploy_file}[/dim]\n")
    elif deploy and silent:
        console.print(f"[dim]Silent mode — deploying from: {args.deploy_file}[/dim]\n")

    # Pre-flight checks
    if not deploy.get("preflight", True):
        console.print("[yellow]⚡ preflight: false in deploy file — checks skipped.[/yellow]")
    else:
        run_preflight(cfg, kind="lxc", silent=silent, verbose=True,
                      deploy=deploy if args.deploy_file else None, yolo=args.yolo)

    # ── Connect to Proxmox ──
    with console.status("[bold green]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
            nodes = get_nodes_with_load(proxmox, storage_content="rootdir")
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    if not nodes:
        console.print("[red]No online nodes found in the cluster.[/red]")
        sys.exit(1)

    console.print(f"[green]✓ Connected.[/green] {len(nodes)} node(s) online.\n")

    # ═══════════════════════════════════════════
    # Interactive prompts — resources first, then node selection
    # ═══════════════════════════════════════════

    # Hostname
    hostname = q(
        questionary.text,
        "Hostname for the new container:",
        instruction="(short name only — domain suffix from config will be appended in inventory)",
        validate=lambda v: True if v.strip() else "Hostname cannot be empty",
        d=deploy, key="hostname", silent=silent,
    ).strip().lower()

    # CPU / Memory / Disk — ask before node selection so we can filter by resources
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

    # ── Package profile + extra packages ──
    package_profile, profile_packages, profile_tags = prompt_package_profile(cfg, deploy, silent)
    extra_packages = prompt_extra_packages(deploy, silent)

    # ── Node selection ──
    memory_mb = int(float(memory_gb_str) * 1024)
    node_name = prompt_node_selection(nodes, deploy, silent, memory_mb, memory_gb_str,
                                      cpu_threshold, ram_threshold)

    # ── Templates ──
    with console.status(f"[bold green]Fetching templates from {node_name}..."):
        templates = get_templates(proxmox, node_name)

    if not templates:
        console.print(f"[red]No LXC templates found on {node_name}.[/red]")
        console.print("Download templates in Proxmox: local storage > CT Templates > Templates")
        sys.exit(1)

    if silent:
        template_volid = str(deploy.get("template_volid", ""))
        if not template_volid:
            template_volid = templates[0]["volid"]
        elif not any(t["volid"] == template_volid for t in templates):
            console.print(
                f"[yellow]Warning: Template '{template_volid}' not found on {node_name}. "
                f"Using first available.[/yellow]"
            )
            template_volid = templates[0]["volid"]
        template_name = template_volid.split("/")[-1]
        console.print(f"  [dim]Template (from deployment file): {template_name}[/dim]")
    else:
        template_choices = [
            questionary.Choice(title=f"[{t['storage']}] {t['name']}", value=t["volid"])
            for t in templates
        ]
        deploy_volid = str(deploy.get("template_volid", ""))
        default_tmpl_name = defaults.get("template", "")
        default_volid = (
            deploy_volid if deploy_volid and any(t["volid"] == deploy_volid for t in templates)
            else next(
                (t["volid"] for t in templates if t["name"] == default_tmpl_name),
                templates[0]["volid"],
            )
        )
        template_volid = questionary.select(
            "Select OS template (Ubuntu templates listed first):",
            choices=template_choices,
            default=default_volid,
        ).ask()
        if template_volid is None:
            sys.exit(0)
        template_name = template_volid.split("/")[-1]

    # ── Storage pool ──
    with console.status(f"[bold green]Querying storage pools on {node_name}..."):
        storage_pools = get_disk_storages(proxmox, node_name)

    if silent:
        storage = str(deploy.get("storage", storage_pools[0] if storage_pools else "local-lvm"))
        console.print(f"  [dim]Storage (from deployment file): {storage}[/dim]")
    elif len(storage_pools) > 1:
        deploy_storage = str(deploy.get("storage", ""))
        default_storage = deploy_storage if deploy_storage in storage_pools else storage_pools[0]
        storage = questionary.select(
            "Select storage pool for container root disk:",
            choices=storage_pools,
            default=default_storage,
        ).ask()
        if storage is None:
            sys.exit(0)
    else:
        storage = storage_pools[0] if storage_pools else "local-lvm"
        console.print(f"  [dim]Storage pool: {storage}[/dim]")

    # ═══════════════════════════════════════════
    # Summary & confirmation
    # ═══════════════════════════════════════════
    next_vmid = get_next_vmid(proxmox)
    bridge = defaults.get("bridge", "vmbr0")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print()
    table = Table(title="Deployment Summary", show_header=False, border_style="cyan", padding=(0, 2))
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_row("VMID",        str(next_vmid))
    table.add_row("Hostname",    hostname)
    table.add_row("Node",        node_name)
    table.add_row("Template",    template_name)
    table.add_row("vCPUs",       cpus_str)
    table.add_row("Memory",      f"{memory_gb_str} GB ({memory_mb} MB)")
    table.add_row("Disk",        f"{disk_gb_str} GB  →  {storage}")
    table.add_row("Network",     f"{bridge}.{vlan_str}  (DHCP)")
    tags_display = ";".join(["auto-deploy"] + profile_tags) if profile_tags else "auto-deploy"
    table.add_row("Tags",        tags_display)
    if ttl:
        table.add_row("TTL / Expires", f"{ttl}  (expires {expires_at_from_ttl(ttl)[:19]} UTC)")
    table.add_row("Users",       f"root, {addusername} (same password)")
    table.add_row("Timezone",    cfg.get("timezone", "UTC"))
    table.add_row("NTP",         ", ".join(cfg.get("ntp", {}).get("servers", ["pool.ntp.org"])))
    table.add_row("SNMP",        f"community='{cfg['snmp']['community']}' (rw) on :161")
    console.print(table)
    console.print()

    if not silent:
        confirm = questionary.confirm("Proceed with deployment?", default=True).ask()
        if not confirm:
            console.print("[yellow]Deployment cancelled.[/yellow]")
            sys.exit(0)

    # ── Pre-creation resource re-check ──
    console.print("[dim]Pre-creation resource check...[/dim]")
    ok, reason = check_node_resources(proxmox, node_name, memory_mb, int(disk_gb_str), storage, cpu_threshold, ram_threshold)
    if not ok:
        console.print(f"[red]✗ Resource check failed: {reason}[/red]")
        console.print("[red]Deployment aborted. Resources may have changed since node selection.[/red]")
        sys.exit(1)
    console.print("[green]✓ Resources verified[/green]")

    # ═══════════════════════════════════════════
    # Create the container
    # ═══════════════════════════════════════════
    console.print()
    console.print("[bold cyan]─── Step 1/7: Creating LXC container ───[/bold cyan]")

    container_note = textwrap.dedent(f"""\
        Auto-deployed by deploy_lxc.py
        ─────────────────────────────────────
        Created    : {now_str}
        Node       : {node_name}
        Template   : {template_name}
        vCPUs      : {cpus_str}
        Memory     : {memory_gb_str} GB
        Disk       : {disk_gb_str} GB ({storage})
        VLAN       : {vlan_str}
        Network    : {bridge}.{vlan_str} / DHCP
        Timezone   : {cfg.get('timezone', 'UTC')}
        NTP        : {', '.join(cfg.get('ntp', {}).get('servers', ['pool.ntp.org']))}
        SNMP       : community={cfg.get('snmp', {}).get('community', 'your-snmp-community')} (rw)
        ─────────────────────────────────────
        Users: root, {addusername} (same password)
    """)

    create_params = {
        "vmid":         next_vmid,
        "hostname":     hostname,
        "ostemplate":   template_volid,
        "cores":        int(cpus_str),
        "memory":       memory_mb,
        "swap":         defaults.get("swap_mb", 512),
        "rootfs":       f"{storage}:{disk_gb_str}",
        "net0":         f"name=eth0,bridge={bridge},tag={vlan_str},ip=dhcp,firewall={firewall_enabled}",
        "password":     password,
        "unprivileged": unprivileged,
        "onboot":       1 if defaults.get("onboot", True) else 0,
        "start":        0,   # We start it explicitly after tagging
        "features":     "nesting=1",
        "nameserver":   defaults.get("nameserver", "8.8.8.8 8.8.4.4"),
        "searchdomain": defaults.get("searchdomain", ""),
        "description":  container_note,
        "tags":         ";".join(["auto-deploy"] + profile_tags),
    }

    for _vmid_attempt in range(3):
        create_params["vmid"] = next_vmid
        try:
            with console.status(f"[bold green]Creating container {next_vmid} ({hostname}) on {node_name}..."):
                task = proxmox.nodes(node_name).lxc.post(**create_params)
                wait_for_task(proxmox, node_name, task, timeout=180)
            console.print(f"[green]✓ Container {next_vmid} created[/green]")
            break
        except Exception as e:
            if "already exists" in str(e) and _vmid_attempt < 2:
                old_vmid = next_vmid
                next_vmid = get_next_vmid(proxmox)
                console.print(
                    f"[yellow]⚠ VMID {old_vmid} already in use (race condition) — "
                    f"retrying with VMID {next_vmid}[/yellow]"
                )
            else:
                console.print(f"[red]✗ Container creation failed: {e}[/red]")
                sys.exit(1)

    # ═══════════════════════════════════════════
    # Start the container
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 2/7: Starting container ───[/bold cyan]")
    try:
        with console.status("[bold green]Starting container..."):
            task = proxmox.nodes(node_name).lxc(next_vmid).status.start.post()
            wait_for_task(proxmox, node_name, task, timeout=60)
        console.print("[green]✓ Container started[/green]")
    except Exception as e:
        console.print(f"[red]✗ Failed to start container: {e}[/red]")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Wait for DHCP IP
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 3/7: Waiting for DHCP IP address ───[/bold cyan]")
    try:
        with console.status("[bold green]Polling for DHCP lease (up to 2 min)..."):
            container_ip, prefix_len = wait_for_ip(proxmox, node_name, next_vmid, timeout=120)
        console.print(f"[green]✓ Container IP: [bold]{container_ip}[/bold] /{prefix_len}[/green]")
    except TimeoutError as e:
        console.print(f"[red]✗ {e}[/red]")
        console.print(f"  You can still SSH to the Proxmox node and run: pct exec {next_vmid} -- ip addr")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Bootstrap SSH via pct exec
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 4/7: Bootstrapping SSH in container ───[/bold cyan]")
    try:
        bootstrap_container(
            cfg, node_name, next_vmid, password,
            container_ip=container_ip, prefix_len=prefix_len,
            nameserver=defaults.get("nameserver", "8.8.8.8 8.8.4.4"),
            searchdomain=defaults.get("searchdomain", "local"),
        )
    except Exception as e:
        console.print(f"[red]✗ Bootstrap failed: {e}[/red]")
        sys.exit(1)

    # Wait for SSH to be ready before handing off to Ansible
    console.print("  [dim]Waiting for SSH to become reachable...[/dim]")
    try:
        wait_for_ssh(container_ip, timeout=60)
        console.print("  [green]✓ SSH is ready[/green]")
    except TimeoutError as e:
        console.print(f"[red]✗ {e}[/red]")
        sys.exit(1)

    # ═══════════════════════════════════════════
    # Ansible post-deploy
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 5/7: Running post-deployment configuration (Ansible) ───[/bold cyan]")
    if cfg.get("ansible", {}).get("enabled", True):
        post_deploy_password = password
        for attempt in range(2):
            try:
                run_ansible_post_deploy(
                    container_ip, post_deploy_password, hostname,
                    nameserver=defaults.get("nameserver", "8.8.8.8 8.8.4.4"),
                    searchdomain=defaults.get("searchdomain", ""),
                    cfg=cfg,
                    profile_packages=profile_packages,
                    extra_packages=extra_packages,
                )
                console.print("[green]✓ Post-deployment configuration complete[/green]")
                break
            except Exception as e:
                console.print(f"[red]✗ Post-deploy failed: {e}[/red]")
                if attempt == 0 and not silent:
                    console.print("[yellow]This may be a password mismatch. "
                                  "Enter the container's current root password to retry.[/yellow]")
                    retry_pw = questionary.password("Container root password:").ask()
                    if retry_pw:
                        post_deploy_password = retry_pw
                        continue
                sys.exit(1)
    else:
        console.print("  [dim]Skipped (ansible.enabled: false) — configure host manually[/dim]")

    # ═══════════════════════════════════════════
    # Register DNS
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 6/7: Registering DNS records ───[/bold cyan]")
    dns_action = dns_precheck(cfg, hostname, container_ip, silent=silent)
    if dns_action == "abort":
        sys.exit(1)
    elif dns_action == "proceed":
        run_ansible_add_dns(cfg, hostname, container_ip)
    else:
        console.print("  [dim]DNS registration skipped — existing record kept.[/dim]")

    # ═══════════════════════════════════════════
    # Update Ansible inventory
    # ═══════════════════════════════════════════
    console.print("[bold cyan]─── Step 7/7: Updating Ansible inventory ───[/bold cyan]")
    run_ansible_inventory_update(cfg, hostname, container_ip, password)

    # ── Save deployment file ──
    save_deployment_file(
        hostname, next_vmid, node_name, template_volid, template_name,
        cpus_str, memory_gb_str, disk_gb_str, storage, vlan_str, bridge,
        password, container_ip, prefix_len, cfg,
        package_profile=package_profile,
        extra_packages=extra_packages,
        ttl=ttl or "",
    )

    # Health check (optional — runs if health_check.enabled in config)
    health_check(container_ip, password, addusername, cfg)

    # ═══════════════════════════════════════════
    # Done!
    # ═══════════════════════════════════════════
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname
    console.print()
    console.print(Panel(
        textwrap.dedent(f"""\
            [bold green]Deployment Complete![/bold green]

            [bold]Hostname   :[/bold]  {hostname}
            [bold]FQDN       :[/bold]  {fqdn}
            [bold]IP Address :[/bold]  {container_ip}
            [bold]VMID       :[/bold]  {next_vmid}  (on {node_name})
            [bold]SSH        :[/bold]  ssh root@{container_ip}
                      ssh {addusername}@{container_ip}

            [dim]Deployment file: deployments/lxc/{hostname}.json[/dim]
            [dim]Tagged 'auto-deploy' with specs note in Proxmox.[/dim]
            [dim]DNS: A + PTR records registered on {cfg.get('dns', {}).get('server', 'N/A')}.[/dim]
            [dim]Added to Ansible inventory group [{cfg['ansible_inventory']['group']}].[/dim]
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
