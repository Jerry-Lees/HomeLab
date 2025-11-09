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
from modules.networking_info import MACVendorDatabase

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

class BufferedLoggingHandler(logging.Handler):
    """
    A logging handler that buffers log records in memory per thread.
    Allows collection of logs and flushing them all at once to prevent interleaving.
    """
    
    def __init__(self, target_logger_name: str = None):
        super().__init__()
        self.target_logger_name = target_logger_name
        self._thread_buffers = {}
        self._lock = threading.Lock()
    
    def emit(self, record):
        """Store the log record in the thread-local buffer"""
        thread_id = threading.current_thread().ident
        
        with self._lock:
            if thread_id not in self._thread_buffers:
                self._thread_buffers[thread_id] = []
            self._thread_buffers[thread_id].append(record)
    
    def flush_thread_buffer(self, thread_id: int = None):
        """
        Flush all buffered log records for a specific thread to the actual logger.
        If thread_id is None, uses the current thread.
        """
        if thread_id is None:
            thread_id = threading.current_thread().ident
        
        with self._lock:
            if thread_id in self._thread_buffers:
                records = self._thread_buffers.pop(thread_id)
                
                # Get the root logger or specified logger
                if self.target_logger_name:
                    target_logger = logging.getLogger(self.target_logger_name)
                else:
                    target_logger = logging.getLogger()
                
                # Emit all buffered records to the actual logger
                # We need to find the original handlers (not this buffered one)
                for record in records:
                    # Call handle() on the logger which will go through all handlers except this one
                    for handler in target_logger.handlers:
                        if handler is not self and not isinstance(handler, BufferedLoggingHandler):
                            handler.handle(record)
    
    def clear_thread_buffer(self, thread_id: int = None):
        """Clear the buffer for a specific thread without flushing"""
        if thread_id is None:
            thread_id = threading.current_thread().ident
        
        with self._lock:
            if thread_id in self._thread_buffers:
                del self._thread_buffers[thread_id]

def check_port_open(host: str, port: int = 22, timeout: float = 2.0) -> bool:
    """
    Quick check if a port is open on a host.
    Much faster than waiting for SSH timeout.
    
    Args:
        host: Hostname or IP address
        port: Port number to check (default 22 for SSH)
        timeout: Connection timeout in seconds (default 2.0)
    
    Returns:
        True if port is open, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except (socket.error, socket.gaierror, socket.timeout):
        return False
    except Exception:
        return False

# Global MAC vendor database instance (initialized on first use)
_mac_vendor_db = None

def _get_mac_vendor_db() -> MACVendorDatabase:
    """Get or create the global MAC vendor database instance"""
    global _mac_vendor_db
    if _mac_vendor_db is None:
        _mac_vendor_db = MACVendorDatabase()
    return _mac_vendor_db

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
    Look up MAC address vendor information using the MAC vendor database
    
    Args:
        mac_address: MAC address in XX:XX:XX:XX:XX:XX format
        use_api: Whether to use online API lookup (requires internet)
    
    Returns:
        Dict with vendor info: {'vendor': 'Vendor Name', 'source': 'api/local/unknown'}
    """
    if not mac_address:
        return {'vendor': 'Unknown', 'source': 'unknown'}
    
    db = _get_mac_vendor_db()
    return db.lookup_vendor(mac_address, use_api=use_api)

def finalize_mac_vendor_db():
    """
    Finalize the MAC vendor database (save if modified).
    Should be called at the end of processing.
    """
    global _mac_vendor_db
    if _mac_vendor_db is not None:
        _mac_vendor_db.finalize()

def setup_logging(log_file: str = 'logs/lab-documenter.log', verbose: bool = False, quiet: bool = False):
    """Set up logging configuration"""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # Determine log level
    if quiet:
        console_level = logging.ERROR
    elif verbose:
        console_level = logging.DEBUG
    else:
        console_level = logging.INFO
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler - always detailed, always DEBUG level
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_formatter)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(detailed_formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers = []  # Clear existing handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # Suppress paramiko INFO and DEBUG messages
    logging.getLogger('paramiko').setLevel(logging.WARNING)
    logging.getLogger('paramiko.transport').setLevel(logging.WARNING)
    
    # Return a logger for the caller to use
    return logging.getLogger(__name__)
    
    return logging.getLogger(__name__)

def get_unique_hosts(hosts: List[str]) -> List[str]:
    """Return unique hosts while preserving order"""
    seen = set()
    unique = []
    for host in hosts:
        if host not in seen:
            seen.add(host)
            unique.append(host)
    return unique

def validate_ssh_configuration(config: Dict) -> None:
    """Validate SSH configuration settings"""
    logger = logging.getLogger(__name__)
    
    # Check SSH key configuration
    if 'ssh_key_path' in config and config['ssh_key_path']:
        ssh_key_path = os.path.expanduser(config['ssh_key_path'])
        if not os.path.exists(ssh_key_path):
            logger.error(f"SSH key file not found: {ssh_key_path}")
            logger.error("Please ensure the SSH key exists or update ssh_key_path in config.json")
            sys.exit(1)
    
    # Check SSH user is configured
    if not config.get('ssh_user'):
        logger.error("SSH user not configured in config.json")
        logger.error("Please set ssh_user in config.json")
        sys.exit(1)

def validate_mediawiki_configuration(config: Dict) -> None:
    """Validate MediaWiki configuration settings"""
    logger = logging.getLogger(__name__)
    
    required_fields = ['mediawiki_api', 'mediawiki_user', 'mediawiki_password']
    missing_fields = [field for field in required_fields if not config.get(field)]
    
    if missing_fields:
        logger.error(f"MediaWiki configuration incomplete. Missing: {', '.join(missing_fields)}")
        logger.error("Please configure these settings in config.json:")
        for field in missing_fields:
            logger.error(f"  - {field}")
        sys.exit(1)

def clean_directories(directories: Optional[List[str]] = None, dry_run: bool = False):
    """Clean specified directories by deleting all files"""
    logger = logging.getLogger(__name__)
    
    # Use default directories if none specified
    if directories is None:
        directories = ['documentation', 'logs']
    
    if dry_run:
        logger.info("DRY RUN: Would clean the following directories:")
        for directory in directories:
            if os.path.exists(directory):
                files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
                logger.info(f"  {directory}: {len(files)} files")
        return
    
    logger.info("Cleaning documentation and logs directories...")
    
    for directory in directories:
        try:
            if not os.path.exists(directory):
                logger.debug(f"Directory does not exist: {directory}")
                continue
                
            files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
            
            if not files:
                logger.debug(f"No files to delete in {directory}")
                continue
                
            deleted_count = 0
            for filename in files:
                file_path = os.path.join(directory, filename)
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
    
    # Finalize MAC vendor database (save any new OUIs discovered)
    finalize_mac_vendor_db()

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
