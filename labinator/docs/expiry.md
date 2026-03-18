[← Back to README](../README.md)

# TTL and Auto-Expiry

### About

`expire.py` manages the lifecycle of temporary deployments. When a container or VM is deployed with `--ttl`, the deployment JSON gets an `expires_at` timestamp. `expire.py` reads those timestamps across all deployment files and lets you check what is expiring, decommission what has already expired, or extend the TTL of a deployment that needs more time.

The three modes are independent and each does one thing: `--check` reports, `--reap` destroys, `--renew` extends. No mode does anything you didn't ask for.

## Table of Contents

- [CLI Options](#cli-options)
- [--check](#--check)
- [--reap](#--reap)
- [--reap --purge](#--reap---purge)
- [--renew](#--renew)
- [--warning](#--warning)
- [--silent](#--silent)
- [TTL Format Reference](#ttl-format-reference)
- [How expires_at Works](#how-expires_at-works)

---

## CLI Options

The following command line options are available:

| Option | Description |
|---|---|
| `--check` | Print a table of expired and expiring-soon hosts. Default mode if no other mode flag is given. No Proxmox connection needed. |
| `--reap` | Connect to Proxmox and decommission all expired deployments. Full pipeline: stop+destroy, DNS, inventory. |
| `--renew HOSTNAME` | Extend the TTL of a deployment. Updates `ttl` and `expires_at` in the JSON file. Requires `--ttl`. |
| `--ttl TTL` | New TTL for `--renew` (e.g. `7d`, `24h`, `2w`, `30m`) |
| `--kind lxc\|vm` | Disambiguate `--renew` when both `deployments/lxc/` and `deployments/vms/` contain the same hostname |
| `--warning TTL` | How far ahead to flag as expiring-soon (default: `48h`). Accepts the same format as `--ttl`. |
| `--purge` | Delete deployment JSON files after successful reap. Requires `--reap`. |
| `--silent` | Skip the per-host confirmation challenge when reaping. Useful for automated pipelines. Be very careful with this, Blue Oyster Cult was not right-- you should definitely fear this reaper. |
| `--yolo` | Continue through warnings; blocked by failures (same semantics as deploy scripts) |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

---

## --check

Scans all deployment JSON files for an `expires_at` field and prints a table of anything expired or expiring within the warning window (default: 48 hours). No Proxmox connection is needed — this reads local files only and is safe to run at any time.

`--check` is the default mode: if you run `./expire.py` with no mode flag, it behaves as `--check`.

```bash
# Show expired and expiring-soon (default warning window: 48h)
./expire.py --check

# Change the warning window
./expire.py --check --warning 3d
```

```
┏━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Hostname    ┃ Type ┃ VMID    ┃ Node       ┃ TTL   ┃ Expires / Expired                    ┃ Status        ┃
┡━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ old-test    │ LXC  │ 111     │ proxmox01  │ 2d    │ 2026-03-01 00:00 UTC  (16d ago)      │ EXPIRED       │
│ staging-vm  │ VM   │ 113     │ proxmox01  │ 1d    │ 2026-03-17 02:40 UTC  (24h left)     │ expiring soon │
└─────────────┴──────┴─────────┴────────────┴───────┴──────────────────────────────────────┴───────────────┘

1 expired deployment(s).  Run ./expire.py --reap to decommission them.
1 deployment(s) expiring within 48h.  Run ./expire.py --renew HOSTNAME --ttl Xd to extend.
```

Expired entries are shown in red. Expiring-soon entries are shown in yellow. If nothing is expired or expiring, a single green line is printed instead of the table.

---

## --reap

Connects to Proxmox and decommissions every deployment whose `expires_at` is in the past. Runs the same full pipeline as `decomm_lxc.py` / `decomm_vm.py` for each host — stop and destroy in Proxmox, remove DNS records, remove from Ansible inventory.

Unless `--silent` is passed, a confirmation challenge is shown for each host before it is destroyed.

```bash
# Decommission all expired deployments (confirmation per host)
./expire.py --reap

# Reap without confirmation prompts
./expire.py --reap --silent
```

```
─── Decommissioning old-test (LXC 111 on proxmox01) ───
  Step 1/4: Destroying Proxmox container...
  ✓ Container 111 destroyed
  Step 2/4: Removing DNS records...
  ✓ DNS records removed
  Step 3/4: Removing from Ansible inventory...
  ✓ Removed from inventory
  Step 4/4: Deployment file...
  Deployment file kept: deployments/lxc/old-test.json

╔══════════════════════════════════╗
║  💀  Done                        ║
║                                  ║
║  Decommissioned:                 ║
║    ✓ old-test                    ║
╚══════════════════════════════════╝
```

If the Proxmox resource is already gone (stale JSON file), `--reap` proceeds with DNS and inventory cleanup and reports the host as **Already gone** rather than **Decommissioned** — so the true state is always clear.

---

## --reap --purge

By default, `--reap` leaves deployment JSON files in place after decommissioning — useful for reference or redeployment. Add `--purge` to delete them automatically after each successful reap.

```bash
# Reap and delete deployment JSON files
./expire.py --reap --purge

# Reap, delete JSON files, no confirmation prompts
./expire.py --reap --purge --silent
```

```
╔══════════════════════════════════╗
║  💀  Done                        ║
║                                  ║
║  Decommissioned:                 ║
║    ✓ old-test                    ║
║                                  ║
║  Deployment files deleted:       ║
║    ✓ old-test.json               ║
╚══════════════════════════════════╝
```

`--purge` only deletes files for hosts that were successfully decommissioned or confirmed already gone. Hosts that aborted (confirmation failed or an error occurred) keep their JSON files.

---

## --renew

Extends the TTL of an existing deployment by updating `ttl` and `expires_at` in the deployment JSON. The new expiry is calculated as `now() + TTL` — it is not added on top of the existing expiry. No Proxmox connection is needed.

```bash
# Extend by 7 days (searches lxc/ then vms/ automatically)
./expire.py --renew myserver --ttl 7d

# Extend specifically the LXC version when both lxc/ and vms/ have myserver.json
./expire.py --renew myserver --ttl 7d --kind lxc

# Extend specifically the VM version
./expire.py --renew myserver --ttl 7d --kind vm
```

```
  ✓ myserver renewed: 2026-03-13T14:22:00.000000+00:00 → 2026-03-20 14:22:00 UTC
```

Use `--kind` to disambiguate when both `deployments/lxc/` and `deployments/vms/` contain a file with the same hostname. Without `--kind`, `lxc/` is searched first, then `vms/`.

> **Note:** `--renew` can also be used to add a TTL to a deployment that was originally deployed without one — just run it against any deployment JSON that lacks an `expires_at` field.

```bash
# myserver was deployed without --ttl and has no expires_at — add one now
./expire.py --renew myserver --ttl 7d
```

```
  ✓ myserver renewed: (none) → 2026-03-24 14:22:00 UTC
```

---

## --warning

Controls how far ahead a deployment is flagged as expiring-soon in `--check` output. The default is `48h`. Accepts the same TTL format as `--ttl`.

```bash
./expire.py --check --warning 3d    # flag anything expiring in the next 3 days
./expire.py --check --warning 12h   # tighter window
```

Deployments outside the warning window are not shown in `--check` output at all — only expired and expiring-soon entries appear.

---

## --silent

Skips the per-host confirmation challenge when reaping. Useful for cron jobs or automated maintenance pipelines where human confirmation is not practical.

```bash
./expire.py --reap --silent
./expire.py --reap --purge --silent
```

> **Note:** `--silent` skips confirmation but does not suppress output. All step results, warnings, and the final summary panel are still printed.

---

## TTL Format Reference

All TTL arguments across `deploy_lxc.py`, `deploy_vm.py`, and `expire.py` use the same format:

| Unit | Meaning | Example |
|---|---|---|
| `m` | minutes | `30m` |
| `h` | hours | `24h` |
| `d` | days | `7d` |
| `w` | weeks | `2w` |

---

## How expires_at Works

When `--ttl` is passed to a deploy script, two fields are written to the deployment JSON:

```json
"ttl": "7d",
"expires_at": "2026-03-13T14:22:00.000000+00:00"
```

- `ttl` — the TTL string as entered (e.g. `7d`), stored for display and reference
- `expires_at` — ISO 8601 UTC timestamp calculated as `now() + TTL` at the moment of deployment

`expire.py` reads `expires_at` directly to determine status. Deployments without an `expires_at` field are ignored entirely — a deployment with no TTL will never appear in `--check` output and will never be reaped.

Setting a TTL at deploy time:
```bash
./deploy_lxc.py --deploy-file deployments/lxc/test-box.json --ttl 7d
./deploy_vm.py --deploy-file deployments/vms/staging-vm.json --ttl 24h
```

Adding or changing a TTL after deploy:
```bash
./expire.py --renew test-box --ttl 7d
```

---

[← Back to README](../README.md)
