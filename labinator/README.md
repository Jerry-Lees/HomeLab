# labinator

A command-line wizard for provisioning, configuring, and onboarding LXC containers and QEMU virtual machines in a Proxmox VE homelab cluster. Handles the full lifecycle from resource creation through post-deployment configuration, DNS registration, Ansible inventory registration, TTL-based auto-expiry, and batch cleanup — all from a single guided session or a pre-built deployment file. Companion decommission scripts reverse the process cleanly.

> **Disclaimer:** This tool is provided **as-is**, without warranty or support of any kind. It was built for a specific homelab environment and is shared for reference and reuse. See [Submitting an Issue](#submitting-an-issue) if you encounter a problem.

---

## Table of Contents

- [What It Does](#what-it-does)
  - [deploy_lxc.py](#deploy_lxcpy)
  - [decomm_lxc.py](#decomm_lxcpy)
  - [deploy_vm.py](#deploy_vmpy)
  - [decomm_vm.py](#decomm_vmpy)
  - [cleanup_tagged.py](#cleanup_taggedpy)
  - [expire.py](#expirepy)
- [Project Layout](#project-layout)
- [Supported Guest Operating Systems](#supported-guest-operating-systems)
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
  - [deploy_lxc.py flags](#deploy_lxcpy-flags)
  - [deploy_vm.py flags](#deploy_vmpy-flags)
  - [decomm_lxc.py flags](#decomm_lxcpy-flags)
  - [decomm_vm.py flags](#decomm_vmpy-flags)
  - [expire.py flags](#expirepy-flags)
  - [cleanup_tagged.py flags](#cleanup_taggedpy-flags)
  - [Flag interaction table — preflight behavior](#flag-interaction-table--preflight-behavior)
  - [Common combinations](#common-combinations)
- [Preflight Checks](#preflight-checks)
  - [Checks performed](#checks-performed)
  - [Skipping preflight for a specific deployment](#skipping-preflight-for-a-specific-deployment)
- [Deployment History Log](#deployment-history-log)
- [TTL / Auto-Expiry](#ttl--auto-expiry)
  - [Setting a TTL at deploy time](#setting-a-ttl-at-deploy-time)
  - [TTL format](#ttl-format)
  - [expire.py workflow](#expirepy-workflow)
- [Deployment Files](#deployment-files)
  - [LXC deployment file](#lxc-deployment-file)
  - [VM deployment file](#vm-deployment-file)
  - [.gitignore behavior](#gitignore-behavior)
- [Deployment Defaults](#deployment-defaults)
- [Package Profiles](#package-profiles)
- [Installed Packages](#installed-packages)
- [Post-Deployment State](#post-deployment-state)
- [Usage — deploy_lxc.py](#usage--deploy_lxcpy)
  - [Interactive Mode](#interactive-mode)
  - [Deploy from File](#deploy-from-file)
  - [Silent Mode](#silent-non-interactive-mode)
  - [Validate Mode](#validate-mode)
  - [Dry-Run Mode](#dry-run-mode)
  - [Preflight Mode](#preflight-mode)
  - [Walkthrough: LXC Prompt Order](#walkthrough-lxc-prompt-order)
  - [The 7 LXC Deployment Steps](#the-7-lxc-deployment-steps)
- [Usage — decomm_lxc.py](#usage--decomm_lxcpy)
- [Usage — deploy_vm.py](#usage--deploy_vmpy)
  - [Interactive Mode](#interactive-mode-1)
  - [Deploy from File](#deploy-from-file-1)
  - [Silent Mode](#silent-non-interactive-mode-1)
  - [Validate Mode](#validate-mode-1)
  - [Dry-Run Mode](#dry-run-mode-1)
  - [Preflight Mode](#preflight-mode-1)
  - [Walkthrough: VM Prompt Order](#walkthrough-vm-prompt-order)
  - [The 7 VM Deployment Steps](#the-7-vm-deployment-steps)
- [Usage — decomm_vm.py](#usage--decomm_vmpy)
- [Usage — expire.py](#usage--expirepy)
  - [--check output](#--check-output)
  - [--reap behavior](#--reap-behavior)
  - [--renew examples](#--renew-examples)
- [Usage — cleanup_tagged.py](#usage--cleanup_taggedpy)
  - [IP resolution order](#ip-resolution-order)
  - [Per-resource actions](#per-resource-actions)
  - [Action List File](#action-list-file)
  - [Summary panel](#summary-panel)
- [examples/ Directory](#examples-directory)
- [Providers](#providers)
  - [DNS provider (BIND)](#dns-provider-bind)
  - [Ansible Inventory provider (flat_file)](#ansible-inventory-provider-flat_file)
  - [Disabling a provider](#disabling-a-provider)
- [Multi-Node / Failover](#multi-node--failover)
- [Ansible Playbooks](#ansible-playbooks)
- [porter Integration](#porter-integration)
- [Troubleshooting](#troubleshooting)
- [Known OS Support](#known-os-support)
- [Submitting an Issue](#submitting-an-issue)
- [known-bugs.md](#known-bugsmd)

---

## What It Does

labinator manages the complete lifecycle of Proxmox resources across six scripts:

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
11. Registers DNS A and PTR records on the BIND server
12. Updates the Ansible inventory on the development server
13. Runs preflight checks at startup
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

### cleanup_tagged.py

Scans every node in the cluster for VMs and LXC containers tagged with a given Proxmox tag (default: `auto-deploy`) and lets you decide what to do with each one — interactively, or via a pre-built action list file for fully automated cleanup windows. Each resource can be kept, promoted to production (tag removed), or permanently decommissioned.

### expire.py

Manages deployment TTLs. Scans all deployment JSON files for an `expires_at` field and reports on or acts on expired and expiring-soon deployments:

- **`--check`** — prints a table of expired and expiring-soon hosts. No Proxmox connection needed.
- **`--reap`** — decommissions all expired hosts (stop/destroy, DNS, inventory). Same full pipeline as the decomm scripts.
- **`--renew HOSTNAME --ttl Xd`** — extends the TTL of a deployment by updating its JSON file.

Deployments without an `expires_at` field are ignored entirely.

---

## Project Layout

```
labinator/
├── deploy_lxc.py                  # LXC provisioning wizard
├── decomm_lxc.py                  # LXC decommission script
├── deploy_vm.py                   # QEMU VM provisioning wizard
├── decomm_vm.py                   # QEMU VM decommission script
├── cleanup_tagged.py              # Cluster-wide tag-based cleanup (keep/promote/decomm)
├── expire.py                      # Deployment TTL manager (check/reap/renew)
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
│   └── lib.py                     # Shared functions used by all 6 scripts
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
git clone https://github.com/Jerry-Lees/HomeLab.git
cd HomeLab/labinator
cp config.yaml.example config.yaml
./setup.sh
```

`setup.sh` will:
- Update apt cache if stale
- Install missing system packages (`ansible`, `sshpass`, `openssh-client`, build tools)
- Create a Python virtualenv at `.venv/`
- Install all Python requirements from `requirements.txt`
- Verify every required Python module imports correctly

The scripts auto-activate the virtualenv at startup, so you can run them with `python3 deploy_lxc.py` directly without sourcing `.venv/bin/activate` first.

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

All scripts support `--config FILE` to point at an alternate config file. The default is `config.yaml` in the project root.

The reference below documents every key:

```yaml
proxmox:
  # Use 'host' for a single node, or 'hosts' list for automatic failover.
  # Any node in the cluster works; they all share the same API state.
  # host: proxmox01.example.com
  hosts:
    - proxmox01.example.com       # Tried in order; first reachable one is used
    - proxmox02.example.com

  # Proxmox API user (realm included)
  user: root@pam

  # API Token ID — the short name only, NOT the full "user!tokenid" string.
  # e.g. if the full token shown in Proxmox is root@pam!vm-deploy, put: vm-deploy
  token_name: vm-deploy

  # API Token secret — the UUID shown once at creation time.
  # The scripts refuse to start if this is still "CHANGEME".
  token_secret: CHANGEME

  # SSH key authorized on all Proxmox nodes as root.
  # Used for pct exec (LXC bootstrap), cloud image download, and qm importdisk.
  ssh_key: ~/.ssh/id_rsa

  # Domain suffix appended to node names to form SSH hostnames.
  # e.g. node "proxmox01" -> SSH to "proxmox01.example.com"
  node_domain: example.com

  # Verify SSL certificate for the Proxmox API. Set true if using valid certs.
  verify_ssl: false

# Known cluster nodes — used for display and SSH preflight checks.
# The live node list is also queried from the API.
nodes:
  - proxmox01
  - proxmox02
  - proxmox03

# Default values pre-filled at each interactive prompt.
# All are overridable at prompt time, and can be overridden per-deployment via deploy files.
defaults:
  cpus: 2                         # vCPUs
  memory_gb: 4                    # RAM in GB
  disk_gb: 100                    # Root disk in GB
  vlan: 220                       # VLAN tag (creates vmbr0.220)
  bridge: vmbr0                   # Proxmox bridge interface
  root_password: changeme         # Default password shown at prompt (change it)
  addusername: admin              # Secondary user created on every deployed container/VM
  swap_mb: 512                    # Swap in MB (LXC only)
  onboot: true                    # Auto-start when Proxmox node boots
  unprivileged: true              # LXC: run as unprivileged container (recommended)
  firewall_enabled: false         # Enable Proxmox firewall on the network interface
  cpu_threshold: 0.85             # Skip nodes at or above this CPU load (0.0–1.0)
  ram_threshold: 0.95             # Skip nodes at or above this RAM usage (0.0–1.0)
  searchdomain: example.com       # DNS search domain injected into new hosts
  nameserver: "10.0.0.1 10.0.0.2" # Space-separated DNS server IPs
  template: ubuntu-24.04-standard_24.04-2_amd64.tar.zst  # Default LXC template (matched by filename)

# Package profiles — named sets of packages + Proxmox tags for a server role.
# Select a profile at deploy time to install a consistent toolset.
# Install order: standard baseline → profile packages → extra_packages.
# Tag names must be alphanumeric, hyphens, or underscores — no spaces.
package_profiles:
  web-server:
    packages: [nginx, certbot, python3-certbot-nginx, ufw]
    tags: [WWW]
  database:
    packages: [mariadb-server, mariadb-client]
    tags: [DB, MariaDB]
  docker-host:
    packages: [docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin]
    tags: [Docker]
  monitoring-node:
    packages: [prometheus-node-exporter, snmpd]
    tags: [Monitoring]
  dev-tools:
    packages: [git, vim, tmux, make, python3-pip]
    tags: [Dev]
  nfs-server:
    packages: [nfs-kernel-server, nfs-common]
    tags: [NFS, Storage]

# Global preflight toggle. Set false to skip preflight checks for all deployments.
# Per-deployment override: add "preflight": false to the deployment JSON.
preflight: true

# DNS registration via BIND.
# provider: currently only 'bind' is implemented.
# Future providers: powerdns, technitium (planned).
dns:
  enabled: true
  provider: bind
  server: 10.0.0.10               # IP of your BIND DNS server
  ssh_user: root
  forward_zone_file: /var/lib/bind/example.com.hosts
  # Reverse zone file is derived automatically from the IP at deploy time.
  # e.g. 10.20.20.x → /var/lib/bind/20.20.10.in-addr.arpa.hosts

# Ansible post-deploy configuration.
# Set enabled: false to skip ALL Ansible post-deploy steps for every deployment.
# DNS registration and inventory update are controlled separately below.
ansible:
  enabled: true

# Ansible inventory update settings.
# provider: currently only 'flat_file' is implemented.
# Future providers: awx, semaphore (planned).
ansible_inventory:
  enabled: true
  provider: flat_file
  server: dev.example.com         # Server holding the master inventory file
  user: root                      # SSH user for connecting to the inventory server
  file: /root/ansible/inventory/hosts  # Full path to the inventory file on the server
  group: Linux                    # Ansible group to add new hosts into (CASE-SENSITIVE)

# SNMP configuration applied to all deployed containers/VMs.
snmp:
  community: your-snmp-community
  source: default                 # Restrict to source networks ('default' = any)
  location: Homelab
  contact: admin@example.com

# NTP servers configured via chrony on all deployed hosts.
ntp:
  servers:
    - pool.ntp.org
    - time.nist.gov

# Post-deployment health check.
# After Ansible completes, verifies the host is reachable and SSH works.
# Failure prints a warning but does NOT roll back the deployment.
health_check:
  enabled: false                  # Set true to enable
  timeout_seconds: 30             # Per-attempt TCP/SSH timeout
  retries: 5                      # TCP port-22 attempts before giving up

# Timezone for all new containers/VMs.
timezone: America/Chicago

# VM-specific settings (deploy_vm.py only).
vm:
  # Proxmox storage to pre-select in the cloud image browser.
  # Must have 'iso' content type configured in Proxmox.
  # Cloud images are stored at {storage_path}/cloud-images/ — not in template/iso/.
  # Leave blank to always prompt without a pre-selection.
  default_cloud_image_storage: local

  cpu_type: x86-64-v2-AES         # VM CPU type (use kvm64 for max compatibility)
  machine: q35                    # VM machine type (q35 or i440fx)
  bios: seabios                   # VM BIOS (seabios or ovmf for UEFI)
  storage_controller: virtio-scsi-pci  # virtio-scsi-pci, lsi, megasas, pvscsi
  nic_driver: virtio              # VM NIC driver (virtio, e1000, rtl8139)
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

**Auto-recovery:** If a deployment file references an image that no longer exists on the storage, the script looks up the download URL in `cloud-images.yaml` by filename, then falls back to the URL stored in the deployment file. If neither source has a URL, the script fails with a clear error directing you to add the entry to `cloud-images.yaml`.

**Fedora note:** Fedora cloud image URLs are version-specific (no stable `current/` redirect). Update the URL in `cloud-images.yaml` when upgrading to a new Fedora release.

---

## Command-Line Reference

### deploy_lxc.py flags

| Flag | Description |
|---|---|
| `--deploy-file FILE` | Load a deployment JSON to pre-fill all prompts (or drive `--silent`) |
| `--silent` | Non-interactive: use all values from `--deploy-file` without prompting. Requires `--deploy-file`. Exits 1 on any preflight warning or failure. |
| `--dry-run` | Validate config + deploy file and print a full step-by-step plan without making any changes |
| `--validate` | Parse and validate `config.yaml` (and deploy file if given) then exit. No Proxmox connection. |
| `--preflight` | Run all preflight connectivity and dependency checks then exit. Add `--deploy-file` to also check DNS hostname and static IP. |
| `--yolo` | Run preflight but continue through warnings without prompting. Fatal failures still block the deploy. |
| `--ttl TTL` | Set a TTL for this deployment (e.g. `7d`, `24h`, `2w`, `30m`). Stores `ttl` and `expires_at` in the deployment JSON. Tracked by `expire.py`. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

### deploy_vm.py flags

| Flag | Description |
|---|---|
| `--deploy-file FILE` | Load a deployment JSON to pre-fill all prompts (or drive `--silent`) |
| `--silent` | Non-interactive: use all values from `--deploy-file` without prompting. Requires `--deploy-file`. Exits 1 on any preflight warning or failure. |
| `--dry-run` | Validate config + deploy file and print a full step-by-step plan without making any changes |
| `--validate` | Parse and validate `config.yaml` (and deploy file if given) then exit. No Proxmox connection. |
| `--preflight` | Run all preflight connectivity and dependency checks then exit. Add `--deploy-file` to also check DNS hostname and static IP. |
| `--yolo` | Run preflight but continue through warnings without prompting. Fatal failures still block the deploy. |
| `--ttl TTL` | Set a TTL for this deployment (e.g. `7d`, `24h`, `2w`, `30m`). Stores `ttl` and `expires_at` in the deployment JSON. Tracked by `expire.py`. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

### decomm_lxc.py flags

| Flag | Description |
|---|---|
| `--deploy-file FILE` | Load deployment JSON directly, skipping the interactive list |
| `--purge` | Also delete the local deployment JSON file after decommissioning |
| `--silent` | Skip the confirmation challenge. Requires `--deploy-file`. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

### decomm_vm.py flags

| Flag | Description |
|---|---|
| `--deploy-file FILE` | Load deployment JSON directly, skipping the interactive list |
| `--purge` | Also delete the local deployment JSON file after decommissioning |
| `--silent` | Skip the confirmation challenge. Requires `--deploy-file`. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

### expire.py flags

| Flag | Description |
|---|---|
| `--check` | Print a table of expired and expiring-soon hosts. Default mode if no other mode is given. No Proxmox connection needed. |
| `--reap` | Connect to Proxmox and decommission all expired deployments. Full pipeline: stop+destroy, DNS, inventory. |
| `--renew HOSTNAME` | Extend the TTL of a deployment. Updates `ttl` and `expires_at` in the JSON file. Requires `--ttl`. |
| `--ttl TTL` | New TTL for `--renew` (e.g. `7d`, `24h`, `2w`, `30m`) |
| `--kind lxc\|vm` | Disambiguate `--renew` when both `deployments/lxc/` and `deployments/vms/` contain the same hostname |
| `--warning TTL` | How far ahead to flag as expiring-soon (default: `48h`). Accepts same format as `--ttl`. |
| `--silent` | Skip the confirmation challenge when reaping |
| `--purge` | Delete deployment JSON files after successful reap. Requires `--reap`. |
| `--yolo` | Continue through warnings; blocked by failures (same semantics as deploy scripts) |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

### cleanup_tagged.py flags

| Flag | Description |
|---|---|
| `--tag TAG` | Proxmox tag to scan for. Default: `auto-deploy`. Alphanumeric, hyphens, underscores, and dots only; max 64 chars. Validated at startup. |
| `--list-file FILE` | Load a pre-built JSON action list. See [Action List File](#action-list-file). |
| `--action ACTION` | *(not a flag — actions are per-resource, selected interactively or via `--list-file`)* |
| `--silent` | Skip interactive prompts and the confirmation challenge. Requires `--list-file`. |
| `--dry-run` | Print the resource table and exit. No changes made. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

### Flag interaction table — preflight behavior

> | Flags | Warnings | Fatal failures |
> |---|---|---|
> | _(none)_ | Continue / Retry / Abort prompt | Continue / Retry / Abort prompt |
> | `--yolo` | Continue silently | Continue / Retry / Abort prompt |
> | `--silent` | Exit 1 | Exit 1 |
> | `--silent --yolo` | Continue silently | Exit 1 |
> | `"preflight": false` in deploy file | Skipped entirely | Skipped entirely |

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
| Deploy with a 7-day TTL | `./deploy_vm.py --deploy-file deployments/vms/myvm.json --ttl 7d` |
| Check for expired/expiring deployments | `./expire.py --check` |
| Reap expired deployments, delete JSON files | `./expire.py --reap --purge` |
| Extend a deployment's TTL | `./expire.py --renew myserver --ttl 7d` |
| See all tagged resources without touching them | `./cleanup_tagged.py --dry-run` |
| Execute a pre-planned cleanup unattended | `./cleanup_tagged.py --list-file plan.json --silent` |

---

## Preflight Checks

Both deploy scripts run a preflight check suite automatically at the start of every deployment — **before** any prompts are shown or Proxmox resources are created. The results are displayed as a table. If all checks pass you see a single `✓ Preflight checks passed.` line. If any check fails or warns, the full table is shown.

Run standalone (check your environment and exit without deploying):
```bash
./deploy_lxc.py --preflight
./deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json
./deploy_vm.py --preflight
./deploy_vm.py --preflight --deploy-file deployments/vms/myvm.json
```

### Checks performed

| Check | Fatal? | What it verifies |
|---|---|---|
| Config valid | Yes | `config.yaml` parses without errors and all required fields are present |
| Proxmox API reachable | Yes | TCP connect to port 8006 on each host in `proxmox.hosts`. Reports `X/Y host(s)` with names of unreachable hosts. |
| Proxmox API auth | Yes | API token is accepted (`GET /version`) |
| SSH key on disk | Warning | `proxmox.ssh_key` file exists at the configured path |
| Proxmox node SSH | Warning | SSH key is accepted by each node in the `nodes:` list. Reports `X/Y node(s)` with names of failing nodes. |
| Ansible installed | Yes | `ansible-playbook` is on PATH (skipped if `ansible.enabled: false`) |
| sshpass installed (LXC) | Yes | `sshpass` is on PATH (LXC deploy only) |
| DNS server reachable | Warning | TCP connect to port 22 on `dns.server` (skipped if `dns.enabled: false`) |
| DNS server SSH auth | Warning | Key-based SSH to `dns.ssh_user@dns.server` succeeds |
| DNS hostname check | Warning | If `--deploy-file` provided: queries DNS server directly for the hostname — warns if a record already exists |
| Static IP in use | **Fatal** | If `--deploy-file` provided and `ip_address` is set: pings the IP — **fails if it responds** (duplicate IP prevention) |
| Inventory server reachable | Warning | TCP connect to port 22 on `ansible_inventory.server` |
| Inventory SSH auth | Warning | Key-based SSH to the inventory server succeeds |

Fatal failures block the deploy entirely. Warning-level checks print a yellow `⚠ warn` row but allow the deploy to proceed after the Continue/Retry/Abort prompt (unless `--silent` is active, in which case any issue exits 1).

### Skipping preflight for a specific deployment

Add `"preflight": false` to the deployment JSON:
```json
{
  "hostname": "myserver",
  "preflight": false
}
```
This is useful for hosts that are intentionally replacing an existing server (where the DNS and IP will already be in use) or in automation where you've pre-validated externally.

---

## Deployment History Log

Every successful deploy and decommission appends a JSON line to `deployments/history.log`. The log is created automatically on first use. It is append-only — nothing is ever deleted from it.

**Format:** one JSON object per line (newline-delimited JSON).

**Fields logged for each event:**

| Field | Description |
|---|---|
| `timestamp` | ISO 8601 UTC timestamp (`YYYY-MM-DDTHH:MM:SS`) |
| `user` | OS username from `$USER` or `$LOGNAME` |
| `action` | `"deploy"` or `"decomm"` |
| `type` | `"lxc"` or `"vm"` |
| `hostname` | Hostname of the resource |
| `node` | Proxmox node the resource was on |
| `vmid` | Proxmox VMID |
| `ip` | IP address (`assigned_ip` if DHCP, else `ip_address`) |
| `result` | `"success"`, `"decommissioned"`, or `"already_gone"` |
| `duration_seconds` | Wall-clock seconds from start to completion |

**Example entries:**
```json
{"timestamp": "2026-03-06T14:22:00", "user": "dad", "action": "deploy", "type": "lxc", "hostname": "myserver", "node": "proxmox03", "vmid": 142, "ip": "10.20.20.150", "result": "success", "duration_seconds": 187}
{"timestamp": "2026-03-13T10:05:00", "user": "dad", "action": "decomm", "type": "lxc", "hostname": "myserver", "node": "proxmox03", "vmid": 142, "ip": "10.20.20.150", "result": "decommissioned", "duration_seconds": 23}
```

The log is not gitignored, but `deployments/` individual JSON files are. It can be queried with standard tools:
```bash
# Show all deploys in the last 7 days
grep '"action": "deploy"' deployments/history.log | python3 -c "import sys,json; [print(json.loads(l)['hostname']) for l in sys.stdin]"
```

---

## TTL / Auto-Expiry

### Setting a TTL at deploy time

Pass `--ttl` to either deploy script to mark a deployment as temporary:

```bash
./deploy_lxc.py --deploy-file deployments/lxc/test-box.json --ttl 7d
./deploy_vm.py --deploy-file deployments/vms/staging-vm.json --ttl 24h
```

The TTL is stored in the deployment JSON as two fields:

```json
"ttl": "7d",
"expires_at": "2026-03-13T14:22:00.000000+00:00"
```

`expires_at` is calculated at deploy time as `now() + TTL`. It is an ISO 8601 UTC timestamp. Deployments without `expires_at` are ignored by `expire.py`.

### TTL format

| Unit | Meaning | Example |
|---|---|---|
| `m` | minutes | `30m` |
| `h` | hours | `24h` |
| `d` | days | `7d` |
| `w` | weeks | `2w` |

All TTL arguments across `deploy_lxc.py`, `deploy_vm.py`, and `expire.py` use the same format.

### expire.py workflow

```bash
# 1. Check what's expired or expiring soon (default: warn within 48h)
./expire.py --check

# 2. Change the warning window
./expire.py --check --warning 3d

# 3. Reap all expired deployments (interactive confirmation per host)
./expire.py --reap

# 4. Reap without confirmation prompts
./expire.py --reap --silent

# 5. Reap and also delete the deployment JSON files
./expire.py --reap --purge

# 6. Reap, delete JSON files, no prompts
./expire.py --reap --purge --silent

# 7. Extend a deployment's TTL (no Proxmox connection needed)
./expire.py --renew myserver --ttl 7d

# 8. Disambiguate when lxc/ and vms/ both have the hostname
./expire.py --renew myserver --ttl 7d --kind lxc
./expire.py --renew myserver --ttl 7d --kind vm
```

`--reap` runs the same full decommission pipeline as `decomm_lxc.py` / `decomm_vm.py`: stop+destroy the Proxmox resource, remove DNS A + PTR records, remove from Ansible inventory. If the Proxmox resource is already gone (stale JSON file), it proceeds with DNS and inventory cleanup and reports the host as **Already gone**.

`--purge` only deletes the JSON files for successfully decommissioned hosts and hosts that were already gone. Hosts that abort (confirmation failed or an error occurred) keep their JSON files.

---

## Deployment Files

Deployment files are JSON saved after each successful deployment. They serve as the input to `--deploy-file` (interactive re-run with pre-filled defaults), `--silent` (fully automated re-run), and the decommission scripts.

### LXC deployment file

Saved to `deployments/lxc/<hostname>.json`:

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
  "assigned_ip": "10.20.20.150",
  "prefix_len": "24",
  "deployed_at": "2026-03-06 14:22:00",
  "ttl": "7d",
  "expires_at": "2026-03-13T14:22:00.000000+00:00",
  "preflight": true
}
```

**Key fields:**

- `ip_address` — the configured IP (may be a static address). Used for DNS registration and static IP preflight check.
- `assigned_ip` — the actual IP assigned to the container (same as `ip_address` for static assignments; the DHCP-assigned IP for DHCP deployments). Used by the decomm pipeline for DNS record removal.
- `ttl` / `expires_at` — present only if `--ttl` was used at deploy time. `expire.py` reads `expires_at` to determine expiry status.
- `preflight` — controls whether preflight checks run before this deployment. Defaults to `true`. Set to `false` to skip all preflight for this specific host.
- `template_volid` — if this template is no longer on the node, the script falls back to the first available template and prints a warning.

### VM deployment file

Saved to `deployments/vms/<hostname>.json`:

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
  "ip_address": "dhcp",
  "assigned_ip": "10.20.20.200",
  "prefix_len": "24",
  "gateway": "10.20.20.1",
  "deployed_at": "2026-03-06 10:00:00",
  "ttl": "1d",
  "expires_at": "2026-03-07T10:00:00.000000+00:00",
  "preflight": true
}
```

**Key fields:**

- `cloud_image_storage` — the Proxmox storage where the cloud image lives.
- `cloud_image_filename` — the filename of the image within the `cloud-images/` directory.
- `cloud_image_url` — the download URL, stored as a fallback for auto-recovery if the image is missing.
- `image_refresh` — if `true`, the image is always re-downloaded before import. Set automatically based on whether you selected an existing image (`false`) or a "Download:" catalog entry (`true`).
- `ip_address` — `"dhcp"` if DHCP was used, otherwise the configured static IP.
- `assigned_ip` — present for DHCP deployments; records the IP assigned at boot time. Also set for static deployments (same as `ip_address`).
- `gateway` — gateway for static IP configurations; absent for DHCP.
- `extra_packages` — optional list of one-off packages installed on top of the baseline and profile. Added by the wizard if you enter any at the prompt.

### .gitignore behavior

The `.gitignore` excludes all deployment files matching `deployments/lxc/*.json` and `deployments/vms/*.json` **except** files beginning with `example-`. This means:

- Real deployment files (which contain passwords and IP addresses) are never committed
- Example files (`example-lxc.json`, `example-vm.json`) are tracked and serve as templates

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

Package profiles are named sets of packages defined in `config.yaml` under `package_profiles:`. At deploy time you select a profile (or none), and its packages are installed on top of the standard baseline. One-off packages not fitting any profile can be added via `extra_packages` in the deployment file.

**Install order:** standard baseline → profile packages → extra packages

Profiles also apply Proxmox tags, labeling deployed hosts by role in the Proxmox UI.

### Built-in profiles

| Profile | Packages | Tags |
|---|---|---|
| `web-server` | nginx, certbot, python3-certbot-nginx, ufw | WWW |
| `database` | mariadb-server, mariadb-client | DB, MariaDB |
| `docker-host` | docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin | Docker |
| `monitoring-node` | prometheus-node-exporter, snmpd | Monitoring |
| `dev-tools` | git, vim, tmux, make, python3-pip | Dev |
| `nfs-server` | nfs-kernel-server, nfs-common | NFS, Storage |

> **Docker CE note:** `docker-ce` is not in the standard Ubuntu/Debian repositories. When the `docker-host` profile is selected, the Ansible playbook automatically sets up Docker's official apt repository before installing. No manual configuration is needed.

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

The Ansible post-deploy playbooks install a standard toolset on every deployed host. The exact package names vary by OS family — see `ansible/vars/Debian.yml`, `RedHat.yml`, and `Suse.yml` for the full per-family lists. VMs additionally include `qemu-guest-agent`. LXC containers additionally include `hwinfo`.

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
| SNMP | snmpd on UDP :161 | snmpd on UDP :161 |
| QEMU guest agent | n/a | Installed and enabled |
| Package state | `apt dist-upgrade` completed | Full system upgrade (apt/dnf/zypper per OS) |
| Proxmox tag | `auto-deploy` | `auto-deploy` |
| DNS | A + PTR registered on BIND | A + PTR registered on BIND |
| Ansible inventory | Added to configured group | Added to configured group |
| Deployment file | `deployments/lxc/<hostname>.json` | `deployments/vms/<hostname>.json` |

---

## Usage — deploy_lxc.py

### Interactive Mode

```bash
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

Skips all interactive prompts and deploys using the values in the file. `--silent` requires `--deploy-file`. In `--silent` mode, any preflight warning or fatal failure causes immediate exit 1.

### Validate Mode

```bash
python3 deploy_lxc.py --validate
python3 deploy_lxc.py --validate --deploy-file deployments/lxc/myserver.json
```

Parses and validates `config.yaml` (and the deployment file if provided) without connecting to Proxmox or making any changes. Exits 0 on success, 1 on error. Useful for CI checks.

### Dry-Run Mode

```bash
python3 deploy_lxc.py --dry-run
python3 deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json
```

Validates config and deployment file, then prints a full summary of what would happen — hostname, node, template, resources, packages, tags, and each numbered step — without connecting to Proxmox, running Ansible, or modifying anything.

### Preflight Mode

```bash
python3 deploy_lxc.py --preflight
python3 deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json
```

Runs all preflight checks and exits without deploying. Without `--deploy-file`, checks infrastructure only. With `--deploy-file`, also checks whether the hostname already has a DNS record and whether the static IP is already in use.

Add `--silent` to make it script-friendly (exits 0 = all clear, 1 = any issue). Add `--yolo` to exit 0 on warnings and only fail on fatal checks.

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
Root / admin user password:          [changeme]
```

**3. Node selection** (filtered by requested resources)
```
Select Proxmox node (★ = most free RAM; 2 node(s) hidden — over resource threshold):
  ★ proxmox03  —  54.2 GB free / 128.0 GB RAM  (CPU: 18%)
    proxmox02   —  28.4 GB free / 64.0 GB RAM   (CPU: 12%)
```
Nodes that would push CPU above `cpu_threshold` or RAM above `ram_threshold` after allocation are hidden.

**4. OS Template**
```
Select OS template (Ubuntu templates listed first):
  [Net-Images] ubuntu-24.04-standard_24.04-2_amd64.tar.zst
  [local] debian-12-standard_12.7-1_amd64.tar.zst
```
Queried live from the selected node. Ubuntu versions are listed first.

**5. Storage pool** (only shown if more than one pool exists on the node)

**6. Package profile** (optional — select a role or skip)

**7. Confirmation summary and pre-creation resource check**

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
python3 decomm_lxc.py                                                       # interactive list
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json          # skip list
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --purge  # also delete JSON
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json --silent # skip confirmation
```

The `--deploy-file` flag skips the numbered list and loads the specified file directly. The scary confirmation still runs unless `--silent` is also passed.

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

### Dry-Run Mode

```bash
python3 deploy_vm.py --dry-run
python3 deploy_vm.py --dry-run --deploy-file deployments/vms/myvm.json
```

### Preflight Mode

```bash
python3 deploy_vm.py --preflight
python3 deploy_vm.py --preflight --deploy-file deployments/vms/myserver.json
```

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
  noble-server-cloudimg-amd64.img   (623 MB)   <- already on storage
  --- Download from catalog ---
  Download: Ubuntu 24.04 LTS (Noble Numbat)
  Download: Ubuntu 22.04 LTS (Jammy Jellyfish)
  Download: Debian 12 (Bookworm)
  ...
  <- Back to storage selection
```
Selecting an existing file uses it without downloading. Selecting a "Download:" entry downloads the image to `{storage_path}/cloud-images/` on the node before importing. The Back option returns to the storage picker.

**8. Storage pool** for the VM disk (images content type)

**9. Package profile** (optional — select a role or skip)

**10. Confirmation summary**

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
Identical behavior to the LXC deploy script — see [The 7 LXC Deployment Steps](#the-7-lxc-deployment-steps) above.

---

## Usage — decomm_vm.py

```bash
python3 decomm_vm.py                                                    # interactive list
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json           # skip list
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json --purge   # also delete JSON
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json --silent  # skip confirmation
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

## Usage — expire.py

Manages deployment TTLs. Scans `deployments/lxc/*.json` and `deployments/vms/*.json` for the `expires_at` field and reports on or acts on expired and expiring-soon deployments. Deployments without `expires_at` are ignored.

```bash
./expire.py --check                               # show expired and expiring-soon (default)
./expire.py --check --warning 3d                  # flag deployments expiring within 3 days
./expire.py --reap                                # decommission all expired deployments
./expire.py --reap --silent                       # reap without confirmation prompts
./expire.py --reap --purge                        # reap and delete JSON files
./expire.py --reap --purge --silent               # reap, delete JSON, no prompts
./expire.py --renew myserver --ttl 7d             # extend myserver's TTL by 7 days
./expire.py --renew myserver --ttl 7d --kind lxc  # disambiguate when lxc/ and vms/ match
```

### --check output

```
┏━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Hostname    ┃ Type ┃ VMID    ┃ Node       ┃ TTL   ┃ Expires / Expired                    ┃ Status        ┃
┡━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ old-test    │ LXC  │ 111     │ proxmox01  │ 2d    │ 2024-01-01 00:00 UTC  (806d ago)     │ EXPIRED       │
│ staging-vm  │ VM   │ 113     │ proxmox01  │ 1d    │ 2026-03-17 02:40 UTC  (24h left)     │ expiring soon │
└─────────────┴──────┴─────────┴────────────┴───────┴──────────────────────────────────────┴───────────────┘
```

Expired entries are shown in red. Expiring-soon entries are shown in yellow. The warning window defaults to `48h` and is configurable with `--warning`.

### --reap behavior

`--reap` runs the same full decommission pipeline as the decomm scripts:

1. Stop and destroy the Proxmox resource
2. Remove DNS A + PTR records from BIND
3. Remove from Ansible inventory

If the Proxmox resource is already gone (stale JSON file), it proceeds with DNS and inventory cleanup and reports the host as **Already gone** rather than **Decommissioned** — so the true state is always clear.

`--purge` (used with `--reap`) deletes the deployment JSON file after each successful decommission or "already gone" outcome. Hosts that abort keep their JSON files.

### --renew examples

```bash
# Extend by 7 days (searches lxc/ then vms/ automatically)
./expire.py --renew myserver --ttl 7d

# Extend specifically the LXC version when both lxc/ and vms/ have myserver.json
./expire.py --renew myserver --ttl 7d --kind lxc

# Extend specifically the VM version
./expire.py --renew myserver --ttl 7d --kind vm
```

`--renew` only updates the JSON file — no Proxmox connection needed.

---

## Usage — cleanup_tagged.py

Scans **every node in the cluster** for VMs and LXC containers carrying a given Proxmox tag (default: `auto-deploy`) and lets you decide what to do with each one — interactively, or via a pre-built action list file.

```bash
python3 cleanup_tagged.py                                          # interactive scan
python3 cleanup_tagged.py --dry-run                                # list resources and exit
python3 cleanup_tagged.py --tag my-custom-tag                      # scan for a different tag
python3 cleanup_tagged.py --list-file cleanup-plan.json            # load actions from file
python3 cleanup_tagged.py --list-file cleanup-plan.json --silent   # run unattended
```

### IP resolution order

For each tagged resource, the script resolves the IP using these sources in order, stopping at the first hit:

1. **Proxmox config** — static IP from the resource's `ipconfig0` / `net0` config key
2. **Deployment JSON** — `assigned_ip` or `ip_address` from the local `deployments/lxc/` or `deployments/vms/` file
3. **Proxmox live interfaces API** — queries the running guest directly (requires qemu-guest-agent for VMs)
4. **DNS lookup** — tries the configured DNS server first (`dns.server`), then falls back to the system resolver

If none of these resolve, IP is shown as `unknown/DHCP` in the table.

### Per-resource actions

After displaying the resource table, the script prompts for each resource individually:

| Action | Effect |
|---|---|
| **Keep** | Leave it alone. No changes. Appears in the summary as Kept. |
| **Promote** | Removes the matched tag from the resource in Proxmox. The resource stays running and is no longer flagged as temporary. |
| **Decomm** | Permanently destroys the resource. Runs the same full destruction sequence as `decomm_lxc.py` / `decomm_vm.py`: stops and destroys in Proxmox (purging all disks), removes DNS A + PTR records, removes from Ansible inventory. |

Decomm operations are queued and confirmed one at a time after the action selection pass completes.

### Action List File

The `--list-file` flag accepts a JSON file that pre-specifies an action for each resource. This separates the decision of what to do from the execution — review and approve the plan file, then hand it to `--silent` to run unattended.

```json
[
  {"hostname": "test-lxc",    "action": "keep"},
  {"hostname": "staging-web", "action": "promote"},
  {"hostname": "old-db",      "vmid": "123", "action": "decomm"}
]
```

**Fields:**

| Field | Required | Description |
|---|---|---|
| `hostname` | Yes | Must match the resource name as reported by Proxmox |
| `action` | Yes | One of `keep`, `promote`, or `decomm` |
| `vmid` | No | Used to disambiguate when two resources share the same hostname |

**Matching:** Resources are matched by `hostname:vmid` first, then `hostname` alone. Resources in the cluster that have no matching entry in the file default to `keep`. List file entries that don't match any cluster resource produce a warning and are skipped — they do not cause an error.

**Typical workflow:**
```bash
# 1. See what's tagged
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

### Summary panel

After all actions complete, a summary panel is printed with five buckets:

- **Decommissioned** — fully destroyed (stopped, disks purged, DNS removed, inventory cleaned)
- **Already gone** — Proxmox resource not found; DNS and inventory were still cleaned up
- **Promoted to production** — tag removed, resource kept running
- **Kept (no changes)** — skipped
- **Aborted** — confirmation failed or an error occurred during destruction

The **Already gone** bucket handles stale JSON files gracefully — DNS and inventory are still cleaned up, and it's reported separately so it doesn't look like a successful decommission.

---

## examples/ Directory

The `examples/` directory contains ready-to-use list files for `cleanup_tagged.py --list-file`. They are committed and serve as templates and documentation for the list-file format.

| File | Description |
|---|---|
| `list-file_keep-all.json` | Assign `keep` to every listed resource. Use when you want to scan but not touch anything. |
| `list-file_decomm-all.json` | Assign `decomm` to every listed resource. Appropriate for end-of-sprint teardown when everything is temporary. |
| `list-file_promote-all.json` | Assign `promote` to every listed resource — removes the `auto-deploy` tag from all, flagging them as permanent. |
| `list-file_mixed.json` | Mix of `decomm`, `promote`, and `keep`. The typical real-world case where some hosts graduate to production and others are torn down. |
| `list-file_duplicate-hostname.json` | Demonstrates disambiguating two resources that share the same hostname by including `vmid` in the entry. |
| `list-file_ghost-host.json` | Shows graceful handling of a hostname that appears in the file but no longer exists in the cluster. The entry produces a warning and is skipped without error. |
| `list-file_invalid-action.json` | Demonstrates the validation error produced when an unsupported action string (e.g. `"nuke"`) is used. The script exits with an error before touching anything. |

All example files reference placeholder hostnames (`test-lxc-01`, `test-vm-01`, `staging-web`, etc.). Copy and edit them for your own use.

---

## Providers

labinator uses a provider model for external integrations. Providers abstract the specific backend (e.g. which DNS server product or which inventory system), making it possible to swap them in the future without changing the core scripts.

### DNS provider (BIND)

The `dns.provider: bind` provider manages A and PTR records on a BIND DNS server via SSH + Ansible. The Ansible playbook edits the zone file directly and calls `rndc reload` to apply changes.

**Configuration:**
```yaml
dns:
  enabled: true
  provider: bind
  server: 10.0.0.10
  ssh_user: root
  forward_zone_file: /var/lib/bind/example.com.hosts
```

The reverse zone file is derived automatically from the deployed IP (e.g. IP `10.20.20.140` → `/var/lib/bind/20.20.10.in-addr.arpa.hosts`). If the reverse zone file does not exist on the DNS server, the PTR record is skipped gracefully with a warning — the A record is still registered.

Future providers planned: PowerDNS, Technitium.

### Ansible Inventory provider (flat_file)

The `ansible_inventory.provider: flat_file` provider manages a plain-text Ansible inventory file on a remote server. The Ansible playbook appends or removes a host entry under the specified group header.

**Configuration:**
```yaml
ansible_inventory:
  enabled: true
  provider: flat_file
  server: dev.example.com
  user: root
  file: /root/ansible/inventory/hosts
  group: Linux
```

The group header (`[Linux]` in this example) must already exist in the inventory file. The group name is case-sensitive. After adding a host, `ssh-keyscan` and `ssh-copy-id` are run from the inventory server to the new host so Ansible can connect with key-based auth immediately.

Future providers planned: AWX, Semaphore.

### Disabling a provider

To skip a provider entirely, set `enabled: false`:

```yaml
dns:
  enabled: false        # Skip all DNS registration and removal

ansible_inventory:
  enabled: false        # Skip all inventory registration and removal

ansible:
  enabled: false        # Skip ALL Ansible steps (post-deploy configuration,
                        # DNS, and inventory are all skipped)
```

When `ansible.enabled: false`, steps 5–7 of the deploy pipeline are all skipped and the host must be configured manually. When `dns.enabled` or `ansible_inventory.enabled` is false, only that specific step is skipped. All flags default to `true` if absent.

---

## Multi-Node / Failover

labinator supports automatic failover across Proxmox cluster nodes. Configure a list of hosts:

```yaml
proxmox:
  hosts:
    - proxmox01.example.com
    - proxmox02.example.com
    - proxmox03.example.com
```

The `connect_proxmox()` function tries each host in order until one accepts the API token. The first successful connection is used for the entire session. If all hosts fail, the last error is raised.

This is backwards-compatible with the single `host:` key from older configurations:

```yaml
proxmox:
  host: proxmox01.example.com   # Single host — still supported
```

The preflight check reports `X/Y host(s)` — for example `2/3 host(s) on :8006` with the unreachable host named — so you know if a node is down before deploying.

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
ansible-playbook -i <dns.server>, add-dns.yml \
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
ansible-playbook -i <dns.server>, remove-dns.yml \
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

## porter Integration

[porter](https://github.com/Jerry-Lees/HomeLab) is a dual-pane terminal file manager built for homelabs and sysadmin work. It integrates with labinator for snapshot-based deployments.

### What porter does

porter lets you connect to a running reference server (via SSH/SFTP), browse its filesystem, and cherry-pick configuration files into an archive. The archive contains files with full permissions and ownership preserved, plus a `manifest.yaml` that documents local users, active systemd services, and installed packages.

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

### Current status

porter is functional. Snapshots can be taken from reference servers and the archives are structured correctly for labinator ingestion. The labinator ingestion step (reading a porter archive and applying it during deployment) is planned as a future feature.

> The manifest schema is documented in `snapshot-manifest-specs.md` in the project root.

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

### "Storage X only has 0.0 GB free" (lvmthin false positive)

Earlier versions of the storage space check reported `0.0 GB` for LVM-thin pools because the raw API bytes value was misread. This has been fixed. If you see this on a current version, file an issue with your Proxmox version and storage type.

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
- For DHCP: confirm `qemu-guest-agent` is installed and running. Ubuntu 24.04 cloud images do not ship it by default — labinator installs it via a cloud-init vendor-data snippet at deploy time. Rocky Linux 8 and openSUSE Leap 15.6 also require this snippet. If you're using a custom image that does not support vendor-data, you may need to install qemu-guest-agent manually or use a static IP instead.
- For static: confirm the IP and gateway are reachable on the VLAN

---

### cloud-init status --wait crashes on Rocky Linux 8  *(VM only)*

**Symptom:** The wait-for-cloud-init step hangs or crashes on Rocky Linux 8.

**Cause:** A bug in cloud-init 23.4 causes `cloud-init status --wait` to call `systemctl show-environment`, which fails over SSH.

**Status: Fixed.** labinator does not use `cloud-init status --wait`. Instead, it waits for `/run/cloud-init/result.json` to appear, which is written by cloud-init when all first-boot stages complete and works reliably across all supported OS families.

---

### Rocky Linux 8 first boot takes up to 15 minutes  *(VM only)*

**Symptom:** After starting a Rocky Linux 8 VM, the Ansible step waits a very long time before the host becomes reachable.

**Cause:** The Rocky Linux 8 cloud image runs a full `dnf upgrade` on first boot. This is baked into the image and cannot be suppressed with `package_upgrade: false` in cloud-init vendor-data. The upgrade can take 10–15 minutes depending on network speed and server load.

**Workaround:** The `wait_for_connection` timeout in `post-deploy-vm.yml` is set to 1800 seconds (30 minutes). No action needed — just wait.

---

### qemu-guest-agent not in Ubuntu 24.04 / Rocky Linux 8 / openSUSE cloud images  *(VM only)*

**Symptom:** After DHCP deploy, the guest agent is not running and the IP cannot be polled.

**Status: Handled automatically.** `deploy_vm.py` writes a cloud-init vendor-data snippet to `/var/lib/vz/snippets/vm-{vmid}-userdata.yaml` on the Proxmox node and passes it via `cicustom=vendor=local:snippets/...`. This snippet installs and enables `qemu-guest-agent` on first boot for all three OS families. The `vendor=` key is used rather than `user=` — `user=` would override Proxmox's generated user-data and break password/SSH key injection.

---

### No reverse DNS zones (PTR records always skipped)

**Symptom:** Every deploy prints "reverse zone file not found" and skips the PTR record.

**Cause:** The PTR step is skipped when the reverse zone file does not exist on the BIND server. This is graceful expected behavior — the A record is still registered.

**Fix:** Create the reverse zone file(s) on your BIND server for each subnet in use (e.g. `/var/lib/bind/20.20.10.in-addr.arpa.hosts` for the `10.20.20.x` subnet). This is a DNS infrastructure gap, not a labinator bug.

---

### VLAN check always passes for VLAN-aware bridges

**Symptom:** The VLAN validation check passes even when the specified VLAN ID may not be configured.

**Cause:** Proxmox VLAN-aware bridges accept any VLAN tag at the API level — they rely on upstream switch configuration to enforce VLAN membership. The check confirms the bridge exists, not that the VLAN is trunked on the upstream port.

**Expected behavior.** If a container/VM gets an IP but not the right one, verify the VLAN is trunked on the physical port connected to the Proxmox node.

---

### Ansible Python 3.6 warning on openSUSE  *(VM only)*

**Symptom:** Ansible prints a deprecation warning about Python 3.6 when configuring an openSUSE Leap 15.6 guest.

**Cause:** openSUSE Leap 15.6 ships Python 3.6 as the platform Python. Ansible warns but works correctly.

**Status: Harmless.** No action needed. The `ansible_python_interpreter=auto` setting in the generated inventory causes Ansible to find the best available Python on the guest, but the warning from the older version is expected.

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

When `ansible.enabled` is false, Steps 5–7 are all skipped and the host must be configured manually. When `dns.enabled` or `ansible_inventory.enabled` is false, only those specific steps are skipped.

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

The hostname already resolves in DNS — an existing host is registered with that name. The existing host will be orphaned after the new deployment registers its own record. Decommission the old host first with `decomm_lxc.py` or `decomm_vm.py` before redeploying.

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

## Known OS Support

| OS | Status | Notes |
|---|---|---|
| Ubuntu 24.04 LTS | Tested and verified | Fully supported |
| Rocky Linux 8 | Tested and verified | First boot takes up to 15 min due to cloud image running full `dnf upgrade`; cloud-init wait uses `/run/cloud-init/result.json` (not `cloud-init status --wait`); Python interpreter resolved via `auto`; all issues resolved |
| openSUSE Leap 15.6 | Tested and verified | Works; low priority — not actively maintained until a GitHub issue is filed; harmless Python 3.6 Ansible warning expected |
| Debian 12 / Ubuntu 22.04 | Should work | Same OS family as Ubuntu 24.04; untested |
| AlmaLinux / CentOS Stream / Fedora | Should work | Same OS family as Rocky Linux 8; untested |
| openSUSE Tumbleweed / SLES | Should work | Same OS family as Leap; untested |

---

## Submitting an Issue

> This tool is provided **as-is** without warranty or active support. Issue submissions are welcomed on a best-effort basis.

**Before opening an issue:**
- Read the [Troubleshooting](#troubleshooting) section
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
