# Lab Documenter

A comprehensive home lab documentation system that automatically discovers and documents servers, VMs, containers, and services in your infrastructure. Collects system information, running services, Docker containers, Kubernetes resources, and Proxmox VMs/containers, then outputs structured data and optionally updates MediaWiki documentation.

## Features

- **Automatic Discovery**: Network scanning to find live hosts
- **Multi-Platform Support**: Ubuntu, Debian, CentOS, RHEL, Rocky Linux, Fedora
- **Comprehensive Data Collection**:
  - System information (OS, kernel, hardware)
  - Resource usage (CPU, memory, disk)
  - Network configuration and listening ports
  - Running services and processes
  - Docker containers
  - Kubernetes cluster information (nodes, pods, services, deployments, issues)
  - Proxmox VMs and containers
- **Flexible Configuration**: JSON config files with command-line overrides
- **Local Documentation**: Automatically creates Markdown files for each server
- **MediaWiki Integration**: Optional wiki page updates
- **Concurrent Processing**: Multi-threaded scanning for performance
- **Secure SSH Access**: Key-based authentication
- **Comprehensive Logging**: Detailed logs with rotation

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
   - Creates configuration files
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
# Scan your network and create local documentation
lab-documenter --scan

# Only scan servers listed in CSV file
lab-documenter --csv-only

# Scan network and also update MediaWiki pages
lab-documenter --scan --update-wiki

# Show help with all options
lab-documenter --help
```

## Configuration

### Configuration File (`config.json`)

Modify the repository's `config.json` file to fit your environment:

```json
{
    "ssh_user": "your_admin_user",
    "ssh_key_path": "./.ssh/homelab_key",
    "network_range": "192.168.1.0/24",
    "ssh_timeout": 10,
    "max_workers": 5,
    "output_file": "./documentation/inventory.json",
    "csv_file": "./servers.csv",
    "mediawiki_api": "http://wiki.homelab.local/api.php",
    "mediawiki_user": "documentation_bot",
    "mediawiki_password": "your_bot_password"
}
```

### Server List (`servers.csv`)

Modify the repository's `servers.csv` file to specify your servers manually: (required only if not using the --scan option mentioned below.)

```csv
hostname,description,role,location
server1.homelab.local,Main file server,NAS,Rack 1
k8s-master.homelab.local,Kubernetes master node,K8s Master,Rack 1
proxmox1.homelab.local,Proxmox hypervisor,Virtualization,Rack 2
192.168.1.100,Docker host,Container Host,Rack 1
ubuntu-vm1,Development VM,Development,Virtual
```

## Command Line Options

### Operation Modes
- `--scan` - Scan network for live hosts and collect data
- `--csv-only` - Only scan hosts listed in CSV file (skip network scan)
- `--update-wiki` - Update MediaWiki pages with collected data
- `--dry-run` - Show what would be done without making changes

### File Paths
- `--config FILE` - Configuration file path (default: config.json)
- `--csv FILE` - CSV file containing server list (default: servers.csv)
- `--output FILE` - Output JSON file path (default: homelab_inventory.json)

### Network Settings
- `--network CIDR` - Network range to scan (e.g., 192.168.1.0/24)
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
lab-documenter --scan

# Scan only CSV hosts with verbose output
lab-documenter --csv-only --verbose

# Test configuration without making changes
lab-documenter --dry-run --scan --update-wiki

# Create local docs and update MediaWiki
lab-documenter --scan --update-wiki
```

### Custom Configuration
```bash
# Use custom configuration file
lab-documenter --config /etc/lab-documenter/production.json --scan

# Override CSV file location
lab-documenter --csv /tmp/test-servers.csv --csv-only

# Save output to custom location
lab-documenter --output /backup/inventory-$(date +%Y%m%d).json --scan
```

### Network Customization
```bash
# Scan specific network range
lab-documenter --network 10.0.0.0/16 --scan

# Use specific SSH settings
lab-documenter --ssh-user admin --ssh-key ~/.ssh/homelab_rsa --scan

# High-performance scanning
lab-documenter --workers 20 --network 192.168.0.0/16 --scan
```

### MediaWiki Integration
```bash
# Update wiki with custom credentials
lab-documenter --wiki-api http://wiki.example.com/api.php \
               --wiki-user bot \
               --wiki-password secret123 \
               --scan --update-wiki
```

## Output Format

The script generates multiple types of output:

### 1. JSON Inventory File

Comprehensive raw data saved to `homelab_inventory.json`:

