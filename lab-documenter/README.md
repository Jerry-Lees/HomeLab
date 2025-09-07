# Lab Documenter

A comprehensive home lab documentation system that automatically discovers and documents servers, VMs, containers, and services across multiple platforms. Features intelligent network scanning, multi-platform authentication, detailed connection failure analysis, automatic service discovery, and MediaWiki integration with a flexible Jinja2 template system.

## Features

### Core Capabilities
- **Multi-Platform Support**: Windows (WinRM), Linux (SSH keys), and NAS systems (SSH passwords)
- **Intelligent Platform Detection**: Automatically detects and adapts to Windows, Linux, and NAS platforms
- **Cascade Authentication**: Smart connection priority system for mixed environments
- **Automatic Discovery**: Network scanning across multiple CIDR ranges to find live hosts
- **Clean Architecture**: Separation of concerns with dedicated modules for different functions
- **Template System**: Jinja2-based templates for customizable documentation output

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
- **Connection Failure Analysis**: Detailed categorization of connection failures with reverse DNS
- **Host Filtering**: ignore.csv support to skip problematic or irrelevant hosts
- **Multiple Network Ranges**: Scan across different subnets in a single run
- **Clean Operation**: Easy cleanup of generated documentation and logs
- **Offline Mode**: Process existing data without re-scanning for iterative development
- **Enhanced SSH Diagnostics**: Comprehensive connectivity troubleshooting tools
- **Beep Notification**: Audio completion signal for long-running scans

### Output Formats
- **Local Documentation**: Always creates Markdown files for each server plus master index
- **JSON Inventory**: Comprehensive raw data in structured format
- **Individual JSON Files**: Separate JSON file per server for analysis tools
- **MediaWiki Integration**: Automatic wiki page creation and updates with server index
- **Template-Based Generation**: Customizable output through Jinja2 templates

### Performance & Reliability
- **Concurrent Processing**: Multi-threaded scanning for faster execution
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

# Use existing data without re-scanning (offline mode)
./lab-documenter.py --use-existing-data --update-wiki

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
    "mediawiki_index_page": "Server Documentation"
}
```

### Multi-Platform Authentication

The system uses a cascade authentication approach for mixed environments:

1. **Windows Systems**: WinRM with username/password (port 5985)
2. **NAS Systems**: SSH with username/password (typically admin accounts)
3. **Linux Systems**: SSH with key-based authentication (most secure)

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
   - Some systems may require enabling SSH in settings

3. **Supported NAS platforms**:
   - Synology DSM
   - QNAP QTS  
   - Asustor ADM
   - Buffalo TeraStation
   - Netgear ReadyNAS
   - TrueNAS Core/Scale

## Command Line Options

### Operation Modes
- `--scan` - Scan network(s) for live hosts and collect data (multi-platform)
- `--csv-only` - Only scan hosts listed in CSV file (skip network scan)
- `--use-existing-data` - Use existing inventory.json without re-scanning (offline mode)
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
```

### Performance Tuning
```bash
# High-performance scanning for large networks
./lab-documenter.py --workers 20 --network "192.168.0.0/16" --scan

# Conservative scanning with longer timeouts
./lab-documenter.py --ssh-timeout 30 --workers 5 --scan
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
4. **Multi-Platform Connection**: Cascade authentication (Windows → NAS → Linux)
5. **Platform Detection**: Identifies and refines platform type (especially TrueNAS)
6. **Data Collection**: Platform-specific information gathering
7. **Service Enhancement**: Uses intelligent database to categorize services
8. **Template Rendering**: Processes Jinja2 templates with collected data
9. **Documentation Generation**: Creates local Markdown files and JSON data
10. **MediaWiki Updates**: Creates/updates individual server pages and index page
11. **Failure Analysis**: Categorizes and reports connection issues with reverse DNS

## Advanced Features

### Multi-Platform Connection Cascade

The system intelligently tries connection methods in priority order:
1. **Windows (WinRM)** - Tries NTLM, Kerberos, then Basic authentication
2. **NAS (SSH Password)** - For systems requiring password authentication
3. **Linux (SSH Keys)** - Most secure method for Linux systems

Platform detection is refined after connection to identify systems like TrueNAS that might initially appear as generic Linux.

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

### Connection Failure Analysis

Detailed categorization helps troubleshoot connectivity issues:

- **Windows authentication failed** - Check WinRM credentials and service status
- **SSH connection timeout** - Host doesn't respond within timeout period
- **SSH connection refused** - Host responds but SSH service not running  
- **SSH authentication failed** - SSH key or credential issues
- **DNS resolution failed** - Hostname cannot be resolved
- **Network unreachable** - Routing issues
- **WinRM connection refused** - WinRM service not running or port blocked

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
- `requests>=2.28.0` - HTTP/MediaWiki API
- `jinja2>=3.0.0` - Template processing
- `pywinrm>=0.4.3` - Windows WinRM connections

### Target Host Requirements

**Windows Systems**:
- WinRM service enabled
- Port 5985 accessible
- Local or domain user account with appropriate permissions

**Linux Systems**:
- SSH server running
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

## License

This project is released under the MIT License. See LICENSE file for details.

## Support

For issues, questions, or contributions:
- Use verbose logging: `./lab-documenter.py --verbose`
- Check the troubleshooting section
- Review configuration with: `./lab-documenter.py --dry-run`
- Enable debug mode for detailed analysis
- Use SSH diagnostics: `./distribute-key.sh --diagnose <host>`

## Changelog

### v1.1.1 (Current)
- **Connection Logging Optimization**: Eliminated paramiko authentication noise during detection
- **MAC Address Lookup**: Failed devices now show MAC addresses in connection summary
- **Vendor Identification**: Automatic device manufacturer lookup using MAC address APIs
- **Grouped Device Logging**: Fixed interlaced log messages between concurrent device scans
- **QNAP Compatibility**: Fixed parsing errors on QNAP NAS systems
- **Full Error Messages**: Removed error message truncation for better troubleshooting
- **Single Host Scanning**: Added `--network IP/32` support for scanning individual devices
- **Connection Context**: Device-specific logging prevents message mixing between hosts

### v1.1.0
- **SSH Diagnostics**: Connectivity troubleshooting with distribute-key.sh
- **Platform Detection**: Windows, NAS, and TrueNAS detection and refinement
- **Offline Mode**: `--use-existing-data` for fast iterative testing
- **Device Logging**: Clear device boundaries and connection summaries
- **Reverse DNS Lookups**: Identify failed devices by hostname in connection summary
- **Windows Features**: Server vs Client feature detection
- **Multi-Platform Authentication**: SSH keys, SSH passwords, and WinRM support
- **TrueNAS Support**: Core and Scale detection with FreeBSD compatibility

### v1.0.9
- **Template System**: Integrated Jinja2-based template engine for customizable output generation
- **Modular Templates**: Organized templates into reusable components (base, components, pages)
- **Template Fallback**: Graceful degradation when Jinja2 unavailable or templates missing
- **Backward Compatibility**: Maintained existing function signatures and behavior
- **Code Reduction**: Replaced 1,350+ lines of string concatenation with maintainable templates
- **Customization**: Non-programmers can modify output by editing template files
- **Architecture**: Clean separation of data processing and presentation logic

### v1.0.0 
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

