"""
labinator.startup — Config loading and early-startup checks.
"""

import subprocess
import sys
from pathlib import Path

import yaml
from rich.console import Console

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

_ROOT = Path(__file__).parent.parent


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else _ROOT / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]ERROR: config file not found at {config_path}[/red]")
        console.print("Copy config.yaml.example to config.yaml and fill in your credentials.")
        sys.exit(1)
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    if cfg["proxmox"]["token_secret"] == "CHANGEME-PASTE-YOUR-TOKEN-SECRET-HERE":
        console.print("[red]ERROR: Edit config.yaml and set proxmox.token_secret[/red]")
        sys.exit(1)
    return cfg


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
