"""
Networking information management for Lab Documenter

Handles networking-related reference data, lookups, and auto-discovery.

CURRENT FEATURES:
- MAC vendor (OUI) database with auto-learning from API lookups

FUTURE EXPANSION IDEAS:
======================

DNS Cache:
- Cache reverse DNS lookups to avoid repeated queries
- Store hostname -> IP and IP -> hostname mappings
- TTL-based expiration for cache entries

Device Fingerprinting Database:
- Identify device types by MAC vendor + open ports + service signatures
- Pattern matching: "If port 8006 + port 22 + 'Proxmox' banner = Proxmox host"
- Learn common device signatures automatically
- Store confidence scores for device type detection

Network Equipment Database:
- Switch/router/AP models and their capabilities
- Default credentials warnings
- Known vulnerabilities by model
- Management port mappings

SNMP OID Mappings:
- Common OIDs for various network equipment
- Vendor-specific OID translations
- MIB information for easier SNMP polling

Port Scan History:
- Historical data on what ports are typically open on which hosts
- Detect anomalies when new ports appear
- Track port changes over time
- Generate security alerts for unexpected open ports

Common Ports/Protocols Reference:
- Beyond services.json, general networking protocol reference
- Well-known ports and their typical uses
- Protocol-specific detection patterns

Private IP Range Reference:
- RFC1918 ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Link-local (169.254.0.0/16)
- Multicast ranges
- Reserved ranges
- Helper functions for IP classification

Known Vulnerability Ports:
- Security scanning reference
- Ports commonly associated with vulnerabilities
- CVE database integration
- Alert on detection of vulnerable services

Manufacturer-Specific Information:
- Ubiquiti typical ports and services
- Synology typical ports and services  
- Proxmox typical ports and services
- Per-vendor service patterns and defaults

Hostname Pattern Analysis:
- Learn patterns in your network (*.lees-family.io)
- Auto-suggest roles based on hostname patterns
- Validate hostname conventions
- Generate naming recommendations

Network Topology Mapping:
- Store switch port connections
- VLAN assignments
- Subnet relationships
- Gateway information
- Build network diagrams from collected data

Certificate Information:
- SSL/TLS certificate tracking
- Expiration monitoring
- Issuer information
- Subject Alternative Names (SANs)

Performance Baselines:
- Normal latency ranges for hosts
- Typical bandwidth usage
- Service response times
- Detect performance degradation

Discovery Patterns:
- "If X ports + Y services + Z banner = known device type"
- Auto-classification rules
- Confidence scoring
- Pattern learning from successful identifications
"""

