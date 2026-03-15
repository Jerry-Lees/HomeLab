# vm-onboard

A command-line wizard for provisioning, configuring, and onboarding LXC containers and QEMU virtual machines in a Proxmox VE homelab cluster. Handles the full lifecycle from resource creation through post-deployment configuration, DNS registration, and Ansible inventory registration — in a single guided session. Companion decommission scripts reverse the process cleanly.

> **Disclaimer:** This tool is provided **as-is**, without warranty or support of any kind. It was built for a specific homelab environment and is shared for reference and reuse. See [Submitting an Issue](#submitting-an-issue) if you encounter a problem.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Project Layout](#project-layout)
- [Prerequisites](#prerequisites)
  - [Controller Machine](#controller-machine)
  - [Proxmox Cluster](#proxmox-cluster)
  - [DNS Server (BIND)](#dns-server-bind)
  - [Ansible Inventory Server](#ansible-inventory-server)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Creating a Proxmox API Token](#creating-a-proxmox-api-token)
  - [Authorizing SSH Key on Proxmox Nodes](#authorizing-ssh-key-on-proxmox-nodes)
  - [config.yaml Reference](#configyaml-reference)
  - [cloud-images.yaml](#cloud-imagesyaml)
- [Command-Line Reference](#command-line-reference)
  - [deploy_lxc.py and deploy_vm.py flags](#deploy_lxcpy-and-deploy_vmpy-flags)
  - [decomm_lxc.py and decomm_vm.py flags](#decomm_lxcpy-and-decomm_vmpy-flags)
  - [Common combinations](#common-combinations)
- [Preflight Checks](#preflight-checks)
- [Usage — deploy_lxc.py](#usage--deploy_lxcpy)
  - [Interactive Mode](#interactive-mode)
  - [Deploy from File](#deploy-from-file)
  - [Silent Mode](#silent-non-interactive-mode)
  - [Validate Mode](#validate-mode)
  - [Dry-Run Mode](#dry-run-mode)
  - [Preflight Mode](#preflight-mode)
  - [Prompt Walkthrough](#walkthrough-lxc-prompt-order)
  - [Deployment Steps](#the-7-lxc-deployment-steps)
- [Usage — decomm_lxc.py](#usage--decomm_lxcpy)
- [Usage — deploy_vm.py](#usage--deploy_vmpy)
  - [Interactive Mode](#interactive-mode-1)
  - [Deploy from File](#deploy-from-file-1)
  - [Silent Mode](#silent-non-interactive-mode-1)
  - [Validate Mode](#validate-mode-1)
  - [Dry-Run Mode](#dry-run-mode-1)
  - [Preflight Mode](#preflight-mode-1)
  - [Prompt Walkthrough](#walkthrough-vm-prompt-order)
  - [Deployment Steps](#the-7-vm-deployment-steps)
- [Usage — decomm_vm.py](#usage--decomm_vmpy)
- [Deployment Files](#deployment-files)
- [Deployment Defaults](#deployment-defaults)
- [Package Profiles](#package-profiles)
- [Installed Packages](#installed-packages)
- [Post-Deployment State](#post-deployment-state)
- [porter Integration](#porter-integration)
- [Ansible Playbooks](#ansible-playbooks)
- [Troubleshooting](#troubleshooting)
- [Submitting an Issue](#submitting-an-issue)

---

## What It Does

### deploy_lxc.py

An interactive wizard that fully provisions and onboards an LXC container:

1. Connects to the Proxmox cluster API and queries available nodes, templates, and storage pools
2. Prompts for container specs — **resource questions (CPU/RAM/disk) are asked first** so the node list can be filtered by capacity
3. Filters the node list to only show nodes that can accommodate the requested resources (CPU <85%, RAM <95% after allocation)
4. Auto-selects the least-loaded passing node (most free RAM), with manual override
5. Creates the LXC container via the Proxmox API, tagged `auto-deploy` with a detailed notes block
6. Starts the container and polls for a DHCP IP address
7. Bootstraps the container via `pct exec` on the Proxmox node (before SSH is available): installs openssh-server, enables root SSH login, converts the DHCP-assigned address to a permanent static netplan configuration
8. Runs the Ansible post-deploy playbook: hostname, timezone, NTP, users, ~50 standard tools, SNMP, full apt dist-upgrade
9. Registers DNS A and PTR records on the BIND server — with a pre-check that detects existing records and prompts to overwrite, skip, or abort
10. Updates the Ansible inventory on the development server and sets up SSH key auth from dev server to new container
11. Saves a deployment file to `deployments/lxc/<hostname>.json`
12. Runs preflight checks at startup — verifies Proxmox API, SSH keys, Ansible, DNS and inventory servers, and (if a deploy file is provided) checks whether the hostname already resolves in DNS and whether the static IP is already in use
13. Prints a connection summary

### decomm_lxc.py

Permanently destroys a container deployed via `deploy_lxc.py`:

1. Lists containers from `deployments/lxc/` JSON files, or accepts `--deploy-file` directly
2. Shows a full destruction warning with container details
3. Flushes keyboard buffer for 5 seconds, then requires typing a **random-caps challenge word** (e.g. `YeS`) — case-sensitive, different every run
4. Stops and destroys the Proxmox container
5. Removes DNS A + PTR records from BIND
6. Removes the host from the Ansible inventory
7. Reports the local deployment file path (use `--purge` to also delete it)

### deploy_vm.py

An interactive wizard that fully provisions and onboards a QEMU virtual machine using cloud-init:

1. Connects to the Proxmox cluster API and queries nodes and storage pools
2. Prompts for VM specs (resource questions first for node filtering)
3. Filters the node list by available capacity
4. Auto-selects the least-loaded node, with manual override
5. Prompts for cloud image storage (ISO-capable datastores only) and image selection, with a two-level browser: storage → image. Shows existing cached images and catalog download options. Navigation includes a Back option to change storage selection.
6. Creates the QEMU VM via the Proxmox API (q35 machine, SeaBIOS, x86-64-v2-AES CPU, virtio-scsi-pci controller)
7. Downloads the cloud image to the selected storage if not already present, then imports it as the VM disk via `qm importdisk`
8. Configures cloud-init: injects the controller's SSH public key, sets hostname, password, network (static or DHCP)
9. Starts the VM and waits for SSH (static IP) or polls the QEMU guest agent for the assigned IP (DHCP)
10. Runs the Ansible post-deploy-vm playbook: waits for cloud-init first-boot to complete, then configures hostname, timezone, NTP, users, tools, SNMP, QEMU guest agent, and a full system upgrade — using OS-appropriate commands for the detected guest family (Debian/RedHat/Suse)
11. Registers DNS A and PTR records on the BIND server — with a pre-check that detects existing records and prompts to overwrite, skip, or abort
12. Updates the Ansible inventory on the development server
13. Runs preflight checks at startup — verifies Proxmox API, SSH keys, Ansible, DNS and inventory servers, and (if a deploy file is provided) checks whether the hostname already resolves in DNS and whether the static IP is already in use
14. Saves a deployment file to `deployments/vms/<hostname>.json`
15. Prints a connection summary

### decomm_vm.py

Permanently destroys a VM deployed via `deploy_vm.py`:

1. Lists VMs from `deployments/vms/` JSON files, or accepts `--deploy-file` directly
2. Shows a full destruction warning with VM details
3. Flushes keyboard buffer for 5 seconds, then requires a random-caps challenge word
4. Stops and destroys the Proxmox VM (purges unreferenced disks)
5. Removes DNS A + PTR records from BIND
6. Removes the host from the Ansible inventory
7. Reports the local deployment file path (use `--purge` to also delete it)

---

## Project Layout

```
labinator/
├── deploy_lxc.py                  # LXC provisioning wizard
├── decomm_lxc.py                  # LXC decommission script
├── deploy_vm.py                   # QEMU VM provisioning wizard
├── decomm_vm.py                   # QEMU VM decommission script
├── config.yaml                    # Credentials + defaults (excluded from git)
├── config.yaml.example            # Documented config template (committed)
├── cloud-images.yaml              # Cloud image catalog for deploy_vm.py
├── requirements.txt               # Python dependencies
├── setup.sh                       # First-time setup script
├── .gitignore                     # Excludes config.yaml and .venv
├── modules/
│   ├── __init__.py                # Package marker
│   └── lib.py                     # Shared functions used by all four scripts
├── deployments/
│   ├── lxc/                       # One JSON file per deployed LXC container
│   │   └── myserver.json
│   └── vms/                       # One JSON file per deployed VM
│       └── myvm.json
└── ansible/
    ├── post-deploy.yml            # Post-deploy configuration for LXC containers
    ├── post-deploy-vm.yml         # Post-deploy configuration for QEMU VMs
    ├── ansible.cfg                # Ansible settings (host key checking disabled)
    ├── add-dns.yml                # Register A + PTR records in BIND
    ├── remove-dns.yml             # Remove A + PTR records from BIND
    ├── update-inventory.yml       # Add host to Ansible inventory
    ├── remove-from-inventory.yml  # Remove host from Ansible inventory
    ├── vars/
    │   ├── Debian.yml             # OS-specific vars for Debian/Ubuntu family
    │   ├── RedHat.yml             # OS-specific vars for RHEL/Rocky/Alma family
    │   └── Suse.yml               # OS-specific vars for openSUSE/SLES family
    ├── tasks/
    │   ├── pre-install-Debian.yml # apt update (+ Docker repo setup if needed)
    │   ├── pre-install-RedHat.yml # epel-release + dnf update
    │   ├── pre-install-Suse.yml   # zypper refresh
    │   ├── upgrade-Debian.yml     # apt dist-upgrade + autoremove
    │   ├── upgrade-RedHat.yml     # dnf upgrade + autoremove
    │   └── upgrade-Suse.yml       # zypper update
    └── templates/
        ├── snmpd.conf.j2          # SNMP daemon configuration template
        └── chrony.conf.j2         # chrony NTP configuration template
```

---

## Supported Guest Operating Systems

VM deployments (`deploy_vm.py`) support any cloud-init capable image. The Ansible post-deploy playbook automatically detects the guest OS family and applies the correct package manager, service names, and package list.

**Tested and verified:**
- Ubuntu 24.04 LTS
- Rocky Linux 8
- openSUSE Leap 15.6

**Should work without changes** — derivatives are covered by the same OS family vars:

- **Debian family:** Debian, Ubuntu, Linux Mint, Raspbian, Kali, Pop!_OS
- **RedHat family:** Rocky, AlmaLinux, CentOS Stream, RHEL, Fedora, Oracle Linux
- **Suse family:** openSUSE Leap, openSUSE Tumbleweed, SLES

If a specific package name differs for a new distro (e.g. a tool is named differently in that distro's repos), update the relevant `ansible/vars/<Family>.yml` file — no playbook changes needed.

LXC containers (`deploy_lxc.py`) currently support Debian/Ubuntu templates only, as the bootstrap step uses `apt` directly.

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
- **API Token** with sufficient permissions (see [Creating a Proxmox API Token](#creating-a-proxmox-api-token))
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
git clone <your-repo-url> ~/projects/vm-onboard
cd ~/projects/vm-onboard
cp config.yaml.example config.yaml
./setup.sh
```

`setup.sh` will:
- Update apt cache if stale
- Install missing system packages (`ansible`, `sshpass`, `openssh-client`, build tools)
- Create a Python virtualenv at `.venv/`
- Install all Python requirements from `requirements.txt`
- Verify every required Python module imports correctly

---

## Configuration

### Creating a Proxmox API Token

1. In the Proxmox web UI, go to **Datacenter → Permissions → API Tokens**
2. Click **Add**
3. Fill in:
   - **User:** `root@pam` (or a dedicated user)
   - **Token ID:** `vm-deploy` (or any name you like)
   - **Privilege Separation:** **unchecked** (token must inherit full user permissions)
4. Click **Add** — copy the **Secret** immediately, it is only shown once
5. Edit `config.yaml` and paste the secret into `proxmox.token_secret`
6. Set `proxmox.token_name` to just the token ID (e.g. `vm-deploy`) — **not** the full `user!tokenid` string

> **Permissions needed:** The token/user needs `Administrator` role on `/`, or at minimum: `VM.Allocate`, `VM.Config.*`, `VM.PowerMgmt`, `Datastore.AllocateSpace`, `Datastore.Audit`, `SDN.Use`, `Sys.Audit`.

---

### Authorizing SSH Key on Proxmox Nodes

The controller machine's SSH key must be in `authorized_keys` on every Proxmox node:

```bash
ssh-copy-id root@proxmox01.example.com
ssh-copy-id root@proxmox02.example.com
# repeat for each node...
```

Verify it works without a password:
```bash
ssh root@proxmox01.example.com 'echo OK'
```

If you use a non-default key, set `proxmox.ssh_key` in `config.yaml`.

---

### config.yaml Reference

`config.yaml` is **excluded from git** — it contains credentials and is never committed. Copy the included example to get started:

```bash
cp config.yaml.example config.yaml
```

Then edit `config.yaml` with your environment's values. The reference below shows all available fields:

```yaml
proxmox:
  # Single host — or use 'hosts' list for automatic failover across cluster nodes.
  # Any node in the cluster works; they all share the same API state.
  # host: proxmox01.example.com
  hosts:
    - proxmox01.example.com       # Tried in order; first reachable one is used
    - proxmox02.example.com
  user: root@pam                    # Proxmox user (realm included)
  token_name: vm-deploy             # Token ID only — NOT the full user!tokenid string
  token_secret: CHANGEME            # ← PASTE YOUR TOKEN SECRET HERE
  ssh_key: ~/.ssh/id_rsa            # SSH key authorized on all Proxmox nodes as root
  node_domain: example.com       # Domain appended to node names for SSH
  verify_ssl: false                 # true only if Proxmox has valid TLS certs

nodes:                              # Used for display; live list is queried from API
  - proxmox01
  - proxmox02
  - proxmox01
  # ...

defaults:
  cpus: 2                           # vCPUs
  memory_gb: 4                      # RAM in GB
  disk_gb: 100                      # Root disk in GB
  vlan: 220                         # VLAN tag (creates vmbr0.220)
  bridge: vmbr0                     # Proxmox bridge interface
  root_password: changeme           # Default password (change at prompt)
  swap_mb: 512                      # Swap in MB (LXC only)
  onboot: true                      # Auto-start when Proxmox node boots
  searchdomain: example.com      # DNS search domain
  nameserver: "10.0.0.10 10.0.0.11"
  template: ubuntu-24.04-standard_24.04-2_amd64.tar.zst  # Default LXC template

ansible:
  enabled: true                     # Set false to skip ALL Ansible post-deploy steps

dns:
  enabled: true                     # Set false to skip DNS registration
  provider: bind                    # Integration type (bind is the only supported provider)
  server: 10.0.0.10               # BIND DNS server IP
  ssh_user: root
  forward_zone_file: /var/lib/bind/example.com.hosts
  # Reverse zone file is derived automatically from the IP

ansible_inventory:
  enabled: true                     # Set false to skip inventory update only
  provider: flat_file               # Integration type (flat_file is the only supported provider)
  server: dev.example.com
  user: root
  file: /root/ansible/inventory/hosts
  group: Linux                      # CASE-SENSITIVE — must match the [GroupName] header

snmp:
  community: YourSNMPCommunityString                  # Read-write SNMP community string
  source: default                   # Source restriction (default = any)
  location: Homelab
  contact: admin@example.com

ntp:
  servers:
    - pool.ntp.org
    - time.nist.gov

timezone: America/Chicago

vm:
  # Proxmox storage to pre-select in the cloud image browser.
  # Must have 'iso' content type configured. Cloud images are stored at
  # {storage_path}/cloud-images/ — not in template/iso/ — so they do not
  # appear in the Proxmox GUI ISO picker.
  # Leave blank to always prompt without a pre-selection.
  default_cloud_image_storage: local
```

---

### cloud-images.yaml

The VM deployment wizard reads its list of downloadable OS images from `cloud-images.yaml` in the project root. This file can be edited without touching any Python scripts.

```yaml
cloud_images:
  - name: "Ubuntu 24.04 LTS (Noble Numbat)"
    url: "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    filename: "noble-server-cloudimg-amd64.img"

  - name: "Debian 12 (Bookworm)"
    url: "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"
    filename: "debian-12-generic-amd64.qcow2"

  # Add custom entries following the same format
```

All entries must be cloud-init capable images. The wizard shows existing cached images on the selected storage first, then the catalog entries as download options.

**Cloud image storage location:** Images are downloaded to `{storage_path}/cloud-images/` on the Proxmox node — not to `template/iso/`. This keeps them invisible to the Proxmox GUI ISO picker, preventing accidental attachment as a CD-ROM during manual VM creation. The directory is created automatically on first download.

**Auto-recovery:** If a deployment file references an image that no longer exists on the storage, the script looks up the download URL in `cloud-images.yaml` by filename, then falls back to the URL stored in the deployment file. This handles the case where someone has cleaned up the storage manually. If neither source has a URL, the script fails with a clear message directing you to add the entry to `cloud-images.yaml`.

**Fedora note:** Fedora cloud image URLs are version-specific (no stable `current/` redirect). Update the URL in `cloud-images.yaml` when upgrading to a new Fedora release. Check the current URL at https://fedoraproject.org/cloud/download.

---

## Command-Line Reference

### deploy_lxc.py and deploy_vm.py flags

| Flag | Description |
|---|---|
| `--deploy-file FILE` | Load deployment JSON to pre-fill prompts (or drive `--silent`) |
| `--silent` | Non-interactive: use all values from `--deploy-file` without prompting. Requires `--deploy-file`. Exits 1 on any preflight warning or failure |
| `--validate` | Parse and validate `config.yaml` (and deploy file if given) then exit. No Proxmox connection |
| `--dry-run` | Validate config + deploy file and print a full step-by-step plan without making any changes |
| `--preflight` | Run all preflight connectivity and dependency checks then exit. Add `--deploy-file` to also check DNS hostname and static IP |
| `--yolo` | Run preflight but continue through warnings without prompting. Fatal failures still block the deploy |

### decomm_lxc.py and decomm_vm.py flags

| Flag | Description |
|---|---|
| `--deploy-file FILE` | Load deployment JSON directly, skipping the interactive list |
| `--purge` | Also delete the local deployment JSON file after decommissioning |
| `--silent` | Skip the confirmation challenge (requires `--deploy-file`) |

### Common combinations

| Goal | Command |
|---|---|
| Check your environment is ready | `./deploy_lxc.py --preflight` |
| Check environment + verify a specific deploy file | `./deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json` |
| Re-deploy interactively from a saved file | `./deploy_lxc.py --deploy-file deployments/lxc/myserver.json` |
| Fully automated deploy (CI/CD, scripted) | `./deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent` |
| Automated deploy, skip preflight for this host | Set `"preflight": false` in the JSON, then `--silent` |
| Deploy without being blocked by preflight warnings | `./deploy_lxc.py --deploy-file deployments/lxc/myserver.json --yolo` |
| Verify config without touching Proxmox | `./deploy_lxc.py --validate` |
| See exactly what would happen without doing it | `./deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json` |
| Decomm and delete the deployment file | `./decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge` |
| Scripted decomm (no confirmation prompt) | `./decomm_lxc.py --deploy-file deployments/lxc/myserver.json --silent` |

> **Flag interaction table — preflight behavior:**
>
> | Flags | Warnings | Fatal failures |
> |---|---|---|
> | _(none)_ | Continue / Retry / Abort prompt | Continue / Retry / Abort prompt |
> | `--yolo` | Continue silently | Continue / Retry / Abort prompt |
> | `--silent` | Exit 1 | Exit 1 |
> | `--silent --yolo` | Continue silently | Exit 1 |
> | `"preflight": false` in deploy file | Skipped entirely | Skipped entirely |

---

## Preflight Checks

Both deploy scripts run a preflight check suite automatically at the start of every deployment — **before** any prompts are shown or Proxmox resources are created. The results are displayed as a table. If all checks pass you see a single `✓ Preflight checks passed.` line. If any check fails or warns, the full table is shown.

Run standalone (check your environment and exit without deploying):
```bash
./deploy_lxc.py --preflight
./deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json
```

### Checks performed

| Check | Fatal? | What it verifies |
|---|---|---|
| Config valid | Yes | `config.yaml` parses without errors and required fields are present |
| Proxmox API reachable | Yes | TCP connect to port 8006 on each host in `proxmox.hosts`. Shows `X/Y host(s)` with names of unreachable hosts |
| Proxmox API auth | Yes | API token is accepted (`GET /version`) |
| SSH key on disk | Warning | `proxmox.ssh_key` file exists at the configured path |
| Proxmox node SSH | Warning | SSH key is accepted by each node in the `nodes:` list. Shows `X/Y node(s)` with names of failing nodes |
| Ansible installed | Yes | `ansible-playbook` is on PATH (skipped if `ansible.enabled: false`) |
| sshpass installed | Yes (LXC only) | `sshpass` is on PATH |
| DNS server reachable | Warning | TCP connect to port 22 on `dns.server` (skipped if `dns.enabled: false`) |
| DNS server SSH auth | Warning | Key-based SSH to `dns.ssh_user@dns.server` succeeds |
| DNS hostname check | Warning | If `--deploy-file` provided: queries DNS server directly for the hostname — warns if a record already exists (existing host may be orphaned) |
| Static IP in use | **Fatal** | If `--deploy-file` provided and `ip_address` is set: pings the IP — **fails if it responds** (duplicate IP prevention) |
| Inventory server reachable | Warning | TCP connect to port 22 on `ansible_inventory.server` |
| Inventory SSH auth | Warning | Key-based SSH to the inventory server succeeds |

Fatal failures block the deploy (or in `--silent` mode, exit 1 immediately). Warning-level checks print a yellow `⚠ warn` row but allow the deploy to proceed after the Continue/Retry/Abort prompt.

### Skipping preflight for a specific deployment

Add `"preflight": false` to the deployment JSON:
```json
{
  "hostname": "myserver",
  "preflight": false,
  ...
}
```
This is useful for hosts that are intentionally replacing an existing server (where the DNS and IP will already be in use) or in automation where you've pre-validated externally.

---

## Usage — deploy_lxc.py

### Interactive Mode

```bash
source .venv/bin/activate
python3 deploy_lxc.py
```

Runs the full interactive wizard. All prompts have defaults from `config.yaml`.

### Deploy from File

```bash
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json
```

Loads a previously saved deployment file and pre-fills all prompts with its values. You can review and edit each value before confirming. Useful for redeploying a container with the same or similar configuration.

### Silent (Non-Interactive) Mode

```bash
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
```

Skips all interactive prompts and deploys using the values in the file. `--silent` requires `--deploy-file`.

### Validate Mode

```bash
python3 deploy_lxc.py --validate
python3 deploy_lxc.py --validate --deploy-file deployments/lxc/myserver.json
```

Parses and validates `config.yaml` (and the deployment file if provided) without connecting to Proxmox or making any changes. Exits 0 on success, 1 on error. Useful for CI checks or verifying a config change before deploying.

### Dry-Run Mode

```bash
python3 deploy_lxc.py --dry-run
python3 deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json
```

Validates config and deployment file, then prints a full summary of what _would_ happen — hostname, node, template, resources, packages, tags, and each numbered step — without connecting to Proxmox, running Ansible, or modifying anything. Exits 0 on success.

Without `--deploy-file`, only the config is validated and a brief message is printed. With `--deploy-file`, a full deployment summary and step-by-step plan are shown. Useful for sanity-checking a deployment file before a real run.

### Preflight Mode

```bash
python3 deploy_lxc.py --preflight
python3 deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json
```

Runs all preflight checks and exits without deploying. Without `--deploy-file`, checks infrastructure only. With `--deploy-file`, also checks whether the hostname already has a DNS record and whether the static IP is already in use.

Add `--silent` to make it script-friendly (exits 0 = all clear, 1 = any issue). Add `--yolo` to exit 0 on warnings and only fail on fatal checks.

---

### Walkthrough: LXC Prompt Order

Resource questions come **before** node selection so that nodes without enough capacity are hidden.

**1. Hostname**
```
Hostname for the new container:
(short name, e.g. myserver — .example.com will be appended in inventory)
> myserver
```

**2. vCPUs / Memory / Disk / VLAN / Password**
```
Number of vCPUs:                     [2]
Memory (GB):                         [4]
Disk size (GB):                      [100]
VLAN tag (bridge: vmbr0.<vlan>):     [220]
Root / admin user password:            [changeme]
```

**3. Node selection** (filtered by requested resources)
```
Select Proxmox node (★ = most free RAM; 2 node(s) hidden — over resource threshold):
  ★ proxmox03  —  54.2 GB free / 128.0 GB RAM  (CPU: 18%)
    proxmox02   —  28.4 GB free / 64.0 GB RAM   (CPU: 12%)
```
Nodes that would push CPU above 85% or RAM above 95% after allocation are hidden.

**4. OS Template**
```
Select OS template (Ubuntu templates listed first):
  [Net-Images] ubuntu-24.04-standard_24.04-2_amd64.tar.zst
  [local] debian-12-standard_12.7-1_amd64.tar.zst
```
Queried live from the selected node. Ubuntu versions are listed first.

**5. Storage pool** (only shown if more than one pool exists on the node)

**6. Confirmation summary and pre-creation resource check**

---

### The 7 LXC Deployment Steps

```
─── Step 1/7: Creating LXC container ───
─── Step 2/7: Starting container ───
─── Step 3/7: Waiting for DHCP IP address ───
─── Step 4/7: Bootstrapping SSH in container ───
─── Step 5/7: Running post-deployment configuration (Ansible) ───
─── Step 6/7: Registering DNS records ───
─── Step 7/7: Updating Ansible inventory ───
```

**Step 4 — Bootstrap via pct exec:**
Runs entirely on the Proxmox node before SSH is available. Installs openssh-server, sets passwords, enables root SSH login, then detects the DHCP-assigned IP and gateway and writes a permanent netplan configuration that locks in the same address as a static assignment.

**Step 6 — DNS pre-check + registration:**
Before writing any records, the configured DNS server is queried directly (`dig @<dns.server>`) for the hostname. If a record already exists:
- **Same IP as deploy file:** shows an idempotent notice — continues but warns the existing host will be orphaned
- **Different IP:** shows both IPs and prompts: **[O]verwrite**, **[S]kip DNS**, **[A]bort**
- **Multiple records:** shows all existing records with count, then prompts

In `--silent` mode, existing records are overwritten automatically with a logged warning. If the record does not exist, A and PTR records are written to the BIND zone files and `rndc reload` is called. If the reverse zone file doesn't exist, PTR is skipped gracefully.

**Step 7 — Inventory + SSH key:**
Runs `ssh-keyscan` and `ssh-copy-id` from the development server to the new container so Ansible can connect with key-based auth immediately. The keyscan step is non-fatal — `ssh-copy-id` uses `StrictHostKeyChecking=no` regardless.

---

## Usage — decomm_lxc.py

**Permanently destroys** a container and removes all associated records. This is irreversible.

```bash
python3 decomm_lxc.py                                                    # interactive list
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json       # skip list
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge  # also delete JSON
```

The `--deploy-file` flag skips the numbered list and loads the specified file directly. The scary confirmation still runs in all cases.

Runs 4 steps after confirmation:

```
─── Step 1/4: Destroying Proxmox container ───
─── Step 2/4: Removing DNS records ───
─── Step 3/4: Removing from Ansible inventory ───
─── Step 4/4: Deployment file ───
```

Without `--purge`, the deployment file is left in place and its path is printed. This is intentional — a recently decommissioned host's JSON is useful for reference or redeployment.

> Only containers with a `deployments/lxc/*.json` file can be decommissioned this way.

---

## Usage — deploy_vm.py

### Interactive Mode

```bash
source .venv/bin/activate
python3 deploy_vm.py
```

### Deploy from File

```bash
python3 deploy_vm.py --deploy-file deployments/vms/myvm.json
```

Pre-fills all prompts from the deployment file. The cloud image storage and image filename are used as defaults in the two-level browser.

### Silent (Non-Interactive) Mode

```bash
python3 deploy_vm.py --deploy-file deployments/vms/myvm.json --silent
```

### Validate Mode

```bash
python3 deploy_vm.py --validate
python3 deploy_vm.py --validate --deploy-file deployments/vms/myvm.json
```

Parses and validates `config.yaml` (and the deployment file if provided) without connecting to Proxmox or making any changes. Exits 0 on success, 1 on error.

### Dry-Run Mode

```bash
python3 deploy_vm.py --dry-run
python3 deploy_vm.py --dry-run --deploy-file deployments/vms/myvm.json
```

Validates config and deployment file, then prints a full summary of what _would_ happen — hostname, node, cloud image, resources, packages, tags, and each numbered step — without connecting to Proxmox, running Ansible, or modifying anything. Exits 0 on success.

Without `--deploy-file`, only the config is validated and a brief message is printed. With `--deploy-file`, a full deployment summary and step-by-step plan are shown.

### Preflight Mode

```bash
python3 deploy_vm.py --preflight
python3 deploy_vm.py --preflight --deploy-file deployments/vms/myserver.json
```

Runs all preflight checks and exits without deploying. Without `--deploy-file`, checks infrastructure only. With `--deploy-file`, also checks whether the hostname already has a DNS record and whether the static IP is already in use.

Add `--silent` to make it script-friendly (exits 0 = all clear, 1 = any issue). Add `--yolo` to exit 0 on warnings and only fail on fatal checks.

---

### Walkthrough: VM Prompt Order

**1. Hostname**

**2. vCPUs / Memory / Disk / VLAN / Password**

**3. IP address** (static or DHCP)
```
IP address for VM: (e.g. 10.20.20.200 — leave blank for DHCP)
```
Leave blank to use DHCP. The VM boots with `ip=dhcp` in cloud-init, then the QEMU guest agent is polled until it reports the assigned address.

**4. Prefix length and gateway** (skipped if DHCP)

**5. Node selection** (filtered by capacity)

**6. Cloud image storage** (ISO-capable datastores only, with free space)
```
Select storage for cloud image:
  Net-Images  (1.8 TB free / 2.0 TB)
  local       (42.3 GB free / 118.0 GB)
```
If `local` is selected, a warning is printed noting that local storage is shared with the OS and VM disks and space is limited.

**7. Image selection** (two-level browser)
```
Select image from Net-Images:
  noble-server-cloudimg-amd64.img   (623 MB)   ← already on storage
  ─── Download from catalog ───
  Download: Ubuntu 24.04 LTS (Noble Numbat)
  Download: Ubuntu 22.04 LTS (Jammy Jellyfish)
  Download: Debian 12 (Bookworm)
  ...
  ← Back to storage selection
```
Selecting an existing file uses it without downloading. Selecting a "Download:" entry downloads the image to `{storage_path}/cloud-images/` on the node before importing. The Back option returns to the storage picker.

**8. Storage pool** for the VM disk (images content type)

**9. Confirmation summary**

---

### The 7 VM Deployment Steps

```
─── Step 1/7: Creating QEMU VM ───
─── Step 2/7: Importing cloud image and configuring VM ───
─── Step 3/7: Starting VM ───
─── Step 4/7: Waiting for SSH / DHCP IP ───
─── Step 5/7: Running post-deployment configuration (Ansible) ───
─── Step 6/7: Registering DNS records ───
─── Step 7/7: Updating Ansible inventory ───
```

**Step 2 — Cloud image import:**
The image is checked for existence at `{storage_path}/cloud-images/{filename}` on the target Proxmox node via SSH. If it is already there and `image_refresh` is false, it is used directly. Otherwise it is downloaded via `wget` to that path. The image is then imported as a VM disk with `qm importdisk`, and cloud-init is configured on `ide2`. A serial console (`serial0=socket`, `vga=serial0`) is required for Ubuntu cloud images and is set automatically.

**Step 4 — Wait for SSH (static) or DHCP:**
For static IPs, the script polls TCP port 22 until SSH accepts connections. For DHCP, it polls the QEMU guest agent `network-get-interfaces` endpoint until a non-loopback IPv4 is reported.

**Step 5 — Ansible with cloud-init wait:**
The playbook waits for `/run/cloud-init/result.json` to exist before making any configuration changes. This file is written by cloud-init when all first-boot stages complete, and works reliably across all supported OS families.

**Step 6 — DNS pre-check + registration:**
Before writing any records, the configured DNS server is queried directly (`dig @<dns.server>`) for the hostname. If a record already exists:
- **Same IP as deploy file:** shows an idempotent notice — continues but warns the existing host will be orphaned
- **Different IP:** shows both IPs and prompts: **[O]verwrite**, **[S]kip DNS**, **[A]bort**
- **Multiple records:** shows all existing records with count, then prompts

In `--silent` mode, existing records are overwritten automatically with a logged warning. If the record does not exist, A and PTR records are written to the BIND zone files and `rndc reload` is called. If the reverse zone file doesn't exist, PTR is skipped gracefully.

---

## Usage — decomm_vm.py

```bash
python3 decomm_vm.py                                                   # interactive list
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json          # skip list
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json --purge  # also delete JSON
```

Runs 4 steps after confirmation:

```
─── Step 1/4: Destroying Proxmox VM ───
─── Step 2/4: Removing DNS records ───
─── Step 3/4: Removing from Ansible inventory ───
─── Step 4/4: Deployment file ───
```

VM destruction uses `purge=1` and `destroy-unreferenced-disks=1` to remove all associated disk volumes.

> Only VMs with a `deployments/vms/*.json` file can be decommissioned this way.

---

## Deployment Files

Deployment files are JSON saved after each successful deployment. They serve as the input to `--deploy-file` (interactive re-run with pre-filled defaults), `--silent` (fully automated re-run), and the decommission scripts.

JSON is used rather than YAML so the files are usable directly as API payloads without transformation.

### LXC deployment file (`deployments/lxc/<hostname>.json`)

```json
{
  "hostname": "myserver",
  "fqdn": "myserver.example.com",
  "node": "proxmox03",
  "vmid": 142,
  "template_volid": "Net-Images:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
  "template_name": "ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
  "cpus": 2,
  "memory_gb": 4.0,
  "disk_gb": 100,
  "storage": "local-lvm",
  "vlan": 220,
  "bridge": "vmbr0",
  "password": "changeme",
  "ip_address": "10.20.20.150",
  "prefix_len": "24",
  "deployed_at": "2026-03-06 14:22:00",
  "preflight": true
}
```

If the `template_volid` stored in the file is no longer present on the node (e.g. the template was deleted or a newer version downloaded), the script falls back to the first available template on the node and prints a warning.

**`preflight`** — controls whether preflight checks run before this deployment. Defaults to `true`. Set to `false` to skip all preflight checks for this specific host (equivalent to passing `--yolo` and disabling the fatal checks). Use with care — see [Preflight Checks](#preflight-checks).

### VM deployment file (`deployments/vms/<hostname>.json`)

```json
{
  "type": "vm",
  "hostname": "myvm",
  "fqdn": "myvm.example.com",
  "node": "proxmox03",
  "vmid": 200,
  "cloud_image_storage": "Net-Images",
  "cloud_image_filename": "noble-server-cloudimg-amd64.img",
  "cloud_image_url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
  "image_refresh": false,
  "cpus": 2,
  "memory_gb": 4.0,
  "disk_gb": 100,
  "storage": "local-lvm",
  "vlan": 220,
  "bridge": "vmbr0",
  "password": "changeme",
  "ip_address": "10.20.20.200",
  "prefix_len": "24",
  "gateway": "10.20.20.1",
  "deployed_at": "2026-03-06 10:00:00",
  "preflight": true
}
```

**`cloud_image_storage`** — the Proxmox storage where the cloud image lives. Used to locate `{storage_path}/cloud-images/{filename}` on the node.

**`cloud_image_filename`** — the filename of the image within the cloud-images directory.

**`cloud_image_url`** — the download URL, stored as a fallback for auto-recovery if the image is missing from storage and not found in `cloud-images.yaml`.

**`image_refresh`** — if `true`, the image is always re-downloaded before import, even if it is already present on the storage. Set automatically based on whether you selected an existing image (`false`) or a "Download:" catalog entry (`true`) during the wizard.

**`ip_address`** — `"dhcp"` if DHCP mode was used, otherwise the configured static IP.

**`assigned_ip`** — present only for DHCP deployments; records the actual IP assigned at boot time.

**`preflight`** — controls whether preflight checks run before this deployment. Defaults to `true`. Set to `false` to skip all preflight checks for this specific host (equivalent to passing `--yolo` and disabling the fatal checks). Use with care — see [Preflight Checks](#preflight-checks).

---

## Deployment Defaults

All defaults come from `config.yaml` and are shown as editable suggestions at each prompt. When using `--deploy-file`, the file's values override `config.yaml` defaults.

### LXC

| Setting | Default | Notes |
|---|---|---|
| vCPUs | 2 | |
| Memory | 4 GB | |
| Root disk | 100 GB | Thin-provisioned on LVM/ZFS |
| Swap | 512 MB | |
| VLAN | 220 | vmbr0.220 |
| IP addressing | DHCP → static | DHCP-assigned address locked in via netplan |
| Boot on start | yes | |
| Features | nesting=1 | Allows Docker inside the container |
| Timezone | America/Chicago | |
| NTP | pool.ntp.org, time.nist.gov | chrony |
| SNMP community | YourSNMPCommunityString | Read-write, UDP :161 |
| Tag | auto-deploy | Applied in Proxmox |

### VM

| Setting | Default | Notes |
|---|---|---|
| vCPUs | 2 | |
| Memory | 4 GB | |
| Disk | 100 GB | scsi0 via virtio-scsi-pci |
| VLAN | 220 | vmbr0.220 |
| IP addressing | Static or DHCP | cloud-init configured |
| Machine type | q35 | |
| BIOS | SeaBIOS | |
| CPU type | x86-64-v2-AES | |
| Storage controller | virtio-scsi-pci | |
| Console | serial0=socket, vga=serial0 | Required for Ubuntu cloud images |
| Boot on start | yes | |
| Timezone | America/Chicago | |
| NTP | pool.ntp.org, time.nist.gov | chrony |
| SNMP community | YourSNMPCommunityString | Read-write, UDP :161 |
| QEMU guest agent | Installed and enabled | Required for DHCP IP discovery |
| Tag | auto-deploy | Applied in Proxmox |

---

## Package Profiles

Package profiles are named sets of packages defined in `config.yaml` under `package_profiles:`. At deploy time you select a profile (or none), and its packages are installed on top of the standard baseline. You can also specify additional one-off packages via `extra_packages` in the deployment file.

**Install order:** standard baseline → profile packages → extra packages

Profiles also apply Proxmox tags, so deployed hosts are labeled by role in the Proxmox UI.

### Built-in profiles

| Profile | Packages | Tags |
|---|---|---|
| `web-server` | nginx, certbot, python3-certbot-nginx, ufw | WWW |
| `database` | mariadb-server, mariadb-client | DB, MariaDB |
| `docker-host` | docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin | Docker |
| `monitoring-node` | prometheus-node-exporter, snmpd | Monitoring |
| `dev-tools` | git, vim, tmux, make, python3-pip | Development, Build |
| `nfs-server` | nfs-kernel-server, nfs-common | NFS, Storage |

> **Docker CE note:** `docker-ce` is not in the standard Ubuntu/Debian repositories. When the `docker-host` profile (or any profile containing `docker-ce`) is selected, the Ansible playbook automatically sets up Docker's official apt repository before installing packages. No manual configuration is needed.

### Defining custom profiles

Add any profile to `config.yaml`:

```yaml
package_profiles:
  my-profile:
    packages:
      - some-package
      - another-package
    tags:
      - MyTag
```

The profile name appears in the selection prompt at deploy time. Tag names are applied to the Proxmox container/VM and appended to the `auto-deploy` tag.

---

## Installed Packages

The Ansible post-deploy playbooks install a standard toolset on every deployed host. The exact package names vary by OS family — see `ansible/vars/Debian.yml`, `RedHat.yml`, and `Suse.yml` for the full per-family lists. The categories and tools are consistent across all families. VMs additionally include `qemu-guest-agent`. LXC containers additionally include `hwinfo`.

The following lists reflect the **Debian/Ubuntu** package names:

**Network tools**
`net-tools` · `iproute2` · `nmap` · `iputils-ping` · `traceroute` · `mtr` · `dnsutils` · `netcat-openbsd` · `socat` · `tcpdump` · `iperf3` · `fping` · `arp-scan` · `ethtool` · `iftop` · `nload` · `curl` · `wget`

**Editors**
`nano` · `vim`

**Data / linting**
`jq` · `yamllint` · `python3-jsonschema` · `shellcheck`

**Archive / compression**
`zip` · `unzip` · `bzip2` · `xz-utils` · `p7zip-full`

**SSH**
`sshpass` · `openssh-client`

**NTP**
`chrony`

**NFS client**
`nfs-common` · `nfs4-acl-tools`

**SNMP**
`snmpd` · `snmp` · `snmp-mibs-downloader`

**Hardware inventory**
`lshw` · `pciutils` · `usbutils` · `dmidecode` · `hdparm` · `smartmontools`

**System monitoring**
`htop` · `iotop` · `lsof` · `strace` · `sysstat` · `dstat`

**Development / misc**
`git` · `rsync` · `screen` · `tmux` · `tree` · `less` · `psmisc` · `procps` · `util-linux` · `python3` · `python3-pip` · `python3-venv` · `bc` · `at` · `cron` · `logrotate`

---

## Post-Deployment State

| Item | LXC | VM |
|---|---|---|
| SSH daemon | Installed, enabled, running | Installed, enabled, running |
| Root SSH login | `PermitRootLogin yes` | `PermitRootLogin yes` |
| Password auth | Enabled | Enabled |
| SSH key auth | Via `ssh-copy-id` from dev server | Via cloud-init injection at boot |
| `root` password | As specified at prompt | As specified at prompt |
| `admin` user | Created, member of `sudo`, same password | Created, member of `sudo`, same password |
| IP address | Static via netplan | Static (cloud-init) or DHCP |
| Timezone | America/Chicago | America/Chicago |
| NTP | chrony running | chrony running |
| SNMP | snmpd on UDP :161, community `YourSNMPCommunityString` | snmpd on UDP :161, community `YourSNMPCommunityString` |
| QEMU guest agent | n/a | Installed and enabled |
| Package state | `apt dist-upgrade` completed | Full system upgrade completed (apt/dnf/zypper per OS family) |
| Proxmox tag | `auto-deploy` | `auto-deploy` |
| DNS | A + PTR registered on BIND | A + PTR registered on BIND |
| Ansible inventory | Added to configured group | Added to configured group |
| Deployment file | `deployments/lxc/<hostname>.json` | `deployments/vms/<hostname>.json` |

---

## porter Integration

[porter](https://github.com/jlees/porter) is a dual-pane terminal file manager built for homelabs and sysadmin work. It is now functional and integrates with labinator for snapshot-based deployments.

### What porter does

porter lets you connect to a running reference server (via SSH/SFTP), browse its filesystem, and cherry-pick configuration files into an archive. The archive contains the files with full permissions and ownership preserved, plus a `manifest.yaml` that documents local users, active systemd services, and installed packages.

### The snapshot workflow

1. Configure and validate a reference server manually (or using labinator)
2. Use porter to take a filesystem snapshot of the reference server
3. Use porter's "Build Archive from Diff" to select changed/new files and produce a `.tar.gz` with a `manifest.yaml`
4. Store the archive alongside your deployment files
5. Use the archive at deploy time to lay down the reference configuration on a fresh container

### manifest.yaml

The sidecar manifest included in every porter archive contains:

| Field | Contents |
|---|---|
| `porter_manifest` | Version, timestamp, hostname, source directory |
| `os` | OS name, version, kernel, arch from `/etc/os-release` |
| `local_users` | Non-system users (uid 1000–60000) to create before extraction |
| `systemd_services.active` | Services that were running on the source — re-enable after extraction |
| `packages.installed` | Full package list from the source (informational reference) |
| `files` | Per-file entries: path, status (`MOD`/`NEW`), permissions, owner, sha256 |

### Labinator ingestion (planned)

When a porter archive is referenced in a deployment, labinator will:
1. Check `os` — warn if source and target OS families differ
2. Create any `local_users` not already present on the target
3. Extract the archive (`sudo tar -xzf <archive> -C /`)
4. Enable and start services listed in `systemd_services.active`
5. Optionally verify extracted file integrity against the `sha256` hashes

> **Note:** The manifest schema is documented in `snapshot-manifest-specs.md` in the project root.

### Current status

porter is functional. Snapshots can be taken from reference servers and the archives are structured correctly for labinator ingestion. The labinator ingestion step (reading a porter archive and applying it during deployment) is planned as a future feature.

---

## Ansible Playbooks

The playbooks are called automatically by the scripts but can be run independently for manual use or troubleshooting.

**Post-deploy LXC** (configure an existing container):
```bash
cd ansible
ansible-playbook -i 10.20.20.150, post-deploy.yml \
  -e container_hostname=myserver \
  -e password=yourpassword
```

**Post-deploy VM** (configure an existing VM):
```bash
cd ansible
ansible-playbook -i 10.20.20.200, post-deploy-vm.yml \
  -e vm_hostname=myvm \
  -e password=yourpassword \
  --private-key ~/.ssh/id_rsa
```

**Add DNS records** (register A + PTR on BIND):
```bash
cd ansible
ansible-playbook -i <dns.server from config.yaml>, add-dns.yml \
  -e new_hostname=myserver \
  -e new_ip=10.20.20.150 \
  -e new_fqdn=myserver.example.com \
  -e forward_zone_file=/var/lib/bind/example.com.hosts \
  -e reverse_zone_file=/var/lib/bind/220.220.10.in-addr.arpa.hosts \
  -u root
```

**Remove DNS records** (remove A + PTR from BIND):
```bash
cd ansible
ansible-playbook -i <dns.server from config.yaml>, remove-dns.yml \
  -e hostname=myserver \
  -e ip_address=10.20.20.150 \
  -e forward_zone_file=/var/lib/bind/example.com.hosts \
  -e reverse_zone_file=/var/lib/bind/220.220.10.in-addr.arpa.hosts \
  -u root
```

**Update inventory** (add host to development server inventory):
```bash
cd ansible
ansible-playbook -i dev.example.com, update-inventory.yml \
  -e new_hostname=myserver \
  -e new_ip=10.20.20.150 \
  -e inventory_file=/root/ansible/inventory/hosts \
  -e inventory_group=Linux \
  -e password=yourpassword \
  -e node_domain=example.com
```

**Remove from inventory** (remove host from development server inventory):
```bash
cd ansible
ansible-playbook -i dev.example.com, remove-from-inventory.yml \
  -e hostname=myserver \
  -e inventory_file=/root/ansible/inventory/hosts
```

---

## Troubleshooting

### "token_secret is CHANGEME" on startup
Edit `config.yaml` and paste your Proxmox API token secret into `proxmox.token_secret`.

---

### 401 Unauthorized connecting to Proxmox
- Verify `proxmox.token_name` is just the token ID (e.g. `vm-deploy`) — **not** the full `root@pam!vm-deploy` string
- Verify `proxmox.token_secret` is correct
- Confirm **Privilege Separation is disabled** on the token in the Proxmox UI

---

### "Failed to connect to Proxmox" / SSL errors
```bash
curl -k https://proxmox01.example.com:8006/api2/json/version
```
If `verify_ssl: true`, either set it to `false` or install a valid cert on Proxmox.

---

### 0.0 GB RAM shown for all nodes / no templates found
The API token lacks permissions. In the Proxmox UI, confirm:
- **Privilege Separation** is unchecked on the token
- The token's user has Administrator role (or equivalent) on `/`

---

### "No LXC templates found on [node]"
Download a template in Proxmox: **node → local storage → CT Templates → Templates → Download**.

---

### "No ISO-capable storage found on [node]"  *(VM only)*
The selected node has no storage configured with `iso` content type. In Proxmox, go to **Datacenter → Storage**, edit an existing storage, and add `ISO image` to its content types. `local` has this by default on most Proxmox installs.

---

### "No nodes pass the resource filter"
All online nodes are at or above the CPU/RAM thresholds for the requested size. The script warns and shows all nodes anyway. Consider requesting fewer resources, waiting for load to decrease, or checking whether any nodes are offline.

---

### "SSH key auth to proxmoxNN.example.com failed"
```bash
ssh -i ~/.ssh/id_rsa root@proxmox03.example.com echo OK
# If that fails:
ssh-copy-id -i ~/.ssh/id_rsa root@proxmox03.example.com
```
Update `proxmox.ssh_key` in `config.yaml` if you use a non-default key path.

---

### Container stuck at "Waiting for DHCP IP" (LXC)
- Verify the VLAN exists as a bridge on that node (Proxmox UI → node → Network)
- Confirm your DHCP server covers that VLAN
- Check manually: `ssh root@proxmox03.example.com "pct exec 142 -- ip -4 addr show eth0"`

---

### Static IP config failed during LXC bootstrap
```
Warning: static IP config failed: pct exec failed...
```
The container will retain a working DHCP IP but no static assignment. Ansible steps may still succeed. Fix manually afterward:
```bash
ssh root@10.20.20.150
# Edit /etc/netplan/01-static.yaml and run netplan apply
```

---

### Ansible post-deploy fails: "UNREACHABLE" (LXC)
1. Confirm the bootstrap step completed without errors
2. Test SSH directly: `ssh -o StrictHostKeyChecking=no root@10.20.20.150`
3. Confirm `sshpass` is installed on the controller: `which sshpass`
4. Check sshd status: `ssh root@proxmox03.example.com "pct exec 142 -- systemctl status ssh"`

The script prompts for a password retry on the first failure.

---

### VM stuck at "Waiting for SSH" or "Polling guest agent for IP"  *(VM only)*
- Check the VM console in the Proxmox web UI — cloud-init errors appear on the serial console
- Confirm the cloud image was imported correctly (VM should have a scsi0 disk in the Proxmox UI)
- For DHCP: confirm `qemu-guest-agent` is running in the VM: the Ubuntu cloud images include it by default, but if using a custom image it may need to be installed
- For static: confirm the IP and gateway are reachable on the VLAN

---

### cloud-init first-boot failed  *(VM only)*
```bash
# Check cloud-init logs on the VM
ssh root@10.20.20.200 'cloud-init status; cat /var/log/cloud-init.log | tail -50'
```
Common causes:
- Invalid SSH key path (`proxmox.ssh_key` in `config.yaml` points to a key that doesn't exist)
- Network config error (incorrect IP, prefix, or gateway)
- Cloud image doesn't support cloud-init (not applicable to images in `cloud-images.yaml`, but relevant for custom entries)

---

### "wget failed" downloading cloud image  *(VM only)*
The download runs on the Proxmox node via SSH, not on the controller. Check:
- The Proxmox node has internet access: `ssh root@proxmox03.example.com curl -I https://cloud-images.ubuntu.com`
- The URL in `cloud-images.yaml` is valid — test it in a browser
- There is enough disk space on the target storage: the download is ~600 MB

---

### "qm importdisk failed"  *(VM only)*
- Verify the storage pool has `images` content type enabled in Proxmox
- Verify there is enough free space on the storage (cloud images expand to their full uncompressed size after import)
- Verify the downloaded file is not corrupt: `ssh root@node 'file /path/to/cloud-images/image.img'`

---

### Skipping Ansible, DNS, or inventory registration

To disable specific integrations without failing, set flags in `config.yaml`:

```yaml
ansible:
  enabled: false              # Skip ALL Ansible post-deploy steps

dns:
  enabled: false              # Skip DNS registration

ansible_inventory:
  enabled: false              # Skip inventory update only
```

When `ansible.enabled` is false, Steps 5–7 are all skipped and the host must be configured manually. When `dns.enabled` or `ansible_inventory.enabled` is false, only those specific steps are skipped. All flags default to `true` if absent.

---

### DNS registration fails
- Confirm key-based SSH works to the DNS server: `ssh root@10.0.0.10 echo OK`
- Check BIND is running: `ssh root@10.0.0.10 systemctl status bind9`
- The forward zone file must exist and be writable
- If the reverse zone file doesn't exist, PTR is skipped (not an error); the A record still registers

---

### Host added to wrong inventory group
`ansible_inventory.group` in `config.yaml` is **case-sensitive** and must exactly match the `[GroupName]` header in the inventory file.

---

### Inventory update fails on development server
Inventory update failure is non-fatal — the script warns and continues. Add manually:
```bash
ssh root@dev.example.com
# Add under [Linux]:
myserver ansible_host=myserver.example.com ansible_python_interpreter=/usr/bin/python3
```

---

### Resource was created but something failed mid-way

**Option A — Decommission and re-run**

If a deployment file was saved before the failure:
```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json
# or
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json
```

If no deployment file exists, destroy manually in Proxmox:
```bash
ssh root@proxmox03.example.com
pct stop 142 && pct destroy 142 --purge    # LXC
qm stop 200 && qm destroy 200 --purge     # VM
```

**Option B — Fix in-place**

Re-run specific Ansible playbooks manually against the host's IP. See [Ansible Playbooks](#ansible-playbooks).

---

### Preflight check fails: "Static IP in use"
The IP address in your deploy file is already responding to ping. Another host is using it. Either:
- Decommission the existing host first: `./decomm_lxc.py --deploy-file deployments/lxc/myserver.json`
- Remove `ip_address` from the deployment JSON to use DHCP instead
- If you're intentionally replacing the host, add `"preflight": false` to the deploy file

---

### Preflight check warns: "DNS hostname check"
The hostname already resolves in DNS — an existing host is registered with that name. The existing host will be orphaned (no DNS record) after the new deployment registers its own record. Decommission the old host first with `decomm_lxc.py` or `decomm_vm.py` before redeploying.

---

### Preflight check warns: "Proxmox node SSH" for one node
One node in your `nodes:` list rejected SSH key auth. This is a warning (non-fatal) because deployments target a single node and the API still works. Fix:
```bash
ssh-copy-id -i ~/.ssh/id_rsa root@proxmoxNN.example.com
```

---

### "--silent" exits 1 on preflight warnings
`--silent` mode is strict — it exits 1 on both warnings and fatal failures. To allow warnings through in silent/automated mode, add `--yolo` alongside `--silent`. To skip preflight entirely for a specific host, set `"preflight": false` in the deployment JSON.

---

### "proxmoxer not installed" / Python import errors
```bash
source .venv/bin/activate
python3 deploy_lxc.py
```
Or re-run `./setup.sh` to reinstall all dependencies.

---

## Submitting an Issue

> This tool is provided **as-is** without warranty or active support. Issue submissions are welcomed on a best-effort basis.

**Before opening an issue:**
- Read the [Troubleshooting](#troubleshooting) section
- Verify your `config.yaml` is correctly filled in
- Verify all [Prerequisites](#prerequisites) are met

**Open a GitHub Issue with the following:**

```
## Issue Report — vm-onboard

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
| vm-onboard version / commit | git rev-parse --short HEAD |

### Which script and step failed?
**LXC (deploy_lxc.py):**
- [ ] Startup / config / Proxmox connection
- [ ] Step 1 — Container creation
- [ ] Step 2 — Container start
- [ ] Step 3 — DHCP IP assignment
- [ ] Step 4 — SSH bootstrap / static IP (pct exec)
- [ ] Step 5 — Ansible post-deploy
- [ ] Step 6 — DNS registration
- [ ] Step 7 — Ansible inventory update
- [ ] decomm_lxc.py — decommission

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

### What did you expect to happen?

### What actually happened?

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

*Built for a Proxmox homelab. Shared without warranty. Use at your own risk.*
