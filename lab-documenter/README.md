# Lab Documenter

A comprehensive home lab documentation system that automatically discovers and documents servers, VMs, containers, and services in your infrastructure. Features intelligent network scanning, detailed connection failure analysis, automatic service discovery, and MediaWiki integration with a flexible Jinja2 template system.

## Features

### Core Capabilities
- **Automatic Discovery**: Network scanning across multiple CIDR ranges to find live hosts
- **Multi-Platform Support**: Ubuntu, Debian, CentOS, RHEL, Rocky Linux, Fedora, and other Linux distributions
- **Clean Architecture**: Separation of concerns with dedicated modules for different functions
- **Template System**: Jinja2-based templates for customizable documentation output

### Data Collection
- **System Information**: OS, kernel, hardware, uptime, resource usage
- **Network Configuration**: IP addresses, listening ports with service identification
- **Running Services**: Systemd services with intelligent descriptions from auto-learning database
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
- **MediaWiki Integration**: Automatic wiki page creation and updates with server index
- **Template-Based Generation**: Customizable output through Jinja2 templates

### Performance & Reliability
- **Concurrent Processing**: Multi-threaded scanning for faster execution
- **Secure SSH Access**: Key-based authentication with connection retry logic
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
   - Generates helper scripts

2. **Manual Installation**:
   ```bash
   # Install dependencies
   pip3 install paramiko requests jinja2
   
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
    "mediawiki_api": "https://wiki.example.com/api.php",
    "mediawiki_user": "documentation_bot",
    "mediawiki_password": "your_bot_password",
    "mediawiki_index_page": "Server Documentation"
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

## Template System

Lab Documenter uses Jinja2 templates for flexible documentation generation. Templates are organized in a structured hierarchy:

### Template Structure
```
templates/
├── base/
│   ├── macros.j2                 # Reusable macros
│   └── server_base.md.j2         # Base Markdown template
├── components/
│   ├── services.md.j2            # Services section
│   ├── kubernetes.md.j2          # Kubernetes section
│   ├── proxmox.md.j2            # Proxmox section
│   ├── memory_modules.md.j2      # Memory information
│   ├── docker_containers.md.j2   # Docker containers
│   └── listening_ports.md.j2     # Network ports
└── pages/
    ├── server.md.j2              # Main server page (Markdown)
    ├── server.wiki.j2            # Main server page (MediaWiki)
    ├── index.md.j2               # Markdown index
    └── index.wiki.j2             # MediaWiki index
