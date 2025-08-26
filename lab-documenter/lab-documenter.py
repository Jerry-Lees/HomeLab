#!/usr/bin/env python3
"""
Lab Documenter
Discovers and documents servers, VMs, containers, and services in your home lab
"""

import json
import csv
import subprocess
import socket
import ipaddress
import paramiko
import requests
from datetime import datetime
import logging
import argparse
import os
import sys
from typing import Dict, List, Optional
import concurrent.futures
import time

# Set up logging
log_dir = 'logs'
os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'lab-documenter.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    'ssh_user': 'your_ssh_user',
    'ssh_key_path': '~/.ssh/id_rsa',
    'network_range': '192.168.1.0/24',
    'ssh_timeout': 5,
    'max_workers': 10,
    'output_file': 'documentation/inventory.json',
    'csv_file': 'servers.csv',
    'mediawiki_api': 'http://your-wiki.local/api.php',
    'mediawiki_user': 'bot_user',
    'mediawiki_password': 'bot_password'
}

class ServiceDatabase:
    def __init__(self, services_db_path: str = 'services.json'):
        self.services_db_path = services_db_path
        self.services_db = self.load_services_database()
        self.new_services_added = False
    
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
    
    def save_services_database(self):
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
    
    def add_unknown_service(self, service_name: str, process_info: str = None) -> Dict:
        """Add a new unknown service to the database"""
        timestamp = datetime.now().strftime('%Y-%m-%d')
        
        detected_ports = []
        if process_info:
            import re
            port_matches = re.findall(r':(\d+)', process_info)
            detected_ports = list(set(port_matches))
        
        new_service = {
            "display_name": service_name.replace('.service', '').replace('_', ' ').title(),
            "description": f"Unknown service - discovered on {timestamp}",
            "category": "unknown",
            "documentation_url": None,
            "support_url": None,
            "access": "Unknown - please update this entry",
            "notes": f"AUTO-GENERATED: This service was automatically discovered. Please update with actual service information.",
            "discovered_date": timestamp,
            "ports": detected_ports,
            "related_services": [],
            "_auto_generated": True
        }
        
        self.services_db[service_name] = new_service
        self.new_services_added = True
        logger.info(f"Added unknown service '{service_name}' to database")
        
        return new_service
    
    def get_service_info(self, service_name: str, process_info: str = None) -> Dict:
        """Get enhanced service information from database"""
        if service_name in self.services_db:
            return self.services_db[service_name]
        
        for db_service, info in self.services_db.items():
            if (service_name.startswith(db_service) or 
                service_name.endswith(db_service) or
                db_service in service_name):
                logger.debug(f"Matched {service_name} to database entry {db_service}")
                return info
        
        if process_info:
            for db_service, info in self.services_db.items():
                if db_service.lower() in process_info.lower():
                    logger.debug(f"Matched {service_name} via process info to {db_service}")
                    return info
        
        logger.debug(f"Service '{service_name}' not found in database, adding as unknown")
        return self.add_unknown_service(service_name, process_info)
    
    def enhance_service(self, service_name: str, status: str, process_info: str = None) -> Dict:
        """Enhance service information with database data"""
        base_info = {
            "name": service_name,
            "status": status,
            "process_info": process_info
        }
        
        db_info = self.get_service_info(service_name, process_info)
        base_info.update(db_info)
        
        return base_info
    
    def finalize(self):
        """Called at the end of data collection to save any new services"""
        if self.new_services_added:
            self.save_services_database()
            logger.info("Services database updated with new unknown services")

