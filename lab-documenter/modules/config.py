"""
Configuration module for Lab Documenter

Contains configuration constants and utilities.
"""

import os
import json
import logging

# Default configuration
CONFIG = {
    'ssh_user': 'your_ssh_user',
    'ssh_key_path': '~/.ssh/id_rsa',
    'network_ranges': ['192.168.1.0/24'],  # Changed to list for multiple networks
    'ssh_timeout': 5,
    'max_workers': 10,
    'output_file': 'documentation/inventory.json',
    'csv_file': 'servers.csv',
    'mediawiki_api': 'http://your-wiki.local/api.php',
    'mediawiki_user': 'bot_user',
    'mediawiki_password': 'bot_password',
    'mediawiki_index_page': 'Server Documentation'
}

def load_config_file(config_path: str) -> dict:
    """Load configuration from JSON file"""
    logger = logging.getLogger(__name__)
    config = CONFIG.copy()
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
            logger.info(f"Loaded configuration from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load config file {config_path}: {e}")
            raise
    else:
        logger.warning(f"Configuration file {config_path} not found, using defaults")
    
    # Handle backward compatibility: convert single network_range to network_ranges list
    if 'network_range' in config and 'network_ranges' not in config:
        config['network_ranges'] = [config['network_range']]
        logger.info(f"Converted single network_range to network_ranges: {config['network_ranges']}")
    elif 'network_ranges' not in config:
        config['network_ranges'] = CONFIG['network_ranges']
    
    # Ensure network_ranges is always a list
    if not isinstance(config['network_ranges'], list):
        config['network_ranges'] = [config['network_ranges']]
    
    return config

def update_config_from_args(config: dict, args) -> dict:
    """Update configuration with command line arguments"""
    if args.csv:
        config['csv_file'] = args.csv
    if args.output:
        config['output_file'] = args.output
    if args.network:
        # Handle multiple networks from command line (comma-separated)
        if ',' in args.network:
            config['network_ranges'] = [net.strip() for net in args.network.split(',')]
        else:
            config['network_ranges'] = [args.network]
    if args.ssh_user:
        config['ssh_user'] = args.ssh_user
    if args.ssh_key:
        config['ssh_key_path'] = args.ssh_key
    if args.ssh_timeout is not None:
        config['ssh_timeout'] = args.ssh_timeout
    if args.workers is not None:
        config['max_workers'] = args.workers
    if args.wiki_api:
        config['mediawiki_api'] = args.wiki_api
    if args.wiki_user:
        config['mediawiki_user'] = args.wiki_user
    if args.wiki_password:
        config['mediawiki_password'] = args.wiki_password
    if args.wiki_index_page:
        config['mediawiki_index_page'] = args.wiki_index_page
    
    return config

