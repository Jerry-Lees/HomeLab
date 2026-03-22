[← Back to README](../README.md)

# Deploying LXC Containers

### About

`deploy_lxc.py` is an interactive wizard that provisions, configures, and onboards a new LXC container in your Proxmox cluster in a single guided session. It handles everything from resource creation through post-deployment configuration, DNS registration, and Ansible inventory registration.

The wizard can be driven entirely interactively, pre-filled from a deployment JSON file, or run fully non-interactively in `--silent` mode for automated pipelines. Both DHCP and static IP addressing are supported.

## Table of Contents

- [CLI Options](#cli-options)
- [Interactive Mode](#interactive-mode)
- [Wizard Navigation](#wizard-navigation)
- [Deploy from File](#deploy-from-file)
- [Silent Mode](#silent-mode)
- [Validate Mode](#validate-mode)
- [Dry-Run Mode](#dry-run-mode)
- [TTL / Expiry](#ttl--expiry)
- [VLAN Check Behavior](#vlan-check-behavior)
- [Preflight Behavior](#preflight-behavior)
- [LXC Feature Flags](#lxc-feature-flags)
- [Static IP Deployment](#static-ip-deployment)
- [Walkthrough: LXC Prompt Order](#walkthrough-lxc-prompt-order)
- [The 7 LXC Deployment Steps](#the-7-lxc-deployment-steps)
- [Deployment Logs](#deployment-logs)
- [Example Scenarios](#example-scenarios)

---

## CLI Options

The following command line options are available:

| Option | Description |
|---|---|
| `--deploy-file FILE` | Load a deployment JSON to pre-fill all prompts (or drive `--silent`) |
| `--silent` | Non-interactive: use all values from `--deploy-file` without prompting. Requires `--deploy-file`. |
| `--validate` | Parse and validate `config.yaml` (and deploy file if given) then exit. No Proxmox connection. |
| `--dry-run` | Validate config and deploy file, then print a full step-by-step plan without making any changes |
| `--preflight` | Run all preflight connectivity and dependency checks then exit |
| `--yolo` | Run preflight but continue through warnings without prompting. Fatal failures still block the deploy. |
| `--ttl TTL` | Set a TTL for this deployment (e.g. `7d`, `24h`, `2w`, `30m`). Stores `ttl` and `expires_at` in the deployment JSON. |
| `--config FILE` | Use an alternate config file instead of the default `config.yaml` in the project root |

---

## Interactive Mode

```bash
python3 deploy_lxc.py
```

Runs the full interactive wizard. All prompts have defaults sourced from `config.yaml`. See [Walkthrough: LXC Prompt Order](#walkthrough-lxc-prompt-order) for the full sequence.

---

## Wizard Navigation

The interactive wizard supports back-navigation at every prompt — you can move backward through the wizard to review or change an earlier answer without starting over.

| Prompt type | How to go back |
|---|---|
| Text input | Press **ESC** |
| Selection list | Arrow up to **← Go Back** and press **Enter** |
| Checkbox | Press **ESC** |

Going back restores the value you previously entered for that prompt, not the config default. ESC at the first prompt exits with `Aborted.` Ctrl+C exits immediately from any prompt.

---

## Deploy from File

```bash
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json
```

Loads a previously saved deployment JSON and pre-fills all prompts with its values. You can review and edit each value before confirming. Useful for redeploying a container with the same or similar configuration.

**Required fields in a hand-written deploy file:**

| Field | Example | Notes |
|---|---|---|
| `hostname` | `"myserver"` | Short name, no domain suffix |
| `ip_address` | `"dhcp"` or `"10.220.220.50"` | `"dhcp"` for DHCP assignment; a static IP address for fixed addressing |
| `cpus` | `2` | |
| `memory_gb` | `4` | |
| `disk_gb` | `20` | |
| `vlan` | `220` | |
| `storage` | `"local-lvm"` | |
| `password` | `"changeme"` | |
| `node` | `"proxmox01"` | Used by `--dry-run` display; wizard re-prompts at deploy time |
| `template_volid` | `"local:vztmpl/ubuntu-24.04-..."` | Used by `--dry-run` display; wizard re-prompts at deploy time |
| `template_name` | `"ubuntu-24.04-..."` | Display name matching the volid |

For static IP deployments, also include:

| Field | Example | Notes |
|---|---|---|
| `prefix_len` | `"24"` | Subnet mask bits (e.g. `"24"` for /24) |
| `gateway` | `"10.220.220.1"` | Default gateway; auto-derived from IP if omitted in silent mode |

Optional fields: `package_profile`, `extra_packages`, `lxc_features`, `ttl`.

> **Note:** `ip_address` must be either the literal string `"dhcp"` or a valid IPv4 address. DHCP deployments skip the static IP conflict preflight check. Static deployments run a ping check to confirm the IP is not already in use before creating the container.

---

## Silent Mode

```bash
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
```

Skips all interactive prompts and deploys using the values in the file. `--silent` requires `--deploy-file`. Any preflight warning or fatal failure causes an immediate exit with a non-zero status code — there are no prompts to continue or retry.

---

## Validate Mode

```bash
python3 deploy_lxc.py --validate
python3 deploy_lxc.py --validate --deploy-file deployments/lxc/myserver.json
```

Parses and validates `config.yaml` (and the deployment file if provided) without connecting to Proxmox or making any changes. Exits 0 on success, 1 on error.

Use `--validate` when you want a simple **pass/fail answer**: "Is my deployment file correct?" It is well suited for CI pipelines where you just need an exit code, not a plan.

---

## Dry-Run Mode

```bash
python3 deploy_lxc.py --dry-run
python3 deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json
```

Does everything `--validate` does, then goes further — prints a full human-readable summary of what *would* happen: hostname, node, template, resources, packages, tags, and each numbered step in order. No Proxmox connection, no Ansible, no changes made.

Use `--dry-run` when you want to **see the plan**: "What will actually happen when I deploy this?" It is the more useful option for day-to-day use before committing to a deployment.

---

## TTL / Expiry

Pass `--ttl` to mark a deployment as temporary:

```bash
./deploy_lxc.py --deploy-file deployments/lxc/test-box.json --ttl 7d
```

The TTL is stored in the deployment JSON as two fields:

```json
"ttl": "7d",
"expires_at": "2026-03-13T14:22:00.000000+00:00"
```

`expires_at` is calculated at deploy time as `now() + TTL`. Deployments without an `expires_at` field are ignored by `expire.py`.

**TTL format:**

| Unit | Meaning | Example |
|---|---|---|
| `m` | minutes | `30m` |
| `h` | hours | `24h` |
| `d` | days | `7d` |
| `w` | weeks | `2w` |

See [expiry.md](expiry.md) for the full TTL management workflow.

---

## VLAN Check Behavior

Before creating the container, the script verifies that the requested VLAN exists on the target node. For traditional VLAN bridges, it looks for a `vmbr0.220`-style interface in the node's network list. For VLAN-aware bridges, the check passes for any VLAN tag — the bridge accepts all tags and relies on upstream switch configuration to enforce VLAN membership.

> **Note:** If your container gets an IP but it is on the wrong network, verify the VLAN is trunked on the physical port connected to the Proxmox node. The VLAN check cannot detect upstream switch misconfigurations.

---

## Preflight Behavior

`deploy_lxc.py` runs a preflight check suite automatically at the start of every deployment — before any prompts are shown or Proxmox resources are created.

Run standalone to check your environment and exit without deploying:

```bash
./deploy_lxc.py --preflight
./deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json
```

Add `--deploy-file` to also check the DNS hostname and static IP. Add `--yolo` to continue through warnings and only block on fatal failures.

See [preflight.md](preflight.md) for the full list of checks, fatal vs warning behavior, and flag interactions.

---

## LXC Feature Flags

LXC containers share the host kernel, so Proxmox must explicitly grant access to kernel features that are blocked by default inside the container's security namespace. VMs don't have this concept — they get a full kernel and Docker, NFS, FUSE, etc. work natively.

The deploy wizard presents a checkbox step after the package profile so you can enable the flags your container actually needs.

| Flag | What it enables |
|---|---|
| `nesting=1` | Containers inside the container (Docker, Podman, LXC-in-LXC) |
| `keyctl=1` | Kernel keyring access — required by some container runtimes and systemd services |
| `fuse=1` | FUSE filesystem mounts inside the container (rclone, sshfs, etc.) |
| `mknod=1` | Creating device nodes — needed by some specialized workloads |
| `mount=nfs` | NFS mounts inside the container |
| `mount=cifs` | CIFS/SMB mounts inside the container |

**Profile-driven defaults** — if the selected package profile defines `lxc_features` in `config.yaml`, those flags are pre-checked when the prompt appears. For example, the `docker-host` profile pre-checks `nesting=1` and `keyctl=1`. You can add or remove flags before confirming.

```
LXC feature flags (optional):
  [ ] nesting=1   — nested containers (Docker, Podman, LXC-in-LXC)
  [x] keyctl=1    — kernel keyring (required by some container runtimes)
  [ ] fuse=1      — FUSE filesystem mounts (rclone, sshfs, etc.)
  [ ] mknod=1     — create block/character device nodes
  [ ] mount=nfs   — NFS mounts inside the container
  [ ] mount=cifs  — CIFS/SMB mounts inside the container
(space to select/deselect, Enter to confirm, ESC to go back)
```

Select nothing (leave all unchecked) to create the container with no extra feature flags — the default for a minimal container.

Applied flags are shown in the confirmation summary before anything is created, and are saved in the deployment JSON under `lxc_features` for reference.

In `--silent` mode, only the profile's `lxc_features` are used — the interactive checkbox is skipped.

---

## Static IP Deployment

By default, LXC containers use DHCP — the IP is assigned by your network's DHCP server at boot and discovered by labinator via the Proxmox API. Static IP addressing is also supported when you need a predictable, fixed address.

**At the IP address prompt**, enter a static IP address instead of leaving it blank:

```
? IP address for container: (leave blank for DHCP) 10.220.220.50
? Prefix length (subnet mask bits): 24
? Gateway: 10.220.220.1
```

Leaving the IP prompt blank selects DHCP. Entering an IP address triggers two additional prompts for prefix length (defaulting to `24`) and gateway (auto-derived from the IP if left blank — e.g. `10.220.220.1` for a `10.220.220.x` address).

The deployment summary shows which mode was selected:

```
│  Network   │  vmbr0.220  (DHCP)                                     │   ← DHCP deployment
│  Network   │  vmbr0.220  (static 10.220.220.50/24  gw 10.220.220.1)  │   ← static deployment
```

**How static IP works in Proxmox:** The IP, prefix, and gateway are passed to the Proxmox API as part of the `net0` interface configuration at container creation time — `ip=10.220.220.50/24,gw=10.220.220.1`. This is set at the hypervisor level, not inside the container, so no additional network configuration is needed inside the guest.

**In the deployment JSON**, static deployments record the configured IP directly in `ip_address`. DHCP deployments record `"dhcp"` in `ip_address` and the actual DHCP-assigned address in `assigned_ip`.

> **Note:** For static deployments, the preflight `Static IP in use` check pings the IP before creating the container. If it responds, the deploy is blocked — preventing duplicate IP conflicts. For DHCP deployments, this check is skipped entirely since the IP is assigned by the DHCP server at boot.

---

## Walkthrough: LXC Prompt Order

Resource questions come **before** node selection so that nodes without enough capacity for the requested resources are filtered out of the list.

**1. Hostname**
```
Hostname for the new container:
(short name, e.g. myserver — .example.com will be appended in inventory)
> myserver
```
The short hostname only — no domain suffix. The domain from `node_domain` (under the `proxmox:` block in `config.yaml`) is appended automatically when registering DNS and inventory records.

**2. vCPUs / Memory / Disk / VLAN / Password**
```
Number of vCPUs:                     [2]
Memory (GB):                         [4]
Disk size (GB):                      [100]
VLAN tag (bridge: vmbr0.<vlan>):     [220]
Root / admin user password:          [changeme]
```
Defaults are sourced from the `defaults:` block in `config.yaml`. The VLAN tag determines which network the container is placed on. The password is set for both `root` and the secondary admin user on the container.

**3. IP address / Prefix / Gateway**

```
? IP address for container: (leave blank for DHCP)
```

Leave blank to use DHCP (the default). Enter an IPv4 address to use static addressing — two follow-up prompts will appear for prefix length and gateway:

```
? Prefix length (subnet mask bits): 24
? Gateway: 10.220.220.1
```

The prefix length defaults to `24`. The gateway is auto-derived from the last octet of the IP (e.g. `10.220.220.50` → `10.220.220.1`) if left blank.

**4. Package profile** — Optional. Select a role-based package set or skip for a minimal baseline install. Profiles are named groups of packages defined in `config.yaml` under `package_profiles`. Each profile also applies one or more Proxmox tags to the container so it is easy to identify its role at a glance. Selecting a profile installs its packages during the Ansible post-deploy step, after the baseline tools. Install order: baseline → profile packages → any extra packages. Select `[none]` to skip and get only the baseline install.

```
Package profile (optional):
  [none]
  web-server
  database
  docker-host
  monitoring-node
  dev-tools
  nfs-server
```

**5. Extra packages** — Optional one-off packages to install on top of the baseline and profile. Enter as a comma-separated list or leave blank.

```
Extra packages to install (optional):
(comma-separated, e.g. htop, curl  —  leave blank for none)
```

**6. LXC feature flags** — Checkbox prompt for kernel feature flags. Any flags defined in the selected profile's `lxc_features` key are pre-checked. Add or remove as needed, or leave all unchecked for no extra features. See [LXC Feature Flags](#lxc-feature-flags) for the full flag reference.

**7. Node selection** (filtered by requested resources)
```
Select Proxmox node (★ = most free RAM; 2 node(s) hidden — over resource threshold):
  ★ proxmox03  —  54.2 GB free / 128.0 GB RAM  (CPU: 18%)
    proxmox02   —  28.4 GB free / 64.0 GB RAM   (CPU: 12%)
```
Only nodes with enough headroom are shown. The star (★) marks the node with the most free RAM — a reasonable default for most deployments. Nodes are hidden if allocating the requested resources would push them above `cpu_threshold` or `ram_threshold` (set in `config.yaml` under `defaults`).

> **Note:** Resource questions (vCPUs, memory, disk) are asked *before* node selection so that nodes which cannot satisfy the request are automatically hidden from the node list.

**8. OS Template**
```
Select OS template (Ubuntu templates listed first):
  [Net-Images] ubuntu-24.04-standard_24.04-2_amd64.tar.zst
  [local] debian-12-standard_12.7-1_amd64.tar.zst
```
Queried live from the selected node — only templates already downloaded on that node are shown. Ubuntu versions are listed first. The storage name in brackets (e.g. `[local]`) indicates where the template is stored on the node.

**9. Storage pool** — Only shown if more than one storage pool is available on the selected node. Determines where the container's root disk is created.

```
Select storage pool for container root disk:
  local-lvm
  ceph-pool
```

**10. Confirmation summary and pre-creation resource check** — Displays a full summary of all selected values before anything is created. A final resource check is run against the selected node to confirm it still has enough capacity (resources may have changed since node selection). Confirm to proceed or abort to cancel without making any changes.

```
                      Deployment Summary
┌────────────┬───────────────────────────────────────────────┐
│  VMID      │  114                                          │
│  Hostname  │  myserver                                     │
│  Node      │  proxmox03                                    │
│  Template  │  ubuntu-24.04-standard_24.04-2_amd64.tar.zst  │
│  vCPUs     │  2                                            │
│  Memory    │  4 GB (4096 MB)                               │
│  Disk      │  100 GB  →  local-lvm                         │
│  Network   │  vmbr0.220  (DHCP)                            │
│  Tags      │  auto-deploy;Docker                           │
│  Features  │  nesting=1,keyctl=1                           │
│  Users     │  root, admin (same password)                  │
│  Timezone  │  America/Chicago                              │
│  NTP       │  pool.ntp.org, time.nist.gov                  │
│  SNMP      │  community='your-community' (rw) on :161      │
└────────────┴───────────────────────────────────────────────┘

? Proceed with deployment? (Y/n)
```

The **Features** row only appears when at least one flag is selected. The **TTL / Expires** row only appears when `--ttl` was passed. Selecting no flags creates the container with no extra capabilities.

For static deployments, the Network row shows the configured IP, prefix, and gateway.

---

## The 7 LXC Deployment Steps

**Step 1** — Creates the container via the Proxmox API with the configured resources, VLAN tag, and options. A VMID is selected automatically from the next available ID in the cluster. If LXC feature flags were selected, they are applied immediately after creation via SSH (`pct set`) — the Proxmox API only allows `nesting=1` to be set by API tokens; all other flags require a direct `root` SSH connection to the node.

```
─── Step 1/7: Creating LXC container ───
  Creating container 114 (myserver) on proxmox03...
  ✓ Container 114 created
  Applying LXC feature flags via SSH (nesting=1,keyctl=1)...
  ✓ Feature flags applied: nesting=1,keyctl=1
```

**Step 2** — Starts the container and waits for it to come online.

```
─── Step 2/7: Starting container ───
  Starting container...
  ✓ Container started
```

**Step 3** — For DHCP deployments: polls the Proxmox guest agent until a DHCP-assigned IP address is visible, up to 2 minutes. The discovered IP is stored as `assigned_ip` in the deployment JSON so DNS cleanup works correctly on decommission.

For static IP deployments, this step is skipped — the IP was already set at container creation time and no polling is needed.

```
─── Step 3/7: Waiting for DHCP IP address ───       ← DHCP
  Polling for DHCP lease (up to 2 min)...
  ✓ Container IP: 10.220.220.114 /24

─── Step 3/7: Static IP — no DHCP wait needed ───   ← static
  Using static IP: 10.220.220.50/24  gw 10.220.220.1
```

**Step 4** — Installs `openssh-server`, sets passwords, and enables root SSH login. For LXC containers, network addressing (whether DHCP or static) is handled at the hypervisor level via the Proxmox API — no additional network configuration is written inside the container.

```
─── Step 4/7: Bootstrapping SSH in container ───
  Connecting to Proxmox node proxmox03.example.com for bootstrap...
  Updating apt cache in container...
  Installing openssh-server...
  Enabling and starting SSH...
  Allowing root SSH login...
  Setting root password...
  ✓ Bootstrap complete — SSH is ready
  Waiting for SSH to become reachable...
  ✓ SSH is ready
```

> **Under The Hood**
> `pct exec` runs commands directly inside the container filesystem via the Proxmox host — no SSH required. This is how SSH gets installed before SSH exists.

**Step 5** — Runs the post-deploy Ansible playbook (`ansible/post-deploy.yml`) against the new container. By this point SSH is already up (Step 4 confirmed it), so Ansible connects immediately without any additional waiting.

What Ansible does, in order:

1. **Baseline install** — packages every container gets regardless of profile: `curl`, `wget`, `vim`, `git`, `htop`, `net-tools`, and others defined in the playbook
2. **OS upgrade** — runs a full package upgrade so the container starts life fully patched
3. **Profile packages** — installs the package set from the profile selected at the prompt (e.g. `docker-ce` and friends for `docker-host`, `nginx` and `certbot` for `web-server`). Skipped if `[none]` was selected
4. **Extra packages** — any additional packages entered at the extra packages prompt, installed after the profile
5. **Users** — creates the secondary admin user and sets passwords for both `root` and the admin user
6. **NTP** — configures chrony with the servers from `config.yaml`
7. **SNMP** — configures `snmpd` with the community, location, and contact from `config.yaml`
8. **Timezone** — sets the system timezone

Skipped entirely if `ansible.enabled` is `false` in `config.yaml`.

```
─── Step 5/7: Running post-deployment configuration (Ansible) ───
  Running: ansible-playbook -i ... ansible/post-deploy.yml
  ✓ Post-deployment configuration complete
```

**Step 6** — DNS pre-check and registration. Before writing any records, the configured DNS server is queried directly for the hostname. If a record already exists:
- **Same IP:** idempotent notice — continues, warns the existing host may be orphaned
- **Different IP:** shows both IPs and prompts: **[O]verwrite**, **[S]kip DNS**, **[A]bort**
- **Multiple records:** shows all existing records with count, then prompts

In `--silent` mode, existing records are overwritten automatically with a logged warning. A and PTR records are written to the BIND zone files and `rndc reload` is called. If the reverse zone file does not exist, the PTR record is skipped gracefully. Skipped if `dns.enabled` is `false` in `config.yaml`.

```
─── Step 6/7: Registering DNS records ───
  Registering myserver.example.com → 192.168.1.114 on 10.0.0.10...
  ✓ DNS registered: myserver.example.com → 192.168.1.114 (+ PTR)
```

**Step 7** — Adds the new host to the Ansible inventory file on the configured inventory server. Also runs `ssh-keyscan` and `ssh-copy-id` from the inventory server to the new container so Ansible can connect with key-based auth immediately. Skipped if `ansible_inventory.enabled` is `false` in `config.yaml`.

```
─── Step 7/7: Updating Ansible inventory ───
  Connecting to dev.example.com to update inventory...
  ✓ Inventory updated on dev.example.com
```

A history log entry is written to `deployments/history.log` on completion.

Once all steps complete, a deployment summary panel is printed:

```
╭─────────────────────────────────── ✓ All Done ────────────────────────────────────╮
│ Deployment Complete!                                                               │
│                                                                                    │
│ Hostname   :  myserver                                                             │
│ FQDN       :  myserver.example.com                                                 │
│ IP Address :  192.168.1.114                                                        │
│ VMID       :  114  (on proxmox03)                                                  │
│ SSH        :  ssh root@192.168.1.114                                               │
│              ssh admin@192.168.1.114                                               │
│                                                                                    │
│ Deployment file: deployments/lxc/myserver.json                                    │
│ Tagged 'auto-deploy' with specs note in Proxmox.                                  │
│ DNS: A + PTR records registered on 10.0.0.10.                                     │
│ Added to Ansible inventory group [Linux].                                          │
│                                                                                    │
╰────────────────────────────────────────────────────────────────────────────────────╯
```

---

## Deployment Logs

Every interactive deploy (non-`--silent`) writes a full log to `logs/last-deployment.log` in the project root. The log captures everything printed to the terminal — preflight results, wizard selections, step output, and the final summary — with ANSI color codes stripped for readability.

```
logs/last-deployment.log
```

The log is overwritten on each run. It is excluded from git via `.gitignore`.

The path to the log is printed at the end of every deployment:

```
Log: /home/dad/projects/HomeLab/labinator/logs/last-deployment.log
```

> **Note:** `--silent` mode (used by batch deploy) does not write to `last-deployment.log` directly. `deploy.py` captures subprocess output and writes it to `last-deployment.log` itself at the end of the batch run.

---

## Example Scenarios

**Deploy a new container with DHCP (interactive):**
```bash
python3 deploy_lxc.py
```
Run the full wizard. Leave the IP address prompt blank to use DHCP. The container's DHCP-assigned IP is discovered at boot and recorded in the deployment file.

---

**Deploy a new container with a static IP (interactive):**
```bash
python3 deploy_lxc.py
```
At the IP address prompt, enter a static IP (e.g. `10.220.220.50`). Follow-up prompts for prefix length (default `24`) and gateway (auto-derived if left blank) appear. The IP is set at the hypervisor level — no manual network config needed inside the container.

---

**Redeploy from a saved deployment file:**
```bash
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json
```
All prompts are pre-filled from the file. Review and accept each value (or change any of them) before confirming. Useful after decommissioning a container and rebuilding it with the same configuration.

---

**Check everything is ready before deploying:**
```bash
python3 deploy_lxc.py --preflight --deploy-file deployments/lxc/myserver.json
```
Runs all preflight checks — including DNS hostname conflict and static IP ping check — and exits without deploying.

---

**Automated / CI deploy from a complete deployment file:**
```bash
python3 deploy_lxc.py --deploy-file deployments/lxc/myserver.json --silent
```
No prompts. All values come from the file. Any preflight failure causes immediate exit 1. Used by `deploy.py` batch mode internally.

---

**Dry-run: see exactly what will happen without making any changes:**
```bash
python3 deploy_lxc.py --dry-run --deploy-file deployments/lxc/myserver.json
```
Validates config and deploy file, then prints a full human-readable deployment plan. Nothing is created.

---

[← Back to README](../README.md)
