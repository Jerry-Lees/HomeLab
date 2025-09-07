"""
Inventory management for Lab Documenter

Handles host data collection and CSV file loading.
"""

import csv
import json
import os
import logging
import concurrent.futures
from datetime import datetime
from typing import Dict, List
from modules.system import SystemCollector

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
    
    def collect_all_data(self, hosts: List[str], config: Dict, max_workers: int = 10):
        """Collect data from all hosts using cascade connection approach"""
        logger.info(f"Collecting data from {len(hosts)} hosts")
    
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.collect_host_data, host, config): host 
                for host in hosts
            }
        
            for future in concurrent.futures.as_completed(futures):
                original_host = futures[future]
                try:
                    data = future.result()
                
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
                        'failure_reason': f"Collection exception: {str(e)[:80]}...",
                        'timestamp': datetime.now().isoformat()
                    }
                    self.connection_failures.append(failure_info)
                    logger.error(f"Failed to collect data for {original_host}: {e}")
    
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

