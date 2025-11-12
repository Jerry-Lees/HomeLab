# Lab Documenter

A comprehensive home lab documentation system that automatically discovers and documents servers, VMs, containers, and services across multiple platforms. Features intelligent network scanning, multi-platform authentication, detailed connection failure analysis, automatic service discovery, and MediaWiki integration with a flexible Jinja2 template system.

## Features

### Core Capabilities
- **Multi-Platform Support**: Windows (WinRM), Linux (SSH keys), and NAS systems (SSH passwords)
- **Intelligent Platform Detection**: Automatically detects and adapts to Windows, Linux, and NAS platforms
- **Cascade Authentication**: Smart connection priority system for mixed environments
- **Automatic Discovery**: Network scanning across multiple CIDR ranges to find live hosts
- **CSV Auto-Update**: Automatically add successfully documented network-discovered hosts to CSV inventory
- **Clean Architecture**: Separation of concerns with dedicated modules for different functions
- **Template System**: Jinja2-based templates for customizable documentation output
- **Port Pre-Check**: Quick port availability check before attempting SSH connections
- **Buffered Logging**: Thread-safe log output prevents message interleaving during concurrent operations
- **Cacti Integration**: Direct SSH import with automatic device addition and updates

### Supported Platforms
- **Windows Systems**: Windows Server and Desktop editions via WinRM
- **Linux Distributions**: Ubuntu, Debian, CentOS, RHEL, Rocky Linux, Fedora, and others
- **NAS Systems**: Synology DSM, QNAP QTS, Asustor ADM, Buffalo, Netgear ReadyNAS
- **FreeBSD Systems**: TrueNAS Core/Scale, generic FreeBSD installations
- **Virtualization**: Proxmox VE hypervisors with VM/container enumeration
- **Container Platforms**: Docker containers and Kubernetes clusters

### Data Collection
- **System Information**: OS, kernel, hardware, uptime, resource usage (all platforms)
- **Network Configuration**: IP addresses, listening ports with service identification
- **Running Services**: Platform-specific service enumeration with intelligent descriptions
- **Windows Features**: Server roles/features and optional features detection
- **NAS Capabilities**: Storage pools, shares, disk health, installed packages
- **Docker Containers**: Container names, images, and status information
- **Kubernetes Integration**: Cluster info, nodes, pods, services, deployments with issue detection
- **Proxmox Support**: VM and container listings on Proxmox hypervisors

### Advanced Features  
- **Smart Service Discovery**: Auto-learning database that categorizes unknown services
- **MAC Vendor Database**: Auto-learning OUI database with 139 pre-seeded vendors
- **Connection Failure Analysis**: Detailed categorization of connection failures with reverse DNS and MAC vendor identification
- **Host Filtering**: ignore.csv support to skip problematic or irrelevant hosts
- **Multiple Network Ranges**: Scan across different subnets in a single run
- **Clean Operation**: Easy cleanup of generated documentation and logs
- **Offline Mode**: Process existing data without re-scanning for iterative development
- **Enhanced SSH Diagnostics**: Comprehensive connectivity troubleshooting tools
- **Beep Notification**: Audio completion signal for long-running scans
- **CSV Auto-Discovery**: Automatically add newly discovered hosts to CSV inventory

### Output Formats
- **Local Documentation**: Always creates Markdown files for each server plus master index
- **JSON Inventory**: Comprehensive raw data in structured format
- **Individual JSON Files**: Separate JSON file per server for analysis tools
- **MediaWiki Integration**: Automatic wiki page creation and updates with server index
- **Template-Based Generation**: Customizable output through Jinja2 templates
- **Cacti Exports**: Exports scanned/discovered devices into a format that can be imported into Cacti for monitoring

### Performance & Reliability
- **Concurrent Processing**: Multi-threaded scanning for faster execution
- **Port Pre-Check**: 2-second port check before SSH attempts reduces time spent on unreachable hosts
- **Buffered Thread Logging**: Clean, sequential log output even with concurrent operations
- **Multi-Platform Security**: WinRM for Windows, SSH keys for Linux, SSH passwords for NAS
- **Comprehensive Logging**: Detailed logs with intelligent error categorization
- **Flexible Configuration**: JSON config files with command-line overrides
- **Template Fallback**: Graceful degradation when templates are unavailable

## Quick Start

### Installation

1. **Automated Installation (Recommended)**:
   ```bash
   ./install.sh
   ```
   
   The installer automatically:
   - Installs system dependencies (may require sudo for packages)
   - Creates Python virtual environment in current directory
   - Installs required dependencies including Jinja2 for templates
   - Generates SSH keys (`homelab_key`) for secure server access
   - Adds SSH key to ssh-agent
   - Creates configuration files with proper paths
   - Sets up daily cron job
   - Generates enhanced helper scripts with diagnostics

2. **Manual Installation**:
   ```bash
   # Install dependencies
   pip3 install paramiko requests jinja2 pywinrm
   
   # Copy script to desired location
   chmod +x lab-documenter.py
   ```

### Basic Usage

```bash
# Scan your networks and create local documentation (all platforms)
./lab-documenter.py --scan

# Only scan servers listed in CSV file
./lab-documenter.py --csv-only

# Scan networks and auto-add new systems to CSV file
./lab-documenter.py --scan --csv servers.csv --csv-update

# Use existing data without re-scanning (offline mode)
./lab-documenter.py --use-existing-data --update-wiki

# Scan networks and also update MediaWiki pages  
./lab-documenter.py --scan --update-wiki

# Scan and directly import to Cacti monitoring
./lab-documenter.py --scan --export-cacti

# Update only the wiki server index page
./lab-documenter.py --update-wiki-index

# Clean old files then perform fresh scan
./lab-documenter.py --clean --scan

# Show help with all options
./lab-documenter.py --help
```

## Configuration

### Configuration File (`config.json`)

```json
{
    "ssh_user": "your_linux_admin_user",
    "ssh_key_path": "./.ssh/homelab_key",
    "network_ranges": [
        "192.168.1.0/24",
        "10.100.100.0/24", 
        "172.16.0.0/16"
    ],
    "ssh_timeout": 10,
    "max_workers": 5,
    "output_file": "./inventory.json",
    "csv_file": "./servers.csv",
    "windows_user": "administrator",
    "windows_password": "your_windows_password",
    "nas_user": "admin",
    "nas_password": "your_nas_password",
    "mediawiki_api": "https://wiki.example.com/api.php",
    "mediawiki_user": "documentation_bot",
    "mediawiki_password": "your_bot_password",
    "mediawiki_index_page": "Server Documentation",
    "cacti": {
        "host": "cacti.example.com",
        "ssh_user": "root",
        "ssh_key_path": "/home/user/.ssh/id_rsa",
        "cli_path": "/var/www/html/cacti/cli",
        "snmp_community": "public",
        "snmp_version": 2,
        "snmp_port": 161,
        "snmp_timeout": 500,
        "template_mapping": {
            "linux": 18,
            "windows": 26,
            "nas": 25,
            "freebsd": 22,
            "proxmox": 18,
            "kubernetes": 18,
            "docker": 18,
            "unknown": 16
        }
    }
}
```

### Multi-Platform Authentication

The system uses a cascade authentication approach for mixed environments:

1. **Windows Systems**: WinRM with username/password (port 5985)
2. **NAS Systems**: SSH with username/password (typically admin accounts)
3. **Linux Systems**: SSH with key-based authentication (most secure)

Each host is tested with all methods in priority order. Port 22 is checked before attempting SSH methods to avoid timeout delays on hosts without SSH enabled.

### Cacti Configuration

The Cacti section configures direct SSH import to Cacti monitoring:

- **host**: Cacti server hostname or IP address (required for direct import)
- **ssh_user**: SSH username for Cacti server (typically root)
- **ssh_key_path**: Path to SSH private key (null to use SSH agent)
- **cli_path**: Path to Cacti CLI scripts directory
- **snmp_community**: SNMP community string for device polling
- **snmp_version**: SNMP version (1, 2, or 3)
- **snmp_port**: SNMP port (default: 161)
- **snmp_timeout**: SNMP timeout in milliseconds
- **template_mapping**: Maps platform types to Cacti template IDs

