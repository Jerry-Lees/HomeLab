#!/usr/bin/env python3
"""
Draft Deployment File Builder
==============================
Interactively builds a deployment JSON file for labinator without
actually deploying anything. The resulting file can be used with:

  python3 deploy_lxc.py --deploy-file deployments/lxc/<hostname>.json
  python3 deploy_vm.py  --deploy-file deployments/vms/<hostname>.json

Use --deploy-file to load an existing file as a starting point for edits.
"""

# Auto-activate virtualenv so `python3 draft-deployment.py` works without sourcing .venv
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
import textwrap
from pathlib import Path

import questionary
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from modules.lib import (
    load_config,
    connect_proxmox,
    wait_for_task,
    get_nodes_with_load,
    get_next_vmid,
    bytes_to_gb,
    load_deployment_file,
    write_deployment_file,
    make_common_wizard_steps,
    run_wizard_steps,
    select_nav,
    checkbox_nav,
    BACK,
    SKIP,
    pt_text,
    q,
    prompt_package_profile,
    prompt_extra_packages,
    prompt_node_selection,
    parse_ttl,
    expires_at_from_ttl,
    resolve_profile,
    resolve_lxc_features,
    features_list_to_proxmox_str,
    get_lxc_templates,
    get_lxc_disk_storages,
    get_vztmpl_storages,
    get_lxc_repo_catalog,
    download_lxc_template,
    get_vm_disk_storages,
    get_iso_capable_storages,
    list_cloud_images_on_storage,
)

console = Console()

_DOWNLOAD_SENTINEL = "__download_from_repo__"
_BACK_SENTINEL     = "__back__"


# ─────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────

def derive_gateway(ip: str) -> str:
    parts = ip.rsplit(".", 1)
    return f"{parts[0]}.1"


# LXC feature flag choices (mirrors deploy_lxc.py)
LXC_FEATURE_CHOICES = [
    ("nesting=1",  "nesting=1   — nested containers (Docker, Podman, LXC-in-LXC)"),
    ("keyctl=1",   "keyctl=1    — kernel keyring (required by some container runtimes)"),
    ("fuse=1",     "fuse=1      — FUSE filesystem mounts (rclone, sshfs, etc.)"),
    ("mknod=1",    "mknod=1     — create block/character device nodes"),
    ("mount=nfs",  "mount=nfs   — NFS mounts inside the container"),
    ("mount=cifs", "mount=cifs  — CIFS/SMB mounts inside the container"),
]


# ─────────────────────────────────────────────
# LXC draft wizard
# ─────────────────────────────────────────────

