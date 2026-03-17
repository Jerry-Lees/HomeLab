# Feature Ideas

## Interactive Config File Wizard (`setup.py` / `configure.py`)

Walk the user through creating a `config.yaml` interactively instead of requiring them
to manually copy and edit `config.yaml.example`. Useful for first-time setup and for
generating alternate config files for different clusters.

### Behavior

- Ask each required field with a prompt, description, and example value.
- Validate input inline (e.g. IP format, non-empty strings, valid token format).
- Write the completed config to `config.yaml` (or a path specified by `--output`).
- `setup.sh` (the existing installer) should invoke this automatically if `config.yaml`
  does not exist after dependencies are installed.

### Usage

```bash
./configure.py                        # creates config.yaml interactively
./configure.py --output prod.yaml     # create an alternate config file
```

### Implementation notes

- Use `questionary` for prompts — consistent with the rest of labinator.
- Group prompts by section (proxmox, dns, ansible_inventory, defaults, etc.).
- Offer sensible defaults where possible (e.g. `dns.enabled: true`, `vlan: 220`).
- After writing the file, always offer to run `--preflight` against it immediately.
  Preflight already includes config validation as its first check (`Config valid`) so
  there is no need for a separate `validate_config()` call — preflight covers it and
  also verifies connectivity, SSH keys, DNS, and inventory in one pass.

---

## LXC Feature Flags (Profile-Driven + Manual Override)

LXC containers share the host kernel, so Proxmox must explicitly grant access to kernel
features that would otherwise be blocked by the container's security namespace. VMs don't
have this concept — they get a full kernel and Docker, NFS, FUSE, etc. all just work natively.

Right now `deploy_lxc.py` hardcodes `features: nesting=1` on every container regardless of
what it's actually for. A database or web server gets Docker capabilities it doesn't need.
A Docker host is fine. An NFS server needs `mount=nfs` instead. This is a blunt instrument.

### Proxmox LXC feature flags

| Flag | What it enables |
|---|---|
| `nesting=1` | Containers inside the container (Docker, Podman, LXC-in-LXC) |
| `keyctl=1` | Kernel keyring access — required by some container runtimes and systemd services |
| `fuse=1` | FUSE filesystem mounts inside the container (rclone, sshfs, etc.) |
| `mknod=1` | Creating device nodes — needed by some specialized workloads |
| `mount=nfs` | NFS mounts inside the container |
| `mount=cifs` | CIFS/SMB mounts inside the container |

### Proposed behavior

Tie feature flags to package profiles in `config.yaml`. If you select the `docker-host`
profile, `nesting=1` and `keyctl=1` turn on automatically. A `database` or `web-server`
profile gets no extra features. A manual override prompt catches anything not covered by a
profile.

```yaml
package_profiles:
  docker-host:
    packages:
      - docker-ce
      - docker-ce-cli
      - containerd.io
      - docker-compose-plugin
    tags:
      - Docker
    lxc_features:
      - nesting=1
      - keyctl=1

  nfs-server:
    packages:
      - nfs-kernel-server
      - nfs-common
    tags:
      - NFS
      - Storage
    lxc_features:
      - mount=nfs
```

If no profile is selected (or the profile defines no `lxc_features`), show a manual
toggle prompt before confirming the deployment:

```
LXC feature flags (space to toggle, enter to confirm):
  [ ] nesting=1   (Docker / container-in-container)
  [ ] keyctl=1    (kernel keyring)
  [ ] fuse=1      (FUSE mounts — rclone, sshfs)
  [ ] mount=nfs   (NFS mounts inside container)
  [ ] mount=cifs  (CIFS/SMB mounts inside container)
```

### Implementation notes

- Add `lxc_features` key (list of strings) to each profile in `config.yaml`. No key = no
  extra features for that profile.
- At deploy time, collect features from the selected profile. If the list is empty AND no
  `--silent` flag is set, show the toggle prompt for manual selection.
- In `--silent` mode, use only the profile's features — no prompt.
- Combine features into a comma-separated string for the Proxmox API:
  `features: "nesting=1,keyctl=1"`.
- Store the applied features list in the deployment JSON under `lxc_features` for reference.
- Remove the hardcoded `nesting=1` from the container creation params — replace it with
  the resolved feature string (which may be empty).
- Update `--dry-run` summary table to show a "Features" row when any flags are set.
- This is LXC-only — VMs have no equivalent concept.

---

## Resource Resize Script