To find your Cacti template IDs:
```bash
ssh root@cacti "php -q /var/www/html/cacti/cli/add_device.php --list-host-templates"
```

### Server List (`servers.csv`)

Optional file to specify servers manually across all platforms:

```csv
hostname,description,role,location
server1.homelab.local,Main file server,NAS,Rack 1
k8s-master.homelab.local,Kubernetes master node,K8s Master,Rack 1
proxmox1.homelab.local,Proxmox hypervisor,Virtualization,Rack 2
windows-server.local,Windows Server 2022,Windows Server,Rack 1
synology-nas.local,Synology NAS,Storage,Rack 2
truenas.local,TrueNAS Scale system,FreeBSD NAS,Rack 2
192.168.1.100,Docker host,Container Host,Rack 1
ubuntu-vm1,Development VM,Development,Virtual
```

## Generated Files

### Documentation Files
```
documentation/
├── hostname1.md                  # Markdown documentation per host
├── hostname1.json                # JSON data per host
├── hostname2.md
├── hostname2.json
├── index.md                      # Master index of all servers
└── [hostname].json files
```

### Inventory Files
```
inventory.json                    # Complete inventory with all collected data
services.json                     # Auto-learning service database
mac-ouis.json                     # Auto-learning MAC vendor database
```

### Cacti Export Files
```
documentation/
├── cacti_import.sh              # Bash script for manual import
├── cacti_devices.csv            # CSV reference file with device info
└── cacti_export.json            # Structured JSON with complete device data
```

The Cacti export files are generated when using `--export-cacti`:

- **cacti_import.sh**: Executable bash script containing `add_device.php` commands for each device. Can be manually copied to Cacti server and executed as backup method.
- **cacti_devices.csv**: Human-readable CSV file listing all devices with their configurations for review and auditing.
- **cacti_export.json**: Structured JSON containing complete device data, metadata, and settings for programmatic processing or future integrations.

## Cacti Integration

### Overview

The Cacti integration provides direct SSH import of discovered devices into Cacti monitoring. When `--export-cacti` is used, the system automatically:

1. Generates export files (bash script, CSV, JSON)
2. Tests SSH connectivity to Cacti server
3. Processes each device individually
4. Attempts to add device using `add_device.php`
5. Updates existing devices using `change_device.php` if already present
6. Displays comprehensive import summary with results

### Quick Start

```bash
# Scan network and import directly to Cacti
./lab-documenter.py --scan --export-cacti --config config.json

# Import existing inventory to Cacti
./lab-documenter.py --use-existing-data --export-cacti --config config.json

# Full workflow: scan, update wiki, import to Cacti
./lab-documenter.py --scan --update-wiki --export-cacti --config config.json
```

### Prerequisites

1. **SSH Access**: Ensure you can SSH to Cacti server:
   ```bash
   ssh root@cacti.example.com "echo test"
   ```

2. **SSH Key**: Configure SSH key authentication (no passwords in config):
   ```bash
   ssh-copy-id root@cacti.example.com
   ```

3. **SNMP Configuration**: Enable SNMP on target devices:
   ```bash
   # Ubuntu/Debian example
   sudo apt-get install snmpd
   sudo nano /etc/snmp/snmpd.conf
   # Add: rocommunity public
   sudo systemctl restart snmpd
   ```

4. **Firewall Rules**: Allow SNMP from Cacti server:
   ```bash
   sudo ufw allow from cacti_ip to any port 161 proto udp
   ```

### Import Behavior

The direct import follows this logic for each device:

1. **Add Attempt**: Tries to add device using `add_device.php`
2. **Duplicate Detection**: If device already exists, automatically switches to update
3. **Update Execution**: Uses `change_device.php` to update SNMP settings and template
4. **Result Tracking**: Categorizes result as Added, Updated, Failed, Skipped, or Already Existed
5. **Continue on Error**: Processes all devices even if some fail

### Import Results

After import completes, a summary displays:

```
============================================================
Cacti Import Summary
============================================================
Added:           15 devices
Updated:         12 devices
Already Existed:  3 devices (no change)
Failed:           2 devices
Skipped:          1 device (unreachable)
============================================================

Failed Devices:
------------------------------------------------------------
  • device1.example.com
    Error: SNMP timeout
  • device2.example.com
    Error: Template not found
```

### Configuration Options

#### Override Command-Line Options

Config file settings can be overridden via command-line:

```bash
# Override SNMP community
./lab-documenter.py --export-cacti --snmp-community custom-string

# Override Cacti CLI path
./lab-documenter.py --export-cacti --cacti-path /custom/path/cli

# Override SNMP version
./lab-documenter.py --export-cacti --snmp-version 3
```

#### Template Mapping

The `template_mapping` section maps platform types to Cacti template IDs. Update this based on your Cacti installation:

```json
"template_mapping": {
    "linux": 18,        # Local Linux Machine
    "windows": 26,      # Windows Device
    "nas": 25,          # Synology NAS
    "freebsd": 22,      # Net-SNMP Device
    "proxmox": 18,      # Uses Linux template
    "kubernetes": 18,   # Uses Linux template
    "docker": 18,       # Uses Linux template
    "unknown": 16       # Generic SNMP Device
}
```

### Manual Import (Backup Method)

If direct SSH import is not configured or fails, use the generated bash script:

```bash
# Copy script to Cacti server
scp documentation/cacti_import.sh root@cacti:/tmp/

# Execute on Cacti server
ssh root@cacti "bash /tmp/cacti_import.sh"
```

The bash script includes:
- Device-specific comments for reference
- Platform and OS information
- All necessary `add_device.php` parameters
- Error checking and completion messages

### Updating Existing Devices

When devices already exist in Cacti, the system automatically updates them with current settings:

- Updates SNMP community string
- Updates SNMP version
- Updates device template
- Updates device description

This ensures devices remain synchronized with your current configuration.

### Export Files Reference

#### cacti_export.json Structure

```json
{
  "metadata": {
    "generated_at": "2025-11-09T22:00:00.000000",
    "generated_by": "Lab Documenter Cacti Exporter",
    "version": "1.0",
    "cacti_cli_path": "/var/www/html/cacti/cli",
    "total_devices": 34,
    "skipped_devices": 2
  },
  "cacti_settings": {
    "cli_path": "/var/www/html/cacti/cli",
    "snmp": {
      "version": 2,
      "community": "public",
      "port": 161,
      "timeout": 500
    },
    "template_mapping": { ... }
  },
  "devices": [
    {
      "hostname": "server.example.com",
      "description": "Server Description",
      "ip": "server.example.com",
      "template_id": 18,
      "template_name": "Local Linux Machine",
      "platform_type": "linux",
      "os_name": "Ubuntu 22.04 LTS",
      "snmp": { ... },
      "availability": { ... },
      "metadata": {
        "reachable": true,
        "collected_at": "2025-11-09T22:00:00",
        "primary_ip": "192.168.1.100"
      }
    }
  ],
  "skipped": [
    {
      "hostname": "offline-server.example.com",
      "reason": "unreachable"
    }
  ]
}
```

This JSON format enables:
- Programmatic processing of export data
- Custom reporting and analysis
- Integration with other monitoring tools
- Audit trails of import operations

## Troubleshooting

### Cacti Import Issues

**Problem:** SSH connection to Cacti server fails

**Solutions:**
1. Test SSH manually: `ssh root@cacti "echo test"`
2. Verify SSH key authentication works
3. Check SSH key path in config.json is correct
4. Ensure SSH key has proper permissions (600)
5. Try with SSH agent: `ssh-add ~/.ssh/id_rsa`
6. Review error in import summary

**Problem:** Devices fail to add to Cacti

