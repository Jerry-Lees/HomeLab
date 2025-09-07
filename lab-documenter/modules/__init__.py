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
from modules.system_kubernetes import KubernetesCollector
from modules.system_proxmox import ProxmoxCollector

# Import new collectors with fallback handling
try:
    from modules.system_windows import WindowsCollector
except ImportError:
    WindowsCollector = None

try:
    from modules.system_nas import NASCollector
except ImportError:
    NASCollector = None

from modules.utils import (
    setup_logging, clean_directories, validate_ssh_configuration, 
    validate_mediawiki_configuration, get_unique_hosts, print_connection_summary, 
    load_ignore_list, filter_ignored_hosts, bytes_to_gb, convert_uptime_seconds
)

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
    'generate_mediawiki_content',
    'KubernetesCollector',
    'ProxmoxCollector',
    'WindowsCollector',
    'NASCollector',
    'setup_logging',
    'clean_directories',
    'validate_ssh_configuration',
    'validate_mediawiki_configuration',
    'get_unique_hosts',
    'print_connection_summary',
    'load_ignore_list',
    'filter_ignored_hosts',
    'bytes_to_gb',
    'convert_uptime_seconds'
]

__version__ = '1.1.0'  # Bump version for multi-platform support

