# config.yaml — Schema Reference

`config.yaml` is the primary configuration file for labinator. It controls how labinator
connects to your Proxmox cluster, what defaults are used during deployment, and how
external integrations (DNS, Ansible inventory, SNMP, NTP) behave.

**This file is never committed to git.** Copy `config.yaml.example` to `config.yaml` and
fill in your values. The `.gitignore` explicitly excludes `config.yaml` to prevent
accidental credential exposure.

---

## Top-Level Sections

| Section | Required | Purpose |
|---|---|---|
| `proxmox` | ✓ | Cluster connection, API credentials, SSH key |
| `nodes` | optional | Known node names for display (live list comes from API) |
| `defaults` | optional | Default values pre-filled in interactive prompts |
| `package_profiles` | optional | Named package sets for server roles |
| `dns` | optional | DNS registration integration |
| `ansible` | optional | Ansible post-deploy toggle |
| `ansible_inventory` | optional | Ansible inventory registration integration |
| `snmp` | optional | SNMP configuration applied to all deployed hosts |
| `ntp` | optional | NTP servers configured on all deployed hosts |
| `health_check` | optional | Post-deploy SSH health check settings |
| `timezone` | optional | Timezone set on all deployed hosts |
| `vm` | optional | VM-specific hardware defaults |

---

## `proxmox` — Cluster Connection

**Required.** All deploy, decomm, cleanup, and expiry scripts use this section to connect
to the Proxmox API and SSH to nodes.

```yaml
proxmox:
  hosts:
    - proxmox01.example.com
    - proxmox02.example.com
  user: root@pam
  token_name: vm-deploy
  token_secret: CHANGEME
  ssh_key: ~/.ssh/id_rsa
  node_domain: example.com
  verify_ssl: false
```

| Field | Required | Type | Description |
|---|---|---|---|
| `hosts` | ✓ (or `host`) | list | Ordered list of Proxmox API hostnames. Tried in order until one connects. Provides automatic failover. |
| `host` | ✓ (or `hosts`) | string | Single Proxmox API hostname. Use `hosts` instead if you have multiple nodes. |
| `user` | ✓ | string | Proxmox API user in `user@realm` format. e.g. `root@pam`. |
| `token_name` | ✓ | string | API token ID only — **not** the full `user!tokenid` string. Created in Proxmox: Datacenter → Permissions → API Tokens. |
| `token_secret` | ✓ | string | API token secret UUID. Shown only once at creation time. |
| `ssh_key` | ✓ | path | Path to SSH private key. Must be authorized on all Proxmox nodes as root. Used for node SSH access, cloud image operations, and the post-deploy health check. |
| `node_domain` | ✓ | string | Domain suffix appended to node names for SSH. e.g. `example.com` → SSH connects to `proxmox01.example.com`. |
| `verify_ssl` | optional | bool | Verify Proxmox API SSL certificate. Default: `false`. Set `true` if using valid certs. |

---

## `nodes` — Known Node List

**Optional.** A static list of node names used for display purposes. The scripts always
query the live node list from the Proxmox API regardless of this setting.

```yaml
nodes:
  - proxmox01
  - proxmox02
  - proxmox03
```

---

## `defaults` — Prompt Defaults

**Optional.** Values pre-filled in interactive prompts. The user can override any of these
at deploy time. If omitted, built-in fallback defaults apply.

```yaml
defaults:
  cpus: 2
  memory_gb: 4
  disk_gb: 100
  vlan: 220
  bridge: vmbr0
  root_password: changeme
  addusername: admin
  swap_mb: 512
  onboot: true
  unprivileged: true
  firewall_enabled: false
  cpu_threshold: 0.85
  ram_threshold: 0.95
  searchdomain: example.com
  nameserver: "10.0.0.1 10.0.0.2"
  template: ubuntu-24.04-standard_24.04-2_amd64.tar.zst
```