**Solutions:**
1. Verify SNMP is enabled on target devices
2. Check SNMP community string matches
3. Verify firewall allows SNMP (UDP port 161)
4. Test SNMP manually: `snmpwalk -v2c -c public device_ip system`
5. Check template IDs match your Cacti installation
6. Review device-specific errors in import summary
7. Check Cacti logs: `/var/www/html/cacti/log/cacti.log`

**Problem:** Template not found errors

**Solutions:**
1. List available templates: `ssh root@cacti "php -q /var/www/html/cacti/cli/add_device.php --list-host-templates"`
2. Update template_mapping in config.json with correct IDs
3. Verify template exists in Cacti web interface
4. Use generic template (16) as fallback

**Problem:** Devices marked as "down" after import

**Solutions:**
1. Check SNMP service is running on device
2. Verify SNMP community string is correct
3. Test from Cacti server: `snmpwalk -v2c -c public device.example.com system`
4. Check device firewall allows SNMP from Cacti server
5. Increase SNMP timeout in config.json
6. Verify device hostname resolves from Cacti server
7. Check Cacti poller is running

(See original README for additional troubleshooting sections: SSH Connection Issues, WinRM Connection Issues, Network Scanning Issues, MediaWiki Update Failures, Performance Issues, Permission Errors, Template Rendering Issues)

## Best Practices

### Cacti Specific
- Test SSH connection before bulk imports
- Start with small batches for initial setup
- Verify template IDs match your installation
- Monitor Cacti logs during imports
- Keep generated bash scripts as backup
- Document custom template mappings
- Schedule regular synchronization

(See original README for additional best practices: Security, Performance, Organization, Maintenance)

### Ignore List (`ignore.csv`)

Skip problematic hosts that will never be reachable:

```csv
IP or hostname,notes about the device
192.168.1.1,Router management interface  
192.168.1.50,Broken server that never responds
printer.local,Network printer without SSH/WinRM
old-server.local,Decommissioned equipment
# 192.168.1.200,This line is commented out
```

## Platform Setup

### Windows Systems

1. **Enable WinRM** on target Windows systems:
   ```cmd
   winrm quickconfig
   winrm set winrm/config/service @{AllowUnencrypted="true"}
   ```

2. **Configure Windows credentials** in `config.json`:
   - Use local administrator or domain account
   - NTLM authentication is preferred for security

3. **Firewall**: Ensure port 5985 is open for WinRM

### Linux Systems

1. **Distribute SSH keys** to Linux servers:
   ```bash
   ./distribute-key.sh user@192.168.1.100 ubuntu@server.local
   ```

2. **SSH key setup** is handled by the installer automatically

3. **Test connectivity**:
   ```bash
   ./distribute-key.sh --diagnose 192.168.1.100
   ```

### NAS Systems

1. **Enable SSH** on your NAS (typically in admin panel)

2. **Configure NAS credentials** in `config.json`:
   - Usually `admin` user with admin password
   - Some NAS systems require enabling SSH password authentication

3. **Test connection**:
   ```bash
   ssh admin@nas-system.local
   ```

4. **Supported NAS platforms**:
   - Synology DSM
   - QNAP QTS  
   - Asustor ADM
   - Buffalo TeraStation
   - Netgear ReadyNAS
   - TrueNAS Core/Scale

### Proxmox Hypervisors

Proxmox systems are detected automatically when connected via SSH key authentication. The system collects:
- Hypervisor information
- Running VMs (QEMU/KVM)
- Containers (LXC)

### Kubernetes Clusters

For Kubernetes cluster documentation:

1. **Kubectl must be installed** on the scanning host
2. **Kubeconfig** must be accessible from the Linux account running the scan
3. **Cluster access** is detected automatically on systems with kubectl configured

## Command Line Options

### Operation Modes
- `--scan` - Scan network(s) for live hosts and collect data (multi-platform)
- `--csv-only` - Only scan hosts listed in CSV file (skip network scan)
- `--use-existing-data` - Use existing inventory.json without re-scanning (offline mode)
- `--update-wiki` - Update MediaWiki pages with collected data
- `--update-wiki-index` - Create or update the wiki server index page
- `--clean` - Delete all files in ./documentation and ./logs directories
- `--dry-run` - Show what would be done without making changes
- `--csv-update` - Auto-add successfully documented network-discovered hosts to CSV file (requires --scan and --csv)

### File Paths
- `--config FILE` - Configuration file path (default: config.json)
- `--csv FILE` - CSV file containing server list (default: servers.csv)
- `--output FILE` - Output JSON file path (default: inventory.json)

### Network Settings
- `--network RANGES` - Network range(s) to scan:
  - Single: `--network 192.168.1.0/24`
  - Multiple: `--network "192.168.1.0/24,10.0.0.0/24,172.16.0.0/16"`
- `--ssh-user USER` - SSH username for Linux server connections
- `--ssh-key FILE` - SSH private key file path
- `--ssh-timeout SECONDS` - SSH connection timeout

### Performance Settings
- `--workers N` - Number of concurrent workers for scanning

### Multi-Platform Settings
- `--windows-user USER` - Windows username for WinRM connections
- `--windows-password PASS` - Windows password for WinRM connections
- `--nas-user USER` - NAS username for SSH password connections
- `--nas-password PASS` - NAS password for SSH password connections

### MediaWiki Settings
- `--wiki-api URL` - MediaWiki API URL
- `--wiki-user USER` - MediaWiki username
- `--wiki-password PASS` - MediaWiki password
- `--wiki-index-page TITLE` - Title for the wiki server index page

### Output Settings
- `--verbose, -v` - Enable verbose logging output
- `--quiet, -q` - Suppress all output except errors

## Usage Examples

### Multi-Platform Operations
```bash
# Scan mixed environment with all platform types
./lab-documenter.py --scan --verbose

# Offline mode - process existing data quickly
./lab-documenter.py --use-existing-data --update-wiki

# Test Windows connectivity
./lab-documenter.py --csv-only --dry-run --verbose

# Clean and rebuild everything
./lab-documenter.py --clean --scan --update-wiki
```

### CSV Auto-Discovery Operations
```bash
# Scan networks and auto-add new systems to CSV
./lab-documenter.py --scan --csv servers.csv --csv-update

# Preview what would be added without making changes
./lab-documenter.py --scan --csv servers.csv --csv-update --dry-run

# Auto-discovery with verbose logging to see what's happening
./lab-documenter.py --scan --csv servers.csv --csv-update --verbose

# Use custom CSV file for auto-updates
./lab-documenter.py --scan --csv my-lab-inventory.csv --csv-update
```

### Platform-Specific Operations
```bash
# Test Windows systems only (specify in CSV)
./lab-documenter.py --csv windows-servers.csv --csv-only

# NAS systems with extended timeout
./lab-documenter.py --ssh-timeout 30 --scan

# Linux systems with custom key
./lab-documenter.py --ssh-key ~/.ssh/custom_key --scan
```

### SSH Diagnostics
```bash
# Diagnose connectivity issues
./distribute-key.sh --diagnose 192.168.1.100

# Distribute keys to multiple Linux systems
./distribute-key.sh admin@192.168.1.100 ubuntu@192.168.1.101 root@192.168.1.102

# Test SSH connectivity after key distribution
ssh -i .ssh/homelab_key admin@192.168.1.100
```

### MediaWiki Operations
```bash
# Scan and update both individual server pages and index
./lab-documenter.py --scan --update-wiki

# Update only the wiki index page from existing data
./lab-documenter.py --use-existing-data --update-wiki-index

# Use custom wiki index page title
./lab-documenter.py --update-wiki-index --wiki-index-page "My Lab Servers"

# Test wiki operations without making changes
./lab-documenter.py --update-wiki-index --dry-run --verbose
```

### Multiple Network Scanning
```bash
# Scan multiple networks from command line
./lab-documenter.py --network "192.168.1.0/24,10.0.0.0/24" --scan

# Use config file with multiple network ranges
./lab-documenter.py --scan  # Uses network_ranges from config.json

# Scan single host (using /32 notation)
./lab-documenter.py --scan --network 192.168.1.100/32
```

