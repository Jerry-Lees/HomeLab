"""
Lab Documenter Modules

This package contains modularized components for the Lab Documenter tool.
"""

from modules.config import CONFIG, load_config_file, update_config_from_args
from modules.services import ServiceDatabase
from modules.system import SystemCollector
from modules.network import NetworkScanner
from modules.inventory import InventoryManager
from modules.wiki import MediaWikiUpdater
from modules.documentation import DocumentationManager, generate_mediawiki_content
from modules.utils import setup_logging, clean_directories, validate_ssh_configuration, validate_mediawiki_configuration, get_unique_hosts, print_connection_summary, load_ignore_list, filter_ignored_hosts

__all__ = [
    'CONFIG',
    'load_config_file', 
    'update_config_from_args',
    'ServiceDatabase',
    'SystemCollector',
    'NetworkScanner',
    'InventoryManager',
    'MediaWikiUpdater',
    'DocumentationManager',
    'generate_mediawiki_content'
]

__version__ = '1.0.0'
