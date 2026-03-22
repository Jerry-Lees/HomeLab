#!/usr/bin/env python3
"""
Labinator Batch Deploy
======================
Deploy multiple LXC containers and/or VMs in parallel from deployment JSON files.
Reads the "type" field from each file ("lxc" or "vm") and calls the appropriate
deploy script in silent mode. Continues on failure and prints a summary table.

Usage:
  python3 deploy.py --batch deployments/lxc/web1.json deployments/vms/db1.json
  python3 deploy.py --batch-dir deployments/batch/
  python3 deploy.py --batch-dir deployments/batch/ --validate
  python3 deploy.py --batch FILE ... --config /path/to/config.yaml
  python3 deploy.py --batch FILE ... --parallel 5
  python3 deploy.py --batch FILE ... --parallel 1   # sequential
"""

# Auto-activate virtualenv so `python3 deploy.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from modules.lib import (
    load_config,
    connect_proxmox,
    validate_config,
    validate_lxc_deployment,
    validate_vm_deployment,
    get_running_vmids,
)

console = Console()
_output_lock = threading.Lock()

_ROOT = Path(__file__).parent
_LOG_DIR = _ROOT / "logs"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def peek_type(path: Path) -> str | None:
    """Read the deployment JSON and return 'lxc' or 'vm'. Returns None on parse error."""
    try:
        with open(path) as f:
            d = json.load(f)
        if d.get("type") == "lxc" or "template_name" in d:
            return "lxc"
        return "vm"
    except Exception:
        return None


def peek_hostname(path: Path) -> str:
    """Return hostname from deployment JSON, falling back to the file stem."""
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("hostname", path.stem)
    except Exception:
        return path.stem


def peek_node(path: Path) -> str:
    """Return node from deployment JSON, falling back to empty string."""
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("node", "")
    except Exception:
        return ""


def peek_vmid(path: Path) -> int | None:
    """Return vmid from deployment JSON if present, else None."""
    try:
        with open(path) as f:
            d = json.load(f)
        vmid = d.get("vmid")
        return int(vmid) if vmid else None
    except Exception:
        return None


def collect_files(args) -> list[Path]:
    """Resolve the list of deployment JSON files from --batch or --batch-dir."""
    if args.batch:
        files = [Path(p) for p in args.batch]
        missing = [p for p in files if not p.exists()]
        if missing:
            for p in missing:
                console.print(f"[red]File not found: {p}[/red]")
            sys.exit(1)
        return files
    else:
        d = Path(args.batch_dir)
        if not d.is_dir():
            console.print(f"[red]Directory not found: {d}[/red]")
            sys.exit(1)
        files = sorted(d.glob("*.json"))
        if not files:
            console.print(f"[red]No JSON files found in {d}[/red]")
            sys.exit(1)
        return files


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s"


_STEP_RE = re.compile(r'Step\s+(\d+/\d+):\s+(?:─+\s*)?(.+?)(?:\s*─+)?$')


def _parse_status(line: str) -> str | None:
    """Extract a short status string from a deploy script output line, or None."""
    s = line.strip()
    if not s:
        return None
    m = _STEP_RE.search(s)
    if m:
        return f"Step {m.group(1)}: {m.group(2).strip()}"
    if "All preflight checks passed" in s:
        return "Preflight passed"
    if s.startswith("✓ Container IP:") or s.startswith("✓ VM IP:"):
        return s.lstrip("✓ ").strip()
    if "Bootstrap complete" in s:
        return "SSH bootstrapped"
    if "Post-deployment configuration complete" in s:
        return "Ansible complete"
    if s.startswith("✓ DNS registered:"):
        return s.lstrip("✓ ").strip()
    if "Inventory updated on" in s:
        return "Inventory updated"
    if "SSH OK" in s and "hostname:" in s:
        return s.lstrip("✓ ").strip()
    if "Connected to" in s and ("proxmox" in s.lower() or "node" in s.lower()):
        return "Connected to Proxmox"
    return None


# ─────────────────────────────────────────────
# Validate-only mode
# ─────────────────────────────────────────────


