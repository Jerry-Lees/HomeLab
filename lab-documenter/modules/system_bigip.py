"""
F5 BIG-IP system information collection for Lab Documenter

Handles F5 BIG-IP appliances via SSH + tmsh.
"""

import logging
import re
from typing import Dict, List, Callable, Optional, Any

logger = logging.getLogger(__name__)


class BigIPCollector:
    def __init__(self, command_runner: Callable[[str], Optional[str]]):
        self.run_command = command_runner

    def detect_bigip(self) -> bool:
        """Detect if the connected system is an F5 BIG-IP"""
        result = self.run_command('test -f /usr/bin/tmsh && echo "bigip"')
        if result and 'bigip' in result:
            return True
        result = self.run_command('test -f /config/bigip.conf && echo "bigip"')
        return result is not None and 'bigip' in result

    def collect_bigip_info(self) -> Dict[str, Any]:
        """Collect BIG-IP configuration and status information"""
        info: Dict[str, Any] = {}

        info['version_info'] = self._get_version_info()
        info['virtual_servers'] = self._get_virtual_servers()
        info['pools'] = self._get_pools()
        info['interfaces'] = self._get_interfaces()
        info['ha_status'] = self._get_ha_status()

        return info

    def _get_version_info(self) -> Dict[str, Any]:
        """Get BIG-IP version and product information"""
        version: Dict[str, Any] = {}
        result = self.run_command('tmsh show sys version 2>/dev/null')
        if not result:
            return version

        for line in result.split('\n'):
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            key = parts[0].strip().lower()
            value = parts[1].strip()
            if key == 'product':
                version['product'] = value
            elif key == 'version':
                version['version'] = value
            elif key == 'build':
                version['build'] = value
            elif key == 'edition':
                version['edition'] = value
            elif key == 'date':
                version['date'] = value

        return version

    def _get_virtual_servers(self) -> List[Dict[str, Any]]:
        """Get LTM virtual server list with key details"""
        virtual_servers: List[Dict[str, Any]] = []

        # Grab just the fields we care about from the full list
        result = self.run_command(
            'tmsh list ltm virtual 2>/dev/null | '
            'grep -E "^ltm virtual |^    destination |^    pool |^    ip-protocol "'
        )
        if not result:
            return virtual_servers

        current_vs: Optional[Dict[str, Any]] = None
        for line in result.split('\n'):
            line_s = line.strip()

            m = re.match(r'^ltm virtual (/\S+)', line_s)
            if m:
                if current_vs:
                    virtual_servers.append(current_vs)
                name = m.group(1)
                current_vs = {
                    'name': name,
                    'partition': name.lstrip('/').split('/')[0] if '/' in name.lstrip('/') else 'Common'
                }
                continue

            if current_vs is None:
                continue

            if line_s.startswith('destination '):
                current_vs['destination'] = line_s.split(None, 1)[1]
            elif line_s.startswith('pool '):
                current_vs['pool'] = line_s.split(None, 1)[1]
            elif line_s.startswith('ip-protocol '):
                current_vs['protocol'] = line_s.split(None, 1)[1]

        if current_vs:
            virtual_servers.append(current_vs)

        return virtual_servers

    def _get_pools(self) -> List[Dict[str, Any]]:
        """Get LTM pool list with member counts"""
        pools: List[Dict[str, Any]] = []

        result = self.run_command('tmsh list ltm pool 2>/dev/null')
        if not result:
            return pools

        current_pool: Optional[Dict[str, Any]] = None
        in_members = False
        member_count = 0

        for line in result.split('\n'):
            line_s = line.strip()

            m = re.match(r'^ltm pool (/\S+)', line_s)
            if m:
                if current_pool is not None:
                    current_pool['member_count'] = member_count
                    pools.append(current_pool)
                name = m.group(1)
                current_pool = {
                    'name': name,
                    'partition': name.lstrip('/').split('/')[0] if '/' in name.lstrip('/') else 'Common'
                }
                in_members = False
                member_count = 0
                continue

            if current_pool is None:
                continue

            if line_s == 'members {':
                in_members = True
            elif in_members and re.match(r'^/\S+:\d+\s+\{', line_s):
                member_count += 1
            elif line_s.startswith('monitor '):
                current_pool['monitor'] = line_s.split(None, 1)[1]
            elif line_s.startswith('load-balancing-mode '):
                current_pool['lb_method'] = line_s.split(None, 1)[1]

        if current_pool is not None:
            current_pool['member_count'] = member_count
            pools.append(current_pool)

        return pools

    def _get_interfaces(self) -> List[Dict[str, Any]]:
        """Get physical interface status from tmsh"""
        interfaces: List[Dict[str, Any]] = []

        result = self.run_command('tmsh show net interface 2>/dev/null')
        if not result:
            return interfaces

        # Skip header lines; data lines look like:
        # 1.1    up    100TX-FD  ...
        for line in result.split('\n'):
            line = line.strip()
            if not line or line.startswith('-') or line.startswith('Net') or line.startswith('Sys'):
                continue
            parts = line.split()
            if len(parts) >= 2 and re.match(r'^\d+\.\d+$', parts[0]):
                interfaces.append({
                    'name': parts[0],
                    'status': parts[1] if len(parts) > 1 else 'Unknown',
                    'media': parts[2] if len(parts) > 2 else 'Unknown'
                })

        return interfaces

    def _get_ha_status(self) -> Dict[str, Any]:
        """Get high availability / failover status"""
        ha: Dict[str, Any] = {}

        result = self.run_command('tmsh show sys failover 2>/dev/null')
        if not result:
            return ha

        for line in result.split('\n'):
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].lower() == 'failover':
                ha['state'] = parts[1].strip()
                break

        return ha