```

### Template Features
- **Variable Substitution**: `{{ hostname }}`, `{{ os_release.pretty_name }}`
- **Conditionals**: `{% if kubernetes_info %}...{% endif %}`
- **Loops**: `{% for service in services %}...{% endfor %}`
- **Includes**: `{% include 'components/kubernetes.md.j2' %}`
- **Macros**: Reusable formatting functions with parameters
- **Filters**: Built-in and custom data transformation functions

### Customizing Templates

Templates can be modified to change output formatting without touching Python code:

```jinja2
{# Example: Customize service display #}
{% for service in services %}
- **{{ service.display_name }}** ({{ service.status }})
  {%- if service.description %} - {{ service.description }}{% endif %}
{% endfor %}
```

### Template Fallback

If Jinja2 is not available or templates are missing, the system automatically falls back to basic content generation with appropriate warnings.

## Command Line Options

### Operation Modes
- `--scan` - Scan network(s) for live hosts and collect data
- `--csv-only` - Only scan hosts listed in CSV file (skip network scan)
- `--update-wiki` - Update MediaWiki pages with collected data
- `--update-wiki-index` - Create or update the wiki server index page
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
- `--wiki-index-page TITLE` - Title for the wiki server index page

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

### MediaWiki Operations
```bash
# Scan and update both individual server pages and index
./lab-documenter.py --scan --update-wiki

# Update only the wiki index page from existing data
./lab-documenter.py --update-wiki-index

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
- System information (OS, kernel, hardware, uptime)
- Resource usage (memory, disk, CPU load)
- Network configuration and listening ports
- Running services with descriptions
- Docker containers and Kubernetes information
- Proxmox VM/container lists

**Server Index Page**: Creates a configurable main index page featuring:
- Quick statistics (total servers, reachable/unreachable counts)
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

### Module Organization

The system uses a clean architecture with focused modules:

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
6. **Template Rendering**: Processes Jinja2 templates with collected data
7. **Documentation Generation**: Creates local Markdown files and JSON data
8. **MediaWiki Updates**: Creates/updates individual server pages and index page
9. **Failure Analysis**: Categorizes and reports connection issues

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

### Connection Failure Analysis

Detailed categorization helps troubleshoot connectivity issues:

- **Connection timeout** - Host doesn't respond within timeout period
- **Connection refused** - Host responds but SSH service not running  
- **Authentication failed** - SSH key or credential issues
- **DNS resolution failed** - Hostname cannot be resolved
- **Network unreachable** - Routing issues
- **SSH key compatibility** - Key format problems (DSA, etc.)

### Template Customization

Templates support advanced features for customizable output:

```jinja2
{# Collapsible sections with smart thresholds #}
{% from 'base/macros.j2' import collapsible_list_md %}
{% call collapsible_list_md(services, "Services", 5) %}
- {{ format_service(this) }}
{% endcall %}

{# Conditional content based on data presence #}
{% if kubernetes_info and kubernetes_info.pods %}
### Kubernetes Cluster
{% for namespace, pods in kubernetes_info.pods|groupby('namespace') %}
**{{ namespace }}**: {{ pods|list|length }} pods
{% endfor %}
{% endif %}
```

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
# Test MediaWiki API connectivity
curl -X POST "https://your-wiki.com/api.php" \
     -d "action=query&meta=siteinfo&format=json"

# Verify bot permissions in MediaWiki user management
# Check API is enabled in LocalSettings.php
```

**Template Errors**:
```bash
# Check template syntax with dry run
./lab-documenter.py --dry-run --verbose --scan

# Verify Jinja2 installation
python3 -c "import jinja2; print(jinja2.__version__)"
```

**JSON Configuration Errors**:
```bash
# Validate JSON syntax
python3 -m json.tool config.json

# Common issues: missing commas, unquoted keys, extra trailing commas
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
- MediaWiki installation (for wiki integration)

### Python Dependencies
- `paramiko>=2.11.0` - SSH connections
- `requests>=2.28.0` - HTTP/MediaWiki API
- `jinja2>=3.0.0` - Template processing

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
3. Make your changes with proper separation of concerns
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

### v1.1.0 (Current)
- **Enhanced SSH Diagnostics**: Comprehensive connectivity troubleshooting with distribute-key.sh
- **Platform Detection Improvements**: Better Windows, NAS, and TrueNAS detection
- **Offline Mode**: `--use-existing-data` for fast iterative testing
- **Enhanced Logging**: Clear device boundaries and connection summaries
- **Reverse DNS Lookups**: Identify failed devices by hostname in connection summary
- **Windows Features Fix**: Proper Server vs Client feature detection
- **Multi-Platform Authentication**: SSH keys, SSH passwords, and WinRM support
- **TrueNAS Support**: Full Core and Scale detection with FreeBSD compatibility

### v1.0.9 (Current)
- **Template System**: Integrated Jinja2-based template engine for customizable output generation
- **Modular Templates**: Organized templates into reusable components (base, components, pages)
- **Template Fallback**: Graceful degradation when Jinja2 unavailable or templates missing
- **Backward Compatibility**: Maintained existing function signatures and behavior
- **Code Reduction**: Replaced 1,350+ lines of string concatenation with maintainable templates
- **Enhanced Customization**: Non-programmers can modify output by editing template files
- **Improved Architecture**: Clean separation of data processing and presentation logic

### v1.0.0 
- **Clean Architecture**: Separation into focused modules
- **Multiple Network Support**: Scan across multiple CIDR ranges simultaneously  
- **Intelligent Service Discovery**: Auto-learning database with service categorization
- **Connection Failure Analysis**: Detailed error categorization and reporting
- **Host Filtering**: ignore.csv support for skipping problematic hosts
- **Clean Operation**: Easy cleanup of generated files with --clean option
- **Local Documentation**: Always-generated Markdown files with comprehensive indexing
- **MediaWiki Integration**: Automatic server page creation with configurable index page
- **Beep Notification**: Audio completion signal for long-running operations
- **Error Handling**: SSH connection retry logic and error classification
- **Individual JSON Files**: Separate JSON output per server for analysis tools
- **Flexible Configuration**: Support for both single and multiple network configurations

