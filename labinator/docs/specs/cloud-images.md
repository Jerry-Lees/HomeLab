[← Back to README](../../README.md)

# cloud-images.yaml — Schema Reference

### About

`cloud-images.yaml` is the catalog of cloud-init capable OS images available for VM
deployment via `deploy_vm.py`. It defines the display name, download URL, and local
filename for each image.

**Why it matters:** `deploy_vm.py` presents this catalog as an interactive selection list.
Without this file, only two built-in fallback images are available (Ubuntu 24.04 and
22.04). Editing this file is how you add, remove, or update OS options without touching
the deploy script itself.

---

## File Location

```
labinator/
└── cloud-images.yaml
```

This file is committed to git. It is the shared catalog for the entire team.

---

## Format

A single top-level key `cloud_images` containing a list of image entries.

```yaml
cloud_images:
  - name: "Ubuntu 24.04 LTS (Noble Numbat)"
    url: "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
    filename: "noble-server-cloudimg-amd64.img"
```

---

## Field Reference

| Field | Required | Description |
|---|---|---|
| `name` | ✓ | Display name shown in the interactive image selection prompt. Should be human-readable and include the version. |
| `url` | ✓ | Direct download URL for the cloud image. Used when the image is not cached locally or when `image_refresh: true` is set in the deployment file. |
| `filename` | ✓ | Local filename used for caching on the Proxmox node. Images are stored at `{storage_path}/cloud-images/<filename>` — not in the Proxmox template/ISO store, so they do not appear in the Proxmox GUI ISO picker. |

---

## Image Requirements

All entries must be **cloud-init capable images** — pre-installed OS images that support
cloud-init for first-boot configuration. Installer ISOs will not work.

| Distro family | Image format | Notes |
|---|---|---|
| Ubuntu, Debian | `.img` (raw with internal qcow2 layout) | Standard `current/latest` URLs — always up to date. |
| Rocky, AlmaLinux, CentOS Stream | `.qcow2` | `latest` redirect URLs available for most. |
| Oracle Linux, Fedora, Alpine, FreeBSD | `.qcow2` or `.qcow2.xz` | **Version-specific URLs** — no stable `latest` redirect. Must be manually updated when new releases come out. See the notes in `cloud-images.yaml` for the correct source URL per distro. |
| openSUSE | `.qcow2` | Filename changes between point releases. Check source when upgrading. |
| Arch Linux | `.qcow2` | Rolling release. Filename is stable (`latest`). |

**FreeBSD note:** Ships as `.qcow2.xz` (compressed). Must be decompressed with `xz -d`
before Proxmox import, or check if your Proxmox version supports direct `.xz` import.

---

## Storage Location on Proxmox Nodes

Images are cached at:
```
{storage_path}/cloud-images/<filename>
```

This is intentionally **outside** the Proxmox template/ISO directories so images cannot
be accidentally attached as CD-ROMs during manual VM creation in the GUI. The
`cloud-images/` directory is created automatically on first download.

---

## Adding a New Image

1. Find the official cloud-init image URL for the OS version.
2. Add an entry to `cloud-images.yaml`:
   ```yaml
   - name: "Debian 13 (Trixie)"
     url: "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2"
     filename: "debian-13-generic-amd64.qcow2"
   ```
3. The new entry will appear in the `deploy_vm.py` image selection prompt immediately.
   No script changes required.

---

## Updating a Version-Specific URL

For distros without a stable `latest` redirect (Oracle Linux, Fedora, Alpine, FreeBSD),
update the `url` and `filename` fields when a new release is available. The old cached
file on Proxmox nodes is not automatically removed — delete it manually from the
`cloud-images/` directory on the node if storage is a concern.

---

## Fallback Behavior

If `cloud-images.yaml` is missing or malformed, `deploy_vm.py` falls back to two
built-in entries:

- Ubuntu 24.04 LTS (Noble)
- Ubuntu 22.04 LTS (Jammy)

This ensures the script is always functional even without the catalog file.

---

[← Back to README](../../README.md)
