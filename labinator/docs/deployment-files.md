[← Back to README](../README.md)

# Deployment Files Reference

### About

Every successful deploy writes a JSON file recording the exact configuration used. These files serve as the input to `--deploy-file` (interactive re-run with pre-filled defaults), `--silent` (fully automated re-run), and the decommission scripts. Because the decommission scripts only operate on resources that have a deployment file, the file is also what ties the full lifecycle together — from creation through DNS cleanup and inventory removal.

**Missing fields and partial deploy files:**

- **Interactive mode (`--deploy-file` without `--silent`):** Fields present in the file are pre-filled as prompt defaults — you can accept or change them. Fields missing from the file behave as if no deploy file was given for that prompt: the `config.yaml` default is shown and the user is prompted normally. A partial deploy file is perfectly valid in interactive mode.

- **Silent mode (`--deploy-file --silent`):** Every field that the wizard would normally prompt for must be present in the deploy file. If a field is missing, the script falls through and attempts to prompt interactively — which will hang in a non-interactive pipeline. Always use a complete deploy file with `--silent`, especially in your CI/CD pipelines.

## Table of Contents

- [Where Files Live](#where-files-live)
- [LXC Deployment File](#lxc-deployment-file)
- [VM Deployment File](#vm-deployment-file)
- [LXC vs VM Differences](#lxc-vs-vm-differences)
- [.gitignore Behavior](#gitignore-behavior)
- [Deployment History Log](#deployment-history-log)
- [Providers](#providers)
- [OS Support Table](#os-support-table)

---

## Where Files Live

Deployment files are organized by resource type under the `deployments/` directory in the project root:

| Type | Directory |
|---|---|
| LXC containers | `deployments/lxc/<hostname>.json` |
| VMs | `deployments/vms/<hostname>.json` |
| History log | `deployments/history.log` |

---

## LXC Deployment File

Saved to `deployments/lxc/<hostname>.json` at the end of a successful deployment:

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

**Field reference:**

| Field | Type | Description |
|---|---|---|
| `hostname` | string | Short hostname (no domain) |
| `fqdn` | string | Fully qualified domain name |
| `node` | string | Proxmox node the container is deployed on |
| `vmid` | integer | Proxmox VMID |
| `template_volid` | string | Full volume ID of the LXC template used |
| `template_name` | string | Filename of the LXC template |
| `cpus` | integer | Number of vCPUs |
| `memory_gb` | float | RAM in GB |
| `disk_gb` | integer | Root disk size in GB |
| `storage` | string | Proxmox storage pool for the container rootfs |
| `vlan` | integer | VLAN tag |
| `bridge` | string | Proxmox bridge interface |
| `password` | string | Root and admin user password |
| `ip_address` | string | Configured IP (static or DHCP-discovered then locked as static) |
| `assigned_ip` | string | Actual IP assigned to the container at runtime |
| `prefix_len` | string | Network prefix length (e.g. `"24"`) |
| `deployed_at` | string | ISO-style timestamp of deployment |
| `ttl` | string | TTL string (e.g. `"7d"`) — present only if `--ttl` was used |
| `expires_at` | string | ISO 8601 UTC expiry timestamp — present only if `--ttl` was used |
| `preflight` | boolean | Controls whether preflight checks run before this deployment |

**Key notes:**
- `ip_address` — the configured IP. Used for DNS registration and static IP preflight check.
- `assigned_ip` — the actual IP assigned to the container. Same as `ip_address` for static assignments; the DHCP-assigned address for DHCP deployments. Used by the decommission pipeline for DNS record removal.
- `ttl` / `expires_at` — present only if `--ttl` was used at deploy time. `expire.py` reads `expires_at` to determine expiry status.
- `preflight` — controls whether preflight checks run before this deployment. Defaults to `true`. Set to `false` to skip all preflight for this specific host.
- `template_volid` — if this template is no longer on the node, the script falls back to the first available template and prints a warning.

---

## VM Deployment File

Saved to `deployments/vms/<hostname>.json` at the end of a successful deployment:

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

**Field reference (VM-specific fields):**

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"vm"` — distinguishes VM files from LXC files |
| `cloud_image_storage` | string | Proxmox storage where the cloud image lives |
| `cloud_image_filename` | string | Filename of the image within the `cloud-images/` directory |
| `cloud_image_url` | string | Download URL stored as a fallback for auto-recovery if the image is missing from storage |
| `image_refresh` | boolean | If `true`, the image is always re-downloaded before import |
| `ip_address` | string | `"dhcp"` if DHCP was used, otherwise the configured static IP |
| `assigned_ip` | string | Present for DHCP deployments; records the IP assigned at boot time. Also set for static deployments (same as `ip_address`). |
| `gateway` | string | Gateway for static IP configurations; absent for DHCP deployments |
| `extra_packages` | array | Optional list of one-off packages installed on top of the baseline and profile. Added by the wizard if packages were entered at the extra packages prompt. |

All other fields (`hostname`, `fqdn`, `node`, `vmid`, `cpus`, `memory_gb`, `disk_gb`, `storage`, `vlan`, `bridge`, `password`, `prefix_len`, `deployed_at`, `ttl`, `expires_at`, `preflight`) have the same meaning as in LXC files.

**Key notes:**
- `image_refresh` — set automatically based on whether you selected an existing image (`false`) or a "Download:" catalog entry (`true`).
- `cloud_image_url` — stored as a fallback for auto-recovery. If the image is missing from storage when redeploying, the script looks up the URL here (and in `cloud-images.yaml`) before failing.

---

## LXC vs VM Differences

The two file types share most fields but differ in how the OS source is specified and a few VM-only networking fields:

| Field | LXC | VM |
|---|---|---|
| `type` | absent | `"vm"` |
| `template_volid` | present | absent |
| `template_name` | present | absent |
| `cloud_image_storage` | absent | present |
| `cloud_image_filename` | absent | present |
| `cloud_image_url` | absent | present |
| `image_refresh` | absent | present |
| `gateway` | absent | present (static only) |
| `extra_packages` | present (if set) | present (if set) |
| Storage location | `deployments/lxc/` | `deployments/vms/` |

---

## .gitignore Behavior

The `.gitignore` excludes all deployment files matching `deployments/lxc/*.json` and `deployments/vms/*.json` **except** files beginning with `example-`. This means:

- Real deployment files (which contain passwords and IP addresses) are never committed
- Example files (`example-lxc.json`, `example-vm.json`) are tracked and serve as templates

---

## Deployment History Log

Every successful deploy and decommission appends a single JSON line to `deployments/history.log`. The log is created automatically on first use and is append-only — nothing is ever deleted from it.

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

The log is gitignored via the `**/*.log` rule in `.gitignore` and is never committed. It can be queried with standard tools:
```bash
# Show all deploys in the last 7 days
grep '"action": "deploy"' deployments/history.log | python3 -c "import sys,json; [print(json.loads(l)['hostname']) for l in sys.stdin]"
```

---

## Providers

labinator uses a provider model for external integrations. Each provider is configured in `config.yaml` and can be enabled or disabled independently.

### DNS provider (BIND)

The `bind` DNS provider manages A and PTR records on a BIND server via SSH + Ansible. The playbook edits the zone file directly and calls `rndc reload` to apply changes.

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

The `flat_file` inventory provider manages a plain-text Ansible inventory file on a remote server.

```yaml
ansible_inventory:
  enabled: true
  provider: flat_file
  server: dev.example.com
  user: root
  file: /root/ansible/inventory/hosts
  group: Linux
```

The group header (`[Linux]` in this example) must already exist in the inventory file. The group name is case-sensitive.

Future providers planned: AWX, Semaphore.

### Disabling a provider

Any provider can be disabled independently without affecting the others:

```yaml
dns:
  enabled: false        # Skip all DNS registration and removal

ansible_inventory:
  enabled: false        # Skip all inventory registration and removal

ansible:
  enabled: false        # Skip ALL Ansible steps (post-deploy configuration,
                        # DNS, and inventory are all skipped)
```

When `ansible` is disabled, steps 5–7 of the deploy pipeline are all skipped and the host must be configured manually. When `dns` or `ansible_inventory` is disabled, only that specific step is skipped. All flags default to `true` if absent.

---

## OS Support Table

VM deployments (`deploy_vm.py`) support any cloud-init capable image. The Ansible post-deploy playbook automatically detects the guest OS family and applies the correct package manager, service names, and package list.

| OS | Status | Notes |
|---|---|---|
| Ubuntu 24.04 LTS | Tested and verified | Fully supported |
| Rocky Linux 8 | Tested and verified | First boot takes up to 15 min due to cloud image running full `dnf upgrade`; cloud-init wait uses `/run/cloud-init/result.json` (not `cloud-init status --wait`); Python interpreter resolved via `auto` |
| openSUSE Leap 15.6 | Tested and verified | Works; low priority — not actively maintained until a GitHub issue is filed; harmless Python 3.6 Ansible warning expected |
| Debian 12 / Ubuntu 22.04 | Should work | Same OS family as Ubuntu 24.04; untested |
| AlmaLinux / CentOS Stream / Fedora | Should work | Same OS family as Rocky Linux 8; untested |
| openSUSE Tumbleweed / SLES | Should work | Same OS family as Leap; untested |

**OS families covered by the Ansible multi-OS pattern:**

- **Debian family:** Debian, Ubuntu, Linux Mint, Raspbian, Kali, Pop!_OS
- **RedHat family:** Rocky, AlmaLinux, CentOS Stream, RHEL, Fedora, Oracle Linux
- **Suse family:** openSUSE Leap, openSUSE Tumbleweed, SLES

If a specific package name differs for a new distro, update the relevant `ansible/vars/<Family>.yml` file — no playbook changes needed.

> **Note:** LXC containers (`deploy_lxc.py`) currently support Debian/Ubuntu templates only, as the bootstrap step uses `apt` directly.

---

[← Back to README](../README.md)
