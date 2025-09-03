#!/usr/bin/env python3
"""
Lab Documenter
Discovers and documents servers, VMs, containers, and services in your home lab
"""

import logging
import argparse
import os
import sys
import json
from datetime import datetime

# Import modules
from modules.config import CONFIG, load_config_file, update_config_from_args
from modules.network import NetworkScanner
from modules.inventory import InventoryManager
from modules.wiki import MediaWikiUpdater

from modules.documentation import DocumentationManager, generate_mediawiki_content, generate_wiki_index_content
from modules.utils import clean_directories, print_connection_summary, load_ignore_list, filter_ignored_hosts

def setup_logging(verbose=False, quiet=False):
    """Set up logging after potential clean operations"""
    log_dir = 'logs'
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

def main():
    parser = argparse.ArgumentParser(
        description='Lab Documenter - Discovers and documents servers, VMs, containers, and services in your home lab',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --scan                              Scan network and collect data from all discovered hosts
  %(prog)s --csv-only                          Only scan servers listed in CSV file  
  %(prog)s --scan --update-wiki                Scan network and update MediaWiki pages
  %(prog)s --update-wiki-index                 Create or update the wiki server index page
  %(prog)s --clean                             Delete all files in ./documentation and ./logs directories
  %(prog)s --clean --dry-run                   Show what files would be deleted without deleting them
  %(prog)s --clean --scan                      Clean directories then scan and rebuild documentation
  %(prog)s --config custom.json --scan        Use custom configuration file
  %(prog)s --csv servers.csv --scan           Use custom CSV file for server list
  %(prog)s --output inventory.json --scan     Save results to custom output file
  %(prog)s --dry-run --scan                   Show what would be done without making changes
  %(prog)s --verbose --scan                   Enable detailed logging output
  %(prog)s --network 10.0.0.0/8 --scan       Scan custom network range
  %(prog)s --network "192.168.1.0/24,10.0.0.0/24" --scan  Scan multiple network ranges
  %(prog)s --ssh-user admin --scan            Use specific SSH username
  %(prog)s --workers 20 --scan                Use 20 concurrent workers for faster scanning

Configuration:
  The script looks for config.json in the current directory by default.
  All configuration options can be overridden via command line arguments.
  
  Default file locations:
    Config file: ./config.json
    CSV file: ./servers.csv  
    Output file: ./documentation/inventory.json
    Log file: ./logs/lab-documenter.log
        ''')
    
    # Main operation modes
    mode_group = parser.add_argument_group('Operation Modes')
    mode_group.add_argument('--scan', action='store_true', 
                           help='Scan network for live hosts and collect data')
    mode_group.add_argument('--csv-only', action='store_true', 
                           help='Only scan hosts listed in CSV file (skip network scan)')
    mode_group.add_argument('--update-wiki', action='store_true', 
                           help='Update MediaWiki pages with collected data')
    mode_group.add_argument('--update-wiki-index', action='store_true',
                           help='Create or update the wiki server index page')
    mode_group.add_argument('--dry-run', action='store_true', 
                           help='Show what would be done without making changes')
    mode_group.add_argument('--clean', action='store_true',
                           help='Delete all files in ./documentation and ./logs directories')
    
    # File paths
    file_group = parser.add_argument_group('File Paths')
    file_group.add_argument('--config', metavar='FILE', default='config.json',
                           help='Configuration file path (default: config.json)')
    file_group.add_argument('--csv', metavar='FILE', default=None,
                           help='CSV file containing server list (default: from config or servers.csv)')
    file_group.add_argument('--output', metavar='FILE', default=None,
                           help='Output JSON file path (default: from config or documentation/inventory.json)')
    
    # Network settings
    network_group = parser.add_argument_group('Network Settings')
    network_group.add_argument('--network', metavar='CIDR', default=None,
                              help='Network range(s) to scan. Single: 192.168.1.0/24 or Multiple: "192.168.1.0/24,10.0.0.0/24"')
    network_group.add_argument('--ssh-user', metavar='USER', default=None,
                              help='SSH username for server connections')
    network_group.add_argument('--ssh-key', metavar='FILE', default=None,
                              help='SSH private key file path')
    network_group.add_argument('--ssh-timeout', metavar='SECONDS', type=int, default=None,
                              help='SSH connection timeout in seconds')
    
    # Performance settings
    perf_group = parser.add_argument_group('Performance Settings')
    perf_group.add_argument('--workers', metavar='N', type=int, default=None,
                           help='Number of concurrent workers for scanning')
    
    # MediaWiki settings
    wiki_group = parser.add_argument_group('MediaWiki Settings')
    wiki_group.add_argument('--wiki-api', metavar='URL', default=None,
                           help='MediaWiki API URL')
    wiki_group.add_argument('--wiki-user', metavar='USER', default=None,
                           help='MediaWiki username')
    wiki_group.add_argument('--wiki-password', metavar='PASS', default=None,
                           help='MediaWiki password')
    wiki_group.add_argument('--wiki-index-page', metavar='TITLE', default=None,
                           help='Title for the wiki server index page')
    
    # Output settings
    output_group = parser.add_argument_group('Output Settings')
    output_group.add_argument('--verbose', '-v', action='store_true',
                             help='Enable verbose logging output')
    output_group.add_argument('--quiet', '-q', action='store_true',
                             help='Suppress all output except errors')
    
    # Add support for --? as alias for --help
    if '--?' in sys.argv:
        sys.argv[sys.argv.index('--?')] = '--help'
    
    args = parser.parse_args()
    
    # Load configuration file
    config = load_config_file(args.config)
    
    # Override config with command line arguments
    config = update_config_from_args(config, args)
    
    # Update global CONFIG for modules
    CONFIG.update(config)
    
    # Handle clean operation FIRST (before logging setup)
    if args.clean:
        print(f"Cleaning documentation and logs directories...")
        clean_directories(dry_run=args.dry_run)
        
        # If only cleaning (no other operations), exit after cleaning
        if not (args.scan or args.csv_only or args.update_wiki or args.update_wiki_index):
            print("Clean operation completed")
            return
    
    # NOW set up logging (after potential clean operation)
    logger = setup_logging(verbose=args.verbose, quiet=args.quiet)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
        networks_display = ', '.join(config['network_ranges'])
        logger.info(f"Would use config: SSH user={config['ssh_user']}, Networks={networks_display}, Workers={config['max_workers']}")
    
    # Handle wiki index update only
    if args.update_wiki_index and not (args.scan or args.csv_only):
        if not config.get('mediawiki_api'):
            logger.error("MediaWiki API URL not configured")
            sys.exit(1)
        if not all([config.get('mediawiki_user'), config.get('mediawiki_password')]):
            logger.error("MediaWiki credentials not configured")
            sys.exit(1)
        
        # Load existing inventory to create index
        if os.path.exists(config['output_file']):
            with open(config['output_file'], 'r') as f:
                inventory = json.load(f)
            
            wiki_updater = MediaWikiUpdater(
                config['mediawiki_api'],
                config['mediawiki_user'],
                config['mediawiki_password']
            )
            
            index_page_title = config.get('mediawiki_index_page', 'Server Documentation')
            index_content = generate_wiki_index_content(inventory)
            
            if args.dry_run:
                logger.info(f"DRY RUN: Would create/update wiki index page: {index_page_title}")
                return
            
            if wiki_updater.create_index_page(index_page_title, index_content):
                logger.info(f"Successfully updated wiki index page: {index_page_title}")
            else:
                logger.error(f"Failed to update wiki index page: {index_page_title}")
        else:
            logger.error(f"No inventory file found at {config['output_file']}. Run a scan first.")
        return
    
    # Validate required settings for scanning operations
    if args.scan or args.csv_only:
        if not config.get('ssh_user'):
            logger.error("SSH user not configured. Set it in config file or use --ssh-user")
            sys.exit(1)
        
        if not os.path.exists(os.path.expanduser(config['ssh_key_path'])):
            logger.error(f"SSH key not found: {config['ssh_key_path']}")
            sys.exit(1)

    inventory_manager = InventoryManager()
    
    # Show configuration if verbose
    if args.verbose:
        logger.debug("Current configuration:")
        for key, value in config.items():
            if 'password' in key.lower():
                logger.debug(f"  {key}: {'*' * len(str(value)) if value else 'None'}")
            else:
                logger.debug(f"  {key}: {value}")
    
    # Determine which hosts to scan
    hosts = []
    
    if not args.csv_only and args.scan:
        if args.dry_run:
            networks_display = ', '.join(config['network_ranges'])
            logger.info(f"Would scan networks: {networks_display}")
        else:
            scanner = NetworkScanner(config['network_ranges'], config['max_workers'])
            scanned_hosts = scanner.scan_network()
            hosts.extend(scanned_hosts)
    
    # Always check for CSV hosts
    csv_file = config['csv_file']
    if os.path.exists(csv_file):
        csv_hosts = inventory_manager.load_csv_hosts(csv_file)
        hosts.extend(csv_hosts)
        if csv_hosts:
            logger.info(f"Loaded {len(csv_hosts)} hosts from {csv_file}")
    else:
        logger.warning(f"CSV file not found: {csv_file}")
    
    # Remove duplicates
    hosts = list(set(hosts))
    
    # Load ignore list and filter out ignored hosts
    ignore_dict = load_ignore_list('ignore.csv')
    hosts, ignored_hosts = filter_ignored_hosts(hosts, ignore_dict)
    
    if not hosts:
        logger.error("No hosts found to scan after filtering ignored hosts")
        logger.info("Try: --scan to scan network, or add hosts to CSV file")
        sys.exit(1)
    
    logger.info(f"Will process {len(hosts)} hosts: {', '.join(hosts[:5])}" + 
                (f" and {len(hosts)-5} more" if len(hosts) > 5 else ""))
    
    if args.dry_run:
        logger.info("DRY RUN: Would collect data from hosts, save inventory, and create documentation files")
        return
    
    # Collect data
    inventory_manager.collect_all_data(
        hosts, 
        config['ssh_user'], 
        config['ssh_key_path'], 
        config['ssh_timeout'], 
        config['max_workers']
    )
    
    # Save inventory
    inventory_manager.save_inventory(config['output_file'])
    
    # Always create local documentation files
    docs_manager = DocumentationManager('documentation')
    docs_manager.save_all_documentation(inventory_manager.inventory)
    
    # Update MediaWiki only if requested
    if args.update_wiki and config.get('mediawiki_api'):
        if not all([config.get('mediawiki_user'), config.get('mediawiki_password')]):
            logger.error("MediaWiki credentials not configured")
            sys.exit(1)
            
        wiki_updater = MediaWikiUpdater(
            config['mediawiki_api'],
            config['mediawiki_user'],
            config['mediawiki_password']
        )
        
        updated_count = 0
        for host, data in inventory_manager.inventory.items():
            if data.get('reachable'):
                content = generate_mediawiki_content(data)
                if wiki_updater.update_page(f"Server:{host}", content):
                    updated_count += 1
                    logger.info(f"Updated wiki page for {host}")
                else:
                    logger.error(f"Failed to update wiki page for {host}")
        
        logger.info(f"Updated {updated_count} wiki pages")
    elif args.update_wiki:
        logger.warning("MediaWiki update requested but API URL not configured")
    
    # Update wiki index page if requested
    if (args.update_wiki or args.update_wiki_index) and config.get('mediawiki_api'):
        if all([config.get('mediawiki_user'), config.get('mediawiki_password')]):
            wiki_updater = MediaWikiUpdater(
                config['mediawiki_api'],
                config['mediawiki_user'],
                config['mediawiki_password']
            )
            
            index_page_title = config.get('mediawiki_index_page', 'Server Documentation')
            index_content = generate_wiki_index_content(inventory_manager.inventory)
            
            if wiki_updater.create_index_page(index_page_title, index_content):
                logger.info(f"Updated wiki index page: {index_page_title}")
            else:
                logger.error(f"Failed to update wiki index page: {index_page_title}")
    
    # Print connection summary at the end
    print_connection_summary(inventory_manager.connection_failures)
    
    # Beep to signal completion
    print('\a')

if __name__ == '__main__':
    main()