Add a `resize.py` (or `resize_lxc.py` / `resize_vm.py`) that modifies CPU, RAM, or disk on an
already-deployed container or VM without a full redeploy.

### Implementation notes

- Accepts `--deploy-file` to identify the target (reads VMID and node from the JSON).
- Interactive prompts (pre-filled with current values) for CPU, memory, disk.
- For LXC: uses Proxmox API `PUT /nodes/{node}/lxc/{vmid}/config`.
- For VM: uses `PUT /nodes/{node}/qemu/{vmid}/config`; disk resize requires an additional
  `POST /nodes/{node}/qemu/{vmid}/resize`.
- Updates the deployment JSON with the new values after a successful resize.
- Warn if resizing down (Proxmox does not support disk shrink).

---

## Profile Tag Colors

When a package profile with tags is selected at deploy time, automatically configure the
corresponding tag colors in Proxmox (Datacenter → Options → Tag Style) so the tags appear
with consistent, role-appropriate colors in the Proxmox UI — without manual configuration.

### Implementation notes

- Add an optional `color` key per tag in `config.yaml` under each profile's `tags` block:
  ```yaml
  web-server:
    packages: [...]
    tags:
      - name: WWW
        color: "#0070c0"     # blue
  ```
- On deploy, after the VM/LXC is created, call `PUT /cluster/options` with the `tag-style`
  color-map to register the color for any new tags not already configured.
- Only updates tags that labinator manages — does not overwrite manually configured colors.
- Falls back gracefully if the API call fails (tags still applied, just without color).

---

## Proxmox Firewall Rules

After a VM or LXC is deployed, automatically configure Proxmox firewall rules on the instance
via the Proxmox API — no iptables or ufw involvement. Rules are defined alongside package
profiles in `config.yaml` so the right ports open automatically when a profile is selected.

### Usage

```yaml
# config.yaml
package_profiles:
  web-server:
    packages:
      - nginx
      - certbot
    tags:
      - WWW
    firewall_rules:
      - direction: in
        action: ACCEPT
        proto: tcp
        dport: "80"
        comment: HTTP
      - direction: in
        action: ACCEPT
        proto: tcp
        dport: "443"
        comment: HTTPS

  database:
    packages:
      - mariadb-server
    tags:
      - DB
    firewall_rules:
      - direction: in
        action: ACCEPT
        proto: tcp
        dport: "3306"
        comment: MariaDB
```

### How it works

- Proxmox firewall operates at three levels: datacenter, node, and VM/LXC instance.
- Labinator adds rules at the **VM/LXC level** via:
  - `POST /nodes/{node}/qemu/{vmid}/firewall/rules` (VMs)
  - `POST /nodes/{node}/lxc/{vmid}/firewall/rules` (LXC)
- The VM firewall must also be **enabled** (`PUT .../firewall/options` with `enable: 1`).
  The NIC already has `firewall=1` set at creation — this enables the per-VM ruleset.
- Optionally supports **Proxmox Security Groups** — define rules once at the cluster level
  and reference by group name, rather than duplicating per-profile:
  ```yaml
  firewall_security_group: web-server   # references cluster firewall group
  ```

### Implementation notes

- Rules are applied as a post-creation step, after the VM/LXC exists in Proxmox.
- If no `firewall_rules` or `firewall_security_group` is defined for a profile, the
  firewall is left in its default state (disabled at VM level, existing datacenter rules apply).
- Store applied rule IDs (or a flag) in the deployment JSON so decomm can optionally
  clean up instance-level rules on decommission.
- Add `firewall_enabled: true` to the profile to explicitly enable the VM-level firewall
  when rules are applied.

---

## Batch Deploy

Deploy multiple containers and/or VMs in sequence from a list of deployment JSON files using a
single unified script, rather than running deploy_lxc.py or deploy_vm.py once per host.

### Usage

```bash
python3 deploy.py --batch deployments/vms/web1.json deployments/lxc/db1.json deployments/vms/app1.json
python3 deploy.py --batch-dir deployments/batch/
```

### Design decisions

- **One script for everything** — a single `deploy.py` entry point reads the `"type"` field
  from each deployment JSON (`"vm"` or `"lxc"`) and dispatches to the appropriate deploy logic.
  No need to remember which script to use.
- **Continue on failure** — if one host fails, the error is logged and the batch continues with
  the remaining files. A summary table is printed at the end showing each host's result.
- **Sequential, not parallel** — deploys run one at a time to avoid VMID conflicts and Proxmox
  API rate issues.

### Implementation notes

