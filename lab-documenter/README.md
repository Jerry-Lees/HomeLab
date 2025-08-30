# Lab Documenter

A comprehensive home lab documentation system that automatically discovers and documents servers, VMs, containers, and services in your infrastructure. Features intelligent network scanning, detailed connection failure analysis, and automatic service discovery with a clean modular architecture.

## Features

### Core Capabilities
- **Automatic Discovery**: Network scanning across multiple CIDR ranges to find live hosts
- **Multi-Platform Support**: Ubuntu, Debian, CentOS, RHEL, Rocky Linux, Fedora, and other Linux distributions
- **Modular Architecture**: Clean separation of concerns with dedicated modules for different functions

### Data Collection
- **System Information**: OS, kernel, hardware, uptime, resource usage
- **Network Configuration**: IP addresses, listening ports with service identification
- **Running Services**: Systemd services with enhanced descriptions from intelligent database
- **Docker Containers**: Container names, images, and status information
- **Kubernetes Integration**: Cluster info, nodes, pods, services, deployments with issue detection
- **Proxmox Support**: VM and container listings on Proxmox hypervisors

### Advanced Features  
- **Smart Service Discovery**: Auto-learning database that categorizes unknown services
- **Connection Failure Analysis**: Detailed categorization of SSH connection failures
- **Host Filtering**: ignore.csv support to skip problematic or irrelevant hosts
- **Multiple Network Ranges**: Scan across different subnets in a single run
- **Clean Operation**: Easy cleanup of generated documentation and logs
- **Beep Notification**: Audio completion signal for long-running scans

### Output Formats
- **Local Documentation**: Always creates Markdown files for each server plus master index
- **JSON Inventory**: Comprehensive raw data in structured format
- **Individual JSON Files**: Separate JSON file per server for analysis tools
- **MediaWiki Integration**: Optional wiki page creation and updates

### Performance & Reliability
- **Concurrent Processing**: Multi-threaded scanning for faster execution
- **Secure SSH Access**: Key-based authentication with connection retry logic
- **Comprehensive Logging**: Detailed logs with intelligent error categorization
- **Flexible Configuration**: JSON config files with command-line overrides

## Quick Start

### Installation

1. **Automated Installation (Recommended)**:
   ```bash
   ./install.sh
   ```
   
   The installer automatically:
   - Installs system dependencies (may require sudo for packages)
   - Creates Python virtual environment in current directory
   - Generates SSH keys (`homelab_key`) for secure server access
   - Adds SSH key to ssh-agent
   - Creates configuration files with proper paths
   - Sets up daily cron job
   - Generates helper scripts

2. **Manual Installation**:
   ```bash
   # Install dependencies
   pip3 install paramiko requests
   
   # Copy script to desired location
   chmod +x lab-documenter.py
   ```

### Basic Usage

```bash
# Scan your networks and create local documentation
./lab-documenter.py --scan

# Only scan servers listed in CSV file
./lab-documenter.py --csv-only

# Scan networks and also update MediaWiki pages  
./lab-documenter.py --scan --update-wiki

# Clean old files then perform fresh scan
./lab-documenter.py --clean --scan

# Show help with all options
./lab-documenter.py --help
```

## Configuration

### Configuration File (`config.json`)

```json
{
    "ssh_user": "your_admin_user",
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
    "mediawiki_api": "http://wiki.homelab.local/api.php",
    "mediawiki_user": "documentation_bot",
    "mediawiki_password": "your_bot_password"
}
```

### Server List (`servers.csv`)

Optional file to specify servers manually:

```csv
hostname,description,role,location
server1.homelab.local,Main file server,NAS,Rack 1
k8s-master.homelab.local,Kubernetes master node,K8s Master,Rack 1
proxmox1.homelab.local,Proxmox hypervisor,Virtualization,Rack 2
192.168.1.100,Docker host,Container Host,Rack 1
ubuntu-vm1,Development VM,Development,Virtual
```

### Ignore List (`ignore.csv`)

Skip problematic hosts that will never be reachable:

```csv
IP or hostname,notes about the device
192.168.1.1,Router management interface  
192.168.1.50,Broken server that never responds
printer.local,Network printer without SSH
old-server.local,Decommissioned equipment
# 192.168.1.200,This line is commented out
```

## Command Line Options

### Operation Modes
- `--scan` - Scan network(s) for live hosts and collect data
- `--csv-only` - Only scan hosts listed in CSV file (skip network scan)
- `--update-wiki` - Update MediaWiki pages with collected data
- `--clean` - Delete all files in ./documentation and ./logs directories
- `--dry-run` - Show what would be done without making changes

### File Paths
- `--config FILE` - Configuration file path (default: config.json)
- `--csv FILE` - CSV file containing server list (default: servers.csv)
- `--output FILE` - Output JSON file path (default: inventory.json)

