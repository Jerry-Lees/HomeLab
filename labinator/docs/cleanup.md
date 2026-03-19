[← Back to README](../README.md)

# Cleanup Tagged Resources

### About
`cleanup_tagged.py` is a bulk lifecycle management tool. Where the deploy and decommission scripts operate on one resource at a time, `cleanup_tagged.py` operates on a fleet — scanning the entire cluster for resources carrying a specific Proxmox tag and letting you act on all of them at once.

The primary use cases are:

- **Periodic cleanup** — scan for everything tagged `auto-deploy` after a sprint or test cycle and decommission whatever is no longer needed.
- **Batch promotion** — remove the `auto-deploy` tag from resources that have proven themselves and should be kept permanently, without decommissioning them.
- **Audit / inventory** — run with `--dry-run` to see every tagged resource across all nodes in one table, with live status from the Proxmox API.
- **Automated pipelines** — use `--list-file` with `--silent` to drive cleanup from a pre-built action list with no interactive prompts, suitable for cron or CI.

Each resource gets one of four actions:

- `decomm` — stop, destroy, remove DNS and inventory
- `keep` — do nothing
- `promote` — remove the tag so it no longer appears in future scans
- `retag` — replace the scanned tag with a different tag (use with `--retag-as`)

Actions can be assigned interactively one by one, or pre-defined in a JSON list file.

## Table of Contents

