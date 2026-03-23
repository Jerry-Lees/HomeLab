"""
labinator shared library — compatibility shim
=============================================
This file re-exports all public symbols from the split module files so that
existing caller scripts (deploy_lxc.py, deploy_vm.py, decomm_lxc.py,
decomm_vm.py, cleanup_tagged.py, expire.py, configure.py) continue to work
without any changes.

Do not add new logic here — add it to the appropriate sub-module.
"""

# ── startup ──────────────────────────────────────────────────────────────────
from modules.startup import (
    load_config,
    connect_proxmox,
    check_ansible,
    check_sshpass,
)

# ── preflight ────────────────────────────────────────────────────────────────
from modules.preflight import (
    _PF,
    run_preflight,
    dns_precheck,
    _pf_config_valid,
    _pf_ssh_key_exists,
    _pf_ansible_installed,
    _pf_sshpass_installed,
    _pf_proxmox_reachable,
    _pf_proxmox_auth,
    _pf_proxmox_ssh,
    _pf_dns_reachable,
    _pf_dns_ssh_auth,
    _pf_dns_hostname,
    _pf_ip_in_use,
)

# ── preflight: inventory checks (now in ansible.py but kept under old names) ─
from modules.ansible import (
    _ansible_inventory_reachable as _pf_inventory_reachable,
    _ansible_inventory_ssh_auth as _pf_inventory_ssh_auth,
)

# ── validation ───────────────────────────────────────────────────────────────
from modules.validation import (
    validate_config,
    _check_ipv4,
    validate_deployment_common,
    run_validate_common,
    dry_run_validate_and_load,
    node_passes_filter,
    check_vlan_exists,
    validate_lxc_deployment,
    validate_vm_deployment,
)

# ── proxmox ──────────────────────────────────────────────────────────────────
from modules.proxmox import (
    wait_for_task,
    get_nodes_with_load,
    get_next_vmid,
    node_ssh_host,
    stop_and_destroy,
    promote_resource,
    retag_resource,
    apply_tag_colors,
    smart_size,
    bytes_to_gb,
    get_vm_disk_storages,
    get_lxc_disk_storages,
    get_iso_capable_storages,
    get_storage_iso_path,
    get_lxc_templates,
    get_vztmpl_storages,
    get_lxc_repo_catalog,
    download_lxc_template,
    check_node_resources,
    run_ssh_cmd,
    list_cloud_images_on_storage,
    import_cloud_image,
    write_guest_agent_snippet,
    wait_for_guest_agent_ip,
    create_vm,
    configure_vm_disk_and_cloudinit,
    start_vm,
    wait_for_lxc_ip,
    run_pct_exec,
    bootstrap_lxc_ssh,
    create_lxc,
    apply_lxc_features_ssh,
    start_lxc,
    get_running_vmids,
    find_lxc_by_hostname,
)

# ── bind (DNS) ───────────────────────────────────────────────────────────────
from modules.bind import (
    run_ansible_add_dns,
    remove_dns,
)

# ── ansible ──────────────────────────────────────────────────────────────────
from modules.ansible import (
    run_ansible_post_deploy,
    run_ansible_inventory_update,
    remove_from_inventory,
)

# ── io ───────────────────────────────────────────────────────────────────────
from modules.io import (
    write_history,
    load_deployment_file,
    load_deployment_json,
    list_deployment_files,
    write_deployment_file,
    add_common_deploy_args,
    print_dry_run_header,
    print_dry_run_footer,
)

# ── deploy ───────────────────────────────────────────────────────────────────
from modules.deploy import (
    health_check,
    wait_for_ssh,
)

# ── decomm ───────────────────────────────────────────────────────────────────
from modules.decomm import (
    decomm_resource,
    process_action_list,
)

# ── ui ───────────────────────────────────────────────────────────────────────
from modules.ui import (
    BACK,
    SKIP,
    SKULL,
    q,
    pt_text,
    select_nav,
    checkbox_nav,
    flush_stdin,
    random_caps,
    confirm_destruction,
    run_wizard_steps,
    prompt_package_profile,
    prompt_extra_packages,
    prompt_node_selection,
    make_common_wizard_steps,
)

# ── profiles ─────────────────────────────────────────────────────────────────
from modules.profiles import (
    resolve_profile,
    resolve_lxc_features,
    resolve_tag_colors,
    features_list_to_proxmox_str,
    parse_ttl,
    expires_at_from_ttl,
)

# ── convenience re-exports used directly in caller scripts ───────────────────
import sys
from pathlib import Path
from rich.console import Console

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("ERROR: proxmoxer not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

console = Console()

_ROOT = Path(__file__).parent.parent
