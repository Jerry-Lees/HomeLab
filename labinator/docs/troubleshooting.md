[← Back to README](../README.md)

# Troubleshooting

### About

Quick reference for common errors and unexpected behavior. Each entry describes the symptom, the cause, and what to do about it. Entries marked *(VM only)* or *(LXC)* apply to that resource type only.

## Table of Contents

- ["token_secret is CHANGEME" on startup](#token_secret-is-changeme-on-startup)
- [401 Unauthorized connecting to Proxmox](#401-unauthorized-connecting-to-proxmox)
- ["Failed to connect to Proxmox" / SSL errors](#failed-to-connect-to-proxmox--ssl-errors)
- [0.0 GB RAM shown for all nodes / no templates found](#00-gb-ram-shown-for-all-nodes--no-templates-found)
- ["No LXC templates found on [node]"](#no-lxc-templates-found-on-node)
- ["No ISO-capable storage found on [node]"](#no-iso-capable-storage-found-on-node)
- ["No nodes pass the resource filter"](#no-nodes-pass-the-resource-filter)
- ["Storage X only has 0.0 GB free" (lvmthin false positive)](#storage-x-only-has-00-gb-free-lvmthin-false-positive)
- ["SSH key auth to proxmoxNN.example.com failed"](#ssh-key-auth-to-proxmoxnnexamplecom-failed)
- [LXC feature flags not applied / "Could not apply feature flags via SSH"](#lxc-feature-flags-not-applied--could-not-apply-feature-flags-via-ssh--lxc-only)
- [Container stuck at "Waiting for DHCP IP" (LXC)](#container-stuck-at-waiting-for-dhcp-ip-lxc)
- [Static IP config failed during LXC bootstrap](#static-ip-config-failed-during-lxc-bootstrap)
- [Ansible post-deploy fails: "UNREACHABLE" (LXC)](#ansible-post-deploy-fails-unreachable-lxc)
- [VM stuck at "Waiting for SSH" or "Polling guest agent for IP"](#vm-stuck-at-waiting-for-ssh-or-polling-guest-agent-for-ip)
- [cloud-init status --wait crashes on Rocky Linux 8](#cloud-init-status---wait-crashes-on-rocky-linux-8)
- [Rocky Linux 8 first boot takes up to 15 minutes](#rocky-linux-8-first-boot-takes-up-to-15-minutes)
- [qemu-guest-agent not in Ubuntu 24.04 / Rocky Linux 8 / openSUSE cloud images](#qemu-guest-agent-not-in-ubuntu-2404--rocky-linux-8--opensuse-cloud-images)
- [No reverse DNS zones (PTR records always skipped)](#no-reverse-dns-zones-ptr-records-always-skipped)
- [VLAN check always passes for VLAN-aware bridges](#vlan-check-always-passes-for-vlan-aware-bridges)
- [Ansible Python 3.6 warning on openSUSE](#ansible-python-36-warning-on-opensuse)
- [cloud-init first-boot failed](#cloud-init-first-boot-failed)
- ["wget failed" downloading cloud image](#wget-failed-downloading-cloud-image)
- ["qm importdisk failed"](#qm-importdisk-failed)
- [Skipping Ansible, DNS, or inventory registration](#skipping-ansible-dns-or-inventory-registration)
- [DNS registration fails](#dns-registration-fails)
- [Host added to wrong inventory group](#host-added-to-wrong-inventory-group)
- [Inventory update fails on development server](#inventory-update-fails-on-development-server)
- [Resource was created but something failed mid-way](#resource-was-created-but-something-failed-mid-way)
- [Preflight check fails: "Static IP in use"](#preflight-check-fails-static-ip-in-use)
- [Preflight check warns: "DNS hostname check"](#preflight-check-warns-dns-hostname-check)
- [Preflight check warns: "Proxmox node SSH" for one node](#preflight-check-warns-proxmox-node-ssh-for-one-node)
- ["--silent" exits 1 on preflight warnings](#--silent-exits-1-on-preflight-warnings)
- ["proxmoxer not installed" / Python import errors](#proxmoxer-not-installed--python-import-errors)

---

### "token_secret is CHANGEME" on startup

Edit `config.yaml` and paste your Proxmox API token secret into `proxmox.token_secret`.

---

### 401 Unauthorized connecting to Proxmox

- Verify `proxmox.token_name` is just the token ID (e.g. `vm-deploy`) — **not** the full `root@pam!vm-deploy` string
- Verify `proxmox.token_secret` is correct
- Confirm **Privilege Separation is disabled** on the token in the Proxmox UI

---

### "Failed to connect to Proxmox" / SSL errors

```bash
curl -k https://proxmox01.example.com:8006/api2/json/version
```
If `verify_ssl: true`, either set it to `false` or install a valid and trusted cert on Proxmox.

> **Note:** Replace `proxmox01.example.com` with your Proxmox hostname from `proxmox.hosts` in `config.yaml`.

---

### 0.0 GB RAM shown for all nodes / no templates found

The API token lacks permissions. In the Proxmox UI, confirm:
- **Privilege Separation** is unchecked on the token
- The token's user has Administrator role (or equivalent) on `/`

---

### "No LXC templates found on [node]"

Download a template in Proxmox: **node → local storage → CT Templates → Templates → Download**.

---

### "No ISO-capable storage found on [node]"  *(VM only)*

The selected node has no storage configured with `iso` content type. In Proxmox, go to **Datacenter → Storage**, edit an existing storage, and add `ISO image` to its content types. `local` has this by default on most Proxmox installs.

---

### "No nodes pass the resource filter"

All online nodes are at or above the CPU/RAM thresholds for the requested size. The script warns and shows all nodes anyway. Consider requesting fewer resources, waiting for load to decrease, or checking whether any nodes are offline.

---

### "Storage X only has 0.0 GB free" (lvmthin false positive)

Earlier versions of the storage space check reported `0.0 GB` for LVM-thin pools because the raw API bytes value was misread. This has been fixed. If you see this on a current version, file an issue with your Proxmox version and storage type.

---

### "SSH key auth to proxmoxNN.example.com failed"
Update `proxmox.ssh_key` in `config.yaml` if you use a non-default key path.

```bash
ssh -i ~/.ssh/id_rsa root@proxmox03.example.com echo OK
# If that fails:
ssh-copy-id -i ~/.ssh/id_rsa root@proxmox03.example.com
```

> **Note:** Replace `proxmox03.example.com` with your Proxmox node hostname and `~/.ssh/id_rsa` with your key path from `proxmox.ssh_key` in `config.yaml`.

---

### LXC feature flags not applied / "Could not apply feature flags via SSH"  *(LXC only)*

**Symptom:** After container creation, you see a yellow warning: `⚠ Could not apply feature flags via SSH: ...` and the flags are not visible in Proxmox.

**Cause:** Feature flags other than `nesting=1` cannot be set via Proxmox API tokens — the API returns `403 Forbidden`. labinator applies them via `pct set` over SSH instead, using the same key configured in `proxmox.ssh_key`. If SSH to the node fails after the container is created, the flags are skipped with a warning (non-fatal — the container is still deployed).

**Fix:** Verify SSH works to the target node:
```bash
ssh -i ~/.ssh/id_rsa root@proxmox03.example.com "pct config <vmid> | grep features"
# If SSH works, apply manually:
ssh -i ~/.ssh/id_rsa root@proxmox03.example.com "pct set <vmid> -features 'nesting=1,keyctl=1'"
```

> **Note:** Replace `proxmox03.example.com` with your node hostname, `~/.ssh/id_rsa` with your key from `proxmox.ssh_key` in `config.yaml`, and `<vmid>` with the container's VMID.

---

### Container stuck at "Waiting for DHCP IP" (LXC)

- Verify the VLAN exists as a bridge on that node (Proxmox UI → node → Network)
- Confirm your DHCP server covers that VLAN
- Check manually: `ssh root@proxmox03.example.com "pct exec 142 -- ip -4 addr show eth0"`

> **Note:** Replace `proxmox03.example.com` with your Proxmox node hostname and `142` with your container's VMID.

---

### Static IP config failed during LXC bootstrap

```
Warning: static IP config failed: pct exec failed...
```
The container will retain a working DHCP IP but no static assignment. Ansible steps may still succeed. Fix manually afterward:
```bash
ssh root@10.20.20.150
# Edit /etc/netplan/01-static.yaml and run netplan apply
```

> **Note:** Replace `10.20.20.150` with your container's actual IP address (check the Proxmox console or your DHCP server leases).

---

### Ansible post-deploy fails: "UNREACHABLE" (LXC)

1. Confirm the bootstrap step completed without errors
2. Test SSH directly: `ssh -o StrictHostKeyChecking=no root@10.20.20.150`
3. Confirm `sshpass` is installed on the controller: `which sshpass`
4. Check sshd status: `ssh root@proxmox03.example.com "pct exec 142 -- systemctl status ssh"`

The script prompts for a password retry on the first failure.

> **Note:** Replace `10.20.20.150` with your container's IP, `proxmox03.example.com` with your Proxmox node hostname, and `142` with your container's VMID.

---

### VM stuck at "Waiting for SSH" or "Polling guest agent for IP"  *(VM only)*

- Check the VM console in the Proxmox web UI — cloud-init errors appear on the serial console
- Confirm the cloud image was imported correctly (VM should have a scsi0 disk in the Proxmox UI)
- For DHCP: confirm `qemu-guest-agent` is installed and running. Ubuntu 24.04 cloud images do not ship it by default — labinator installs it via a cloud-init vendor-data snippet at deploy time. Rocky Linux 8 and openSUSE Leap 15.6 also require this snippet. If you're using a custom image that does not support vendor-data, you may need to install qemu-guest-agent manually or use a static IP instead.
- For static: confirm the IP and gateway are reachable on the VLAN

---

### cloud-init status --wait crashes on Rocky Linux 8  *(VM only)*

**Symptom:** The wait-for-cloud-init step hangs or crashes on Rocky Linux 8.

**Cause:** A bug in cloud-init 23.4 causes `cloud-init status --wait` to call `systemctl show-environment`, which fails over SSH.

**Status: Fixed.** labinator does not use `cloud-init status --wait`. Instead, it waits for `/run/cloud-init/result.json` to appear, which is written by cloud-init when all first-boot stages complete and works reliably across all supported OS families.

---

### Rocky Linux 8 first boot takes up to 15 minutes  *(VM only)*

**Symptom:** After starting a Rocky Linux 8 VM, the Ansible step waits a very long time before the host becomes reachable.

**Cause:** The Rocky Linux 8 cloud image runs a full `dnf upgrade` on first boot. This is baked into the image and cannot be suppressed with `package_upgrade: false` in cloud-init vendor-data. The upgrade can take 10–15 minutes depending on network speed and server load.

**Workaround:** The `wait_for_connection` timeout in `post-deploy-vm.yml` is set to 1800 seconds (30 minutes). No action needed — just wait.

---

### qemu-guest-agent not in Ubuntu 24.04 / Rocky Linux 8 / openSUSE cloud images  *(VM only)*

**Symptom:** After DHCP deploy, the guest agent is not running and the IP cannot be polled.

**Status: Handled automatically.** `deploy_vm.py` writes a cloud-init vendor-data snippet to `/var/lib/vz/snippets/vm-{vmid}-userdata.yaml` on the Proxmox node and passes it via `cicustom=vendor=local:snippets/...`. This snippet installs and enables `qemu-guest-agent` on first boot for all three OS families. The `vendor=` key is used rather than `user=` — `user=` would override Proxmox's generated user-data and break password/SSH key injection.

---

### No reverse DNS zones (PTR records always skipped)

**Symptom:** Every deploy prints "reverse zone file not found" and skips the PTR record.

**Cause:** The PTR step is skipped when the reverse zone file does not exist on the BIND server. This is graceful expected behavior — the A record is still registered.

**Fix:** Create the reverse zone file(s) on your BIND server for each subnet in use (e.g. `/var/lib/bind/20.20.10.in-addr.arpa.hosts` for the `10.20.20.x` subnet). This is a DNS infrastructure gap, not a labinator bug.

---

### VLAN check always passes for VLAN-aware bridges

**Symptom:** The VLAN validation check passes even when the specified VLAN ID may not be configured.

**Cause:** Proxmox VLAN-aware bridges accept any VLAN tag at the API level — they rely on upstream switch configuration to enforce VLAN membership. The check confirms the bridge exists, not that the VLAN is trunked on the upstream port.

**Expected behavior.** If a container/VM gets an IP but not the right one, verify the VLAN is trunked on the physical port connected to the Proxmox node.

---

### Ansible Python 3.6 warning on openSUSE  *(VM only)*

**Symptom:** Ansible prints a deprecation warning about Python 3.6 when configuring an openSUSE Leap 15.6 guest.

**Cause:** openSUSE Leap 15.6 ships Python 3.6 as the platform Python. Ansible warns but works correctly.

**Status: Harmless.** No action needed. The `ansible_python_interpreter=auto` setting in the generated inventory causes Ansible to find the best available Python on the guest, but the warning from the older version is expected.

---

### cloud-init first-boot failed  *(VM only)*

```bash
# Check cloud-init logs on the VM
ssh root@10.20.20.200 'cloud-init status; cat /var/log/cloud-init.log | tail -50'
```

> **Note:** Replace `10.20.20.200` with your VM's IP address.

Common causes:
- Invalid SSH key path (`proxmox.ssh_key` in `config.yaml` points to a key that doesn't exist)
- Network config error (incorrect IP, prefix, or gateway)
- Cloud image doesn't support cloud-init (not applicable to images in `cloud-images.yaml`, but relevant for custom entries)

---

### "wget failed" downloading cloud image  *(VM only)*

The download runs on the Proxmox node via SSH, not on the controller. Check:
- The Proxmox node has internet access: `ssh root@proxmox03.example.com curl -I https://cloud-images.ubuntu.com`
- The URL in `cloud-images.yaml` is valid — test it in a browser
- There is enough disk space on the target storage: the download is ~600 MB

> **Note:** Replace `proxmox03.example.com` with the Proxmox node the download was attempted on.

---

### "qm importdisk failed"  *(VM only)*

- Verify the storage pool has `images` content type enabled in Proxmox
- Verify there is enough free space on the storage (cloud images expand to their full uncompressed size after import)
- Verify the downloaded file is not corrupt: `ssh root@node 'file /path/to/cloud-images/image.img'`

> **Note:** Replace `node` with your Proxmox node hostname and `/path/to/cloud-images/image.img` with the actual path on your node (typically `{storage_mountpoint}/cloud-images/{filename}`).

---

### Skipping Ansible, DNS, or inventory registration

To disable specific integrations without failing, set flags in `config.yaml`:

```yaml
ansible:
  enabled: false              # Skip ALL Ansible post-deploy steps

dns:
  enabled: false              # Skip DNS registration

ansible_inventory:
  enabled: false              # Skip inventory update only
```

When `ansible.enabled` is false, Steps 5–7 are all skipped and the host must be configured manually. When `dns.enabled` or `ansible_inventory.enabled` is false, only those specific steps are skipped.

---

### DNS registration fails

- Confirm key-based SSH works to the DNS server: `ssh root@10.0.0.10 echo OK`
- Check BIND is running: `ssh root@10.0.0.10 systemctl status bind9`
- The forward zone file must exist and be writable
- If the reverse zone file doesn't exist, PTR is skipped (not an error); the A record still registers

> **Note:** Replace `10.0.0.10` with your DNS server address from `dns.server` in `config.yaml`.

---

### Host added to wrong inventory group

`ansible_inventory.group` in `config.yaml` is **case-sensitive** and must exactly match the `[GroupName]` header in the inventory file.

---

### Inventory update fails on development server

Inventory update failure is non-fatal — the script warns and continues. Add manually:
```bash
ssh root@dev.example.com
# Add under [Linux]:
myserver ansible_host=myserver.example.com ansible_python_interpreter=/usr/bin/python3
```

> **Note:** Replace `dev.example.com` with your inventory server from `ansible_inventory.server`, `myserver` with your hostname, `myserver.example.com` with the FQDN, and `[Linux]` with your group from `ansible_inventory.group` in `config.yaml`.

---

### Resource was created but something failed mid-way

**Option A — Decommission and re-run**

If a deployment file was saved before the failure:
```bash
python3 decomm_lxc.py --deploy-file deployments/lxc/myserver.json
# or
python3 decomm_vm.py --deploy-file deployments/vms/myvm.json
```

If no deployment file exists, destroy manually in Proxmox:
```bash
ssh root@proxmox03.example.com
pct stop 142 && pct destroy 142 --purge    # LXC
#or
qm stop 142 && qm destroy 142 --purge     # VM
```
> **Note:** replace 142 in the commands above with your VM/lxc's ID in Proxmox.

**Option B — Fix in-place**

Re-run specific Ansible playbooks manually against the host's IP. The playbooks are called automatically by the scripts but can be run independently for manual use or troubleshooting.

**Post-deploy LXC** (configure an existing container):
```bash
cd ansible
ansible-playbook -i 10.20.20.150, post-deploy.yml \
  -e container_hostname=myserver \
  -e password=yourpassword
```

> **Note:** Replace `10.20.20.150` with your container's IP, `myserver` with your hostname, and `yourpassword` with the password you set at deploy time.

**Post-deploy VM** (configure an existing VM):
```bash
cd ansible
ansible-playbook -i 10.20.20.200, post-deploy-vm.yml \
  -e vm_hostname=myvm \
  -e password=yourpassword \
  --private-key ~/.ssh/id_rsa
```

> **Note:** Replace `10.20.20.200` with your VM's IP, `myvm` with your hostname, `yourpassword` with the password from deploy, and `~/.ssh/id_rsa` with your key path from `proxmox.ssh_key` in `config.yaml`.

**Add DNS records** (register A + PTR on BIND):
```bash
cd ansible
ansible-playbook -i <dns.server>, add-dns.yml \
  -e new_hostname=myserver \
  -e new_ip=10.20.20.150 \
  -e new_fqdn=myserver.example.com \
  -e forward_zone_file=/var/lib/bind/example.com.hosts \
  -e reverse_zone_file=/var/lib/bind/220.220.10.in-addr.arpa.hosts \
  -u root
```

> **Note:** Replace `<dns.server>` with your DNS server IP from `dns.server` in `config.yaml`, `myserver` with your hostname, `10.20.20.150` with the IP, `myserver.example.com` with the FQDN, and both zone file paths with the actual paths on your BIND server.

**Remove DNS records** (remove A + PTR from BIND):
```bash
cd ansible
ansible-playbook -i <dns.server>, remove-dns.yml \
  -e hostname=myserver \
  -e ip_address=10.20.20.150 \
  -e forward_zone_file=/var/lib/bind/example.com.hosts \
  -e reverse_zone_file=/var/lib/bind/220.220.10.in-addr.arpa.hosts \
  -u root
```

> **Note:** Same substitutions as Add DNS records above.

**Update inventory** (add host to development server inventory):
```bash
cd ansible
ansible-playbook -i dev.example.com, update-inventory.yml \
  -e new_hostname=myserver \
  -e new_ip=10.20.20.150 \
  -e inventory_file=/root/ansible/inventory/hosts \
  -e inventory_group=Linux \
  -e password=yourpassword \
  -e node_domain=example.com
```

> **Note:** Replace `dev.example.com` with your inventory server, `myserver` with your hostname, `10.20.20.150` with the IP, `yourpassword` with the host's password, and `example.com` with your domain. `inventory_file` and `inventory_group` must match your `ansible_inventory` settings in `config.yaml`.

**Remove from inventory** (remove host from development server inventory):
```bash
cd ansible
ansible-playbook -i dev.example.com, remove-from-inventory.yml \
  -e hostname=myserver \
  -e inventory_file=/root/ansible/inventory/hosts
```

> **Note:** Replace `dev.example.com` with your inventory server, `myserver` with your hostname, and `inventory_file` with the path from `ansible_inventory.file` in `config.yaml`.

---

### Preflight check fails: "Static IP in use"

The IP address in your deploy file is already responding to ping. Another host is using it. Either:
- Decommission the existing host first: `./decomm_lxc.py --deploy-file deployments/lxc/myserver.json`
- Remove `ip_address` from the deployment JSON to use DHCP instead
- If you're intentionally replacing the host, add `"preflight": false` to the deploy file

---

### Preflight check warns: "DNS hostname check"

The hostname already resolves in DNS — an existing host is registered with that name. The existing host will be orphaned after the new deployment registers its own record. Decommission the old host first with `decomm_lxc.py` or `decomm_vm.py` before redeploying.

---

### Preflight check warns: "Proxmox node SSH" for one node

One node in your `nodes:` list rejected SSH key auth. This is a warning (non-fatal) because deployments target a single node and the API still works. Fix:
```bash
ssh-copy-id -i ~/.ssh/id_rsa root@proxmoxNN.example.com
```

> **Note:** Replace `proxmoxNN.example.com` with the specific node that failed (shown in the preflight output) and `~/.ssh/id_rsa` with your key path from `proxmox.ssh_key` in `config.yaml`.

---

### "--silent" exits 1 on preflight warnings

`--silent` mode is strict — it exits 1 on both warnings and fatal failures. To allow warnings through in silent/automated mode, add `--yolo` alongside `--silent`. To skip preflight entirely for a specific host, set `"preflight": false` in the deployment JSON.

---

### "proxmoxer not installed" / Python import errors

```bash
source .venv/bin/activate
python3 deploy_lxc.py
```
Or re-run `./setup.sh` to reinstall all dependencies.

---

[← Back to README](../README.md)
