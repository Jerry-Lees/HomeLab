# Feature Ideas

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

## Proxmox Cluster Import / Scan (`import.py`)

**Scope: the entire cluster, no tag filter.** This is for adopting an existing Proxmox
environment into labinator — every VM and LXC on every node, regardless of tags or
whether labinator originally deployed them.

The goal is a deployment JSON file for every resource so the cluster can be documented,
migrated, or rebuilt from scratch. Fields that can't be determined automatically (e.g.
passwords, cloud image source) are left as clearly-marked placeholders — the operator
fills them in before using the file for a redeploy.

**This is not `cleanup_tagged.py --plan`.** `import.py` ignores tags entirely and
produces deployment JSON files (the same format `deploy_lxc.py` / `deploy_vm.py` write).
`--plan` is scoped to a tag and produces a cleanup action list.

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

**Live test target available:** `kuma.lees-family.io` (deployed via labinator, running Uptime Kuma 2.2.1).

### Deploy: Create monitor

- Add a ping or TCP monitor for the new host's IP / FQDN.
- Tag the monitor with the hostname for easy filtering in the Kuma UI.

### Decommission: Remove monitor

- Look up the monitor by the stored monitor ID and delete it.

### API approach — check REST API availability at implementation time

> **Note (2026-03-22):** Uptime Kuma 2.2.1 (current latest) does NOT have a monitor
> management REST API. The `/api/v1/monitors` endpoint does not exist — all routes
> return the Vue SPA. The API keys generated in Settings → API Keys authenticate
> Socket.IO connections only, not a REST API. This was confirmed by inspecting
> `server/routers/api-router.js` on the live kuma instance.
>
> **Before implementing:** check the latest Uptime Kuma release to see if a monitor
> management REST API has been added. If yes, use `requests` with
> `Authorization: Bearer <api_key>`. If no, use the Socket.IO approach below.

#### Option A — REST API (use this if available in current release)

```
Authorization: Bearer <api_key>

GET    /api/v1/monitors              list all monitors
POST   /api/v1/monitors              create a monitor
DELETE /api/v1/monitors/{id}         delete a monitor
PATCH  /api/v1/monitors/{id}         update a monitor
```

Minimal create payload:
```json
{
  "type": "ping",
  "name": "myserver",
  "hostname": "10.220.220.150",
  "interval": 60
}
```

No new Python dependencies — `requests` is already installed.

#### Option B — Socket.IO API via `uptime-kuma-api` library (fallback)

The `uptime-kuma-api` Python library wraps the Socket.IO interface that the Kuma web UI
uses internally. As of 2.2.1 this is the only way to programmatically manage monitors.
API key auth is supported by newer versions of the library.

```python
from uptime_kuma_api import UptimeKumaApi, MonitorType

with UptimeKumaApi("http://kuma.lees-family.io:3001") as api:
    api.login_by_token("uk1_...")   # API key auth
    api.add_monitor(
        type=MonitorType.PING,
        name="myserver",
        hostname="10.220.220.150",
        interval=60,
    )
```

Add `uptime-kuma-api` to `requirements.txt` if using this approach.

### Implementation notes

- Config keys under a new `uptime_kuma:` block in `config.yaml`:
  - `enabled: true/false`
  - `url:` (e.g. `http://kuma.lees-family.io:3001`)
  - `api_key:` (generated in Kuma Settings → API Keys — stored in `labinator/kuma` file)
  - `default_monitor_type:` (`ping` or `port` — `ping` is a good default for all hosts)
  - `default_port:` (port to check when type is `port`, default: `22`)
  - `notification_id:` (optional — attach an existing Kuma notification channel ID)
- Store the returned monitor `id` in the deployment JSON as `uptime_kuma_monitor_id` for
  reliable lookup during decommission — do not rely on name-based lookup.
- If `uptime_kuma.enabled: false` or the block is absent, skip silently (same pattern as DNS and inventory).
- On decomm: if `uptime_kuma_monitor_id` is missing from the JSON, fall back to searching
  monitors by name. Log a warning if not found (monitor may have been deleted manually).

### Default monitors to create per host

