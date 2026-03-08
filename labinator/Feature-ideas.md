# Feature Ideas

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

## Shared Library Module

`deploy_lxc.py`, `deploy_vm.py`, `decomm_lxc.py`, and `decomm_vm.py` duplicate significant code
(config loading, Proxmox connection, `wait_for_task`, DNS helpers, Ansible inventory steps).
Extract into a `lib.py` (or `labinator/lib.py`) shared module.

### Implementation notes

- Functions to extract: `load_config()`, `connect_proxmox()`, `wait_for_task()`, DNS add/remove
  wrappers, Ansible inventory add/remove wrappers.
- All four scripts import from `lib` — no behaviour changes, just deduplication.
- Makes adding new integrations (Netbox, Cacti, etc.) a single-place change.

---

## --validate Flag

Add a `--validate` flag to `deploy_lxc.py` and `deploy_vm.py` that checks a deployment JSON
against a schema and verifies config.yaml is well-formed, without connecting to Proxmox or
making any changes.

### Implementation notes

- Use `jsonschema` (add to `requirements.txt`) to validate the deployment file.
- Check required keys, value types, and plausible ranges (e.g. memory > 0, valid IP format).
- Exit 0 on success, non-zero with a clear error message on failure.
- Useful in CI or before batch deployments to catch typos early.

---

## Post-Deploy Status / Health Check

After a container or VM is provisioned and Ansible runs, verify the host is actually healthy
before reporting success.

### Implementation notes

- Ping the IP address (ICMP or TCP port 22) with a configurable timeout/retry count.
- Attempt an SSH connection and run a trivial command (e.g. `hostname`).
- If the check fails after N retries, print a warning but do not roll back (deployment file is
  already written — operator can investigate or decomm manually).
- Add `health_check:` block to `config.yaml`:
  - `enabled: true/false`
  - `timeout_seconds: 60`
  - `retries: 5`

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
- The `--validate` flag (see above) pairs naturally with batch — validate all files before
  starting any deploys.

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

## Auto-Expire / VM TTL

Assign a time-to-live to a deployment so test VMs don't accumulate and quietly consume
resources indefinitely.

### Usage

```bash
python3 deploy_vm.py --deploy-file deployments/vms/test-thing.json --ttl 7d
python3 expire.py --check      # scan for expired or expiring-soon deployments
python3 expire.py --reap       # decomm all expired VMs/LXCs automatically
```

### Implementation notes

- Store `expires_at` (ISO 8601 timestamp) in the deployment JSON at deploy time when
  `--ttl` is specified. TTL format: `7d`, `24h`, `2w`, etc.
- `expire.py --check` reads all deployment JSONs, compares `expires_at` to now, and
  prints a table of expired and expiring-soon (within 48h) hosts.
- `expire.py --reap` calls the normal decomm logic for each expired host — same DNS,
  inventory, and Proxmox cleanup as a manual decomm.
- Optional: send a warning notification (email, Slack, ntfy.sh) N hours before expiry.
- Optional: `--renew` flag to extend the TTL on an existing deployment without redeploying.
- Pairs naturally with a cron job or systemd timer to run `expire.py --reap` nightly.
- The REST API (see above) can expose a `POST /api/deployments/{hostname}/renew` endpoint
  for TTL extension without shell access.

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

## Package Profiles

Define named sets of packages in `config.yaml` that represent a server role. Select a
profile at deploy time to install a consistent, reusable toolset on top of the standard
baseline — without editing vars files or hardcoding packages into deployment JSONs.

### Usage

```yaml
# config.yaml
package_profiles:
  web-server:
    - nginx
    - certbot
    - python3-certbot-nginx
  database:
    - postgresql
    - postgresql-client
  monitoring:
    - prometheus-node-exporter
    - grafana
  docker-host:
    - docker-ce
    - docker-ce-cli
    - containerd.io
    - docker-compose-plugin
```

```json
// deployment JSON
{
  "hostname": "my-web-server",
  "package_profile": "web-server"
}
```

### Implementation notes

- The wizard prompts for an optional profile after the hostname: `Package profile (optional):`
  with a list of defined profiles plus `[none]`. Defaults to none.
- The selected profile name is stored in the deployment JSON as `package_profile`.
- The Ansible playbook reads `package_profile`, looks it up in `config.yaml` vars passed
  as extra variables, and appends the profile's packages to the standard install list.
- Profiles are OS-agnostic by name — the package names within each profile should match
  the target OS (or use per-family sub-keys if cross-OS profiles are needed later).
- Multiple profiles could be supported in a future iteration (`package_profiles: [web-server, monitoring]`).

---

## Extra Packages Per Deployment

Allow one-off packages to be specified directly in the deployment JSON for packages that
don't belong in a profile — without touching vars files or config.yaml.

### Usage

```json
{
  "hostname": "my-app-server",
  "package_profile": "web-server",
  "extra_packages": ["ffmpeg", "imagemagick", "wkhtmltopdf"]
}
```

### Implementation notes

- The wizard prompts: `Extra packages (optional, comma-separated):` after the profile prompt.
- Stored in the deployment JSON as `extra_packages` (list of strings).
- The Ansible playbook appends `extra_packages` to the install list after the profile
  packages — profile first, then extras.
- Works with or without a profile — either, both, or neither can be specified.
- Pairs with the `--validate` flag to catch obviously invalid package names early.
- The natural language deploy feature (see above) can populate both `package_profile` and
  `extra_packages` automatically based on the description.
