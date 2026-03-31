"""
macOS system information collection for Lab Documenter

Handles Apple macOS systems via SSH.
"""

import logging
from typing import Dict, List, Callable, Optional, Any

logger = logging.getLogger(__name__)


class MacCollector:
    def __init__(self, command_runner: Callable[[str], Optional[str]]):
        self.run_command = command_runner

    def detect_mac(self) -> bool:
        """Detect if the connected system is macOS/Darwin"""
        result = self.run_command('uname -s')
        return result is not None and 'Darwin' in result

    def collect_mac_info(self) -> Dict[str, Any]:
        """Collect macOS-specific hardware and system information"""
        info: Dict[str, Any] = {}

        # macOS version details
        info['product_name'] = self.run_command('sw_vers -productName') or 'macOS'
        info['product_version'] = self.run_command('sw_vers -productVersion') or 'Unknown'
        info['build_version'] = self.run_command('sw_vers -buildVersion') or 'Unknown'

        # Hardware info from system_profiler
        hw_info = self._get_hardware_info()
        if hw_info:
            info.update(hw_info)

        return info

    def _get_hardware_info(self) -> Dict[str, Any]:
        """Get hardware details from system_profiler SPHardwareDataType"""
        hw: Dict[str, Any] = {}

        result = self.run_command('system_profiler SPHardwareDataType 2>/dev/null')
        if not result:
            return hw

        for line in result.split('\n'):
            line = line.strip()
            if ':' not in line:
                continue
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip()

            if not value:
                continue

            if key == 'Model Name':
                hw['model_name'] = value
            elif key == 'Model Identifier':
                hw['model_identifier'] = value
            elif key in ('Chip', 'Processor Name'):
                hw['chip'] = value
            elif key == 'Processor Speed':
                hw['processor_speed'] = value
            elif key == 'Total Number of Cores':
                hw['total_cores'] = value
            elif key == 'Memory':
                hw['memory_installed'] = value
            elif key == 'Serial Number (system)':
                hw['serial_number'] = value

        return hw

    def get_listening_ports(self) -> List[Dict[str, Any]]:
        """Get listening TCP ports on macOS"""
        ports = []

        # Try lsof first (most informative on Mac)
        result = self.run_command(
            "lsof -iTCP -sTCP:LISTEN -n -P 2>/dev/null | awk 'NR>1 {print $1, $9}'"
        )

        # Fall back to netstat if lsof fails
        if not result:
            result = self.run_command(
                "netstat -an -p tcp 2>/dev/null | grep LISTEN | awk '{print \"unknown\", $4}'"
            )

        if not result:
            return ports

        seen: set = set()
        for line in result.split('\n'):
            parts = line.strip().split(None, 1)
            if len(parts) < 2:
                continue
            process_name = parts[0]
            address = parts[1]
            port_str = address.split(':')[-1] if ':' in address else address
            if not port_str or port_str in seen:
                continue
            seen.add(port_str)
            ports.append({
                'port': port_str,
                'process_name': process_name,
                'service_info': None
            })

        return ports[:20]
