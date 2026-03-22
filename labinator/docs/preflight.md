[← Back to README](../README.md)

# Preflight Checks

### About

Both deploy scripts run a preflight check suite automatically at the start of every deployment — **before** any prompts are shown or Proxmox resources are created. Preflight verifies that your environment is ready: Proxmox is reachable and authenticated, SSH keys are in place, Ansible and its dependencies are installed, and DNS and inventory servers are accessible. Catching these issues before a deploy begins prevents half-finished deployments and wasted time.

If all checks pass, a single `✓ Preflight checks passed.` line is shown and the deploy continues. If any check fails or warns, the full results table is shown.

## Table of Contents

- [Checks Performed](#checks-performed)
- [What the Output Looks Like](#what-the-output-looks-like)
- [Fatal vs Warning](#fatal-vs-warning)
- [Running Standalone](#running-standalone)
- [Disabling Preflight](#disabling-preflight)
- [--yolo](#--yolo)
- [--silent Behavior with Preflight](#--silent-behavior-with-preflight)
- [Flag Interaction Table](#flag-interaction-table)

---

## Checks Performed

The following checks are run at the start of every deployment. Checks that depend on optional integrations are skipped automatically if that integration is disabled in `config.yaml`.

| Check | Fatal? | What it verifies |
|---|---|---|
| Config valid | Yes | `config.yaml` parses without errors and all required fields are present |
| Proxmox API reachable | Yes | TCP connect to port 8006 on each host under `proxmox:`. Reports `X/Y host(s)` with names of unreachable hosts. |
| Proxmox API auth | Yes | API token is accepted by the Proxmox API |
| SSH key on disk | Warning | The `ssh_key` path (under `proxmox:`) exists on disk |
| Proxmox node SSH | Warning | SSH key is accepted by each node in the `nodes:` list. Reports `X/Y node(s)` with names of failing nodes. |
| Ansible installed | Yes | `ansible-playbook` is on PATH. Skipped if `enabled` is `false` under `ansible:`. |
| sshpass installed (LXC) | Yes | `sshpass` is on PATH. LXC deploy only. |
| DNS server reachable | Warning | TCP connect to port 22 on the `server` (under `dns:`). Skipped if `enabled` is `false` under `dns:`. |
| DNS server SSH auth | Warning | Key-based SSH to the DNS server succeeds |
| DNS hostname check | Warning | If `--deploy-file` provided: queries the DNS server directly for the hostname. If a record exists and the IP **does not respond to ping** (stale orphan), the record is **auto-removed** and the check passes. If the IP is alive (real conflict), the check warns. |
| Static IP in use | **Fatal** | If `--deploy-file` provided and `ip_address` is a static IP: pings the IP and **fails if it responds** (duplicate IP prevention). Skipped entirely for DHCP deployments (`ip_address: "dhcp"`) — the IP is assigned at boot and checking it in advance is not meaningful. |
| Inventory server reachable | Warning | TCP connect to port 22 on the `server` (under `ansible_inventory:`). Skipped if `enabled` is `false` under `ansible_inventory:`. |
| Inventory SSH auth | Warning | Key-based SSH to the inventory server succeeds |

---

## What the Output Looks Like

When all checks pass, only a summary line is shown and the deploy continues immediately:

```
✓ Preflight checks passed.
```

When `--preflight` is run standalone, or when any check warns or fails, the full table is shown:

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                       ┃ Status  ┃ Detail                             ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Config valid                │ ✓ pass  │                                    │
│ Proxmox API reachable       │ ✓ pass  │ 3/3 host(s) on :8006               │
│ Proxmox API auth            │ ✓ pass  │                                    │
│ SSH key on disk             │ ✓ pass  │ ~/.ssh/id_rsa                       │
│ Proxmox node SSH            │ ✓ pass  │ 3/3 node(s)                        │
│ Ansible installed           │ ✓ pass  │                                    │
│ sshpass installed           │ ✓ pass  │                                    │
│ DNS server reachable        │ ✓ pass  │ 10.0.0.10:22                       │
│ DNS server SSH auth         │ ✓ pass  │                                    │
│ Inventory server reachable  │ ✓ pass  │ dev.example.com:22                 │
│ Inventory SSH auth          │ ✓ pass  │                                    │
└─────────────────────────────┴─────────┴────────────────────────────────────┘
✓ Preflight checks passed.
```

Warning and fatal rows look like this:

```
│ DNS server reachable        │ ⚠ warn  │ 10.0.0.10:22 unreachable           │
│ Static IP in use            │ ✗ FATAL │ 10.20.20.150 already responds      │
```

After a warning, the deploy pauses with a **Continue / Retry / Abort** prompt (unless `--silent` or `--yolo` is active). After a fatal failure, the deploy cannot proceed.

---

## Fatal vs Warning

**Fatal failures** block the deploy entirely. The deploy cannot proceed until the issue is resolved — or preflight is disabled for that deployment (see [Disabling Preflight](#disabling-preflight)).

**Warning-level checks** print a yellow `⚠ warn` row but allow the deploy to proceed after the Continue / Retry / Abort prompt. In `--silent` mode, any issue — warning or fatal — causes immediate exit 1.

---

## Running Standalone

Run preflight without deploying to check that your environment is ready before committing to a deployment. Without `--deploy-file`, only infrastructure checks run (Proxmox, SSH, Ansible, DNS, inventory). With `--deploy-file`, the DNS hostname check and static IP duplicate check are also run against the specific host being deployed.

```bash
# Check your environment is ready
./deploy_lxc.py --preflight

# Check environment + verify a specific deploy file
./deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json

# Script-friendly: exit 0 = all clear, exit 1 = any issue
./deploy_lxc.py --preflight --silent

# Exit 0 on warnings, exit 1 only on fatal failures
./deploy_lxc.py --preflight --yolo
```

---

## Disabling Preflight

To skip all preflight checks for a specific deployment, add `"preflight": false` to the deployment JSON:

```json
{
  "hostname": "myserver",
  "preflight": false
}
```

This is useful for hosts that are intentionally replacing an existing server (where the DNS record and IP will already be in use) or in automation where you have pre-validated externally.

To disable preflight globally for all deployments, set it in `config.yaml`:

```yaml
preflight: false   # Skip preflight checks for all deployments
```

---

## --yolo

```bash
./deploy_lxc.py --yolo
./deploy_vm.py --deploy-file deployments/vms/myvm.json --yolo
```

Runs preflight but continues through **warnings** without prompting. Fatal failures still block the deploy. Useful for environments where some warning-level checks are expected to fail (e.g. a DNS server that is temporarily unreachable).

> **Note:** This can be dangerous. Think through your choices in life.

---

## --silent Behavior with Preflight

In `--silent` mode, any preflight issue — warning or fatal — causes immediate exit 1. There is no prompt to continue or retry.

To allow warnings through in silent or automated mode, combine `--silent` with `--yolo`:

```bash
./deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent --yolo
```

With `--silent --yolo`, warnings are ignored and only fatal failures cause exit 1.

---

## Flag Interaction Table

This table summarizes how each flag combination affects preflight behavior:

| Flags | Warnings | Fatal failures |
|---|---|---|
| _(none)_ | Continue / Retry / Abort prompt | Continue / Retry / Abort prompt |
| `--yolo` | Continue silently | Continue / Retry / Abort prompt |
| `--silent` | Exit 1 | Exit 1 |
| `--silent --yolo` | Continue silently | Exit 1 |
| `"preflight": false` in deploy file | Skipped entirely | Skipped entirely |

---

[← Back to README](../README.md)
