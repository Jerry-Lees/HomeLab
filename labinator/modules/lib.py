"""
labinator shared library
========================
Shared helper functions used across deploy_vm.py, deploy_lxc.py,
decomm_vm.py, and decomm_lxc.py.

All path resolution is relative to _ROOT (the labinator project root),
which is the parent of this file's containing directory (modules/).
"""

import ipaddress
import json
import os
import random
import socket
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path

import paramiko
import yaml
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

SKULL = "[bold red]☠[/bold red]"

# Root of the labinator project (parent of this modules/ directory)
_ROOT = Path(__file__).parent.parent


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

def load_config() -> dict:
    config_path = _ROOT / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]ERROR: config.yaml not found at {config_path}[/red]")
        console.print("Copy config.yaml.example to config.yaml and fill in your credentials.")
        sys.exit(1)
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    if cfg["proxmox"]["token_secret"] == "CHANGEME-PASTE-YOUR-TOKEN-SECRET-HERE":
        console.print("[red]ERROR: Edit config.yaml and set proxmox.token_secret[/red]")
        sys.exit(1)
    return cfg


# ─────────────────────────────────────────────
# Proxmox connection
# ─────────────────────────────────────────────

def connect_proxmox(cfg: dict) -> ProxmoxAPI:
    """Connect to Proxmox API, trying each host in the hosts list until one succeeds."""
    pve = cfg["proxmox"]
    hosts = pve.get("hosts") or [pve["host"]]
    last_err: Exception | None = None
    for host in hosts:
        try:
            api = ProxmoxAPI(
                host,
                user=pve["user"],
                token_name=pve["token_name"],
                token_value=pve["token_secret"],
                verify_ssl=pve.get("verify_ssl", False),
            )
            api.nodes.get()  # verify connectivity
            return api
        except Exception as e:
            last_err = e
    raise last_err


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


# ─────────────────────────────────────────────
# Decommission: DNS + inventory removal
# ─────────────────────────────────────────────

def remove_dns(cfg: dict, deploy: dict) -> None:
    """Remove A and PTR DNS records via Ansible (skipped if dns.enabled is false)."""
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        console.print("  [dim]DNS removal skipped (disabled in config)[/dim]")
        return

    ansible_dir = _ROOT / "ansible"
    hostname = deploy["hostname"]
    ip_address = deploy.get("assigned_ip") or deploy.get("ip_address", "")

    ip_parts = ip_address.split(".")
    if not ip_address or len(ip_parts) != 4:
        console.print("  [yellow]Warning: No IP address found in deployment file — skipping DNS removal.[/yellow]")
        return
    reverse_zone = f"{ip_parts[2]}.{ip_parts[1]}.{ip_parts[0]}.in-addr.arpa"
    zone_dir = str(Path(dns["forward_zone_file"]).parent)
    reverse_zone_file = f"{zone_dir}/{reverse_zone}.hosts"

    cmd = [
        "ansible-playbook",
        "-i", f"{dns['server']},",
        str(ansible_dir / "remove-dns.yml"),
        "-e", f"hostname={hostname}",
        "-e", f"ip_address={ip_address}",
        "-e", f"forward_zone_file={dns['forward_zone_file']}",
        "-e", f"reverse_zone_file={reverse_zone_file}",
        "-u", dns.get("ssh_user", "root"),
        "--timeout", "30",
    ]
    console.print(f"  [dim]Removing DNS records for {hostname} ({ip_address})...[/dim]")
    result = subprocess.run(cmd, cwd=str(ansible_dir))
    if result.returncode != 0:
        console.print(f"  [yellow]Warning: DNS removal failed. Remove manually: {hostname} A/PTR records.[/yellow]")
    else:
        console.print(f"  [green]✓ DNS records removed[/green]")


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


# ─────────────────────────────────────────────
# Decommission: scary confirmation challenge
# ─────────────────────────────────────────────

def random_caps(word: str) -> str:
    """Return word with randomly mixed case — guaranteed at least one upper and one lower."""
    chars = [c.upper() if random.random() > 0.5 else c.lower() for c in word]
    if not any(c.isupper() for c in chars):
        idx = random.randrange(len(chars))
        chars[idx] = chars[idx].upper()
    if not any(c.islower() for c in chars):
        idx = random.randrange(len(chars))
        chars[idx] = chars[idx].lower()
    return "".join(chars)


