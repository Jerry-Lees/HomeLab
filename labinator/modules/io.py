"""
labinator.io — File I/O helpers: deployment files, history log, argparse, dry-run banners.
"""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

_ROOT = Path(__file__).parent.parent


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


def write_deployment_file(data: dict, hostname: str, kind: str, cfg: dict) -> Path:
    """Write deployment JSON to deployments/{kind}/{hostname}.json. Returns path.

    kind: 'lxc' or 'vms' (directory name under deployments/).
    Caller assembles the full data dict including all fields.
    """
    deployments_dir = _ROOT / "deployments" / kind
    deployments_dir.mkdir(parents=True, exist_ok=True)
    deploy_file = deployments_dir / f"{hostname}.json"
    with open(deploy_file, "w") as f:
        json.dump(data, f, indent=2)
    console.print(f"  [dim]Deployment file saved: {deploy_file}[/dim]")
    return deploy_file


def add_common_deploy_args(parser: argparse.ArgumentParser) -> None:
    """Add deploy wizard CLI arguments that are identical across deploy_lxc.py and deploy_vm.py."""
    parser.add_argument(
        "--deploy-file", metavar="FILE",
        help="JSON deployment file to pre-fill defaults (saved from a previous run)",
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
    parser.add_argument(
        "--config", metavar="FILE",
        help="Path to an alternate config file (default: config.yaml in project root)",
    )


def print_dry_run_header(kind: str) -> None:
    """Print the dry-run wizard banner panel."""
    label = "LXC Deploy" if kind == "lxc" else "VM Deploy"
    console.print()
    console.print(Panel.fit(
        Text(f"Labinator Dry Run — {label}", style="bold yellow"),
        border_style="yellow",
    ))
    console.print()


def print_dry_run_footer() -> None:
    """Print the dry-run completion message and exit 0."""
    console.print()
    console.print("[bold green]Dry run complete — no changes made.[/bold green]")
    sys.exit(0)
