#!/usr/bin/env python3
"""
Proxmox LXC Decommission Script
================================
PERMANENTLY destroys an LXC container and removes all traces:
  - Stops and deletes the container via Proxmox API
  - Removes DNS A + PTR records from BIND
  - Removes host from Ansible inventory
  - Deletes the local deployment file

THIS IS IRREVERSIBLE. Use with extreme caution.
"""

# Auto-activate virtualenv so `python3 decomm_lxc.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import random
import subprocess
import termios
import time
import tty
from pathlib import Path

import json
import questionary
import yaml
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


# ─────────────────────────────────────────────
# Config + Proxmox helpers (shared with deploy_lxc.py)
# ─────────────────────────────────────────────

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        console.print(f"[red]ERROR: config.yaml not found at {config_path}[/red]")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def connect_proxmox(cfg: dict) -> ProxmoxAPI:
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


def wait_for_task(proxmox: ProxmoxAPI, node: str, taskid: str, timeout: int = 120) -> None:
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
    raise TimeoutError(f"Task {taskid} did not complete within {timeout}s")


# ─────────────────────────────────────────────
# Deployment file helpers
# ─────────────────────────────────────────────

def list_deployment_files() -> list[Path]:
    deployments_dir = Path(__file__).parent / "deployments" / "lxc"
    if not deployments_dir.exists():
        return []
    return sorted(deployments_dir.glob("*.json"))


def load_deployment_file(path: Path) -> dict:
    with open(path) as f:
        return json.load(f) or {}


# ─────────────────────────────────────────────
# Decommission steps
# ─────────────────────────────────────────────

def stop_and_destroy_container(proxmox: ProxmoxAPI, deploy: dict) -> None:
    node = deploy["node"]
    vmid = deploy["vmid"]
    hostname = deploy["hostname"]

    # Check if container exists
    try:
        status = proxmox.nodes(node).lxc(vmid).status.current.get()
    except Exception:
        console.print(f"  [yellow]Container {vmid} not found on {node} — may already be deleted.[/yellow]")
        return

    # Stop if running
    if status.get("status") == "running":
        console.print(f"  [dim]Stopping container {vmid} ({hostname})...[/dim]")
        try:
            task = proxmox.nodes(node).lxc(vmid).status.stop.post()
            wait_for_task(proxmox, node, task, timeout=60)
            console.print(f"  [green]✓ Container stopped[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not stop container cleanly: {e}[/yellow]")

    # Destroy
    console.print(f"  [dim]Destroying container {vmid}...[/dim]")
    try:
        task = proxmox.nodes(node).lxc(vmid).delete(**{"purge": 1, "destroy-unreferenced-disks": 1})
        wait_for_task(proxmox, node, task, timeout=120)
        console.print(f"  [green]✓ Container {vmid} destroyed[/green]")
    except Exception as e:
        console.print(f"  [red]✗ Failed to destroy container: {e}[/red]")
        raise


def remove_dns(cfg: dict, deploy: dict) -> None:
    dns = cfg.get("dns", {})
    if not dns.get("enabled", False):
        console.print("  [dim]DNS removal skipped (disabled in config)[/dim]")
        return

    ansible_dir = Path(__file__).parent / "ansible"
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
    inv_cfg = cfg.get("ansible_inventory", {})
    if not inv_cfg:
        console.print("  [dim]Inventory removal skipped (not configured)[/dim]")
        return

    ansible_dir = Path(__file__).parent / "ansible"
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
# Scary confirmation challenge
# ─────────────────────────────────────────────

def random_caps(word: str) -> str:
    """Return word with randomly mixed case — guaranteed at least one upper and one lower."""
    chars = [c.upper() if random.random() > 0.5 else c.lower() for c in word]
    # Guarantee mixed case so it always looks randomized
    if not any(c.isupper() for c in chars):
        idx = random.randrange(len(chars))
        chars[idx] = chars[idx].upper()
    if not any(c.islower() for c in chars):
        idx = random.randrange(len(chars))
        chars[idx] = chars[idx].lower()
    return "".join(chars)


def flush_stdin() -> None:
    """Discard any buffered keystrokes for 5 seconds so accidental presses don't auto-confirm."""
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setraw(fd)
        sys.stdin.flush()
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        pass  # Not a TTY — skip (e.g. piped input)


