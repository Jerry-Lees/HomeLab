"""
labinator.profiles — Package profile resolution, LXC features, tag colors, TTL helpers.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent

_TTL_RE = re.compile(r'^(\d+)(m|h|d|w)$')


def parse_ttl(ttl_str: str) -> timedelta:
    """Parse a TTL string (e.g. '7d', '24h', '2w', '30m') into a timedelta.
    Raises ValueError on invalid format."""
    m = _TTL_RE.match(ttl_str.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid TTL '{ttl_str}': use a number followed by m/h/d/w "
            "(e.g. 30m, 24h, 7d, 2w)"
        )
    n, unit = int(m.group(1)), m.group(2)
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n),
            "d": timedelta(days=n),   "w": timedelta(weeks=n)}[unit]


def expires_at_from_ttl(ttl_str: str) -> str:
    """Return an ISO 8601 UTC timestamp string for now + ttl_str."""
    return (datetime.now(timezone.utc) + parse_ttl(ttl_str)).isoformat()


def resolve_profile(profile_name: str, profiles: dict) -> tuple[list, list]:
    """Return (packages, tag_names) for a named profile.

    Supports flat-list format (packages only), dict format with 'packages'
    and optional 'tags' keys, and dict-format tags ({name, color}).
    Always returns tag names as plain strings.
    """
    profile = profiles.get(profile_name)
    if not profile:
        return [], []
    if isinstance(profile, list):
        return list(profile), []
    raw_tags = profile.get("tags", [])
    tag_names = [t["name"] if isinstance(t, dict) else t for t in raw_tags]
    return list(profile.get("packages", [])), tag_names


def resolve_lxc_features(profile_name: str, profiles: dict) -> list:
    """Return list of LXC feature flag strings for a named profile.

    e.g. ["nesting=1", "keyctl=1"] or ["mount=nfs"].
    Returns [] if no profile, flat-list profile, or no lxc_features key.
    """
    profile = profiles.get(profile_name)
    if not profile or isinstance(profile, list):
        return []
    return list(profile.get("lxc_features", []))


def resolve_tag_colors(profile_name: str, profiles: dict) -> dict:
    """Return {tag_name: hex_color} for tags in the named profile that define a color.

    Tags may be plain strings (no color) or dicts with 'name' and 'color' keys.
    """
    profile = profiles.get(profile_name)
    if not profile or isinstance(profile, list):
        return {}
    colors = {}
    for tag in profile.get("tags", []):
        if isinstance(tag, dict) and "name" in tag and "color" in tag:
            colors[tag["name"]] = tag["color"]
    return colors


def features_list_to_proxmox_str(features: list) -> str:
    """Convert a list of LXC feature flag strings to a Proxmox-compatible features string.

    Handles boolean flags (e.g. 'nesting=1') and mount types (e.g. 'mount=nfs').
    Multiple mount types are merged: ['mount=nfs', 'mount=cifs'] → 'mount=nfs;cifs'.
    """
    if not features:
        return ""
    bool_flags = []
    mount_types = []
    for f in features:
        if f.startswith("mount="):
            mount_types.append(f.split("=", 1)[1])
        else:
            bool_flags.append(f)
    parts = list(bool_flags)
    if mount_types:
        parts.append("mount=" + ";".join(mount_types))
    return ",".join(parts)