def run_validate_all(files: list[Path], config_path: str | None) -> bool:
    console.print()
    console.print(Panel.fit(
        Text("Labinator Batch Validate", style="bold", justify="center"),
        border_style="dim",
    ))
    console.print()

    # Validate global config once
    cfg_ok = True
    try:
        cfg_path_obj = Path(config_path) if config_path else _ROOT / "config.yaml"
        errors = validate_config(cfg_path_obj)
        if errors:
            for e in errors:
                console.print(f"[red]  config.yaml: {e}[/red]")
            cfg_ok = False
        else:
            console.print("[green]✓ config.yaml  OK[/green]")
    except Exception as e:
        console.print(f"[red]✗ config.yaml: {e}[/red]")
        cfg_ok = False

    console.print()

    all_ok = cfg_ok
    for path in files:
        kind = peek_type(path)
        hostname = peek_hostname(path)
        if kind is None:
            console.print(f"[red]✗ {path.name}  —  invalid JSON[/red]")
            all_ok = False
            continue
        validator = validate_lxc_deployment if kind == "lxc" else validate_vm_deployment
        errors = validator(path)
        if errors:
            console.print(f"[red]✗ {path.name}  ({hostname})[/red]")
            for e in errors:
                console.print(f"  [red]→ {e}[/red]")
            all_ok = False
        else:
            console.print(f"[green]✓ {path.name}  ({hostname} / {kind})[/green]")

    console.print()
    if all_ok:
        console.print(f"[green]All {len(files)} file(s) valid.[/green]")
    else:
        console.print("[red]Validation failed — fix errors before deploying.[/red]")
    return all_ok


# ─────────────────────────────────────────────
# Per-file deploy
# ─────────────────────────────────────────────


def deploy_one(
    path: Path,
    kind: str,
    running_vmids: set[int],
    passthrough_args: list[str],
    idx: int,
    total: int,
    on_status=None,
) -> dict:
    """Deploy a single file.  on_status(hostname, str) signals parallel mode."""
    hostname = peek_hostname(path)
    vmid = peek_vmid(path)
    parallel = on_status is not None

    result = {
        "hostname": hostname,
        "type": kind,
        "path": path,
        "status": "failed",
        "elapsed": 0.0,
        "error": "",
    }

    # Skip if already running (idempotent re-run protection)
    if vmid and vmid in running_vmids:
        if not parallel:
            console.print()
            console.print(
                f"[yellow]── [{idx}/{total}] {hostname} ({kind}) — "
                f"VMID {vmid} already running, skipping ──[/yellow]"
            )
        result["status"] = "skipped"
        return result

    if not parallel:
        console.print()
        console.print(f"[bold]── [{idx}/{total}] {hostname} ({kind}) ──[/bold]")

    script = str(_ROOT / ("deploy_lxc.py" if kind == "lxc" else "deploy_vm.py"))
    cmd = [sys.executable, script, "--deploy-file", str(path), "--silent"] + passthrough_args

    t0 = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if parallel:
            # Parallel mode: read line-by-line, emit status updates, buffer output
            output_lines: list[str] = []
            for line in proc.stdout:
                output_lines.append(line)
                detected = _parse_status(line)
                if detected:
                    on_status(hostname, detected)
            proc.wait()
            result["output"] = "".join(output_lines)
            result["elapsed"] = time.time() - t0
            result["status"] = "ok" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                result["error"] = f"exit code {proc.returncode}"
        else:
            # Sequential mode: stream output live AND buffer for log
            output_lines: list[str] = []
            for line in proc.stdout:
                output_lines.append(line)
                console.print(line, end="")
            proc.wait()
            result["output"] = "".join(output_lines)
            result["elapsed"] = time.time() - t0
            if proc.returncode == 0:
                result["status"] = "ok"
            else:
                result["status"] = "failed"
                result["error"] = f"exit code {proc.returncode}"
    except Exception as e:
        result["elapsed"] = time.time() - t0
        result["status"] = "failed"
        result["error"] = str(e)
        if not parallel:
            console.print(f"[red]✗ Failed to launch deploy script: {e}[/red]")

    return result


# ─────────────────────────────────────────────
# Summary table
# ─────────────────────────────────────────────


