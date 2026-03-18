[← Back to README](../../README.md)

# Cleanup Action List File — Schema Reference

### About

The cleanup action list file is a JSON input consumed by `cleanup_tagged.py --list-file`.
It pre-defines what action to take on each tagged resource, enabling automated or
batch cleanup runs without interactive prompts.

**Why it matters:** Without a list file, `cleanup_tagged.py` is fully interactive —
it displays each resource and asks what to do. The list file replaces that interaction,
making it suitable for scheduled cleanup windows, CI pipelines, or any situation where
decisions are made in advance. Combined with `--silent`, it requires zero human input.

---

## File Format

A JSON array of objects. Each object represents one resource to act on.

```json
[
  {"hostname": "test-lxc",   "action": "keep"},
  {"hostname": "test-vm",    "vmid": "113", "action": "decomm"},
  {"hostname": "staging-db", "action": "promote"}
]
```

---

## Field Reference

| Field | Required | Type | Description |
|---|---|---|---|
| `hostname` | ✓ | string | Short hostname of the resource as it appears in Proxmox. Must match the Proxmox resource name exactly. |
| `action` | ✓ | string | What to do with this resource. Must be one of: `keep`, `decomm`, `promote`. See Actions below. |
| `vmid` | optional | string or int | Proxmox VMID. Used to disambiguate when two resources share the same hostname (one LXC and one VM). If omitted, hostname alone is used for matching. |

---

## Actions

| Action | Description |
|---|---|
| `keep` | Leave the resource running. No changes made. Shown in summary as kept. |
| `decomm` | Fully decommission: stop and destroy the Proxmox resource, remove DNS records, remove from Ansible inventory. Same pipeline as `decomm_lxc.py` / `decomm_vm.py`. |
| `promote` | Remove the `auto-deploy` tag from the resource in Proxmox. The resource continues running and is no longer managed by labinator cleanup. Use when a temporary resource becomes permanent. |

---

## Matching Behavior

- Matching is by **hostname** first. If a `vmid` is provided, it is appended to form a
  compound key (`hostname:vmid`) for disambiguation.
- Resources in the cluster that are **not listed** in the file default to `keep`.
  The list file only needs to enumerate resources you want to act on — omitted resources
  are left alone.
- Resources listed in the file that are **not found in the cluster** produce a warning
  and are skipped. This is not an error — the resource may have already been cleaned up.

---

## Flags

| Flag | Description |
|---|---|
| `--list-file FILE` | Load actions from this file. Interactive prompt is skipped for all listed resources. |
| `--silent` | Skip all confirmation prompts and the typed challenge. Requires `--list-file`. Intended for automated/unattended runs. |

**Note:** `--silent` without `--list-file` is an error. The silent flag exists specifically
for pre-planned automated runs where human oversight has already happened at list-file
creation time.

---

## Validation Rules

The file is validated on load before any Proxmox connections are made. Any validation
error exits immediately with a descriptive message.

| Rule | Error |
|---|---|
| File must be valid JSON | `ERROR: Could not read list file` |
| Top level must be a JSON array | `ERROR: List file must be a JSON array of objects` |
| Each entry must be a JSON object | `ERROR: List file entry N is not an object` |
| `hostname` must be present and non-empty | `ERROR: List file entry N missing 'hostname'` |
| `action` must be `keep`, `decomm`, or `promote` | `ERROR: List file entry N has invalid action 'X'` |

---

## Examples

### Keep all — safe inspection run

```json
[
  {"hostname": "test-lxc",    "action": "keep"},
  {"hostname": "test-vm",     "action": "keep"},
  {"hostname": "staging-web", "action": "keep"}
]
```

### Mixed actions — typical cleanup window

```json
[
  {"hostname": "test-lxc",    "action": "decomm"},
  {"hostname": "test-vm",     "vmid": "113", "action": "decomm"},
  {"hostname": "staging-web", "action": "promote"},
  {"hostname": "dev-db",      "action": "keep"}
]
```

### Disambiguate duplicate hostname

```json
[
  {"hostname": "test-box", "vmid": "111", "action": "decomm"},
  {"hostname": "test-box", "vmid": "112", "action": "keep"}
]
```

### Silent automated run

```bash
./cleanup_tagged.py --list-file nightly-cleanup.json --silent
```

The file is processed without any prompts. All `decomm` actions execute immediately.

---

[← Back to README](../../README.md)
