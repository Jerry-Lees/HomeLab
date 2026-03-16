---
# About This Document
---

This file tracks known bugs and issues in labinator. It serves as both an open task list
and a historical record of what broke and how it was fixed.

**This is not a replacement for GitHub Issues.** Bugs here are either found by people
actively developing the tools, or are issues that were opened on GitHub and accepted for
work. For user-reported bugs or feature requests, open an issue at:
https://github.com/Jerry-Lees/HomeLab/issues

**Conventions:**
- Bug IDs are sequential and never reused. Next ID: **BUG-003**.
- New bugs go into the `# Known Bugs / Issues` section with the next available ID.
- When a bug is fixed, move it to the **top** of the `# FIXED Bugs / Issues` section
  (most recently fixed first) and update Status, Date Fixed, and add a Fix Applied section.
- Severity levels: `Critical` (blocks deployment), `High` (data loss risk),
  `Medium` (functional breakage, workaround exists), `Low` (cosmetic).

---
# Example Bug Record
---

## BUG-000 — Short description of the problem

**Status:** Open | Fixed
**Severity:** Critical | High | Medium | Low
**Affected script:** `script-name.py` → `modules/lib.py` (`function_name`)
**First observed:** YYYY-MM-DD, during what test or operation
**Date Added:** YYYY-MM-DD
**Date Fixed:** YYYY-MM-DD | N/A

### Symptom

What the user sees. Include exact error messages or output snippets.

### Root Cause

What is actually wrong in the code. If unknown, label as `(suspected)` and describe
the theory. Include file and function references.

### To Investigate

- Bullet list of specific things to check if the root cause is not yet confirmed.
- When the bug is fixed, rename this section to `### To Investigate (FIXED: See Fix Applied below)` and leave the original verbiage intact.

### Fix Applied

What was changed and in which file/function. Include the commit or date if known.

### Workaround

If the bug is open, describe any workaround. If none exists, state that explicitly.


---
# Known Bugs / Issues
---


---
# FIXED Bugs / Issues
---

## BUG-002 — `expire.py --reap` confirmation panel shows `IP: ???`

**Status:** Fixed
**Severity:** Low (cosmetic — decomm proceeds correctly, DNS removal uses correct IP)
**Affected script:** `expire.py` → `modules/lib.py` (`confirm_destruction`)
**First observed:** 2026-03-15, during Phase 5.2 reap testing (test-expire, VMID 111)
**Date Added:** 2026-03-15
**Date Fixed:** 2026-03-15

### Symptom

The confirmation panel before decommission shows:

```
IP  : ???
```

Even though the DNS removal step immediately after uses the correct IP (`10.220.220.150`).

### Root Cause

`confirm_destruction()` in `lib.py` read only the `ip_address` key from the resource dict. Entries built by `scan_expiring()` in `expire.py` set the `ip` key (not `ip_address`) from `assigned_ip`/`ip_address` in the deployment JSON. The DNS step reads `assigned_ip` directly from the JSON file on disk, which is why it worked correctly.

### To Investigate (FIXED: See Fix Applied below)

- Check `confirm_destruction()` in `lib.py` — which key does it read for IP display?
- Check `decomm_resource()` in `lib.py` — does it set both `ip` and `ip_address` on the resource dict before calling `confirm_destruction`?
- Check `scan_expiring()` in `expire.py` — the entry sets `"ip": assigned_ip or ip_address` but does it also set `"ip_address"`?

### Fix Applied

`confirm_destruction()` in `modules/lib.py` — changed the IP lookup from:
```python
ip = deploy.get("ip_address", "???")
```
to:
```python
ip = deploy.get("ip_address") or deploy.get("ip", "???")
```
This falls back to the `ip` key if `ip_address` is not set, matching how `scan_expiring()` populates the resource dict.

---

## BUG-001 — VM Health Check: SSH fails with "Bad authentication type"

**Status:** Fixed
**Severity:** Low (cosmetic — deploy succeeds)
**Affected script:** `deploy_vm.py` → `modules/lib.py` (`health_check`)
**First observed:** 2026-03-15, during Phase 4.3 TTL testing (testvm-1d, VMID 113)
**Date Added:** 2026-03-15
**Date Fixed:** 2026-03-16

### Symptom

At the end of a successful VM deployment, the health check reports:

```
✓ TCP port 22 open on <IP>
⚠ SSH check failed: Bad authentication type; allowed types: ['publickey']
```

The deployment itself completes successfully — DNS, inventory, Ansible all worked. Only the final SSH health check fails.

### Root Cause

Ubuntu 24.04 cloud images ship `/etc/ssh/sshd_config.d/60-cloudimg-settings.conf` which enforces `PasswordAuthentication no` at the SSH daemon level. This overrides any changes the Ansible post-deploy playbook makes to `/etc/ssh/sshd_config`, so password authentication is rejected regardless of what the playbook sets. The health check was attempting password auth and failing at the protocol level.

Additionally, cloud-init only injects the SSH key for `root` — not for the additional user created during deploy — so connecting as that user with any auth method would also fail.

### Not affected

- LXC deployments (`deploy_lxc.py`) — health check passes cleanly on LXCs because SSH is bootstrapped manually via `pct exec` and password auth is explicitly configured before Ansible runs.

### To Investigate (FIXED: See Fix Applied below)

- Check what authentication method the health check in `deploy_vm.py` is using (search for the health check section near the end of `main()`).
- Verify whether `PasswordAuthentication yes` is actually taking effect — SSH into the VM manually after deploy and check `sshd_config`.
- Consider switching the health check to key-based auth for VMs (consistent with how Ansible connects to VMs).
- Alternatively, just test TCP port 22 connectivity and skip the SSH auth check entirely for VMs, since Ansible already confirmed SSH works.

### Fix Applied

`health_check()` in `modules/lib.py` — removed password-based authentication and the `ssh_key` parameter entirely. The function now always connects as `root` using the SSH agent (`allow_agent=True, look_for_keys=True`), which matches how the Proxmox node injects the deployment key for root:

```python
client.connect(ip, username="root", timeout=timeout,
               allow_agent=True, look_for_keys=True)
```

All call sites in `deploy_vm.py` and `deploy_lxc.py` updated to remove the `ssh_key=` argument.