- **Ping** — every labinator-managed host (universal)
- **TCP port 22** — every host (labinator bootstraps SSH on all LXC and VM deployments)
- **Profile-aware extras** (based on `package_profile` in the deployment JSON):
  - `web-server` profile → HTTP port 80, HTTPS port 443
  - `database` profile → TCP port 3306 (MySQL/MariaDB) or 5432 (Postgres)
  - `dns` profile → TCP port 53

### Infrastructure monitors (one-time manual setup, outside labinator scope)

These are not per-host but should exist in Kuma for full coverage:
- Proxmox web UI — HTTPS port 8006 on each node
- BIND DNS server — TCP port 53
- The Kuma host itself (self-monitor or external ping)
- Default gateway / router

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

## Provider Plugin Architecture

The current provider system is aspirational — `config.yaml` has a `provider:` key under
`dns` and `ansible_inventory`, but the code never dispatches on it. `remove_dns()`,
`run_ansible_add_dns()`, `dns_precheck()`, `remove_from_inventory()`, and
`run_ansible_inventory_update()` in `modules/lib.py` all call the BIND / flat_file
Ansible playbooks unconditionally, regardless of what `provider:` says.

The goal is a true drop-in provider model: to add a new DNS or inventory provider, you
drop a single Python file into a `modules/providers/` directory. No changes to
`modules/lib.py` or any deploy/decomm script are needed.

### Design

Each provider type has an abstract base class defining the interface:

```python
# modules/providers/base.py

class DnsProvider:
    def precheck(self, cfg, hostname, ip, silent) -> str: ...
    def register(self, cfg, hostname, ip) -> None: ...
    def remove(self, cfg, deploy) -> None: ...

class InventoryProvider:
    def register(self, cfg, hostname, ip, password) -> None: ...
    def remove(self, cfg, deploy) -> None: ...
```

Concrete providers implement the interface and are placed in `modules/providers/`:

```
modules/providers/
├── base.py              ← abstract base classes
├── dns_bind.py          ← existing BIND logic, refactored into class
├── dns_powerdns.py      ← new provider — drop in, no other changes needed
├── dns_technitium.py    ← new provider — same
├── inventory_flat_file.py
├── inventory_awx.py
└── inventory_semaphore.py
```

A provider registry in `modules/providers/__init__.py` maps provider names to classes:

```python
DNS_PROVIDERS = {
    "bind":       "dns_bind.DnsBindProvider",
    "powerdns":   "dns_powerdns.PowerDnsProvider",
    "technitium": "dns_technitium.TechnitiumProvider",
}

INVENTORY_PROVIDERS = {
    "flat_file":  "inventory_flat_file.FlatFileProvider",
    "awx":        "inventory_awx.AwxProvider",
    "semaphore":  "inventory_semaphore.SemaphoreProvider",
}
```

`modules/lib.py` loads the provider at call time using the `provider:` key from
`config.yaml`, then delegates:

```python
def remove_dns(cfg, deploy):
    provider = load_dns_provider(cfg)   # reads cfg["dns"]["provider"], imports class
    provider.remove(cfg, deploy)
```

`load_dns_provider()` and `load_inventory_provider()` are the only additions needed in
`lib.py` — all other functions stay as thin wrappers that delegate to the loaded provider.

### Adding a new provider (end state)

1. Create `modules/providers/dns_myprovider.py` implementing `DnsProvider`
2. Add `"myprovider": "dns_myprovider.MyProvider"` to the registry in `__init__.py`
3. Set `dns.provider: myprovider` in `config.yaml`

No changes to `lib.py`, no changes to any deploy or decomm script. The provider is
discovered and loaded automatically.

### Implementation notes

- Refactor existing BIND and flat_file logic into `dns_bind.py` and
  `inventory_flat_file.py` first — `lib.py` keeps the same public function signatures,
  just delegates internally. This is a pure refactor with no behavior change.
- Use `importlib.import_module()` for lazy loading — providers are only imported when
  actually needed. This keeps startup fast and means an uninstalled optional dependency
  (e.g. `python-dotenv` for a future provider) doesn't break the whole tool.
- The `preflight` checks for DNS and inventory (`_pf_dns_reachable`, `_pf_dns_ssh_auth`,
  etc.) should also be delegated to the provider — each provider knows what connectivity
  it needs to check.
- Unknown provider name → clear error at startup: `Unknown DNS provider: 'myprovider'.
  Available: bind, powerdns, technitium`.

