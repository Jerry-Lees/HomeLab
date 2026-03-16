# Deployment Files — Schema Reference

Deployment files are JSON records written to `deployments/lxc/` (LXC containers) or
`deployments/vms/` (QEMU VMs) when a resource is successfully deployed. They serve as the
authoritative record of what was deployed, where, and how — and are the primary input for
decommission, expiry, renewal, and re-deploy operations.

**Why they matter:** Without a deployment file, labinator has no record of a resource.
Decommission scripts read the file to know which node to contact, what VMID to destroy,
which IP to clean from DNS, and which hostname to remove from inventory. `expire.py` scans
these files for `expires_at` to manage TTLs. Pre-building a deployment file and passing it
with `--deploy-file` allows silent/automated deployments.

---

## File Naming and Location

```
deployments/
├── lxc/
│   └── <hostname>.json        ← one file per LXC container
└── vms/
    └── <hostname>.json        ← one file per VM
```

Files are named after the container/VM hostname (short name, no domain suffix).
All deployment files are excluded from git via `.gitignore`. Example files
(`example-deployment.json`, `example-vm-deployment.json`) are explicitly tracked as
reference.

---

## LXC Deployment File

Written by `deploy_lxc.py`. Read by `decomm_lxc.py`, `expire.py`, and `cleanup_tagged.py`.

### Full Example

```json
{
  "hostname": "my-example-server",
  "fqdn": "my-example-server.lees-family.io",
  "node": "proxmoxb01",
  "vmid": 142,
  "template_volid": "local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
  "template_name": "ubuntu-24.04-standard_24.04-2_amd64.tar.zst",
  "cpus": 2,
  "memory_gb": 4.0,
  "disk_gb": 100,
  "storage": "local-lvm",
  "vlan": 220,
  "bridge": "vmbr0",
  "password": "changeme",
  "ip_address": "10.220.220.150",
  "assigned_ip": "10.220.220.150",
  "prefix_len": "24",
  "deployed_at": "2026-03-05 22:45:00",
  "ttl": "7d",
  "expires_at": "2026-03-12T22:45:00.000000+00:00",
  "preflight": true
}
```

### Field Reference

| Field | Required | Auto-populated | Description |
|---|---|---|---|
| `hostname` | ✓ | — | Short hostname (no domain suffix). Used as the resource name in Proxmox, DNS, and inventory. |
| `fqdn` | ✓ | ✓ | Fully qualified domain name. Constructed from `hostname` + domain suffix from config. |
| `node` | ✓ | — | Proxmox node name where the container lives. Must match a node returned by the Proxmox API. |
| `vmid` | ✓ | ✓ | Proxmox VMID assigned at creation time. Auto-assigned by Proxmox; recorded here for all future operations. |
| `template_volid` | ✓ | — | Full Proxmox volume ID of the LXC template. e.g. `local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst`. Used by `deploy_lxc.py` when re-deploying from this file. |
| `template_name` | ✓ | — | Filename portion of the template. Used for display and matching available templates. |
| `cpus` | ✓ | — | Number of vCPUs allocated to the container. |
| `memory_gb` | ✓ | — | RAM allocated in GB. Converted to MB internally for the Proxmox API. |
| `disk_gb` | ✓ | — | Root disk size in GB. |
| `storage` | ✓ | — | Proxmox storage pool for the container root disk. e.g. `local-lvm`, `Net-Images`. |
| `vlan` | ✓ | — | VLAN tag applied to the network interface. |
| `bridge` | ✓ | — | Proxmox bridge the container is attached to. Combined with vlan as `vmbr0.220`. |
| `password` | ✓ | — | Root and secondary user password set at deploy time. Stored here for reference and re-deploy. |
| `ip_address` | ✓ | — | IP address assigned at deploy time. Either a static IP (`10.220.220.150`) or `dhcp`. |
| `assigned_ip` | ✓ | ✓ | The actual IP address the container is reachable on. For static deployments, same as `ip_address`. For DHCP, populated after the DHCP lease is discovered. Used by DNS removal during decommission — this is the authoritative IP for cleanup. |
| `prefix_len` | ✓ | — | Network prefix length (subnet mask bits). e.g. `24` for /24. |
| `deployed_at` | ✓ | ✓ | Timestamp when the deployment completed. Format: `YYYY-MM-DD HH:MM:SS`. |
| `ttl` | optional | — | Time-to-live for this deployment. Accepted formats: `30m`, `24h`, `7d`, `2w`. Set with `--ttl` at deploy time. If present, `expires_at` is also set. Deployments without `ttl` are not tracked by `expire.py`. |
| `expires_at` | optional | ✓ | ISO 8601 UTC timestamp when this deployment expires. Calculated from `deployed_at` + `ttl`. Scanned by `expire.py --check` and `expire.py --reap`. |
| `preflight` | optional | — | Whether to run preflight checks before re-deploying from this file. Default: `true`. Set `false` with `--yolo` or to skip checks for a known-good re-deploy. |