def run_lxc_wizard(proxmox, nodes, cfg, deploy, ttl):
    """Run the LXC wizard and save a draft deployment file. Returns saved file path."""
    defaults = cfg["defaults"]
    profiles = cfg.get("package_profiles", {})

    _ws = make_common_wizard_steps(cfg, deploy, silent=False, nodes=nodes,
                                   cpu_threshold=float(defaults.get("cpu_threshold", 0.85)),
                                   ram_threshold=float(defaults.get("ram_threshold", 0.95)),
                                   hostname_label="container")

    def step_ip(s):
        deploy_ip = str(deploy.get("ip_address", ""))
        ip_default = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
        r = pt_text(
            "IP address for container:",
            default=s.get("ip_address", ip_default),
            instruction="leave blank for DHCP",
            validate=lambda v: (
                True if v.strip() == "" or v.strip().count(".") == 3
                else "Enter a valid IPv4 address or leave blank for DHCP"
            ),
        )
        if r is BACK:
            return BACK
        ip_address = r.strip()
        return {**s, "ip_address": ip_address, "use_dhcp": (ip_address == "")}

    def step_prefix(s):
        if s.get("use_dhcp"):
            return SKIP
        r = pt_text(
            "Prefix length (subnet mask bits):",
            default=s.get("prefix_len", "24"),
            validate=lambda v: True if v.isdigit() and 1 <= int(v) <= 32 else "Must be 1–32",
            d=deploy, key="prefix_len", silent=False,
        )
        if r is BACK:
            return BACK
        return {**s, "prefix_len": r.strip()}

    def step_gateway(s):
        if s.get("use_dhcp"):
            return SKIP
        auto_gw = derive_gateway(s["ip_address"])
        r = pt_text(
            "Gateway:",
            default=s.get("gateway", auto_gw),
            validate=lambda v: True if v.count(".") == 3 else "Enter a valid IPv4 address",
            d=deploy, key="gateway", silent=False,
        )
        if r is BACK:
            return BACK
        return {**s, "gateway": r.strip()}

    def step_lxc_features(s):
        profile_features = resolve_lxc_features(s.get("package_profile", ""), profiles)
        current_features = s.get("lxc_features", deploy.get("lxc_features", profile_features))
        feature_choices = [
            questionary.Choice(title=title, value=key)
            for key, title in LXC_FEATURE_CHOICES
        ]
        r = checkbox_nav(
            "LXC feature flags (optional):",
            feature_choices,
            defaults=current_features,
        )
        if r is BACK:
            return BACK
        return {**s, "lxc_features": r}

    def step_template(s):
        node_name = s["node_name"]
        pre_select_volid = s.get("template_volid", "")

        while True:
            with console.status(f"[bold green]Fetching templates from {node_name}..."):
                templates = get_lxc_templates(proxmox, node_name)

            if not templates:
                console.print(f"[red]No LXC templates found on {node_name}.[/red]")
                console.print("Download a template first via Proxmox UI or choose the download option.")

            template_choices = [
                questionary.Choice(title=f"[{t['storage']}] {t['name']}", value=t["volid"])
                for t in templates
            ]
            template_choices.append(
                questionary.Choice(title="─── Download from Proxmox repo...", value=_DOWNLOAD_SENTINEL)
            )

            deploy_volid     = str(deploy.get("template_volid", ""))
            default_tmpl_name = defaults.get("template", "")
            default_volid = (
                pre_select_volid if pre_select_volid and any(t["volid"] == pre_select_volid for t in templates)
                else deploy_volid if deploy_volid and any(t["volid"] == deploy_volid for t in templates)
                else next(
                    (t["volid"] for t in templates if t["name"] == default_tmpl_name),
                    templates[0]["volid"] if templates else _DOWNLOAD_SENTINEL,
                )
            )

            r = select_nav(
                "Select OS template (Ubuntu templates listed first):",
                choices=template_choices,
                default=default_volid,
            )
            if r is BACK:
                return BACK
            if r != _DOWNLOAD_SENTINEL:
                pre_select_volid = r
                template_name = r.split("/")[-1]
                return {**s, "template_volid": r, "template_name": template_name}

            # ── Download from repo flow ──
            downloaded_names = {t["name"] for t in templates}
            with console.status("[bold green]Fetching Proxmox template catalog..."):
                catalog = get_lxc_repo_catalog(proxmox, node_name, downloaded_names)

            if not catalog:
                console.print("[yellow]No additional templates available in the Proxmox repo.[/yellow]")
                continue

            catalog_choices = [
                questionary.Choice(
                    title=f"{t.get('description', t['template'])}",
                    value=t["template"],
                )
                for t in catalog
            ]
            catalog_choices.insert(0, questionary.Choice(title="← Back to template list", value=BACK))

            chosen_template = questionary.select(
                "Select template to download:",
                choices=catalog_choices,
            ).ask()

            if chosen_template is None or chosen_template is BACK:
                continue

            dl_storages = get_vztmpl_storages(proxmox, node_name)
            if not dl_storages:
                console.print("[red]No storage pools support template downloads on this node.[/red]")
                continue
            if len(dl_storages) == 1:
                dl_storage = dl_storages[0]
                console.print(f"  [dim]Downloading to storage: {dl_storage}[/dim]")
            else:
                storage_choices = [questionary.Choice(title=s, value=s) for s in dl_storages]
                dl_storage = questionary.select(
                    "Select storage to download template into:",
                    choices=storage_choices,
                ).ask()
                if dl_storage is None:
                    continue

            console.print(f"  [dim]Downloading {chosen_template}...[/dim]")
            try:
                task_id = download_lxc_template(proxmox, node_name, dl_storage, chosen_template)
                with console.status(f"[bold green]Downloading {chosen_template} (this may take a minute)..."):
                    wait_for_task(proxmox, node_name, task_id, timeout=300)
                console.print(f"  [green]✓ Downloaded {chosen_template} to {dl_storage}[/green]")
                pre_select_volid = f"{dl_storage}:vztmpl/{chosen_template}"
            except Exception as e:
                console.print(f"  [red]✗ Download failed: {e}[/red]")

    def step_storage(s):
        with console.status(f"[bold green]Querying storage pools on {s['node_name']}..."):
            storage_pools = get_lxc_disk_storages(proxmox, s["node_name"])
        if len(storage_pools) > 1:
            deploy_storage = str(deploy.get("storage", ""))
            default_storage = deploy_storage if deploy_storage in storage_pools else storage_pools[0]
            r = select_nav(
                "Select storage pool for container root disk:",
                choices=storage_pools,
                default=s.get("storage", default_storage),
            )
            if r is BACK:
                return BACK
            storage = r
        else:
            storage = storage_pools[0] if storage_pools else "local-lvm"
            console.print(f"  [dim]Storage pool: {storage}[/dim]")
        return {**s, "storage": storage}

    def step_confirm(s):
        bridge = defaults.get("bridge", "vmbr0")
        memory_mb = int(float(s["memory_gb_str"]) * 1024)
        _use_dhcp = s.get("use_dhcp", True)
        net_detail = (
            "(DHCP — IP assigned at boot)"
            if _use_dhcp else
            f"(static {s.get('ip_address', '')}/{s.get('prefix_len', '24')}  gw {s.get('gateway', '')})"
        )
        lxc_features = s.get("lxc_features", [])
        profile_tags = s.get("profile_tags", [])
        tags_display = (";".join(["auto-deploy"] + profile_tags) if profile_tags else "auto-deploy")

        console.print()
        table = Table(title="Draft Deployment Summary", show_header=False,
                      border_style="cyan", padding=(0, 2))
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Hostname",  s["hostname"])
        table.add_row("Node",      s["node_name"])
        table.add_row("Template",  s["template_name"])
        table.add_row("vCPUs",     s["cpus_str"])
        table.add_row("Memory",    f"{s['memory_gb_str']} GB ({memory_mb} MB)")
        table.add_row("Disk",      f"{s['disk_gb_str']} GB  →  {s['storage']}")
        table.add_row("Network",   f"{bridge}.{s['vlan_str']}  {net_detail}")
        table.add_row("Tags",      tags_display)
        if lxc_features:
            table.add_row("Features",  features_list_to_proxmox_str(lxc_features))
        if ttl:
            table.add_row("TTL / Expires", f"{ttl}  (expires {expires_at_from_ttl(ttl)[:19]} UTC)")
        console.print(table)
        console.print()

        try:
            r = questionary.confirm("Save draft deployment file?", default=True).unsafe_ask()
        except KeyboardInterrupt:
            console.print("\n[yellow]Aborted.[/yellow]")
            sys.exit(0)
        if r is None:
            return BACK
        if not r:
            console.print("[yellow]Draft cancelled.[/yellow]")
            sys.exit(0)

        # Build and save the deployment JSON
        domain = cfg["proxmox"].get("node_domain", "")
        fqdn = f"{s['hostname']}.{domain}" if domain else s["hostname"]
        use_dhcp = s.get("use_dhcp", True)
        data = {
            "hostname":        s["hostname"],
            "fqdn":            fqdn,
            "node":            s["node_name"],
            "template_volid":  s["template_volid"],
            "template_name":   s["template_name"],
            "cpus":            int(s["cpus_str"]),
            "memory_gb":       float(s["memory_gb_str"]),
            "disk_gb":         int(s["disk_gb_str"]),
            "storage":         s["storage"],
            "vlan":            int(s["vlan_str"]),
            "bridge":          bridge,
            "password":        s["password"],
            "ip_address":      "dhcp" if use_dhcp else s.get("ip_address", ""),
            "prefix_len":      "" if use_dhcp else s.get("prefix_len", "24"),
            "gateway":         "" if use_dhcp else s.get("gateway", ""),
            "package_profile": s.get("package_profile", ""),
            "extra_packages":  list(s.get("extra_packages", [])),
            "lxc_features":    list(lxc_features),
        }
        if ttl:
            data["ttl"]        = ttl
            data["expires_at"] = expires_at_from_ttl(ttl)

        path = write_deployment_file(data, s["hostname"], "lxc", cfg)
        console.print()
        console.print(Panel.fit(
            f"[bold green]✓ Draft saved[/bold green]\n\n"
            f"  [bold]{path}[/bold]\n\n"
            f"  Deploy with:\n"
            f"  [dim]python3 deploy_lxc.py --deploy-file {path}[/dim]",
            border_style="green",
        ))
        return {**s, "_saved": True}

    run_wizard_steps([
        _ws["hostname"], _ws["cpus"], _ws["memory"], _ws["disk"], _ws["vlan"], _ws["password"],
        step_ip, step_prefix, step_gateway,
        _ws["package_profile"], _ws["extra_packages"], step_lxc_features,
        _ws["node"], step_template, step_storage, step_confirm,
    ])


