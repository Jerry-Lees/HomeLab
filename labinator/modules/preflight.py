"""
labinator.preflight — Preflight connectivity and dependency checks.
"""

import os
import socket
import subprocess
import sys
from pathlib import Path

import questionary
from rich.console import Console
from rich.table import Table

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

_ROOT = Path(__file__).parent.parent


class _PF:
    """Single preflight check result."""
    __slots__ = ("name", "passed", "msg", "fatal")

    def __init__(self, name: str, passed: bool, msg: str, fatal: bool = True):
        self.name = name
        self.passed = passed
        self.msg = msg
        self.fatal = fatal


def _pf_config_valid(cfg_path: Path) -> _PF:
    from modules.validation import validate_config
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
    from modules.startup import connect_proxmox
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


def dns_precheck(cfg: dict, hostname: str, ip: str, silent: bool = False) -> str:
    """Check whether a DNS A record already exists for hostname before registering.

    Queries the configured DNS server directly (not the system resolver) to
    avoid cache skew.

    Returns one of:
      "proceed" — no existing record, or user chose overwrite
      "skip"    — existing record is the same IP (idempotent), or user chose skip
      "abort"   — user chose abort
    """
    import subprocess as _sp
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        return "proceed"

    dns_server = dns.get("server", "")
    domain = cfg["proxmox"].get("node_domain", "")
    fqdn = f"{hostname}.{domain}" if domain else hostname

    # ── Query forward record (A) ──
    try:
        result = _sp.run(
            ["dig", f"@{dns_server}", fqdn, "A", "+short", "+time=3", "+tries=1"],
            capture_output=True, text=True, timeout=10,
        )
        existing_ips = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, _sp.TimeoutExpired):
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
        ptr_result = _sp.run(
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


def run_preflight(cfg: dict, kind: str, silent: bool = False, verbose: bool = False,
                  deploy: dict | None = None, yolo: bool = False,
                  config_path: Path | None = None) -> None:
    """Run preflight checks. Prints results table on any failure (always if verbose).

    kind:        "lxc" or "vm"
    silent:      exit 1 on warnings OR fatal failures — no prompts
    verbose:     always print the full table (used by --preflight standalone)
    deploy:      if provided, also check DNS hostname and static IP
    yolo:        continue through warnings without prompting; still blocks on fatal failures
    config_path: path to config file (defaults to config.yaml in project root)
    """
    import modules.proxmox as _proxmox_provider
    import modules.bind as _bind_provider
    import modules.ansible as _ansible_provider

    cfg_path = config_path or (_ROOT / "config.yaml")

    while True:
        with console.status("[dim]Running preflight checks...[/dim]"):
            checks = [
                _pf_config_valid(cfg_path),
                _pf_ssh_key_exists(cfg),
                _pf_ansible_installed(cfg),
            ]
            if kind == "lxc":
                checks.append(_pf_sshpass_installed())
            checks += _proxmox_provider.preflight_checks(cfg, kind)
            checks += _bind_provider.preflight_checks(cfg, kind, deploy=deploy)
            if deploy:
                checks.append(_pf_ip_in_use(deploy))
            checks += _ansible_provider.preflight_checks(cfg, kind)

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
