# labinator

A command-line wizard for provisioning, configuring, and onboarding LXC containers and QEMU virtual machines in a Proxmox VE homelab cluster. Handles the full lifecycle from resource creation through post-deployment configuration, DNS registration, Ansible inventory registration, TTL-based auto-expiry, and batch cleanup — all from a single guided session or a pre-built deployment file. Companion decommission scripts reverse the process cleanly.

> **Disclaimer:** This tool is provided **as-is**, without warranty or support of any kind. It was built for a specific homelab environment and is shared for reference and reuse. See [Submitting an Issue](#submitting-an-issue) if you encounter a problem.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Project Layout](#project-layout)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Documentation](#documentation)
- [Quick Start](#quick-start)
- [Submitting an Issue](#submitting-an-issue)
- [known-bugs.md](#known-bugsmd)

---

## What It Does

labinator manages the complete lifecycle of Proxmox resources across eight scripts:

- **`configure.py`** — interactive wizard to build or edit `config.yaml` with per-field hints, autocomplete timezone picker, and immediate validation
- **`deploy_lxc.py`** — interactive wizard to fully provision and onboard an LXC container (create, bootstrap SSH, run Ansible, register DNS and inventory); supports both DHCP and static IP addressing; writes a full deployment log to `logs/last-deployment.log`
- **`decomm_lxc.py`** — permanently destroy a container and remove all associated records; writes a full decommission log to `logs/last-decomm.log`
- **`deploy_vm.py`** — interactive wizard to provision and onboard a QEMU VM via cloud-init with multi-OS Ansible post-deploy; writes a full deployment log to `logs/last-deployment.log`
- **`decomm_vm.py`** — permanently destroy a VM and remove all associated records; writes a full decommission log to `logs/last-decomm.log`
- **`deploy.py`** — batch deploy multiple LXC containers and/or VMs in parallel from deployment JSON files, with a live per-host status board (showing target node), summary table, and idempotent re-run protection
- **`decomm.py`** — batch decommission multiple resources from deployment JSON files, sequentially by default to avoid DNS race conditions
- **`cleanup_tagged.py`** — scan the cluster for tagged resources and keep, promote, or decommission each one interactively or via a plan file
- **`expire.py`** — manage deployment TTLs: check, reap expired hosts, or renew a deployment's TTL
- **`draft-deployment.py`** — interactive wizard to build a deployment JSON file without deploying; runs the full LXC or VM wizard and saves the result for use with `deploy_lxc.py`, `deploy_vm.py`, or `deploy.py`

---

## Project Layout

```
labinator/
├── configure.py                   # Interactive config.yaml wizard (build, edit, validate)
├── deploy_lxc.py                  # LXC provisioning wizard
├── decomm_lxc.py                  # LXC decommission script
├── deploy_vm.py                   # QEMU VM provisioning wizard
├── decomm_vm.py                   # QEMU VM decommission script
├── deploy.py                      # Batch deploy — multiple LXC/VM JSON files in parallel
├── decomm.py                      # Batch decomm — multiple LXC/VM JSON files (sequential by default)
├── cleanup_tagged.py              # Cluster-wide tag-based cleanup (keep/promote/decomm)
├── expire.py                      # Deployment TTL manager (check/reap/renew)
├── draft-deployment.py            # Interactive wizard to build a deployment JSON without deploying
├── config.yaml                    # Credentials + defaults (excluded from git — never commit)
├── config.yaml.example            # Documented config template (committed — copy to start)
├── cloud-images.yaml              # Cloud image catalog for deploy_vm.py
├── requirements.txt               # Python dependencies
├── setup.sh                       # First-time setup script (virtualenv + system deps)
├── known-bugs.md                  # Known issues and current status
├── Feature-ideas.md               # Ideas and planned features
├── .gitignore                     # Excludes config.yaml, .venv, deployments/, test files
├── modules/
│   ├── __init__.py                # Package marker
│   ├── lib.py                     # Shared utility functions (config, Proxmox connect, DNS, inventory)
│   ├── preflight.py               # Preflight check suite
│   ├── validation.py              # Deployment file and config validation
│   ├── proxmox.py                 # Proxmox API helpers (resource queries, task polling)
│   ├── startup.py                 # Wizard startup (banner, config load, cluster connect)
│   ├── profiles.py                # Package profile helpers
│   ├── ui.py                      # Interactive wizard helpers (prompts, back-navigation)
│   ├── io.py                      # I/O helpers (logging, file utilities)
│   ├── deploy.py                  # Shared deploy pipeline steps
│   ├── decomm.py                  # Shared decommission pipeline steps
│   ├── ansible.py                 # Ansible integration helpers
│   └── bind.py                    # BIND DNS integration helpers
├── deployments/
│   ├── lxc/                       # One JSON file per deployed LXC (gitignored except example-*)
│   │   └── example-lxc.json       # Example deployment file (tracked)
│   ├── vms/                       # One JSON file per deployed VM (gitignored except example-*)
│   │   └── example-vm.json        # Example deployment file (tracked)
│   └── history.log                # Append-only log of all deploy/decomm events (one JSON per line)
├── examples/
│   ├── list-file_keep-all.json           # Example: keep all tagged resources
│   ├── list-file_decomm-all.json         # Example: decomm all tagged resources
│   ├── list-file_promote-all.json        # Example: promote all tagged resources
│   ├── list-file_mixed.json              # Example: mixed actions
│   ├── list-file_duplicate-hostname.json # Example: disambiguate by vmid
│   ├── list-file_ghost-host.json         # Example: host that is already gone
│   └── list-file_invalid-action.json     # Example: shows invalid action error handling
└── ansible/
    ├── post-deploy.yml            # Post-deploy configuration for LXC containers
    ├── post-deploy-vm.yml         # Post-deploy configuration for QEMU VMs
    ├── ansible.cfg                # Ansible settings (host key checking disabled)
    ├── add-dns.yml                # Register A + PTR records in BIND
    ├── remove-dns.yml             # Remove A + PTR records from BIND
    ├── update-inventory.yml       # Add host to Ansible inventory on dev server
    ├── remove-from-inventory.yml  # Remove host from Ansible inventory
    ├── vars/
    │   ├── Debian.yml             # OS-specific vars for Debian/Ubuntu family
    │   ├── RedHat.yml             # OS-specific vars for RHEL/Rocky/Alma family
    │   └── Suse.yml               # OS-specific vars for openSUSE/SLES family
    ├── tasks/
    │   ├── pre-install-Debian.yml # apt update (+ Docker repo setup if needed)
    │   ├── pre-install-RedHat.yml # epel-release + dnf makecache
    │   ├── pre-install-Suse.yml   # zypper refresh
    │   ├── upgrade-Debian.yml     # apt dist-upgrade + autoremove
    │   ├── upgrade-RedHat.yml     # dnf upgrade + autoremove
    │   └── upgrade-Suse.yml       # zypper update
    └── templates/
        ├── snmpd.conf.j2          # SNMP daemon configuration template
        └── chrony.conf.j2         # chrony NTP configuration template
```

---

## Prerequisites

### Controller Machine

The machine you run the scripts from needs:

| Requirement | Version | Install |
|---|---|---|
| Python 3 | 3.10+ | `apt install python3 python3-pip python3-venv` |
| Ansible | 2.12+ | `apt install ansible` |
| sshpass | any | `apt install sshpass` |
| SSH key pair | — | `ssh-keygen -t rsa -b 4096` |

> **Why sshpass?** When Ansible first connects to a new LXC container it uses password auth (no key deployed yet). `sshpass` enables non-interactive password auth for that first connection. VM deployments use SSH key injection via cloud-init and do not require `sshpass`.

**Python packages** (installed into a virtualenv by `setup.sh`):

| Package | Purpose |
|---|---|
| `proxmoxer` | Proxmox REST API client |
| `paramiko` | SSH client (LXC bootstrap via pct exec; cloud image download to Proxmox nodes) |
| `questionary` | Interactive CLI prompts with arrow-key selection and defaults |
| `rich` | Terminal formatting (tables, panels, spinners) |
| `PyYAML` | `config.yaml` and `cloud-images.yaml` parsing |
| `requests` | HTTP transport for proxmoxer |

---

### Proxmox Cluster

- Proxmox VE 7.x or 8.x
- All nodes in a **single cluster** (one API endpoint sees all nodes)
- **API Token** with sufficient permissions (see [docs/configuration.md](docs/configuration.md#creating-a-proxmox-api-token))
  - **Privilege Separation must be disabled** on the token so it inherits the user's full permissions
- **SSH key** from the controller authorized as `root` on every Proxmox node

**For LXC containers:**
- At least one LXC template downloaded on the target node (`vztmpl` content type)
- Storage pool supporting `rootdir` content type (e.g. `local-lvm`, `local-zfs`)

**For QEMU VMs:**
- At least one storage with `iso` content type configured — used as the location for cloud images
- Storage pool supporting `images` content type for VM disks
- QEMU guest agent support on the target node (standard in Proxmox VE)

---

### DNS Server (BIND)

The automatic DNS registration requires:

- A BIND DNS server reachable via SSH from the controller machine (key-based auth)
- The SSH user (default: `root`) must have write access to the zone files
- `rndc` must be available on the DNS server to reload zones
- Forward zone file path configured in `config.yaml` under `dns.forward_zone_file`
- The reverse zone file is derived automatically from the IP at deploy time:
  - e.g. IP `10.20.20.140` → `/var/lib/bind/20.20.10.in-addr.arpa.hosts`
  - If the reverse zone file doesn't exist, the PTR step is skipped gracefully

DNS registration can be disabled by setting `dns.enabled: false` in `config.yaml`.

---

### Ansible Inventory Server

A server that:

- Is reachable via SSH from the controller machine (key-based auth preferred)
- Holds the master Ansible inventory file at the path in `config.yaml`
- Has a group header matching `ansible_inventory.group` (e.g. `[Linux]`) — **case-sensitive**
- The SSH user has write access to that inventory file

After deployment, the inventory entry looks like:
```
myserver ansible_host=myserver.example.com ansible_python_interpreter=/usr/bin/python3
```

---

## Installation

```bash
git clone https://github.com/Jerry-Lees/HomeLab.git
cd HomeLab/labinator
./setup.sh
```

`setup.sh` will:
- Update apt cache if stale
- Install missing system packages (`ansible`, `sshpass`, `openssh-client`, build tools)
- Create a Python virtualenv at `.venv/`
- Install all Python requirements from `requirements.txt`
- Verify every required Python module imports correctly
- Ask if you want to run the config wizard (`configure.py`) immediately — say **Y** to build `config.yaml` interactively, or **N** to do it later

The scripts auto-activate the virtualenv at startup, so you can run them with `python3 deploy_lxc.py` directly without sourcing `.venv/bin/activate` first.

---

## Documentation

| Page | Contents |
|---|---|
| [docs/configuration.md](docs/configuration.md) | Proxmox API token setup, SSH key auth, full `config.yaml` reference, `cloud-images.yaml`, `--config` flag, multi-node failover |
| [docs/configure.md](docs/configure.md) | Interactive config wizard — build, edit, and validate `config.yaml` with guided prompts |
| [docs/deploy-lxc.md](docs/deploy-lxc.md) | All `deploy_lxc.py` flags, interactive walkthrough, static IP and DHCP deployment, deploy from file, silent mode, dry-run, TTL, VLAN check, preflight, deployment logs, example scenarios |
| [docs/deploy-vm.md](docs/deploy-vm.md) | All `deploy_vm.py` flags, interactive walkthrough, deploy from file, silent mode, dry-run, TTL, VLAN check, preflight |
| [docs/decommission.md](docs/decommission.md) | `decomm_lxc.py` and `decomm_vm.py` flags, interactive and file-based mode, `--purge`, `--silent`, decommission logs, example scenarios |
| [docs/batch.md](docs/batch.md) | `deploy.py` and `decomm.py` — batch operations, `--parallel`, `--validate`, `--batch-dir`, `--yolo`, `--ttl`, `--purge` |
| [docs/expiry.md](docs/expiry.md) | All `expire.py` flags, `--check` output example, `--reap`, `--renew`, TTL format reference |
| [docs/cleanup.md](docs/cleanup.md) | All `cleanup_tagged.py` flags, tag-based cleanup, `--list-file`, action list format, `--dry-run` |
| [docs/preflight.md](docs/preflight.md) | Every preflight check, fatal vs warning, standalone mode, disabling preflight, `--yolo`, `--silent` |
| [docs/draft-deployment.md](docs/draft-deployment.md) | `draft-deployment.py` — build a deployment JSON interactively without deploying; LXC and VM wizard walkthroughs, editing drafts, TTL support |
| [docs/deployment-files.md](docs/deployment-files.md) | Deployment JSON field reference, LXC vs VM differences, file locations, `.gitignore` behavior, history log, providers, OS support |
| [docs/troubleshooting.md](docs/troubleshooting.md) | All known issues, symptoms, causes, and fixes |

---

## Quick Start

```bash
# Build config.yaml interactively (first-time setup)
python3 configure.py
  or
# Edit an existing config.yaml
python3 configure.py --edit

# Validate config.yaml without changing anything
python3 configure.py --validate

# Build a deployment file without deploying (LXC or VM)
python3 draft-deployment.py
python3 draft-deployment.py --lxc
python3 draft-deployment.py --vm --ttl 7d

# Deploy an LXC container interactively (DHCP or static IP — wizard will ask)
python3 deploy_lxc.py

# Deploy a VM interactively
python3 deploy_vm.py

# Deploy from a saved file (non-interactive)
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
python3 deploy_vm.py --deploy-file deployments/vms/myvm.json --silent

# Check the deployment log after any interactive run
cat logs/last-deployment.log

# Decommission a container or VM
python3 decomm_lxc.py
python3 decomm_vm.py

# Batch deploy multiple resources in parallel (up to 3 at a time by default)
python3 deploy.py --batch deployments/lxc/web1.json deployments/lxc/db1.json
python3 deploy.py --batch-dir deployments/lxc/ --validate
python3 deploy.py --batch deployments/lxc/web1.json deployments/vms/db1.json --parallel 5

# Batch decommission multiple resources (sequential by default — safe for DNS)
python3 decomm.py --batch deployments/lxc/web1.json deployments/lxc/db1.json
python3 decomm.py --batch-dir deployments/lxc/

# Check for expired or expiring-soon deployments
./expire.py --check

# Reap all expired deployments
./expire.py --reap --purge
```

---

## Submitting an Issue

> This tool is provided **as-is** without warranty or active support. Issue submissions are welcomed on a best-effort basis.

**Before opening an issue:**
- Read the [Troubleshooting](docs/troubleshooting.md) section
- Verify your `config.yaml` is correctly filled in \(Please be certain to redact sensitive information with `**redacted**`\)
- Verify all [Prerequisites](#prerequisites) are met

**Open a GitHub Issue with the following:**

```
## Issue Report — labinator

### Summary
<!-- One sentence describing what went wrong -->

### Environment
| Item | Value |
|---|---|
| OS (controller machine) | e.g. Ubuntu 24.04 |
| Python version | python3 --version |
| Ansible version | ansible --version |
| proxmoxer version | pip show proxmoxer |
| Proxmox VE version | shown in Proxmox UI top-right |
| labinator version / commit | git rev-parse --short HEAD |

### Which script and step failed?
**Batch:**
- [ ] deploy.py — batch deploy
- [ ] decomm.py — batch decommission

**LXC (deploy_lxc.py):**
- [ ] Startup / config / Proxmox connection
- [ ] Step 1 — Container creation
- [ ] Step 2 — Container start
- [ ] Step 3 — DHCP IP assignment (or static IP skip)
- [ ] Step 4 — SSH bootstrap (pct exec)
- [ ] Step 5 — Ansible post-deploy
- [ ] Step 6 — DNS registration
- [ ] Step 7 — Ansible inventory update
- [ ] decomm_lxc.py — decommission

**Cleanup / Expiry:**
- [ ] cleanup_tagged.py — interactive or --list-file mode
- [ ] expire.py --check
- [ ] expire.py --reap
- [ ] expire.py --renew

**VM (deploy_vm.py):**
- [ ] Startup / config / Proxmox connection
- [ ] Step 1 — VM creation
- [ ] Step 2 — Cloud image import / disk config
- [ ] Step 3 — VM start
- [ ] Step 4 — Waiting for SSH / DHCP IP
- [ ] Step 5 — Ansible post-deploy
- [ ] Step 6 — DNS registration
- [ ] Step 7 — Ansible inventory update
- [ ] decomm_vm.py — decommission

### Suggested Severity
<!-- Pick one — we may adjust when triaging -->
- [ ] Critical — blocks deployment entirely
- [ ] High — risk of data loss or silent failure
- [ ] Medium — functional breakage, but a workaround exists
- [ ] Low — cosmetic or minor inconvenience

### What did you expect to happen?

### What actually happened?
<!-- Include exact error messages or output snippets here -->

### Workaround
<!-- Did you find any way to work around this? If none, say so explicitly. -->

### Full terminal output
<!-- Paste complete output. Redact passwords and token secrets. -->
```
<details>
<summary>Terminal output</summary>

```
PASTE OUTPUT HERE
```

</details>
```

### config.yaml (REDACTED)
```yaml
PASTE REDACTED CONFIG HERE
```

### Steps to reproduce
1.
2.
3.
```

---

## known-bugs.md

See `known-bugs.md` in the project root for a list of known issues, their current status (open/fixed/wontfix), and any workarounds. New issues discovered during development are documented there before a fix is released.

---

*Built for a Proxmox homelab. Shared without warranty. Use at your own risk.*
