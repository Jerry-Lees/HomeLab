"""
Service database management for Lab Documenter

Handles service information lookup, enhancement, and auto-discovery.
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

class ServiceDatabase:
    def __init__(self, services_db_path: str = 'services.json'):
        self.services_db_path = services_db_path
        self.services_db = self.load_services_database()
        self.new_services_added = False
    
    def load_services_database(self) -> Dict:
        """Load services database from JSON file"""
        if not os.path.exists(self.services_db_path):
            logger.info(f"Services database not found at {self.services_db_path}, creating new one")
            return {}
        
        try:
            with open(self.services_db_path, 'r', encoding='utf-8') as f:
                db = json.load(f)
                logger.debug(f"Loaded services database with {len(db)} entries")
                return db
        except Exception as e:
            logger.error(f"Failed to load services database from {self.services_db_path}: {e}")
            return {}
    
    def save_services_database(self):
        """Save the services database back to file"""
        try:
            if os.path.exists(self.services_db_path):
                backup_path = f"{self.services_db_path}.backup"
                import shutil
                shutil.copy2(self.services_db_path, backup_path)
                logger.debug(f"Created backup at {backup_path}")
            
            with open(self.services_db_path, 'w', encoding='utf-8') as f:
                json.dump(self.services_db, f, indent=2, sort_keys=True)
            logger.info(f"Saved services database to {self.services_db_path}")
            
        except Exception as e:
            logger.error(f"Failed to save services database: {e}")
    
    def add_unknown_service(self, service_name: str, process_info: str = None) -> Dict:
        """Add a new unknown service to the database"""
        timestamp = datetime.now().strftime('%Y-%m-%d')
        
        detected_ports = []
        if process_info:
            import re
            port_matches = re.findall(r':(\d+)', process_info)
            detected_ports = list(set(port_matches))
        
        new_service = {
            "display_name": service_name.replace('.service', '').replace('_', ' ').title(),
            "description": f"Unknown service - discovered on {timestamp}",
            "category": "unknown",
            "documentation_url": None,
            "support_url": None,
            "access": "Unknown - please update this entry",
            "notes": f"AUTO-GENERATED: This service was automatically discovered. Please update with actual service information.",
            "discovered_date": timestamp,
            "ports": detected_ports,
            "related_services": [],
            "_auto_generated": True
        }
        
        self.services_db[service_name] = new_service
        self.new_services_added = True
        logger.info(f"Added unknown service '{service_name}' to database")
        
        return new_service
    
    def get_service_info(self, service_name: str, process_info: str = None) -> Dict:
        """Get enhanced service information from database"""
        if service_name in self.services_db:
            return self.services_db[service_name]
        
        for db_service, info in self.services_db.items():
            if (service_name.startswith(db_service) or 
                service_name.endswith(db_service) or
                db_service in service_name):
                logger.debug(f"Matched {service_name} to database entry {db_service}")
                return info
        
        if process_info:
            for db_service, info in self.services_db.items():
                if db_service.lower() in process_info.lower():
                    logger.debug(f"Matched {service_name} via process info to {db_service}")
                    return info
        
        logger.debug(f"Service '{service_name}' not found in database, adding as unknown")
        return self.add_unknown_service(service_name, process_info)
    
    def enhance_service(self, service_name: str, status: str, process_info: str = None) -> Dict:
        """Enhance service information with database data"""
        base_info = {
            "name": service_name,
            "status": status,
            "process_info": process_info
        }
        
        db_info = self.get_service_info(service_name, process_info)
        base_info.update(db_info)
        
        return base_info
    
    def finalize(self):
        """Called at the end of data collection to save any new services"""
        if self.new_services_added:
            self.save_services_database()
            logger.info("Services database updated with new unknown services")