---

## Draft Deployment File Builder — **Implemented**

> **Status: Implemented** — `draft-deployment.py`
> See [docs/draft-deployment.md](docs/draft-deployment.md) for full documentation.

`draft-deployment.py` runs the full LXC or VM wizard and saves the resulting deployment JSON without creating anything in Proxmox. The output file can then be used with `deploy_lxc.py --deploy-file`, `deploy_vm.py --deploy-file`, or `deploy.py --batch`.

Supports `--lxc` / `--vm` flags, `--deploy-file` to edit an existing draft, `--ttl` for expiry planning, and full back-navigation through every wizard step.

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

## Auto-enable `nesting=1` for Ubuntu 24.04 LXC Containers

**Implemented** — Option 1 (auto-detect from template name).

After the wizard completes, `deploy_lxc.py` checks the selected template name against
`lxc_auto_nesting_templates` in `config.yaml` (default: `["ubuntu-24.04"]`). If the template
matches and `nesting=1` isn't already enabled, it's silently added before container creation
and a dim note is printed.

---

## LXC Template Download from Proxmox Repository

**Implemented** — "─── Download from Proxmox repo..." option appears at the bottom of the
template selector. Fetches the full Proxmox community catalog via `GET /nodes/{node}/aplinfo`,
filters out already-downloaded templates, lets the user pick one, then triggers the download
via `POST /nodes/{node}/aplinfo`, polls the task until complete, and returns to the template
list with the new template pre-selected.

Storage is chosen automatically if only one `vztmpl`-capable pool exists; otherwise a quick
prompt appears. `--silent` mode is unaffected.

---


## Batch Deploy Node-Aware Staging

When deploying in parallel, multiple containers/VMs targeted at the same Proxmox node run
concurrently, which can cause resource contention (CPU, disk I/O, network) during the busiest
parts of deployment (template extraction, apt upgrade, Ansible).

### Proposed behavior

Add a `--node-serial` flag (or make it the default) that groups jobs by target node and ensures
no two jobs deploying to the same node run simultaneously. Jobs on different nodes still run in
parallel.

### Implementation notes

- Before dispatching jobs, group deployment files by their `node` field.
- Use a per-node semaphore (`threading.Semaphore(1)`) so only one job per node runs at a time.
- Jobs for different nodes are still submitted to the ThreadPoolExecutor concurrently — the
  semaphore gates the actual work, not the submission.
- The status board already shows the target node per host — staggered starts would be visible
  there naturally.
- `--parallel N` would still control total concurrency; node-serial would add an additional
  per-node constraint on top.

---

## ssh.py — Post-Deploy SSH Jump and ~/.ssh/config Manager

Two related capabilities in one script:

1. **Jump** — after any successful deploy, offer to drop directly into an SSH session on the new host. The IP is already known at the end of the deploy flow, so no lookup is needed.
2. **Standalone** — `python3 ssh.py <hostname>` looks up the deployment JSON by hostname, resolves the IP (`assigned_ip` → `ip_address`), and execs SSH. Works like a smarter alias for any labinator-managed host.
3. **`~/.ssh/config` updater** — optionally write (or update) a `Host` entry in `~/.ssh/config` so you can `ssh myserver` directly from anywhere.

### Usage

```bash
# Standalone — look up hostname in deployment JSONs
python3 ssh.py myserver
python3 ssh.py myserver --user dad        # connect as a specific user (default: root)
python3 ssh.py myserver --update-config   # also write/update entry in ~/.ssh/config
python3 ssh.py                            # interactive picker — same style as decomm scripts
```

### Post-deploy integration

At the very end of `deploy_lxc.py` and `deploy_vm.py`, after the `✓ All Done` panel and only when NOT `--silent`:

```python
if questionary.confirm(
    f"Connect to {hostname} now? (ssh root@{container_ip})", default=False
).ask():
    os.execvp("ssh", ["ssh", f"root@{container_ip}"])
```

Default is **No** — in pipelines or when the deploy finishes late, the user may not want a session. `os.execvp` replaces the Python process entirely so the terminal behaves normally (resize, Ctrl-C, colors all work).

### Standalone lookup logic