class SystemCollector:
    def __init__(self, hostname: str, ssh_user: str, ssh_key_path: str):
        self.hostname = hostname
        self.ssh_user = ssh_user
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self.ssh_client = None
        self.services_db = ServiceDatabase()
        
    def connect(self) -> bool:
        """Establish SSH connection"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(
                self.hostname,
                username=self.ssh_user,
                key_filename=self.ssh_key_path,
                timeout=CONFIG['ssh_timeout']
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to {self.hostname}: {e}")
            return False
    
    def run_command(self, command: str) -> Optional[str]:
        """Execute command over SSH"""
        if not self.ssh_client:
            return None
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command)
            return stdout.read().decode('utf-8').strip()
        except Exception as e:
            logger.warning(f"Command failed on {self.hostname}: {command} - {e}")
            return None
    
    def collect_system_info(self) -> Dict:
        """Collect comprehensive system information"""
        info = {
            'hostname': self.hostname,  # This is the IP initially
            'timestamp': datetime.now().isoformat(),
            'reachable': False
        }
    
        if not self.connect():
            return info
        
        info['reachable'] = True
    
        # Get the actual hostname/FQDN from the system
        actual_hostname = self.run_command('hostname -f 2>/dev/null || hostname')
        if actual_hostname and actual_hostname != "Unknown":
            info['actual_hostname'] = actual_hostname.strip()
            logger.debug(f"Discovered hostname: {actual_hostname} for IP: {self.hostname}")
    
        commands = {
            'os_release_raw': 'cat /etc/os-release 2>/dev/null || echo "Unknown"',
            'kernel': 'uname -r',
            'architecture': 'uname -m',
            'uptime': 'uptime -p',
            'load_average': 'uptime | awk -F"load average:" \'{print $2}\'',
            'memory_total': 'free -h | grep Mem | awk \'{print $2}\'',
            'memory_used': 'free -h | grep Mem | awk \'{print $3}\'',
            'disk_usage': 'df -h / | tail -1 | awk \'{print $3 "/" $2 " (" $5 ")"}\'',
            'cpu_info': 'lscpu | grep "Model name" | cut -d: -f2 | xargs',
            'cpu_cores': 'nproc',
            'ip_addresses': 'ip -4 addr show | grep inet | awk \'{print $2}\' | grep -v 127.0.0.1',
        }
        for key, command in commands.items():
            result = self.run_command(command)
            info[key] = result if result else "Unknown"
        
        info['os_release'] = self.parse_os_release(info.get('os_release_raw', ''))
        if info['os_release_raw'] != "Unknown":
            del info['os_release_raw']
        
        info['services'] = self.get_services()
        info['docker_containers'] = self.get_docker_containers()
        info['listening_ports'] = self.get_listening_ports()
        info['kubernetes_info'] = self.get_kubernetes_info()
        info['proxmox_info'] = self.get_proxmox_info()
        
        self.ssh_client.close()
        return info
    
    def parse_os_release(self, os_release_content: str) -> Dict:
        """Parse /etc/os-release content into structured data"""
        os_info = {}
        
        if not os_release_content or os_release_content == "Unknown":
            return {"name": "Unknown", "version": "Unknown", "id": "unknown"}
        
        for line in os_release_content.split('\n'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                value = value.strip('"\'')
                os_info[key.lower()] = value
        
        parsed = {
            'name': os_info.get('name', os_info.get('pretty_name', 'Unknown')),
            'version': os_info.get('version', os_info.get('version_id', 'Unknown')),
            'version_id': os_info.get('version_id', 'Unknown'),
            'version_codename': os_info.get('version_codename', os_info.get('ubuntu_codename', None)),
            'id': os_info.get('id', 'unknown'),
            'id_like': os_info.get('id_like', None),
            'pretty_name': os_info.get('pretty_name', os_info.get('name', 'Unknown')),
            'home_url': os_info.get('home_url', None),
            'support_url': os_info.get('support_url', None),
            'bug_report_url': os_info.get('bug_report_url', None)
        }
        
        return {k: v for k, v in parsed.items() if v is not None}
    
    def get_services(self) -> List[Dict]:
        """Get systemd services with enhanced information"""
        services = []
        result = self.run_command(
            "systemctl list-units --type=service --state=active --no-pager --plain | "
            "awk '{print $1}' | grep -v '^●' | head -20"
        )
        if result:
            for service in result.split('\n'):
                if service.strip():
                    enhanced_service = self.services_db.enhance_service(
                        service.strip(), 
                        'active'
                    )
                    services.append(enhanced_service)
        return services
    
    def get_docker_containers(self) -> List[Dict]:
        """Get Docker containers if Docker is installed"""
        containers = []
        result = self.run_command('docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" 2>/dev/null')
        if result and 'NAMES' in result:
            lines = result.split('\n')[1:]
            for line in lines:
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        containers.append({
                            'name': parts[0],
                            'image': parts[1],
                            'status': parts[2]
                        })
        return containers
    
    def get_listening_ports(self) -> List[Dict]:
        """Get listening network ports with enhanced service information"""
        ports = []
        result = self.run_command('ss -tlnp | grep LISTEN')
        if result:
            for line in result.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 4:
                        process_info = parts[-1] if len(parts) > 4 else 'unknown'
                        
                        process_name = 'unknown'
                        if 'users:' in process_info:
                            try:
                                import re
                                match = re.search(r'\("([^"]+)"', process_info)
                                if match:
                                    process_name = match.group(1)
                            except:
                                pass
                        
                        service_info = self.services_db.get_service_info(process_name, process_info)
                        
                        port_info = {
                            'port': parts[3],
                            'process': process_info,
                            'process_name': process_name,
                            'service_info': service_info
                        }
                        ports.append(port_info)
        return ports[:20]
    
    def get_kubernetes_info(self) -> Dict:
        """Get Kubernetes information if kubectl is available"""
        k8s_info = {}
        
        kubectl_version = self.run_command('kubectl version --client -o json 2>/dev/null | grep gitVersion || kubectl version --client 2>/dev/null | head -1')
        if not kubectl_version:
            return k8s_info
            
        k8s_info['kubectl_version'] = kubectl_version.strip()
        
        cluster_info = self.run_command('kubectl cluster-info 2>/dev/null | head -3')
        if cluster_info:
            k8s_info['cluster_info'] = cluster_info
        
        nodes = self.run_command('kubectl get nodes --no-headers -o wide 2>/dev/null')
        if nodes:
            k8s_info['nodes'] = []
            for line in nodes.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        k8s_info['nodes'].append({
                            'name': parts[0],
                            'status': parts[1],
                            'roles': parts[2] if len(parts) > 2 else 'Unknown',
                            'version': parts[4] if len(parts) > 4 else 'Unknown'
                        })
        
        namespaces = self.run_command('kubectl get namespaces --no-headers 2>/dev/null')
        if namespaces:
            k8s_info['namespaces'] = [line.split()[0] for line in namespaces.split('\n') if line.strip()]
        
        pods = self.run_command('kubectl get pods --all-namespaces --no-headers -o wide 2>/dev/null')
        if pods:
            k8s_info['pods'] = []
            problematic_pods = []
            
            for line in pods.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 5:
                        pod_info = {
                            'namespace': parts[0],
                            'name': parts[1],
                            'ready': parts[2],
                            'status': parts[3],
                            'restarts': parts[4],
                            'age': parts[5] if len(parts) > 5 else 'Unknown'
                        }
                        k8s_info['pods'].append(pod_info)
                        
                        try:
                            restarts = int(parts[4])
                        except (ValueError, IndexError):
                            restarts = 0
                            
                        if (parts[3] not in ['Running', 'Completed'] or 
                            '/' in parts[2] and parts[2].split('/')[0] != parts[2].split('/')[1] or
                            restarts > 5):
                            problematic_pods.append(pod_info)
            
            if problematic_pods:
                k8s_info['problematic_pods'] = problematic_pods
        
        services = self.run_command('kubectl get services --all-namespaces --no-headers 2>/dev/null')
        if services:
            k8s_info['services'] = []
            for line in services.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 4:
                        k8s_info['services'].append({
                            'namespace': parts[0],
                            'name': parts[1],
                            'type': parts[2],
                            'cluster_ip': parts[3],
                            'external_ip': parts[4] if len(parts) > 4 and parts[4] != '<none>' else None,
                            'ports': parts[5] if len(parts) > 5 else 'Unknown'
                        })
        
        deployments = self.run_command('kubectl get deployments --all-namespaces --no-headers 2>/dev/null')
        if deployments:
            k8s_info['deployments'] = []
            for line in deployments.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 4:
                        k8s_info['deployments'].append({
                            'namespace': parts[0],
                            'name': parts[1],
                            'ready': parts[2],
                            'up_to_date': parts[3],
                            'available': parts[4] if len(parts) > 4 else 'Unknown',
                            'age': parts[5] if len(parts) > 5 else 'Unknown'
                        })
        
        return k8s_info
    
    def get_proxmox_info(self) -> Dict:
        """Get Proxmox information if available"""
        proxmox_info = {}
        
        pve_version = self.run_command('pveversion 2>/dev/null')
        if pve_version:
            proxmox_info['version'] = pve_version
            
            vms = self.run_command('qm list 2>/dev/null')
            if vms:
                proxmox_info['vms'] = vms.split('\n')[1:]
            
            containers = self.run_command('pct list 2>/dev/null')
            if containers:
                proxmox_info['containers'] = containers.split('\n')[1:]
        
        return proxmox_info

class NetworkScanner:
    def __init__(self, network_range: str):
        self.network_range = network_range
    
    def scan_network(self) -> List[str]:
        """Scan network for live hosts"""
        logger.info(f"Scanning network range: {self.network_range}")
        live_hosts = []
        
        try:
            network = ipaddress.IPv4Network(self.network_range, strict=False)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['max_workers']) as executor:
                futures = {executor.submit(self.ping_host, str(ip)): str(ip) for ip in network.hosts()}
                
                for future in concurrent.futures.as_completed(futures):
                    ip = futures[future]
                    if future.result():
                        live_hosts.append(ip)
        
        except Exception as e:
            logger.error(f"Network scanning failed: {e}")
        
        logger.info(f"Found {len(live_hosts)} live hosts")
        return live_hosts
    
    def ping_host(self, ip: str) -> bool:
        """Ping a single host"""
        try:
            result = subprocess.run(['ping', '-c', '1', '-W', '1', ip], 
                                  capture_output=True, timeout=2)
            return result.returncode == 0
        except:
            return False

class InventoryManager:
    def __init__(self):
        self.inventory = {}
    
    def load_csv_hosts(self, csv_file: str) -> List[str]:
        """Load hosts from CSV file"""
        hosts = []
        if os.path.exists(csv_file):
            try:
                with open(csv_file, 'r') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if 'hostname' in row or 'ip' in row:
                            host = row.get('hostname', row.get('ip'))
                            if host:
                                hosts.append(host.strip())
            except Exception as e:
                logger.error(f"Failed to read CSV file {csv_file}: {e}")
        return hosts
    
    def collect_all_data(self, hosts: List[str]):
        """Collect data from all hosts"""
        logger.info(f"Collecting data from {len(hosts)} hosts")
    
        with concurrent.futures.ThreadPoolExecutor(max_workers=CONFIG['max_workers']) as executor:
            futures = {
                executor.submit(self.collect_host_data, host): host 
                for host in hosts
            }
        
            for future in concurrent.futures.as_completed(futures):
                original_host = futures[future]
                try:
                    data = future.result()
                
                    # Determine the best key to use for this host
                    if data.get('reachable') and data.get('actual_hostname'):
                        # Use the actual hostname from the system
                        best_key = data['actual_hostname']
                        logger.info(f"Using hostname '{best_key}' instead of IP '{original_host}'")
                    else:
                        # Fall back to the original (IP or hostname from CSV)
                        best_key = original_host
                
                    self.inventory[best_key] = data
                    logger.info(f"Collected data for {best_key}")
                
                except Exception as e:
                    logger.error(f"Failed to collect data for {original_host}: {e}")
    
    def collect_host_data(self, host: str) -> Dict:
        """Collect data from a single host"""
        collector = SystemCollector(host, CONFIG['ssh_user'], CONFIG['ssh_key_path'])
        data = collector.collect_system_info()
        
        collector.services_db.finalize()
        
        return data
    
    def save_inventory(self, filename: str):
        """Save inventory to JSON file"""
        try:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, 'w') as f:
                json.dump(self.inventory, f, indent=2)
            logger.info(f"Inventory saved to {filename}")
        except Exception as e:
            logger.error(f"Failed to save inventory: {e}")

class MediaWikiUpdater:
    def __init__(self, api_url: str, username: str, password: str):
        self.api_url = api_url
        self.username = username
        self.password = password
        self.session = requests.Session()
    
    def login(self) -> bool:
        """Login to MediaWiki"""
        try:
            login_token = self.session.get(self.api_url, params={
                'action': 'query',
                'meta': 'tokens',
                'type': 'login',
                'format': 'json'
            }).json()['query']['tokens']['logintoken']
            
            response = self.session.post(self.api_url, data={
                'action': 'login',
                'lgname': self.username,
                'lgpassword': self.password,
                'lgtoken': login_token,
                'format': 'json'
            })
            
            return response.json().get('login', {}).get('result') == 'Success'
        except Exception as e:
            logger.error(f"MediaWiki login failed: {e}")
            return False
    
    def update_page(self, title: str, content: str):
        """Update a MediaWiki page"""
        if not self.login():
            return False
            
        try:
            edit_token = self.session.get(self.api_url, params={
                'action': 'query',
                'meta': 'tokens',
                'format': 'json'
            }).json()['query']['tokens']['csrftoken']
            
            response = self.session.post(self.api_url, data={
                'action': 'edit',
                'title': title,
                'text': content,
                'token': edit_token,
                'format': 'json'
            })
            
            return 'error' not in response.json()
        except Exception as e:
            logger.error(f"Failed to update page {title}: {e}")
            return False

def generate_wiki_content(host_data: Dict) -> str:
    """Generate MediaWiki/Markdown content for a host"""
    os_info = host_data.get('os_release', {})
    
    content = f"""# {host_data['hostname']}