# ─────────────────────────────────────────────
# VM draft wizard
# ─────────────────────────────────────────────

def _load_cloud_images(cfg) -> list[dict]:
    """Load cloud images from cloud-images.yaml, falling back to built-ins."""
    import yaml
    _BUILTIN = [
        {
            "name": "Ubuntu 24.04 LTS (Noble Numbat)",
            "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
            "filename": "noble-server-cloudimg-amd64.img",
        },
        {
            "name": "Ubuntu 22.04 LTS (Jammy Jellyfish)",
            "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
            "filename": "jammy-server-cloudimg-amd64.img",
        },
    ]
    images_file = Path(__file__).parent / "cloud-images.yaml"
    if images_file.exists():
        with open(images_file) as f:
            data = yaml.safe_load(f)
        if data and "cloud_images" in data and data["cloud_images"]:
            return data["cloud_images"]
    return _BUILTIN


def _select_image_with_storage(proxmox, node_name, cfg, deploy, catalog) -> tuple:
    """Two-level browser: storage → image. Returns (storage, filename, url, image_refresh) or BACK."""
    vm_cfg = cfg.get("vm", {})
    default_storage  = deploy.get("cloud_image_storage") or vm_cfg.get("default_cloud_image_storage")
    default_filename = deploy.get("cloud_image_filename")

    while True:
        with console.status(f"[bold green]Querying ISO-capable storages on {node_name}..."):
            storages = get_iso_capable_storages(proxmox, node_name)

        if not storages:
            console.print(
                f"[red]No ISO-capable storage found on {node_name}. "
                "Configure a storage with content type 'iso' in Proxmox.[/red]"
            )
            return BACK

        storage_choices = [questionary.Choice(title="← Go Back", value=BACK)]
        for s in storages:
            avail = s.get("avail", 0)
            total = s.get("total", 0)
            free_str = f"  ({bytes_to_gb(avail)} GB free / {bytes_to_gb(total)} GB)" if total else ""
            storage_choices.append(questionary.Choice(
                title=f"{s['storage']}{free_str}",
                value=s["storage"],
            ))

        default_storage_val = default_storage if any(
            s["storage"] == default_storage for s in storages
        ) else None

        console.print("  [bold yellow]Cloud image storage[/bold yellow] [dim](directory storage for the image file — VM disk storage is a separate step)[/dim]")
        selected_storage = questionary.select(
            "Select storage for cloud image:",
            choices=storage_choices,
            default=default_storage_val,
        ).ask()
        if selected_storage is None or selected_storage is BACK:
            return BACK

        # Image selection
        with console.status(f"[bold green]Listing images on {selected_storage}..."):
            existing_images = list_cloud_images_on_storage(cfg, proxmox, node_name, selected_storage)

        image_choices = []
        if existing_images:
            for img in existing_images:
                size_mb = img["size"] // (1024 ** 2) if img.get("size") else 0
                size_str = f"  ({size_mb} MB)" if size_mb else ""
                image_choices.append(questionary.Choice(
                    title=f"{img['filename']}{size_str}",
                    value={"filename": img["filename"], "url": None, "action": "existing"},
                ))
            image_choices.append(questionary.Separator("─── Download from catalog ───"))

        for img in catalog:
            image_choices.append(questionary.Choice(
                title=f"Download: {img['name']}",
                value={"filename": img["filename"], "url": img["url"], "action": "download"},
            ))

        image_choices.append(questionary.Separator())
        image_choices.append(questionary.Choice(
            title="← Back to storage selection",
            value=_BACK_SENTINEL,
        ))

        default_img_val = None
        if default_filename:
            default_img_val = next(
                (c.value for c in image_choices
                 if hasattr(c, "value") and isinstance(c.value, dict)
                 and c.value.get("filename") == default_filename),
                None,
            )

        selected = questionary.select(
            f"Select image from {selected_storage}:",
            choices=image_choices,
            default=default_img_val,
        ).ask()
        if selected is None:
            return BACK
        if selected == _BACK_SENTINEL:
            continue  # Back to storage selection

        image_refresh = selected["action"] == "download"
        return selected_storage, selected["filename"], selected.get("url"), image_refresh


