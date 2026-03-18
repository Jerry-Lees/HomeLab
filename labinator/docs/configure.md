[← Back to README](../README.md)

# Config File Wizard

### About

`configure.py` is an interactive wizard that builds or edits `config.yaml` section by section, with a one-sentence hint at every prompt explaining what the field does and why it matters. It pre-fills all prompts from the existing config when editing, validates the result immediately after writing, and produces a fully-commented output file that matches `config.yaml.example` in structure and style.

If you prefer to edit `config.yaml` by hand, see the full field reference in [Initial Configuration Guide](configuration.md).

## Table of Contents

- [CLI Options](#cli-options)
- [Running the Wizard](#running-the-wizard)
- [Editing an Existing Config](#editing-an-existing-config)
- [Validating an Existing Config](#validating-an-existing-config)
- [Package Profiles](#package-profiles)
- [Timezone Autocomplete](#timezone-autocomplete)
- [Output Format](#output-format)

---

## CLI Options

`configure.py` accepts the following options. All are optional — running it with no flags starts a fresh wizard.

```
python3 configure.py [OPTIONS]
```

| Option | Description |
|---|---|
| `--edit` | Pre-fill all prompts from the existing `config.yaml` |
| `--validate` | Validate `config.yaml` and exit — no prompts, no changing, no writing to the config |
| `--output FILE` | Write to `FILE` instead of `config.yaml` |
| `--config FILE` | Config file to edit or validate (default: `config.yaml`) |
| `--help`, `--?` | Show help and exit |

---

## Running the Wizard

Run with no flags to build a `config.yaml` from scratch:

```bash
python3 configure.py
```

If a `config.yaml` already exists, the wizard won't silently overwrite it. Instead, it asks what you want to do:

```
╭──────────────────────────────────────────────╮
│           Labinator Config Wizard            │
│ Build or edit your config.yaml interactively │
╰──────────────────────────────────────────────╯

An existing config.yaml was found at /home/user/labinator/config.yaml.
? What would you like to do?
 » Edit existing config (pre-fill prompts)
   Start fresh (overwrite existing)
   Exit — I changed my mind
```

The wizard walks through every section in order. Each field shows a one-sentence hint in dim italic before the prompt:

```
── Proxmox Connection ──
  API endpoint, credentials, and SSH access for the Proxmox cluster.

  Any node in the cluster works — failover tries each one in order.
  Enter Proxmox host/FQDNs one at a time. Leave blank when done.
?   Proxmox host/fqdn (blank to finish) proxmox01.example.com
?   Proxmox host/fqdn (blank to finish) proxmox02.example.com
?   Proxmox host/fqdn (blank to finish)
  Realm-qualified API user — Proxmox format is user@realm.
? Proxmox API user (realm included, e.g. root@pam) root@pam
  Short token ID only — not the full user!tokenid string.
? API token name (the short ID, NOT the full user!id string) vm-deploy
  UUID from Proxmox: Datacenter › Permissions › API Tokens.
? API token secret (UUID — input hidden) ************************************
```

After all sections complete, the wizard writes the file and immediately runs validation:

```
  ✓ Written: /home/user/labinator/config.yaml

╭──────────────────────────── ✓  Config Valid ─────────────────────────────╮
│ All required fields present and valid.                                   │
╰──────────────────────────────────────────────────────────────────────────╯

╭────────────────────────────────── Done ───────────────────────────────────╮
│ Config saved to config.yaml                                               │
│                                                                           │
│ Next steps:                                                               │
│   • Run python3 deploy_lxc.py --preflight to test your connection         │
│   • Run python3 configure.py --validate to re-check at any time           │
│   • Edit config.yaml directly to tweak package_profiles                   │
╰───────────────────────────────────────────────────────────────────────────╯
```

If validation fails — for example, the token secret was left blank — the errors appear in a red panel instead of the green one, and the file is still written so you can re-run `--validate` or `--edit` to correct the issues.

---

## Editing an Existing Config

Use `--edit` to pre-fill every prompt from the current `config.yaml`:

```bash
python3 configure.py --edit
```

All prompts show your existing values as defaults. Press Enter to accept each one. For list fields (hosts, nodes, NTP servers), the wizard shows the current list and asks whether you want to edit it before prompting for individual entries.

To test changes without touching your live config, write to a separate file:

```bash
python3 configure.py --edit --output /tmp/config-test.yaml
```

> **Note:** `--edit` reads from `config.yaml` (or `--config FILE` if specified) and writes to `config.yaml` (or `--output FILE` if specified). The source and destination can be different files.

---

## Validating an Existing Config

Run `--validate` to check `config.yaml` without entering any prompts:

```bash
python3 configure.py --validate
```

This is useful after a manual edit to confirm nothing is missing or malformed. Exit code is `0` on pass, `1` on failure.

Example failure output:

```
Validating: config.yaml

╭──────────────────────── Config Validation Failed ─────────────────────────╮
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ ✗  proxmox.token_secret still contains a placeholder value         │    │
│ └────────────────────────────────────────────────────────────────────┘    │
╰───────────────────────────────────────────────────────────────────────────╯
```

Validation checks the following required fields: `proxmox.host` or `proxmox.hosts`, `proxmox.user`, `proxmox.token_name`, `proxmox.token_secret` (CHANGEME detection), `defaults.addusername`, `snmp.community`, `ntp.servers`, and `timezone`.

---

## Package Profiles

Package profiles are named sets of packages and Proxmox tags representing a server role. They're selected at deploy time to install a consistent toolset on every container or VM of that type.

On a fresh install, the wizard offers the six standard profiles from `config.yaml.example` automatically — just say Yes to include them:

```
── Package Profiles ──
  Named package sets for deploy-time role selection (e.g. web-server, database).
  These are nested and easiest to fine-tune by hand in config.yaml.

  Standard profiles from config.yaml.example:
    web-server, database, docker-host, monitoring-node, dev-tools, nfs-server
?   Include standard package profiles? Yes
?   Add custom profiles interactively? No
```

The standard profiles cover:

| Profile | Packages | Tags |
|---|---|---|
| `web-server` | nginx, certbot, python3-certbot-nginx, ufw | WWW |
| `database` | mariadb-server, mariadb-client | DB, MariaDB |
| `docker-host` | docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin | Docker |
| `monitoring-node` | prometheus-node-exporter, snmpd | Monitoring |
| `dev-tools` | git, vim, tmux, make, python3-pip | Dev |
| `nfs-server` | nfs-kernel-server, nfs-common | NFS, Storage |

When editing an existing config, the wizard shows your current profile names and asks whether to keep them. Fine-grained edits to package lists are easiest to make by hand directly in `config.yaml`.

> **Note:** Package names are OS-specific. The defaults above target Debian/Ubuntu. For Rocky Linux or openSUSE, adjust names as needed (e.g. `python3-certbot-nginx` may differ, but `mariadb-server` stays the same). Proxmox tag names must be alphanumeric, hyphens, or underscores — no spaces.

---

## Timezone Autocomplete

The timezone prompt uses `questionary.autocomplete()` against the full `zoneinfo` database. Start typing a city or region name and completions filter as you type — you don't need to know the full `Continent/City` format in advance:

```
── Timezone ──
  Set the system timezone on every deployed container/VM.

  Start typing a city or region — completions filter as you type.
? Timezone America/Chicago
```

Typing `Chicago` is enough to find `America/Chicago`. Typing `UTC` finds it directly.

---

## Output Format

The wizard writes `config.yaml` as a fully-commented file — not a bare YAML dump. The output matches `config.yaml.example` in structure and comment style so it's immediately readable and editable:

```yaml
proxmox:
  # API endpoint - any node in the cluster works (cluster shares state).
  # Use 'host' for a single node, or 'hosts' list for automatic failover.
  hosts:
    - proxmox01.example.com
    - proxmox02.example.com

  # Proxmox API user (realm included)
  user: root@pam

  # API Token name (created in Proxmox: Datacenter > Permissions > API Tokens)
  token_name: vm-deploy

  # API Token secret (UUID shown only once at creation time)
  token_secret: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

The file is fairly safe to edit by hand after the wizard writes it — comments are preserved on re-generation only if you run the wizard again. Manual edits between wizard runs are not lost unless you overwrite the file.

---

[← Back to README](../README.md)
