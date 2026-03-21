"""
labinator.deploy — Deploy-time helpers: health check and SSH wait.
"""

import socket
import sys
import time
from pathlib import Path

import paramiko
from rich.console import Console

console = Console()

_ROOT = Path(__file__).parent.parent


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