### Performance Tuning
```bash
# High-performance scanning for large networks
./lab-documenter.py --workers 20 --network "192.168.0.0/16" --scan

# Conservative scanning with longer timeouts
./lab-documenter.py --ssh-timeout 30 --workers 5 --scan
```

## CSV Auto-Discovery Feature

The `--csv-update` feature automatically maintains your server inventory by adding newly discovered systems to your CSV file.

### How It Works

1. **Network Scan**: Use `--scan` to discover live hosts on your networks
2. **CSV Comparison**: Compare discovered hosts against existing CSV entries
3. **Connection Testing**: Only add hosts that were successfully connected to and documented
4. **Smart Defaults**: Automatically suggest appropriate descriptions and roles based on detected platform and OS
5. **Preserve Structure**: Maintains your existing CSV file structure and format

### Generated CSV Entries

When new systems are discovered, they're added with intelligent defaults:

```csv
hostname,description,role,location
# Existing entries remain unchanged
server1.local,Main file server,NAS,Rack 1

# Auto-discovered entries get smart defaults
ubuntu-vm-01,Auto-discovered linux system (Ubuntu),Ubuntu Server,Auto-discovered
windows-pc-05,Auto-discovered windows system (Windows 11 Pro),Windows Client,Auto-discovered
synology-backup,Auto-discovered nas system (Synology DSM),Storage/NAS,Auto-discovered
```

### Requirements

- Must use `--scan` (to discover new hosts)
- Must specify `--csv FILE` or have `csv_file` in config
- New hosts must be successfully connected to and documented

### Example Workflow

```bash
# Initial setup with existing servers
./lab-documenter.py --csv-only --csv my-servers.csv

# Later, scan for new systems and auto-add them
./lab-documenter.py --scan --csv my-servers.csv --csv-update --verbose

# Review what was added (check the logs)
tail -20 logs/lab-documenter.log

# Edit CSV file to customize descriptions/roles if needed
nano my-servers.csv
```

## Platform-Specific Features

### Windows Detection
- **Edition Detection**: Distinguishes Server vs Desktop/Client editions
- **Features Enumeration**: Server roles/features and optional features
- **Service Discovery**: Windows services with proper categorization
- **System Information**: Hardware, memory, disk, and network details
- **Update Information**: Last installed updates and patch levels

### NAS Platform Detection
- **Automatic Identification**: Detects Synology, QNAP, Asustor, Buffalo, etc.
- **Storage Information**: Volumes, shares, storage pools, and RAID arrays
- **Disk Health**: SMART status monitoring where available
- **Package Information**: Installed applications and services
- **Network Shares**: SMB/CIFS and NFS export enumeration

### TrueNAS Support
- **FreeBSD Detection**: Proper identification of TrueNAS Core/Scale systems
- **ZFS Integration**: Pool status, dataset information, and health monitoring
- **Jail Information**: Container/jail enumeration on Core systems
- **Service Status**: TrueNAS-specific service monitoring

### Linux Enhanced Detection
- **Distribution Identification**: Detailed OS release information
- **Container Platforms**: Docker and Kubernetes integration
- **Virtualization**: Proxmox hypervisor detection and VM enumeration
- **Service Database**: Auto-learning service categorization

## MediaWiki Integration

### MediaWiki Setup

1. **Create a bot user account** in your MediaWiki installation
2. **Grant bot permissions**: `bot`, `confirmed`, and `autoconfirmed` user groups
3. **Enable API access** in `LocalSettings.php`:
   ```php
   $wgEnableAPI = true;
   $wgEnableWriteAPI = true;
   ```
4. **Configure the bot credentials** in your `config.json`

### Wiki Output Features

**Individual Server Pages**: Creates `Server:hostname` pages containing:
- Platform-specific system information (Windows, Linux, NAS)
- Resource usage (memory, disk, CPU load)
- Network configuration and listening ports
- Running services with descriptions
- Platform-specific features (Windows roles, NAS shares, etc.)
- Docker containers and Kubernetes information
- Proxmox VM/container lists

**Server Index Page**: Creates a configurable main index page featuring:
- Quick statistics (total servers, reachable/unreachable counts)
- Platform breakdown (Windows, Linux, NAS counts)
- Operating system breakdown
- Special services summary (Kubernetes, Docker, Proxmox)
- Sortable table of all active servers with links
- Unreachable servers section with failure reasons
- Built-in search box for quick server lookup
- Navigation links to browse all server pages

### Wiki Page Access

- Individual servers: `https://your-wiki.com/wiki/Server:hostname`
- Main index: `https://your-wiki.com/wiki/Server_Documentation` (or custom title)

## Output Format

### 1. Local Documentation (Always Created)

The script creates a `documentation/` folder containing:

**Individual Server Files**: `documentation/server1.homelab.local.md`
```markdown
# server1.homelab.local

**Last Updated:** 2025-01-06T10:30:00
**Platform:** Linux (Ubuntu 24.04.3 LTS)

## System Information
- **OS:** Ubuntu 24.04.3 LTS
- **Kernel:** 6.8.0-44-generic
- **Architecture:** x86_64
- **Uptime:** up 15 days, 6 hours, 23 minutes
- **CPU:** Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz (8 cores)

## Resources
- **Memory:** 8.2G / 32G
- **Disk Usage:** 250G/1.0T (25%)
- **Load Average:** 0.15, 0.22, 0.18

## Services
- **OpenSSH Server** (active) - *system* - Secure shell server for remote access
- **Docker Engine** (active) - *containers* - Container runtime and management platform

## Windows Features (Windows only)
- **IIS-WebServerRole** - Internet Information Services
- **RSAT-AD-PowerShell** - Active Directory PowerShell module

## NAS Information (NAS only)
- **Type:** Synology DSM 7.2
- **Storage Pools:** 2 RAID arrays
- **Shares:** 8 SMB shares, 3 NFS exports
```

**Master Index**: `documentation/index.md` with server summaries and status
**Individual JSON Files**: `documentation/server1.homelab.local.json` for each server

### 2. Enhanced Connection Failure Analysis

Detailed failure categorization with reverse DNS lookup at the end of each run:

```
============================================================
CONNECTION SUMMARY
============================================================
Failed to connect to 5 devices:

• Windows authentication failed (check Windows credentials) (2 devices):
  - 192.168.1.131 (hostname: windows-server.local)
  - 192.168.1.92  

• SSH connection timeout (waited 10s) (2 devices):
  - 192.168.1.7 (hostname: nas-backup.local)
  - 10.0.0.100

• SSH connection refused (service may not be running) (1 devices):
  - 192.168.1.50 (hostname: old-server.local)
============================================================
```

### 3. Services Database with Auto-Discovery

The system maintains an intelligent `services.json` database that:
- Automatically discovers unknown services and adds them
- Provides rich descriptions and categorization
- Grows smarter over time as you scan different systems
- Can be manually edited to add custom service information

### 4. JSON Inventory File

Comprehensive raw data saved to inventory file:

```json
{
  "server1.homelab.local": {
    "hostname": "server1.homelab.local", 
    "timestamp": "2025-01-06T10:30:00",
    "reachable": true,
    "platform_type": "linux",
    "connection_type": "ssh_key",
    "os_release": {
      "name": "Ubuntu",
      "version": "24.04.3 LTS", 
      "pretty_name": "Ubuntu 24.04.3 LTS"
    },
    "services": [...],
    "docker_containers": [...],
    "kubernetes_info": {...},
    "listening_ports": [...]
  },
  "windows-server.local": {
    "hostname": "windows-server.local",
    "platform_type": "windows",
    "connection_type": "winrm",
    "windows_info": {
      "os_release": {...},
      "system_info": {...},
      "features": [...],
      "services": [...]
    }
  }
}
```

## File Locations Reference