| Field | Required | Type | Description |
|---|---|---|---|
| `cpus` | optional | int | Default vCPU count. |
| `memory_gb` | optional | float | Default RAM in GB. |
| `disk_gb` | optional | int | Default root disk size in GB. |
| `vlan` | optional | int | Default VLAN tag applied to the network interface. |
| `bridge` | optional | string | Default Proxmox bridge. Combined with vlan as `vmbr0.220`. |
| `root_password` | optional | string | Default password for root and the secondary user. Always prompted — this is just the pre-fill. |
| `addusername` | optional | string | Name of the secondary user created on every deployed host. Shown in prompts (e.g. `Root / dad user password`). Default: `admin`. |
| `swap_mb` | optional | int | LXC only. Swap size in MB. |
| `onboot` | optional | bool | Whether the container/VM starts automatically when the Proxmox node boots. |
| `unprivileged` | optional | bool | LXC only. Run as unprivileged container. Recommended `true` for security. |
| `firewall_enabled` | optional | bool | Enable Proxmox firewall on the container/VM network interface. |
| `cpu_threshold` | optional | float | Node filter: skip nodes at or above this CPU load (0.0–1.0). Default: `0.85`. |
| `ram_threshold` | optional | float | Node filter: skip nodes at or above this RAM usage after allocation (0.0–1.0). Default: `0.95`. |
| `searchdomain` | optional | string | DNS search domain set in the container/VM. |
| `nameserver` | optional | string | Space-separated DNS resolver IPs set in the container/VM. |
| `template` | optional | string | LXC only. Default OS template filename (matched against available templates). |

---

## `package_profiles` — Server Role Profiles

**Optional.** Named sets of packages representing a server role. Selected at deploy time
to install a consistent toolset. Profiles also apply Proxmox tags to the resource for
visibility and cleanup targeting.

**Why it matters:** Without profiles, every deployment requires manually typing package
names. Profiles ensure every web server, database, or monitoring node gets the same
baseline without human error.

```yaml
package_profiles:
  web-server:
    packages:
      - nginx
      - certbot
      - python3-certbot-nginx
      - ufw
    tags:
      - WWW
```

| Field | Required | Type | Description |
|---|---|---|---|
| `packages` | ✓ | list | Package names to install after the standard baseline. Names are OS-specific — these target Debian/Ubuntu. Adjust for Rocky/openSUSE. |
| `tags` | optional | list | Proxmox tags applied to the resource. Alphanumeric, hyphens, underscores only — no spaces. Combined with `auto-deploy` tag that is always applied. |

**Note:** `auto-deploy` is always added as a tag regardless of profile. This is what
`cleanup_tagged.py` uses to find managed resources.

---

## `dns` — DNS Registration

**Optional.** Controls automatic DNS A-record registration when a container or VM is deployed.
Decommission scripts use the same settings for record removal.

```yaml
dns:
  enabled: true
  provider: bind
  server: 10.0.0.10
  ssh_user: root
  forward_zone_file: /var/lib/bind/example.com.hosts
```

| Field | Required | Type | Description |
|---|---|---|---|
| `enabled` | optional | bool | Enable DNS registration. Default: `true`. Set `false` to manage DNS manually. |
| `provider` | optional | string | DNS backend. Currently only `bind` is implemented. Future: `powerdns`, `technitium`. |
| `server` | ✓ if enabled | string | IP address of the DNS server. Labinator SSHes to this host to add/remove records. |
| `ssh_user` | optional | string | SSH user for DNS server access. Default: `root`. |
| `forward_zone_file` | ✓ if enabled | path | Full path to the BIND forward zone file on the DNS server. |

**Note:** Reverse (PTR) zone files are derived automatically from the deployed IP address.
If the reverse zone file does not exist on the BIND server, PTR registration is skipped
with a warning — this is expected if reverse zones haven't been configured. It is not a
labinator error.

---

## `ansible` — Post-Deploy Configuration Toggle

**Optional.** Controls whether Ansible runs after deployment. If disabled, the host is
created and started but not configured — no users, no packages, no NTP, no SNMP.

```yaml
ansible:
  enabled: true
```

| Field | Required | Type | Description |
|---|---|---|---|
| `enabled` | optional | bool | Run Ansible post-deploy playbook. Default: `true`. |

---

## `ansible_inventory` — Inventory Registration

