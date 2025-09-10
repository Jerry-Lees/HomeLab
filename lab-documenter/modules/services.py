"""
Service database management for Lab Documenter

Handles service information lookup, enhancement, and auto-discovery with auto-updating.
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

class ServiceDatabase:
    def __init__(self, services_db_path: str = 'services.json'):
        self.services_db_path = services_db_path
        self.services_db = self.load_services_database()
        self.new_services_added = False
        self.services_updated = False
    
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
    
    def save_services_database(self) -> None:
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
    
    def add_unknown_service(self, service_name: str, enhanced_data: Optional[Dict[str, Any]] = None) -> Dict:
        """Add a new unknown service to the database"""
        timestamp = datetime.now().strftime('%Y-%m-%d')
        
        new_service = {
            "display_name": service_name.replace('.service', '').replace('_', ' ').title(),
            "description": f"Unknown service - discovered on {timestamp}",
            "category": "unknown",
            "documentation_url": None,
            "support_url": None,
            "access": "Unknown - please update this entry",
            "notes": f"AUTO-GENERATED: This service was automatically discovered. Please update with actual service information.",
            "discovered_date": timestamp,
            "last_updated": timestamp,
            "ports": [],
            "related_services": [],
            "_auto_generated": True
        }
        
        # Add enhanced data if available
        if enhanced_data:
            new_service.update(self._filter_enhanced_data(enhanced_data))
        
        self.services_db[service_name] = new_service
        self.new_services_added = True
        logger.info(f"Added unknown service '{service_name}' to database")
        
        return new_service
    
    def update_existing_service(self, service_name: str, enhanced_data: Dict[str, Any]) -> bool:
        """Update existing service with missing data"""
        if service_name not in self.services_db:
            return False
        
        existing_service = self.services_db[service_name]
        updated = False
        
        # Fields that should be updated if missing or empty
        updatable_fields = [
            'binary_path', 'command_line', 'working_directory', 'user_context',
            'unit_file_path', 'service_type', 'auto_start', 'dependencies',
            'config_files', 'package_name', 'version', 'ports'
        ]
        
        for field in updatable_fields:
            if field in enhanced_data and enhanced_data[field]:
                # Update if field is missing or empty
                if field not in existing_service or not existing_service[field]:
                    existing_service[field] = enhanced_data[field]
                    updated = True
                    logger.debug(f"Updated {service_name}.{field}: {enhanced_data[field]}")
                # Special handling for lists - merge unique items
                elif isinstance(enhanced_data[field], list) and isinstance(existing_service[field], list):
                    new_items = [item for item in enhanced_data[field] if item not in existing_service[field]]
                    if new_items:
                        existing_service[field].extend(new_items)
                        updated = True
                        logger.debug(f"Added to {service_name}.{field}: {new_items}")
        
        # Always update last_seen timestamp
        existing_service['last_seen'] = datetime.now().strftime('%Y-%m-%d')
        
        if updated:
            existing_service['last_updated'] = datetime.now().strftime('%Y-%m-%d')
            self.services_updated = True
            logger.debug(f"Updated existing service '{service_name}' with new data")
        
        return updated
    
    def _filter_enhanced_data(self, enhanced_data: Dict[str, Any]) -> Dict[str, Any]:
        """Filter and clean enhanced data for storage"""
        filtered = {}
        
        # Define which fields to store and their expected types
        field_mappings = {
            'binary_path': str,
            'command_line': str,
            'working_directory': str,
            'user_context': str,
            'unit_file_path': str,
            'service_type': str,
            'auto_start': str,
            'dependencies': list,
            'config_files': list,
            'package_name': str,
            'version': str,
            'ports': list
        }
        
        for field, expected_type in field_mappings.items():
            if field in enhanced_data and enhanced_data[field]:
                value = enhanced_data[field]
                
                # Type conversion and validation
                if expected_type == list and not isinstance(value, list):
                    if isinstance(value, str):
                        # Split string into list if needed
                        filtered[field] = [v.strip() for v in value.split(',') if v.strip()]
                elif expected_type == str and not isinstance(value, str):
                    filtered[field] = str(value)
                else:
                    filtered[field] = value
        
        return filtered
    
    def get_service_info(self, service_name: str, enhanced_data: Optional[Dict[str, Any]] = None) -> Dict:
        """Get enhanced service information from database with auto-updating"""
        # Check for exact match first
        if service_name in self.services_db:
            existing_service = self.services_db[service_name].copy()
            
            # Update with new enhanced data if provided
            if enhanced_data:
                self.update_existing_service(service_name, enhanced_data)
                # Return the updated version
                existing_service = self.services_db[service_name].copy()
            
            return existing_service
        
        # Check for partial matches
        for db_service, info in self.services_db.items():
            if (service_name.startswith(db_service) or 
                service_name.endswith(db_service) or
                db_service in service_name):
                logger.debug(f"Matched {service_name} to database entry {db_service}")
                
                # Update the matched service with enhanced data
                if enhanced_data:
                    self.update_existing_service(db_service, enhanced_data)
                
                return self.services_db[db_service].copy()
        
        # Check binary path matches for enhanced data
        if enhanced_data and enhanced_data.get('binary_path'):
            binary_path = enhanced_data['binary_path']
            for db_service, info in self.services_db.items():
                if info.get('binary_path') == binary_path:
                    logger.debug(f"Matched {service_name} to {db_service} via binary path")
                    self.update_existing_service(db_service, enhanced_data)
                    return self.services_db[db_service].copy()
        
        # No match found, create new service
        logger.debug(f"Service '{service_name}' not found in database, adding as unknown")
        return self.add_unknown_service(service_name, enhanced_data)
    
    def enhance_service(self, service_name: str, status: str, enhanced_data: Optional[Dict[str, Any]] = None) -> Dict:
        """Enhance service information with database data and runtime info"""
        base_info = {
            "name": service_name,
            "status": status
        }
        
        # Add enhanced data to base info
        if enhanced_data:
            base_info.update(enhanced_data)
        
        # Get database info (this will auto-update if needed)
        db_info = self.get_service_info(service_name, enhanced_data)
        
        # Merge database info with base info (base info takes precedence for runtime data)
        final_info = db_info.copy()
        final_info.update(base_info)
        
        return final_info
    
    def finalize(self) -> None:
        """Called at the end of data collection to save any new or updated services"""
        if self.new_services_added or self.services_updated:
            self.save_services_database()
            action = []
            if self.new_services_added:
                action.append("new services added")
            if self.services_updated:
                action.append("existing services updated")
            logger.info(f"Services database updated: {', '.join(action)}")

