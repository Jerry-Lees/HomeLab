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

[← Back to README](../README.md)
