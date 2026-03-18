[← Back to README](../../README.md)

# Ansible Integration

### About

Labinator uses Ansible for two distinct purposes after a resource is deployed:

1. **Post-deploy configuration** — configures the newly deployed host (users, packages, NTP, SNMP, timezone, SSH).
2. **Inventory registration** — adds the host to the Ansible inventory on a remote server so it can be managed by future playbook runs.

Both are controlled by settings in `config.yaml` and can be independently disabled.

---

## Post-Deploy Configuration

### How it works

After a container or VM is deployed and reachable via SSH, labinator runs an Ansible
playbook against the new host. The playbook is invoked directly via `ansible-playbook`
on the local machine.

| Resource type | Playbook |
|---|---|
| LXC | `ansible/post-deploy.yml` |
| VM | `ansible/post-deploy-vm.yml` |

The inventory for the run is a temporary file written to `/tmp/` and deleted after the
playbook completes.

### Connection

- LXC: connects via password auth using `sshpass` (bootstrapped manually before Ansible runs).
- VM: connects via SSH key (`--private-key` passed to `ansible-playbook`). Key is injected
  via cloud-init at VM creation time.
- Both: `ansible_python_interpreter=auto` is set in the generated inventory to handle
  non-standard Python paths (e.g. Rocky Linux 8).

### Variables passed to the playbook

| Variable | Source | Description |
|---|---|---|
| `container_hostname` / `vm_hostname` | deployment | Short hostname of the new host. |
| `password` | deployment | Root and secondary user password. |
| `addusername` | config `defaults.addusername` | Secondary user to create. |
| `container_nameserver` | config `defaults.nameserver` | DNS resolvers (LXC only). |
| `container_searchdomain` | config `defaults.searchdomain` | Search domain (LXC only). |
| `snmp_community` | config `snmp.community` | SNMP community string. |
| `snmp_source` | config `snmp.source` | SNMP source restriction. |
| `snmp_location` | config `snmp.location` | SNMP sysLocation. |
| `snmp_contact` | config `snmp.contact` | SNMP sysContact. |
| `timezone` | config `timezone` | System timezone. |
| `ntp_servers` | config `ntp.servers` | NTP server list (JSON-encoded). |
| `profile_packages` | selected profile | Packages from the chosen package profile. |
| `extra_packages` | deployment / prompt | One-off extra packages. |

### What the playbook does

1. Waits for SSH to be available.
2. Gathers facts.
3. Loads OS-specific variables (`vars/Debian.yml`, `vars/RedHat.yml`, or `vars/Suse.yml`).
4. Sets hostname and `/etc/hosts`.
5. Creates secondary user and sets passwords.
6. Configures SSH (`PermitRootLogin yes`, `PasswordAuthentication yes`).
7. Sets timezone.
8. Runs OS-specific pre-install tasks (apt/dnf/zypper cache refresh, Docker repo if needed).
9. Installs standard baseline packages.
10. Installs profile packages.
11. Installs extra packages (if any).
12. Configures chrony NTP.
13. Configures and starts snmpd.
14. Runs OS-specific upgrade tasks.
15. VM only: installs and starts `qemu-guest-agent`.

### Multi-OS support

The playbook branches per OS family using `include_vars` and `include_tasks`:

```yaml
include_vars: "vars/{{ ansible_os_family }}.yml"
```

| OS Family | vars file | pre-install tasks | upgrade tasks |
|---|---|---|---|
| Debian (Ubuntu, Debian) | `vars/Debian.yml` | `pre-install-Debian.yml` | `upgrade-Debian.yml` |
| RedHat (Rocky, AlmaLinux, CentOS) | `vars/RedHat.yml` | `pre-install-RedHat.yml` | `upgrade-RedHat.yml` |
| Suse (openSUSE) | `vars/Suse.yml` | `pre-install-Suse.yml` | `upgrade-Suse.yml` |

Adding a new OS family: create the three files above with the appropriate variable names
and package manager commands. No changes to the main playbooks required.

### Disabling Ansible

Set `ansible.enabled: false` in `config.yaml` to skip all post-deploy Ansible steps.
The host will be deployed and started but not configured. DNS and inventory registration
are controlled separately.

---

## Inventory Registration

### How it works

After post-deploy configuration, labinator SSHes to a designated inventory server and
runs a second Ansible playbook (`ansible/update-inventory.yml`) to:

1. Add the new host to `known_hosts` (by IP and FQDN).
2. Copy the SSH key to the new host (`ssh-copy-id` as root).
3. Insert the host into the specified group in the inventory file.

### Inventory entry format

```
<hostname> ansible_host=<fqdn> ansible_python_interpreter=/usr/bin/python3
```

Added at the end of the specified `[Group]` section in the inventory file.

### Configuration

Controlled by `ansible_inventory` in `config.yaml`:

```yaml
ansible_inventory:
  enabled: true
  provider: flat_file
  server: dev.example.com
  user: root
  file: /root/ansible/inventory/hosts
  group: Linux
```

### Decommission

`decomm_lxc.py` and `decomm_vm.py` run `ansible/remove-from-inventory.yml` to remove
the host from the inventory file on the same server using the same settings.

### Disabling inventory registration

Set `ansible_inventory.enabled: false` in `config.yaml` to skip inventory registration
while still running the post-deploy configuration playbook.

---

[← Back to README](../../README.md)