import json
import os
import logging
import time
import requests
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class MACVendorDatabase:
    """
    Manages MAC address vendor (OUI) lookups with auto-learning capabilities.
    
    Maintains a local database of MAC address prefixes (OUIs) and their vendors.
    When an unknown MAC is encountered, queries an online API and adds the result
    to the local database for future use.
    """
    
    def __init__(self, db_path: str = 'mac-ouis.json'):
        self.db_path = db_path
        self.database = self.load_database()
        self.modified = False
        self.api_cache = {}  # Session cache to avoid duplicate API calls
    
    def load_database(self) -> Dict:
        """Load MAC vendor database from JSON file"""
        if not os.path.exists(self.db_path):
            logger.info(f"MAC vendor database not found at {self.db_path}, will create on first save")
            return {}
        
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                db = json.load(f)
                logger.debug(f"Loaded MAC vendor database with {len(db)} OUI entries")
                return db
        except Exception as e:
            logger.error(f"Failed to load MAC vendor database from {self.db_path}: {e}")
            return {}
    
    def save_database(self) -> None:
        """Save the MAC vendor database back to file"""
        try:
            # Create backup if file exists
            if os.path.exists(self.db_path):
                backup_path = f"{self.db_path}.backup"
                import shutil
                shutil.copy2(self.db_path, backup_path)
                logger.debug(f"Created backup at {backup_path}")
            
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.database, f, indent=2, sort_keys=True)
            logger.info(f"Saved MAC vendor database with {len(self.database)} entries to {self.db_path}")
            
        except Exception as e:
            logger.error(f"Failed to save MAC vendor database: {e}")
    
    def get_oui(self, mac_address: str) -> str:
        """Extract OUI (first 6 hex digits) from MAC address"""
        if not mac_address:
            return None
        # Remove common separators and get first 6 hex digits
        oui = mac_address.replace(':', '').replace('-', '').replace('.', '')[:6].upper()
        return oui if len(oui) == 6 else None
    
    def lookup_vendor(self, mac_address: str, use_api: bool = True) -> Dict[str, str]:
        """
        Look up vendor for a MAC address
        
        Args:
            mac_address: MAC address in any common format (XX:XX:XX:XX:XX:XX, etc.)
            use_api: Whether to use online API for unknown OUIs
        
        Returns:
            Dict with keys: vendor, source, date_added (if from local db)
        """
        if not mac_address:
            return {'vendor': 'Unknown', 'source': 'invalid'}
        
        oui = self.get_oui(mac_address)
        if not oui:
            return {'vendor': 'Unknown', 'source': 'invalid'}
        
        # Check local database first
        if oui in self.database:
            entry = self.database[oui]
            return {
                'vendor': entry['vendor'],
                'source': 'local',
                'date_added': entry.get('date_added', 'unknown')
            }
        
        # Check session cache
        if oui in self.api_cache:
            return self.api_cache[oui]
        
        # Try API lookup if enabled
        if use_api:
            vendor = self._api_lookup(mac_address)
            if vendor:
                # Add to database and mark as modified
                self.add_vendor(oui, vendor, source='api')
                result = {
                    'vendor': vendor,
                    'source': 'api',
                    'date_added': datetime.now().strftime('%Y-%m-%d')
                }
                self.api_cache[oui] = result
                return result
        
        # Unknown OUI
        return {'vendor': 'Unknown', 'source': 'not_found'}
    
    def _api_lookup(self, mac_address: str) -> Optional[str]:
        """Look up MAC vendor using macvendors.com API with rate limiting"""
        try:
            # Rate limit to avoid API throttling (max 2 requests per second)
            time.sleep(0.5)
            
            url = f"https://api.macvendors.com/{mac_address}"
            response = requests.get(url, timeout=3)
            
            if response.status_code == 200:
                vendor = response.text.strip()
                logger.debug(f"API lookup for {mac_address}: {vendor}")
                return vendor
            elif response.status_code == 404:
                # MAC address not found in database
                logger.debug(f"API lookup for {mac_address}: not found")
                return None
            else:
                logger.debug(f"API lookup for {mac_address} returned status {response.status_code}")
                return None
        except Exception as e:
            logger.debug(f"API lookup failed for {mac_address}: {e}")
            return None
    
    def add_vendor(self, oui: str, vendor: str, source: str = 'manual') -> None:
        """
        Add a vendor to the database
        
        Args:
            oui: 6-character OUI (e.g., '001122')
            vendor: Vendor name
            source: How this was discovered (api, manual, import)
        """
        if not oui or len(oui) != 6:
            logger.warning(f"Invalid OUI format: {oui}")
            return
        
        oui = oui.upper()
        
        if oui in self.database:
            logger.debug(f"OUI {oui} already in database, skipping")
            return
        
        self.database[oui] = {
            'vendor': vendor,
            'date_added': datetime.now().strftime('%Y-%m-%d'),
            'source': source
        }
        
        self.modified = True
        logger.debug(f"Added OUI {oui} -> {vendor} (source: {source})")
    
    def import_ouis(self, oui_dict: Dict[str, str], source: str = 'import') -> int:
        """
        Bulk import OUIs from a dictionary
        
        Args:
            oui_dict: Dictionary of OUI -> vendor name
            source: Source of this import
        
        Returns:
            Number of new OUIs added
        """
        added_count = 0
        for oui, vendor in oui_dict.items():
            if oui not in self.database:
                self.add_vendor(oui, vendor, source)
                added_count += 1
        
        if added_count > 0:
            logger.info(f"Imported {added_count} new OUIs from {source}")
        
        return added_count
    
    def finalize(self) -> None:
        """Save database if any modifications were made"""
        if self.modified:
            self.save_database()
            logger.info(f"MAC vendor database updated with new entries")
            self.modified = False
