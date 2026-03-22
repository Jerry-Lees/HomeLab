#!/usr/bin/env python3
"""
Labinator Batch Decomm
======================
Decommission multiple LXC containers and/or VMs from deployment JSON files.
Reads the "type" field from each file ("lxc" or "vm") and calls the appropriate
decomm script in silent mode. Defaults to sequential (--parallel 1) to avoid
race conditions on the shared BIND DNS zone file.

Usage:
  python3 decomm.py --batch deployments/lxc/web1.json deployments/vms/db1.json
  python3 decomm.py --batch-dir deployments/batch/
  python3 decomm.py --batch FILE ... --parallel 3   # parallel (DNS race risk)
  python3 decomm.py --batch FILE ... --purge        # also delete deploy files
"""

# Auto-activate virtualenv so `python3 decomm.py` works without sourcing .venv
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

console = Console()
_output_lock = threading.Lock()

_ROOT = Path(__file__).parent
_LOG_DIR = _ROOT / "logs"


# ─────────────────────────────────────────────
# Helpers (mirrors deploy.py)
# ─────────────────────────────────────────────


def peek_type(path: Path) -> str | None:
    try:
        with open(path) as f:
            d = json.load(f)
        if d.get("type") == "lxc" or "template_name" in d:
            return "lxc"
        return "vm"
    except Exception:
        return None


def peek_hostname(path: Path) -> str:
    try:
        with open(path) as f:
            d = json.load(f)
        return d.get("hostname", path.stem)
    except Exception:
        return path.stem


def collect_files(args) -> list[Path]:
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
    """Extract a short status string from a decomm script output line, or None."""
    s = line.strip()
    if not s:
        return None
    m = _STEP_RE.search(s)
    if m:
        return f"Step {m.group(1)}: {m.group(2).strip()}"
    if "All preflight checks passed" in s:
        return "Preflight passed"
    if "Stopped" in s and ("container" in s.lower() or "vm" in s.lower()):
        return "Stopped"
    if "Destroyed" in s or "Deleted" in s:
        return "Destroyed"
    if "DNS record removed" in s or "DNS removed" in s:
        return "DNS removed"
    if "Inventory updated" in s or "removed from inventory" in s.lower():
        return "Inventory updated"
    if "Connected to" in s and ("proxmox" in s.lower() or "node" in s.lower()):
        return "Connected to Proxmox"
    return None


# ─────────────────────────────────────────────
# Per-file decomm
# ─────────────────────────────────────────────


def decomm_one(
    path: Path,
    kind: str,
    passthrough_args: list[str],
    idx: int,
    total: int,
    on_status=None,
) -> dict:
    """Decomm a single file.  on_status(hostname, str) signals parallel mode."""
    hostname = peek_hostname(path)
    parallel = on_status is not None

    result = {
        "hostname": hostname,
        "type": kind,
        "path": path,
        "status": "failed",
        "elapsed": 0.0,
        "error": "",
    }

    if not parallel:
        console.print()
        console.print(f"[bold red]── [{idx}/{total}] {hostname} ({kind}) ──[/bold red]")

    script = str(_ROOT / ("decomm_lxc.py" if kind == "lxc" else "decomm_vm.py"))
    cmd = [sys.executable, script, "--silent", "--deploy-file", str(path)] + passthrough_args

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
            console.print(f"[red]✗ Failed to launch decomm script: {e}[/red]")

    return result


# ─────────────────────────────────────────────
# Summary table
# ─────────────────────────────────────────────


