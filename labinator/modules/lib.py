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
import re
import socket
import subprocess
import sys
import termios
import time
import tty
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paramiko
import yaml
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
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
# History log
# ─────────────────────────────────────────────

def write_history(entry: dict) -> None:
    """Append a deployment event to deployments/history.log (one JSON object per line).
    Warns but never fails if the log is unwritable."""
    log_path = _ROOT / "deployments" / "history.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        console.print(f"  [dim yellow]Warning: could not write history log: {e}[/dim yellow]")


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
    ip = deploy.get("ip_address") or deploy.get("ip", "???")

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
# TTL / expiry helpers
# ─────────────────────────────────────────────

_TTL_RE = re.compile(r'^(\d+)(m|h|d|w)$')

def parse_ttl(ttl_str: str) -> timedelta:
    """Parse a TTL string (e.g. '7d', '24h', '2w', '30m') into a timedelta.
    Raises ValueError on invalid format."""
    m = _TTL_RE.match(ttl_str.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid TTL '{ttl_str}': use a number followed by m/h/d/w "
            "(e.g. 30m, 24h, 7d, 2w)"
        )
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n),
            "d": timedelta(days=n),   "w": timedelta(weeks=n)}[unit]


def expires_at_from_ttl(ttl_str: str) -> str:
    """Return an ISO 8601 UTC timestamp string for now + ttl_str."""
    return (datetime.now(timezone.utc) + parse_ttl(ttl_str)).isoformat()


# ─────────────────────────────────────────────
# Decommission pipeline (shared with cleanup_tagged + expire)
# ─────────────────────────────────────────────

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


def decomm_resource(proxmox: ProxmoxAPI, cfg: dict, resource: dict,
                    idx: int = 1, total: int = 1) -> str:
    """Full decommission: stop+destroy in Proxmox, remove DNS, remove from inventory.
    Returns 'decommissioned' if destroyed, 'already_gone' if not found in Proxmox."""
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


def process_action_list(resources: list, proxmox: ProxmoxAPI, cfg: dict,
                        skip_confirmation: bool = False) -> dict:
    """Execute keep/promote/decomm actions on a list of resource dicts.

    Each resource must have an 'action' key: 'keep' | 'promote' | 'decomm'.
    Returns a summary dict with keys: decommissioned, already_gone, promoted, kept, aborted.
    skip_confirmation=True skips the scary challenge (for --silent / automated modes).
    """
    decommissioned: list = []
    already_gone:   list = []
    promoted:       list = []
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
        "kept":           kept,
        "aborted":        aborted,
    }


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
    Tries agent/key auth first, falls back to password.
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
        client.connect(ip, username="root", timeout=timeout,
                       allow_agent=True, look_for_keys=True)
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