```
lab-documenter/
├── lab-documenter.py              # Main script
├── config.json                    # Default configuration
├── config-my.json                 # User-specific config (gitignored)
├── servers.csv                    # Host inventory
├── ignore.csv                     # Hosts to skip
├── inventory.json                 # Generated inventory
├── services.json                  # Service database
├── mac-ouis.json                  # MAC vendor database
│
├── modules/                       # Python modules
│   ├── config.py                 # Configuration management
│   ├── documentation.py          # Template rendering
│   ├── inventory.py              # Data collection
│   ├── network.py                # Network scanning
│   ├── services.py               # Service discovery
│   ├── system.py                 # System collection
│   ├── windows.py                # Windows collector
│   ├── linux.py                  # Linux collector
│   ├── nas.py                    # NAS collector
│   ├── mediawiki.py              # Wiki integration
│   ├── cacti.py                  # Cacti integration
│   ├── networking_info.py        # MAC vendor lookup
│   └── utils.py                  # Utilities
│
├── templates/                     # Jinja2 templates
│   ├── markdown_host.j2          # Markdown format
│   ├── mediawiki_host.j2         # MediaWiki format
│   └── mediawiki_index.j2        # Wiki index
│
├── documentation/                 # Generated files
│   ├── *.md                      # Markdown per host
│   ├── *.json                    # JSON per host
│   ├── index.md                  # Master index
│   ├── cacti_import.sh           # Cacti bash script
│   ├── cacti_devices.csv         # Cacti CSV
│   └── cacti_export.json         # Cacti JSON
│
└── logs/                          # Log files
    └── lab-documenter.log
```

## Auto-Learning Databases

### Service Database (`services.json`)

The system maintains an auto-learning database of services:
- Unknown services are automatically added with timestamp
- Services are categorized by type (web, database, monitoring, etc.)
- Additional fields can be manually populated (URLs, access info, etc.)
- Database is updated during each scan with new discoveries

### MAC Vendor Database (`mac-ouis.json`)

The system maintains an auto-learning database of MAC address vendors:
- Pre-seeded with 139 common vendor OUIs (Organizational Unique Identifiers)
- Unknown MAC addresses trigger API lookup to macvendors.com
- Successful API lookups are automatically added to local database
- Reduces API calls over time as database grows
- Database structure: `{"OUI": {"vendor": "Name", "date_added": "YYYY-MM-DD", "source": "api/import"}}`

## Advanced Usage

### Network Scanning Options

```bash
# Scan multiple networks
./lab-documenter.py --scan --network 192.168.1.0/24 --network 10.0.0.0/24

# Scan single host (using /32 notation)
./lab-documenter.py --scan --network 192.168.1.100/32

# Combine network scan with CSV hosts
./lab-documenter.py --scan --csv servers.csv
```

### Template Customization

Templates are located in the `templates/` directory and use Jinja2 syntax:

```bash
# Custom server page template
templates/pages/server_page.md.j2

# Custom components
templates/components/system_info.md.j2
templates/components/services_table.md.j2

# Base layout
templates/base/base.md.j2
```

Modify templates to change output format, add sections, or customize styling.

### Offline Development Mode

```bash
# Process existing data without scanning
./lab-documenter.py --use-existing-data

# Test wiki updates without rescanning
./lab-documenter.py --use-existing-data --update-wiki --dry-run

# Test templates with existing data
./lab-documenter.py --use-existing-data --verbose
```

### Debug and Troubleshooting

```bash
# Verbose output
./lab-documenter.py --verbose --scan

# Dry run mode (no changes made)
./lab-documenter.py --dry-run --scan --update-wiki

# Check what would be cleaned
./lab-documenter.py --clean --dry-run

# Diagnose SSH connection issues
./distribute-key.sh --diagnose 192.168.1.100
```

## Architecture

### Module Organization

The system uses a clean architecture with focused modules:

```
lab-documenter/
├── lab-documenter.py          # Main CLI interface and orchestration
├── modules/
│   ├── __init__.py           # Package initialization
│   ├── config.py             # Configuration management
│   ├── network.py            # Network scanning functionality
│   ├── system.py             # Multi-platform connections and data collection
│   ├── system_windows.py     # Windows-specific collection via WinRM
│   ├── system_nas.py         # NAS-specific collection (all NAS types)
│   ├── system_kubernetes.py  # Kubernetes cluster information
│   ├── system_proxmox.py     # Proxmox VE information
│   ├── services.py           # Service database management
│   ├── networking_info.py    # MAC vendor database and network utilities
│   ├── inventory.py          # Host data aggregation
│   ├── documentation.py      # Jinja2 template-based generation
│   ├── wiki.py              # MediaWiki API integration
│   └── utils.py              # Utility functions and helpers
├── templates/
│   ├── base/                 # Base templates and macros
│   ├── components/           # Reusable component templates
│   └── pages/                # Complete page templates
├── config.json               # Main configuration
├── servers.csv              # Optional server list
├── ignore.csv               # Optional ignore list
├── services.json            # Auto-learning services database
├── mac-ouis.json            # Auto-learning MAC vendor database
├── requirements.txt         # Python dependencies
└── documentation/           # Generated documentation
    ├── index.md            # Master server index
    ├── server1.local.md    # Individual server docs
    └── server1.local.json  # Individual server data
```

### Data Flow

1. **Configuration Loading**: Loads settings from JSON and command line
2. **Network Discovery**: Scans multiple CIDR ranges for live hosts
3. **Host Filtering**: Applies ignore.csv exclusions
4. **Port Pre-Check**: Quick 2-second check for SSH port 22 availability
5. **Multi-Platform Connection**: Cascade authentication (Windows → NAS → Linux)
6. **Platform Detection**: Identifies and refines platform type (especially TrueNAS)
7. **Data Collection**: Platform-specific information gathering
8. **Service Enhancement**: Uses intelligent database to categorize services
9. **MAC Vendor Lookup**: Identifies device manufacturers for failed connections
10. **Template Rendering**: Processes Jinja2 templates with collected data
11. **Documentation Generation**: Creates local Markdown files and JSON data
12. **CSV Auto-Update**: Adds newly discovered hosts to CSV inventory (if enabled)
13. **MediaWiki Updates**: Creates/updates individual server pages and index page
14. **Failure Analysis**: Categorizes and reports connection issues with reverse DNS and MAC vendors

## Advanced Features

### Multi-Platform Connection Cascade

The system intelligently tries connection methods in priority order:
1. **Windows (WinRM)** - Tries NTLM, Kerberos, then Basic authentication
2. **NAS (SSH Password)** - For systems requiring password authentication
3. **Linux (SSH Keys)** - Most secure method for Linux systems

Port 22 is checked before SSH attempts to reduce timeout delays. Platform detection is refined after connection to identify systems like TrueNAS that might initially appear as generic Linux.

### Enhanced SSH Diagnostics

The `distribute-key.sh` script provides comprehensive connectivity diagnostics:
- **Ping Tests**: Basic network reachability
- **Port Scanning**: SSH service detection on standard and alternative ports
- **Banner Detection**: SSH service identification
- **Alternative Port Discovery**: Scans common SSH ports (22, 2222, 2200, etc.)
- **Key Distribution**: Automated SSH key deployment with verification

### Intelligent Service Discovery

The `services.json` database automatically learns about services:

```json
{
  "nginx": {
    "display_name": "Nginx Web Server",
    "description": "High-performance web server and reverse proxy",
    "category": "web",
    "ports": ["80", "443"],
    "access": "HTTP on port 80, HTTPS on port 443",
    "documentation_url": "https://nginx.org/en/docs/"
  }
}
```

Unknown services are auto-added and can be manually edited for better descriptions.

### MAC Vendor Identification

The `mac-ouis.json` database identifies device manufacturers:
- Pre-seeded with 139 common vendors (Cisco, HP, Dell, Ubiquiti, etc.)
- API lookups for unknown vendors (macvendors.com)
- 0.5 second delay between API calls to respect rate limits
- Automatic database growth over time
- Helps identify failed devices by manufacturer

### Connection Failure Analysis

Detailed categorization helps troubleshoot connectivity issues:

- **Windows authentication failed** - Check WinRM credentials and service status
- **SSH connection timeout** - Host doesn't respond within timeout period
- **SSH connection refused** - Host responds but SSH service not running  
- **SSH authentication failed** - SSH key or credential issues
- **DNS resolution failed** - Hostname cannot be resolved
- **Network unreachable** - Routing issues
- **WinRM connection refused** - WinRM service not running or port blocked
- **Port 22 not accessible** - SSH port check failed (2-second timeout)

### Template Customization

Templates support advanced features for customizable output:

```jinja2
{# Platform-specific sections #}
{% if platform_type == 'windows' %}
### Windows Features
{% for feature in windows_info.features %}
- **{{ feature }}**
{% endfor %}
{% endif %}

{% if platform_type == 'nas' %}
### Storage Information
{% for pool in nas_info.storage_pools %}
- **{{ pool.name }}** ({{ pool.type }}) - {{ pool.state }}
{% endfor %}
{% endif %}

{# Conditional content based on data presence #}
{% if kubernetes_info and kubernetes_info.pods %}
### Kubernetes Cluster
{% for namespace, pods in kubernetes_info.pods|groupby('namespace') %}
**{{ namespace }}**: {{ pods|list|length }} pods
{% endfor %}
{% endif %}
```

### Performance Optimization

**Port Pre-Check System**:
- Quick 2-second check for SSH port 22 before attempting full connection
- Reduces wasted time on hosts without SSH enabled
- Prevents unnecessary 10-second SSH timeouts
- Significantly improves scan performance on large networks

**Buffered Thread Logging**:
- Thread-safe sequential log output
- Each host's logs appear as a complete block
- Prevents message interleaving during concurrent operations
- Cleaner, more readable log files

## Automation

### Cron Job (Installed Automatically)

The installer creates a daily cron job:

```bash
# View current cron job
crontab -l

# Edit schedule  
crontab -e

# View logs
tail -f ./logs/cron.log
```

### Helper Scripts (Created by Installer)

- **`setup-ssh-agent.sh`**: Adds SSH key to ssh-agent
- **`distribute-key.sh`**: Enhanced SSH key distribution with diagnostics
- **`run-lab-documenter.sh`**: Runs with proper environment

## Security

### Multi-Platform Security

The system uses appropriate authentication methods for each platform:

**Windows Systems**:
- WinRM over HTTP with NTLM/Kerberos authentication
- Support for domain and local accounts
- Encrypted authentication even over HTTP

**Linux Systems**:
- SSH public key authentication (most secure)
- Dedicated SSH keys for lab documentation
- No password authentication for Linux hosts

**NAS Systems**:
- SSH password authentication (admin accounts)
- Typically required for embedded NAS systems
- Credentials stored securely in config.json

### Best Practices

- Use dedicated SSH keys (not personal keys)
- Restrict SSH key access to read-only operations where possible
- Store sensitive credentials in environment variables when possible
- Use firewall rules to restrict network access
- Regularly rotate SSH keys and passwords
- Keep sensitive configuration files secure (config.json permissions set to 600)

## Troubleshooting

### Multi-Platform Connection Issues

**Windows WinRM Failures**:
```bash
# Test WinRM connectivity manually
Test-WSMan -ComputerName server-name

# Check WinRM service status
Get-Service WinRM

# Verify WinRM configuration
winrm get winrm/config
```

**SSH Connection Failures**:
```bash
# Use enhanced diagnostics
./distribute-key.sh --diagnose 192.168.1.100

# Test SSH connectivity manually
ssh -i .ssh/homelab_key user@server

# Check SSH key permissions
chmod 600 .ssh/homelab_key
chmod 644 .ssh/homelab_key.pub
```

**NAS Connection Issues**:
```bash
# Test SSH password authentication
ssh admin@nas-system.local

# Check if SSH is enabled on NAS
# (varies by NAS system - check admin panel)
```

**Port 22 Accessibility**:
```bash
# Test if port 22 is open
nc -zv 192.168.1.100 22

# Check firewall on target host
# Host will be skipped if port 22 is not accessible
```

### Platform Detection Issues

**TrueNAS Detection**:
```bash
# Verify TrueNAS detection with verbose output
./lab-documenter.py --verbose --csv truenas-only.csv --csv-only

# Check FreeBSD detection
ssh admin@truenas "uname -a"
```

**Windows Feature Detection**:
```bash
# Test Windows feature commands manually
powershell "Get-WindowsFeature | Where-Object {$_.InstallState -eq 'Installed'}"

# For Windows 10/11 clients
powershell "Get-WindowsOptionalFeature -Online | Where-Object {$_.State -eq 'Enabled'}"
```

### CSV Auto-Update Issues

**CSV Not Being Updated**:
```bash
# Check that requirements are met
./lab-documenter.py --scan --csv servers.csv --csv-update --verbose

# Verify hosts are being discovered and documented successfully
./lab-documenter.py --scan --dry-run --verbose

# Check CSV file permissions
ls -la servers.csv
chmod 644 servers.csv
```

**Unexpected CSV Entries**:
```bash
# Review what was added
tail -50 logs/lab-documenter.log | grep "Added hosts"

# Edit CSV to customize entries
nano servers.csv

# Use dry-run to preview changes
./lab-documenter.py --scan --csv servers.csv --csv-update --dry-run
```

### MAC Vendor Lookup Issues

**MAC Vendor Database Not Growing**:
```bash
# Check if mac-ouis.json exists and is writable
ls -la mac-ouis.json

# Verify API connectivity
curl https://api.macvendors.com/00:00:00:00:00:00

# Check logs for vendor lookup errors
grep "MAC vendor" logs/lab-documenter.log
```

**Rate Limiting from API**:
- The system includes 0.5 second delays between API calls
- Pre-seeded database contains 139 common vendors
- Local lookups do not count against rate limits

### Performance Issues

**Slow Scanning**:
- Adjust `max_workers` in config.json (default: 5)
- Use ignore.csv to skip hosts that timeout
- Port 22 pre-check reduces time on hosts without SSH (2 seconds vs 10 second SSH timeout)

**Log Output Interleaving**:
- Buffered logging prevents message mixing
- Each host's logs appear as a complete block
- Check logs/lab-documenter.log for complete sequential output

### Debug Mode

```bash
# Maximum verbosity with offline mode for quick testing
./lab-documenter.py --use-existing-data --verbose --update-wiki --dry-run

# Check what would be done without changes
./lab-documenter.py --dry-run --scan --update-wiki

# View logs
tail -f ./logs/lab-documenter.log
```

### Common Issues

**Mixed Environment Authentication**:
- Ensure Windows credentials are properly configured for WinRM
- Verify NAS credentials are set for SSH password authentication
- Check that Linux SSH keys are properly distributed

**Network Discovery**:
- Test with single network first: `--network 192.168.1.0/24`
- Check firewall settings on scanning host
- Verify ping responses from target hosts

**Template Errors**:
```bash
# Check template syntax with dry run
./lab-documenter.py --dry-run --verbose --use-existing-data

# Verify Jinja2 installation
python3 -c "import jinja2; print(jinja2.__version__)"
```

## Requirements

### System Requirements
- Python 3.6+
- SSH client
- Network connectivity to target hosts
- Cron daemon (for automation)
- MediaWiki installation (for wiki integration)

### Python Dependencies
- `paramiko>=2.11.0` - SSH connections
- `requests>=2.28.0` - HTTP/MediaWiki API and MAC vendor lookups
- `jinja2>=3.0.0` - Template processing
- `pywinrm>=0.4.3` - Windows WinRM connections

### Target Host Requirements

**Windows Systems**:
- WinRM service enabled
- Port 5985 accessible
- Local or domain user account with appropriate permissions

**Linux Systems**:
- SSH server running (port 22)
- SSH key-based authentication configured  
- Standard command-line tools (ps, df, free, etc.)

**NAS Systems**:
- SSH service enabled (admin panel setting)
- Admin account access
- Basic Unix-like command set available