def run_vm_wizard(proxmox, nodes, cfg, deploy, ttl):
    """Run the VM wizard and save a draft deployment file."""
    defaults = cfg["defaults"]
    vm_cfg = cfg.get("vm", {})
    cpu_type = vm_cfg.get("cpu_type", "x86-64-v2-AES")
    machine  = vm_cfg.get("machine",  "q35")
    bios     = vm_cfg.get("bios",     "seabios")
    scsihw   = vm_cfg.get("storage_controller", "virtio-scsi-pci")

    _ws = make_common_wizard_steps(cfg, deploy, silent=False, nodes=nodes,
                                   cpu_threshold=float(defaults.get("cpu_threshold", 0.85)),
                                   ram_threshold=float(defaults.get("ram_threshold", 0.95)),
                                   hostname_label="VM")

    def step_ip(s):
        deploy_ip = str(deploy.get("ip_address", ""))
        ip_default = "" if deploy_ip.lower() in ("dhcp", "") else deploy_ip
        r = pt_text(
            "IP address for VM:",
            default=s.get("ip_address", ip_default),
            instruction="leave blank for DHCP",
            validate=lambda v: (
                True if v.strip() == "" or v.strip().count(".") == 3
                else "Enter a valid IPv4 address or leave blank for DHCP"
            ),
        )
        if r is BACK:
            return BACK
        ip_address = r.strip()
        return {**s, "ip_address": ip_address, "use_dhcp": (ip_address == "")}

    def step_prefix(s):
        if s.get("use_dhcp"):
            return SKIP
        r = pt_text(
            "Prefix length (subnet mask bits):",
            default=s.get("prefix_len", "24"),
            validate=lambda v: True if v.isdigit() and 1 <= int(v) <= 32 else "Must be 1–32",
            d=deploy, key="prefix_len", silent=False,
        )
        if r is BACK:
            return BACK
        return {**s, "prefix_len": r.strip()}

    def step_gateway(s):
        if s.get("use_dhcp"):
            return SKIP
        auto_gw = derive_gateway(s["ip_address"])
        r = pt_text(
            "Gateway:",
            default=s.get("gateway", auto_gw),
            validate=lambda v: True if v.count(".") == 3 else "Enter a valid IPv4 address",
            d=deploy, key="gateway", silent=False,
        )
        if r is BACK:
            return BACK
        return {**s, "gateway": r.strip()}

    def step_image(s):
        catalog = _load_cloud_images(cfg)
        r = _select_image_with_storage(proxmox, s["node_name"], cfg, deploy, catalog)
        if r is BACK:
            return BACK
        image_storage_name, image_filename, image_url, image_refresh = r
        return {**s, "image_storage_name": image_storage_name,
                "image_filename": image_filename, "image_url": image_url,
                "image_refresh": image_refresh}

    def step_storage(s):
        with console.status(f"[bold green]Querying storage pools on {s['node_name']}..."):
            storage_pools = get_vm_disk_storages(proxmox, s["node_name"])
        if len(storage_pools) > 1:
            deploy_storage = str(deploy.get("storage", ""))
            default_storage = deploy_storage if deploy_storage in storage_pools else storage_pools[0]
            r = select_nav(
                "Select storage pool for VM disk:",
                choices=storage_pools,
                default=s.get("storage", default_storage),
            )
            if r is BACK:
                return BACK
            storage = r
        else:
            storage = storage_pools[0] if storage_pools else "local-lvm"
            console.print(f"  [dim]Storage pool: {storage}[/dim]")
        return {**s, "storage": storage}

    def step_confirm(s):
        bridge   = defaults.get("bridge", "vmbr0")
        memory_mb = int(float(s["memory_gb_str"]) * 1024)
        use_dhcp  = s.get("use_dhcp", True)
        net_display = (
            f"{bridge}.{s['vlan_str']}  (DHCP — IP assigned at boot)"
            if use_dhcp else
            f"{bridge}.{s['vlan_str']}  (static {s['ip_address']}/{s.get('prefix_len', '')} "
            f" gw {s.get('gateway', '')})"
        )
        profile_tags = s.get("profile_tags", [])
        tags_display = (";".join(["auto-deploy"] + profile_tags) if profile_tags else "auto-deploy")

        console.print()
        table = Table(title="Draft VM Deployment Summary", show_header=False,
                      border_style="green", padding=(0, 2))
        table.add_column("Key", style="bold")
        table.add_column("Value")
        table.add_row("Hostname", s["hostname"])
        table.add_row("Node",     s["node_name"])
        table.add_row("Image",    f"{s['image_storage_name']}:{s['image_filename']}")
        table.add_row("Machine",  f"{machine} / {bios} / {cpu_type} / {scsihw}")
        table.add_row("vCPUs",    s["cpus_str"])
        table.add_row("Memory",   f"{s['memory_gb_str']} GB ({memory_mb} MB)")
        table.add_row("Disk",     f"{s['disk_gb_str']} GB  →  {s['storage']}  (scsi0)")
        table.add_row("Network",  net_display)
        table.add_row("Tags",     tags_display)
        if ttl:
            table.add_row("TTL / Expires", f"{ttl}  (expires {expires_at_from_ttl(ttl)[:19]} UTC)")
        console.print(table)
        console.print()

        try:
            r = questionary.confirm("Save draft deployment file?", default=True).unsafe_ask()
        except KeyboardInterrupt:
            console.print("\n[yellow]Aborted.[/yellow]")
            sys.exit(0)
        if r is None:
            return BACK
        if not r:
            console.print("[yellow]Draft cancelled.[/yellow]")
            sys.exit(0)

        # Build and save the deployment JSON
        domain = cfg["proxmox"].get("node_domain", "")
        fqdn = f"{s['hostname']}.{domain}" if domain else s["hostname"]
        use_dhcp = s.get("use_dhcp", True)
        data = {
            "type":                "vm",
            "hostname":            s["hostname"],
            "fqdn":                fqdn,
            "node":                s["node_name"],
            "cloud_image_storage": s["image_storage_name"],
            "cloud_image_filename": s["image_filename"],
            "cloud_image_url":     s.get("image_url"),
            "image_refresh":       s.get("image_refresh", False),
            "cpus":                int(s["cpus_str"]),
            "memory_gb":           float(s["memory_gb_str"]),
            "disk_gb":             int(s["disk_gb_str"]),
            "storage":             s["storage"],
            "vlan":                int(s["vlan_str"]),
            "bridge":              bridge,
            "password":            s["password"],
            "ip_address":          "dhcp" if use_dhcp else s.get("ip_address", ""),
            "prefix_len":          "" if use_dhcp else s.get("prefix_len", "24"),
            "gateway":             "" if use_dhcp else s.get("gateway", ""),
            "package_profile":     s.get("package_profile", ""),
            "extra_packages":      list(s.get("extra_packages", [])),
        }
        if ttl:
            data["ttl"]        = ttl
            data["expires_at"] = expires_at_from_ttl(ttl)

        path = write_deployment_file(data, s["hostname"], "vms", cfg)
        console.print()
        console.print(Panel.fit(
            f"[bold green]✓ Draft saved[/bold green]\n\n"
            f"  [bold]{path}[/bold]\n\n"
            f"  Deploy with:\n"
            f"  [dim]python3 deploy_vm.py --deploy-file {path}[/dim]",
            border_style="green",
        ))
        return {**s, "_saved": True}

    run_wizard_steps([
        _ws["hostname"], _ws["cpus"], _ws["memory"], _ws["disk"], _ws["vlan"], _ws["password"],
        _ws["package_profile"], _ws["extra_packages"],
        step_ip, step_prefix, step_gateway,
        _ws["node"], step_image, step_storage, step_confirm,
    ])


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main() -> None:
    if "--?" in sys.argv:
        sys.argv[sys.argv.index("--?")] = "--help"

    parser = argparse.ArgumentParser(
        prog="draft-deployment.py",
        description="Interactively build a labinator deployment JSON file without deploying.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 draft-deployment.py
              python3 draft-deployment.py --lxc
              python3 draft-deployment.py --vm
              python3 draft-deployment.py --lxc --deploy-file deployments/lxc/myserver.json
              python3 draft-deployment.py --lxc --ttl 7d
        """),
        add_help=False,
    )
    parser.add_argument("--help", action="help", default=argparse.SUPPRESS,
                        help="show this help message and exit")
    parser.add_argument("--lxc", action="store_true",
                        help="Build an LXC container deployment file")
    parser.add_argument("--vm", action="store_true",
                        help="Build a VM deployment file")
    parser.add_argument("--deploy-file", metavar="FILE",
                        help="Load an existing deployment JSON as a starting point")
    parser.add_argument("--config", metavar="FILE",
                        help="Path to an alternate config file (default: config.yaml)")
    parser.add_argument("--ttl", metavar="TTL",
                        help="Time-to-live (e.g. 7d, 24h, 2w). Stored in 'expires_at' field.")
    args = parser.parse_args()

    if args.lxc and args.vm:
        parser.error("--lxc and --vm are mutually exclusive")

    # Validate --ttl early
    ttl = None
    if args.ttl:
        try:
            parse_ttl(args.ttl)
            ttl = args.ttl
        except ValueError as e:
            console.print(f"[red]ERROR: {e}[/red]")
            sys.exit(1)

    cfg = load_config(args.config)
    deploy = load_deployment_file(args.deploy_file) if args.deploy_file else {}

    console.print()
    console.print(Panel.fit(
        Text("Draft Deployment Builder\n", style="bold cyan", justify="center") +
        Text("github.com: Jerry-Lees/HomeLab/labinator", style="dim cyan", justify="center"),
        border_style="cyan",
    ))
    console.print()

    if deploy:
        console.print(f"[dim]Loaded starting point: {args.deploy_file}[/dim]\n")

    # Determine type
    if args.lxc:
        kind = "lxc"
    elif args.vm:
        kind = "vm"
    elif deploy.get("type") == "vm":
        kind = "vm"
        console.print("[dim]Type detected from deployment file: VM[/dim]\n")
    elif "template_volid" in deploy or "template_name" in deploy:
        kind = "lxc"
        console.print("[dim]Type detected from deployment file: LXC[/dim]\n")
    else:
        r = questionary.select(
            "Draft for:",
            choices=[
                questionary.Choice("LXC container", value="lxc"),
                questionary.Choice("VM (cloud-init)", value="vm"),
            ],
        ).ask()
        if r is None:
            console.print("[yellow]Aborted.[/yellow]")
            sys.exit(0)
        kind = r

    # Connect to Proxmox
    storage_content = "rootdir" if kind == "lxc" else "images"
    with console.status("[bold green]Connecting to Proxmox cluster..."):
        try:
            proxmox = connect_proxmox(cfg)
            nodes = get_nodes_with_load(proxmox, storage_content=storage_content)
        except Exception as e:
            console.print(f"[red]Failed to connect to Proxmox: {e}[/red]")
            sys.exit(1)

    if not nodes:
        console.print("[red]No online nodes found in the cluster.[/red]")
        sys.exit(1)

    console.print(f"[green]✓ Connected.[/green] {len(nodes)} node(s) online.\n")

    if kind == "lxc":
        run_lxc_wizard(proxmox, nodes, cfg, deploy, ttl)
    else:
        run_vm_wizard(proxmox, nodes, cfg, deploy, ttl)


if __name__ == "__main__":
    main()
