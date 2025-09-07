"""
Utility functions for Lab Documenter

Contains shared helper functions and utilities.
"""

import logging
import os
import sys
import socket
from typing import Optional, List, Tuple, Dict, Union
import subprocess
import requests
import json
import time
import threading
_thread_local = threading.local()

def set_device_context(hostname: str):
    """Set the current device being processed for this thread"""
    _thread_local.current_device = hostname

def get_device_context() -> Optional[str]:
    """Get the current device being processed for this thread"""
    return getattr(_thread_local, 'current_device', None)

def clear_device_context():
    """Clear the device context for this thread"""
    if hasattr(_thread_local, 'current_device'):
        delattr(_thread_local, 'current_device')

class DeviceContextFilter(logging.Filter):
    """Logging filter to add device context to log messages"""
    
    def filter(self, record):
        device = get_device_context()
        if device:
            # Only add context to messages that don't already have block headers
            if not any(marker in record.getMessage() for marker in ['===', 'STARTING DATA COLLECTION', 'FINISHED DATA COLLECTION']):
                record.msg = f"[{device}] {record.msg}"
        return True

# Cache for MAC vendor lookups to avoid repeated API calls
_mac_vendor_cache = {}

def get_mac_address(ip_address: str) -> Optional[str]:
    """Get MAC address for an IP address using ARP table"""
    try:
        # Try different ARP commands for different systems
        commands = [
            f'arp -n {ip_address}',  # Linux/Unix
            f'arp {ip_address}',     # Alternative format
            f'ip neigh show {ip_address}'  # Modern Linux
        ]
        
        for cmd in commands:
            try:
                result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout:
                    output = result.stdout.strip()
                    
                    # Parse ARP output to extract MAC address
                    # Look for MAC address pattern: XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX
                    import re
                    mac_pattern = r'([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}'
                    match = re.search(mac_pattern, output)
                    if match:
                        mac = match.group(0)
                        # Normalize to colon format
                        return mac.replace('-', ':').upper()
                        
            except (subprocess.TimeoutExpired, subprocess.SubprocessError):
                continue
                
        return None
    except Exception as e:
        return None

def lookup_mac_vendor(mac_address: str, use_api: bool = True) -> Dict[str, str]:
    """
    Look up MAC address vendor information
    
    Args:
        mac_address: MAC address in XX:XX:XX:XX:XX:XX format
        use_api: Whether to use online API lookup (requires internet)
    
    Returns:
        Dict with vendor info: {'vendor': 'Vendor Name', 'source': 'api/local/unknown'}
    """
    if not mac_address:
        return {'vendor': 'Unknown', 'source': 'unknown'}
    
    # Check cache first
    if mac_address in _mac_vendor_cache:
        return _mac_vendor_cache[mac_address]
    
    vendor_info = {'vendor': 'Unknown', 'source': 'unknown'}
    
    # Extract OUI (first 3 octets)
    oui = mac_address[:8].replace(':', '').upper()
    
    # Try local OUI lookup first (basic common vendors)
    local_vendor = _get_local_vendor(oui)
    if local_vendor:
        vendor_info = {'vendor': local_vendor, 'source': 'local'}
    
    # Try API lookup if enabled and no local match
    if use_api and vendor_info['vendor'] == 'Unknown':
        api_vendor = _api_vendor_lookup(mac_address)
        if api_vendor:
            vendor_info = {'vendor': api_vendor, 'source': 'api'}
    
    # Cache the result
    _mac_vendor_cache[mac_address] = vendor_info
    return vendor_info