### Optional Requirements
- `kubectl` - For Kubernetes cluster information
- `docker` - For Docker container information
- `pveversion`, `qm`, `pct` - For Proxmox information

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes with proper separation of concerns
4. Add appropriate logging and error handling
5. Update documentation and help text
6. Test with `--dry-run` and `--verbose` modes
7. Test across multiple platforms (Windows, Linux, NAS)
8. Submit a pull request

# Cacti Export Feature Documentation

## Overview

The Cacti export feature allows you to automatically import all discovered devices from Lab Documenter into your Cacti monitoring system. It generates a bash script with `add_device.php` CLI commands that can be executed on your Cacti server.

## Files Generated

### 1. `documentation/cacti_import.sh`
A bash script containing Cacti CLI commands to add each discovered device. This script:
- Checks if Cacti CLI exists at the specified path
- Adds each reachable device with appropriate template and settings
- Provides progress output during import
- Includes device metadata as comments for reference

### 2. `documentation/cacti_devices.csv`
A CSV reference file containing:
- hostname
- ip_address
- description
- platform_type
- os_name
- template_id (Cacti host template ID)
- reachable (Yes/No)

## Platform Type to Cacti Template Mapping

Lab Documenter automatically maps platform types to standard Cacti host templates:

| Platform Type | Cacti Template ID | Template Name |
|--------------|-------------------|---------------|
| linux | 8 | Local Linux Machine |
| windows | 7 | Windows 2000/XP Host |
| nas | 1 | Generic SNMP-enabled Host |
| freebsd | 3 | ucd/net SNMP Host |
| proxmox | 8 | Local Linux Machine |
| kubernetes | 8 | Local Linux Machine |
| docker | 8 | Local Linux Machine |
| unknown | 1 | Generic SNMP-enabled Host |

**Note:** These are standard Cacti template IDs. If your Cacti installation uses different IDs, you can:
1. Edit the TEMPLATE_MAPPING in `modules/cacti.py`
2. Manually edit the generated bash script before running it
3. Use `php -q /usr/share/cacti/cli/add_device.php --list-host-templates` to see your templates

## Usage

### Basic Export

```bash
# First, scan your network to generate inventory
./lab-documenter.py --scan

# Export to Cacti format
./lab-documenter.py --export-cacti
```

### Export with Custom Settings

```bash
# Use custom Cacti path and SNMP settings
./lab-documenter.py --export-cacti \
    --cacti-path /var/www/html/cacti/cli \
    --snmp-community mycommunity \
    --snmp-version 2
```

### Combined Operations

```bash
# Scan network and immediately export to Cacti
./lab-documenter.py --scan --export-cacti

# Use existing data and export
./lab-documenter.py --use-existing-data --export-cacti
```

## Command Line Options

| Option | Default | Description |
|--------|---------|-------------|
| `--export-cacti` | - | Enable Cacti export (requires inventory.json) |
| `--cacti-path PATH` | /usr/share/cacti/cli | Path to Cacti CLI directory |
| `--snmp-community STRING` | public | SNMP community string |
| `--snmp-version VERSION` | 2 | SNMP version (1, 2, or 3) |

## Installation on Cacti Server

### Method 1: Copy and Execute Script

1. Generate the import script:
   ```bash
   ./lab-documenter.py --export-cacti
   ```

2. Copy the generated script to your Cacti server:
   ```bash
   scp documentation/cacti_import.sh user@cacti-server:/tmp/
   ```

3. On the Cacti server, execute the script:
   ```bash
   sudo bash /tmp/cacti_import.sh
   ```

### Method 2: Review and Customize Before Import

1. Generate and review the script:
   ```bash
   ./lab-documenter.py --export-cacti
   cat documentation/cacti_import.sh
   ```

2. Edit the script if needed (adjust paths, templates, SNMP settings)

3. Copy and execute as above

### Method 3: Manual CSV Import

If you prefer manual control, use the CSV reference file:

1. Generate CSV:
   ```bash
   ./lab-documenter.py --export-cacti
   ```

2. Review `documentation/cacti_devices.csv`

3. Manually add devices through Cacti web interface using CSV as reference

## Example Output

### Bash Script (cacti_import.sh)

```bash
#!/bin/bash
#
# Cacti Device Import Script
# Generated by Lab Documenter on 2025-01-15 14:30:00
#
# This script uses Cacti's add_device.php CLI tool to import devices.
# Adjust CACTI_PATH if your Cacti CLI directory is different.
#
# Usage: sudo bash cacti_import.sh
#

CACTI_PATH="/usr/share/cacti/cli"
SNMP_COMMUNITY="public"
SNMP_VERSION="2"

# Check if Cacti CLI exists
if [ ! -f "$CACTI_PATH/add_device.php" ]; then
    echo "ERROR: Cacti CLI not found at $CACTI_PATH/add_device.php"
    echo "Please update CACTI_PATH in this script"
    exit 1
fi

echo "Starting Cacti device import..."
echo "========================================"

# Device: k8s-master1.homelab.local
# Platform: linux
# OS: Ubuntu 22.04.3 LTS
echo "Adding device: k8s-master1.homelab.local"
php -q "$CACTI_PATH/add_device.php" \
    --description="Kubernetes Master Node" \
    --ip="10.100.100.10" \
    --template=8 \
    --community="$SNMP_COMMUNITY" \
    --version="$SNMP_VERSION"

# Device: proxmox1.homelab.local
# Platform: proxmox
# OS: Debian GNU/Linux 12 (bookworm)
echo "Adding device: proxmox1.homelab.local"
php -q "$CACTI_PATH/add_device.php" \
    --description="Proxmox Hypervisor 1" \
    --ip="10.100.100.20" \
    --template=8 \
    --community="$SNMP_COMMUNITY" \
    --version="$SNMP_VERSION"

echo "========================================"
echo "Completed import of 2 devices"
echo "Check Cacti web interface to verify devices were added"
```

### CSV Reference (cacti_devices.csv)

```csv
hostname,ip_address,description,platform_type,os_name,template_id,reachable
"k8s-master1.homelab.local","10.100.100.10","Kubernetes Master Node","linux","Ubuntu 22.04.3 LTS",8,"Yes"
"proxmox1.homelab.local","10.100.100.20","Proxmox Hypervisor 1","proxmox","Debian GNU/Linux 12 (bookworm)",8,"Yes"
"nas1.homelab.local","10.100.100.30","Synology NAS","nas","Synology DSM 7.2",1,"Yes"
"dc1.homelab.local","10.100.100.40","Domain Controller","windows","Windows Server 2022",7,"Yes"
```

## Troubleshooting

### "Cacti CLI not found"

**Problem:** Script can't find Cacti's add_device.php

**Solutions:**
1. Update `--cacti-path` parameter with correct path
2. Or edit the generated script and change `CACTI_PATH` variable
3. Find your Cacti CLI path: `sudo find / -name add_device.php`

Common Cacti paths:
- `/usr/share/cacti/cli/`
- `/var/www/html/cacti/cli/`
- `/opt/cacti/cli/`

### "Device already exists"

**Problem:** Cacti reports device already exists

**Solutions:**
1. Remove duplicate lines from script for already-added devices
2. Check Cacti web interface: Console > Management > Devices
3. Delete existing device in Cacti if you want to re-add it

### Wrong Template ID

**Problem:** Devices added with incorrect template

**Solutions:**
1. List available templates on Cacti server:
   ```bash
   php -q /usr/share/cacti/cli/add_device.php --list-host-templates
   ```
2. Update TEMPLATE_MAPPING in `modules/cacti.py`
3. Or manually edit the bash script before running

### SNMP Issues

**Problem:** Devices added but not collecting data

**Solutions:**
1. Verify SNMP is enabled on target devices
2. Check SNMP community string matches
3. Verify firewall allows SNMP (UDP port 161)
4. Test SNMP manually: `snmpwalk -v2c -c public device_ip system`

### Permissions Error

**Problem:** "Permission denied" when running script