1. Search `deployments/lxc/<hostname>.json` then `deployments/vms/<hostname>.json`. Accept FQDN — strip domain suffix.
2. If not found by filename, scan all JSONs and match on `hostname` field.
3. If multiple matches, show a picker.
4. Resolve IP: `assigned_ip` → `ip_address`. Exit with error if `"dhcp"` and no `assigned_ip`.

### ~/.ssh/config management

Write or update a stanza in `~/.ssh/config`:

```
Host myserver
    HostName 10.220.220.180
    User root
    IdentityFile ~/.ssh/id_rsa
```

- Key path from `cfg["proxmox"]["ssh_key"]`.
- If the stanza already exists, update `HostName` in place (the IP may have changed after a redeploy).
- Parse the config file line-by-line — do not use a third-party SSH config parser.
- `--update-config` flag enables this. Add `ssh.update_config: true` to `config.yaml` to make it the default.

### Implementation notes

- `list_deployment_files("lxc")` and `list_deployment_files("vms")` from `modules/io.py` are the data source.
- No Proxmox API connection needed — pure file lookup + exec.
- Interactive picker (no argument given): show a rich table of all known hosts with IP and node, then exec SSH on selection.

---

## health.py — Batch Host Reachability Check

Reads every deployment JSON in `deployments/lxc/` and `deployments/vms/`, probes each host's SSH port (22 by default), and prints a color-coded table. Cron-friendly: exits 0 if all hosts are reachable, 1 if any are down.

### Usage

```bash
python3 health.py                   # check all managed hosts
python3 health.py --timeout 3       # TCP connect timeout per host in seconds (default: 3)
python3 health.py --port 22         # port to probe (default: 22)
python3 health.py --type lxc        # filter to LXC only (or --type vm)
python3 health.py --node proxmox01  # filter to hosts deployed on a specific node
python3 health.py --json            # machine-readable JSON output
python3 health.py --down-only       # only print hosts that are down or unreachable
```

### Output table

```
┏━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓
┃ Hostname    ┃ Type ┃ IP             ┃ Node      ┃ Status  ┃
┡━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩
│ kuma        │ lxc  │ 10.220.220.180 │ proxmox01 │ ✓ up    │
│ myserver    │ vm   │ 10.220.220.150 │ proxmox02 │ ✗ down  │
│ dhcp-host   │ lxc  │ —              │ proxmox01 │ ? no IP │
└─────────────┴──────┴────────────────┴───────────┴─────────┘
2 up  1 down  1 skipped (no IP)
```

### Implementation notes

- IP resolution: `assigned_ip` → `ip_address`. If resolved value is `"dhcp"` and no `assigned_ip` exists, skip with `? no IP` — cannot probe.
- Probe method: `socket.create_connection((ip, port), timeout=timeout)` — pure TCP connect, no SSH handshake. Fast and zero extra dependencies.
- Run probes in parallel with `concurrent.futures.ThreadPoolExecutor(max_workers=20)` — large inventories return quickly.
- Status colors: green `✓ up`, red `✗ down`, yellow `? no IP` / `? skipped`.
- Exit code: `sys.exit(1)` if any host is `down`. Skipped hosts (no IP) do not count as failures.
- `--json` output:
  ```json
  [{"hostname": "kuma", "type": "lxc", "ip": "10.220.220.180", "node": "proxmox01", "status": "up"}, ...]
  ```
- No Proxmox API connection needed — pure local file read + TCP probe.
- Data source: `list_deployment_files("lxc")` and `list_deployment_files("vms")` from `modules/io.py`.

---

## rerun-ansible.py — Re-run Post-Deploy Ansible Playbook

Re-runs the labinator Ansible post-deploy playbook against an already-running host without touching Proxmox at all. Use it to push updated SNMP config, install additional packages, rotate NTP servers, or apply any other change the playbook manages.

### Usage

```bash
python3 rerun-ansible.py --deploy-file deployments/lxc/myserver.json
python3 rerun-ansible.py --deploy-file deployments/vms/myvm.json
python3 rerun-ansible.py --deploy-file deployments/lxc/myserver.json --tags snmp,ntp
python3 rerun-ansible.py --deploy-file deployments/lxc/myserver.json --extra-vars "extra_packages=[vim,htop]"
python3 rerun-ansible.py --deploy-file deployments/lxc/myserver.json --check
```

### How it works