### Network Settings
- `--network RANGES` - Network range(s) to scan:
  - Single: `--network 192.168.1.0/24`
  - Multiple: `--network "192.168.1.0/24,10.0.0.0/24,172.16.0.0/16"`
- `--ssh-user USER` - SSH username for server connections
- `--ssh-key FILE` - SSH private key file path
- `--ssh-timeout SECONDS` - SSH connection timeout

### Performance Settings
- `--workers N` - Number of concurrent workers for scanning

### MediaWiki Settings
- `--wiki-api URL` - MediaWiki API URL
- `--wiki-user USER` - MediaWiki username
- `--wiki-password PASS` - MediaWiki password

### Output Settings
- `--verbose, -v` - Enable verbose logging output
- `--quiet, -q` - Suppress all output except errors

## Usage Examples

### Basic Operations
```bash
# Simple network scan with local documentation
./lab-documenter.py --scan

# Scan only CSV hosts with verbose output
./lab-documenter.py --csv-only --verbose

# Test configuration without making changes
./lab-documenter.py --dry-run --scan --update-wiki

# Clean old files and create fresh documentation
./lab-documenter.py --clean --scan --update-wiki
```

### Multiple Network Scanning
```bash
# Scan multiple networks from command line
./lab-documenter.py --network "192.168.1.0/24,10.0.0.0/24" --scan

# Use config file with multiple network ranges
./lab-documenter.py --scan  # Uses network_ranges from config.json
```

### Custom Configuration
```bash
# Use custom configuration file
./lab-documenter.py --config production.json --scan

# Override CSV file location
./lab-documenter.py --csv /tmp/test-servers.csv --csv-only

# Save output to custom location with timestamp
./lab-documenter.py --output inventory-$(date +%Y%m%d).json --scan
```

### Performance Tuning
```bash
# High-performance scanning for large networks
./lab-documenter.py --workers 20 --network "192.168.0.0/16" --scan

# Conservative scanning with longer timeouts
./lab-documenter.py --ssh-timeout 30 --workers 5 --scan
```

## Output Format

### 1. Local Documentation (Always Created)

The script creates a `documentation/` folder containing:

**Individual Server Files**: `documentation/server1.homelab.local.md`
```markdown
# server1.homelab.local

**Last Updated:** 2025-08-29T10:30:00

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
```

**Master Index**: `documentation/index.md` with server summaries and status
**Individual JSON Files**: `documentation/server1.homelab.local.json` for each server

### 2. Connection Failure Analysis

Detailed failure categorization at the end of each run:

```
============================================================
CONNECTION SUMMARY
============================================================
Failed to connect to 5 devices:

• Connection timeout (waited 10s) (3 devices):
  - 192.168.1.131
  - 192.168.1.92  
  - 192.168.1.7

• Connection refused (SSH service may not be running) (2 devices):
  - 192.168.1.50 (hostname: old-server.local)
  - 10.0.0.100 (hostname: printer.local)
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
    "timestamp": "2025-08-29T10:30:00",
    "reachable": true,
    "os_release": {
      "name": "Ubuntu",
      "version": "24.04.3 LTS", 
      "pretty_name": "Ubuntu 24.04.3 LTS"
    },
    "services": [...],
    "docker_containers": [...],
    "kubernetes_info": {...},
    "listening_ports": [...]
  }
}
```

## Architecture

### Modular Design

The system uses a clean modular architecture:

