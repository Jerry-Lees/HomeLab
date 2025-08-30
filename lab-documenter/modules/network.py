"""
Network scanning utilities for Lab Documenter

Handles network discovery and host reachability testing.
"""

import ipaddress
import subprocess
import logging
import concurrent.futures
from typing import List

logger = logging.getLogger(__name__)

class NetworkScanner:
    def __init__(self, network_ranges: list, max_workers: int = 10):
        self.network_ranges = network_ranges if isinstance(network_ranges, list) else [network_ranges]
        self.max_workers = max_workers
    
    def scan_networks(self) -> List[str]:
        """Scan multiple network ranges for live hosts"""
        all_live_hosts = []
        
        for network_range in self.network_ranges:
            logger.info(f"Scanning network range: {network_range}")
            live_hosts = self.scan_single_network(network_range)
            all_live_hosts.extend(live_hosts)
        
        # Remove duplicates in case networks overlap
        unique_hosts = list(set(all_live_hosts))
        logger.info(f"Found {len(unique_hosts)} unique live hosts across {len(self.network_ranges)} network ranges")
        return unique_hosts
    
    def scan_single_network(self, network_range: str) -> List[str]:
        """Scan a single network range for live hosts"""
        live_hosts = []
        
        try:
            network = ipaddress.IPv4Network(network_range, strict=False)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self.ping_host, str(ip)): str(ip) for ip in network.hosts()}
                
                for future in concurrent.futures.as_completed(futures):
                    ip = futures[future]
                    if future.result():
                        live_hosts.append(ip)
        
        except Exception as e:
            logger.error(f"Network scanning failed for {network_range}: {e}")
        
        logger.info(f"Found {len(live_hosts)} live hosts in {network_range}")
        return live_hosts
    
    # Keep the old method name for backward compatibility
    def scan_network(self) -> List[str]:
        """Scan network ranges for live hosts (backward compatibility method)"""
        return self.scan_networks()
    
    def ping_host(self, ip: str) -> bool:
        """Ping a single host"""
        try:
            result = subprocess.run(['ping', '-c', '1', '-W', '1', ip], 
                                  capture_output=True, timeout=2)
            return result.returncode == 0
        except:
            return False