def dns_precheck(cfg: dict, hostname: str, ip: str, silent: bool = False) -> str:
    """Check whether a DNS A record already exists for hostname before registering.

    Queries the configured DNS server directly (not the system resolver) to
    avoid cache skew.

    Returns one of:
      "proceed" — no existing record, or user chose overwrite
      "skip"    — existing record is the same IP (idempotent), or user chose skip
      "abort"   — user chose abort
    """
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        return "proceed"

    dns_server = dns.get("server", "")
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname

    # ── Query forward record (A) ──
    try:
        result = subprocess.run(
            ["dig", f"@{dns_server}", fqdn, "A", "+short", "+time=3", "+tries=1"],
            capture_output=True, text=True, timeout=10,
        )
        existing_ips = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        console.print("  [dim]DNS pre-check skipped (dig not available or timed out)[/dim]")
        return "proceed"

    if not existing_ips:
        return "proceed"

    # ── Idempotent: exact match ──
    if existing_ips == [ip]:
        console.print(
            f"  [dim]DNS pre-check: {fqdn} already resolves to {ip} — record is current.[/dim]"
        )
        return "skip"

    # ── One or more records exist but differ ──
    count = len(existing_ips)
    if count == 1:
        console.print(
            f"\n  [yellow]DNS pre-check: {fqdn} already resolves to "
            f"[bold]{existing_ips[0]}[/bold] (new IP: [bold]{ip}[/bold])[/yellow]"
        )
    else:
        ips_list = ", ".join(existing_ips)
        console.print(
            f"\n  [yellow]DNS pre-check: {fqdn} has [bold]{count} existing A records[/bold]: "
            f"{ips_list}  (new IP: [bold]{ip}[/bold])[/yellow]"
        )

    # ── PTR check for the new IP ──
    try:
        ip_parts = ip.split(".")
        arpa = f"{ip_parts[3]}.{ip_parts[2]}.{ip_parts[1]}.{ip_parts[0]}.in-addr.arpa"
        ptr_result = subprocess.run(
            ["dig", f"@{dns_server}", arpa, "PTR", "+short", "+time=3", "+tries=1"],
            capture_output=True, text=True, timeout=10,
        )
        ptr_records = [ln.strip() for ln in ptr_result.stdout.splitlines() if ln.strip()]
        if ptr_records:
            ptr_str = ", ".join(ptr_records)
            console.print(
                f"  [yellow]PTR record for {ip} already points to: [bold]{ptr_str}[/bold][/yellow]"
            )
    except Exception:
        pass

    if silent:
        console.print("  [yellow]Silent mode: overwriting existing DNS record.[/yellow]")
        return "proceed"

    console.print()
    action = questionary.select(
        "Existing DNS record found. What would you like to do?",
        choices=[
            questionary.Choice(title="Overwrite — replace the existing record(s)", value="proceed"),
            questionary.Choice(title="Skip     — leave DNS as-is and continue deployment", value="skip"),
            questionary.Choice(title="Abort    — stop deployment now", value="abort"),
        ],
    ).ask()

    if action is None or action == "abort":
        console.print("[red]Aborted.[/red]")
        return "abort"

    return action


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


# ─────────────────────────────────────────────
# Deploy: Proxmox helpers
# ─────────────────────────────────────────────

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


def bytes_to_gb(b: int) -> str:
    return f"{b / (1024 ** 3):.1f}"


def get_next_vmid(proxmox: ProxmoxAPI) -> int:
    return int(proxmox.cluster.nextid.get())


