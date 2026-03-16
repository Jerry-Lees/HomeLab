# Labinator — Architecture Overview

Labinator is a command-line tool for deploying and managing LXC containers and QEMU VMs
in a Proxmox VE homelab cluster. It handles the full lifecycle: creation, post-deploy
configuration, DNS registration, inventory registration, TTL-based expiry, and
decommission.

---

## High-Level Flow

```
User runs deploy_lxc.py or deploy_vm.py
        │
        ├─ 1. Preflight checks (API, SSH, Ansible, DNS, inventory reachable)
        ├─ 2. Interactive prompts (or --deploy-file + --silent)
        ├─ 3. Create resource in Proxmox via API
        ├─ 4. Configure cloud-init / bootstrap SSH
        ├─ 5. Run Ansible post-deploy playbook
        ├─ 6. Register DNS A record on BIND server
        ├─ 7. Register host in Ansible inventory
        └─ 8. Write deployment JSON to deployments/lxc/ or deployments/vms/

User runs decomm_lxc.py or decomm_vm.py
        │
        ├─ 1. Read deployment JSON (--deploy-file or interactive selection)
        ├─ 2. Confirm destruction (typed challenge)
        ├─ 3. Stop and destroy resource via Proxmox API
        ├─ 4. Remove DNS records from BIND
        ├─ 5. Remove from Ansible inventory
        └─ 6. Report deployment file path (--purge to delete it)
```

---

## Scripts

| Script | Purpose |
|---|---|
| `deploy_lxc.py` | Deploy a new LXC container |
| `deploy_vm.py` | Deploy a new QEMU VM via cloud-init |
| `decomm_lxc.py` | Decommission an LXC container |
| `decomm_vm.py` | Decommission a QEMU VM |
| `cleanup_tagged.py` | Batch cleanup of all resources tagged `auto-deploy` |
| `expire.py` | TTL-based expiry management (check / reap / renew) |

---

## Shared Library (`modules/lib.py`)

All scripts import from `modules/lib.py`. This is the single source of truth for:

- Proxmox API connection and failover (`connect_proxmox`)
- Config loading and validation (`load_config`, `validate_config`)
- Node selection with resource filtering (`get_nodes_with_load`, `prompt_node_selection`)
- DNS add/remove wrappers (via Ansible playbooks)
- Ansible inventory add/remove wrappers
- Resource destruction (`stop_and_destroy`)
- Decommission pipeline (`decomm_resource`) — used by `cleanup_tagged.py` and `expire.py`
- Action list processing (`process_action_list`) — used by `cleanup_tagged.py` and `expire.py`
- Post-deploy health check (`health_check`)
- TTL parsing (`parse_ttl`)
- Deployment file helpers (`list_deployment_files`, `load_deployment_json`)
- Interactive confirmation (`confirm_destruction`)

**Why this matters:** `cleanup_tagged.py` and `expire.py` reuse the exact same
decommission pipeline as `decomm_lxc.py` and `decomm_vm.py`. Any fix or improvement to
the decomm flow is automatically inherited by all four consumers.

---

## External Systems

```
labinator (local machine)
    │
    ├──── Proxmox API (:8006) ─────────── create/destroy VMs and LXC
    │
    ├──── Proxmox node SSH (:22) ─────── cloud image downloads, snippets, bootstrap
    │
    ├──── Deployed host SSH (:22) ──────── Ansible post-deploy configuration
    │
    ├──── BIND DNS server SSH (:22) ──── A and PTR record registration/removal
    │
    └──── Ansible inventory server SSH (:22) ── known_hosts, ssh-copy-id, inventory file
```

All external connections use SSH key auth (via the key specified in `config.proxmox.ssh_key`).
No passwords are transmitted over the network except as Ansible `extra-vars` to the
post-deploy playbook.

---

## Deployment Files as State

Deployment JSON files in `deployments/lxc/` and `deployments/vms/` are the only
persistent state labinator maintains. There is no database.

- Deploy scripts write the file at completion.
- Decomm scripts read the file to drive cleanup. Optionally delete it with `--purge`.
- `expire.py` scans all files for `expires_at` to find expiring/expired deployments.
- `cleanup_tagged.py` uses the Proxmox API (not deployment files) to find tagged resources,
  but falls back to deployment files for IP information during DNS cleanup.

---

## Tagging Convention

Every resource deployed by labinator is tagged `auto-deploy` in Proxmox. Additional tags
come from the selected package profile (e.g. `WWW`, `DB`, `Docker`).

The `auto-deploy` tag is what `cleanup_tagged.py` uses to find managed resources. If
a resource is promoted (tag removed), it is no longer visible to cleanup and expiry tools.

---

## TTL and Expiry

Deployments can be given a TTL at deploy time (`--ttl 7d`). This stores `ttl` and
`expires_at` (ISO 8601 UTC) in the deployment file.

`expire.py` scans all deployment files for `expires_at`:
- `--check` — reports expired and expiring-soon resources. No Proxmox connection needed.
- `--reap` — decommissions all expired resources using the same pipeline as the decomm scripts.
- `--renew HOSTNAME --ttl Xd` — extends a deployment by updating `expires_at` in the file.

Deployments without `expires_at` are ignored by `expire.py`.

---

## Provider Architecture

DNS and inventory integrations are designed around a provider model. Currently:

| Integration | Current provider | Planned providers |
|---|---|---|
| DNS | `bind` | `powerdns`, `technitium` |
| Inventory | `flat_file` | `awx`, `semaphore` |

The `provider` field in `config.yaml` selects which backend is used. Adding a new
provider means implementing the corresponding Ansible playbooks and updating the
provider dispatch logic in the deploy/decomm scripts.

---

## Further Reading

- `docs/specs/config-schema.md` — full `config.yaml` field reference
- `docs/specs/deployment-file.md` — LXC and VM deployment JSON schema
- `docs/specs/cleanup-action-list.md` — `--list-file` format for `cleanup_tagged.py`
- `docs/specs/cloud-images.md` — `cloud-images.yaml` catalog format
- `docs/integrations/ansible.md` — Ansible post-deploy and inventory integration
- `docs/integrations/bind-dns.md` — BIND DNS registration and removal
- `docs/specs/porter-snapshot-manifest.md` — porter archive manifest spec (future integration)