def print_summary(results: list[dict], con: Console | None = None) -> None:
    con = con or console
    ok_count   = sum(1 for r in results if r["status"] == "ok")
    fail_count = sum(1 for r in results if r["status"] == "failed")

    con.print()
    tbl = Table(
        title="Batch Decomm Results",
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
        else:
            result_cell = "[red]✗[/red]"
            row_style   = "red"
            elapsed     = fmt_elapsed(r["elapsed"]) if r["elapsed"] else "—"
        tbl.add_row(r["hostname"], r["type"], result_cell, elapsed, style=row_style)

    con.print(tbl)
    con.print(
        f"  [green]{ok_count} decomm'd[/green]   "
        f"[red]{fail_count} failed[/red]"
    )


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="decomm.py",
        description="Labinator Batch Decomm — decommission multiple VMs/LXCs from JSON files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--batch", nargs="+", metavar="FILE",
        help="One or more deployment JSON files to decommission",
    )
    group.add_argument(
        "--batch-dir", metavar="DIR",
        help="Directory of deployment JSON files to decommission",
    )

    parser.add_argument("--config", metavar="FILE", default=None,
                        help="Path to config.yaml (default: config.yaml next to this script)")
    parser.add_argument("--purge", action="store_true",
                        help="Delete deployment JSON files after decommission")
    parser.add_argument(
        "--parallel", metavar="N", type=int, default=1,
        help="Max concurrent decomms (default: 1 — sequential avoids DNS zone race conditions)",
    )
    parser.add_argument("--stagger", metavar="SECS", type=int, default=0,
                        help="Seconds between each job start in parallel mode (default: 0)")

    args = parser.parse_args()
    files = collect_files(args)
    parallel = max(1, args.parallel)
    stagger = max(0, args.stagger) if parallel > 1 else 0

    if parallel > 1:
        console.print(
            f"[yellow]⚠ parallel={parallel}: DNS zone file updates may race. "
            f"If records are missed, re-run sequentially.[/yellow]"
        )

    mode_label = f"parallel ×{parallel}" if parallel > 1 else "sequential"
    if stagger and parallel > 1:
        mode_label += f"  stagger {stagger}s"
    console.print()
    console.print(Panel.fit(
        Text(f"Labinator Batch Decomm — {len(files)} file(s)  [{mode_label}]",
             style="bold red", justify="center"),
        border_style="red",
    ))

    passthrough = []
    if args.config:
        passthrough += ["--config", args.config]
    if args.purge:
        passthrough += ["--purge"]

    total = len(files)
    results: list[dict | None] = [None] * total
    valid_jobs: list[tuple[int, int, Path, str]] = []

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
        for list_i, disp_i, path, kind in valid_jobs:
            results[list_i] = decomm_one(path, kind, passthrough, idx=disp_i, total=total)
    else:
        # Parallel — Live 2-line-per-host status board, buffered output printed after
        job_meta = {peek_hostname(p): (kind, disp_i, total) for _, disp_i, p, kind in valid_jobs}
        hostnames_ordered = [peek_hostname(p) for _, _, p, _ in valid_jobs]
        statuses: dict[str, str] = {h: "[dim]queued...[/dim]" for h in hostnames_ordered}
        statuses_lock = threading.Lock()

        def make_renderable() -> Text:
            t = Text()
            with statuses_lock:
                for i, h in enumerate(hostnames_ordered):
                    kind_h, idx_h, tot_h = job_meta[h]
                    if i > 0:
                        t.append("\n")
                    t.append(f" {h} ({kind_h}) [{idx_h}/{tot_h}]\n", style="bold")
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
                        decomm_one, path, kind, passthrough, disp_i, total, on_status,
                    )
                    future_to_list_i[f] = list_i
                for f in as_completed(future_to_list_i):
                    r = f.result()
                    results[future_to_list_i[f]] = r
                    elapsed_str = fmt_elapsed(r["elapsed"]) if r["elapsed"] else ""
                    if r["status"] == "ok":
                        on_status(r["hostname"], f"[green]✓ Done in {elapsed_str}[/green]")
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
                console.print(f"[green]✓ {r['hostname']} decomm'd in {elapsed_str}[/green]")
            else:
                console.print(f"[red]✗ {r['hostname']} failed ({r.get('error', '')}) after {elapsed_str}[/red]")

    final_results = [r for r in results if r is not None]
    print_summary(final_results)

    # Write full run log
    _LOG_DIR.mkdir(exist_ok=True)
    log_path = _LOG_DIR / "last-decomm.log"
    with open(log_path, "w") as lf:
        lc = Console(file=lf, highlight=False, width=120, no_color=True)
        lc.print(f"Labinator Batch Decomm — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lc.print(f"Command: {' '.join(sys.argv)}")
        lc.print()
        for r in [results[i] for i in range(total) if results[i] is not None]:
            if "output" not in r:
                continue
            elapsed_str = fmt_elapsed(r["elapsed"]) if r["elapsed"] else "—"
            lc.print(f"{'─' * 10} {r['hostname']} {'─' * 10}")
            lc.print(r["output"], end="")
            if r["status"] == "ok":
                lc.print(f"✓ {r['hostname']} decomm'd in {elapsed_str}")
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