1. Load the deployment JSON.
2. Determine type: `deploy.get("type") == "vm"` → use `ansible/post-deploy-vm.yml`; otherwise → `ansible/post-deploy.yml`.
3. Resolve IP: `assigned_ip` → `ip_address`. Exit with error if no usable IP.
4. Call `run_ansible_post_deploy(ip, password, hostname, cfg, kind, ...)` from `modules/ansible.py` — the exact same function called by the deploy scripts. No new Ansible logic needed.
5. Print output in the same format as a normal deploy.

### CLI options

| Option | Description |
|---|---|
| `--deploy-file FILE` | Deployment JSON to target. Required. |
| `--tags TAG,...` | Ansible `--tags` pass-through — run only specific roles/tasks. |
| `--skip-tags TAG,...` | Ansible `--skip-tags` pass-through. |
| `--extra-vars VARS` | Additional Ansible `-e` variables (override any playbook default). |
| `--check` | Ansible `--check` dry-run — show what would change without applying. |
| `--config FILE` | Alternate config file. |

### Implementation notes

- Password read from deployment JSON. If absent (e.g. after Vault integration), exit with a clear error.
- For VMs: SSH key auth (same as normal VM deploy flow). For LXC: password auth via sshpass.
- Pass `profile_packages` and `extra_packages` from the JSON to `run_ansible_post_deploy()` so the playbook state matches the original deployment.
- No Proxmox API connection needed at all.
- Optional `--all` flag: iterate every deployment JSON and re-run against each, with `--parallel N` for concurrent execution. Useful for fleet-wide config rollouts.
- `run_ansible_post_deploy()` already accepts a `tags` and `extra_vars` argument — add those parameters if not present, or build the ansible command with them appended.

---

## migrate.py — Live-Migrate LXC/VM Between Proxmox Nodes

Move a running or stopped LXC container or VM from one Proxmox node to another using the Proxmox migration API, then update the deployment JSON with the new node.

### Usage

```bash
python3 migrate.py --deploy-file deployments/lxc/myserver.json
python3 migrate.py --deploy-file deployments/vms/myvm.json --target proxmox02
python3 migrate.py --deploy-file deployments/lxc/myserver.json --target proxmox03 --online
```

### How it works

1. Load the deployment JSON. Read `vmid`, current `node`, and type.
2. Connect to Proxmox API. Call `get_nodes_with_load()` to list online nodes.
3. If `--target` not given, show an interactive node picker (same `prompt_node_selection()` as deploy wizards) excluding the current node.
4. Check migration feasibility:
   - **VMs**: online migration requires shared storage (NFS, Ceph). Query storage type from API. If local storage, warn that the VM will be stopped during migration.
   - **LXC**: `--online` requires shared storage for rootfs. If local storage, the container must be stopped; warn before proceeding.
5. Trigger migration via Proxmox API:
   - LXC: `POST /nodes/{current_node}/lxc/{vmid}/migrate` — body: `{"target": target_node, "online": 0|1}`
   - VM: `POST /nodes/{current_node}/qemu/{vmid}/migrate` — body: `{"target": target_node, "online": 0|1, "with-local-disks": 1}`
6. Poll the returned task ID with `wait_for_task(proxmox, current_node, task_id, timeout=600)`.
7. On success: update deployment JSON `"node"` field and save with `write_deployment_file()`.

### CLI options

| Option | Description |
|---|---|
| `--deploy-file FILE` | Deployment JSON for the host to migrate. Required. |
| `--target NODE` | Destination Proxmox node name. Interactive picker if omitted. |
| `--online` | Attempt live migration (no downtime). Requires shared storage. |
| `--config FILE` | Alternate config file. |

### Implementation notes

- Migration timeout is longer than most operations — use 600s default.
- If the migration task fails, the deployment JSON is NOT updated. Host stays on original node.
- After migration, confirm success: query `proxmox.nodes(target_node).lxc(vmid).status.current.get()` and verify it responds. Print ✓ confirmed or a warning.
- `with-local-disks: 1` is required for VM migration with local storage — include it unconditionally.
- For LXC with local storage: migration copies the rootfs as a tar to the target node. Warn the user this may be slow for large containers.

---

## vlan-report.py — VLAN Inventory Report