def wait_for_ssh(host: str, timeout: int = 300) -> None:
    """Poll until SSH port 22 accepts TCP connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, 22), timeout=5):
                return
        except (socket.timeout, ConnectionRefusedError, OSError):
            time.sleep(5)
    raise TimeoutError(f"SSH on {host}:22 did not become reachable within {timeout}s")


def node_ssh_host(cfg: dict, node_name: str) -> str:
    """Construct the SSH hostname for a Proxmox node."""
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


def check_ansible() -> None:
    """Ensure ansible-playbook is available."""
    result = subprocess.run(["which", "ansible-playbook"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("ansible-playbook not found. Install Ansible: apt install ansible")


def check_sshpass() -> None:
    """Ensure sshpass is available for password-based Ansible connections (LXC only)."""
    result = subprocess.run(["which", "sshpass"], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("sshpass not found. Install it: apt install sshpass")


# ─────────────────────────────────────────────
# Deploy: Preflight checks
# ─────────────────────────────────────────────

class _PF:
    """Single preflight check result."""
    __slots__ = ("name", "passed", "msg", "fatal")

    def __init__(self, name: str, passed: bool, msg: str, fatal: bool = True):
        self.name = name
        self.passed = passed
        self.msg = msg
        self.fatal = fatal


def _pf_config_valid(cfg_path: Path) -> _PF:
    errs = validate_config(cfg_path)
    if errs:
        return _PF("Config valid", False, "; ".join(errs))
    return _PF("Config valid", True, str(cfg_path))


def _pf_proxmox_reachable(cfg: dict) -> _PF:
    pve = cfg["proxmox"]
    hosts = pve.get("hosts") or [pve.get("host", "")]
    ok, failed, errors = [], [], []
    for host in hosts:
        try:
            with socket.create_connection((host, 8006), timeout=5):
                pass
            ok.append(host)
        except OSError as e:
            failed.append(host)
            errors.append(f"{host} ({e})")
    if not ok:
        return _PF("Proxmox API reachable", False, "Cannot reach any host: " + "; ".join(errors))
    if failed:
        fail_str = "  (" + ", ".join(f"✗ {h}" for h in failed) + ")"
        return _PF("Proxmox API reachable", True,
                   f"{len(ok)}/{len(hosts)} host(s) on :8006{fail_str}", fatal=False)
    return _PF("Proxmox API reachable", True, f"{len(ok)}/{len(hosts)} host(s) on :8006")


def _pf_proxmox_auth(cfg: dict) -> _PF:
    try:
        connect_proxmox(cfg)
        return _PF("Proxmox API auth", True, "Token accepted")
    except Exception as e:
        return _PF("Proxmox API auth", False, str(e))


def _pf_ssh_key_exists(cfg: dict) -> _PF:
    key = cfg["proxmox"].get("ssh_key", "")
    if not key:
        return _PF("SSH key on disk", True, "proxmox.ssh_key not set — skipped", fatal=False)
    expanded = os.path.expanduser(key)
    if os.path.exists(expanded):
        return _PF("SSH key on disk", True, expanded, fatal=False)
    return _PF("SSH key on disk", False, f"Not found: {expanded}", fatal=False)


def _pf_proxmox_ssh(cfg: dict) -> _PF:
    pve = cfg["proxmox"]
    key = pve.get("ssh_key", "")
    if not key:
        return _PF("Proxmox node SSH", True, "proxmox.ssh_key not set — skipped", fatal=False)
    expanded = os.path.expanduser(key)
    if not os.path.exists(expanded):
        return _PF("Proxmox node SSH", False, f"SSH key not found: {expanded}", fatal=False)
    domain = pve.get("node_domain", "")
    nodes_cfg = cfg.get("nodes", [])
    if not nodes_cfg:
        return _PF("Proxmox node SSH", True, "No nodes in config — skipped", fatal=False)
    failed = []
    for node in nodes_cfg:
        host = f"{node}.{domain}" if domain else node
        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=5", "-i", expanded, f"root@{host}", "echo", "OK"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0 or "OK" not in result.stdout:
                failed.append(host)
        except Exception:
            failed.append(host)
    if failed:
        fail_str = "  (" + ", ".join(f"✗ {h}" for h in failed) + ")"
        return _PF("Proxmox node SSH", True,
                   f"{len(nodes_cfg) - len(failed)}/{len(nodes_cfg)} node(s) SSH OK{fail_str}",
                   fatal=False)
    return _PF("Proxmox node SSH", True, f"{len(nodes_cfg)}/{len(nodes_cfg)} node(s) SSH OK", fatal=False)


def _pf_ansible_installed(cfg: dict) -> _PF:
    if not cfg.get("ansible", {}).get("enabled", True):
        return _PF("Ansible installed", True, "ansible.enabled: false — skipped")
    result = subprocess.run(["which", "ansible-playbook"], capture_output=True, text=True)
    if result.returncode != 0:
        return _PF("Ansible installed", False, "ansible-playbook not found — apt install ansible")
    return _PF("Ansible installed", True, result.stdout.strip())


def _pf_sshpass_installed() -> _PF:
    result = subprocess.run(["which", "sshpass"], capture_output=True, text=True)
    if result.returncode != 0:
        return _PF("sshpass installed (LXC)", False, "not found — apt install sshpass")
    return _PF("sshpass installed (LXC)", True, result.stdout.strip())


def _pf_dns_hostname(cfg: dict, deploy: dict) -> _PF:
    """Check whether the hostname from the deploy file already resolves in DNS."""
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        return _PF("DNS hostname check", True, "dns.enabled: false — skipped", fatal=False)
    hostname = deploy.get("hostname", "")
    if not hostname:
        return _PF("DNS hostname check", True, "No hostname in deploy file — skipped", fatal=False)
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname
    dns_server = dns.get("server", "")
    try:
        result = subprocess.run(
            ["dig", f"@{dns_server}", fqdn, "A", "+short", "+time=3", "+tries=1"],
            capture_output=True, text=True, timeout=10,
        )
        existing_ips = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return _PF("DNS hostname check", True, "dig not available — skipped", fatal=False)
    if not existing_ips:
        return _PF("DNS hostname check", True, f"{fqdn} — no existing record", fatal=False)
    known_ip = deploy.get("assigned_ip") or deploy.get("ip_address", "")
    count = len(existing_ips)
    if count == 1:
        msg = f"{fqdn} already resolves to {existing_ips[0]}"
        if known_ip and existing_ips[0] == known_ip:
            msg += "  (matches deploy file — existing host may be orphaned if not decommissioned first)"
        elif known_ip:
            msg += f"  (deploy file IP: {known_ip})"
    else:
        msg = f"{fqdn} has {count} existing records: {', '.join(existing_ips)}"
        if known_ip:
            msg += f"  (deploy file IP: {known_ip})"
    msg += "  — decomm first or this host will be orphaned"
    return _PF("DNS hostname check", False, msg, fatal=False)


def _pf_ip_in_use(deploy: dict) -> _PF:
    """Ping the static IP from the deploy file — fail if it's already in use."""
    ip = deploy.get("ip_address", "").strip()
    if not ip:
        return _PF("Static IP in use", True, "No ip_address in deploy file (DHCP) — skipped", fatal=False)
    try:
        result = subprocess.run(
            ["ping", "-c", "2", "-W", "2", ip],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return _PF("Static IP in use", False,
                       f"{ip} is already responding to ping — decomm the existing host first, "
                       f"or remove ip_address from the deploy file to use DHCP")
        return _PF("Static IP in use", True, f"{ip} — no response (safe to use)", fatal=False)
    except Exception as e:
        return _PF("Static IP in use", True, f"ping check failed: {e} — skipped", fatal=False)


def _pf_dns_reachable(cfg: dict) -> _PF:
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        return _PF("DNS server reachable", True, "dns.enabled: false — skipped", fatal=False)
    server = dns.get("server", "")
    try:
        with socket.create_connection((server, 22), timeout=5):
            pass
        return _PF("DNS server reachable", True, f"{server}:22 OK", fatal=False)
    except OSError as e:
        return _PF("DNS server reachable", False, f"{server}: {e}", fatal=False)


def _pf_dns_ssh_auth(cfg: dict) -> _PF:
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        return _PF("DNS server SSH auth", True, "dns.enabled: false — skipped", fatal=False)
    server = dns.get("server", "")
    user = dns.get("ssh_user", "root")
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=5", f"{user}@{server}", "echo", "OK"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and "OK" in result.stdout:
            return _PF("DNS server SSH auth", True, f"{user}@{server} OK", fatal=False)
        return _PF("DNS server SSH auth", False, f"Auth failed: {user}@{server}", fatal=False)
    except Exception as e:
        return _PF("DNS server SSH auth", False, str(e), fatal=False)


def _pf_inventory_reachable(cfg: dict) -> _PF:
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


def _pf_inventory_ssh_auth(cfg: dict) -> _PF:
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


def run_preflight(cfg: dict, kind: str, silent: bool = False, verbose: bool = False,
                  deploy: dict | None = None, yolo: bool = False) -> None:
    """Run preflight checks. Prints results table on any failure (always if verbose).

    kind:    "lxc" or "vm"
    silent:  exit 1 on warnings OR fatal failures — no prompts
    verbose: always print the full table (used by --preflight standalone)
    deploy:  if provided, also check DNS hostname and static IP
    yolo:    continue through warnings without prompting; still blocks on fatal failures
    """
    cfg_path = _ROOT / "config.yaml"

    while True:
        with console.status("[dim]Running preflight checks...[/dim]"):
            checks = [
                _pf_config_valid(cfg_path),
                _pf_proxmox_reachable(cfg),
                _pf_proxmox_auth(cfg),
                _pf_ssh_key_exists(cfg),
                _pf_proxmox_ssh(cfg),
                _pf_ansible_installed(cfg),
            ]
            if kind == "lxc":
                checks.append(_pf_sshpass_installed())
            checks += [
                _pf_dns_reachable(cfg),
                _pf_dns_ssh_auth(cfg),
            ]
            if deploy:
                checks.append(_pf_dns_hostname(cfg, deploy))
                checks.append(_pf_ip_in_use(deploy))
            checks += [
                _pf_inventory_reachable(cfg),
                _pf_inventory_ssh_auth(cfg),
            ]

        fatal_failures = [c for c in checks if not c.passed and c.fatal]
        any_failure = any(not c.passed for c in checks)

        if verbose or any_failure:
            table = Table(show_header=True, header_style="bold")
            table.add_column("Check", min_width=28, style="bold")
            table.add_column("Status", min_width=8)
            table.add_column("Details")
            for c in checks:
                if c.passed:
                    status = "[green]✓ pass[/green]"
                elif c.fatal:
                    status = "[red]✗ FAIL[/red]"
                else:
                    status = "[yellow]⚠ warn[/yellow]"
                table.add_row(c.name, status, c.msg)
            console.print(table)

        if not any_failure:
            if verbose:
                console.print("[green]✓ All preflight checks passed.[/green]\n")
            else:
                console.print("[green]✓ Preflight checks passed.[/green]")
            return

        # Warnings only (no fatal failures)
        if not fatal_failures:
            if silent:
                console.print("[red]✗ Preflight: warnings present. Aborting (--silent).[/red]")
                sys.exit(1)
            if yolo:
                console.print("[yellow]⚠ --yolo: warnings noted — continuing.[/yellow]")
                return
            action = questionary.select(
                "Preflight warnings found. What would you like to do?",
                choices=[
                    questionary.Choice(title="Continue  — proceed despite warnings", value="continue"),
                    questionary.Choice(title="Retry     — re-run all checks",        value="retry"),
                    questionary.Choice(title="Abort     — stop here",                value="abort"),
                ],
            ).ask()
            if action is None or action == "abort":
                console.print("[yellow]Aborted.[/yellow]")
                sys.exit(0)
            if action == "continue":
                console.print("[yellow]Continuing despite preflight warnings...[/yellow]")
                return
            continue  # retry

        # Fatal failures
        if silent:
            console.print("[red]✗ Preflight: fatal checks failed. Aborting.[/red]")
            sys.exit(1)

        action = questionary.select(
            "Preflight checks failed. What would you like to do?",
            choices=[
                questionary.Choice(title="Continue  — proceed despite failures", value="continue"),
                questionary.Choice(title="Retry     — re-run all checks",        value="retry"),
                questionary.Choice(title="Abort     — stop here",                value="abort"),
            ],
        ).ask()
        if action is None or action == "abort":
            console.print("[yellow]Aborted.[/yellow]")
            sys.exit(0)
        if action == "continue":
            console.print("[yellow]Continuing despite preflight failures...[/yellow]")
            return
        # action == "retry" → loop back


# ─────────────────────────────────────────────
# Deploy: interactive wizard helpers
# ─────────────────────────────────────────────

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


def load_deployment_file(path: str) -> dict:
    """Load a deployment JSON; print error and exit if the file is not found."""
    p = Path(path)
    if not p.exists():
        console.print(f"[red]ERROR: Deployment file not found: {path}[/red]")
        sys.exit(1)
    with open(p) as f:
        return json.load(f) or {}


def load_deployment_json(path: Path) -> dict:
    """Load a deployment JSON file (bare — no exit on missing)."""
    with open(path) as f:
        return json.load(f) or {}


def list_deployment_files(kind: str) -> list[Path]:
    """Return sorted list of deployment JSON files for 'vms' or 'lxc'."""
    deployments_dir = _ROOT / "deployments" / kind
    if not deployments_dir.exists():
        return []
    return sorted(deployments_dir.glob("*.json"))


def prompt_package_profile(cfg: dict, deploy: dict, silent: bool) -> tuple[str, list, list]:
    """Interactive package profile selection.

    Returns (package_profile, profile_packages, profile_tags).
    """
    profiles = cfg.get("package_profiles", {})
    deploy_profile = (deploy.get("package_profile", "") or "") if deploy else ""
    if silent:
        package_profile = deploy_profile
        if package_profile and package_profile not in profiles:
            console.print(
                f"[yellow]Warning: package_profile '{package_profile}' not found in config "
                f"— skipping.[/yellow]"
            )
            package_profile = ""
        profile_packages, profile_tags = resolve_profile(package_profile, profiles)
    elif profiles:
        profile_choices = [questionary.Choice(title="[none]", value="")] + [
            questionary.Choice(title=name, value=name) for name in profiles
        ]
        package_profile = questionary.select(
            "Package profile (optional):",
            choices=profile_choices,
            default=deploy_profile if deploy_profile in profiles else "",
        ).ask()
        if package_profile is None:
            sys.exit(0)
        profile_packages, profile_tags = resolve_profile(package_profile, profiles)
    else:
        package_profile = ""
        profile_packages = []
        profile_tags = []
    return package_profile, profile_packages, profile_tags


def prompt_extra_packages(deploy: dict, silent: bool) -> list[str]:
    """Interactive extra packages prompt. Returns list of package names."""
    deploy_extra_pkgs = deploy.get("extra_packages", []) if deploy else []
    if silent:
        return deploy_extra_pkgs
    pkgs_default = ", ".join(deploy_extra_pkgs) if deploy_extra_pkgs else ""
    pkgs_answer = questionary.text(
        "Extra packages to install (optional):",
        instruction="comma-separated, e.g. htop, curl  —  leave blank for none",
        default=pkgs_default,
    ).ask()
    if pkgs_answer is None:
        sys.exit(0)
    return [p.strip() for p in pkgs_answer.split(",") if p.strip()]


def prompt_node_selection(nodes: list[dict], deploy: dict, silent: bool,
                          memory_mb: int, memory_gb_str: str,
                          cpu_threshold: float, ram_threshold: float) -> str:
    """Interactive node selection with resource filtering. Returns node name."""
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
        return node_name

    deploy_node = str(deploy.get("node", ""))
    max_name_len   = max(len(n["name"]) for n in filtered_nodes)
    max_ram_len    = max(len(f"{smart_size(n['free_mem'])} free / {smart_size(n['maxmem'])}") for n in filtered_nodes)
    max_shared_len = max(len(smart_size(n['shared_disk'])) for n in filtered_nodes)
    max_local_len  = max(len(smart_size(n['local_disk']))  for n in filtered_nodes)
    max_cpu_len    = max(len(f"{n['cpu'] * 100:.0f}%")     for n in filtered_nodes)
    node_choices = []
    for n in filtered_nodes:
        is_best = n["name"] == best_node["name"]
        suffix = "  [deploy file]" if n["name"] == deploy_node else ""
        ram_str    = f"{smart_size(n['free_mem'])} free / {smart_size(n['maxmem'])}".ljust(max_ram_len)
        shared_str = smart_size(n['shared_disk']).ljust(max_shared_len)
        local_str  = smart_size(n['local_disk']).ljust(max_local_len)
        cpu_str    = f"{n['cpu'] * 100:.0f}%".ljust(max_cpu_len)
        node_choices.append(questionary.Choice(
            title=[
                ("", "★ " if is_best else "  "),
                ("bold", n["name"].ljust(max_name_len)),
                ("", "  —  "),
                ("bold", "RAM:"),
                ("", f" [{ram_str}]  "),
                ("bold", "Disk:"),
                ("", f" [{local_str} (local) - {shared_str} (shared)]  "),
                ("bold", "CPU:"),
                ("", f" [{cpu_str}]{suffix}"),
            ],
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
    return node_name