**Last Updated:** {host_data['timestamp']}

## System Information
- **OS:** {os_info.get('pretty_name', os_info.get('name', 'Unknown'))}
- **Version:** {os_info.get('version', 'Unknown')}
- **Distribution:** {os_info.get('id', 'unknown').title()}
- **Kernel:** {host_data.get('kernel', 'Unknown')}
- **Architecture:** {host_data.get('architecture', 'Unknown')}
- **Uptime:** {host_data.get('uptime', 'Unknown')}
- **CPU:** {host_data.get('cpu_info', 'Unknown')} ({host_data.get('cpu_cores', 'Unknown')} cores)

## Resources
- **Memory:** {host_data.get('memory_used', 'Unknown')} / {host_data.get('memory_total', 'Unknown')}
- **Disk Usage:** {host_data.get('disk_usage', 'Unknown')}
- **Load Average:** {host_data.get('load_average', 'Unknown')}

## Network
- **IP Addresses:** {host_data.get('ip_addresses', 'Unknown')}

## Services
"""
    
    if host_data.get('services'):
        for service in host_data['services'][:10]:
            display_name = service.get('display_name', service['name'])
            description = service.get('description', '')
            category = service.get('category', '')
            
            service_line = f"- **{display_name}** ({service['status']})"
            if category and category != 'unknown':
                service_line += f" - *{category}*"
            elif service.get('_auto_generated'):
                service_line += f" - *🔍 auto-discovered*"
            
            if description and description != 'Unknown service':
                if service.get('_auto_generated'):
                    service_line += f" - ⚠️ Please update service information"
                else:
                    service_line += f" - {description}"
            content += service_line + "\n"
    
    if host_data.get('listening_ports'):
        content += "\n## Listening Ports\n"
        for port in host_data['listening_ports'][:10]:
            port_line = f"- **{port['port']}**"
            
            if port.get('service_info') and port['service_info'].get('display_name'):
                service_info = port['service_info']
                port_line += f" - **{service_info['display_name']}**"
                if service_info.get('description') and service_info['description'] != 'Unknown service':
                    port_line += f" - {service_info['description']}"
                if service_info.get('access'):
                    port_line += f" - *Access: {service_info['access']}*"
            else:
                process_name = port.get('process_name', 'unknown')
                port_line += f" - {process_name}"
            
            content += port_line + "\n"
    
    if host_data.get('kubernetes_info'):
        k8s = host_data['kubernetes_info']
        content += "\n## Kubernetes\n"
        
        if 'kubectl_version' in k8s:
            content += f"- **Version:** {k8s['kubectl_version']}\n"
        
        if 'nodes' in k8s:
            content += f"\n### Nodes ({len(k8s['nodes'])})\n"
            for node in k8s['nodes']:
                status_emoji = "✅" if node['status'] == 'Ready' else "❌"
                content += f"- {status_emoji} **{node['name']}** ({node['roles']}) - {node['status']}\n"
        
        if 'pods' in k8s:
            running_pods = len([p for p in k8s['pods'] if p['status'] == 'Running'])
            total_pods = len(k8s['pods'])
            content += f"\n### Pods Summary\n"
            content += f"- **Total Pods:** {total_pods}\n"
            content += f"- **Running:** {running_pods}\n"
    
    return content

def generate_mediawiki_content(host_data: Dict) -> str:
    """Generate MediaWiki-specific content for a host"""
    os_info = host_data.get('os_release', {})
    
    content = f"""= {host_data['hostname']} =

