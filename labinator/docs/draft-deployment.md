[← Back to README](../README.md)

# Building Draft Deployment Files

### About

`draft-deployment.py` is an interactive wizard that builds a labinator deployment JSON file without actually deploying anything. It runs the same wizard prompts as `deploy_lxc.py` and `deploy_vm.py` — node selection, template or cloud image browsing, storage, IP addressing, package profiles, LXC features, and TTL — and saves the result to `deployments/lxc/<hostname>.json` or `deployments/vms/<hostname>.json`.

Use it to:
- Plan a deployment before committing to it
- Build a deployment file to hand off to `deploy.py` for batch execution
- Edit an existing deployment file interactively without redeploying
- Pre-build files for environments where interactive deployment isn't practical

## Table of Contents

- [CLI Options](#cli-options)
- [Basic Usage](#basic-usage)
- [Choosing LXC or VM](#choosing-lxc-or-vm)
- [Loading an Existing File](#loading-an-existing-file)
- [TTL / Expiry](#ttl--expiry)
- [Wizard Navigation](#wizard-navigation)
- [Walkthrough: LXC Prompt Order](#walkthrough-lxc-prompt-order)
- [Walkthrough: VM Prompt Order](#walkthrough-vm-prompt-order)
- [Output File](#output-file)
- [Deploying from a Draft](#deploying-from-a-draft)
- [Example Scenarios](#example-scenarios)

---

## CLI Options

| Option | Description |
|---|---|
| `--lxc` | Build an LXC container deployment file |
| `--vm` | Build a VM (cloud-init) deployment file |
| `--deploy-file FILE` | Load an existing deployment JSON as a starting point |
| `--ttl TTL` | Set a TTL for this deployment (e.g. `7d`, `24h`, `2w`). Stored in `expires_at`. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` |

---

## Basic Usage

```bash
python3 draft-deployment.py
```

Prompts you to choose LXC or VM, then runs the full wizard and saves the resulting JSON file. No containers or VMs are created — only the file is written.

---

## Choosing LXC or VM

Without flags, the wizard asks at startup:

```
? Draft for:
  > LXC container
    VM (cloud-init)
```

Pass `--lxc` or `--vm` to skip this prompt:

```bash
python3 draft-deployment.py --lxc
python3 draft-deployment.py --vm
```

When loading an existing file with `--deploy-file`, the type is auto-detected from the file contents (`type: vm` key or presence of `template_volid`) and the prompt is skipped.

---

## Loading an Existing File

```bash
python3 draft-deployment.py --lxc --deploy-file deployments/lxc/myserver.json
```

Loads an existing deployment JSON and pre-fills all prompts with its values. You can review and change any field before saving. The existing file is overwritten with the updated values when you confirm.

This is the recommended way to edit a draft — it lets you step through every field and make targeted changes without touching the file directly.

---

## TTL / Expiry

```bash
python3 draft-deployment.py --lxc --ttl 7d
```

Stores `ttl` and `expires_at` fields in the saved JSON. The deployment can then be managed with `expire.py` after it is deployed.

| Format | Meaning |
|---|---|
| `7d` | 7 days |
| `24h` | 24 hours |
| `2w` | 2 weeks |
| `30m` | 30 minutes |

See [docs/expiry.md](expiry.md) for full TTL documentation.

---

## Wizard Navigation

The wizard supports back-navigation at every prompt — you can move backward through the wizard to review or change an earlier answer without starting over.

| Prompt type | How to go back |
|---|---|
| Text input | Press **ESC** |
| Selection list | Arrow up to **← Go Back** and press **Enter** |
| Checkbox | Press **ESC** |

Going back restores the value you previously entered for that step. ESC at the first prompt exits with `Aborted.` Ctrl+C exits immediately from any prompt.

---

## Walkthrough: LXC Prompt Order

1. **Hostname** — short name only; FQDN is assembled from config
2. **vCPUs** — number of virtual CPUs
3. **Memory (GB)** — RAM allocated to the container
4. **Disk size (GB)** — root disk size
5. **VLAN tag** — bridges as `vmbr0.<vlan>` (or the bridge from config)
6. **Password** — root and secondary user password
7. **IP address** — leave blank for DHCP, or enter a static IPv4 address
8. **Prefix length** — subnet mask bits (skipped if DHCP)
9. **Gateway** — default gateway (skipped if DHCP; derived automatically from IP if not set)
10. **Package profile** — optional named group of packages from `config.yaml`
11. **Extra packages** — freeform list of additional packages to install
12. **LXC feature flags** — nesting, keyctl, fuse, mknod, NFS/CIFS mounts (checkbox)
13. **Node** — Proxmox node to target; shows CPU and RAM usage to aid selection
14. **OS template** — select from downloaded templates or download a new one from the Proxmox repo
15. **Storage pool** — where the container root disk lives
16. **Confirm** — shows a full summary and asks `Save draft deployment file?`

---

## Walkthrough: VM Prompt Order

1. **Hostname** — short name only
2. **vCPUs** — number of virtual CPUs
3. **Memory (GB)** — RAM allocated to the VM
4. **Disk size (GB)** — root disk size
5. **VLAN tag** — bridges as `vmbr0.<vlan>`
6. **Password** — root and secondary user password
7. **Package profile** — optional named group of packages
8. **Extra packages** — freeform list of additional packages
9. **IP address** — leave blank for DHCP, or enter a static IPv4 address
10. **Prefix length** — subnet mask bits (skipped if DHCP)
11. **Gateway** — default gateway (skipped if DHCP)
12. **Node** — Proxmox node to target
13. **Cloud image** — two-level browser: storage → image file; download from catalog if needed
14. **Storage pool** — where the VM disk lives
15. **Confirm** — shows a full summary and asks `Save draft deployment file?`

---

## Output File

The wizard saves to:

```
deployments/lxc/<hostname>.json   # for LXC containers
deployments/vms/<hostname>.json   # for VMs
```

The file does **not** include `vmid` or `deployed_at` — those are assigned and recorded at actual deploy time. All other fields needed for a full deployment are present.

After saving, the wizard prints the exact command to deploy it:

```
╭──────────────────────────────────────╮
│ ✓ Draft saved                        │
│                                      │
│   deployments/lxc/myserver.json      │
│                                      │
│   Deploy with:                       │
│   python3 deploy_lxc.py \            │
│     --deploy-file deployments/...    │
╰──────────────────────────────────────╯
```

---

## Deploying from a Draft

Once a draft file exists, deploy it with:

```bash
# Interactive — pre-fills all prompts from the file, you confirm each
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json

# Silent — deploys immediately with no prompts
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent

# Batch — deploy multiple drafts in parallel
python3 deploy.py --batch deployments/lxc/web1.json deployments/lxc/db1.json
```

See [docs/deploy-lxc.md](deploy-lxc.md), [docs/deploy-vm.md](deploy-vm.md), and [docs/batch.md](batch.md) for full deployment documentation.

---

## Example Scenarios

**Build a new LXC draft from scratch:**
```bash
python3 draft-deployment.py --lxc
```

**Build a VM draft with a 14-day TTL:**
```bash
python3 draft-deployment.py --vm --ttl 14d
```

**Edit an existing LXC draft to change the node and storage:**
```bash
python3 draft-deployment.py --lxc --deploy-file deployments/lxc/myserver.json
```

**Build a draft and immediately deploy it silently:**
```bash
python3 draft-deployment.py --lxc
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
```

**Pre-build a batch of deployment files, then deploy them all in parallel:**
```bash
python3 draft-deployment.py --lxc   # saves deployments/lxc/web1.json
python3 draft-deployment.py --lxc   # saves deployments/lxc/web2.json
python3 draft-deployment.py --vm    # saves deployments/vms/db1.json

python3 deploy.py --batch \
  deployments/lxc/web1.json \
  deployments/lxc/web2.json \
  deployments/vms/db1.json \
  --parallel 3
```
