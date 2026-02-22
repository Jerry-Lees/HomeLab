"""
Cacti Export Module

Generates Cacti-compatible device import files from Lab Documenter inventory.
Cacti uses CLI scripts (add_device.php) to add devices, so this module generates
a bash script with the appropriate commands for each discovered device.

Now also generates a JSON export file for programmatic use.
"""

import json
import logging
import os
import re
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class CactiExporter:
    """
    Exports Lab Documenter inventory to Cacti-compatible formats.
    
    Generates:
    1. Bash script with add_device.php commands
    2. CSV reference file for manual import/review
    3. JSON export file for programmatic use
    """
    
    # Default template mapping - can be overridden by config
    DEFAULT_TEMPLATE_MAPPING = {
        'linux': 18,         # Local Linux Machine
        'windows': 26,       # Windows Device
        'nas': 25,           # Synology NAS
        'freebsd': 22,       # Net-SNMP Device
        'proxmox': 18,       # Local Linux Machine
        'kubernetes': 18,    # Local Linux Machine
        'docker': 18,        # Local Linux Machine
        'unknown': 16        # Generic SNMP Device
    }
    
    def __init__(self, inventory_file: str, output_dir: str = 'documentation', 
                 config: Optional[Dict[str, Any]] = None):
        """
        Initialize Cacti exporter.
        
        Args:
            inventory_file: Path to inventory.json file
            output_dir: Directory to save Cacti export files
            config: Optional configuration dictionary (from config.json)
        """
        self.inventory_file = inventory_file
        self.output_dir = output_dir
        self.inventory = {}
        
        # Load Cacti configuration from config or use defaults
        self.cacti_config = {}
        if config and 'cacti' in config:
            self.cacti_config = config['cacti']
        
        # Get template mapping from config or use defaults
        self.template_mapping = self.cacti_config.get('template_mapping', self.DEFAULT_TEMPLATE_MAPPING)
        
    def load_inventory(self) -> bool:
        """
        Load inventory from JSON file.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            if not os.path.exists(self.inventory_file):
                logger.error(f"Inventory file not found: {self.inventory_file}")
                return False
                
            with open(self.inventory_file, 'r') as f:
                self.inventory = json.load(f)
                
            logger.info(f"Loaded {len(self.inventory)} devices from inventory")
            return True
            
        except Exception as e:
            logger.error(f"Error loading inventory: {e}")
            return False
    
    def get_template_id(self, platform_type: str) -> int:
        """
        Get Cacti template ID for a given platform type.
        
        Args:
            platform_type: Platform type from inventory
            
        Returns:
            Cacti template ID
        """
        platform = platform_type.lower() if platform_type else 'unknown'
        return self.template_mapping.get(platform, self.template_mapping.get('unknown', 16))
    
    def get_primary_ip(self, host_data: Dict[str, Any]) -> str:
        """
        Extract primary IP address from host data.
        Handles string format with newlines and CIDR notation.
        
        Args:
            host_data: Host information dictionary
            
        Returns:
            Primary IP address or hostname
        """
        # Try to get from ip_addresses first
        if 'ip_addresses' in host_data and host_data['ip_addresses']:
            ip_data = host_data['ip_addresses']
            
            # Handle string format (newline-separated)
            if isinstance(ip_data, str):
                ip_list = ip_data.split('\n')
            else:
                ip_list = ip_data if isinstance(ip_data, list) else []
            
            # Process and filter IPs
            valid_ips = []
            for ip in ip_list:
                # Strip CIDR notation (e.g., "192.168.1.209/23" -> "192.168.1.209")
                ip = ip.split('/')[0].strip()
                
                # Filter out unwanted addresses
                if (ip and 
                    not ip.startswith('127.') and 
                    not ip.startswith('::1') and
                    not ip.startswith('172.17.') and  # Docker bridge
                    not ip.startswith('10.244.') and  # Kubernetes pod network
                    not ip.startswith('10.42.') and   # Kubernetes service network
                    not ip.startswith('10.200.') and  # Additional K8s network
                    not ip.startswith('10.220.') and  # Additional K8s network
                    not ip.startswith('10.10.') and   # K8s service ranges
                    not ip.startswith('fe80:')):      # IPv6 link-local
                    valid_ips.append(ip)
            
            if valid_ips:
                return valid_ips[0]
        
        # Fallback to hostname - check if hostname is actually an IP
        hostname = host_data.get('hostname', '')
        actual_hostname = host_data.get('actual_hostname', '')
        
        # If hostname looks like an IP, use it
        if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', hostname):
            return hostname
        
        # Otherwise use actual_hostname
        return actual_hostname if actual_hostname else hostname
    
    def get_fqdn_or_ip(self, host_data: Dict[str, Any]) -> str:
        """
        Get FQDN if available, otherwise fallback to IP address.
        Prefers DNS-resolvable names over IP addresses.
        
        Args:
            host_data: Host information dictionary
            
        Returns:
            FQDN or IP address
        """
        # Try to get FQDN first
        fqdn = host_data.get('actual_hostname', host_data.get('hostname', ''))
        
        # Check if it's a valid FQDN (has dots, but not an IP address)
        if fqdn and '.' in fqdn and not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', fqdn):
            return fqdn
        
        # Fall back to IP address
        return self.get_primary_ip(host_data)
    
    def sanitize_description(self, description: str) -> str:
        """
        Sanitize description for Cacti compatibility.
        
        Args:
            description: Raw description string
            
        Returns:
            Sanitized description safe for shell scripts
        """
        # Remove or escape problematic characters
        description = description.replace('"', '\\"')
        description = description.replace('$', '\\$')
        description = description.replace('`', '\\`')
        return description
    
    def build_device_data(self, hostname: str, host_data: Dict[str, Any], 
                         cacti_path: str, snmp_community: str, 
                         snmp_version: int) -> Optional[Dict[str, Any]]:
        """
        Build complete device data structure for Cacti import.
        
        Args:
            hostname: Device hostname from inventory
            host_data: Device data from inventory
            cacti_path: Path to Cacti CLI
            snmp_community: SNMP community string
            snmp_version: SNMP version
            
        Returns:
            Dictionary with all device data for Cacti, or None if invalid
        """
        if not host_data.get('reachable', False):
            return None
        
        description = host_data.get('description', hostname)
        fqdn_or_ip = self.get_fqdn_or_ip(host_data)
        
        # Skip if we don't have a valid IP or hostname
        if not fqdn_or_ip or fqdn_or_ip == 'unknown':
            return None
        
        platform_type = host_data.get('platform_type', 'unknown')
        template_id = self.get_template_id(platform_type)
        
        # Build device data structure
        device = {
            'hostname': hostname,
            'description': description,
            'ip': fqdn_or_ip,
            'template_id': template_id,
            'template_name': self._get_template_name(template_id),
            'platform_type': platform_type,
            'os_name': host_data.get('os_release', {}).get('pretty_name', 'Unknown'),
            'snmp': {
                'version': snmp_version,
                'community': snmp_community,
                'port': self.cacti_config.get('snmp_port', 161),
                'timeout': self.cacti_config.get('snmp_timeout', 500)
            },
            'availability': {
                'method': 'pingsnmp',
                'ping_method': 'icmp'
            },
            'metadata': {
                'reachable': True,
                'collected_at': datetime.now().isoformat(),
                'primary_ip': self.get_primary_ip(host_data)
            }
        }
        
        return device
    
    def _get_template_name(self, template_id: int) -> str:
        """
        Get template name from template ID (reverse lookup).
        
        Args:
            template_id: Cacti template ID
            
        Returns:
            Template name or 'Unknown'
        """
        template_names = {
            18: 'Local Linux Machine',
            26: 'Windows Device',
            25: 'Synology NAS',
            22: 'Net-SNMP Device',
            16: 'Generic SNMP Device'
        }
        return template_names.get(template_id, f'Template {template_id}')
    
    def generate_json_export(self, cacti_path: str = '/var/www/html/cacti/cli',
                            snmp_community: str = 'public',
                            snmp_version: int = 2) -> str:
        """
        Generate JSON export file with all device data for Cacti.
        
        Args:
            cacti_path: Path to Cacti CLI directory
            snmp_community: SNMP community string
            snmp_version: SNMP version (1, 2, or 3)
            
        Returns:
            Path to generated JSON file
        """
        if not self.inventory:
            logger.error("No inventory loaded")
            return None
        
        # Override with config values if available
        cacti_path = self.cacti_config.get('cli_path', cacti_path)
        snmp_community = self.cacti_config.get('snmp_community', snmp_community)
        snmp_version = self.cacti_config.get('snmp_version', snmp_version)
        
        # Create output directory if needed
        os.makedirs(self.output_dir, exist_ok=True)
        
        json_path = os.path.join(self.output_dir, 'cacti_export.json')
        
        try:
            devices = []
            skipped = []
            
            # Process each device
            for hostname, data in sorted(self.inventory.items()):
                device_data = self.build_device_data(
                    hostname, data, cacti_path, snmp_community, snmp_version
                )
                
                if device_data:
                    devices.append(device_data)
                else:
                    skipped.append({
                        'hostname': hostname,
                        'reason': 'unreachable' if not data.get('reachable') else 'no_valid_ip'
                    })
            
            # Build complete export structure
            export_data = {
                'metadata': {
                    'generated_at': datetime.now().isoformat(),
                    'generated_by': 'Lab Documenter Cacti Exporter',
                    'version': '1.0',
                    'cacti_cli_path': cacti_path,
                    'total_devices': len(devices),
                    'skipped_devices': len(skipped)
                },
                'cacti_settings': {
                    'cli_path': cacti_path,
                    'snmp': {
                        'version': snmp_version,
                        'community': snmp_community,
                        'port': self.cacti_config.get('snmp_port', 161),
                        'timeout': self.cacti_config.get('snmp_timeout', 500)
                    },
                    'template_mapping': self.template_mapping
                },
                'devices': devices,
                'skipped': skipped
            }
            
            # Write JSON file
            with open(json_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            logger.info(f"Generated Cacti JSON export: {json_path}")
            logger.info(f"JSON contains {len(devices)} devices")
            if skipped:
                logger.info(f"Skipped {len(skipped)} devices")
            
            return json_path
            
        except Exception as e:
            logger.error(f"Error generating JSON export: {e}")
            return None
    
    def generate_bash_script(self, cacti_path: str = '/var/www/html/cacti/cli', 
                            snmp_community: str = 'public',
                            snmp_version: int = 2) -> str:
        """
        Generate bash script with add_device.php commands.
        
        Args:
            cacti_path: Path to Cacti CLI directory
            snmp_community: SNMP community string
            snmp_version: SNMP version (1, 2, or 3)
            
        Returns:
            Path to generated script
        """
        if not self.inventory:
            logger.error("No inventory loaded")
            return None
        
        # Override with config values if available
        cacti_path = self.cacti_config.get('cli_path', cacti_path)
        snmp_community = self.cacti_config.get('snmp_community', snmp_community)
        snmp_version = self.cacti_config.get('snmp_version', snmp_version)
        
        # Create output directory if needed
        os.makedirs(self.output_dir, exist_ok=True)
        
        script_path = os.path.join(self.output_dir, 'cacti_import.sh')
        
        try:
            with open(script_path, 'w') as f:
                # Write script header
                f.write("#!/bin/bash\n")
                f.write("#\n")
                f.write("# Cacti Device Import Script\n")
                f.write(f"# Generated by Lab Documenter on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("#\n")
                f.write("# This script uses Cacti's add_device.php CLI tool to import devices.\n")
                f.write(f"# Adjust CACTI_PATH if your Cacti CLI directory is different.\n")
                f.write("#\n")
                f.write("# Usage: sudo bash cacti_import.sh\n")
                f.write("#\n\n")
                
                f.write(f"CACTI_PATH=\"{cacti_path}\"\n")
                f.write(f"SNMP_COMMUNITY=\"{snmp_community}\"\n")
                f.write(f"SNMP_VERSION=\"{snmp_version}\"\n\n")
                
                f.write("# Check if Cacti CLI exists\n")
                f.write("if [ ! -f \"$CACTI_PATH/add_device.php\" ]; then\n")
                f.write("    echo \"ERROR: Cacti CLI not found at $CACTI_PATH/add_device.php\"\n")
                f.write("    echo \"Please update CACTI_PATH in this script\"\n")
                f.write("    exit 1\n")
                f.write("fi\n\n")
                
                f.write("echo \"Starting Cacti device import...\"\n")
                f.write("echo \"========================================\"\n\n")
                
                # Counter for statistics
                device_count = 0
                skipped_count = 0
                
                # Generate add_device.php commands for each reachable host
                for hostname, data in sorted(self.inventory.items()):
                    if not data.get('reachable', False):
                        logger.debug(f"Skipping unreachable host: {hostname}")
                        continue
                    
                    # Extract device information
                    description = self.sanitize_description(
                        data.get('description', hostname)
                    )
                    
                    # Use FQDN if available, fallback to IP
                    fqdn_or_ip = self.get_fqdn_or_ip(data)
                    
                    # Skip if we don't have a valid IP or hostname
                    if not fqdn_or_ip or fqdn_or_ip == 'unknown':
                        logger.warning(f"Skipping {hostname}: no valid IP address or FQDN found")
                        skipped_count += 1
                        continue
                    
                    template_id = self.get_template_id(data.get('platform_type', 'unknown'))
                    
                    device_count += 1
                    
                    # Add comment with device info
                    f.write(f"# Device: {hostname}\n")
                    f.write(f"# Platform: {data.get('platform_type', 'unknown')}\n")
                    f.write(f"# OS: {data.get('os_release', {}).get('pretty_name', 'Unknown')}\n")
                    
                    # Generate add_device.php command
                    f.write(f"echo \"Adding device: {hostname}\"\n")
                    f.write(f"php -q \"$CACTI_PATH/add_device.php\" \\\n")
                    f.write(f"    --description=\"{description}\" \\\n")
                    f.write(f"    --ip=\"{fqdn_or_ip}\" \\\n")
                    f.write(f"    --template={template_id} \\\n")
                    f.write(f"    --community=\"$SNMP_COMMUNITY\" \\\n")
                    f.write(f"    --version=\"$SNMP_VERSION\"\n\n")
                
                # Write footer
                f.write("echo \"========================================\"\n")
                f.write(f"echo \"Completed import of {device_count} devices\"\n")
                if skipped_count > 0:
                    f.write(f"echo \"Skipped {skipped_count} devices with no valid IP or FQDN\"\n")
                f.write("echo \"Check Cacti web interface to verify devices were added\"\n")
            
            # Make script executable
            os.chmod(script_path, 0o755)
            
            logger.info(f"Generated Cacti import script: {script_path}")
            logger.info(f"Script contains {device_count} devices")
            if skipped_count > 0:
                logger.info(f"Skipped {skipped_count} devices with no valid IP or FQDN")
            
            return script_path
            
        except Exception as e:
            logger.error(f"Error generating bash script: {e}")
            return None
    
    def generate_csv(self) -> str:
        """
        Generate CSV reference file with device information.
        
        This CSV can be used for manual review or import into other tools.
        
        Returns:
            Path to generated CSV file
        """
        if not self.inventory:
            logger.error("No inventory loaded")
            return None
        
        # Create output directory if needed
        os.makedirs(self.output_dir, exist_ok=True)
        
        csv_path = os.path.join(self.output_dir, 'cacti_devices.csv')
        
        try:
            with open(csv_path, 'w') as f:
                # Write CSV header
                f.write("hostname,fqdn_or_ip,description,platform_type,os_name,template_id,reachable\n")
                
                # Write device data
                for hostname, data in sorted(self.inventory.items()):
                    # Get device information
                    fqdn_or_ip = self.get_fqdn_or_ip(data)
                    description = data.get('description', hostname)
                    platform_type = data.get('platform_type', 'unknown')
                    os_name = data.get('os_release', {}).get('pretty_name', 'Unknown')
                    template_id = self.get_template_id(platform_type)
                    reachable = 'Yes' if data.get('reachable', False) else 'No'
                    
                    # Escape commas and quotes in fields
                    description = description.replace('"', '""')
                    os_name = os_name.replace('"', '""')
                    
                    # Write CSV row
                    f.write(f'"{hostname}","{fqdn_or_ip}","{description}",')
                    f.write(f'"{platform_type}","{os_name}",{template_id},"{reachable}"\n')
            
            logger.info(f"Generated Cacti CSV reference: {csv_path}")
            return csv_path
            
        except Exception as e:
            logger.error(f"Error generating CSV: {e}")
            return None
    
    def export_all(self, cacti_path: str = '/var/www/html/cacti/cli',
                   snmp_community: str = 'public',
                   snmp_version: int = 2) -> Dict[str, str]:
        """
        Export all Cacti formats.
        
        Args:
            cacti_path: Path to Cacti CLI directory
            snmp_community: SNMP community string
            snmp_version: SNMP version (1, 2, or 3)
            
        Returns:
            Dictionary with paths to generated files
        """
        results = {}
        
        # Load inventory
        if not self.load_inventory():
            return results
        
        # Generate JSON export
        json_path = self.generate_json_export(cacti_path, snmp_community, snmp_version)
        if json_path:
            results['json'] = json_path
        
        # Generate bash script
        script_path = self.generate_bash_script(cacti_path, snmp_community, snmp_version)
        if script_path:
            results['bash_script'] = script_path
        
        # Generate CSV reference
        csv_path = self.generate_csv()
        if csv_path:
            results['csv'] = csv_path
        
        return results


def export_cacti_format(inventory_file: str, output_dir: str = 'documentation',
                       cacti_path: str = '/var/www/html/cacti/cli',
                       snmp_community: str = 'public',
                       snmp_version: int = 2,
                       config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """
    Convenience function to export Cacti formats.
    
    Args:
        inventory_file: Path to inventory.json
        output_dir: Directory for output files
        cacti_path: Path to Cacti CLI directory
        snmp_community: SNMP community string
        snmp_version: SNMP version
        config: Optional configuration dictionary
        
    Returns:
        Dictionary with paths to generated files
    """
    exporter = CactiExporter(inventory_file, output_dir, config)
    return exporter.export_all(cacti_path, snmp_community, snmp_version)

