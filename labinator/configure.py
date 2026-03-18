#!/usr/bin/env python3
"""
Interactive Config File Wizard
===============================
Guides you through building a config.yaml for labinator, section by section.
Pre-fills all prompts when editing an existing config.

  --edit                Edit an existing config.yaml (pre-fills all prompts)
  --validate            Validate config.yaml and exit without prompting
  --output FILE         Write to a custom path (default: config.yaml)
  --config FILE         Config to edit or validate (default: config.yaml)
"""

# ─────────────────────────────────────────────────────────────────────────────
# Virtual environment auto-activation.
# Allows `python3 configure.py` to work without manually sourcing .venv first.
# ─────────────────────────────────────────────────────────────────────────────
import os, sys
_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
    os.execv(_venv, [_venv] + sys.argv)

import argparse
from pathlib import Path
from zoneinfo import available_timezones

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from modules.lib import validate_config

console = Console()
_ROOT = Path(__file__).parent


# ─────────────────────────────────────────────────────────────────────────────
# Default package profiles — shipped verbatim from config.yaml.example.
# Offered automatically on fresh installs so new users get a useful starting
# point without having to know what profiles are or how to format them.
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_PROFILES = {
    "web-server": {
        "packages": ["nginx", "certbot", "python3-certbot-nginx", "ufw"],
        "tags": ["WWW"],
    },
    "database": {
        "packages": ["mariadb-server", "mariadb-client"],
        "tags": ["DB", "MariaDB"],
    },
    "docker-host": {
        "packages": ["docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"],
        "tags": ["Docker"],
    },
    "monitoring-node": {
        "packages": ["prometheus-node-exporter", "snmpd"],
        "tags": ["Monitoring"],
    },
    "dev-tools": {
        "packages": ["git", "vim", "tmux", "make", "python3-pip"],
        "tags": ["Dev"],
    },
    "nfs-server": {
        "packages": ["nfs-kernel-server", "nfs-common"],
        "tags": ["NFS", "Storage"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Prompt helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hint(text: str) -> None:
    """
    Print a one-sentence field hint in dim italic just before the prompt.
    Keeps hints visually distinct from the bold section headers above them.
    """
    console.print(f"  [dim italic]{text}[/dim italic]")


def _ask(question: str, default: str = "") -> str:
    """Single-line text prompt.  Strips leading/trailing whitespace."""
    return (questionary.text(question, default=str(default)).ask() or "").strip()


def _ask_bool(question: str, default: bool = True) -> bool:
    """Yes/No confirm prompt."""
    return questionary.confirm(question, default=default).ask()


def _ask_select(question: str, choices: list, default: str = None) -> str:
    """Single-choice select prompt."""
    return questionary.select(question, choices=choices, default=default).ask()


def _ask_list(item_name: str, existing: list = None) -> list:
    """
    Collect a variable-length list from the user.
    Shows the current list first so they know what they're working with,
    then loops asking for one item at a time until they're done.
    """
    items = list(existing or [])

    if items:
        console.print(f"  [dim]Current {item_name}s: {', '.join(str(i) for i in items)}[/dim]")
        if not _ask_bool(f"  Edit the {item_name} list?", default=False):
            return items
        # Start fresh — user will re-enter whatever they want to keep
        items = []

    console.print(f"  [dim]Enter {item_name}s one at a time. Leave blank when done.[/dim]")
    while True:
        val = _ask(f"  {item_name.capitalize()} (blank to finish)").strip()
        if not val:
            break
        items.append(val)

    return items


# ─────────────────────────────────────────────────────────────────────────────
# Section prompts
# Each prompt_*() function accepts the existing section dict (or {} / bare value
# for scalars) and returns the new value.  Pre-fill defaults come from the
# existing config so that --edit mode behaves like a proper in-place editor.
# ─────────────────────────────────────────────────────────────────────────────

def prompt_proxmox(existing: dict) -> dict:
    """
    Proxmox API connection settings.
    Covers host(s), credentials, SSH key, domain suffix, and SSL verification.
    """
    console.print("\n[bold cyan]── Proxmox Connection ──[/bold cyan]")
    console.print("  [dim]API endpoint, credentials, and SSH access for the Proxmox cluster.[/dim]\n")

    # Hosts list (supports single host: or hosts: list — wizard always writes hosts:)
    existing_hosts = existing.get("hosts") or ([existing["host"]] if existing.get("host") else [])
    _hint("Any node in the cluster works — failover tries each one in order.")
    hosts = _ask_list("Proxmox host/FQDN", existing_hosts)
    if not hosts:
        # Require at least one host — wizard can't write a useless config
        console.print("  [yellow]Warning: no hosts entered — using placeholder.[/yellow]")
        hosts = ["proxmox01.example.com"]

    _hint("Realm-qualified API user — Proxmox format is user@realm.")
    user = _ask("Proxmox API user (realm included, e.g. root@pam)",
                existing.get("user", "root@pam"))

    _hint("Short token ID only — not the full user!tokenid string.")
    token_name = _ask("API token name (the short ID, NOT the full user!id string)",
                      existing.get("token_name", "vm-deploy"))

    _hint("UUID from Proxmox: Datacenter › Permissions › API Tokens.")
    token_secret = questionary.password(
        "API token secret (UUID — input hidden)",
        default=existing.get("token_secret", ""),
    ).ask() or ""

    _hint("Used to copy files (cloud images, snippets) directly to Proxmox nodes.")
    ssh_key = _ask("Path to SSH key for Proxmox node access",
                   existing.get("ssh_key", "~/.ssh/id_rsa"))

    _hint("Appended to node short names to build SSH hostnames.")
    node_domain = _ask("Node domain suffix (e.g. example.com — used to build SSH hostnames)",
                       existing.get("node_domain", "example.com"))

    _hint("Enable only if Proxmox nodes have valid TLS certs — self-signed will fail.")
    verify_ssl = _ask_bool("Verify Proxmox API SSL certificate?",
                           existing.get("verify_ssl", False))

    return {
        "hosts":        hosts,
        "user":         user,
        "token_name":   token_name,
        "token_secret": token_secret,
        "ssh_key":      ssh_key,
        "node_domain":  node_domain,
        "verify_ssl":   verify_ssl,
    }


def prompt_nodes(existing: list) -> list:
    """
    Known cluster node short names (e.g. proxmox01, proxmox02).
    Used for display — the scripts also query node lists live from the API.
    """
    console.print("\n[bold cyan]── Cluster Nodes ──[/bold cyan]")
    console.print("  [dim]Short names of your Proxmox nodes (display only — live list is queried from the API).[/dim]\n")
    _hint("Short names only — the live node list is queried from the API at deploy time.")
    return _ask_list("node name", existing)


def prompt_defaults(existing: dict) -> dict:
    """
    Default values shown as suggestions in interactive deploy prompts.
    None of these are hard limits — users can override every one at deploy time.
    """
    console.print("\n[bold cyan]── Deployment Defaults ──[/bold cyan]")
    console.print("  [dim]Suggested values pre-filled in deploy wizard prompts. All can be overridden at deploy time.[/dim]\n")

    _hint("vCPU count pre-filled in the deploy wizard.")
    cpus = int(_ask("Default CPU count", existing.get("cpus", 2)))

    _hint("RAM in GB pre-filled in the deploy wizard.")
    memory_gb = int(_ask("Default RAM (GB)", existing.get("memory_gb", 4)))

    _hint("Root disk size in GB.")
    disk_gb = int(_ask("Default disk size (GB)", existing.get("disk_gb", 100)))

    _hint("802.1Q VLAN tag — 0 or blank for untagged.")
    vlan = int(_ask("Default VLAN tag", existing.get("vlan", 1)))

    _hint("Proxmox Linux bridge the container/VM NIC attaches to.")
    bridge = _ask("Default network bridge", existing.get("bridge", "vmbr0"))

    _hint("Set on the root account of every deployed host.")
    root_password = _ask("Default root password", existing.get("root_password", "changeme"))

    _hint("A second sudo user created on every host for day-to-day access.")
    addusername = _ask("Secondary admin username to create", existing.get("addusername", "admin"))

    _hint("Swap file/partition size in MB — 0 to disable.")
    swap_mb = int(_ask("Default swap (MB)", existing.get("swap_mb", 512)))

    _hint("Auto-start the container/VM when the Proxmox node boots.")
    onboot = _ask_bool("Start on Proxmox node boot?", existing.get("onboot", True))

    _hint("Unprivileged containers map UIDs away from root — highly recommended.")
    unprivileged = _ask_bool("Run LXC containers as unprivileged (recommended)?",
                             existing.get("unprivileged", True))

    _hint("Enables the Proxmox-level packet filter on the VM/LXC NIC.")
    firewall_enabled = _ask_bool("Enable Proxmox firewall on NIC by default?",
                                 existing.get("firewall_enabled", False))

    _hint("Nodes at or above this CPU load fraction are skipped during deploy.")
    cpu_threshold = float(_ask("Node CPU load limit (0.0–1.0 — skip node if above this)",
                               existing.get("cpu_threshold", 0.85)))

    _hint("Nodes at or above this RAM usage fraction are skipped during deploy.")
    ram_threshold = float(_ask("Node RAM usage limit (0.0–1.0 — skip node if above this)",
                               existing.get("ram_threshold", 0.95)))

    _hint("Appended when resolving unqualified hostnames.")
    searchdomain = _ask("Default DNS search domain", existing.get("searchdomain", "example.com"))

    _hint("DNS resolvers written to /etc/resolv.conf on every new host.")
    nameserver = _ask("Default DNS nameservers (space-separated)",
                      existing.get("nameserver", "10.0.0.1"))

    _hint("LXC template filename matched against your Proxmox template storage.")
    template = _ask("Default LXC template filename",
                    existing.get("template", "ubuntu-24.04-standard_24.04-2_amd64.tar.zst"))

    return {
        "cpus":             cpus,
        "memory_gb":        memory_gb,
        "disk_gb":          disk_gb,
        "vlan":             vlan,
        "bridge":           bridge,
        "root_password":    root_password,
        "addusername":      addusername,
        "swap_mb":          swap_mb,
        "onboot":           onboot,
        "unprivileged":     unprivileged,
        "firewall_enabled": firewall_enabled,
        "cpu_threshold":    cpu_threshold,
        "ram_threshold":    ram_threshold,
        "searchdomain":     searchdomain,
        "nameserver":       nameserver,
        "template":         template,
    }


def prompt_package_profiles(existing: dict) -> dict:
    """
    Package profiles — named sets of packages representing server roles.
    On fresh installs, the standard profiles from config.yaml.example are offered
    automatically so users get a useful starting point.  On edit, existing profiles
    can be kept as-is or replaced interactively.
    """
    console.print("\n[bold cyan]── Package Profiles ──[/bold cyan]")
    console.print("  [dim]Named package sets for deploy-time role selection (e.g. web-server, database).[/dim]")
    console.print("  [dim]These are nested and easiest to fine-tune by hand in config.yaml.[/dim]\n")

    if existing:
        # Edit path — show existing profile names and offer to keep them
        profile_names = ", ".join(existing.keys())
        console.print(f"  [dim]Existing profiles: {profile_names}[/dim]")
        keep = _ask_bool("  Keep existing package profiles?", default=True)
        if keep:
            return existing
        # Fall through to the fresh-install path below if they said no
        start_with = {}
    else:
        # Fresh install path — offer the standard profiles automatically
        default_names = ", ".join(_DEFAULT_PROFILES.keys())
        console.print(f"  [dim]Standard profiles from config.yaml.example:[/dim]")
        console.print(f"  [dim]  {default_names}[/dim]")
        include_defaults = _ask_bool(
            "  Include standard package profiles?", default=True
        )
        start_with = dict(_DEFAULT_PROFILES) if include_defaults else {}

    # Ask if they want to add custom profiles on top of whatever was selected
    add = _ask_bool("  Add custom profiles interactively?", default=False)
    if not add:
        if not start_with:
            console.print("  [dim]Leaving package_profiles empty — add profiles manually in config.yaml.[/dim]")
        return start_with

    # Minimal interactive profile builder — name, packages, tags
    profiles = dict(start_with)
    while True:
        name = _ask("  Profile name (blank to finish)").strip()
        if not name:
            break
        packages_raw = _ask(f"  Packages for '{name}' (comma-separated)").strip()
        packages = [p.strip() for p in packages_raw.split(",") if p.strip()]
        tags_raw = _ask(f"  Proxmox tags for '{name}' (comma-separated, blank for none)").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        profiles[name] = {"packages": packages, "tags": tags}

    return profiles


def prompt_preflight(existing: bool) -> bool:
    """Global preflight check toggle.  Can be overridden per-deployment."""
    console.print("\n[bold cyan]── Preflight Checks ──[/bold cyan]")
    console.print("  [dim]Run connectivity and resource checks before every deploy.[/dim]\n")
    _hint("Catches misconfigured nodes and network issues before any resources are created.")
    return _ask_bool("Enable preflight checks by default?", existing if existing is not None else True)


def prompt_dns(existing: dict) -> dict:
    """DNS registration settings.  Skipped entirely if disabled."""
    console.print("\n[bold cyan]── DNS Registration ──[/bold cyan]")
    console.print("  [dim]Automatic A/PTR record creation on your BIND server at deploy time.[/dim]\n")

    _hint("Disable if you manage DNS manually or don't have a BIND server.")
    enabled = _ask_bool("Enable automatic DNS registration?", existing.get("enabled", True))
    if not enabled:
        return {"enabled": False, "provider": "bind", "server": "", "ssh_user": "root",
                "forward_zone_file": ""}

    _hint("IP of the BIND server labinator SSHes into to update zone files.")
    server = _ask("DNS server IP", existing.get("server", "10.0.0.10"))

    _hint("SSH user for the DNS server — needs write access to the zone file.")
    ssh_user = _ask("SSH user for DNS server", existing.get("ssh_user", "root"))

    _hint("Full path to the forward zone file on the DNS server.")
    forward_zone_file = _ask("Forward zone file path on DNS server",
                             existing.get("forward_zone_file", "/var/lib/bind/example.com.hosts"))

    return {
        "enabled":           True,
        "provider":          _ask_select("DNS provider", ["bind"], default="bind"),
        "server":            server,
        "ssh_user":          ssh_user,
        "forward_zone_file": forward_zone_file,
    }


def prompt_ansible(existing: dict) -> dict:
    """Ansible post-deploy toggle.  Disabling skips all Ansible steps."""
    console.print("\n[bold cyan]── Ansible Post-Deploy ──[/bold cyan]")
    console.print("  [dim]Run Ansible playbooks after deploy (users, packages, NTP, SNMP, etc.).[/dim]\n")
    _hint("Disable if you want a bare OS with no post-deploy configuration.")
    enabled = _ask_bool("Enable Ansible post-deploy?", existing.get("enabled", True))
    return {"enabled": enabled}


def prompt_ansible_inventory(existing: dict) -> dict:
    """Ansible inventory registration settings."""
    console.print("\n[bold cyan]── Ansible Inventory ──[/bold cyan]")
    console.print("  [dim]Register new hosts in your Ansible inventory file after deploy.[/dim]\n")

    _hint("Disable if you manage your Ansible inventory by hand.")
    enabled = _ask_bool("Enable inventory registration?", existing.get("enabled", True))
    if not enabled:
        return {"enabled": False, "provider": "flat_file", "server": "", "user": "root",
                "file": "", "group": "Linux"}

    _hint("Host that holds your Ansible inventory file.")
    server = _ask("Inventory server hostname/IP", existing.get("server", "dev.example.com"))

    _hint("SSH user labinator uses to push inventory updates.")
    user = _ask("SSH user for inventory server", existing.get("user", "root"))

    _hint("Full path to the inventory file on the remote server.")
    file = _ask("Full path to inventory file", existing.get("file", "/root/ansible/inventory/hosts"))

    _hint("Hosts are added to this group — it must already exist in the inventory.")
    group = _ask("Ansible group for new hosts (CASE-SENSITIVE)", existing.get("group", "Linux"))

    return {
        "enabled":  True,
        "provider": _ask_select("Inventory provider", ["flat_file"], default="flat_file"),
        "server":   server,
        "user":     user,
        "file":     file,
        "group":    group,
    }


def prompt_snmp(existing: dict) -> dict:
    """SNMP agent configuration applied to every deployed container/VM."""
    console.print("\n[bold cyan]── SNMP ──[/bold cyan]")
    console.print("  [dim]SNMPv2c community string and metadata written to every deployed host.[/dim]\n")

    _hint("SNMPv2c read community string written to snmpd.conf on every host.")
    community = _ask("SNMP community string", existing.get("community", "your-snmp-community"))

    _hint("Restrict SNMP queries to this CIDR — 'default' allows any source.")
    source = _ask("Allowed source (CIDR or 'default' for any)", existing.get("source", "default"))

    _hint("Written as sysLocation in snmpd.conf.")
    location = _ask("SNMP sysLocation string", existing.get("location", "Homelab"))

    _hint("Written as sysContact in snmpd.conf.")
    contact = _ask("SNMP sysContact string", existing.get("contact", "admin@example.com"))

    return {
        "community": community,
        "source":    source,
        "location":  location,
        "contact":   contact,
    }


def prompt_ntp(existing: dict) -> dict:
    """NTP server list.  At least one server is required."""
    console.print("\n[bold cyan]── NTP ──[/bold cyan]")
    console.print("  [dim]NTP servers written to chrony/timesyncd on every deployed host.[/dim]\n")
    _hint("Use pool.ntp.org for public servers, or your own internal NTP server.")
    servers = _ask_list("NTP server", existing.get("servers", []))
    if not servers:
        # validate_config() will catch this, but give the user a helpful nudge now
        console.print("  [yellow]Warning: no NTP servers entered — using pool.ntp.org as fallback.[/yellow]")
        servers = ["pool.ntp.org"]
    return {"servers": servers}


def prompt_health_check(existing: dict) -> dict:
    """Post-deploy SSH reachability check (optional)."""
    console.print("\n[bold cyan]── Health Check ──[/bold cyan]")
    console.print("  [dim]After Ansible finishes, verify the host is reachable over SSH.[/dim]")
    console.print("  [dim]A failed check prints a warning but does NOT roll back the deployment.[/dim]\n")

    _hint("Recommended — catches network/firewall issues before you declare the deploy done.")
    enabled = _ask_bool("Enable post-deploy health check?", existing.get("enabled", True))
    if not enabled:
        return {
            "enabled":         False,
            "timeout_seconds": existing.get("timeout_seconds", 30),
            "retries":         existing.get("retries", 5),
        }

    _hint("Seconds to wait per TCP connection attempt to port 22.")
    timeout_seconds = int(_ask("Per-attempt TCP timeout (seconds)",
                               existing.get("timeout_seconds", 30)))

    _hint("How many times to retry before declaring the host unreachable.")
    retries = int(_ask("Retry attempts before giving up", existing.get("retries", 5)))

    return {
        "enabled":         True,
        "timeout_seconds": timeout_seconds,
        "retries":         retries,
    }


def prompt_timezone(existing: str) -> str:
    """
    Timezone string applied to every new container/VM.
    Uses questionary.autocomplete() with the full zoneinfo database so you can
    type a partial name (e.g. 'Chicago') and get matching completions.
    """
    console.print("\n[bold cyan]── Timezone ──[/bold cyan]")
    console.print("  [dim]Set the system timezone on every deployed container/VM.[/dim]\n")
    _hint("Start typing a city or region — completions filter as you type.")

    # Build a sorted list from the system's zoneinfo database
    tz_list = sorted(available_timezones())
    current = existing or "America/Chicago"

    # Custom style: questionary's default answer color is yellow, which is nearly
    # invisible on a gray terminal background.  Cyan reads cleanly on both light and dark.
    tz_style = questionary.Style([
        ("answer",      "fg:ansired bold"),
        ("highlighted", "fg:ansired bold"),
    ])

    # autocomplete with match_middle=True lets "Chicago" match "America/Chicago"
    tz = questionary.autocomplete(
        "Timezone",
        choices=tz_list,
        default=current,
        match_middle=True,
        style=tz_style,
    ).ask()

    return tz or current


def prompt_vm(existing: dict) -> dict:
    """VM-specific hardware configuration defaults."""
    console.print("\n[bold cyan]── VM Hardware Defaults ──[/bold cyan]")
    console.print("  [dim]Default hardware settings for QEMU VMs (LXC containers ignore these).[/dim]\n")

    _hint("Storage pool for cloud images — must have 'iso' content type in Proxmox.")
    default_cloud_image_storage = _ask(
        "Default cloud image storage (blank to always prompt)",
        existing.get("default_cloud_image_storage", "local"),
    )

    _hint("x86-64-v2-AES is modern and fast; use kvm64 for maximum VM portability.")
    cpu_type = _ask_select(
        "VM CPU type",
        ["x86-64-v2-AES", "x86-64-v2", "kvm64", "host"],
        default=existing.get("cpu_type", "x86-64-v2-AES"),
    )

    _hint("q35 is the modern default; i440fx for legacy compatibility.")
    machine = _ask_select(
        "VM machine type",
        ["q35", "i440fx"],
        default=existing.get("machine", "q35"),
    )

    _hint("Use ovmf only if the OS requires UEFI boot.")
    bios = _ask_select(
        "VM BIOS",
        ["seabios", "ovmf"],
        default=existing.get("bios", "seabios"),
    )

    _hint("virtio-scsi-pci is fastest; use lsi/megasas for OS compatibility.")
    storage_controller = _ask_select(
        "Storage controller",
        ["virtio-scsi-pci", "lsi", "megasas", "pvscsi"],
        default=existing.get("storage_controller", "virtio-scsi-pci"),
    )

    _hint("virtio is fastest; e1000 for OSes without virtio drivers.")
    nic_driver = _ask_select(
        "NIC driver",
        ["virtio", "e1000", "rtl8139"],
        default=existing.get("nic_driver", "virtio"),
    )

    return {
        "default_cloud_image_storage": default_cloud_image_storage,
        "cpu_type":           cpu_type,
        "machine":            machine,
        "bios":               bios,
        "storage_controller": storage_controller,
        "nic_driver":         nic_driver,
    }


# ─────────────────────────────────────────────────────────────────────────────
# YAML renderer
# We write the config file manually rather than using yaml.dump() so that the
# output includes the same explanatory comments as config.yaml.example.
# This makes the resulting config.yaml human-friendly from the start.
# ─────────────────────────────────────────────────────────────────────────────

def _bool_str(v: bool) -> str:
    """YAML boolean — lowercase true/false."""
    return "true" if v else "false"


def _yaml_list(items: list, indent: int = 4) -> str:
    """Format a list as YAML list items at the given indentation level."""
    pad = " " * indent
    return "\n".join(f"{pad}- {item}" for item in items)


def render_config(cfg: dict) -> str:
    """
    Build the full config.yaml content as a commented string.
    The output matches config.yaml.example in structure and comment style so that
    anyone who opens the file can immediately understand every setting.
    """
    px   = cfg.get("proxmox", {})
    nd   = cfg.get("nodes", [])
    df   = cfg.get("defaults", {})
    pp   = cfg.get("package_profiles", {})
    dns  = cfg.get("dns", {})
    ans  = cfg.get("ansible", {})
    inv  = cfg.get("ansible_inventory", {})
    snmp = cfg.get("snmp", {})
    ntp  = cfg.get("ntp", {})
    hc   = cfg.get("health_check", {})
    tz   = cfg.get("timezone", "UTC")
    vm   = cfg.get("vm", {})
    pf   = cfg.get("preflight", True)

    # ── proxmox / nodes / ntp list blocks ────────────────────────────────────
    hosts_block      = _yaml_list(px.get("hosts", []))
    nodes_block      = _yaml_list(nd, indent=2)
    ntp_servers_block = _yaml_list(ntp.get("servers", []))

    # ── package profiles ─────────────────────────────────────────────────────
    if pp:
        profile_lines = []
        for pname, pdata in pp.items():
            profile_lines.append(f"  {pname}:")
            pkgs = pdata.get("packages", []) if isinstance(pdata, dict) else pdata
            tags = pdata.get("tags", []) if isinstance(pdata, dict) else []
            if pkgs:
                profile_lines.append("    packages:")
                for pkg in pkgs:
                    profile_lines.append(f"      - {pkg}")
            if tags:
                profile_lines.append("    tags:")
                for tag in tags:
                    profile_lines.append(f"      - {tag}")
        package_profiles_block = "\n".join(profile_lines)
    else:
        # No profiles defined — leave a reminder comment so the file isn't a mystery
        package_profiles_block = (
            "  # No profiles defined. Add named profiles here, e.g.:\n"
            "  # web-server:\n"
            "  #   packages:\n"
            "  #     - nginx\n"
            "  #   tags:\n"
            "  #     - WWW"
        )

    # ── assemble the full YAML string ─────────────────────────────────────────
    return f"""# ============================================================
# Proxmox LXC/VM Deploy Wizard - Configuration
# ============================================================
# Generated by configure.py — edit directly to make further changes.
# config.yaml is excluded from git — never commit it.
# ============================================================

proxmox:
  # API endpoint - any node in the cluster works (cluster shares state).
  # Use 'host' for a single node, or 'hosts' list for automatic failover.
  hosts:
{hosts_block}

  # Proxmox API user (realm included)
  user: {px.get("user", "root@pam")}

  # API Token name (created in Proxmox: Datacenter > Permissions > API Tokens)
  # Use the token ID only — NOT the full "user!tokenid" string.
  # e.g. if the full token is root@pam!vm-deploy, put: vm-deploy
  token_name: {px.get("token_name", "vm-deploy")}

  # API Token secret (UUID shown only once at creation time)
  token_secret: {px.get("token_secret", "CHANGEME")}

  # SSH key for node access (must be authorized on all Proxmox nodes as root)
  ssh_key: {px.get("ssh_key", "~/.ssh/id_rsa")}

  # Domain suffix used to construct node SSH hostnames
  # e.g., node "proxmox01" -> SSH to "proxmox01.example.com"
  node_domain: {px.get("node_domain", "example.com")}

  # Verify SSL certificate for Proxmox API (set true if using valid certs)
  verify_ssl: {_bool_str(px.get("verify_ssl", False))}

# Known cluster nodes (used for display; script also queries live from API)
nodes:
{nodes_block}

# Default values shown as suggestions in the interactive prompts
defaults:
  cpus: {df.get("cpus", 2)}
  memory_gb: {df.get("memory_gb", 4)}
  disk_gb: {df.get("disk_gb", 100)}
  vlan: {df.get("vlan", 1)}
  bridge: {df.get("bridge", "vmbr0")}
  root_password: {df.get("root_password", "changeme")}
  addusername: {df.get("addusername", "admin")}             # secondary user created on every deployed container/VM
  swap_mb: {df.get("swap_mb", 512)}
  onboot: {_bool_str(df.get("onboot", True))}
  unprivileged: {_bool_str(df.get("unprivileged", True))}          # LXC: run as unprivileged container (recommended)
  firewall_enabled: {_bool_str(df.get("firewall_enabled", False))}     # Enable Proxmox firewall on container/VM network interface
  cpu_threshold: {df.get("cpu_threshold", 0.85)}         # Node filter: skip nodes at or above this CPU load (0.0-1.0)
  ram_threshold: {df.get("ram_threshold", 0.95)}         # Node filter: skip nodes at or above this RAM usage (0.0-1.0)
  # Default search domain for new containers/VMs
  searchdomain: {df.get("searchdomain", "example.com")}
  # Default DNS servers (space-separated)
  nameserver: "{df.get("nameserver", "10.0.0.1")}"
  # Default OS template for LXC (matched by filename)
  template: {df.get("template", "ubuntu-24.04-standard_24.04-2_amd64.tar.zst")}

# Package profiles — named sets of packages representing a server role.
# Select a profile at deploy time to install a consistent toolset on every VM/LXC of that role.
# Install order: standard baseline → profile packages → extra_packages (one-off additions).
#
# NOTE: Package names are OS-specific. The names below target Debian/Ubuntu.
#       If you deploy Rocky Linux or openSUSE, adjust the names accordingly
#       (e.g. 'mariadb-server' stays the same, but 'python3-certbot-nginx' may differ).
#
# NOTE: Tag names are applied directly to Proxmox VMs/LXC containers.
#       Proxmox only allows alphanumeric characters, hyphens, and underscores in tags.
#       Spaces are NOT supported — use hyphens instead (e.g. 'build-server' not 'Build Server').
#
package_profiles:
{package_profiles_block}

# Preflight checks — run connectivity and resource checks before every deploy.
# Override per-deployment with the "preflight" field in the deployment JSON.
# The --yolo flag continues through warnings; --preflight runs checks and exits without deploying.
preflight: {_bool_str(pf)}

# DNS registration
# Set enabled: false to skip automatic DNS registration and manage records manually.
# provider: selects the DNS integration backend. Currently only 'bind' is implemented.
dns:
  enabled: {_bool_str(dns.get("enabled", True))}
  provider: {dns.get("provider", "bind")}          # bind | powerdns | technitium (future)
  server: {dns.get("server", "10.0.0.10")}             # IP of your DNS server
  ssh_user: {dns.get("ssh_user", "root")}
  # Forward zone file on the DNS server
  forward_zone_file: {dns.get("forward_zone_file", "/var/lib/bind/example.com.hosts")}
  # Reverse zone file is derived automatically from the container IP at deploy time:
  # e.g. 10.10.10.140 -> /var/lib/bind/10.10.10.in-addr.arpa.hosts

# Ansible post-deploy configuration
# Set enabled: false to skip ALL Ansible post-deploy steps — you will need to
# configure the host (users, packages, NTP, SNMP, etc.) manually.
# DNS registration and inventory update are controlled separately below.
ansible:
  enabled: {_bool_str(ans.get("enabled", True))}

# Ansible inventory update settings
# Set enabled: false to skip inventory registration.
# provider: selects the inventory backend. Currently only 'flat_file' is implemented.
ansible_inventory:
  enabled: {_bool_str(inv.get("enabled", True))}
  provider: {inv.get("provider", "flat_file")}     # flat_file | awx | semaphore (future)
  # The server that holds the master inventory file
  server: {inv.get("server", "dev.example.com")}
  # SSH user for connecting to the inventory server
  user: {inv.get("user", "root")}
  # Full path to the inventory file on the remote server
  file: {inv.get("file", "/root/ansible/inventory/hosts")}
  # Ansible group to add new hosts into (CASE-SENSITIVE)
  group: {inv.get("group", "Linux")}

# SNMP configuration (applied to all deployed containers/VMs)
snmp:
  community: {snmp.get("community", "your-snmp-community")}
  # Restrict to these source networks ('default' = any source)
  source: {snmp.get("source", "default")}
  location: {snmp.get("location", "Homelab")}
  contact: {snmp.get("contact", "admin@example.com")}

# NTP servers
ntp:
  servers:
{ntp_servers_block}

# Post-deployment health check
# After Ansible completes, verify the host is reachable and SSH is working.
# If the check fails, a warning is printed but the deployment is NOT rolled back.
health_check:
  enabled: {_bool_str(hc.get("enabled", True))}          # Set to true to enable
  timeout_seconds: {hc.get("timeout_seconds", 30)}     # Per-attempt TCP/SSH timeout
  retries: {hc.get("retries", 5)}              # Number of TCP port-22 attempts before giving up

# Timezone for new containers/VMs
timezone: {tz}

# VM-specific settings
vm:
  # Default Proxmox storage to pre-select when choosing where to store cloud images.
  # Must be a storage with content type 'iso' configured in Proxmox.
  # Cloud images are stored at {{storage_path}}/cloud-images/ — not in template/iso/,
  # so they are invisible to the Proxmox GUI ISO picker.
  # Leave blank (or remove) to always prompt without a default.
  default_cloud_image_storage: {vm.get("default_cloud_image_storage", "local")}
  cpu_type: {vm.get("cpu_type", "x86-64-v2-AES")}     # VM CPU type (x86-64-v2-AES requires host support; use kvm64 for max compatibility)
  machine: {vm.get("machine", "q35")}                # VM machine type (q35 or i440fx)
  bios: {vm.get("bios", "seabios")}               # VM BIOS (seabios or ovmf for UEFI)
  storage_controller: {vm.get("storage_controller", "virtio-scsi-pci")}  # VM storage controller (virtio-scsi-pci, lsi, megasas, pvscsi)
  nic_driver: {vm.get("nic_driver", "virtio")}          # VM NIC driver (virtio, e1000, rtl8139)
"""


# ─────────────────────────────────────────────────────────────────────────────
# Validation display
# Wraps validate_config() output into a nicely formatted Rich panel.
# ─────────────────────────────────────────────────────────────────────────────

def show_validation_results(cfg_path: Path) -> bool:
    """
    Run validate_config() and display results.
    Returns True if the config is valid, False if there are errors.
    """
    errors = validate_config(cfg_path)

    if not errors:
        console.print(Panel(
            "[green]All required fields present and valid.[/green]",
            border_style="green",
            title="[bold green]✓  Config Valid[/bold green]",
        ))
        return True

    # Build a table of errors — one row per issue
    t = Table(border_style="red", show_header=False)
    t.add_column("", style="red")
    for err in errors:
        t.add_row(f"✗  {err}")

    console.print(Panel(
        t,
        border_style="red",
        title="[bold red]Config Validation Failed[/bold red]",
    ))
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Allow --? as an alias for --help, matching the rest of labinator's scripts
    if "--?" in sys.argv:
        sys.argv[sys.argv.index("--?")] = "--help"

    parser = argparse.ArgumentParser(
        prog="configure.py",
        description="Interactive wizard for building and validating labinator config.yaml",
        epilog=(
            "Examples:\n"
            "  python3 configure.py                       # create config.yaml from scratch\n"
            "  python3 configure.py --edit                # edit existing config.yaml\n"
            "  python3 configure.py --validate            # validate and exit\n"
            "  python3 configure.py --output my.yaml      # write to a custom file\n"
            "  python3 configure.py --edit --output /tmp/config-test.yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("--help", action="help", default=argparse.SUPPRESS,
                        help="show this help message and exit")
    parser.add_argument("--edit",     action="store_true",
                        help="Pre-fill prompts from existing config (edit in place)")
    parser.add_argument("--validate", action="store_true",
                        help="Validate config.yaml and exit without prompting")
    parser.add_argument("--output",   metavar="FILE",
                        help="Write config to FILE instead of config.yaml")
    parser.add_argument("--config",   metavar="FILE",
                        help="Path to existing config to edit/validate (default: config.yaml)")
    args = parser.parse_args()

    # Resolve paths — config_path is the source to read from; output_path is where we write
    config_path = Path(args.config) if args.config else _ROOT / "config.yaml"
    output_path = Path(args.output) if args.output else _ROOT / "config.yaml"

    console.print()
    console.print(Panel.fit(
        Text("Labinator Config Wizard\nBuild or edit your config.yaml interactively",
             style="bold cyan", justify="center"),
        border_style="cyan",
    ))
    console.print()

    # ── Validate-only mode ─────────────────────────────────────────────────
    # Just check the existing config and exit — no prompts, no writing.
    if args.validate:
        console.print(f"[bold]Validating:[/bold] {config_path}\n")
        valid = show_validation_results(config_path)
        sys.exit(0 if valid else 1)

    # ── Decide whether to pre-fill from existing config ───────────────────
    # --edit: explicit pre-fill request.
    # No flag but config.yaml exists: ask the user what they want to do.
    existing_cfg: dict = {}

    if args.edit:
        # Load the existing config for pre-fill, exit cleanly if not found
        if not config_path.exists():
            console.print(f"[red]ERROR: --edit specified but {config_path} not found.[/red]")
            sys.exit(1)
        existing_cfg = _load_existing(config_path)
        console.print(f"  [dim]Pre-filling from: {config_path}[/dim]\n")

    elif config_path.exists() and not args.output:
        # Existing config found and no --output redirect — prompt to avoid accidental overwrites
        console.print(f"[yellow]An existing config.yaml was found at {config_path}.[/yellow]")
        choice = _ask_select(
            "What would you like to do?",
            choices=[
                questionary.Choice("Edit existing config (pre-fill prompts)", value="edit"),
                questionary.Choice("Start fresh (overwrite existing)",         value="fresh"),
                questionary.Choice("Exit — I changed my mind",                 value="exit"),
            ],
        )
        if choice == "exit" or choice is None:
            console.print("[yellow]Aborted.[/yellow]")
            sys.exit(0)
        if choice == "edit":
            existing_cfg = _load_existing(config_path)
            console.print(f"  [dim]Pre-filling from: {config_path}[/dim]\n")

    # ── Run all section prompts ────────────────────────────────────────────
    # Each section returns a dict (or scalar for simple values).
    # We assemble the full cfg dict at the end before writing.
    console.print("[bold]Answer each prompt — press Enter to accept the default.[/bold]\n")

    proxmox_cfg   = prompt_proxmox(existing_cfg.get("proxmox", {}))
    nodes_cfg     = prompt_nodes(existing_cfg.get("nodes", []))
    defaults_cfg  = prompt_defaults(existing_cfg.get("defaults", {}))
    profiles_cfg  = prompt_package_profiles(existing_cfg.get("package_profiles", {}))
    preflight_cfg = prompt_preflight(existing_cfg.get("preflight", True))
    dns_cfg       = prompt_dns(existing_cfg.get("dns", {}))
    ansible_cfg   = prompt_ansible(existing_cfg.get("ansible", {}))
    inv_cfg       = prompt_ansible_inventory(existing_cfg.get("ansible_inventory", {}))
    snmp_cfg      = prompt_snmp(existing_cfg.get("snmp", {}))
    ntp_cfg       = prompt_ntp(existing_cfg.get("ntp", {}))
    hc_cfg        = prompt_health_check(existing_cfg.get("health_check", {}))
    tz_cfg        = prompt_timezone(existing_cfg.get("timezone", "America/Chicago"))
    vm_cfg        = prompt_vm(existing_cfg.get("vm", {}))

    # ── Assemble full config dict ──────────────────────────────────────────
    full_cfg = {
        "proxmox":           proxmox_cfg,
        "nodes":             nodes_cfg,
        "defaults":          defaults_cfg,
        "package_profiles":  profiles_cfg,
        "preflight":         preflight_cfg,
        "dns":               dns_cfg,
        "ansible":           ansible_cfg,
        "ansible_inventory": inv_cfg,
        "snmp":              snmp_cfg,
        "ntp":               ntp_cfg,
        "health_check":      hc_cfg,
        "timezone":          tz_cfg,
        "vm":                vm_cfg,
    }

    # ── Write output ───────────────────────────────────────────────────────
    console.print()
    content = render_config(full_cfg)
    try:
        output_path.write_text(content)
        console.print(f"  [green]✓ Written:[/green] {output_path}")
    except Exception as e:
        console.print(f"[red]ERROR: Could not write {output_path}: {e}[/red]")
        sys.exit(1)

    # ── Validate the written file ──────────────────────────────────────────
    # This catches any fields the wizard missed (e.g. empty token_secret)
    # and gives the user immediate feedback before they try to run a deploy.
    console.print()
    show_validation_results(output_path)

    console.print()
    console.print(Panel(
        f"[cyan]Config saved to [bold]{output_path}[/bold][/cyan]\n\n"
        "Next steps:\n"
        "  [dim]• Run [bold]python3 deploy_lxc.py --preflight[/bold] to test your connection[/dim]\n"
        "  [dim]• Run [bold]python3 configure.py --validate[/bold] to re-check at any time[/dim]\n"
        "  [dim]• Edit [bold]config.yaml[/bold] directly to tweak package_profiles[/dim]",
        border_style="cyan",
        title="[bold cyan]Done[/bold cyan]",
    ))


def _load_existing(path: Path) -> dict:
    """
    Load an existing config.yaml for pre-fill.
    Returns an empty dict if the file can't be parsed — wizard will use defaults.
    """
    import yaml
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        console.print(f"  [yellow]Warning: could not parse {path}: {e} — using defaults.[/yellow]")
        return {}


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(1)