- [CLI Options](#cli-options)
- [Tag-Based Cleanup](#tag-based-cleanup)
- [--list-file Usage](#--list-file-usage)
- [--plan Flag](#--plan-flag)
- [Example List-File JSON Format](#example-list-file-json-format)
- [--action Options](#--action-options)
- [--dry-run](#--dry-run)
- [IP Resolution Order](#ip-resolution-order)
- [Summary Panel](#summary-panel)
- [examples/ Directory](#examples-directory)

---

## CLI Options

The following command line options are available:

| Flag | Description |
|---|---|
| `--tag TAG` | Proxmox tag to scan for. Default: `auto-deploy`. Alphanumeric, hyphens, underscores, and dots only; max 64 chars. Validated at startup. |
| `--list-file FILE` | Load a pre-built JSON action list. See [--list-file Usage](#--list-file-usage). When used with `--plan`, specifies the output path instead. |
| `--silent` | Skip interactive prompts and the confirmation challenge. Requires `--list-file`. |
| `--dry-run` | Print the resource table and exit. No changes made. |
| `--plan` | Scan tagged resources and write a pre-populated list-file (all `keep`) then exit. See [--plan Flag](#--plan-flag). Mutually exclusive with `--dry-run` and `--silent`. |
| `--retag-as TAG` | New tag to apply when action is `retag`. Required if any resource uses the `retag` action (interactive or list-file). |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

---

## Tag-Based Cleanup

`cleanup_tagged.py` scans **every node in the cluster** for VMs and LXC containers carrying a given Proxmox tag (default: `auto-deploy`) and lets you decide what to do with each one — interactively, or via a pre-built action list file.

```bash
python3 cleanup_tagged.py                                          # interactive scan
python3 cleanup_tagged.py --dry-run                                # list resources and exit
python3 cleanup_tagged.py --tag my-custom-tag                      # scan for a different tag
python3 cleanup_tagged.py --list-file cleanup-plan.json            # load actions from file
python3 cleanup_tagged.py --list-file cleanup-plan.json --silent   # run unattended
```

After displaying the resource table, the script prompts for each resource individually (unless `--list-file` is used). Decomm operations are queued and confirmed one at a time after the action selection pass completes.

```
              Resources tagged 'auto-deploy'
┏━━━━┳━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ #  ┃ Hostname    ┃ Type  ┃  VMID   ┃ Node       ┃ Status  ┃ IP            ┃
┡━━━━╇━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│  1 │ test-lxc    │ LXC   │  111    │ proxmox01  │ running │ 10.20.20.111  │
│  2 │ staging-web │ VM    │  200    │ proxmox03  │ running │ 10.20.20.200  │
│  3 │ old-db      │ VM    │  201    │ proxmox01  │ stopped │ 10.20.20.201  │
└────┴─────────────┴───────┴─────────┴────────────┴─────────┴───────────────┘

old-db  VM  vmid=201  node=proxmox01  ip=10.20.20.201
? What do you want to do with old-db? (Use arrow keys)
 » Keep    — leave it alone, come back later
   Promote — remove the tag (it's prod now)
   Retag   — replace 'auto-deploy' with 'production'
   Decomm  — permanently destroy it
```

---

## --list-file Usage

The `--list-file` flag accepts a JSON file that pre-specifies an action for each resource. This separates the decision of what to do from the execution — review and approve the plan file, then hand it to `--silent` to run unattended.

**Typical workflow:**
```bash
# 1. See what's tagged (or generate a plan file automatically — see --plan below)
./cleanup_tagged.py --dry-run

# 2. Build a plan file based on the output
vim cleanup-plan.json

# 3. Dry-run with the plan to verify actions
./cleanup_tagged.py --list-file cleanup-plan.json --dry-run

# 4. Execute with per-resource confirmation challenge
./cleanup_tagged.py --list-file cleanup-plan.json

# 5. Or execute fully unattended
./cleanup_tagged.py --list-file cleanup-plan.json --silent
```

When a list file is loaded, the planned action for each resource is shown before anything runs:

```
Actions loaded from: cleanup-plan.json

  keep      test-lxc     LXC  vmid=111
  promote   staging-web  VM   vmid=200
  decomm    old-db       VM   vmid=201
```

---

## --plan Flag

`--plan` automates step 2 of the workflow above — instead of manually writing a list file from scratch, it scans the cluster and writes one for you with every resource set to `keep`. You then edit it and pass it back to `--list-file`.

```bash
./cleanup_tagged.py --plan                              # writes cleanup-plan.json in cwd
./cleanup_tagged.py --plan --list-file my-plan.json    # writes to a specific path instead
./cleanup_tagged.py --plan --tag my-custom-tag         # scan a different tag
```

After writing, the script prints the full path of the saved file and exits — it never proceeds to action selection or execution.

```
✓ Plan written (3 resource(s)): /home/user/projects/labinator/cleanup-plan.json
Edit the file, then run:
  ./cleanup_tagged.py --list-file cleanup-plan.json
```

The generated file looks like:
```json
[
  {"hostname": "test-lxc", "vmid": "111", "action": "keep"},
  {"hostname": "staging-web", "vmid": "200", "action": "keep"},
  {"hostname": "old-db", "vmid": "201", "action": "keep"}
]
```

Edit the `action` field for each resource to `decomm`, `promote`, `retag`, or leave it as `keep`, then run the file back through `--list-file`.

> **Note:** `--plan` is mutually exclusive with `--dry-run` and `--silent`.

---

## Example List-File JSON Format

Each entry in the file specifies a hostname and an action. An optional `vmid` disambiguates resources that share the same hostname.

```json
[
  {"hostname": "test-lxc",    "action": "keep"},
  {"hostname": "staging-web", "action": "promote"},
  {"hostname": "old-db",      "vmid": "123", "action": "decomm"},
  {"hostname": "dev-worker",  "action": "retag"}
]
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `hostname` | Yes | Must match the resource name as reported by Proxmox |
| `action` | Yes | One of `keep`, `promote`, `decomm`, or `retag` |
| `vmid` | No | Used to disambiguate when two resources share the same hostname |

> **Note:** When any entry uses `action: retag`, `--retag-as TAG` must be passed on the command line. All `retag` entries in the file are renamed to the same target tag.

**Matching:** Resources are matched by `hostname:vmid` first, then `hostname` alone. Resources in the cluster that have no matching entry in the file default to `keep`. List file entries that don't match any cluster resource produce a warning and are skipped — they do not cause an error.

The `examples/` directory in the project root contains ready-to-use list files for common scenarios. See [examples/ Directory](#examples-directory) below.

---

## --action Options

The four possible values for the `action` field and their effects:

| Action | Effect |
|---|---|
| **Keep** | Leave it alone. No changes. Appears in the summary as Kept. |
| **Promote** | Removes the matched tag from the resource in Proxmox. The resource stays running and is no longer flagged as temporary. |
| **Retag** | Replaces the scanned tag with the tag specified by `--retag-as`. All other tags on the resource are preserved. Requires `--retag-as TAG` on the command line. |
| **Decomm** | Permanently destroys the resource. Runs the same full destruction sequence as `decomm_lxc.py` / `decomm_vm.py`: stops and destroys in Proxmox (purging all disks), removes DNS A + PTR records, removes from Ansible inventory. |

---

## --dry-run

Prints the resource table and exits. No changes are made. When combined with `--list-file`, the table shows which action would be applied to each resource.

```bash
python3 cleanup_tagged.py --dry-run
python3 cleanup_tagged.py --list-file cleanup-plan.json --dry-run
```

---

## IP Resolution Order

For each tagged resource, the script resolves the IP using these sources in order, stopping at the first hit:

1. **Proxmox config** — static IP from the resource's `ipconfig0` / `net0` config key
2. **Deployment JSON** — `assigned_ip` or `ip_address` from the local `deployments/lxc/` or `deployments/vms/` file
3. **Proxmox live interfaces API** — queries the running guest directly (requires qemu-guest-agent for VMs)
4. **DNS lookup** — tries the configured DNS server first (`dns.server`), then falls back to the system resolver

If none of these resolve, IP is shown as `unknown/DHCP` in the table.

---

## Summary Panel

After all actions complete, a summary panel is printed with six possible outcomes/states:

- **Decommissioned** — fully destroyed (stopped, disks purged, DNS removed, inventory cleaned)
- **Already gone** — Proxmox resource not found; DNS and inventory were still cleaned up
- **Promoted to production** — tag removed, resource kept running
- **Retagged** — scanned tag replaced with the new tag specified by `--retag-as`
- **Kept (no changes)** — skipped
- **Aborted** — confirmation failed or an error occurred during destruction

The **Already gone** bucket handles stale JSON files gracefully — DNS and inventory are still cleaned up, if they existed, and it's reported separately so it doesn't look like a successful decommission.

```
╭──────────────────────────────────╮
│  💀  Done                        │
│                                  │
│  Decommissioned:                 │
│    ✓ old-db                      │
│                                  │
│  Promoted to production:         │
│    ✓ staging-web                 │
│                                  │
│  Kept (no changes):              │
│    - test-lxc                    │
╰──────────────────────────────────╯
```

---

## examples/ Directory

The `examples/` directory contains ready-to-use list files for `cleanup_tagged.py --list-file`. These serve as templates and documentation for the list-file's use and format.

| File | Description |
|---|---|
| `list-file_keep-all.json` | Assign `keep` to every listed resource. Use when you want to scan but not touch anything. |
| `list-file_decomm-all.json` | Assign `decomm` to every listed resource. Appropriate for end-of-sprint teardown when everything is temporary. |
| `list-file_promote-all.json` | Assign `promote` to every listed resource — removes the `auto-deploy` tag from all, flagging them as permanent. |
| `list-file_mixed.json` | Mix of `decomm`, `promote`, and `keep`. The typical real-world case where some hosts graduate to production and others are torn down. |
| `list-file_retag.json` | All resources set to `retag`. Use with `--retag-as` to bulk-rename a tag across multiple resources. |
| `list-file_duplicate-hostname.json` | Demonstrates disambiguating two resources that share the same hostname by including `vmid` in the entry. |
| `list-file_ghost-host.json` | Shows graceful handling of a hostname that appears in the file but no longer exists in the cluster. The entry produces a warning and is skipped without error. |
| `list-file_invalid-action.json` | Demonstrates the validation error produced when an unsupported action string (e.g. `"nuke"`) is used. The script exits with an error before touching anything. |

All example files reference placeholder hostnames (`test-lxc-01`, `test-vm-01`, `staging-web`, etc.). Copy and edit them for your own use.

---

[← Back to README](../README.md)