- `--batch` accepts one or more JSON file paths; `--batch-dir` processes all JSON files in a
  directory alphabetically. Both are mutually exclusive with `--deploy-file`.
- Each file is deployed in silent mode (no interactive prompts) — all required values must be
  present in the deployment file.
- Skip any file whose VMID is already running in Proxmox (idempotent re-runs).
- Print a `rich` summary table at the end: hostname, type, result (✓ / ✗), and elapsed time.
- The `--validate` flag pairs naturally with batch — validate all files before starting any deploys.

---

## Proxmox Cluster Import / Scan

Scan an existing Proxmox cluster and generate labinator deployment JSON files for VMs and LXCs
that were provisioned outside of labinator — allowing you to adopt an existing environment
without starting from scratch.

### Usage

```bash
python3 import.py                          # scan all nodes, interactive review
python3 import.py --node proxmox01         # scan a single node
python3 import.py --silent                 # generate files without prompting
```

### Implementation notes

- Query all nodes via the Proxmox API and enumerate every VM (`qemu`) and LXC (`lxc`).
- For each host, extract: VMID, hostname (from config name), node, CPU, RAM, disk size,
  storage pool, VLAN/bridge, IP (from cloud-init config or guest agent), and tags.
- Skip any VMID that already has a matching deployment file in `deployments/vms/` or
  `deployments/lxc/` (unless `--overwrite` is passed).
- Fields that can't be determined automatically (e.g. `cloud_image_filename`, `password`)
  are left blank or set to sensible placeholders — clearly marked so the operator knows
  what to fill in before using the file for a redeploy.
- In interactive mode, display each discovered host and confirm before writing the file.
  In `--silent` mode, write all files without prompting.
- Print a summary at the end: how many files written, how many skipped, how many need
  manual review.
- Pairs naturally with Batch Deploy — import first, then batch redeploy to a new cluster.

---

## Cacti Monitoring Integration

Cacti does not have an official REST API (it has been on the roadmap for v1.3 for several years).
It does expose a **CLI PHP script interface** that can be driven over SSH.

### Deploy: Auto-add host to Cacti

After a VM/LXC is provisioned, SSH to the Cacti server and run:

```bash
php /path/to/cacti/cli/add_device.php \
  --description="<hostname>" \
  --ip="<ip_address>" \
  --template=<template_id> \
  --community="<snmp_community>" \
  --version=2
```

Then attach graph templates:

```bash
php /path/to/cacti/cli/add_graphs.php \
  --host-id=<id> \
  --graph-template-id=<id>
```

### Decommission: Auto-remove host from Cacti

During decomm (Step 3 or a new Step 5), SSH to the Cacti server and run:

```bash
php /path/to/cacti/cli/remove_device.php --device-id=<id>
```

### Implementation notes

- Same pattern as existing DNS and Ansible inventory steps: an Ansible playbook or direct
  `subprocess.run()` SSH call.
