"""
System information collection for Lab Documenter

Handles SSH connections and system information gathering.
"""

import paramiko
import logging
import os
import json
from datetime import datetime
from typing import Dict, List, Optional
from modules.services import ServiceDatabase

logger = logging.getLogger(__name__)

class SystemCollector:
    def __init__(self, hostname: str, ssh_user: str, ssh_key_path: str, ssh_timeout: int = 5):
        self.hostname = hostname
        self.ssh_user = ssh_user
        self.ssh_key_path = os.path.expanduser(ssh_key_path)
        self.ssh_client = None
        self.services_db = ServiceDatabase()
        self.ssh_timeout = ssh_timeout
        self.connection_failure_reason = None
        
    def connect(self) -> bool:
        """Establish SSH connection"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(
                self.hostname,
                username=self.ssh_user,
                key_filename=self.ssh_key_path,
                timeout=self.ssh_timeout
            )
            return True
        except Exception as e:
            # Categorize the failure reason
            error_str = str(e).lower()
            if 'timeout' in error_str or 'timed out' in error_str:
                self.connection_failure_reason = f"Connection timeout (waited {self.ssh_timeout}s)"
            elif 'connection refused' in error_str:
                self.connection_failure_reason = "Connection refused (SSH service may not be running)"
            elif 'no route to host' in error_str:
                self.connection_failure_reason = "No route to host (network unreachable)"
            elif 'host unreachable' in error_str:
                self.connection_failure_reason = "Host unreachable"
            elif 'authentication failed' in error_str:
                self.connection_failure_reason = "Authentication failed (check SSH key/credentials)"
            elif 'no authentication methods available' in error_str:
                self.connection_failure_reason = "No authentication methods available"
            elif 'q must be exactly' in error_str:
                self.connection_failure_reason = "SSH key compatibility issue (DSA key format)"
            elif 'permission denied' in error_str:
                self.connection_failure_reason = "Permission denied (check SSH key permissions)"
            elif 'name resolution' in error_str or 'nodename nor servname' in error_str:
                self.connection_failure_reason = "DNS resolution failed"
            elif 'network is unreachable' in error_str:
                self.connection_failure_reason = "Network unreachable"
            elif 'unable to connect to port 22' in error_str:
                self.connection_failure_reason = "Unable to connect to SSH port 22"
            elif 'connection reset' in error_str or 'connection aborted' in error_str:
                self.connection_failure_reason = "Connection reset by remote host"
            elif 'errno none' in error_str:
                self.connection_failure_reason = "Connection failed (port may be filtered or closed)"
            else:
                # For truly unknown errors, normalize by removing IP addresses and specific details
                import re
                normalized_error = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', 'X.X.X.X', str(e))
                normalized_error = re.sub(r'port \d+', 'port XX', normalized_error)
                if len(normalized_error) > 80:
                    normalized_error = normalized_error[:80] + "..."
                self.connection_failure_reason = f"Unknown error: {normalized_error}"
            
            logger.warning(f"Failed to connect to {self.hostname}: {self.connection_failure_reason}")
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
            info['connection_failure_reason'] = self.connection_failure_reason
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
        
        # Collect detailed memory module information
        memory_data = self.get_memory_modules()
        info['memory_modules'] = memory_data
        
        # Extract BIOS info for system information section
        if memory_data.get('bios_info'):
            info['bios_info'] = memory_data['bios_info']
        
        info['services'] = self.get_services()
        info['docker_containers'] = self.get_docker_containers()
        info['listening_ports'] = self.get_listening_ports()
        info['kubernetes_info'] = self.get_kubernetes_info()
        info['proxmox_info'] = self.get_proxmox_info()
        
        self.ssh_client.close()
        return info
    
    def parse_lshw_memory_output(self, lshw_output: str) -> dict:
        """Parse lshw memory output into structured data"""
        memory_data = {
            'bios_info': {},
            'memory_banks': [],
            'cache_info': [],
            'system_memory': {}
        }
        
        if not lshw_output or len(lshw_output.strip()) < 10:
            return memory_data
            
        # Split into sections based on *-entries
        sections = []
        current_section = []
        
        for line in lshw_output.split('\n'):
            if line.strip().startswith('*-'):
                if current_section:
                    sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        
        if current_section:
            sections.append('\n'.join(current_section))
        
        # Process each section
        for section in sections:
            section_header = section.split('\n')[0].strip() if section.strip() else ''
            
            if '*-firmware' in section_header:
                # Parse BIOS information
                bios_info = {}
                for line in section.split('\n')[1:]:  # Skip the header line
                    line = line.strip()
                    if ':' in line and not line.startswith('*-'):
                        key, value = line.split(':', 1)
                        bios_info[key.strip()] = value.strip()
                
                if bios_info:  # Only add if we found some BIOS info
                    memory_data['bios_info'] = bios_info
                    logger.debug(f"Found BIOS info on {self.hostname}: {bios_info}")
                
            elif '*-memory' in section_header and '*-bank:' not in section_header:
                # Parse system memory info
                sys_mem = {}
                for line in section.split('\n')[1:]:  # Skip the header line
                    line = line.strip()
                    if ':' in line and not line.startswith('*-'):
                        key, value = line.split(':', 1)
                        sys_mem[key.strip()] = value.strip()
                
                if sys_mem:
                    memory_data['system_memory'] = sys_mem
                
            elif '*-bank:' in section_header:
                # Parse memory bank information
                bank_info = {}
                for line in section.split('\n')[1:]:  # Skip the header line
                    line = line.strip()
                    if ':' in line and not line.startswith('*-'):
                        key, value = line.split(':', 1)
                        bank_info[key.strip()] = value.strip()
                        
                # Only include banks that have useful information
                if bank_info.get('size') or '[empty]' not in bank_info.get('description', ''):
                    memory_data['memory_banks'].append(bank_info)
                elif '[empty]' in bank_info.get('description', ''):
                    # Mark as empty but keep slot info
                    bank_info['empty'] = True
                    memory_data['memory_banks'].append(bank_info)
                    
            elif '*-cache:' in section_header:
                # Parse cache information
                cache_info = {}
                for line in section.split('\n')[1:]:  # Skip the header line
                    line = line.strip()
                    if ':' in line and not line.startswith('*-'):
                        key, value = line.split(':', 1)
                        cache_info[key.strip()] = value.strip()
                
                if cache_info:
                    memory_data['cache_info'].append(cache_info)
        
        logger.debug(f"Parsed memory data for {self.hostname}: BIOS={bool(memory_data.get('bios_info'))}, Banks={len(memory_data.get('memory_banks', []))}")
        return memory_data

    def get_memory_modules(self) -> dict:
        """Get detailed memory module information as structured data"""
        # Try multiple methods to get memory information
        
        # Method 1: lshw (preferred for detailed info)
        logger.debug(f"Trying lshw command on {self.hostname}")
        memory_info = self.run_command('lshw -c memory 2>/dev/null')
        if memory_info and 'command not found' not in memory_info.lower() and len(memory_info.strip()) > 10:
            logger.debug(f"lshw successful on {self.hostname}")
            return self.parse_lshw_memory_output(memory_info)
        else:
            logger.debug(f"lshw failed on {self.hostname}: {memory_info}")
        
        # Method 2: dmidecode fallback (return as structured data too)
        logger.debug(f"Trying dmidecode command on {self.hostname}")
        memory_info = self.run_command('dmidecode -t memory 2>/dev/null | grep -A 15 "Memory Device" | head -80')
        if memory_info and 'command not found' not in memory_info.lower() and len(memory_info.strip()) > 10:
            logger.debug(f"dmidecode successful on {self.hostname}")
            return {
                'bios_info': {},
                'memory_banks': [],
                'cache_info': [],
                'system_memory': {},
                'raw_dmidecode': memory_info,
                'source': 'dmidecode'
            }
        else:
            logger.debug(f"dmidecode failed on {self.hostname}: {memory_info}")
        
        # Method 3: Try with sudo (in case user has sudo without password)
        logger.debug(f"Trying sudo lshw command on {self.hostname}")
        memory_info = self.run_command('sudo lshw -c memory 2>/dev/null')
        if memory_info and 'command not found' not in memory_info.lower() and 'sudo:' not in memory_info and len(memory_info.strip()) > 10:
            logger.debug(f"sudo lshw successful on {self.hostname}")
            return self.parse_lshw_memory_output(memory_info)
        else:
            logger.debug(f"sudo lshw failed on {self.hostname}: {memory_info}")
            
        # Method 4: /proc/meminfo (basic fallback)
        logger.debug(f"Trying /proc/meminfo on {self.hostname}")
        memory_info = self.run_command('cat /proc/meminfo | head -15')
        if memory_info and len(memory_info.strip()) > 10:
            logger.debug(f"/proc/meminfo successful on {self.hostname}")
            return {
                'bios_info': {},
                'memory_banks': [],
                'cache_info': [],
                'system_memory': {},
                'basic_meminfo': memory_info,
                'source': 'meminfo'
            }
        else:
            logger.debug(f"/proc/meminfo failed on {self.hostname}: {memory_info}")
            
        # Method 5: Return error information
        available_commands = []
        for cmd in ['lshw', 'dmidecode', 'cat']:
            check_result = self.run_command(f'which {cmd} 2>/dev/null')
            if check_result:
                available_commands.append(f"{cmd}: {check_result}")
        
        error_info = {
            'bios_info': {},
            'memory_banks': [],
            'cache_info': [],
            'system_memory': {},
            'error': 'Memory module information not available',
            'available_commands': available_commands,
            'source': 'error'
        }
        
        # Try to get OS info to help debug
        os_info = self.run_command('cat /etc/os-release | head -3 2>/dev/null')
        if os_info:
            error_info['os_info'] = os_info
        
        logger.warning(f"Could not get memory info for {self.hostname}")
        return error_info
    
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
            "awk '{print $1}' | grep -v '^UNIT' | head -20"
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
        """Get comprehensive Proxmox information if available"""
        proxmox_info = {}
        
        # Check if Proxmox is installed
        pve_version = self.run_command('pveversion 2>/dev/null')
        if not pve_version:
            return proxmox_info
        
        proxmox_info['pve_version'] = pve_version.strip()
        
        # Try JSON API first for more reliable parsing
        cluster_json = self.run_command('pvesh get /cluster/status --output-format=json 2>/dev/null')
        if cluster_json:
            try:
                cluster_data = json.loads(cluster_json)
                proxmox_info['cluster_status'] = self.parse_cluster_json(cluster_data)
                proxmox_info['nodes'] = self.parse_nodes_json(cluster_data)
                
                # JSON doesn't include transport info, so get it from text output
                cluster_status = self.run_command('pvecm status 2>/dev/null')
                if cluster_status and 'Transport:' in cluster_status:
                    for line in cluster_status.split('\n'):
                        if 'Transport:' in line:
                            proxmox_info['cluster_status']['transport'] = line.split(':', 1)[1].strip()
                            break
                            
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse JSON cluster status on {self.hostname}: {e}")
                # Fall back to text parsing
                cluster_status = self.run_command('pvecm status 2>/dev/null')
                if cluster_status and 'Cluster information' in cluster_status:
                    proxmox_info['cluster_status'] = self.parse_cluster_status(cluster_status)
                else:
                    proxmox_info['cluster_status'] = {'clustered': False, 'nodes': 1}
                
                # Get node information for non-JSON fallback
                if proxmox_info['cluster_status']['clustered']:
                    nodes_output = self.run_command('pvecm nodes 2>/dev/null')
                    if nodes_output:
                        proxmox_info['nodes'] = self.parse_cluster_nodes(nodes_output)
                else:
                    # Single node setup
                    hostname = self.run_command('hostname')
                    proxmox_info['nodes'] = [{'name': hostname or 'localhost', 'status': 'online', 'online': True}]
        else:
            # Fallback to text parsing if JSON not available
            cluster_status = self.run_command('pvecm status 2>/dev/null')
            if cluster_status and 'Cluster information' in cluster_status:
                proxmox_info['cluster_status'] = self.parse_cluster_status(cluster_status)
            else:
                proxmox_info['cluster_status'] = {'clustered': False, 'nodes': 1}
            
            # Get node information
            if proxmox_info['cluster_status']['clustered']:
                nodes_output = self.run_command('pvecm nodes 2>/dev/null')
                if nodes_output:
                    proxmox_info['nodes'] = self.parse_cluster_nodes(nodes_output)
            else:
                # Single node setup
                hostname = self.run_command('hostname')
                node_status = self.run_command('pvesh get /nodes/$(hostname)/status --output-format=json 2>/dev/null')
                if node_status:
                    try:
                        node_data = json.loads(node_status)
                        proxmox_info['nodes'] = [{
                            'name': hostname or 'localhost',
                            'status': 'online',
                            'online': True,
                            'cpu_usage': f"{node_data.get('cpu', 0)*100:.1f}%",
                            'memory_usage': f"{node_data.get('memory', {}).get('used', 0) / (1024*1024*1024):.1f}G / {node_data.get('memory', {}).get('total', 0) / (1024*1024*1024):.1f}G",
                            'uptime': node_data.get('uptime', 'Unknown')
                        }]
                    except:
                        proxmox_info['nodes'] = [{'name': hostname or 'localhost', 'status': 'online', 'online': True}]
        
        # Get cluster resource usage summaries
        cluster_resources = self.run_command('pvesh get /cluster/resources --output-format=json 2>/dev/null')
        if cluster_resources:
            try:
                resources_data = json.loads(cluster_resources)
                proxmox_info['cluster_resources'] = self.parse_cluster_resources(resources_data)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse cluster resources on {self.hostname}: {e}")
        
        # Get VM information with detailed status
        vms_output = self.run_command('qm list 2>/dev/null')
        if vms_output:
            proxmox_info['vms'] = self.parse_vm_list(vms_output)
        
        # Get container information with detailed status  
        containers_output = self.run_command('pct list 2>/dev/null')
        if containers_output:
            proxmox_info['containers'] = self.parse_container_list(containers_output)
        
        # Get storage information
        storage_output = self.run_command('pvesm status 2>/dev/null')
        if storage_output:
            proxmox_info['storage'] = self.parse_storage_status(storage_output)
        
        # Identify problematic VMs/containers
        problematic_resources = []
        if proxmox_info.get('vms'):
            for vm in proxmox_info['vms']:
                # Only consider truly problematic states, not stopped VMs
                if (vm.get('status') not in ['running', 'stopped'] or vm.get('lock')):
                    # Don't include stopped VMs unless they have other issues
                    if vm.get('status') != 'stopped' or vm.get('lock'):
                        problematic_resources.append(vm)
        
        if proxmox_info.get('containers'):
            for ct in proxmox_info['containers']:
                # Only consider truly problematic states, not stopped containers
                if (ct.get('status') not in ['running', 'stopped'] or ct.get('lock')):
                    # Don't include stopped containers unless they have other issues
                    if ct.get('status') != 'stopped' or ct.get('lock'):
                        problematic_resources.append(ct)
        
        if problematic_resources:
            proxmox_info['problematic_resources'] = problematic_resources
        
        return proxmox_info

    def bytes_to_gb(self, bytes_str: str) -> str:
        """Convert bytes string to GB with appropriate formatting"""
        try:
            bytes_value = int(bytes_str)
            gb_value = bytes_value / (1024 ** 3)
            
            if gb_value >= 1000:
                return f"{gb_value/1024:.1f}TB"
            elif gb_value >= 1:
                return f"{gb_value:.1f}GB"
            else:
                return f"{gb_value*1024:.0f}MB"
        except (ValueError, TypeError):
            return bytes_str  # Return original if conversion fails

    def parse_cluster_json(self, cluster_data: list) -> Dict:
        """Parse JSON cluster status from pvesh API"""
        cluster_info = {'clustered': True}
        
        for item in cluster_data:
            if item.get('type') == 'cluster':
                cluster_info['name'] = item.get('name', 'Unknown')
                cluster_info['node_count'] = item.get('nodes', 0)
                cluster_info['config_version'] = item.get('version', 'Unknown')
                cluster_info['quorate'] = bool(item.get('quorate', 0))
                break
        
        return cluster_info

    def parse_nodes_json(self, cluster_data: list) -> List[Dict]:
        """Parse JSON node information from pvesh API"""
        nodes = []
        
        for item in cluster_data:
            if item.get('type') == 'node':
                node = {
                    'name': item.get('name', 'Unknown'),
                    'id': item.get('nodeid', 'Unknown'),
                    'status': 'online' if item.get('online') else 'offline',
                    'online': bool(item.get('online', 0)),
                    'ip': item.get('ip', 'Unknown'),
                    'local': bool(item.get('local', 0)),
                    'type': item.get('type', 'node'),
                    'level': item.get('level', '')
                }
                
                # Get detailed node status if online
                if node['online']:
                    detailed_status = self.get_node_detailed_status(node['name'])
                    if detailed_status:
                        node.update(detailed_status)
                
                nodes.append(node)
        
        return nodes

    def parse_cluster_status(self, cluster_status: str) -> Dict:
        """Parse pvecm status output"""
        cluster_info = {'clustered': True}
        
        lines = cluster_status.split('\n')
        for line in lines:
            line = line.strip()
            if 'Name:' in line:
                cluster_info['name'] = line.split(':', 1)[1].strip()
            elif 'Config Version:' in line:
                cluster_info['config_version'] = line.split(':', 1)[1].strip()
            elif 'Transport:' in line:
                cluster_info['transport'] = line.split(':', 1)[1].strip()
            elif 'Nodes:' in line:
                try:
                    cluster_info['node_count'] = int(line.split(':')[1].strip())
                except:
                    pass
            elif 'Quorate:' in line:
                cluster_info['quorate'] = 'Yes' in line
        
        return cluster_info

    def parse_cluster_nodes(self, nodes_output: str) -> List[Dict]:
        """Parse pvecm nodes output"""
        nodes = []
        lines = nodes_output.split('\n')
        
        # Find the membership information section
        in_membership = False
        for line in lines:
            line = line.strip()
            
            if 'Membership information' in line:
                in_membership = True
                continue
            
            if in_membership and line and not line.startswith('Nodeid') and not line.startswith('---'):
                parts = line.split()
                if len(parts) >= 3:
                    # Format: Nodeid Votes Name
                    node = {
                        'name': parts[2],
                        'id': parts[0],
                        'votes': parts[1],
                        'status': 'online',  # Assume online if in membership list
                        'online': True
                    }
                    # Remove (local) from name if present
                    if '(local)' in node['name']:
                        node['name'] = node['name'].replace('(local)', '').strip()
                        node['local'] = True
                    else:
                        node['local'] = False
                    
                    nodes.append(node)
        
        return nodes

    def parse_vm_list(self, vms_output: str) -> List[Dict]:
        """Parse qm list output into structured data"""
        vms = []
        lines = vms_output.split('\n')
        
        # Get current node name for detailed queries
        current_node = self.run_command('hostname') or 'localhost'
        
        # Skip header line and limit detailed queries for performance (first 20 VMs)
        detail_count = 0
        max_detailed = 20
        
        for line in lines[1:]:
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    vm = {
                        'vmid': parts[0],
                        'name': parts[1],
                        'status': parts[2],
                        'type': 'vm'
                    }
                    
                    # Additional fields if available - clarify what they represent
                    if len(parts) > 3:
                        # This is memory allocation in MB
                        memory_mb = parts[3] if len(parts) > 3 else 'Unknown'
                        if memory_mb != 'Unknown' and memory_mb.isdigit():
                            vm['memory_allocated'] = f"{memory_mb}MB"
                        else:
                            vm['memory_allocated'] = memory_mb
                    
                    if len(parts) > 4:
                        # This appears to be disk size in GB, not boot disk name
                        disk_size = parts[4] if len(parts) > 4 else 'Unknown'
                        if disk_size != 'Unknown':
                            try:
                                disk_gb = float(disk_size)
                                vm['disk_size'] = f"{disk_gb:.1f}GB"
                            except ValueError:
                                vm['disk_size'] = disk_size
                        else:
                            vm['disk_size'] = 'Unknown'
                    
                    if len(parts) > 5:
                        vm['pid'] = parts[5] if len(parts) > 5 else 'Unknown'
                    
                    # Get detailed info for running VMs (limited for performance)
                    if vm['status'] in ['running', 'stopped'] and detail_count < max_detailed:
                        detailed_info = self.get_vm_detailed_info(current_node, vm['vmid'])
                        if detailed_info:
                            vm['detailed_info'] = detailed_info
                            detail_count += 1
                    
                    vms.append(vm)
        
        logger.debug(f"Got detailed info for {detail_count} VMs out of {len(vms)} total VMs")
        return vms

    def parse_container_list(self, containers_output: str) -> List[Dict]:
        """Parse pct list output into structured data"""
        containers = []
        lines = containers_output.split('\n')
        
        # Get current node name for detailed queries
        current_node = self.run_command('hostname') or 'localhost'
        
        # Skip header line and limit detailed queries for performance (first 20 containers)
        detail_count = 0
        max_detailed = 20
        
        for line in lines[1:]:
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    container = {
                        'vmid': parts[0],
                        'status': parts[1],
                        'lock': parts[2] if parts[2] != '-' else None,
                        'type': 'container'
                    }
                    
                    # Container name might be in position 3 or later, or we need to get it from config
                    if len(parts) > 3:
                        container['name'] = parts[3]
                    else:
                        # If name not in list output, we'll get it from detailed info
                        container['name'] = f"Container-{parts[0]}"  # Fallback name
                    
                    # Get detailed info for containers (limited for performance)
                    if container['status'] in ['running', 'stopped'] and detail_count < max_detailed:
                        detailed_info = self.get_container_detailed_info(current_node, container['vmid'])
                        if detailed_info:
                            container['detailed_info'] = detailed_info
                            # Update name from config if we got a better one
                            if detailed_info.get('hostname') and detailed_info['hostname'] != 'Unknown':
                                container['name'] = detailed_info['hostname']
                            detail_count += 1
                    
                    containers.append(container)
        
        logger.debug(f"Got detailed info for {detail_count} containers out of {len(containers)} total containers")
        return containers

    def parse_storage_status(self, storage_output: str) -> List[Dict]:
        """Parse pvesm status output"""
        storage = []
        lines = storage_output.split('\n')
        
        # Skip header
        for line in lines[1:]:
            if line.strip():
                parts = line.split()
                if len(parts) >= 6:
                    storage_info = {
                        'name': parts[0],
                        'type': parts[1],
                        'status': parts[2],
                        'total': self.bytes_to_gb(parts[3]),
                        'used': self.bytes_to_gb(parts[4]),
                        'available': self.bytes_to_gb(parts[5]),
                        'usage_percent': parts[6] if len(parts) > 6 else 'Unknown'
                    }
                    storage.append(storage_info)
        
        return storage

    def convert_uptime_seconds(self, uptime_seconds) -> str:
        """Convert uptime in seconds to human readable format"""
        try:
            seconds = int(float(str(uptime_seconds)))
            
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            minutes = (seconds % 3600) // 60
            remaining_seconds = seconds % 60
            
            parts = []
            if days > 0:
                parts.append(f"{days} day{'s' if days != 1 else ''}")
            if hours > 0:
                parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0:
                parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            if remaining_seconds > 0 or not parts:  # Show seconds if no other parts or if only seconds
                parts.append(f"{remaining_seconds} second{'s' if remaining_seconds != 1 else ''}")
            
            # Return first 2-3 most significant parts
            return ", ".join(parts[:3])
            
        except (ValueError, TypeError):
            return str(uptime_seconds)  # Return original if conversion fails

    def get_node_detailed_status(self, node_name: str) -> Dict:
        """Get detailed status for a specific node"""
        try:
            node_status = self.run_command(f'pvesh get /nodes/{node_name}/status --output-format=json 2>/dev/null')
            if node_status:
                status_data = json.loads(node_status)
                
                # Handle CPU percentage - API might return decimal or percentage
                cpu_raw = status_data.get('cpu', 0)
                if cpu_raw < 1.0:  # Likely decimal format (0.15 = 15%)
                    cpu_percentage = f"{cpu_raw * 100:.1f}%"
                else:  # Already percentage
                    cpu_percentage = f"{cpu_raw:.1f}%"
                
                # Convert uptime from seconds to readable format
                uptime_seconds = status_data.get('uptime', 0)
                uptime_readable = self.convert_uptime_seconds(uptime_seconds)
                
                return {
                    'cpu_usage': cpu_percentage,
                    'memory_total': self.bytes_to_gb(str(status_data.get('memory', {}).get('total', 0))),
                    'memory_used': self.bytes_to_gb(str(status_data.get('memory', {}).get('used', 0))),
                    'memory_usage_percent': f"{(status_data.get('memory', {}).get('used', 0) / max(status_data.get('memory', {}).get('total', 1), 1))*100:.1f}%",
                    'uptime': uptime_readable,
                    'uptime_seconds': uptime_seconds,  # Keep raw for other uses
                    'load_average': status_data.get('loadavg', 'Unknown'),
                    'kernel_version': status_data.get('kversion', 'Unknown'),
                    'pve_version': status_data.get('pveversion', 'Unknown')
                }
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to get detailed status for node {node_name}: {e}")
        return {}

    def parse_cluster_resources(self, resources_data: list) -> Dict:
        """Parse cluster resources summary"""
        summary = {
            'total_nodes': 0,
            'online_nodes': 0,
            'total_vms': 0,
            'running_vms': 0,
            'total_containers': 0,
            'running_containers': 0,
            'storage_pools': 0,
            'cpu_usage': {'total': 0, 'used': 0},
            'memory_usage': {'total': 0, 'used': 0}
        }
        
        for resource in resources_data:
            res_type = resource.get('type', '')
            
            if res_type == 'node':
                summary['total_nodes'] += 1
                if resource.get('status') == 'online':
                    summary['online_nodes'] += 1
                    # Aggregate CPU and memory across all nodes
                    if resource.get('maxcpu'):
                        summary['cpu_usage']['total'] += resource.get('maxcpu', 0)
                        summary['cpu_usage']['used'] += resource.get('cpu', 0) * resource.get('maxcpu', 0)
                    if resource.get('maxmem'):
                        summary['memory_usage']['total'] += resource.get('maxmem', 0)
                        summary['memory_usage']['used'] += resource.get('mem', 0)
                        
            elif res_type == 'qemu':
                summary['total_vms'] += 1
                if resource.get('status') == 'running':
                    summary['running_vms'] += 1
                    
            elif res_type == 'lxc':
                summary['total_containers'] += 1
                if resource.get('status') == 'running':
                    summary['running_containers'] += 1
                    
            elif res_type == 'storage':
                summary['storage_pools'] += 1
        
        # Calculate percentages
        if summary['cpu_usage']['total'] > 0:
            summary['cpu_usage']['percentage'] = f"{(summary['cpu_usage']['used'] / summary['cpu_usage']['total']) * 100:.1f}%"
        
        if summary['memory_usage']['total'] > 0:
            summary['memory_usage']['total_gb'] = self.bytes_to_gb(str(int(summary['memory_usage']['total'])))
            summary['memory_usage']['used_gb'] = self.bytes_to_gb(str(int(summary['memory_usage']['used'])))
            summary['memory_usage']['percentage'] = f"{(summary['memory_usage']['used'] / summary['memory_usage']['total']) * 100:.1f}%"
        
        return summary

    def get_vm_detailed_info(self, node_name: str, vmid: str) -> Dict:
        """Get detailed information for a specific VM"""
        detailed_info = {}
        
        # Limit detailed queries to avoid performance issues
        try:
            # Get VM configuration
            config = self.run_command(f'pvesh get /nodes/{node_name}/qemu/{vmid}/config --output-format=json 2>/dev/null')
            if config:
                config_data = json.loads(config)
                detailed_info.update({
                    'cores': config_data.get('cores', 'Unknown'),
                    'sockets': config_data.get('sockets', 'Unknown'), 
                    'memory_mb': config_data.get('memory', 'Unknown'),
                    'bootdisk': config_data.get('bootdisk', 'Unknown'),
                    'description': config_data.get('description', ''),
                    'tags': config_data.get('tags', ''),
                    'ha_priority': config_data.get('startup', '')
                })
                
                # Parse network interfaces
                networks = []
                for key, value in config_data.items():
                    if key.startswith('net'):
                        networks.append(f"{key}: {value}")
                detailed_info['networks'] = networks
            
            # Get VM current status
            status = self.run_command(f'pvesh get /nodes/{node_name}/qemu/{vmid}/status/current --output-format=json 2>/dev/null')
            if status:
                status_data = json.loads(status)
                
                # Handle CPU percentage properly
                cpu_raw = status_data.get('cpu', 0)
                if cpu_raw < 1.0:  # Likely decimal format (0.015 = 1.5%)
                    cpu_percentage = f"{cpu_raw * 100:.1f}%"
                else:  # Already percentage or very high usage
                    cpu_percentage = f"{cpu_raw:.1f}%"
                
                # Convert uptime from seconds to readable format
                uptime_seconds = status_data.get('uptime', 0)
                uptime_readable = self.convert_uptime_seconds(uptime_seconds)
                
                detailed_info.update({
                    'uptime': uptime_readable,
                    'uptime_seconds': uptime_seconds,
                    'cpu_usage': cpu_percentage,
                    'memory_current': self.bytes_to_gb(str(status_data.get('mem', 0))),
                    'pid': status_data.get('pid', 'Unknown'),
                    'ha_state': status_data.get('ha', {}),
                })
                
                # Get network I/O if available
                if 'netin' in status_data and 'netout' in status_data:
                    detailed_info['network_io'] = {
                        'bytes_in': self.bytes_to_gb(str(status_data.get('netin', 0))),
                        'bytes_out': self.bytes_to_gb(str(status_data.get('netout', 0)))
                    }
                    
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to get detailed VM info for {vmid} on {node_name}: {e}")
            
        return detailed_info

    def get_container_detailed_info(self, node_name: str, vmid: str) -> Dict:
        """Get detailed information for a specific container"""
        detailed_info = {}
        
        try:
            # Get container configuration
            config = self.run_command(f'pvesh get /nodes/{node_name}/lxc/{vmid}/config --output-format=json 2>/dev/null')
            if config:
                config_data = json.loads(config)
                detailed_info.update({
                    'cores': config_data.get('cores', 'Unknown'),
                    'memory_mb': config_data.get('memory', 'Unknown'),
                    'swap_mb': config_data.get('swap', 'Unknown'),
                    'description': config_data.get('description', ''),
                    'tags': config_data.get('tags', ''),
                    'rootfs': config_data.get('rootfs', 'Unknown')
                })
                
                # Parse network interfaces
                networks = []
                for key, value in config_data.items():
                    if key.startswith('net'):
                        networks.append(f"{key}: {value}")
                detailed_info['networks'] = networks
                
                # Parse mount points
                mounts = []
                for key, value in config_data.items():
                    if key.startswith('mp'):
                        mounts.append(f"{key}: {value}")
                detailed_info['mount_points'] = mounts
            
            # Get container current status
            status = self.run_command(f'pvesh get /nodes/{node_name}/lxc/{vmid}/status/current --output-format=json 2>/dev/null')
            if status:
                status_data = json.loads(status)
                
                # Handle CPU percentage properly
                cpu_raw = status_data.get('cpu', 0)
                if cpu_raw < 1.0:  # Likely decimal format (0.015 = 1.5%)
                    cpu_percentage = f"{cpu_raw * 100:.1f}%"
                else:  # Already percentage or very high usage
                    cpu_percentage = f"{cpu_raw:.1f}%"
                
                # Convert uptime from seconds to readable format
                uptime_seconds = status_data.get('uptime', 0)
                uptime_readable = self.convert_uptime_seconds(uptime_seconds)
                
                detailed_info.update({
                    'uptime': uptime_readable,
                    'uptime_seconds': uptime_seconds,
                    'cpu_usage': cpu_percentage,
                    'memory_current': self.bytes_to_gb(str(status_data.get('mem', 0))),
                    'swap_current': self.bytes_to_gb(str(status_data.get('swap', 0))),
                    'disk_usage': self.bytes_to_gb(str(status_data.get('disk', 0))),
                    'pid': status_data.get('pid', 'Unknown')
                })
                    
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to get detailed container info for {vmid} on {node_name}: {e}")
            
        return detailed_info