def flush_stdin() -> None:
    """Discard any buffered keystrokes so accidental presses don't auto-confirm."""
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdin.flush()
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        pass  # Not a TTY — skip (e.g. piped input)


def confirm_destruction(deploy: dict, kind: str = "VM") -> bool:
    """Display scary warning and require typed confirmation.

    kind: human-readable resource type shown in the warning panel ("VM" or "container").
    """
    hostname = deploy["hostname"]
    vmid = deploy.get("vmid", "???")
    node = deploy.get("node", "???")
    ip = deploy.get("ip_address", "???")

    challenge = random_caps("yes")

    console.print()
    console.print(Panel(
        Text.from_markup(
            f"{SKULL}  [bold red blink]WARNING: IRREVERSIBLE DESTRUCTION[/bold red blink]  {SKULL}\n\n"
            f"You are about to [bold red]PERMANENTLY DELETE[/bold red]:\n\n"
            f"  [bold]Hostname :[/bold] {hostname}\n"
            f"  [bold]VMID     :[/bold] {vmid}  (on {node})\n"
            f"  [bold]IP       :[/bold] {ip}\n\n"
            f"This will [bold red]STOP and DESTROY[/bold red] the {kind},\n"
            f"[bold red]REMOVE[/bold red] its DNS records, and\n"
            f"[bold red]DELETE[/bold red] it from the Ansible inventory.\n\n"
            f"[bold yellow]There is NO undo.[/bold yellow]"
        ),
        border_style="bold red",
        title=f"[bold red]{SKULL}  DECOMMISSION WIZARD  {SKULL}[/bold red]",
        padding=(1, 2),
    ))
    console.print()

    console.print("[yellow]Flushing keyboard buffer — please wait 5 seconds...[/yellow]")
    flush_stdin()
    for i in range(5, 0, -1):
        console.print(f"  [dim]{i}...[/dim]", end="\r")
        time.sleep(1)
    console.print()
    flush_stdin()  # discard any keystrokes made during the countdown

    console.print(
        f"[bold]To confirm destruction of [red]{hostname}[/red], "
        f"type exactly:[/bold] [bold yellow]{challenge}[/bold yellow]"
    )
    console.print("[dim](case-sensitive)[/dim]")
    console.print()

    try:
        answer = input("Type here: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Aborted.[/yellow]")
        return False

    if answer != challenge:
        console.print(f"\n[red]✗ Expected '{challenge}', got '{answer}'. Aborted.[/red]")
        return False

    console.print("\n[bold red]Confirmed. Proceeding with decommission...[/bold red]")
    console.print()
    return True


# ─────────────────────────────────────────────
# Deploy: validation
# ─────────────────────────────────────────────

def _check_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def validate_config(cfg_path: Path) -> list[str]:
    """Return a list of error strings; empty means config is valid."""
    errors = []
    if not cfg_path.exists():
        return [f"config.yaml not found at {cfg_path}"]
    try:
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return [f"config.yaml is not valid YAML: {e}"]
    if not isinstance(cfg, dict):
        return ["config.yaml is empty or not a YAML mapping"]

    px = cfg.get("proxmox") or {}
    if not px.get("host") and not px.get("hosts"):
        errors.append("proxmox.host (or proxmox.hosts list) is required")
    if not px.get("user"):
        errors.append("proxmox.user is required")
    if not px.get("token_name"):
        errors.append("proxmox.token_name is required")
    secret = str(px.get("token_secret", ""))
    if not secret:
        errors.append("proxmox.token_secret is required")
    elif "CHANGEME" in secret:
        errors.append("proxmox.token_secret still contains a placeholder value")

    defaults = cfg.get("defaults") or {}
    if not defaults.get("addusername"):
        errors.append("defaults.addusername is required")

    snmp = cfg.get("snmp") or {}
    if not snmp.get("community"):
        errors.append("snmp.community is required")

    ntp = cfg.get("ntp") or {}
    servers = ntp.get("servers")
    if not servers or not isinstance(servers, list):
        errors.append("ntp.servers must be a non-empty list")

    if not cfg.get("timezone"):
        errors.append("timezone is required")

    return errors


# ─────────────────────────────────────────────
# Deploy: package profiles
# ─────────────────────────────────────────────