Read all deployment JSONs and produce a table grouped by VLAN showing every managed host, its IP, type, and Proxmox node. Useful for IP planning, subnet auditing, and spotting gaps.

### Usage

```bash
python3 vlan-report.py               # all VLANs, grouped
python3 vlan-report.py --vlan 220    # filter to a specific VLAN
python3 vlan-report.py --sort ip     # sort within each group by IP (default: hostname)
python3 vlan-report.py --format csv  # CSV output for spreadsheets
python3 vlan-report.py --format json # JSON output for scripting
```

### Output

```
VLAN 220 — Servers
┏━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Hostname    ┃ Type ┃ IP             ┃ Node       ┃ FQDN                   ┃
┡━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━┩
│ kuma        │ lxc  │ 10.220.220.180 │ proxmox01  │ kuma.lees-family.io    │
│ myserver    │ vm   │ 10.220.220.150 │ proxmox02  │ myserver.lees-family.io│
│ dhcp-host   │ lxc  │ (DHCP)         │ proxmox01  │ dhcp-host.lees-family.io│
└─────────────┴──────┴────────────────┴────────────┴────────────────────────┘

Summary: 3 hosts across 2 VLANs
```

### Implementation notes

- Data source: all JSONs from `deployments/lxc/` and `deployments/vms/` via `list_deployment_files()`. No Proxmox API connection needed.
- VLAN from `"vlan"` field in each JSON. Group numerically, sort hosts within each group by hostname (or IP if `--sort ip`).
- IP display: `assigned_ip` → `ip_address`. If value is `"dhcp"` and no `assigned_ip`, show `(DHCP)`.
- FQDN from `"fqdn"` field.
- CSV columns: `vlan,hostname,fqdn,type,ip,node`.
- Optional `vlan_names` block in `config.yaml` for section headers:
  ```yaml
  vlan_names:
    10: "Management"
    220: "Servers"
    230: "IoT"
  ```
  If not configured, section headers show `VLAN <N>` only.

---

## usage.py — Resource Usage Report from Proxmox RRD Data

Pull CPU, RAM, disk I/O, and network usage trends for all managed hosts directly from the Proxmox built-in RRD database. No external monitoring stack required.

### Usage

```bash
python3 usage.py                     # all hosts, last hour average
python3 usage.py --timeframe day     # last 24 hours (hour|day|week|month|year)
python3 usage.py --node proxmox02    # filter to one node
python3 usage.py --type vm           # filter to VMs only
python3 usage.py --sort cpu          # sort by column (cpu|ram|netin|netout)
python3 usage.py --top 10            # show only top N by sort column
```

### Output table

```
Resource Usage — last hour average
┏━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃ Hostname    ┃ Type ┃ CPU %  ┃ RAM      ┃ RAM % ┃ Net In   ┃ Net Out  ┃
┡━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│ kuma        │ lxc  │  12.4% │ 1.1 GB   │  27%  │ 1.2 MB/s │ 0.4 MB/s │
│ myserver    │ vm   │   3.1% │ 2.8 GB   │  70%  │ 0.1 MB/s │ 0.0 MB/s │
│ stopped-vm  │ vm   │     —  │ —        │   —   │ —        │ —        │
└─────────────┴──────┴────────┴──────────┴───────┴──────────┴──────────┘
```

### Proxmox RRD API

```
GET /nodes/{node}/lxc/{vmid}/rrddata?timeframe={tf}&cf=AVERAGE
GET /nodes/{node}/qemu/{vmid}/rrddata?timeframe={tf}&cf=AVERAGE
```

Returns a list of timestamped data points, each with fields: `cpu`, `mem`, `maxmem`, `netin`, `netout`, `diskread`, `diskwrite`. Average the non-null values for the summary figure.

### Implementation notes

- Load deployment JSONs for the hostname → VMID → node mapping. Query RRD for each VMID.
- Skip hosts where VMID is not found on the expected node (ghost files) — print a dim warning row.
- Skip hosts where all RRD values are null (stopped hosts with no recent data) — show dashes.
- Run API queries in parallel with `ThreadPoolExecutor` — one query per host, bounded to ~10 workers to avoid hammering the API.
- `timeframe` maps directly to Proxmox RRD strings: `hour`, `day`, `week`, `month`, `year`.
- RAM: `mem` is bytes used, `maxmem` is bytes allocated. Show used value and percentage.
- Network/disk RRD values are bytes/second — display as MB/s.
- Color CPU column: green < 50%, yellow 50–80%, red > 80%. Same thresholds for RAM %.
- No new config keys required.