- Config keys to add under a new `cacti:` block in `config.yaml`:
  - `enabled: true/false`
  - `server:` (IP or hostname of Cacti server)
  - `ssh_user:` (e.g. root)
  - `cli_path:` (path to Cacti's `cli/` directory on the server)
  - `default_template_id:` (Cacti host template ID to apply)
- The Cacti device ID returned by `add_device.php` could be stored in the deployment JSON
  for use during decommission.
- SNMP community is already stored in config under `snmp.community` — reuse it.

### References

- [Cacti CLI: add_device.php](https://files.cacti.net/docs/html/cli_add_device.html)
- [Cacti REST API forum discussion](https://forums.cacti.net/viewtopic.php?t=58539)
- [Cacti CLI command reference](https://nsrc.org/workshops/2019/ubuntunet-nren-noc/netmgmt/en/cacti/cacti-cli-commands.html)

---

## Netbox Integration

Register new hosts in Netbox on deploy and remove them on decommission, mirroring the existing
DNS and Ansible inventory pattern.

### Deploy: Create device/IP in Netbox

- Create or update a **Device** (or **Virtual Machine**) record with name, primary IP, and role.
- Assign the IP to the correct **Prefix** / **VLAN**.
- Store the Netbox object ID in the deployment JSON for use during decommission.

### Decommission: Remove from Netbox

- Delete the IP address record and the VM/device record using the stored object ID.

### Implementation notes

- Use the `pynetbox` library (add to `requirements.txt`).
- Config keys under a new `netbox:` block in `config.yaml`:
  - `enabled: true/false`
  - `url:` (e.g. `https://netbox.example.com`)
  - `token:` (Netbox API token)
  - `default_site:` (Netbox site slug)
  - `default_role:` (VM role slug, e.g. `server`)
  - `default_cluster:` (Netbox cluster name for VMs)
- Same opt-in pattern as `dns.enabled` and `ansible_inventory` — skip silently if disabled.

---

## Lab-Documenter Sync

After deploy or decommission, trigger a lab-documenter update so the wiki and `services.json`
stay current without manual intervention.

### Implementation notes

- After a successful deploy, append a minimal entry to `services.json` (hostname, IP, type,
  deployed_at) on the lab-documenter host, then re-run the documenter to regenerate wiki pages.
- On decomm, remove the entry and re-run.
- Implementation options (in order of preference):
  1. SSH to the dev/documenter server and run the documenter script directly.
  2. Invoke a webhook or CI job that pulls and re-runs the documenter.
- Config keys under a new `lab_documenter:` block in `config.yaml`:
  - `enabled: true/false`
  - `server:` (host running lab-documenter)
  - `ssh_user:`
  - `script_path:` (full path to the lab-documenter entry point)
  - `services_json_path:` (full path to `services.json` on the remote host)

---

## HashiCorp Vault / Vaultwarden Integration

On deploy, write the generated root/admin password to a Vault secret so it is never stored in
plaintext in the deployment JSON. On decommission, delete the secret.

### Deploy: Store credentials in Vault

- Write to a KV-v2 path such as `secret/labinator/<hostname>` with fields `root_password` and
  `admin_password`.
- Optionally redact the password from the deployment JSON after writing (replace with
  `"password": "vault:<path>"`).

### Decommission: Remove credentials from Vault

- Delete (and optionally destroy all versions of) the KV-v2 secret at the host's path.

### Implementation notes

- Use the `hvac` Python library (add to `requirements.txt`).
- Config keys under a new `vault:` block in `config.yaml`:
  - `enabled: true/false`
  - `url:` (e.g. `https://vault.example.com`)
  - `token:` (Vault token — consider also supporting AppRole auth)
  - `kv_mount:` (KV-v2 mount point, e.g. `secret`)
  - `path_prefix:` (e.g. `labinator` — secrets stored at `<mount>/<prefix>/<hostname>`)
- Same opt-in pattern as other integrations — skip silently if disabled.

---

## Uptime Kuma Integration

After a container or VM is deployed, automatically add a monitor in Uptime Kuma. Remove it on
decommission.

### Deploy: Create monitor

- Add a TCP ping or HTTP monitor for the new host's IP / FQDN.
- Tag the monitor with the hostname for easy filtering.

### Decommission: Remove monitor

- Look up the monitor by hostname/tag and delete it.

### Implementation notes

- Use the `uptime-kuma-api` Python library (add to `requirements.txt`), which wraps the
  Uptime Kuma Socket.IO API.
- Config keys under a new `uptime_kuma:` block in `config.yaml`:
  - `enabled: true/false`
  - `url:` (e.g. `https://uptime.example.com`)
  - `username:`
  - `password:`
  - `default_monitor_type:` (e.g. `ping` or `tcp` — `ping` is a good default for all hosts)
  - `notification_id:` (optional — attach an existing Uptime Kuma notification channel)
- Store the monitor ID in the deployment JSON for reliable lookup during decommission.

---

## phpIPAM Integration

Instead of manually specifying an IP address at deploy time, claim the next available IP from a
phpIPAM subnet automatically.

### Deploy: Reserve next available IP

- Query a configured subnet for the next free address via the phpIPAM REST API.
- Reserve it with the hostname and description before the Proxmox container is created.
- Use the returned IP as the container/VM's static IP — no manual entry needed.

### Decommission: Release IP

- Mark the IP as free (or delete the address record) in phpIPAM using the stored address ID.

### Implementation notes

- phpIPAM REST API requires an app token (created under Administration > API).
- Config keys under a new `phpipam:` block in `config.yaml`:
  - `enabled: true/false`
  - `url:` (e.g. `https://ipam.example.com`)
  - `app_id:` (phpIPAM API app ID)
  - `token:` (phpIPAM API token)
  - `default_subnet_id:` (subnet to allocate from by default — can be overridden per deployment)
- When enabled, the IP prompt in the deploy wizard is replaced with subnet selection + automatic
  allocation. The resolved IP is shown to the user for confirmation before proceeding.
- Store the phpIPAM address ID in the deployment JSON for use during decommission.

---

## Zabbix Integration

Zabbix is a full-stack infrastructure monitoring platform with a well-documented REST API.
On deploy, register the new host in Zabbix and apply a monitoring template. On decommission,
remove it — the same pattern as the Cacti integration.

### Deploy: Register host in Zabbix

- Create a host record with the hostname, IP, and assigned host group.
- Link one or more Zabbix templates (e.g. `Linux by Zabbix agent` or an SNMP template).
- Optionally install and configure the Zabbix agent via the post-deploy Ansible playbook.

### Decommission: Remove host from Zabbix

- Look up the host by name or stored host ID and delete it.

### Implementation notes

- Use the Zabbix REST API (`POST /api_jsonrpc.php`) — no third-party library required, plain
  `requests` calls are sufficient.
- Config keys under a new `zabbix:` block in `config.yaml`:
  - `enabled: true/false`
  - `url:` (e.g. `https://zabbix.example.com`)
  - `user:`
  - `password:`
  - `default_group:` (Zabbix host group name, e.g. `Linux servers`)
  - `default_template:` (Zabbix template name to apply, e.g. `Linux by SNMP`)
- Store the Zabbix host ID in the deployment JSON for reliable lookup during decommission.
- If Cacti is also enabled, both integrations run independently — they are not mutually exclusive.

---

## REST API (FastAPI)

Wrap labinator's core logic in a REST API server so deployments and decommissions can be
triggered programmatically — from a web UI, CI/CD pipeline, or any HTTP client — without
needing shell access to the controller machine.

### Usage

```bash
uvicorn api:app --host 0.0.0.0 --port 8080
```

### Endpoints

```
POST   /api/vms/deploy           deploy a VM (body: deployment JSON)
POST   /api/lxc/deploy           deploy an LXC container (body: deployment JSON)
DELETE /api/vms/{hostname}       decommission a VM
DELETE /api/lxc/{hostname}       decommission an LXC container
GET    /api/deployments          list all deployment files (VMs and LXCs)
GET    /api/deployments/{hostname}  return a single deployment JSON
GET    /api/status               cluster overview (nodes, running VMs/LXCs, resource usage)
```

### Implementation notes

- **FastAPI** is the natural choice — same Python ecosystem as the existing scripts, so core
  functions can be imported directly rather than shelling out to subprocesses.
- Auto-generates interactive Swagger UI docs at `/docs` — no extra work required.
- Long-running operations (deploy, decomm) should run as background tasks
  (`fastapi.BackgroundTasks` or a task queue like Celery) and return a job ID immediately.
  A `GET /api/jobs/{id}` endpoint returns status and logs for the running operation.
- Add basic API key authentication (header: `X-API-Key`) configured in `config.yaml`.
- Opens the door to a web UI frontend (HTMX or React) that calls this API instead of
  running scripts from the terminal.

### Dependencies to add

- `fastapi`
- `uvicorn`

---

## Natural Language Deploy

Describe a VM in plain English and let Claude generate the deployment JSON, confirm it
with you, and kick off the deploy — no wizard prompts required.

### Usage

```bash
python3 deploy.py "spin up a 4-core Rocky Linux box with 8GB RAM for load testing"
python3 deploy.py "give me a small Ubuntu VM on proxmox02 for the wiki"
```

### How it works

1. The description is sent to the Claude API with the labinator deployment JSON schema
   as context.
2. Claude returns a populated deployment JSON (hostname suggestion, OS image, CPU, RAM,
   disk, VLAN, node preference if mentioned).
3. The generated JSON is displayed in a confirmation summary — same as the normal wizard.
4. User confirms or edits, then the deploy runs in silent mode.

### Implementation notes

- Uses the Claude API (`anthropic` Python SDK).
- The system prompt includes the full deployment JSON schema, available cloud images
  from `cloud-images.yaml`, and current cluster state (nodes, free resources) so Claude
  can make sensible choices.
- Falls back to interactive wizard if the description is too ambiguous to produce a
  confident JSON.
- Config keys under a new `ai:` block in `config.yaml`:
  - `enabled: true/false`
  - `api_key:` (Anthropic API key)
  - `model:` (e.g. `claude-opus-4-6`)

### Dependencies to add

- `anthropic`

---

## status.py — Live Cluster Dashboard

Cross-reference all local deployment JSONs against live Proxmox state and display a unified
rich table showing every managed host at a glance.

### Usage

```bash
./status.py                    # all hosts
./status.py --node proxmox02   # filter by node
./status.py --type vm          # filter by type (vm or lxc)
./status.py --tag Docker        # filter by Proxmox tag
```

### What it shows

| Column | Source |
|---|---|
| Hostname | deployment JSON |
| Type | VM / LXC |
| VMID | deployment JSON |
| Node | deployment JSON |
| IP | deployment JSON (`assigned_ip` → `ip_address`) |
| Status | live Proxmox API (running / stopped / unknown) |
| CPU % | live Proxmox API |
| RAM used | live Proxmox API |
| Deployed | `deployed_at` from deployment JSON |
| Tags | live Proxmox API |

### Anomaly detection

- **Orphaned VM/LXC** — running in Proxmox but no deployment JSON found. Printed in yellow
  as a warning row. Suggests a host deployed outside of labinator, or a deployment file
  that was deleted.
- **Ghost file** — deployment JSON exists but the VMID is not present in Proxmox. Printed
  in red. Suggests the VM/LXC was manually destroyed without running decomm.
- **Node mismatch** — deployment JSON says `proxmox02` but Proxmox reports it on `proxmox03`.
  Suggests a live migration happened outside of labinator.

### Implementation notes

- Reads all JSONs from `deployments/lxc/` and `deployments/vms/`.
- Queries Proxmox API for all nodes' VMs and LXCs in parallel using the cluster resources
  endpoint (`GET /cluster/resources?type=vm`).
- Matches by VMID. No VMID match = orphan or ghost, depending on which side is missing.
- Uses `rich` table with color-coded Status column (green = running, red = stopped, yellow = warning).
- Does not require SSH — API-only.

---

## console.py — Out-of-Band Console Access

SSH to the right Proxmox node and open a direct console session on a VM or LXC container
without needing the Proxmox web UI or knowing which node it's on.

This is the "I broke SSH and need to get in anyway" tool. Also useful for initial
post-deploy inspection or any time you want shell access without going through the VM's
network stack.

### Usage

```bash
./console.py myserver                    # lookup by hostname in deployment JSONs
./console.py myserver.lees-family.io    # FQDN — domain suffix is stripped automatically
./console.py --vmid 142                  # direct access by VMID (no deployment JSON needed)
./console.py --vmid 142 --node proxmox02 # skip the API lookup entirely
./console.py                             # interactive list — same picker as decomm scripts
```

### How it works

- Looks up the deployment JSON by hostname (stripping domain suffix if an FQDN is given).
- Reads `node` and `vmid` from the JSON.
- SSHs to `root@<node>.<node_domain>` and runs:
  - LXC: `pct enter <vmid>`
  - VM: `qm terminal <vmid>` (requires serial console — already configured by `deploy_vm.py`)
- The SSH session is interactive — your terminal is handed directly to the console.
- On exit (Ctrl-D or `exit`), you are returned to your local shell.

### Implementation notes

- Uses `os.execvp("ssh", [...])` to replace the Python process with SSH — no subprocess
  wrapper, so terminal resizing and Ctrl-C work naturally.
- `--vmid` without `--node` queries the Proxmox API to find which node the VMID lives on.
- Falls back to the interactive picker (same style as `decomm_lxc.py`) if no argument is given.
- Print a one-line banner before handing off: `Connecting to console of myserver (LXC 142) on proxmox02...`
- Add a note that `qm terminal` requires the VM to have `serial0=socket` configured — which
  `deploy_vm.py` sets automatically, but manually-created VMs may not have it.

---

## Post-Deploy Hook Scripts — Plugins and Extensibility

After a successful deployment (or decommission), run user-defined scripts or Ansible
playbooks automatically. This is the plugin/extensibility mechanism that keeps labinator
from needing a built-in integration for every possible tool.

### Usage

```yaml
# config.yaml — global hooks run for every deployment
hooks:
  post_deploy:
    - ./hooks/notify-slack.sh
    - ./hooks/register-in-wiki.sh
  post_decomm:
    - ./hooks/remove-from-wiki.sh
```

```json
// deployment JSON — per-host hooks (merged with global hooks)
{
  "hostname": "myserver",
  "post_deploy_hooks": ["./hooks/setup-monitoring.sh"],
  "post_decomm_hooks": []
}
```

### Hook interface

Each hook is called as a subprocess with a standard set of environment variables so it
has everything it needs without parsing config files:

```bash
LABINATOR_HOSTNAME=myserver
LABINATOR_FQDN=myserver.lees-family.io
LABINATOR_IP=10.20.20.150
LABINATOR_NODE=proxmox02
LABINATOR_VMID=142
LABINATOR_TYPE=lxc          # or "vm"
LABINATOR_ACTION=deploy     # or "decomm"
LABINATOR_DEPLOY_FILE=/home/dad/projects/HomeLab/labinator/deployments/lxc/myserver.json
```

### Behavior

- Hooks run after all built-in steps complete successfully.
- Each hook's stdout/stderr is captured and printed under a collapsible section.
- A non-zero exit code from a hook prints a warning but does NOT abort — hooks are
  best-effort by default. Add `hook_failure: abort` to `config.yaml` to make failures fatal.
- Hooks are executed in order, one at a time.
- Any executable file is supported: shell scripts, Python scripts, Ansible playbooks
  (via `ansible-playbook`), etc.

### Implementation notes

- Add `run_hooks(action, deploy_record, cfg)` to `modules/lib.py`.
- Merge global hooks from `config.yaml` with per-host hooks from the deployment JSON,
  global first.
- Log each hook invocation (name, exit code, elapsed time) to the deployment history log.
- Include a `hooks/` directory in the project with example scripts demonstrating the
  environment variable interface.
- This replaces the need for built-in Cacti, Netbox, Uptime Kuma, etc. integrations for
  users who prefer scripting their own.

---

## Deployment Rollback

If a deploy fails partway through — after the VM/LXC is created in Proxmox but before
all steps complete — offer to automatically roll back by running the full decomm sequence
on the newly created host.

**Also covers:** History log failure entries. Currently `write_history()` only logs
successful deploys/decomms. Failure logging requires wrapping `main()` in each script
with try/except, tracking which step failed, and writing a `"result": "failed"` entry
with `failed_step` and `failure_reason` fields. This should be implemented alongside
rollback since both require the same error-handling infrastructure.

Currently the script warns and exits, leaving a half-configured host behind that must be
cleaned up manually. Rollback makes the tool feel safe to use, especially in `--silent`
automation contexts.

### Behavior

- Rollback is only possible after Step 1 (container/VM creation) succeeds and a deployment
  JSON has been written.
- If any subsequent step fails (Ansible, DNS, inventory), offer:
  ```
  Step 5 failed. Options:
    [R]ollback — destroy the VM, remove DNS, remove from inventory
    [C]ontinue — leave the VM running and fix manually
    [A]bort    — leave the VM running, mark deployment as failed in history log
  ```
- In `--silent` mode: default to `Continue` (non-destructive) unless
  `rollback_on_failure: true` is set in `config.yaml`.
- Rollback uses the same decomm logic as `decomm_vm.py` / `decomm_lxc.py` — no duplicate
  code. The just-written deployment JSON is the input.
- After rollback, the deployment JSON is deleted automatically (equivalent to `--purge`).
- Log the rollback event to the deployment history log with the failure reason.

### Implementation notes

- Wrap the post-creation steps in a try/except that catches failures and triggers the
  rollback prompt.
- Pass a `rollback_json_path` to the error handler so it always knows what to clean up.
- The rollback itself calls `decomm_vm()` / `decomm_lxc()` from the shared library —
  same functions as the decomm scripts.
- Add `rollback_on_failure: false` to `config.yaml` as an opt-in for automated pipelines.

---

## LXC Template Auto-Download with Browse Interface

When deploying an LXC container, if the desired template is not already downloaded on the
target node, offer to browse available templates from the Proxmox template repository and
download one automatically — rather than requiring the user to go into the Proxmox web UI.

### Current behavior

The template picker shows only templates already downloaded on the selected node. If none
are present, or the one you want isn't there, you have to manually download it through
Proxmox UI → node → local storage → CT Templates → Templates → Download. Then re-run the
script.

### Proposed behavior

The template picker gains a second section — just like the VM cloud image browser:

```
Select OS template for proxmox02:

  Already downloaded:
    ubuntu-24.04-standard_24.04-2_amd64.tar.zst   (proxmox02 · local)

  ─── Download from Proxmox template repository ───
    Download: Ubuntu 24.04 LTS Standard
    Download: Ubuntu 22.04 LTS Standard
    Download: Debian 12 Standard
    Download: Debian 11 Standard
    Download: Alpine 3.19
    Download: Rocky Linux 9
    Download: Fedora 39
    ...
  ← Back
```

Selecting a "Download:" entry fetches the template to the node's local storage, then
proceeds with the deploy using that template.

### Implementation notes

- Proxmox exposes a template list at `GET /nodes/{node}/aplinfo` — returns all available
  templates from the configured repositories (pve-no-subscription, etc.) with name,
  version, description, and download URL.
- Download is triggered via `POST /nodes/{node}/storage/{storage}/download-url` or by
  calling `pveam download local <template>` via SSH on the node.
- Ubuntu templates should still be sorted first (same as current behavior).
- Show download size and a spinner during download — same pattern as VM cloud image downloads.
- Store the downloaded template's `volid` in the deployment JSON as usual.
- Add `lxc_template_storage` to `config.yaml` defaults to pre-select where templates are
  stored (default: `local`).

---

## App-Profile Deployments — Archive-Based Application Templates

A lightweight alternative to writing per-app Ansible roles. Instead of scripting an
application install from scratch, an admin configures the app once on a reference
container (manually, or via a community helper script), then archives the working config
files into a `.tar.gz`. Every future deployment of that app: install packages → extract
archive → reboot → running.

This approach sidesteps the two bad alternatives:
- **Community helper scripts** — run on the Proxmox host, create their own container,
  can't cleanly integrate with labinator's infra pipeline, and depend on an external URL
  that can change or disappear.
- **Full Ansible roles per app** — high maintenance burden; you own the install logic forever.

The archive approach lets the admin do the hard work once, then captures the result.

### How archives are built

The `.tar.gz` deployment archives are built using **porter**, which is functional and
producing archives. Porter generates a `manifest.yaml` inside each archive documenting
the source OS, local users, active systemd services, installed packages, and per-file
SHA-256 checksums. Labinator reads this manifest to drive the post-extract workflow.

**Manifest spec:** `docs/specs/porter-snapshot-manifest.md` — full schema, archive layout,
labinator integration steps, and caveats. Read this before implementing.

### Archive storage

Archives live in `labinator/app-templates/`:

```
labinator/
└── app-templates/
    ├── pihole.tar.gz
    ├── pihole-manifest.yaml
    ├── nginx-proxy-manager.tar.gz
    ├── nginx-proxy-manager-manifest.yaml
    └── vaultwarden.tar.gz
```

### App catalog (`app-catalog.yaml`)

```yaml
apps:
  - name: Pi-hole
    description: Network-wide ad blocking
    recommended:
      cpus: 1
      memory_gb: 1
      disk_gb: 8
    profile: monitoring-node       # labinator package profile to apply first
    extra_packages:
      - pihole
    lxc_features: []
    archive: app-templates/pihole.tar.gz
    manifest: app-templates/pihole-manifest.yaml

  - name: Nginx Proxy Manager
    description: Reverse proxy with GUI and automatic SSL
    recommended:
      cpus: 2
      memory_gb: 2
      disk_gb: 20
    profile: web-server
    extra_packages: []
    lxc_features: [nesting=1]
    archive: app-templates/nginx-proxy-manager.tar.gz
    manifest: app-templates/nginx-proxy-manager-manifest.yaml
```

### Deploy-time behavior

When an app profile is selected during `deploy_lxc.py`:

1. Pre-fill CPU/RAM/disk from `recommended` values (user can override)
2. Apply the referenced labinator package profile (installs baseline + profile packages)
3. Install any `extra_packages`
4. Apply `lxc_features` from the catalog entry
5. Read the manifest — create any custom users/groups listed
6. Extract the archive to `/` on the container: `tar xzf pihole.tar.gz -C /`
7. Run any `post_extract_commands` from the manifest
8. Reboot the container
9. Continue with DNS, inventory, health check as normal

### Permissions and ownership

`tar` preserves chmod bits and numeric uid/gid. Since labinator installs packages before
extracting (step 2 above), system users created by packages already exist with the correct
uid/gid by the time the archive lands. Custom users are handled via the porter-generated
manifest (step 5). For edge cases, `post_extract_commands` in the manifest can run
targeted `chown` fixes.

### Implementation notes

- Add `--app` flag to `deploy_lxc.py`: `./deploy_lxc.py --app pihole`
- App selection also available interactively — a new prompt after profile selection:
  `Select app template (or None for standard profile-only deploy)`
- Archive extraction runs via `pct exec <vmid> -- tar xzf ...` after the bootstrap step,
  before Ansible.
- Add `app_name` and `archive` fields to the deployment JSON for reference.
- `--dry-run` shows the app name and archive path in the summary table.
- This is LXC-focused initially — VM equivalent is possible but cloud-init handles most
  VM app config differently (user-data scripts, etc.).

---