```
lab-documenter/
├── lab-documenter.py          # Main CLI interface and orchestration
├── modules/
│   ├── __init__.py           # Package initialization
│   ├── config.py             # Configuration management
│   ├── network.py            # Network scanning functionality
│   ├── system.py             # SSH connections and data collection
│   ├── services.py           # Service database management
│   ├── inventory.py          # Host data aggregation
│   ├── documentation.py      # Markdown/MediaWiki generation
│   ├── wiki.py              # MediaWiki API integration
│   └── utils.py              # Utility functions and helpers
├── config.json               # Main configuration
├── servers.csv              # Optional server list
├── ignore.csv               # Optional ignore list
├── services.json            # Auto-learning services database
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
4. **SSH Data Collection**: Concurrent connections to gather system information
5. **Service Enhancement**: Uses intelligent database to categorize services
6. **Documentation Generation**: Creates local Markdown files and JSON data
7. **Optional Wiki Updates**: Pushes to MediaWiki if configured
8. **Failure Analysis**: Categorizes and reports connection issues

## Advanced Features

### Multiple Network Support

Configure multiple networks in `config.json`:
```json
{
    "network_ranges": [
        "192.168.1.0/24",      // Main LAN
        "10.100.100.0/24",     // DMZ network  
        "172.16.0.0/12",       // Container network
        "10.200.200.0/24"      // Lab network
    ]
}
```

Or specify from command line:
```bash
./lab-documenter.py --network "192.168.1.0/24,10.0.0.0/8" --scan
```

### Intelligent Service Discovery

The `services.json` database automatically learns about new services:

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

Unknown services are auto-added and can be manually enhanced.

### Connection Failure Analysis

Detailed categorization helps troubleshoot connectivity issues:

- **Connection timeout** - Host doesn't respond within timeout period
- **Connection refused** - Host responds but SSH service not running  
- **Authentication failed** - SSH key or credential issues
- **DNS resolution failed** - Hostname cannot be resolved
- **Network unreachable** - Routing issues
- **SSH key compatibility** - Key format problems (DSA, etc.)

## MediaWiki Integration

### Setup

1. Create a bot user in MediaWiki
2. Grant appropriate permissions (edit pages, create pages)
3. Configure API URL and credentials in `config.json`
4. Use `--update-wiki` flag when running

### Generated Pages

MediaWiki pages follow the format `Server:hostname` and include:
- System information (OS, kernel, hardware)
- Resource usage (memory, disk, CPU load)
- Network configuration and listening ports
- Running services with descriptions
- Docker containers and Kubernetes information
- Proxmox VM/container lists

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
- **`distribute-key.sh`**: Copies SSH key to multiple servers
- **`run-lab-documenter.sh`**: Runs with proper environment

## Security

### SSH Configuration

The installer creates dedicated SSH keys for secure access:

```bash
# Distribute key to servers
./distribute-key.sh admin@192.168.1.100 ubuntu@server.local

# Or manually copy to individual servers
ssh-copy-id -i .ssh/homelab_key.pub user@server
```

### Best Practices

- Use dedicated SSH keys (not personal keys)
- Restrict SSH key access to read-only operations where possible
- Store MediaWiki credentials securely
- Use firewall rules to restrict network access
- Regularly rotate SSH keys and passwords
- Keep sensitive configuration files secure

## Troubleshooting

### Common Issues

**SSH Connection Failures**:
```bash
# Test SSH connectivity manually
ssh -i .ssh/homelab_key user@server

# Check SSH key permissions
chmod 600 .ssh/homelab_key
chmod 644 .ssh/homelab_key.pub

# Add key to ssh-agent
./setup-ssh-agent.sh
```

**Network Discovery Issues**:
```bash
# Test with verbose output and dry run
./lab-documenter.py --verbose --dry-run --scan

# Check specific network connectivity
ping -c 1 192.168.1.1
```

**MediaWiki Update Failures**:
```bash
# Test MediaWiki API
curl -X POST "http://wiki.local/api.php" \
     -d "action=query&meta=siteinfo&format=json"
```

### Debug Mode

```bash
# Maximum verbosity
./lab-documenter.py --verbose --scan

# Check what would be done without changes
./lab-documenter.py --dry-run --scan --update-wiki

# View logs
tail -f ./logs/lab-documenter.log
```

## Requirements

### System Requirements
- Python 3.6+
- SSH client
- Network connectivity to target hosts
- Cron daemon (for automation)

### Python Dependencies
- `paramiko>=2.11.0` - SSH connections
- `requests>=2.28.0` - HTTP/MediaWiki API

### Target Host Requirements
- SSH server running
- SSH key-based authentication configured  
- Linux operating system (Ubuntu, Debian, CentOS, RHEL, etc.)
- Standard command-line tools (ps, df, free, etc.)

### Optional Requirements
- `kubectl` - For Kubernetes cluster information
- `docker` - For Docker container information
- `pveversion`, `qm`, `pct` - For Proxmox information

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes with proper modular separation
4. Add appropriate logging and error handling
5. Update documentation and help text
6. Test with `--dry-run` and `--verbose` modes
7. Submit a pull request

## License

This project is released under the MIT License. See LICENSE file for details.

## Support

For issues, questions, or contributions:
- Use verbose logging: `./lab-documenter.py --verbose`
- Check the troubleshooting section
- Review configuration with: `./lab-documenter.py --dry-run`
- Enable debug mode for detailed analysis

## Changelog

### v1.0.0 (Current)
- **Modular Architecture**: Clean separation into focused modules
- **Multiple Network Support**: Scan across multiple CIDR ranges simultaneously  
- **Intelligent Service Discovery**: Auto-learning database with service categorization
- **Connection Failure Analysis**: Detailed error categorization and reporting
- **Host Filtering**: ignore.csv support for skipping problematic hosts
- **Clean Operation**: Easy cleanup of generated files with --clean option
- **Enhanced Documentation**: Always-generated local Markdown files with comprehensive indexing
- **Beep Notification**: Audio completion signal for long-running operations
- **Improved Error Handling**: Better SSH connection retry logic and error classification
- **Individual JSON Files**: Separate JSON output per server for analysis tools
- **Flexible Configuration**: Support for both single and multiple network configurations