---

## freshen.py — Template and Cloud Image Freshness Check

Compare locally downloaded LXC templates and VM cloud images against what is available upstream. Flag anything with a newer version available.

### Usage

```bash
python3 freshen.py                    # check both LXC templates and cloud images, all nodes
python3 freshen.py --lxc              # LXC templates only
python3 freshen.py --vm               # cloud images only
python3 freshen.py --node proxmox01   # check templates on a specific node
python3 freshen.py --download         # automatically download newer LXC templates
```

### LXC template freshness

- **Local**: `GET /nodes/{node}/storage/{storage}/content?content=vztmpl` — currently downloaded templates.
- **Available**: `GET /nodes/{node}/aplinfo` — full Proxmox community catalog. Already used by `get_lxc_repo_catalog()` in `modules/proxmox.py` — reuse it.
- **Match logic**: filenames encode version (e.g. `ubuntu-24.04-standard_24.04-2_amd64.tar.zst`). Extract the version token (second `_`-delimited segment) and compare to the catalog entry for the same base name. If catalog has `_24.04-3_` and local has `_24.04-2_`, the local copy is outdated.
- `--download`: call `download_lxc_template()` (already in `modules/proxmox.py`) for each outdated template. Does not delete the old version — Proxmox keeps both.

### Cloud image freshness

- **Local**: `list_cloud_images_on_storage()` (already in `modules/proxmox.py`) returns filenames and `ctime`.
- **Available**: `cloud-images.yaml` has the upstream URL per image. Send `requests.head(url, timeout=10, allow_redirects=True)` and read `Last-Modified` header.
- If `Last-Modified` > local `ctime`, the upstream image has been updated. If no `Last-Modified` header, show `unknown`.

### Output

```
LXC Templates — proxmox01
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Template              ┃ Local Ver  ┃ Available   ┃ Status     ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ ubuntu-24.04-standard │ 24.04-2    │ 24.04-3     │ ⚠ outdated │
│ debian-12-standard    │ 12.7-1     │ 12.7-1      │ ✓ current  │
└───────────────────────┴────────────┴─────────────┴────────────┘

Cloud Images
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Image                        ┃ Local Age  ┃ Upstream Modified    ┃ Status     ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ noble-server-cloudimg-amd64  │ 45 days    │ 2026-03-01           │ ⚠ outdated │
│ jammy-server-cloudimg-amd64  │  3 days    │ 2026-03-20           │ ✓ current  │
└──────────────────────────────┴────────────┴──────────────────────┴────────────┘
```

### Implementation notes

- Requires Proxmox API connection (storage content queries + aplinfo). No SSH needed.
- Reuse `get_lxc_templates()`, `get_lxc_repo_catalog()`, `get_iso_capable_storages()`, `list_cloud_images_on_storage()` — all in `modules/proxmox.py`.
- For HTTP HEAD: `requests.head(url, timeout=10, allow_redirects=True)`. Handle timeouts and non-200 responses gracefully (show `unknown`).
- Template version extraction: split filename on `_`; compare the version token. Fall back to full filename comparison for non-standard naming.

---

## rotate-passwd.py — Bulk Password Rotation

SSH to a deployed host and change the root and secondary user passwords, then update the deployment JSON so the stored credential stays current.

### Usage

```bash
python3 rotate-passwd.py --deploy-file deployments/lxc/myserver.json
python3 rotate-passwd.py --deploy-file deployments/lxc/myserver.json --new-password "newpass"
python3 rotate-passwd.py --all-lxc            # rotate every LXC in deployments/lxc/
python3 rotate-passwd.py --all                # rotate every managed host
python3 rotate-passwd.py --all --generate     # unique random password per host
```

### How it works

1. Load the deployment JSON. Read IP (`assigned_ip` → `ip_address`) and current `password`.
2. Prompt for new password if not given via `--new-password`. Confirm twice. Or generate a random one with `--generate` using `secrets.token_urlsafe(16)`.
3. Connect via paramiko using the current stored password:
   ```python
   ssh.connect(ip, username="root", password=old_password)
   ssh.exec_command(f'echo "root:{new_password}" | chpasswd && echo "{addusername}:{new_password}" | chpasswd')
   ```
   `addusername` from `cfg["defaults"]["addusername"]`.
