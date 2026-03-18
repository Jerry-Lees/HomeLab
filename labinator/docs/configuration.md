[← Back to README](../README.md)

# Initial Configuration Guide

### About

This guide covers the one-time setup required before any labinator script will run: creating a Proxmox API token, authorizing your SSH key on the cluster nodes, and filling in `config.yaml`. Once configured, the scripts handle everything else automatically.

This will be the first and, hopefully, only challenge in getting running.

## Table of Contents

- [Config Wizard — The Easy Way](#config-wizard--the-easy-way)
- [Creating a Proxmox API Token](#creating-a-proxmox-api-token)
- [Authorizing SSH Key on Proxmox Nodes](#authorizing-ssh-key-on-proxmox-nodes)
- [config.yaml Reference](#configyaml-reference)
- [cloud-images.yaml](#cloud-imagesyaml)
- [--config FILE Flag](#--config-file-flag)
- [Multi-Node / Failover](#multi-node--failover)

---

## Config Wizard — The Easy Way

Not a fan of editing YAML by hand? `configure.py` walks you through every field interactively, with a one-sentence hint at each prompt. It writes a fully-commented `config.yaml` and validates it immediately when done.

```bash
python3 configure.py          # build config.yaml from scratch
python3 configure.py --edit   # edit an existing config.yaml
python3 configure.py --validate  # check config.yaml without changing anything
```

See the full wizard docs: **[Config File Wizard](configure.md)**

The rest of this page is the manual field reference — useful for understanding what each setting does, or for making targeted edits to an existing config without running the wizard again. Or if you are a super human YAML editing machine.

---

## Creating a Proxmox API Token
A Proxmox API token is required to be configured in the config.yaml file for the script to operate. Without it, there's no point. The steps to create one are listed below. These steps may differ in the future as new versions of Proxmox come out, if so, refer to the Proxmox Official documentation.

1. In the Proxmox web UI, go to **Datacenter → Permissions → API Tokens**
2. Click **Add**
3. Fill in:
   - **User:** `root@pam` (or a dedicated user)
   - **Token ID:** `vm-deploy` (or any name you like)
   - **Privilege Separation:** **unchecked** (token must inherit full user permissions)
4. Click **Add** — copy the **Secret** immediately, it is only shown once
5. Edit `config.yaml` and paste the secret into `token_secret` (under the `proxmox:` block)
6. Set `token_name` (under the `proxmox:` block) to just the token ID (e.g. `vm-deploy`) — **not** the full `user!tokenid` string 

> **Note:** `token_name` is the short name you gave the token when you created it (e.g. `vm-deploy`) — the token ID. `token_secret` is the UUID Proxmox shows you once at creation time.
>
> ```yaml
> # API Token name (created in Proxmox: Datacenter > Permissions > API Tokens)
> token_name: vm-deploy
>
> # API Token secret (UUID shown only once at creation time)
> token_secret: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
> ```


> **Permissions required:** The token/user needs `Administrator` role on `/`, or at minimum: `VM.Allocate`, `VM.Config.*`, `VM.PowerMgmt`, `Datastore.AllocateSpace`, `Datastore.Audit`, `SDN.Use`, `Sys.Audit`.

---

## Authorizing SSH Key on Proxmox Nodes

The controller machine's SSH key must be in `authorized_keys` on every Proxmox node. This can be accomplished with the commands below for each host:

```bash
ssh-copy-id root@proxmox01.example.com
ssh-copy-id root@proxmox02.example.com
# repeat for each node...
```

Verify it works without a password:
```bash
ssh root@proxmox01.example.com 'echo OK'
```

If you use a non-default key, set `ssh_key` (under the `proxmox:` block) in `config.yaml`.

---

## config.yaml Reference

The default configuration file, `config.yaml`, is **excluded from git** — it contains credentials and is never committed. Copy the included example to get started:

```bash
cp config.yaml.example config.yaml
```

All scripts support `--config FILE` to point at an alternate config file. Optionally, you can provide a full path and file name instead of just assuming it is in the current directory or project root. The default is `config.yaml` in the project root.

The reference below attempts to document every key, however, the config.yaml.example will always be up to date and have comments to guide you:

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
  vlan: 220                       # VLAN tag (creates the device on VLAN 220, either with a VLAN Aware bridge or vmbr0.220)
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

## cloud-images.yaml

The VM deployment wizard reads its list of downloadable OS images from `cloud-images.yaml` in the project root. This file can be edited carefully without touching any Python scripts.

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

**Fedora note:** Fedora cloud image URLs are version-specific (no stable `current/` redirect). You **MUST** update the URL in `cloud-images.yaml` when upgrading to a new Fedora release. Thank Red Hat for that madness, unfortunately, it's not mine to fix.

---

## --config FILE Flag

All scripts support the `--config FILE` option to point at an alternate config file:

```bash
python3 deploy_lxc.py --config /path/to/my-config.yaml
python3 deploy_vm.py --config /path/to/my-config.yaml
python3 decomm_lxc.py --config /path/to/my-config.yaml
python3 expire.py --config /path/to/my-config.yaml
python3 cleanup_tagged.py --config /path/to/my-config.yaml
```

The default is `config.yaml` in the project root. This flag is useful for running multiple labinator environments or for CI/CD pipelines with environment-specific configs or to manage multiple environments, like; Prod, Dev, QA, UAT, etc.

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

> **Under The Hood**
> The `connect_proxmox()` function tries each host in order until one accepts the API token. The first successful connection is used for the entire session. If all hosts fail, the last error is raised.

The scripts are backwards-compatible with the single `host:` key from older configurations, but that option is deprecated and may disappear in future versions. The deprecated configuration options look like the below:

```yaml
proxmox:
  host: proxmox01.example.com   # Single host — still supported
```

The preflight check reports `X/Y host(s)` — for example `2/3 host(s) on :8006` with the unreachable host name referenced — so you know if a node is down before deploying.

---

[← Back to README](../README.md)