def resolve_profile(profile_name: str, profiles: dict) -> tuple[list, list]:
    """Return (packages, tags) for a named profile.

    Supports both flat-list format (packages only, no tags) and
    dict format with 'packages' and optional 'tags' keys.
    """
    profile = profiles.get(profile_name)
    if not profile:
        return [], []
    if isinstance(profile, list):
        return list(profile), []
    return list(profile.get("packages", [])), list(profile.get("tags", []))


# ─────────────────────────────────────────────
# Deploy: health check
# ─────────────────────────────────────────────

def health_check(ip: str, password: str, addusername: str, cfg: dict) -> bool:
    """
    Verify the host is healthy after deployment.
    Checks TCP port 22, then SSHes in and runs hostname.
    Returns True if healthy, False if not. Never raises.
    Skipped silently if health_check.enabled is false/absent in config.
    """
    hc = (cfg or {}).get("health_check", {})
    if not hc.get("enabled", False):
        return True

    timeout = int(hc.get("timeout_seconds", 30))
    retries = int(hc.get("retries", 5))

    console.print()
    console.print("[bold]─── Health Check ───[/bold]")

    # ── TCP port 22 ──
    alive = False
    for attempt in range(1, retries + 1):
        try:
            with socket.create_connection((ip, 22), timeout=timeout):
                alive = True
                break
        except OSError:
            console.print(f"[yellow]  Port 22 not yet open (attempt {attempt}/{retries}) — retrying in 5s...[/yellow]")
            time.sleep(5)

    if not alive:
        console.print(f"[yellow]⚠ Health check: port 22 unreachable on {ip} after {retries} attempts[/yellow]")
        console.print("[yellow]  Deployment may be OK — investigate if the host doesn't respond.[/yellow]")
        return False

    console.print(f"[green]✓ TCP port 22 open on {ip}[/green]")

    # ── SSH: run hostname ──
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, username=addusername, password=password,
                       timeout=timeout, allow_agent=False, look_for_keys=False)
        _, stdout, _ = client.exec_command("hostname")
        result = stdout.read().decode().strip()
        client.close()
        console.print(f"[green]✓ SSH OK — hostname: {result}[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ SSH check failed: {e}[/yellow]")
        return False

    return True


# ─────────────────────────────────────────────
# Deploy: Ansible runners (DNS + inventory)
# ─────────────────────────────────────────────

def run_ansible_add_dns(cfg: dict, hostname: str, ip: str) -> None:
    """Register A and PTR records on the BIND DNS server (skipped if dns.enabled is false)."""
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        console.print("  [dim]DNS registration skipped (disabled in config)[/dim]")
        return

    ansible_dir = _ROOT / "ansible"
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname

    ip_parts = ip.split(".")
    reverse_zone = f"{ip_parts[2]}.{ip_parts[1]}.{ip_parts[0]}.in-addr.arpa"
    zone_dir = str(Path(dns["forward_zone_file"]).parent)
    reverse_zone_file = f"{zone_dir}/{reverse_zone}.hosts"

    cmd = [
        "ansible-playbook",
        "-i", f"{dns['server']},",
        str(ansible_dir / "add-dns.yml"),
        "-e", f"new_hostname={hostname}",
        "-e", f"new_ip={ip}",
        "-e", f"new_fqdn={fqdn}",
        "-e", f"forward_zone_file={dns['forward_zone_file']}",
        "-e", f"reverse_zone_file={reverse_zone_file}",
        "-u", dns.get("ssh_user", "root"),
        "--timeout", "30",
    ]
    console.print(f"  [dim]Registering {fqdn} → {ip} on {dns['server']}...[/dim]")
    result = subprocess.run(cmd, cwd=str(ansible_dir))
    if result.returncode != 0:
        console.print(
            f"  [yellow]Warning: DNS registration failed. "
            f"Add manually: {fqdn} A {ip} and PTR.[/yellow]"
        )
    else:
        console.print(f"  [green]✓ DNS registered: {fqdn} → {ip} (+ PTR)[/green]")


def run_ansible_inventory_update(cfg: dict, hostname: str, ip: str, password: str) -> None:
    """Run the inventory-update playbook against the development server."""
    inv_cfg = cfg.get("ansible_inventory", {})
    if not inv_cfg:
        console.print("  [dim]Inventory update skipped (not configured)[/dim]")
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