```json
{
  "server1.homelab.local": {
    "hostname": "server1.homelab.local",
    "timestamp": "2025-08-25T10:30:00",
    "reachable": true,
    "os_release": "Ubuntu 22.04 LTS",
    "kernel": "5.15.0-56-generic",
    "architecture": "x86_64",
    "uptime": "up 15 days, 6 hours, 23 minutes",
    "cpu_info": "Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz",
    "cpu_cores": "8",
    "memory_total": "32G",
    "memory_used": "8.2G",
    "disk_usage": "250G/1.0T (25%)",
    "ip_addresses": "192.168.1.100/24\n10.0.0.100/16",
    "services": [
      {"name": "ssh.service", "status": "active"},
      {"name": "docker.service", "status": "active"}
    ],
    "docker_containers": [
      {
        "name": "nginx-proxy",
        "image": "nginx:latest",
        "status": "Up 2 days"
      }
    ],
    "listening_ports": [
      {"port": "0.0.0.0:22", "process": "sshd"},
      {"port": "0.0.0.0:80", "process": "nginx"}
    ],
    "kubernetes_info": {
      "kubectl_version": "v1.28.0",
      "nodes": ["master-1", "worker-1", "worker-2"],
      "namespaces": ["default", "kube-system", "monitoring"]
    }
  }
}
```

### 2. Local Documentation Files (Always Created)

The script automatically creates a `documentation/` folder with:

**Individual Server Files:** `documentation/server1.homelab.local.md`
```markdown
# server1.homelab.local

### **3. Enhanced OS Information**
Should mention that OS information is now parsed and structured:
```markdown
### Structured OS Data
The script now parses `/etc/os-release` into structured data:
- **OS:** Ubuntu 24.04.3 LTS
- **Version:** 24.04.3 LTS (Noble)
- **Distribution:** Ubuntu (based on Debian)

### 4. Individual JSON Files

Each server also gets its own JSON file:
- `documentation/server1.homelab.local.json`
- Perfect for visualization tools or individual analysis
- Same data structure as the main inventory
- **Smart Hostname Detection**: Uses FQDN/hostname instead of IP addresses when possible
- Files named by hostname: `server1.example.com.md` instead of `192.168.1.100.md`

**Last Updated:** 2025-08-25T10:30:00

## System Information
- **OS:** Ubuntu 22.04 LTS
- **Kernel:** 5.15.0-56-generic
- **Architecture:** x86_64
- **Uptime:** up 15 days, 6 hours, 23 minutes
- **CPU:** Intel(R) Core(TM) i7-8700K CPU @ 3.70GHz (8 cores)

## Resources
- **Memory:** 8.2G / 32G
- **Disk Usage:** 250G/1.0T (25%)
- **Load Average:** 0.15, 0.22, 0.18

## Network
- **IP Addresses:**
```
192.168.1.100/24
10.0.0.100/16
```

## Services
- ssh.service (active)
- docker.service (active)

## Docker Containers
- **Name:** nginx-proxy, **Image:** nginx:latest, **Status:** Up 2 days
```

**Master Index File:** `documentation/index.md`
```markdown
# Lab Documentation Index

Generated on: 2025-08-25 10:30:00

## Documented Servers

### Active Servers

- **[server1.homelab.local](server1.homelab.local.md)** - Ubuntu 22.04 LTS - up 15 days, 6 hours, 23 minutes
- **[k8s-master.homelab.local](k8s-master.homelab.local.md)** - Ubuntu 22.04 LTS - up 5 days, 2 hours, 10 minutes

### Unreachable Servers

- **old-server.homelab.local** - Last attempt: 2025-08-25T10:30:00

---

**Total Servers:** 3 (2 reachable, 1 unreachable)
```

### 3. Directory Structure

```
lab-documenter/
├── lab-documenter.py
├── config.json
├── servers.csv
├── homelab_inventory.json
└── documentation/
    ├── index.md
    ├── server1.homelab.local.md
    ├── k8s-master.homelab.local.md
    └── proxmox1.homelab.local.md
```

## Local Documentation Files

The script **always** creates local documentation files in a `documentation/` folder. This provides:

### Benefits
- **Offline Access**: Documentation available without network connectivity
- **Version Control**: Can be committed to git for change tracking  
- **Backup**: Easy to backup with standard file backup tools
- **Portability**: Standard Markdown format readable anywhere
- **Fast Access**: No need to wait for wiki or web interface

### Generated Files
- **Individual server files**: `documentation/servername.md` for each reachable host
- **Master index**: `documentation/index.md` listing all servers with status
- **Automatic updates**: Recreated every time the script runs
- **Safe filenames**: Hostnames sanitized for filesystem compatibility

### File Format
- Clean Markdown format (not MediaWiki syntax)
- Same comprehensive information as MediaWiki pages
- Structured sections for easy reading
- Code blocks for configuration data
- Cross-linked from the index file

When `--update-wiki` is specified, the script creates or updates MediaWiki pages with the format `Server:hostname`. Each page includes the same information as the local documentation files but formatted in MediaWiki markup.

MediaWiki pages include:
- System Information (OS, kernel, hardware)
- Resource Usage (memory, disk, CPU load)
- Network Configuration
- Running Services
- Docker Containers (if present)
- Kubernetes Information (if present)
- Proxmox Information (if present)

### MediaWiki Setup

1. Create a bot user in MediaWiki
2. Grant appropriate permissions (edit pages, create pages)
3. Configure the API URL and credentials in `config.json`
4. Use `--update-wiki` flag when running the script

**Note:** Local documentation files are always created regardless of MediaWiki settings.

## Services Database

The script includes an intelligent services database that:
- **Automatically discovers** unknown services and adds them to `services.json`
- **Enhances documentation** with service descriptions, categories, and access information
- **Self-updating** - grows as you scan different systems
- **Customizable** - edit `services.json` to add your own service information

### Auto-Discovery
When unknown services are found, they're automatically added:
```json
{
  "new-service": {
    "display_name": "New Service",
    "description": "Unknown service - discovered on 2025-08-26",
    "category": "unknown",
    "_auto_generated": true
  }
}