4. On success: update `"password"` in the deployment JSON and save with `write_deployment_file()`.
5. Print ✓ success or ✗ failure per host. Never update the JSON if SSH failed.

### CLI options

| Option | Description |
|---|---|
| `--deploy-file FILE` | Single target. Mutually exclusive with `--all*` flags. |
| `--all-lxc` | All LXC deployment files. |
| `--all-vm` | All VM deployment files. |
| `--all` | All deployment files (LXC + VM). |
| `--new-password PASS` | New password. Prompted interactively if omitted. |
| `--generate` | Generate a unique random password per host (`secrets.token_urlsafe(16)`). |
| `--parallel N` | Concurrent hosts (default: 1 — serial for safety). |
| `--config FILE` | Alternate config file. |

### Implementation notes

- Use paramiko directly (already a dependency) rather than sshpass subprocess — cleaner and no shell injection risk.
- `--generate` with `--all`: generate a distinct password per host. Print a summary table of `hostname → new_password` after all rotations complete so the operator can record them.
- For VMs using key auth: connect with the SSH key from config, then run the same `chpasswd` command. Still update the JSON.
- History log entry: `"action": "rotate-passwd"` in `deployments/history.log` per successful rotation. Never log the password itself.
- `--parallel` default is 1 — a scripting error should not simultaneously lock out every host. Only parallelize with explicit flag.

---

## push-key.py — SSH Public Key Push

Push an SSH public key to one or more deployed hosts' `authorized_keys` without redeploying. Handles both adding and revoking keys, across root and the secondary user.

### Usage

```bash
python3 push-key.py --deploy-file deployments/lxc/myserver.json
python3 push-key.py --deploy-file deployments/lxc/myserver.json --key ~/.ssh/new_key.pub
python3 push-key.py --all                                         # push to every managed host
python3 push-key.py --all --key ~/.ssh/teammate.pub               # push a colleague's key
python3 push-key.py --deploy-file deployments/lxc/myserver.json --revoke ~/.ssh/old_key.pub
python3 push-key.py --deploy-file deployments/lxc/myserver.json --user root
python3 push-key.py --deploy-file deployments/lxc/myserver.json --user all
```

### How it works

1. Load the deployment JSON. Read IP and `password`.
2. Read the public key from `--key` (default: `~/.ssh/id_rsa.pub`).
3. Connect via paramiko using the stored password (or existing key if already authorized — try key first, fall back to password).
4. For each target user:
   - `mkdir -p ~/.ssh && chmod 700 ~/.ssh`
   - `touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys`
   - **Push**: read current `authorized_keys`, check if key body already present (compare key type + base64 body, ignore comment). Append only if not found — fully idempotent.
   - **Revoke** (`--revoke`): filter out lines whose key body matches. Write back the filtered list.
5. Print ✓ per host per user on success.

### CLI options

| Option | Description |
|---|---|
| `--deploy-file FILE` | Single target host. |
| `--all-lxc` | All LXC hosts. |
| `--all-vm` | All VM hosts. |
| `--all` | All managed hosts. |
| `--key FILE` | Public key file to push (default: `~/.ssh/id_rsa.pub`). |
| `--revoke FILE` | Public key to remove instead of adding. |
| `--user USER` | `root`, `<addusername>`, or `all` (default: `all`). |
| `--parallel N` | Concurrent hosts (default: 5). |
| `--config FILE` | Alternate config file. |

### Implementation notes

- Idempotent push: split the key line into `[type, body, comment]`. Compare only `type + body` — the comment can differ between copies of the same key. Never append a duplicate.
- `--user all`: derive `addusername` from `cfg["defaults"]["addusername"]`. Handle both users in a single SSH session — two `authorized_keys` operations, one connection.
- Auth order: try key auth first (`~/.ssh/id_rsa` from config). If that fails, fall back to password from deployment JSON. If both fail, report error and skip.
- History log entry: `"action": "push-key"` with the key fingerprint (not the full key body) — use `ssh-keygen -lf <keyfile>` via subprocess or compute it from the key bytes directly.
