[← Back to README](../README.md)

# Decommissioning Resources

### About

`decomm_lxc.py` and `decomm_vm.py` <u>**permanently**</u> destroy a Proxmox LXC container or QEMU VM and remove all associated records. Both scripts follow the same four-step process: destroy the resource in Proxmox, remove DNS records, remove from Ansible inventory, and handle the deployment JSON file.

These scripts are the counterpart to `deploy_lxc.py` and `deploy_vm.py`.

> **Note:** By design, and for safety, only resources that were deployed with a labinator deploy script (and have a corresponding deployment JSON file) can be decommissioned this way.

> **Warning!!!** Keep in mind that **This is irreversible.** Both scripts require a confirmation challenge before proceeding. Use `--silent` only in automated pipelines where you are certain of what you are destroying.

## Table of Contents

- [CLI Options](#cli-options)
- [Interactive Mode](#interactive-mode)
- [File-Based Mode](#file-based-mode)
- [--purge Behavior](#--purge-behavior)
- [--silent Behavior](#--silent-behavior)
- [Decommission Steps](#decommission-steps)
- [Batch Decommission](#batch-decommission)
- [Deployment Logs](#deployment-logs)
- [Example Scenarios](#example-scenarios)

---

## CLI Options

Both `decomm_lxc.py` and `decomm_vm.py` support the same options:

| Option | Description |
|---|---|
| `--deploy-file FILE` | Load deployment JSON directly, skipping the interactive list |
| `--purge` | Also delete the local deployment JSON file after decommissioning |
| `--silent` | Skip the confirmation challenge. Requires `--deploy-file`. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

---

## Interactive Mode

Run without arguments to select from a numbered list of deployed containers or VMs:

```bash
python3 decomm_lxc.py    # lists from deployments/lxc/
python3 decomm_vm.py     # lists from deployments/vms/
```

```
? Select container to decommission: (Use arrow keys)
 » myserver              node=proxmox03      ip=10.20.20.150     deployed=2026-03-06 14:22:00
   old-test              node=proxmox01      ip=10.20.20.111     deployed=2026-02-14 09:00:00
```

After selection, a full destruction warning panel is shown with container/VM details. The script flushes the keyboard buffer for 5 seconds, then requires typing a **random-caps challenge word** (e.g. `YeS`) — case-sensitive, different every run of the script. This is so you can't accidentally run it to completion without warnings or notification, unless you specify `--silent`.

```
╭──────────────────────────────────────────────────────────────╮
│  💀  DECOMMISSION WIZARD  💀                                 │
│                                                              │
│  💀  ⚠ WARNING: IRREVERSIBLE DESTRUCTION ⚠  💀             │
│                                                              │
│  You are about to PERMANENTLY DELETE:                        │
│                                                              │
│    Hostname : myserver                                       │
│    VMID     : 142  (on proxmox03)                            │
│    IP       : 10.20.20.150                                   │
│                                                              │
│  This will STOP and DESTROY the container,                   │
│  REMOVE its DNS records, and                                 │
│  DELETE it from the Ansible inventory.                       │
│                                                              │
│  There is NO undo.                                           │
│                                                              │
╰──────────────────────────────────────────────────────────────╯

Flushing keyboard buffer — please wait 5 seconds...

To confirm destruction of myserver, type exactly: yEs
(case-sensitive)

Type here:
```

---

## File-Based Mode

```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json
```

The `--deploy-file` option skips the numbered list and loads the specified file directly. The confirmation challenge still runs unless `--silent` is also passed.

---

## --purge Behavior

Without `--purge`, the deployment JSON file is left in place and its path is printed after decommissioning. This is intentional — a recently decommissioned host's JSON is useful for reference or redeployment.

```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json --purge
```

With `--purge`, the JSON file is deleted after successful decommissioning.

---

## --silent Behavior

```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --silent
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json --silent
```

Skips the confirmation challenge entirely. Requires `--deploy-file`. Use for scripted or automated decommissioning pipelines.

---

## Decommission Steps

Both scripts run the same four steps after confirmation:

**Step 1** — Stops the resource if running, then destroys it in Proxmox removing all associated disk volumes. If the resource is not found in Proxmox (already manually deleted), a warning is printed and the script continues with Steps 2–4 to clean up DNS and inventory.

```
─── Step 1/4: Destroying Proxmox container ───
  Stopping container 142 (myserver)...
  ✓ Container stopped
  Destroying container 142...
  ✓ Container 142 destroyed
```

If the container was already deleted manually in Proxmox:

```
─── Step 1/4: Destroying Proxmox container ───
  Container 999 not found on proxmoxb01 — may already be deleted.
```

> **Note:** If a container was already deleted manually in Proxmox, Steps 2–4 still run. This ensures DNS records and inventory entries are cleaned up even when the Proxmox resource is already gone — making decommission safe to run on orphaned deployment files.

> **Under The Hood**
> The Proxmox API delete call is made with `purge=1` and `destroy-unreferenced-disks=1` to ensure all disk volumes are removed even if they are not directly referenced by the resource config.

**Step 2** — Removes the DNS A record and PTR record for the host from the configured BIND server. Skipped silently if `dns.enabled` is `false` in `config.yaml`.

```
─── Step 2/4: Removing DNS records ───
  Removing DNS records for myserver (10.20.20.150)...
  ✓ DNS records removed
```

**Step 3** — Removes the host entry from the Ansible inventory file on the configured inventory server. Skipped silently if `ansible_inventory.enabled` is `false` in `config.yaml`.

```
─── Step 3/4: Removing from Ansible inventory ───
  Removing myserver from Ansible inventory on dev.example.com...
  ✓ Removed from inventory
```

**Step 4** — With `--purge`: deletes the deployment JSON file and prints the path. Without `--purge`: prints the path and reminds you to delete it manually if no longer needed.

```
─── Step 4/4: Deployment file ───
  Deployment file NOT deleted: deployments/lxc/myserver.json
  Run with --purge to delete it, or remove it manually.
```

```
╭──────────────────────────────────────────────────────────╮
│  💀  Done                                                │
│                                                          │
│  Decommission Complete                                   │
│                                                          │
│  myserver has been permanently destroyed.                │
│  Container deleted, DNS removed, inventory updated.      │
╰──────────────────────────────────────────────────────────╯
```
A history log entry is written to `deployments/history.log` on completion.

---

## Batch Decommission

To decommission multiple resources at once, use `decomm.py` instead of calling the individual scripts directly. `decomm.py` accepts the same deployment JSON files, auto-detects LXC vs VM, and runs `decomm_lxc.py` or `decomm_vm.py` in `--silent` mode for each file.

```bash
# Decommission specific hosts
python3 decomm.py --batch deployments/lxc/web01.json deployments/lxc/db01.json

# Decommission all JSON files in a directory
python3 decomm.py --batch-dir deployments/lxc/

# Decommission and delete the deployment files
python3 decomm.py --batch deployments/lxc/web01.json --purge
```

`decomm.py` defaults to **sequential execution** (`--parallel 1`) to avoid race conditions when multiple processes write to the same BIND DNS zone file simultaneously. Use `--parallel N` only if you understand the risk of stale DNS records.

See [docs/batch.md](batch.md) for full `decomm.py` documentation.

---

## Deployment Logs

Every standalone decomm run (non-`--silent`) writes a full log to `logs/last-decomm.log` in the project root. The log captures confirmation output, all four steps, and the final summary — with ANSI codes stripped.

```
logs/last-decomm.log
```

The log is overwritten on each run and is excluded from git.

The path is printed at the end of every decommission:

```
Log: /home/dad/projects/HomeLab/labinator/logs/last-decomm.log
```

> **Note:** `--silent` mode (used by `decomm.py` batch) does not write to `last-decomm.log` directly. `decomm.py` captures subprocess output and writes the combined log itself at the end of the batch run.

---

## Example Scenarios

**Decommission a container interactively:**
```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json
```
Shows the destruction warning panel, waits 5 seconds (keyboard buffer flush), then requires a case-sensitive random-caps challenge word before proceeding.

---

**Decommission and delete the deployment file in one step:**
```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge
```
After the four steps complete, the deployment JSON is deleted. Use when you are certain the host will not be redeployed.

---

**Decommission a resource that was already manually deleted in Proxmox:**
```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/orphan.json
```
If the container no longer exists in Proxmox, Step 1 prints a warning and continues. Steps 2–4 still run — cleaning up the DNS record and Ansible inventory entry. This is the correct way to clean up orphaned deployment files.

---

**Automated / scripted decommission:**
```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --silent
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json --silent --purge
```
Skips the confirmation challenge entirely. Used by `decomm.py` batch mode internally.

---

**Batch decommission multiple resources:**
```bash
python3 decomm.py --batch deployments/lxc/web01.json deployments/lxc/web02.json
```
See [batch.md](batch.md) for full `decomm.py` documentation.

---

[← Back to README](../README.md)