**Solutions:**
1. Make script executable: `chmod +x documentation/cacti_import.sh`
2. Run with sudo: `sudo bash documentation/cacti_import.sh`
3. Ensure you're running on Cacti server with proper permissions

## Advanced Customization

### Custom Template Mapping

Edit `modules/cacti.py` and modify the TEMPLATE_MAPPING dictionary:

```python
TEMPLATE_MAPPING = {
    'linux': 8,          # Your custom Linux template ID
    'windows': 15,       # Your custom Windows template ID
    'nas': 20,          # Your custom NAS template ID
    # ... add your custom mappings
}
```

### Adding SNMP v3 Support

For SNMP v3, you'll need to modify the bash script generation to include:
- `--username`
- `--password`
- `--authproto` (MD5 or SHA)
- `--privpass` (privacy password)
- `--privproto` (DES or AES)

Example modification in the generated script:
```bash
php -q "$CACTI_PATH/add_device.php" \
    --description="Device Name" \
    --ip="10.0.0.1" \
    --template=8 \
    --version=3 \
    --username="snmpuser" \
    --password="authpass" \
    --authproto="SHA" \
    --privpass="privpass" \
    --privproto="AES"
```

### Filtering Devices

To export only specific devices, you can:

1. Use the CSV file to selectively import
2. Modify the bash script to comment out unwanted devices
3. Filter in the Python code before export (custom modification)

## Integration with Existing Workflows

### Automated Daily Updates

Create a cron job to scan and update Cacti:

```bash
# /etc/cron.daily/lab-documenter-cacti
#!/bin/bash
cd /opt/lab-documenter
./lab-documenter.py --scan --export-cacti
scp documentation/cacti_import.sh cacti-server:/tmp/
ssh cacti-server "sudo bash /tmp/cacti_import.sh"
```

### CI/CD Pipeline Integration

```yaml
# .gitlab-ci.yml or similar
deploy-to-cacti:
  script:
    - ./lab-documenter.py --scan --export-cacti
    - scp documentation/cacti_import.sh ${CACTI_SERVER}:/tmp/
    - ssh ${CACTI_SERVER} "sudo bash /tmp/cacti_import.sh"
```

## Best Practices

1. **Always review generated script before running** - Verify settings are correct
2. **Test on a few devices first** - Comment out most devices, test with 2-3
3. **Backup Cacti database** - Before bulk imports, backup your Cacti database
4. **Use SNMP v2c or v3** - v1 is deprecated and less secure
5. **Document template mappings** - Keep notes on which templates work best
6. **Monitor initial graphs** - Check that graphs start populating after import
7. **Clean up failed imports** - Remove devices that don't work from Cacti

## Support and Contribution

If you encounter issues or have suggestions:
1. Check Cacti logs: `/var/www/html/cacti/log/cacti.log`
2. Review Lab Documenter logs: `logs/lab-documenter.log`
3. Test Cacti CLI manually first to isolate issues

## Related Documentation

- [Cacti Command Line Scripts](https://docs.cacti.net/Command-Line-Scripts.md)
- [Cacti Device Management](https://docs.cacti.net/Devices.md)
- [Lab Documenter Main README](README.md)

# License

This project is released under the MIT License. See LICENSE file for details.

# Support

For issues, questions, or contributions:
- Use verbose logging: `./lab-documenter.py --verbose`
- Check the troubleshooting section
- Review configuration with: `./lab-documenter.py --dry-run`
- Enable debug mode for detailed analysis
- Use SSH diagnostics: `./distribute-key.sh --diagnose <host>`

# Changelog

## v1.2.1 (Current)
- **Cacti Direct Import**: SSH-based automatic device import to Cacti monitoring
- **Automatic Device Updates**: Uses change_device.php for existing devices
- **Configurable Templates**: Template mapping in config.json for platform detection
- **Import Result Tracking**: Per-device status (Added/Updated/Failed/Skipped)
- **Multiple Export Formats**: JSON, CSV, and bash script generation
- **FQDN Support**: Prefers DNS names over IP addresses for devices
- **SSH Key Authentication**: Secure key-based access to Cacti server
- **Comprehensive Reporting**: Detailed import summaries with error categorization
- **Backup Methods**: Generates bash scripts for manual import fallback

## v1.2.0 
- **Port Pre-Check**: SSH port 22 availability check (2-second timeout) before authentication attempts
- **Buffered Logging**: Thread-safe sequential log output prevents message interleaving during concurrent operations
- **MAC Vendor Database**: Auto-learning OUI database with 139 pre-seeded vendors
- **Networking Info Module**: New `modules/networking_info.py` with MACVendorDatabase class
- **Performance**: Faster scanning by skipping SSH attempts on hosts without port 22 accessible
- **Database Auto-Growth**: MAC vendor database automatically grows via API lookups
- **Cleaner Output**: Each host's logs appear as complete sequential blocks
- **Future Ready**: Networking module includes documented expansion possibilities (DNS cache, device fingerprinting, etc.)

## v1.1.2
- **CSV Auto-Discovery**: Added `--csv-update` flag to automatically add successfully documented network-discovered hosts to CSV file
- **Intelligent CSV Entries**: Auto-generated entries include smart role and description suggestions based on detected platform and OS
- **CSV Structure Preservation**: Maintains existing CSV file format and field structure when adding new entries
- **Auto-Discovery Workflow**: Seamless integration between network scanning and CSV inventory management
- **Enhanced Logging**: Detailed logging of CSV update operations with summaries of added hosts

## v1.1.1
- **Connection Logging Optimization**: Eliminated paramiko authentication noise during detection
- **MAC Address Lookup**: Failed devices now show MAC addresses in connection summary
- **Vendor Identification**: Automatic device manufacturer lookup using MAC address APIs
- **Grouped Device Logging**: Fixed interlaced log messages between concurrent device scans
- **QNAP Compatibility**: Fixed parsing errors on QNAP NAS systems
- **Full Error Messages**: Removed error message truncation for better troubleshooting
- **Single Host Scanning**: Added `--network IP/32` support for scanning individual devices
- **Connection Context**: Device-specific logging prevents message mixing between hosts

## v1.1.0
- **SSH Diagnostics**: Connectivity troubleshooting with distribute-key.sh
- **Platform Detection**: Windows, NAS, and TrueNAS detection and refinement
- **Offline Mode**: `--use-existing-data` for fast iterative testing
- **Device Logging**: Clear device boundaries and connection summaries
- **Reverse DNS Lookups**: Identify failed devices by hostname in connection summary
- **Windows Features**: Server vs Client feature detection
- **Multi-Platform Authentication**: SSH keys, SSH passwords, and WinRM support
- **TrueNAS Support**: Core and Scale detection with FreeBSD compatibility

## v1.0.9
- **Template System**: Integrated Jinja2-based template engine for customizable output generation
- **Modular Templates**: Organized templates into reusable components (base, components, pages)
- **Template Fallback**: Graceful degradation when Jinja2 unavailable or templates missing
- **Backward Compatibility**: Maintained existing function signatures and behavior
- **Code Reduction**: Replaced 1,350+ lines of string concatenation with maintainable templates
- **Customization**: Non-programmers can modify output by editing template files
- **Architecture**: Clean separation of data processing and presentation logic

## v1.0.0 
- **Modular Architecture**: Separation into focused modules
- **Multiple Network Support**: Scan across multiple CIDR ranges simultaneously  
- **Service Discovery**: Auto-learning database with service categorization
- **Connection Failure Analysis**: Error categorization and reporting
- **Host Filtering**: ignore.csv support for skipping problematic hosts
- **Clean Operation**: Cleanup of generated files with --clean option
- **Local Documentation**: Always-generated Markdown files with indexing
- **MediaWiki Integration**: Automatic server page creation with configurable index page
- **Beep Notification**: Audio completion signal for long-running operations
- **Error Handling**: SSH connection retry logic and error classification
- **Individual JSON Files**: Separate JSON output per server for analysis tools
- **Configuration**: Support for both single and multiple network configurations
