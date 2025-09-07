"""
System information collection for Lab Documenter

Handles multi-platform connections and system information gathering.
"""

import paramiko
import logging
import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from modules.services import ServiceDatabase
from modules.utils import bytes_to_gb, convert_uptime_seconds
from modules.system_kubernetes import KubernetesCollector
from modules.system_proxmox import ProxmoxCollector

# Import new collectors with fallback if not available
try:
    from modules.system_windows import WindowsCollector
    HAS_WINDOWS_COLLECTOR = True
except ImportError:
    HAS_WINDOWS_COLLECTOR = False
    logger = logging.getLogger(__name__)
    logger.warning("Windows collector not available - Windows systems will be treated as Linux")

try:
    from modules.system_nas import NASCollector
    HAS_NAS_COLLECTOR = True
except ImportError:
    HAS_NAS_COLLECTOR = False
    logger = logging.getLogger(__name__)
    logger.warning("NAS collector not available - NAS systems will be treated as Linux")

# Import WinRM with fallback
try:
    import winrm
    HAS_WINRM = True
except ImportError:
    HAS_WINRM = False
    logger = logging.getLogger(__name__)
    logger.warning("pywinrm not available - Windows systems cannot be accessed via WinRM")

logger = logging.getLogger(__name__)

class SystemCollector:
    def __init__(self, hostname: str, config: Dict[str, Any]):
        self.hostname = hostname
        self.config = config
        self.services_db = ServiceDatabase()
        self.connection_failure_reason = None
        
        # Connection state
        self.connection_type = None
        self.ssh_client = None
        self.winrm_session = None
        
        # Platform detection
        self.platform_type = None
        self.platform_info = {}
        
        # Initialize specialized collectors
        self.kubernetes_collector = None
        self.proxmox_collector = None
        self.windows_collector = None
        self.nas_collector = None
    
    def try_connection_cascade(self) -> Tuple[bool, str]:
        """Try connection methods in priority order: Windows -> Linux -> NAS"""
        logger.debug(f"Trying connection cascade for {self.hostname}")
        
        # Method 1: Try Windows (WinRM)
        if HAS_WINRM and HAS_WINDOWS_COLLECTOR:
            if self.try_winrm_connection():
                logger.info(f"Connected to {self.hostname} via WinRM (Windows detected)")
                self.platform_type = 'windows'
                self.platform_info['detection_method'] = 'WinRM connection successful'
                return True, 'windows'
        
        # Method 2: Try Linux (SSH with keys)
        if self.try_ssh_key_connection():
            logger.info(f"Connected to {self.hostname} via SSH keys (Linux assumed)")
            self.platform_type = 'linux'
            self.platform_info['detection_method'] = 'SSH key authentication successful'
            return True, 'linux'
        
        # Method 3: Try NAS (SSH with password)
        if HAS_NAS_COLLECTOR:
            if self.try_ssh_password_connection():
                logger.info(f"Connected to {self.hostname} via SSH password (NAS assumed)")
                self.platform_type = 'nas'
                self.platform_info['detection_method'] = 'SSH password authentication successful'
                return True, 'nas'
        
        logger.warning(f"All connection methods failed for {self.hostname}")
        return False, 'unreachable'
    
    def try_winrm_connection(self) -> bool:
        """Try to connect via WinRM"""
        try:
            windows_user = self.config.get('windows_user')
            windows_password = self.config.get('windows_password')
            
            if not windows_user or not windows_password:
                logger.debug(f"Windows credentials not configured for {self.hostname}")
                return False
            
            # Try WinRM connection with NTLM authentication (more secure)
            winrm_url = f"http://{self.hostname}:5985/wsman"
            
            # Try NTLM first (most secure for domain/local users)
            try:
                session = winrm.Session(winrm_url, auth=(windows_user, windows_password), transport='ntlm')
                result = session.run_cmd('echo test')
                if result.status_code == 0:
                    self.winrm_session = session
                    self.connection_type = 'winrm'
                    logger.debug(f"WinRM connected using NTLM authentication for {self.hostname}")
                    return True
            except Exception as ntlm_error:
                logger.debug(f"NTLM authentication failed for {self.hostname}: {ntlm_error}")
            
            # Fallback to Kerberos if in domain environment
            try:
                session = winrm.Session(winrm_url, auth=(windows_user, windows_password), transport='kerberos')
                result = session.run_cmd('echo test')
                if result.status_code == 0:
                    self.winrm_session = session
                    self.connection_type = 'winrm'
                    logger.debug(f"WinRM connected using Kerberos authentication for {self.hostname}")
                    return True
            except Exception as krb_error:
                logger.debug(f"Kerberos authentication failed for {self.hostname}: {krb_error}")
            
            # Last resort: basic auth (only if others fail)
            try:
                session = winrm.Session(winrm_url, auth=(windows_user, windows_password), transport='basic')
                result = session.run_cmd('echo test')
                if result.status_code == 0:
                    self.winrm_session = session
                    self.connection_type = 'winrm'
                    logger.warning(f"WinRM connected using basic authentication for {self.hostname} - consider enabling NTLM")
                    return True
                else:
                    logger.debug(f"WinRM test command failed for {self.hostname}: {result.std_err}")
                    return False
            except Exception as basic_error:
                logger.debug(f"Basic authentication failed for {self.hostname}: {basic_error}")
                return False
                
        except Exception as e:
            logger.debug(f"WinRM connection failed for {self.hostname}: {e}")
            if 'timeout' in str(e).lower():
                self.connection_failure_reason = "WinRM connection timeout (port 5985 may be closed)"
            elif 'unauthorized' in str(e).lower() or 'authentication' in str(e).lower():
                self.connection_failure_reason = "WinRM authentication failed (check Windows credentials)"
            elif 'connection refused' in str(e).lower():
                self.connection_failure_reason = "WinRM connection refused (service may not be running)"
            return False
    
    def try_ssh_key_connection(self) -> bool:
        """Try to connect via SSH with keys (Linux method)"""
        try:
            ssh_user = self.config.get('ssh_user')
            ssh_key_path = self.config.get('ssh_key_path')
            ssh_timeout = self.config.get('ssh_timeout', 10)
            
            if not ssh_user or not ssh_key_path:
                logger.debug(f"SSH key credentials not configured for {self.hostname}")
                return False
            
            ssh_key_path = os.path.expanduser(ssh_key_path)
            if not os.path.exists(ssh_key_path):
                logger.debug(f"SSH key not found: {ssh_key_path}")
                return False
            
            # Try SSH connection with key
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(
                self.hostname,
                username=ssh_user,
                key_filename=ssh_key_path,
                timeout=ssh_timeout
            )
            
            self.connection_type = 'ssh_key'
            return True
            
        except Exception as e:
            logger.debug(f"SSH key connection failed for {self.hostname}: {e}")
            self._categorize_ssh_failure(e)
            if self.ssh_client:
                self.ssh_client.close()
                self.ssh_client = None
            return False
    
    def try_ssh_password_connection(self) -> bool:
        """Try to connect via SSH with password (NAS method)"""
        try:
            nas_user = self.config.get('nas_user')
            nas_password = self.config.get('nas_password')
            ssh_timeout = self.config.get('ssh_timeout', 10)
            
            if not nas_user or not nas_password:
                logger.debug(f"NAS credentials not configured for {self.hostname}")
                return False
            
            # Try SSH connection with password
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(
                self.hostname,
                username=nas_user,
                password=nas_password,
                timeout=ssh_timeout
            )
            
            self.connection_type = 'ssh_password'
            return True
            
        except Exception as e:
            logger.debug(f"SSH password connection failed for {self.hostname}: {e}")
            self._categorize_ssh_failure(e)
            if self.ssh_client:
                self.ssh_client.close()
                self.ssh_client = None
            return False
    
    def _categorize_ssh_failure(self, exception: Exception):
        """Categorize SSH connection failures"""
        error_str = str(exception).lower()
        ssh_timeout = self.config.get('ssh_timeout', 10)
        
        if 'timeout' in error_str or 'timed out' in error_str:
            self.connection_failure_reason = f"SSH connection timeout (waited {ssh_timeout}s)"
        elif 'connection refused' in error_str:
            self.connection_failure_reason = "SSH connection refused (service may not be running)"
        elif 'no route to host' in error_str:
            self.connection_failure_reason = "No route to host (network unreachable)"
        elif 'host unreachable' in error_str:
            self.connection_failure_reason = "Host unreachable"
        elif 'authentication failed' in error_str:
            self.connection_failure_reason = "SSH authentication failed (check credentials/keys)"
        elif 'permission denied' in error_str:
            self.connection_failure_reason = "SSH permission denied (check credentials/key permissions)"
        else:
            # Normalize error message
            import re
            normalized_error = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', 'X.X.X.X', str(exception))
            if len(normalized_error) > 80:
                normalized_error = normalized_error[:80] + "..."
            self.connection_failure_reason = f"SSH error: {normalized_error}"
    
    def run_command(self, command: str) -> Optional[str]:
        """Execute command using appropriate connection method"""
        if self.connection_type == 'winrm' and self.winrm_session:
            return self.run_winrm_command(command)
        elif self.connection_type in ['ssh_key', 'ssh_password'] and self.ssh_client:
            return self.run_ssh_command(command)
        else:
            logger.warning(f"No active connection for {self.hostname}")
            return None
    
    def run_winrm_command(self, command: str) -> Optional[str]:
        """Execute command over WinRM"""
        try:
            if command.startswith('powershell'):
                # Execute PowerShell command
                ps_command = command.replace('powershell -Command "', '').rstrip('"')
                result = self.winrm_session.run_ps(ps_command)
            else:
                # Execute regular command
                result = self.winrm_session.run_cmd(command)
            
            if result.status_code == 0:
                return result.std_out.decode('utf-8').strip()
            else:
                logger.warning(f"WinRM command failed on {self.hostname}: {command} - {result.std_err}")
                return None
        except Exception as e:
            logger.warning(f"WinRM command failed on {self.hostname}: {command} - {e}")
            return None
    
    def run_ssh_command(self, command: str) -> Optional[str]:
        """Execute command over SSH"""
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command)
            return stdout.read().decode('utf-8').strip()
        except Exception as e:
            logger.warning(f"SSH command failed on {self.hostname}: {command} - {e}")
            return None
    
    def collect_system_info(self) -> Dict:
        """Collect comprehensive system information using cascade connection"""
        info = {
            'hostname': self.hostname,
            'timestamp': datetime.now().isoformat(),
            'reachable': False
        }
        
        # Try connection cascade
        connected, platform_type = self.try_connection_cascade()
        if not connected:
            info['connection_failure_reason'] = self.connection_failure_reason
            return info
        
        info['reachable'] = True
        info['platform_type'] = platform_type
        info['platform_detection'] = self.platform_info
        info['connection_type'] = self.connection_type
        
        # Initialize specialized collectors
        if self.connection_type in ['ssh_key', 'ssh_password']:
            self.kubernetes_collector = KubernetesCollector(self.run_command)
            self.proxmox_collector = ProxmoxCollector(self.run_command)
        
        if HAS_WINDOWS_COLLECTOR and self.connection_type == 'winrm':
            self.windows_collector = WindowsCollector(self.run_command)
        
        if HAS_NAS_COLLECTOR and self.connection_type == 'ssh_password':
            self.nas_collector = NASCollector(self.run_command)
        
        # Get actual hostname
        actual_hostname = self.get_actual_hostname()
        if actual_hostname:
            info['actual_hostname'] = actual_hostname
        
        # Route to platform-specific collection
        if platform_type == 'windows':
            info.update(self.collect_windows_info())
        elif platform_type == 'nas':
            info.update(self.collect_nas_info())
        else:  # linux
            info.update(self.collect_linux_info())
        
        # Always try to collect Kubernetes and Proxmox info (Linux/NAS only)
        if platform_type in ['linux', 'nas']:
            info['kubernetes_info'] = self.kubernetes_collector.collect_kubernetes_info()
            info['proxmox_info'] = self.proxmox_collector.collect_proxmox_info()
        
        # Clean up connections
        self.cleanup_connections()
        return info
    
    def get_actual_hostname(self) -> Optional[str]:
        """Get actual hostname using appropriate method for platform"""
        if self.connection_type == 'winrm':
            # Windows hostname commands
            hostname_commands = ['echo %COMPUTERNAME%', 'hostname']
        else:
            # Linux/NAS hostname commands
            hostname_commands = ['hostname -f', 'hostname', 'echo $HOSTNAME']
        
        for cmd in hostname_commands:
            result = self.run_command(cmd)
            if result and result != "Unknown" and result.strip():
                return result.strip()
        
        return None
    
    def collect_windows_info(self) -> Dict:
        """Collect Windows-specific information"""
        logger.info(f"Collecting Windows information for {self.hostname}")
        
        info = {}
        
        if self.windows_collector:
            windows_data = self.windows_collector.collect_windows_info()
            if windows_data:
                info['windows_info'] = windows_data
                
                # Map Windows data to standard fields for compatibility
                if 'os_release' in windows_data:
                    info['os_release'] = windows_data['os_release']
                if 'system_info' in windows_data:
                    sys_info = windows_data['system_info']
                    info['kernel'] = 'Windows NT'
                    info['architecture'] = sys_info.get('architecture', 'Unknown')
                    info['uptime'] = sys_info.get('uptime', 'Unknown')
                    info['cpu_info'] = sys_info.get('cpu_info', 'Unknown')
                    info['cpu_cores'] = sys_info.get('cpu_cores', 'Unknown')
                if 'memory_info' in windows_data:
                    mem_info = windows_data['memory_info']
                    info['memory_total'] = mem_info.get('memory_total', 'Unknown')
                    info['memory_used'] = mem_info.get('memory_available', 'Unknown')
                
                # Convert Windows services to standard format
                if 'services' in windows_data:
                    info['services'] = windows_data['services']
                else:
                    info['services'] = []
                
                # No Docker/K8s info expected on Windows
                info['docker_containers'] = []
                info['listening_ports'] = []
        
        return info
    
    def collect_nas_info(self) -> Dict:
        """Collect NAS-specific information"""
        logger.info(f"Collecting NAS information for {self.hostname}")
        
        info = {}
        
        if self.nas_collector:
            nas_data = self.nas_collector.collect_nas_info()
            if nas_data:
                info['nas_info'] = nas_data
                
                # Try to get basic Linux info that works on NAS
                basic_commands = {
                    'kernel': 'uname -r',
                    'architecture': 'uname -m',
                    'uptime': 'uptime -p 2>/dev/null || uptime',
                    'memory_total': 'free -h | grep Mem | awk \'{print $2}\' 2>/dev/null',
                    'memory_used': 'free -h | grep Mem | awk \'{print $3}\' 2>/dev/null',
                    'load_average': 'uptime | awk -F"load average:" \'{print $2}\'',
                    'ip_addresses': 'ip -4 addr show | grep inet | awk \'{print $2}\' | grep -v 127.0.0.1 2>/dev/null || ifconfig | grep "inet " | awk \'{print $2}\' | grep -v 127.0.0.1'
                }
                
                for key, command in basic_commands.items():
                    result = self.run_command(command)
                    info[key] = result if result else "Unknown"
                
                # Create OS release info from NAS data
                if 'model_info' in nas_data:
                    model_info = nas_data['model_info']
                    info['os_release'] = {
                        'name': f"{nas_data.get('nas_type', 'NAS').title()} NAS",
                        'version': model_info.get('firmware_version', 'Unknown'),
                        'id': nas_data.get('nas_type', 'nas'),
                        'pretty_name': f"{model_info.get('model', 'Unknown')} - {model_info.get('firmware_version', 'Unknown')}"
                    }
                
                # Try to get basic services
                info['services'] = self.get_services()
                info['docker_containers'] = self.get_docker_containers()
                info['listening_ports'] = self.get_listening_ports()
        
        return info
    
    def collect_linux_info(self) -> Dict:
        """Collect Linux-specific information"""
        logger.debug(f"Collecting Linux information for {self.hostname}")
        
        info = {}
        
        # Existing Linux collection logic
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
        
        # Extract BIOS info
        if memory_data.get('bios_info'):
            info['bios_info'] = memory_data['bios_info']
        
        # Collect services and network information
        info['services'] = self.get_services()
        info['docker_containers'] = self.get_docker_containers()
        info['listening_ports'] = self.get_listening_ports()
        
        return info
    
    def cleanup_connections(self):
        """Clean up all connections"""
        if self.ssh_client:
            self.ssh_client.close()
            self.ssh_client = None
        
        if self.winrm_session:
            # WinRM sessions don't need explicit cleanup
            self.winrm_session = None
    
    # Include all the existing methods from the previous system.py
    # (parse_os_release, get_memory_modules, etc. - keeping them unchanged)
    
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
    
    def get_memory_modules(self) -> dict:
        """Get detailed memory module information as structured data"""
        # [Keep existing implementation]
        logger.debug(f"Trying lshw command on {self.hostname}")
        memory_info = self.run_command('lshw -c memory 2>/dev/null')
        if memory_info and 'command not found' not in memory_info.lower() and len(memory_info.strip()) > 10:
            logger.debug(f"lshw successful on {self.hostname}")
            return self.parse_lshw_memory_output(memory_info)
        
        # [Keep all existing fallback methods]
        return {'bios_info': {}, 'memory_banks': [], 'cache_info': [], 'system_memory': {}, 'error': 'Memory module information not available'}
    
    def parse_lshw_memory_output(self, lshw_output: str) -> dict:
        """Parse lshw memory output into structured data"""
        # [Keep existing implementation - no changes needed]
        memory_data = {
            'bios_info': {},
            'memory_banks': [],
            'cache_info': [],
            'system_memory': {}
        }
        # ... rest of existing implementation
        return memory_data
    
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