def _get_local_vendor(oui: str) -> Optional[str]:
    """Get vendor from local OUI database (common vendors only)"""
    # Common OUI mappings - you can expand this
    local_ouis = {
        '000D93': 'Apple',
        '001B63': 'Apple', 
        '001EC2': 'Apple',
        '002608': 'Apple',
        '002332': 'Apple',
        '002436': 'Apple',
        '002500': 'Apple',
        '0025BC': 'Apple',
        '0026BB': 'Apple',
        'F0F61C': 'Apple',
        'F82793': 'Apple',
        '001F3F': 'Apple',
        '0050E4': 'Apple',
        '006171': 'Apple',
        '0003BA': 'Sun Microsystems',
        '080027': 'VirtualBox',
        '0C0267': 'VirtualBox', 
        '005056': 'VMware',
        '000C29': 'VMware',
        '001C14': 'VMware',
        '0003FF': 'Microsoft',
        '000D3A': 'Microsoft',
        '001DD8': 'Microsoft',
        '0017FA': 'Microsoft',
        '7C1E52': 'Microsoft',
        '001B44': 'Microsoft',
        '00155D': 'Microsoft',
        '3C970E': 'Microsoft',
        '000B97': 'Intel',
        '001B21': 'Intel',
        '0013CE': 'Intel',
        '001517': 'Intel',
        '0016E6': 'Intel',
        '001E68': 'Intel',
        '0021F6': 'Intel',
        '002186': 'Intel',
        '002241': 'Intel',
        '0024D7': 'Intel',
        'E4B318': 'Intel',
        '7CD1C3': 'Intel',
        '000C76': 'Micro-Star International',
        '001E58': 'WD',
        '001B2F': 'WD',
        '0090A9': 'Western Digital',
        '001CF0': 'WD',
        '001143': 'Dell',
        '0014C2': 'Dell',
        '00188B': 'Dell',
        '001EC9': 'Dell',
        '002219': 'Dell',
        '0024E8': 'Dell',
        '34159E': 'Dell',
        'B0838F': 'Dell',
        '001E0B': 'Cisco',
        '00036B': 'Cisco',
        '0007EB': 'Cisco',
        '000B46': 'Cisco',
        '000C85': 'Cisco',
        '000FE2': 'Cisco',
        '0013C4': 'Cisco',
        '001643': 'Cisco',
        '00D0C0': 'Cisco',
        '001B0C': 'HP',
        '001CC4': 'HP',
        '001E0B': 'HP',
        '002264': 'HP',
        '0024A8': 'HP',
        '0025B3': 'HP',
        '001A4B': 'HP',
        '009027': 'HP',
        '001B78': 'HP',
        '000423': 'HP',
        '001560': 'ASUSTek',
        '0013D4': 'ASUSTek',
        '001EA6': 'ASUSTek',
        '002522': 'ASUSTek',
        '0026B6': 'ASUSTek',
        '001F3F': 'ASUSTek',
        '001D60': 'ASUSTek',
        '000272': 'ASUSTek',
        '000EA6': 'ASUSTek',
        '70F395': 'ASUSTek',
        '0025D3': 'Apple',
        '68A86D': 'Apple',
        'A81B5A': 'Apple',
        'D49A20': 'Apple',
        'E48B7F': 'Apple',
        'F07960': 'Apple',
        'F4F15A': 'Apple',
        'F86214': 'Apple',
        '4C32CC': 'Apple',
        '5C59D6': 'Apple',
        '6CBB13': 'Apple',
        '84B153': 'Apple',
        '843835': 'Apple',
        '8C7712': 'Apple',
        '90840D': 'Apple',
        '9027E4': 'Apple',
        '9803D8': 'Apple',
        'A4C361': 'Apple',
        'AC3C0B': 'Apple',
        'B09FBA': 'Apple',
        'B4F0AB': 'Apple',
        'BCE143': 'Apple',
        'C0D012': 'Apple',
        'C42AD0': 'Apple',
        'C4618B': 'Apple',
        'C83DDC': 'Apple',
        'CC25EF': 'Apple',
        'D022BE': 'Apple',
        'D02598': 'Apple',
        'D0929E': 'Apple',
        'D4619D': 'Apple',
        'D8CF9C': 'Apple',
        'DC2B2A': 'Apple',
        'E0B52D': 'Apple',
        'E425E7': 'Apple',
        'E49A79': 'Apple',
        'E498D1': 'Apple',
        'E4C63D': 'Apple',
        'E8040B': 'Apple',
        'EC3586': 'Apple',
        'EC8892': 'Apple',
        'F40F24': 'Apple',
        'F41BA1': 'Apple',
        'F45FD4': 'Apple',
        'F4D108': 'Apple',
        'F4F951': 'Apple',
        'F86FC1': 'Apple',
        'FC253F': 'Apple',
        '001124': 'Synology',
        '001132': 'Synology',
        '001743': 'Synology',
        '0011D8': 'Synology',
        '0E8E68': 'Synology',
        '001EF7': 'QNAP',
        '245EBE': 'QNAP',
        '24F5AA': 'QNAP',
        '000C29': 'VMware',
        '005056': 'VMware',
        '001C14': 'VMware',
        '000569': 'VMware',
        '0050C2': 'VMware',
        '0A0027': 'VirtualBox'
    }
    
    return local_ouis.get(oui[:6])  # Use first 6 chars (3 octets)

