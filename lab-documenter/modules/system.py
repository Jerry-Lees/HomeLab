"""
System information collection for Lab Documenter

Handles SSH connections and system information gathering.
"""

import paramiko
import logging
import os
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