'''Last Updated:''' {host_data['timestamp']}

== System Information ==
* '''OS:''' {os_info.get('pretty_name', os_info.get('name', 'Unknown'))}
* '''Version:''' {os_info.get('version', 'Unknown')}
* '''Kernel:''' {host_data.get('kernel', 'Unknown')}
* '''Architecture:''' {host_data.get('architecture', 'Unknown')}
* '''Uptime:''' {host_data.get('uptime', 'Unknown')}
* '''CPU:''' {host_data.get('cpu_info', 'Unknown')} ({host_data.get('cpu_cores', 'Unknown')} cores)

== Resources ==
* '''Memory:''' {host_data.get('memory_used', 'Unknown')} / {host_data.get('memory_total', 'Unknown')}
* '''Disk Usage:''' {host_data.get('disk_usage', 'Unknown')}

== Services ==
"""
    
    if host_data.get('services'):
        for service in host_data['services'][:10]:
            display_name = service.get('display_name', service['name'])
            content += f"* '''{display_name}''' ({service['status']})\n"
    
    return content

class DocumentationManager:
    def __init__(self, docs_dir: str = 'documentation'):
        self.docs_dir = docs_dir
        self.ensure_docs_directory()
    
    def ensure_docs_directory(self):
        """Create documentation directory if it doesn't exist"""
        try:
            if not os.path.exists(self.docs_dir):
                os.makedirs(self.docs_dir)
                logger.info(f"Created documentation directory: {self.docs_dir}")
            else:
                logger.debug(f"Documentation directory already exists: {self.docs_dir}")
        except Exception as e:
            logger.error(f"Failed to create documentation directory {self.docs_dir}: {e}")
            raise
    
    def save_host_documentation(self, hostname: str, host_data: Dict):
        """Save documentation file for a single host"""
        if not host_data.get('reachable'):
            logger.warning(f"Skipping documentation for unreachable host: {hostname}")
            return False
        
        # Sanitize hostname for filename
        safe_hostname = self.sanitize_filename(hostname)
        doc_path = os.path.join(self.docs_dir, f"{safe_hostname}.md")
        json_path = os.path.join(self.docs_dir, f"{safe_hostname}.json")
        
        try:
            # Save Markdown documentation
            content = generate_wiki_content(host_data)
            with open(doc_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Documentation saved: {doc_path}")
            
            # Save individual JSON file
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump({hostname: host_data}, f, indent=2)
            logger.info(f"Individual JSON saved: {json_path}")
            
            return True
        except PermissionError as e:
            logger.error(f"Permission denied writing documentation for {hostname} to {doc_path}: {e}")
            return False
        except OSError as e:
            logger.error(f"OS error writing documentation for {hostname} to {doc_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error saving documentation for {hostname}: {e}")
            return False
    
    def save_all_documentation(self, inventory: Dict):
        """Save documentation files for all hosts in inventory"""
        logger.info(f"Saving documentation files to {self.docs_dir}/")
        
        if not inventory:
            logger.warning("No inventory data provided to save_all_documentation")
            return
        
        reachable_count = 0
        total_files_created = 0
        
        for hostname, host_data in inventory.items():
            try:
                if host_data.get('reachable'):
                    if self.save_host_documentation(hostname, host_data):
                        reachable_count += 1
                        total_files_created += 1
                else:
                    logger.debug(f"Skipping unreachable host: {hostname}")
            except Exception as e:
                logger.error(f"Error processing documentation for {hostname}: {e}")
        
        # Create index file
        try:
            if self.create_index_file(inventory):
                total_files_created += 1
                logger.info(f"Created index file successfully")
            else:
                logger.error("Failed to create index file")
        except Exception as e:
            logger.error(f"Exception creating index file: {e}")
        
        logger.info(f"Documentation summary: {total_files_created} files created for {reachable_count} reachable hosts out of {len(inventory)} total hosts")
    
    def create_index_file(self, inventory: Dict):
        """Create an index.md file listing all documented hosts"""
        index_path = os.path.join(self.docs_dir, 'index.md')
        
        try:
            content = f"""# Lab Documentation Index

Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Documented Servers

"""
            
            reachable_hosts = [(hostname, data) for hostname, data in inventory.items() if data.get('reachable')]
            unreachable_hosts = [(hostname, data) for hostname, data in inventory.items() if not data.get('reachable')]
            
            if reachable_hosts:
                content += "### Active Servers\n\n"
                for hostname, data in sorted(reachable_hosts):
                    safe_hostname = self.sanitize_filename(hostname)
                    os_info = data.get('os_release', {})
                    os_display = os_info.get('pretty_name', os_info.get('name', 'Unknown OS'))
                    uptime = data.get('uptime', 'Unknown uptime')
                    
                    # Add additional info if available
                    extra_info = []
                    if data.get('kubernetes_info'):
                        k8s_pods = len(data['kubernetes_info'].get('pods', []))
                        if k8s_pods > 0:
                            extra_info.append(f"K8s: {k8s_pods} pods")
                    if data.get('docker_containers'):
                        docker_count = len(data['docker_containers'])
                        extra_info.append(f"Docker: {docker_count} containers")
                    if data.get('proxmox_info'):
                        extra_info.append("Proxmox")
                    
                    extra_text = f" | {' | '.join(extra_info)}" if extra_info else ""
                    content += f"- **[{hostname}]({safe_hostname}.md)** - {os_display} - {uptime}{extra_text}\n"
            
            if unreachable_hosts:
                content += "\n### Unreachable Servers\n\n"
                for hostname, data in sorted(unreachable_hosts):
                    last_seen = data.get('timestamp', 'Never')
                    content += f"- **{hostname}** - Last attempt: {last_seen}\n"
            
            content += f"\n---\n\n**Total Servers:** {len(inventory)} ({len(reachable_hosts)} reachable, {len(unreachable_hosts)} unreachable)\n"
            
            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Index file created: {index_path}")
            return True
            
        except PermissionError as e:
            logger.error(f"Permission denied creating index file at {index_path}: {e}")
            return False
        except OSError as e:
            logger.error(f"OS error creating index file at {index_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error creating index file: {e}")
            return False
    
    def sanitize_filename(self, hostname: str) -> str:
        """Sanitize hostname for use as filename"""
        try:
            import re
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', hostname)
            safe_name = safe_name.replace(' ', '_')
            logger.debug(f"Sanitized hostname '{hostname}' to '{safe_name}'")
            return safe_name
        except Exception as e:
            logger.error(f"Error sanitizing filename for hostname '{hostname}': {e}")
            return f"server_{hash(hostname) % 10000}"

def main():
    parser = argparse.ArgumentParser(
        description='Lab Documenter - Discovers and documents servers, VMs, containers, and services in your home lab',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s --scan                              Scan network and collect data from all discovered hosts
  %(prog)s --csv-only                          Only scan servers listed in CSV file  
  %(prog)s --scan --update-wiki                Scan network and update MediaWiki pages
  %(prog)s --config custom.json --scan        Use custom configuration file
  %(prog)s --csv servers.csv --scan           Use custom CSV file for server list
  %(prog)s --output inventory.json --scan     Save results to custom output file
  %(prog)s --dry-run --scan                   Show what would be done without making changes
  %(prog)s --verbose --scan                   Enable detailed logging output
  %(prog)s --network 10.0.0.0/8 --scan       Scan custom network range
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
    mode_group.add_argument('--dry-run', action='store_true', 
                           help='Show what would be done without making changes')
    
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
                              help='Network range to scan in CIDR format (e.g., 192.168.1.0/24)')
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
    
    # Set logging level based on verbosity
    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Load configuration file
    config = CONFIG.copy()
    if os.path.exists(args.config):
        try:
            with open(args.config, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
            logger.info(f"Loaded configuration from {args.config}")
        except Exception as e:
            logger.error(f"Failed to load config file {args.config}: {e}")
            sys.exit(1)
    else:
        logger.warning(f"Configuration file {args.config} not found, using defaults")
    
    # Override config with command line arguments
    if args.csv:
        config['csv_file'] = args.csv
    if args.output:
        config['output_file'] = args.output
    if args.network:
        config['network_range'] = args.network
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
    
    # Update global CONFIG
    CONFIG.update(config)
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
        logger.info(f"Would use config: {json.dumps(config, indent=2)}")
    
    # Validate required settings
    if not config.get('ssh_user'):
        logger.error("SSH user not configured. Set it in config file or use --ssh-user")
        sys.exit(1)
    
    if (args.scan or args.csv_only) and not os.path.exists(os.path.expanduser(config['ssh_key_path'])):
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
            logger.info(f"Would scan network: {config['network_range']}")
        else:
            scanner = NetworkScanner(config['network_range'])
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
    
    if not hosts:
        logger.error("No hosts found to scan")
        logger.info("Try: --scan to scan network, or add hosts to CSV file")
        sys.exit(1)
    
    logger.info(f"Will process {len(hosts)} hosts: {', '.join(hosts[:5])}" + 
                (f" and {len(hosts)-5} more" if len(hosts) > 5 else ""))
    
    if args.dry_run:
        logger.info("DRY RUN: Would collect data from hosts, save inventory, and create documentation files")
        return
    
    # Collect data
    inventory_manager.collect_all_data(hosts)
    
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

if __name__ == '__main__':
    main()