def confirm_destruction(deploy: dict) -> bool:
    """Display scary warning and require typed confirmation."""
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
            f"This will [bold red]STOP and DESTROY[/bold red] the container,\n"
            f"[bold red]REMOVE[/bold red] its DNS records, and\n"
            f"[bold red]DELETE[/bold red] it from the Ansible inventory.\n\n"
            f"[bold yellow]There is NO undo.[/bold yellow]"
        ),
        border_style="bold red",
        title=f"[bold red]{SKULL}  DECOMMISSION WIZARD  {SKULL}[/bold red]",
        padding=(1, 2),
    ))
    console.print()

    # Flush any buffered keystrokes, then countdown
    console.print("[yellow]Flushing keyboard buffer — please wait 5 seconds...[/yellow]")
    flush_stdin()
    for i in range(5, 0, -1):
        console.print(f"  [dim]{i}...[/dim]", end="\r")
        time.sleep(1)
    console.print()

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
# Main
# ─────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="decomm_lxc.py",
        description="Proxmox LXC Decommission Wizard — permanently destroys a container",
        epilog="Examples:\n  python3 decomm_lxc.py\n  python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--deploy-file", metavar="FILE",
        help="Path to deployment JSON file — skips interactive selection",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help="Also delete the local deployment file after decommission",
    )
    parser.add_argument(
        "--silent", action="store_true",
        help="Non-interactive mode: skip confirmation challenge (requires --deploy-file)",
    )
    args = parser.parse_args()

    if args.silent and not args.deploy_file:
        console.print("[red]ERROR: --silent requires --deploy-file (no interactive selection in silent mode)[/red]")
        sys.exit(1)

    console.print()
    console.print(Panel.fit(
        Text(
            "Proxmox LXC Decommission Wizard\n"
            "Permanently destroys a container and removes all traces",
            style="bold red",
            justify="center",
        ),
        border_style="red",
    ))
    console.print()

    if args.deploy_file:
        # Load directly from specified file — skip interactive list
        deploy_path = Path(args.deploy_file)
        if not deploy_path.exists():
            console.print(f"[red]ERROR: Deployment file not found: {deploy_path}[/red]")
            sys.exit(1)
        deploy = load_deployment_file(deploy_path)
    else:
        # List available deployment files and let user pick
        deploy_files = list_deployment_files()
        if not deploy_files:
            console.print("[red]No deployment files found in deployments/lxc/ folder.[/red]")
            console.print("Only containers deployed via deploy_lxc.py can be decommissioned this way.")
            sys.exit(1)

        choices = []
        for f in deploy_files:
            d = load_deployment_file(f)
            ip = d.get("assigned_ip") or d.get("ip_address", "?")
            label = (
                f"{d.get('hostname', f.stem):<20}  "
                f"node={d.get('node', '?'):<12}  "
                f"ip={ip:<16}  "
                f"deployed={d.get('deployed_at', '?')}"
            )
            choices.append(questionary.Choice(title=label, value=f))

        deploy_path = questionary.select(
            "Select container to decommission:",
            choices=choices,
        ).ask()

        if deploy_path is None:
            console.print("\n[yellow]Aborted.[/yellow]")
            sys.exit(0)

        deploy = load_deployment_file(deploy_path)

    # Scary confirmation (skipped in silent mode)
    if args.silent:
        hostname = deploy.get("hostname", "unknown")
        console.print(f"[yellow]Silent mode: skipping confirmation challenge for {hostname}.[/yellow]")
    elif not confirm_destruction(deploy):
        sys.exit(0)

    # Load config and connect to Proxmox
    cfg = load_config()

    with console.status("[bold red]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    # ── Step 1: Stop and destroy container ──
    console.print("[bold red]─── Step 1/4: Destroying Proxmox container ───[/bold red]")
    try:
        stop_and_destroy_container(proxmox, deploy)
    except Exception as e:
        console.print(f"[red]✗ Container destruction failed: {e}[/red]")
        console.print("[yellow]Continuing with DNS and inventory cleanup anyway...[/yellow]")

    # ── Step 2: Remove DNS records ──
    console.print("[bold red]─── Step 2/4: Removing DNS records ───[/bold red]")
    remove_dns(cfg, deploy)

    # ── Step 3: Remove from Ansible inventory ──
    console.print("[bold red]─── Step 3/4: Removing from Ansible inventory ───[/bold red]")
    remove_from_inventory(cfg, deploy)

    # ── Step 4: Deployment file ──
    # SAFETY: NEVER delete the deployment file unless --purge was explicitly passed by
    # the user on the command line. Do NOT add automatic deletion logic here.
    # The file is the permanent record of the deployment and may be needed for recovery.
    console.print("[bold red]─── Step 4/4: Deployment file ───[/bold red]")
    if args.purge:
        try:
            deploy_path.unlink()
            console.print(f"  [green]✓ Deleted {deploy_path}[/green]")
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not delete {deploy_path}: {e}[/yellow]")
    else:
        console.print(f"  [yellow]Deployment file NOT deleted: {deploy_path}[/yellow]")
        console.print(f"  [dim]Run with --purge to delete it, or remove it manually.[/dim]")

    # Done
    hostname = deploy.get("hostname", "unknown")
    console.print()
    console.print(Panel(
        f"[bold red]Decommission Complete[/bold red]\n\n"
        f"[bold]{hostname}[/bold] has been permanently destroyed.\n"
        f"Container deleted, DNS removed, inventory updated.",
        border_style="red",
        title=f"[bold red]{SKULL}  Done[/bold red]",
    ))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
