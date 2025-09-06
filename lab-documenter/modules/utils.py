"""
Utility functions for Lab Documenter

Contains shared helper functions and utilities.
"""

import logging
import os
import sys

def setup_logging(log_dir: str = 'logs', verbose: bool = False, quiet: bool = False):
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

def validate_ssh_configuration(config: dict) -> None:
    """Validate SSH configuration parameters and exit on failure"""
    logger = logging.getLogger(__name__)
    
    if not config.get('ssh_user'):
        logger.error("SSH user not configured. Set it in config file or use --ssh-user")
        sys.exit(1)
    
    ssh_key_path = os.path.expanduser(config['ssh_key_path'])
    if not os.path.exists(ssh_key_path):
        logger.error(f"SSH key not found: {config['ssh_key_path']}")
        sys.exit(1)

def validate_mediawiki_configuration(config: dict) -> None:
    """Validate MediaWiki configuration parameters and exit on failure"""
    logger = logging.getLogger(__name__)
    
    if not config.get('mediawiki_api'):
        logger.error("MediaWiki API URL not configured")
        sys.exit(1)
        
    if not all([config.get('mediawiki_user'), config.get('mediawiki_password')]):
        logger.error("MediaWiki credentials not configured")
        sys.exit(1)

def get_unique_hosts(hosts: list) -> list:
    """Remove duplicates from host list while preserving order"""
    seen = set()
    unique_hosts = []
    
    for host in hosts:
        if host not in seen:
            seen.add(host)
            unique_hosts.append(host)
    
    return unique_hosts

def clean_directories(directories_to_clean: list = None, dry_run: bool = False):
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

def load_ignore_list(ignore_file: str = 'ignore.csv') -> dict:
    """Load list of hosts to ignore from CSV file"""
    logger = logging.getLogger(__name__)
    ignore_dict = {}
    
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

def filter_ignored_hosts(hosts: list, ignore_dict: dict) -> tuple:
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

def print_connection_summary(connection_failures: list):
    """Print a summary of connection failures at the end of execution"""
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
    failure_groups = {}
    for failure in connection_failures:
        reason = failure['failure_reason']
        if reason not in failure_groups:
            failure_groups[reason] = []
        failure_groups[reason].append(failure)
    
    # Print failures grouped by reason
    for reason, failures in failure_groups.items():
        logger.info(f"• {reason} ({len(failures)} device{'s' if len(failures) != 1 else ''}):")
        for failure in failures:
            original_host = failure['original_host']
            actual_hostname = failure.get('actual_hostname')
            
            if actual_hostname and actual_hostname != original_host:
                logger.info(f"  - {original_host} (hostname: {actual_hostname})")
            else:
                logger.info(f"  - {original_host}")
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

def convert_uptime_seconds(uptime_seconds) -> str:
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