def _api_vendor_lookup(mac_address: str) -> Optional[str]:
    """Look up vendor using online API"""
    try:
        # Use macvendors.com API (free, no rate limit mentioned)
        url = f"https://api.macvendors.com/{mac_address}"
        
        # Add a small delay to be respectful
        time.sleep(0.1)
        
        response = requests.get(url, timeout=3)
        if response.status_code == 200:
            vendor = response.text.strip()
            if vendor and vendor != "Not Found":
                return vendor
        elif response.status_code == 429:  # Rate limited
            time.sleep(1)  # Wait a bit longer
            
    except (requests.RequestException, requests.Timeout):
        pass
    
    return None

def format_device_summary_with_mac(host: str, failure_reason: str, use_mac_lookup: bool = True) -> str:
    """Format device summary with MAC address and vendor info"""
    formatted_host = format_host_with_dns(host)
    
    if use_mac_lookup:
        mac_address = get_mac_address(host.split('@')[-1] if '@' in host else host)
        if mac_address:
            vendor_info = lookup_mac_vendor(mac_address, use_api=True)
            vendor_text = f" [{vendor_info['vendor']}]" if vendor_info['vendor'] != 'Unknown' else ''
            return f"{formatted_host} (MAC: {mac_address}{vendor_text})"
        else:
            return f"{formatted_host} (MAC: Unknown)"
    else:
        return formatted_host

def setup_logging(log_dir: str = 'logs', verbose: bool = False, quiet: bool = False) -> logging.Logger:
    """Set up logging configuration after potential clean operations"""
    os.makedirs(log_dir, exist_ok=True)

    if quiet:
        level = logging.ERROR
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(log_dir, 'lab-documenter.log')),
            logging.StreamHandler()
        ]
    )
    
    return logging.getLogger(__name__)

def validate_ssh_configuration(config: Dict[str, Union[str, int, List[str]]]) -> None:
    """Validate SSH configuration parameters and exit on failure"""
    logger = logging.getLogger(__name__)
    
    if not config.get('ssh_user'):
        logger.error("SSH user not configured. Set it in config file or use --ssh-user")
        sys.exit(1)
    
    ssh_key_path = os.path.expanduser(str(config['ssh_key_path']))
    if not os.path.exists(ssh_key_path):
        logger.error(f"SSH key not found: {config['ssh_key_path']}")
        sys.exit(1)

def validate_mediawiki_configuration(config: Dict[str, Union[str, int, List[str]]]) -> None:
    """Validate MediaWiki configuration parameters and exit on failure"""
    logger = logging.getLogger(__name__)
    
    if not config.get('mediawiki_api'):
        logger.error("MediaWiki API URL not configured")
        sys.exit(1)
        
    if not all([config.get('mediawiki_user'), config.get('mediawiki_password')]):
        logger.error("MediaWiki credentials not configured")
        sys.exit(1)

def get_unique_hosts(hosts: List[str]) -> List[str]:
    """Remove duplicates from host list while preserving order"""
    seen = set()
    unique_hosts = []
    
    for host in hosts:
        if host not in seen:
            seen.add(host)
            unique_hosts.append(host)
    
    return unique_hosts