## Automation

### Cron Job (Default)

The installer creates a daily cron job that runs at 6 AM:

```bash
# Check cron job status
crontab -l

# Edit cron schedule
crontab -e

# View cron logs
tail -f ./logs/cron.log

# Manual cron job creation (if needed)
0 6 * * * /path/to/lab-documenter/lab-documenter-cron.sh
```

### Customizing Schedule

Edit your crontab to change when the script runs:
```bash
crontab -e

# Examples:
# Every 4 hours: 0 */4 * * *
# Twice daily: 0 6,18 * * *  
# Weekly: 0 6 * * 0
```

## Security Considerations

### SSH Key Setup

The installer creates a dedicated SSH key (`homelab_key`) for secure, passwordless access to your servers.

#### Automated Setup (via installer)
```bash
# The installer automatically:
# 1. Generates homelab_key SSH key pair
# 2. Adds key to ssh-agent
# 3. Creates helper scripts

# Distribute key to your servers
./distribute-key.sh user@server1 user@server2

# Or manually copy to each server
ssh-copy-id -i .ssh/homelab_key.pub user@server
```

#### Manual Setup
```bash
# Generate SSH key
ssh-keygen -t rsa -b 4096 -f ~/.ssh/homelab_key -C "lab-documenter@$(hostname)"

# Add to ssh-agent
ssh-add ~/.ssh/homelab_key

# Copy to servers
ssh-copy-id -i ~/.ssh/homelab_key.pub user@server
```

#### Helper Scripts (Created by Installer)
- **`setup-ssh-agent.sh`**: Adds the SSH key to your ssh-agent session
- **`distribute-key.sh`**: Bulk copy SSH key to multiple servers

```bash
# Add key to ssh-agent (if needed)
./setup-ssh-agent.sh

# Distribute to multiple servers at once
./distribute-key.sh admin@192.168.1.100 ubuntu@server.local
```

### Best Practices

- Use the dedicated `homelab_key` SSH key generated by the installer
- Restrict SSH key access to read-only operations on target servers
- Store MediaWiki credentials securely (consider using bot passwords)
- Run the script from a secure, dedicated directory
- Regularly rotate SSH keys and passwords
- Use firewall rules to restrict network access
- Keep the SSH key secure and backed up
- Use specific usernames (ubuntu, admin) rather than personal usernames

## Troubleshooting

### Common Issues

**SSH Connection Failures**:
```bash
# Test SSH connectivity manually
ssh -i .ssh/homelab_key user@server

# Check SSH key permissions
chmod 600 .ssh/homelab_key
chmod 644 .ssh/homelab_key.pub

# Add key to ssh-agent if needed
./setup-ssh-agent.sh

# Verify ssh-agent has the key loaded
ssh-add -l
```

**Network Discovery Issues**:
```bash
# Test network connectivity
lab-documenter --verbose --network 192.168.1.0/24 --dry-run --scan

# Check if ping works
ping -c 1 192.168.1.1
```

**MediaWiki Update Failures**:
```bash
# Test MediaWiki API connectivity
curl -X POST "http://wiki.homelab.local/api.php" \
     -d "action=query&meta=siteinfo&format=json"
```

### Debugging

Enable verbose logging:
```bash
lab-documenter --verbose --scan
```

Check log files:
```bash
tail -f /var/log/lab-documenter/lab-documenter.log
```

Use dry-run mode to test configuration:
```bash
lab-documenter --dry-run --scan --update-wiki
```

## Architecture

### Components

- **NetworkScanner**: Discovers live hosts on the network
- **SystemCollector**: Connects to hosts and gathers system information
- **InventoryManager**: Manages data collection and storage
- **MediaWikiUpdater**: Updates documentation pages

### Data Flow

1. Load configuration from file and command line
2. Discover hosts via network scan and/or CSV file
3. Connect to each host via SSH
4. Collect comprehensive system information
5. Save data to JSON file
6. Optionally update MediaWiki pages

## Requirements

### System Requirements
- Python 3.6+
- SSH client
- Network connectivity to target hosts
- Cron daemon (for automation)

### Python Dependencies
- `paramiko` - SSH connections
- `requests` - HTTP/MediaWiki API
- `ipaddress` - Network operations (built-in Python 3.3+)

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
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is released under the MIT License. See LICENSE file for details.

## Support

For issues, questions, or contributions:
- Check the troubleshooting section above
- Review command line help: `lab-documenter --help`
- Enable verbose logging for debugging: `lab-documenter --verbose`

## Changelog

### v0.1.0
- Initial release
- Network scanning and host discovery
- Comprehensive system information collection
- Local Markdown documentation (always created)
- MediaWiki integration (optional)
- Multi-threaded processing
- Systemd integration
- Automated installation script

