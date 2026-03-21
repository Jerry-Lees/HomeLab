"""
labinator.bind — DNS (BIND) Ansible integration helpers.
"""

import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()

_ROOT = Path(__file__).parent.parent


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


def preflight_checks(cfg: dict, kind: str, deploy: dict | None = None) -> list:
    """Return DNS-specific preflight check results."""
    from modules.preflight import _pf_dns_reachable, _pf_dns_ssh_auth, _pf_dns_hostname
    checks = [_pf_dns_reachable(cfg), _pf_dns_ssh_auth(cfg)]
    if deploy is not None:
        checks.append(_pf_dns_hostname(cfg, deploy))
    return checks