def clean_directories(directories_to_clean: Optional[List[str]] = None, dry_run: bool = False) -> None:
    """Clean files from specified directories"""
    logger = logging.getLogger(__name__)
    
    if directories_to_clean is None:
        directories_to_clean = ['./documentation', './logs']
    
    for directory in directories_to_clean:
        if not os.path.exists(directory):
            logger.info(f"Directory {directory} does not exist, skipping")
            continue
            
        try:
            files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
            
            if not files:
                logger.info(f"No files found in {directory}")
                continue
                
            if dry_run:
                logger.info(f"Would delete {len(files)} files from {directory}:")
                for file in files:
                    logger.info(f"  - {os.path.join(directory, file)}")
            else:
                logger.info(f"Deleting {len(files)} files from {directory}")
                deleted_count = 0
                for file in files:
                    file_path = os.path.join(directory, file)
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.debug(f"Deleted: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to delete {file_path}: {e}")
                
                logger.info(f"Successfully deleted {deleted_count}/{len(files)} files from {directory}")
                
        except Exception as e:
            logger.error(f"Error processing directory {directory}: {e}")

def load_ignore_list(ignore_file: str = 'ignore.csv') -> Dict[str, str]:
    """Load list of hosts to ignore from CSV file"""
    logger = logging.getLogger(__name__)
    ignore_dict: Dict[str, str] = {}
    
    if not os.path.exists(ignore_file):
        logger.debug(f"Ignore file {ignore_file} does not exist, no hosts will be ignored")
        return ignore_dict
    
    try:
        import csv
        with open(ignore_file, 'r') as f:
            reader = csv.DictReader(f)
            
            # Handle different possible column names
            fieldnames = reader.fieldnames
            if not fieldnames:
                logger.warning(f"Ignore file {ignore_file} is empty or has no headers")
                return ignore_dict
                
            # Look for hostname/IP column (flexible naming)
            host_column = None
            notes_column = None
            
            for field in fieldnames:
                field_lower = field.lower().strip()
                if any(keyword in field_lower for keyword in ['ip', 'hostname', 'host', 'address']):
                    host_column = field
                elif any(keyword in field_lower for keyword in ['notes', 'note', 'description', 'reason', 'comment']):
                    notes_column = field
            
            if not host_column:
                logger.warning(f"Could not find hostname/IP column in {ignore_file}. Expected column names: 'IP or hostname', 'hostname', 'IP', etc.")
                return ignore_dict
            
            for row in reader:
                host = row.get(host_column, '').strip()
                notes = row.get(notes_column, '') if notes_column else 'No notes'
                
                # Skip empty rows or rows starting with #
                if host and not host.startswith('#'):
                    ignore_dict[host] = notes.strip() if notes else 'No notes'
        
        if ignore_dict:
            logger.info(f"Loaded {len(ignore_dict)} hosts to ignore from {ignore_file}")
        else:
            logger.debug(f"No hosts found to ignore in {ignore_file}")
            
    except Exception as e:
        logger.error(f"Failed to load ignore file {ignore_file}: {e}")
    
    return ignore_dict

def filter_ignored_hosts(hosts: List[str], ignore_dict: Dict[str, str]) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Filter out ignored hosts from the host list"""
    logger = logging.getLogger(__name__)
    
    if not ignore_dict:
        return hosts, []
    
    filtered_hosts = []
    ignored_hosts = []
    
    for host in hosts:
        if host in ignore_dict:
            ignored_hosts.append((host, ignore_dict[host]))
            logger.debug(f"Ignoring host {host}: {ignore_dict[host]}")
        else:
            filtered_hosts.append(host)
    
    if ignored_hosts:
        logger.info(f"Ignoring {len(ignored_hosts)} hosts as specified in ignore.csv:")
        for host, notes in ignored_hosts:
            logger.info(f"  - {host} ({notes})")
    
    return filtered_hosts, ignored_hosts

def reverse_dns_lookup(ip_address: str, timeout: float = 2.0) -> Optional[str]:
    """Perform reverse DNS lookup with timeout"""
    try:
        # Set socket timeout for DNS lookup
        socket.setdefaulttimeout(timeout)
        hostname, _, _ = socket.gethostbyaddr(ip_address)
        return hostname
    except (socket.herror, socket.gaierror, socket.timeout, OSError):
        # All these exceptions indicate DNS lookup failed
        return None
    finally:
        # Reset socket timeout to default
        socket.setdefaulttimeout(None)

def format_host_with_dns(host: str) -> str:
    """Format host with reverse DNS lookup if it's an IP address"""
    # Check if the host looks like an IP address
    import re
    ip_pattern = r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    
    if re.match(ip_pattern, host):
        # It's an IP address, try reverse DNS
        hostname = reverse_dns_lookup(host)
        if hostname and hostname != host:
            # Got a hostname, format as "IP (hostname)"
            return f"{host} ({hostname})"
        else:
            # No hostname found or same as IP, just return IP
            return host
    else:
        # Not an IP address, return as-is
        return host