**Optional.** Controls automatic registration of newly deployed hosts into the Ansible
inventory on a remote server. Decommission scripts remove entries using the same settings.

```yaml
ansible_inventory:
  enabled: true
  provider: flat_file
  server: dev.example.com
  user: root
  file: /root/ansible/inventory/hosts
  group: Linux
```

| Field | Required | Type | Description |
|---|---|---|---|
| `enabled` | optional | bool | Register hosts in Ansible inventory. Default: `true`. |
| `provider` | optional | string | Inventory backend. Currently only `flat_file` is implemented. Future: `awx`, `semaphore`. |
| `server` | ✓ if enabled | string | Hostname or IP of the server holding the inventory file. |
| `user` | optional | string | SSH user for inventory server. Default: `root`. |
| `file` | ✓ if enabled | path | Full path to the inventory file on the remote server. |
| `group` | ✓ if enabled | string | Ansible group to add new hosts into. **Case-sensitive.** Must match an existing group in the inventory file. |

---

## `snmp` — SNMP Configuration

**Optional.** SNMP settings applied to every deployed host via the Ansible playbook.
Configures `snmpd` with the specified community string and metadata.

```yaml
snmp:
  community: your-snmp-community
  source: default
  location: Homelab
  contact: admin@example.com
```

| Field | Required | Type | Description |
|---|---|---|---|
| `community` | ✓ if snmp used | string | SNMP community string (read-write). |
| `source` | optional | string | Restrict SNMP access to a source network. `default` = any source. |
| `location` | optional | string | SNMP sysLocation value. |
| `contact` | optional | string | SNMP sysContact value. |

---

## `ntp` — NTP Servers

**Optional.** NTP servers written to `chrony.conf` on every deployed host.

```yaml
ntp:
  servers:
    - pool.ntp.org
    - time.nist.gov
```

| Field | Required | Type | Description |
|---|---|---|---|
| `servers` | optional | list | List of NTP server hostnames or IPs. Minimum one recommended. |

---

## `health_check` — Post-Deploy SSH Verification

**Optional.** After Ansible completes, optionally verify the host is reachable via SSH.
Connects as `root` using the SSH agent. Failure prints a warning but does NOT roll back
the deployment.

```yaml
health_check:
  enabled: false
  timeout_seconds: 30
  retries: 5
```

| Field | Required | Type | Description |
|---|---|---|---|
| `enabled` | optional | bool | Run health check after deployment. Default: `false`. |
| `timeout_seconds` | optional | int | Per-attempt TCP and SSH connection timeout. Default: `30`. |
| `retries` | optional | int | Number of TCP port-22 attempts before declaring the host unreachable. Default: `5`. |

---

## `timezone` — System Timezone

**Optional.** Timezone set on every deployed host via the Ansible playbook.

```yaml
timezone: America/Chicago
```

Uses standard tz database names (e.g. `America/New_York`, `Europe/London`, `UTC`).

---

## `vm` — VM Hardware Defaults

**Optional.** VM-specific hardware settings. Only used by `deploy_vm.py`.

```yaml
vm:
  default_cloud_image_storage: local
  cpu_type: x86-64-v2-AES
  machine: q35
  bios: seabios
  storage_controller: virtio-scsi-pci
  nic_driver: virtio
```

| Field | Required | Type | Description |
|---|---|---|---|
| `default_cloud_image_storage` | optional | string | Default storage pre-selected for cloud image downloads. Must support `iso` content type in Proxmox. Leave blank to always prompt. |
| `cpu_type` | optional | string | VM CPU type. `x86-64-v2-AES` requires host CPU support. Use `kvm64` for maximum compatibility across mixed hardware. |
| `machine` | optional | string | VM machine type. `q35` (modern, recommended) or `i440fx` (legacy). |
| `bios` | optional | string | VM BIOS type. `seabios` (standard) or `ovmf` (UEFI). |
| `storage_controller` | optional | string | VM disk controller. `virtio-scsi-pci` recommended for performance. |
| `nic_driver` | optional | string | VM NIC driver. `virtio` recommended for performance. |