def print_summary(results: list[dict], con: Console | None = None) -> None:
    con = con or console
    ok_count      = sum(1 for r in results if r["status"] == "ok")
    skip_count    = sum(1 for r in results if r["status"] == "skipped")
    fail_count    = sum(1 for r in results if r["status"] == "failed")

    con.print()
    tbl = Table(
        title="Batch Deploy Results",
        show_header=True,
        header_style="bold",
        border_style="dim",
    )
    tbl.add_column("Hostname", style="cyan")
    tbl.add_column("Type", style="dim", min_width=4)
    tbl.add_column("Result", justify="center")
    tbl.add_column("Elapsed", justify="right", min_width=7)

    for r in results:
        if r["status"] == "ok":
            result_cell = "[green]✓[/green]"
            row_style   = ""
            elapsed     = fmt_elapsed(r["elapsed"])
        elif r["status"] == "skipped":
            result_cell = "[yellow]skipped[/yellow]"
            row_style   = "dim"
            elapsed     = "—"
        else:
            result_cell = "[red]✗[/red]"
            row_style   = "red"
            elapsed     = fmt_elapsed(r["elapsed"]) if r["elapsed"] else "—"
        tbl.add_row(r["hostname"], r["type"], result_cell, elapsed, style=row_style)

    con.print(tbl)
    con.print(
        f"  [green]{ok_count} deployed[/green]   "
        f"[yellow]{skip_count} skipped[/yellow]   "
        f"[red]{fail_count} failed[/red]"
    )


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="deploy.py",
        description="Labinator Batch Deploy — deploy multiple VMs/LXCs from JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--batch", nargs="+", metavar="FILE",
        help="One or more deployment JSON files to deploy in order",
    )
    group.add_argument(
        "--batch-dir", metavar="DIR",
        help="Directory of deployment JSON files to deploy alphabetically",
    )

    parser.add_argument("--validate", action="store_true",
                        help="Validate all files and exit without deploying")
    parser.add_argument("--config", metavar="FILE", default=None,
                        help="Path to config.yaml (default: config.yaml next to this script)")
    parser.add_argument("--yolo", action="store_true",
                        help="Skip preflight checks in each deploy script")
    parser.add_argument("--ttl", metavar="DURATION", default=None,
                        help="TTL for all deployed resources (e.g. 7d, 24h)")
    parser.add_argument("--parallel", metavar="N", type=int, default=3,
                        help="Max concurrent deployments (default: 3, use 1 for sequential)")
    parser.add_argument("--stagger", metavar="SECS", type=int, default=45,
                        help="Seconds between each job start in parallel mode (default: 45, use 0 to disable)")

    args = parser.parse_args()

    files = collect_files(args)

    if args.validate:
        ok = run_validate_all(files, args.config)
        sys.exit(0 if ok else 1)

    # Connect once to get running VMIDs for skip-check
    cfg = load_config(args.config)
    proxmox = connect_proxmox(cfg)
    console.print()
    with console.status("[bold green]Fetching running VMIDs..."):
        running_vmids = get_running_vmids(proxmox)

    passthrough = []
    if args.config:
        passthrough += ["--config", args.config]
    if args.yolo:
        passthrough += ["--yolo"]
    if args.ttl:
        passthrough += ["--ttl", args.ttl]

    parallel = max(1, args.parallel)
    stagger = max(0, args.stagger) if parallel > 1 else 0
    mode_label = f"parallel ×{parallel}" if parallel > 1 else "sequential"
    if stagger and parallel > 1:
        mode_label += f"  stagger {stagger}s"
    console.print()
    console.print(Panel.fit(
        Text(f"Labinator Batch Deploy — {len(files)} file(s)  [{mode_label}]", style="bold", justify="center"),
        border_style="cyan",
    ))

    # Separate invalid files (handle synchronously) from valid jobs
    total = len(files)
    results: list[dict | None] = [None] * total
    valid_jobs: list[tuple[int, int, Path, str]] = []  # (list_idx, disp_idx, path, kind)

    for i, path in enumerate(files):
        kind = peek_type(path)
        if kind is None:
            console.print(f"\n[red]── [{i+1}/{total}] {path.name} — invalid JSON, skipping ──[/red]")
            results[i] = {
                "hostname": path.stem,
                "type": "?",
                "path": path,
                "status": "failed",
                "elapsed": 0.0,
                "error": "invalid JSON",
            }
        else:
            valid_jobs.append((i, i + 1, path, kind))

    if parallel == 1:
        # Sequential — stream output undecorated, same as before
        for list_i, disp_i, path, kind in valid_jobs:
            results[list_i] = deploy_one(
                path, kind, running_vmids, passthrough,
                idx=disp_i, total=total,
            )
    else:
        # Parallel — Live 2-line-per-host status board, buffered output printed after
        job_meta = {peek_hostname(p): (kind, disp_i, total, peek_node(p)) for _, disp_i, p, kind in valid_jobs}
        hostnames_ordered = [peek_hostname(p) for _, _, p, _ in valid_jobs]
        statuses: dict[str, str] = {h: "[dim]queued...[/dim]" for h in hostnames_ordered}
        statuses_lock = threading.Lock()

        def make_renderable() -> Text:
            t = Text()
            with statuses_lock:
                for i, h in enumerate(hostnames_ordered):
                    kind_h, idx_h, tot_h, node_h = job_meta[h]
                    if i > 0:
                        t.append("\n")
                    node_suffix = f" (deploying to: {node_h})" if node_h else ""
                    t.append(f" {h} ({kind_h}) [{idx_h}/{tot_h}]{node_suffix}\n", style="bold")
                    t.append("   → ", style="dim")
                    t.append_text(Text.from_markup(statuses[h]))
            return t

        future_to_list_i: dict = {}
        with Live(make_renderable(), refresh_per_second=4, console=console) as live:
            def on_status(hostname: str, status: str) -> None:
                with statuses_lock:
                    statuses[hostname] = status
                live.update(make_renderable())

            with ThreadPoolExecutor(max_workers=parallel) as executor:
                for job_num, (list_i, disp_i, path, kind) in enumerate(valid_jobs):
                    if stagger and job_num > 0:
                        time.sleep(stagger)
                    f = executor.submit(
                        deploy_one, path, kind, running_vmids, passthrough,
                        disp_i, total, on_status,
                    )
                    future_to_list_i[f] = list_i
                for f in as_completed(future_to_list_i):
                    r = f.result()
                    results[future_to_list_i[f]] = r
                    elapsed_str = fmt_elapsed(r["elapsed"]) if r["elapsed"] else ""
                    if r["status"] == "ok":
                        on_status(r["hostname"], f"[green]✓ Done in {elapsed_str}[/green]")
                    elif r["status"] == "skipped":
                        on_status(r["hostname"], "[yellow]skipped[/yellow]")
                    else:
                        on_status(r["hostname"], f"[red]✗ Failed ({r.get('error', '')})[/red]")

        # Live has exited — print all buffered outputs in original order
        for r in [results[i] for i in range(total) if results[i] is not None]:
            if "output" not in r:
                continue
            elapsed_str = fmt_elapsed(r["elapsed"]) if r["elapsed"] else "—"
            console.print(f"\n[bold cyan]{'─' * 10} {r['hostname']} {'─' * 10}[/bold cyan]")
            console.print(r["output"], end="")
            if r["status"] == "ok":
                console.print(f"[green]✓ {r['hostname']} done in {elapsed_str}[/green]")
            else:
                console.print(f"[red]✗ {r['hostname']} failed ({r.get('error', '')}) after {elapsed_str}[/red]")

    final_results = [r for r in results if r is not None]
    print_summary(final_results)

    # Write full run log
    _LOG_DIR.mkdir(exist_ok=True)
    log_path = _LOG_DIR / "last-deployment.log"
    with open(log_path, "w") as lf:
        lc = Console(file=lf, highlight=False, width=120, no_color=True)
        lc.print(f"Labinator Batch Deploy — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lc.print(f"Command: {' '.join(sys.argv)}")
        lc.print()
        for r in [results[i] for i in range(total) if results[i] is not None]:
            if "output" not in r:
                continue
            elapsed_str = fmt_elapsed(r["elapsed"]) if r["elapsed"] else "—"
            lc.print(f"{'─' * 10} {r['hostname']} {'─' * 10}")
            lc.print(r["output"], end="")
            if r["status"] == "ok":
                lc.print(f"✓ {r['hostname']} done in {elapsed_str}")
            elif r["status"] == "skipped":
                lc.print(f"- {r['hostname']} skipped")
            else:
                lc.print(f"✗ {r['hostname']} failed ({r.get('error', '')}) after {elapsed_str}")
        print_summary(final_results, lc)
    console.print(f"[dim]Log: {log_path}[/dim]")

    failed = sum(1 for r in results if r is not None and r["status"] == "failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/yellow]")
        sys.exit(0)
