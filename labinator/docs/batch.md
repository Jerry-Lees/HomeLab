[← Back to README](../README.md)

# Batch Deploy and Decommission

### About

`deploy.py` and `decomm.py` operate on multiple deployment JSON files at once, driving `deploy_lxc.py` / `deploy_vm.py` / `decomm_lxc.py` / `decomm_vm.py` as subprocesses in silent mode and printing a summary table on completion.

- **`deploy.py`** deploys up to `--parallel N` (default: 3) resources concurrently
- **`decomm.py`** decommissions resources sequentially by default to avoid DNS zone file race conditions

Both scripts auto-detect whether each file is an LXC or VM deployment from the JSON `type` field (or presence of `template_name`) and call the correct script automatically.

---

## Table of Contents

- [deploy.py](#deploypy)
  - [CLI Options](#cli-options-deploy)
  - [Selecting Files](#selecting-files)
  - [Parallel Mode](#parallel-mode)
  - [Validate Mode](#validate-mode)
  - [Idempotent Re-runs](#idempotent-re-runs)
  - [Examples](#examples-deploy)
- [decomm.py](#decommpy)
  - [CLI Options](#cli-options-decomm)
  - [Why Sequential by Default](#why-sequential-by-default)
  - [Examples](#examples-decomm)
- [Output Format](#output-format)

---

## deploy.py

### CLI Options {#cli-options-deploy}

| Option | Description |
|---|---|
| `--batch FILE [FILE ...]` | One or more deployment JSON files to deploy (mutually exclusive with `--batch-dir`) |
| `--batch-dir DIR` | Deploy all `*.json` files in a directory alphabetically |
| `--validate` | Validate all files and exit without deploying |
| `--parallel N` | Max concurrent deployments (default: 3, use 1 for sequential) |
| `--config FILE` | Alternate config file (default: `config.yaml` next to the script) |
| `--yolo` | Skip preflight checks in each deploy script |
| `--ttl DURATION` | Apply a TTL to all deployed resources (e.g. `7d`, `24h`) |

### Selecting Files

**Named files** — deploy specific files in the order given:
```bash
python3 deploy.py --batch deployments/lxc/web01.json deployments/vms/db01.json
```

**Directory** — deploy every `*.json` file in a directory, sorted alphabetically:
```bash
python3 deploy.py --batch-dir deployments/lxc/
```

> **Note:** `--batch-dir` will pick up every JSON in the directory, including `example-*.json` files. Use `--batch` with explicit file names for a curated set.

### Parallel Mode

By default, up to 3 deployments run concurrently (`--parallel 3`). The first 3 jobs start immediately; additional jobs queue and start as slots free up.

```bash
# Default: 3 concurrent
python3 deploy.py --batch deployments/lxc/web01.json deployments/lxc/web02.json deployments/lxc/db01.json

# Run 5 at a time
python3 deploy.py --batch-dir deployments/lxc/ --parallel 5

# Sequential (useful for debugging or when DNS issues arise)
python3 deploy.py --batch-dir deployments/lxc/ --parallel 1
```

When running in parallel, each output line is prefixed with `[hostname]` so you can follow multiple deployments in the interleaved stream:

```
[web01]  ─── Step 1/7: Preflight checks ───
[db01]   ─── Step 1/7: Preflight checks ───
[web01]  ✓ All preflight checks passed.
[db01]   ✓ All preflight checks passed.
[web01]  ─── Step 2/7: Creating LXC container ───
...
[web01]  ✓ web01 done in 5m 42s
```

Each deployment runs the full deploy script in `--silent` mode, which suppresses interactive prompts and reads all configuration from the deployment JSON file.

### Validate Mode

Check all deployment files for errors without deploying anything:

```bash
python3 deploy.py --batch-dir deployments/lxc/ --validate
```

```
╭──────────────────────────╮
│ Labinator Batch Validate │
╰──────────────────────────╯

✓ config.yaml  OK

✓ web01.json  (web01 / lxc)
✓ web02.json  (web02 / lxc)
✗ broken.json  (broken)
  → Missing required field: hostname
  → Missing required field: storage

All 3 file(s): 1 invalid.
```

Exits 0 if all files pass, 1 if any fail.

### Idempotent Re-runs

Before deploying each file, `deploy.py` fetches all currently running VMIDs from the cluster. If the VMID in a deployment file is already running, that file is skipped with a `skipped` status in the summary table. This makes batch re-runs safe — already-deployed resources are left alone.

```
│ web01  │ lxc  │  skipped  │    —    │   ← already running, skipped
│ web02  │ lxc  │     ✓     │ 5m 42s  │   ← deployed
```

> This only applies when the deployment file contains a `vmid` field. Files without a `vmid` will always attempt to deploy and get a new VMID assigned.

### Examples {#examples-deploy}

```bash
# Deploy two specific hosts
python3 deploy.py --batch deployments/lxc/web01.json deployments/lxc/db01.json

# Deploy a whole directory with validation first
python3 deploy.py --batch-dir deployments/lxc/ --validate
python3 deploy.py --batch-dir deployments/lxc/

# Deploy with a TTL so hosts expire automatically
python3 deploy.py --batch-dir deployments/lxc/ --ttl 7d

# Deploy without preflight checks (faster, for trusted environments)
python3 deploy.py --batch-dir deployments/lxc/ --yolo

# Sequential deploy (one at a time)
python3 deploy.py --batch-dir deployments/lxc/ --parallel 1
```

---

## decomm.py

### CLI Options {#cli-options-decomm}

| Option | Description |
|---|---|
| `--batch FILE [FILE ...]` | One or more deployment JSON files to decommission |
| `--batch-dir DIR` | Decommission all `*.json` files in a directory alphabetically |
| `--parallel N` | Max concurrent decomms (default: 1 — sequential) |
| `--config FILE` | Alternate config file |
| `--purge` | Delete deployment JSON files after decommissioning |

### Why Sequential by Default

`decomm.py` defaults to `--parallel 1` (sequential). This is intentional.

When multiple decommission processes run in parallel, they all run Ansible against the same BIND DNS zone file at the same time. Concurrent writes to the zone file can cause records to silently not be removed — leaving stale DNS entries that block future deployments.

Sequential decomm eliminates this race entirely. The time cost is low: container/VM destruction is fast (seconds), and the Ansible DNS + inventory cleanup runs one at a time without conflicts.

```bash
# Sequential (default — recommended)
python3 decomm.py --batch deployments/lxc/web01.json deployments/lxc/web02.json

# Parallel — faster, but DNS records may race
python3 decomm.py --batch-dir deployments/lxc/ --parallel 3
```

> **Note:** If you use `--parallel > 1` and notice stale DNS records after a batch decomm, re-run the decomm sequentially for the affected hosts, or clean up the records manually with `python3 decomm_lxc.py --deploy-file <file> --silent`.

### Examples {#examples-decomm}

```bash
# Decommission specific hosts
python3 decomm.py --batch deployments/lxc/web01.json deployments/lxc/web02.json

# Decommission all LXC containers in a directory
python3 decomm.py --batch-dir deployments/lxc/

# Decommission and delete the deployment files
python3 decomm.py --batch deployments/lxc/web01.json deployments/lxc/web02.json --purge
```

---

## Output Format

Both scripts print a summary table after all jobs complete. Results are always shown in original file order, regardless of completion order.

```
           Batch Deploy Results
┏━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Hostname     ┃ Type ┃ Result ┃ Elapsed ┃
┡━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ web01        │ lxc  │   ✓    │  5m 42s │
│ web02        │ lxc  │   ✓    │  5m 38s │
│ db01         │ lxc  │   ✓    │  5m 51s │
│ middleware01 │ lxc  │   ✗    │  0m 22s │
└──────────────┴──────┴────────┴─────────┘
  3 deployed   0 skipped   1 failed
```

Exit code is 0 if all jobs succeeded, 1 if any failed. This makes both scripts suitable for use in CI pipelines.

---

[← Back to README](../README.md)