def print_connection_summary(connection_failures: List[Dict[str, str]]) -> None:
    """Print a summary of connection failures with MAC addresses at the end of execution"""
    logger = logging.getLogger(__name__)
    
    if not connection_failures:
        logger.info("All hosts were successfully connected to!")
        return
    
    failure_count = len(connection_failures)
    logger.info(f"{'='*60}")
    logger.info(f"CONNECTION SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Failed to connect to {failure_count} device{'s' if failure_count != 1 else ''}:")
    logger.info("")
    
    # Group failures by reason for better organization
    failure_groups: Dict[str, List[Dict[str, str]]] = {}
    for failure in connection_failures:
        reason = failure['failure_reason']
        if reason not in failure_groups:
            failure_groups[reason] = []
        failure_groups[reason].append(failure)
    
    # Print failures grouped by reason with MAC addresses
    for reason, failures in failure_groups.items():
        logger.info(f"• {reason} ({len(failures)} device{'s' if len(failures) != 1 else ''}):")
        for failure in failures:
            original_host = failure['original_host']
            actual_hostname = failure.get('actual_hostname')
            
            # Get MAC address and vendor info for failed device
            ip_only = original_host.split('@')[-1] if '@' in original_host else original_host
            
            # Extract just IP if it has port info
            if ':' in ip_only:
                ip_only = ip_only.split(':')[0]
            
            mac_address = get_mac_address(ip_only)
            vendor_text = ""
            if mac_address:
                vendor_info = lookup_mac_vendor(mac_address, use_api=True)
                if vendor_info['vendor'] != 'Unknown':
                    vendor_text = f" [{vendor_info['vendor']}]"
                mac_text = f" (MAC: {mac_address}{vendor_text})"
            else:
                mac_text = " (MAC: Unknown)"
            
            # Format the host with reverse DNS lookup
            formatted_host = format_host_with_dns(original_host)
            
            if actual_hostname and actual_hostname != original_host:
                logger.info(f"  - {formatted_host} (hostname: {actual_hostname}){mac_text}")
            else:
                logger.info(f"  - {formatted_host}{mac_text}")
        logger.info("")
    
    logger.info(f"{'='*60}")

def bytes_to_gb(bytes_str: str) -> str:
    """Convert bytes string to GB with appropriate formatting"""
    try:
        bytes_value = int(bytes_str)
        gb_value = bytes_value / (1024 ** 3)
        
        if gb_value >= 1000:
            return f"{gb_value/1024:.1f}TB"
        elif gb_value >= 1:
            return f"{gb_value:.1f}GB"
        else:
            return f"{gb_value*1024:.0f}MB"
    except (ValueError, TypeError):
        return bytes_str  # Return original if conversion fails

def convert_uptime_seconds(uptime_seconds: Union[str, int, float]) -> str:
    """Convert uptime in seconds to human readable format"""
    try:
        seconds = int(float(str(uptime_seconds)))
        
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        remaining_seconds = seconds % 60
        
        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if remaining_seconds > 0 or not parts:  # Show seconds if no other parts or if only seconds
            parts.append(f"{remaining_seconds} second{'s' if remaining_seconds != 1 else ''}")
        
        # Return first 2-3 most significant parts
        return ", ".join(parts[:3])
        
    except (ValueError, TypeError):
        return str(uptime_seconds)  # Return original if conversion fails

