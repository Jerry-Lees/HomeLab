"""
Inventory management for Lab Documenter

Handles host data collection and CSV file loading.
"""

import csv
import json
import os
import logging
import concurrent.futures
import threading
from datetime import datetime
from typing import Dict, List
from modules.system import SystemCollector
from modules.utils import BufferedLoggingHandler

logger = logging.getLogger(__name__)

class InventoryManager:
    def __init__(self):
        self.inventory = {}
        self.connection_failures = []
    
    def load_csv_hosts(self, csv_file: str) -> List[str]:
        """Load hosts from CSV file"""
        hosts = []
        if os.path.exists(csv_file):
            try:
                with open(csv_file, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if 'hostname' in row or 'ip' in row:
                            host = row.get('hostname', row.get('ip'))
                            if host and host.strip() and not host.strip().startswith('#'):
                                hosts.append(host.strip())
            except Exception as e:
                logger.error(f"Failed to read CSV file {csv_file}: {e}")
        return hosts
    
    def update_csv_with_new_hosts(self, csv_file: str, new_hosts: List[Dict[str, str]]) -> bool:
        """
        Update CSV file with newly discovered hosts
        
        Args:
            csv_file: Path to the CSV file to update
            new_hosts: List of host dictionaries with hostname, platform_type, os_name, original_ip
        
        Returns:
            True if successful, False otherwise
        """
        if not new_hosts:
            return True
        
        try:
            # First, read the existing CSV to understand the structure
            existing_rows = []
            fieldnames = ['hostname', 'description', 'role', 'location']  # Default fieldnames
            
            if os.path.exists(csv_file):
                with open(csv_file, 'r', newline='') as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        fieldnames = list(reader.fieldnames)
                    existing_rows = list(reader)
                    
                logger.debug(f"CSV file has {len(existing_rows)} existing rows with fields: {fieldnames}")
            else:
                logger.info(f"CSV file {csv_file} doesn't exist, will create with default structure")
            
            # Prepare new rows to add
            new_rows = []
            for host_info in new_hosts:
                hostname = host_info['hostname']
                platform_type = host_info['platform_type']
                os_name = host_info['os_name']
                original_ip = host_info['original_ip']
                
                # Generate reasonable defaults based on discovered information
                description = f"Auto-discovered {platform_type} system"
                if os_name != 'Unknown':
                    description += f" ({os_name})"
                
                # Suggest role based on platform type and OS
                role = self._suggest_role(platform_type, os_name)
                
                # Default location
                location = "Auto-discovered"
                
                # Create row dict matching CSV structure
                new_row = {}
                for field in fieldnames:
                    if field.lower() in ['hostname', 'host', 'ip']:
                        new_row[field] = hostname
                    elif field.lower() in ['description', 'desc']:
                        new_row[field] = description
                    elif field.lower() in ['role', 'type', 'function']:
                        new_row[field] = role
                    elif field.lower() in ['location', 'rack', 'site']:
                        new_row[field] = location
                    elif field.lower() in ['original_ip', 'discovered_ip', 'ip_address']:
                        new_row[field] = original_ip
                    else:
                        # For any other fields, leave empty
                        new_row[field] = ""
                
                new_rows.append(new_row)
                logger.info(f"Prepared CSV entry: {hostname} ({description})")
            
            # Write updated CSV file
            with open(csv_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                # Write existing rows
                writer.writerows(existing_rows)
                
                # Write new rows
                writer.writerows(new_rows)
            
            logger.info(f"Successfully added {len(new_rows)} new hosts to {csv_file}")
            
            # Log the additions for user review
            logger.info("Added hosts:")
            for row in new_rows:
                hostname = row.get('hostname', 'Unknown')
                description = row.get('description', 'Unknown')
                logger.info(f"  - {hostname}: {description}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to update CSV file {csv_file}: {e}")
            return False
    
    def _suggest_role(self, platform_type: str, os_name: str) -> str:
        """Suggest a role based on platform type and OS name"""
        # Platform-based suggestions
        if platform_type == 'nas':
            return "Storage/NAS"
        elif platform_type == 'windows':
            if 'server' in os_name.lower():
                return "Windows Server"
            else:
                return "Windows Client"
        elif platform_type == 'linux':
            # OS-specific suggestions for Linux
            os_lower = os_name.lower()
            if 'ubuntu' in os_lower:
                return "Ubuntu Server"
            elif 'centos' in os_lower or 'rhel' in os_lower or 'red hat' in os_lower:
                return "RHEL/CentOS Server"
            elif 'debian' in os_lower:
                return "Debian Server"
            elif 'proxmox' in os_lower:
                return "Virtualization"
            elif 'truenas' in os_lower or 'freenas' in os_lower:
                return "FreeBSD NAS"
            else:
                return "Linux Server"
        else:
            return "Unknown System"
    
    def collect_all_data(self, hosts: List[str], config: Dict, max_workers: int = 10):
        """Collect data from all hosts using cascade connection approach with buffered logging"""
        logger.info(f"Collecting data from {len(hosts)} hosts")
        
        # Set up buffered logging handler for cleaner output
        buffered_handler = BufferedLoggingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(buffered_handler)
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks with thread ID tracking
                future_to_host = {}
                for host in hosts:
                    future = executor.submit(self._collect_host_with_buffering, host, config, buffered_handler)
                    future_to_host[future] = host
            
                # Process completed tasks
                for future in concurrent.futures.as_completed(future_to_host):
                    original_host = future_to_host[future]
                    try:
                        data, thread_id = future.result()
                        
                        # Flush the buffered logs for this thread now that collection is complete
                        buffered_handler.flush_thread_buffer(thread_id)
                    
                        # Track connection failures
                        if not data.get('reachable'):
                            failure_info = {
                                'original_host': original_host,
                                'actual_hostname': data.get('actual_hostname'),
                                'failure_reason': data.get('connection_failure_reason', 'Unknown failure'),
                                'timestamp': data.get('timestamp')
                            }
                            self.connection_failures.append(failure_info)
                    
                        # Determine the best key to use for this host
                        if data.get('reachable') and data.get('actual_hostname'):
                            # Use the actual hostname from the system
                            best_key = data['actual_hostname']
                            logger.info(f"Using hostname '{best_key}' instead of IP '{original_host}'")
                        else:
                            # Fall back to the original (IP or hostname from CSV)
                            best_key = original_host
                    
                        self.inventory[best_key] = data
                        logger.info(f"Collected data for {best_key} (platform: {data.get('platform_type', 'unknown')})")
                    
                    except Exception as e:
                        # Track exceptions as failures too
                        failure_info = {
                            'original_host': original_host,
                            'actual_hostname': None,
                            'failure_reason': f"Collection exception: {str(e)}",
                            'timestamp': datetime.now().isoformat()
                        }
                        self.connection_failures.append(failure_info)
                        logger.error(f"Failed to collect data for {original_host}: {e}")
        finally:
            # Remove the buffered handler when done
            root_logger.removeHandler(buffered_handler)
    
    def _collect_host_with_buffering(self, host: str, config: Dict, buffered_handler: BufferedLoggingHandler) -> tuple:
        """
        Wrapper for collect_host_data that tracks thread ID for buffered logging.
        Returns tuple of (data, thread_id) for proper log flushing.
        """
        thread_id = threading.current_thread().ident
        data = self.collect_host_data(host, config)
        return data, thread_id
    
    def collect_host_data(self, host: str, config: Dict) -> Dict:
        """Collect data from a single host using new cascade approach"""
        collector = SystemCollector(host, config)
        data = collector.collect_system_info()
        
        # Finalize services database
        collector.services_db.finalize()
        
        return data
    
    def save_inventory(self, filename: str):
        """Save inventory to JSON file"""
        try:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, 'w') as f:
                json.dump(self.inventory, f, indent=2)
            logger.info(f"Inventory saved to {filename}")
        except Exception as e:
            logger.error(f"Failed to save inventory: {e}")