---

## VM Deployment File

Written by `deploy_vm.py`. Read by `decomm_vm.py`, `expire.py`, and `cleanup_tagged.py`.

### Full Example

```json
{
  "type": "vm",
  "hostname": "my-example-vm",
  "fqdn": "my-example-vm.lees-family.io",
  "node": "proxmoxb01",
  "vmid": 200,
  "cloud_image_storage": "local",
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
  "assigned_ip": "10.220.220.200",
  "prefix_len": "24",
  "gateway": "10.220.220.1",
  "deployed_at": "2026-03-06 10:00:00",
  "ttl": "1d",
  "expires_at": "2026-03-07T10:00:00.000000+00:00",
  "preflight": true
}
```

### Field Reference

Fields shared with LXC (same meaning): `hostname`, `fqdn`, `node`, `vmid`, `cpus`,
`memory_gb`, `disk_gb`, `storage`, `vlan`, `bridge`, `password`, `ip_address`,
`assigned_ip`, `prefix_len`, `deployed_at`, `ttl`, `expires_at`, `preflight`.

VM-specific fields:

| Field | Required | Auto-populated | Description |
|---|---|---|---|
| `type` | ✓ | ✓ | Always `"vm"`. Distinguishes VM files from LXC files when both are processed together (e.g. by `expire.py`). LXC files do not have this field. |
| `cloud_image_storage` | ✓ | — | Proxmox storage where the cloud image is cached. e.g. `local`. Must support `iso` content type. |
| `cloud_image_filename` | ✓ | — | Filename of the cloud image on the Proxmox node. e.g. `noble-server-cloudimg-amd64.img`. |
| `cloud_image_url` | ✓ | — | Download URL for the cloud image. Used if `image_refresh: true` or the image is not cached. |
| `image_refresh` | optional | — | If `true`, re-download the cloud image before deploying even if the cached file exists. Default: `false`. |
| `gateway` | optional | — | Default gateway for static IP deployments. Not set for DHCP deployments. |

---

## Using Deployment Files as Input (`--deploy-file`)

Both `deploy_lxc.py` and `deploy_vm.py` accept `--deploy-file <path>` to pre-fill prompts
from an existing deployment file. Combined with `--silent`, this enables fully automated
re-deployments with no user interaction.

**Required fields for `--deploy-file`:** `hostname`, `node`, `cpus`, `memory_gb`,
`disk_gb`, `storage`, `vlan`, `bridge`, `password`.

**Optional but recommended:** `ip_address`, `vmid` (if you want the same VMID),
`ttl`, `preflight`.

**Fields that are always auto-populated and should not be hand-crafted:**
`vmid` (if not specified), `fqdn`, `assigned_ip`, `deployed_at`, `expires_at`.

---

## Cleanup Action List File (`--list-file`)

Used by `cleanup_tagged.py --list-file`. A separate format — see
`docs/specs/cleanup-action-list.md`.
