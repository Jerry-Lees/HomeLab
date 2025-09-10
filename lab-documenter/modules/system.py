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
        
        # Detection mode flag
        self.in_detection_mode = True
        
        # Initialize specialized collectors
        self.kubernetes_collector = None
        self.proxmox_collector = None
        self.windows_collector = None
        self.nas_collector = None
    
    def try_connection_cascade(self) -> Tuple[bool, str]:
        """Try connection methods in priority order: Windows → NAS → Linux"""
        logger.debug(f"Trying connection cascade for {self.hostname}")
        
        # Set detection mode to suppress warnings during fingerprinting
        self.in_detection_mode = True
        
        # Method 1: Try Windows (WinRM)
        if HAS_WINRM and HAS_WINDOWS_COLLECTOR:
            if self.try_winrm_connection():
                logger.info(f"Connected to {self.hostname} via WinRM (Windows detected)")
                self.platform_type = 'windows'
                self.platform_info['detection_method'] = 'WinRM connection successful'
                self.in_detection_mode = False
                return True, 'windows'
        
        # Method 2: Try NAS (SSH with password) - moved before generic Linux
        if HAS_NAS_COLLECTOR:
            if self.try_ssh_password_connection():
                logger.info(f"Connected to {self.hostname} via SSH password (NAS assumed)")
                self.platform_type = 'nas'
                self.platform_info['detection_method'] = 'SSH password authentication successful'
                self.in_detection_mode = False
                return True, 'nas'
        
        # Method 3: Try Linux (SSH with keys) - now the fallback
        if self.try_ssh_key_connection():
            logger.info(f"Connected to {self.hostname} via SSH keys (Linux assumed)")
            self.platform_type = 'linux'
            self.platform_info['detection_method'] = 'SSH key authentication successful'
            self.in_detection_mode = False
            return True, 'linux'
        
        self.in_detection_mode = False
        logger.warning(f"All connection methods failed for {self.hostname}")
        return False, 'unreachable'
    
    def refine_platform_detection(self) -> Optional[str]:
        """After successful connection, refine platform detection (especially for TrueNAS)"""
        if self.platform_type != 'linux':
            return self.platform_type
        
        # Check if this Linux system is actually a NAS
        if HAS_NAS_COLLECTOR and self.connection_type in ['ssh_key', 'ssh_password']:
            # Create a temporary NAS collector to test detection
            temp_nas_collector = NASCollector(self.run_command) # type: ignore
            nas_type = temp_nas_collector.detect_nas_type()
            
            if nas_type:
                logger.info(f"Refined detection: {self.hostname} is actually a {nas_type} NAS system")
                self.platform_type = 'nas'
                self.platform_info['detection_method'] += f' (refined to {nas_type} NAS)'
                self.platform_info['nas_type'] = nas_type
                # Initialize the real NAS collector
                self.nas_collector = temp_nas_collector
                return 'nas'
        
        return self.platform_type
    
    def try_winrm_connection(self) -> bool:
        """Try to connect via WinRM"""
        try:
            windows_user = self.config.get('windows_user')
            windows_password = self.config.get('windows_password')
            
            if not windows_user or not windows_password:
                if not self.in_detection_mode:
                    logger.debug(f"Windows credentials not configured for {self.hostname}")
                return False
            
            # Try WinRM connection with NTLM authentication (more secure)
            winrm_url = f"http://{self.hostname}:5985/wsman"
            
            # Try NTLM first (most secure for domain/local users)
            try:
                session = winrm.Session(winrm_url, auth=(windows_user, windows_password), transport='ntlm') # type: ignore
                result = session.run_cmd('echo test')
                if result.status_code == 0:
                    self.winrm_session = session
                    self.connection_type = 'winrm'
                    if not self.in_detection_mode:
                        logger.debug(f"WinRM connected using NTLM authentication for {self.hostname}")
                    return True
            except Exception as ntlm_error:
                if not self.in_detection_mode:
                    logger.debug(f"NTLM authentication failed for {self.hostname}: {ntlm_error}")
            
            # Fallback to Kerberos if in domain environment
            try:
                session = winrm.Session(winrm_url, auth=(windows_user, windows_password), transport='kerberos') # type: ignore
                result = session.run_cmd('echo test')
                if result.status_code == 0:
                    self.winrm_session = session
                    self.connection_type = 'winrm'
                    if not self.in_detection_mode:
                        logger.debug(f"WinRM connected using Kerberos authentication for {self.hostname}")
                    return True
            except Exception as krb_error:
                if not self.in_detection_mode:
                    logger.debug(f"Kerberos authentication failed for {self.hostname}: {krb_error}")
            
            # Last resort: basic auth (only if others fail)
            try:
                session = winrm.Session(winrm_url, auth=(windows_user, windows_password), transport='basic') # type: ignore
                result = session.run_cmd('echo test')
                if result.status_code == 0:
                    self.winrm_session = session
                    self.connection_type = 'winrm'
                    if not self.in_detection_mode:
                        logger.warning(f"WinRM connected using basic authentication for {self.hostname} - consider enabling NTLM")
                    return True
                else:
                    if not self.in_detection_mode:
                        logger.debug(f"WinRM test command failed for {self.hostname}: {result.std_err}")
                    return False
            except Exception as basic_error:
                if not self.in_detection_mode:
                    logger.debug(f"Basic authentication failed for {self.hostname}: {basic_error}")
                return False
                
        except Exception as e:
            if not self.in_detection_mode:
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
                if not self.in_detection_mode:
                    logger.debug(f"SSH key credentials not configured for {self.hostname}")
                return False
            
            ssh_key_path = os.path.expanduser(ssh_key_path)
            if not os.path.exists(ssh_key_path):
                if not self.in_detection_mode:
                    logger.debug(f"SSH key not found: {ssh_key_path}")
                return False
            
            # Suppress paramiko logging during detection
            paramiko_logger = None
            original_level = None
            if self.in_detection_mode:
                paramiko_logger = logging.getLogger('paramiko.transport')
                original_level = paramiko_logger.level
                paramiko_logger.setLevel(logging.WARNING)
            
            try:
                # Try SSH connection with key
                self.ssh_client = paramiko.SSHClient()
                self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh_client.connect(
                    self.hostname,
                    username=ssh_user,
                    key_filename=ssh_key_path,
                    timeout=ssh_timeout,
                    look_for_keys=False,  # Only use the specific key we provided
                    allow_agent=False     # Don't try ssh-agent keys
                )
                
                self.connection_type = 'ssh_key'
                return True
            finally:
                # Restore paramiko logging level
                if paramiko_logger and original_level is not None:
                    paramiko_logger.setLevel(original_level)
            
        except Exception as e:
            if not self.in_detection_mode:
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
                if not self.in_detection_mode:
                    logger.debug(f"NAS credentials not configured for {self.hostname}")
                return False
            
            # Suppress paramiko logging during detection to avoid multiple key attempt messages
            paramiko_logger = None
            original_level = None
            if self.in_detection_mode:
                paramiko_logger = logging.getLogger('paramiko.transport')
                original_level = paramiko_logger.level
                paramiko_logger.setLevel(logging.WARNING)
            
            try:
                # Try SSH connection with password
                self.ssh_client = paramiko.SSHClient()
                self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self.ssh_client.connect(
                    self.hostname,
                    username=nas_user,
                    password=nas_password,
                    timeout=ssh_timeout,
                    look_for_keys=False,  # Don't try keys when doing password auth
                    allow_agent=False     # Don't try ssh-agent keys
                )
                
                self.connection_type = 'ssh_password'
                return True
            finally:
                # Restore paramiko logging level
                if paramiko_logger and original_level is not None:
                    paramiko_logger.setLevel(original_level)
            
        except Exception as e:
            # Always categorize the failure for connection summary
            self._categorize_ssh_failure(e)
            # Only log during normal operation, not detection
            if not self.in_detection_mode:
                logger.debug(f"SSH password connection failed for {self.hostname}: {e}")
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
            # Normalize error message but don't truncate
            import re
            normalized_error = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', 'X.X.X.X', str(exception))
            self.connection_failure_reason = f"SSH error: {normalized_error}"
    
    def run_command(self, command: str) -> Optional[str]:
        """Execute command using appropriate connection method"""
        if self.connection_type == 'winrm' and self.winrm_session:
            return self.run_winrm_command(command)
        elif self.connection_type in ['ssh_key', 'ssh_password'] and self.ssh_client:
            return self.run_ssh_command(command)
        else:
            if not self.in_detection_mode:
                logger.warning(f"No active connection for {self.hostname}")
            return None
    
    def run_winrm_command(self, command: str) -> Optional[str]:
        """Execute command over WinRM"""
        try:
            if command.startswith('powershell'):
                # Execute PowerShell command
                ps_command = command.replace('powershell -Command "', '').rstrip('"')
                result = self.winrm_session.run_ps(ps_command) # type: ignore
            else:
                # Execute regular command
                result = self.winrm_session.run_cmd(command) # type: ignore
            
            if result.status_code == 0:
                return result.std_out.decode('utf-8').strip()
            else:
                if not self.in_detection_mode:
                    logger.warning(f"WinRM command failed on {self.hostname}: {command} - {result.std_err}")
                return None
        except Exception as e:
            if not self.in_detection_mode:
                logger.warning(f"WinRM command failed on {self.hostname}: {command} - {e}")
            return None
    
    def run_winrm_command_silent(self, command: str) -> Optional[str]:
        """Execute WinRM command without warnings for expected failures"""
        try:
            if command.startswith('powershell'):
                # Execute PowerShell command
                ps_command = command.replace('powershell -Command "', '').rstrip('"')
                result = self.winrm_session.run_ps(ps_command) # type: ignore
            else:
                # Execute regular command
                result = self.winrm_session.run_cmd(command) # type: ignore
            
            if result.status_code == 0:
                return result.std_out.decode('utf-8').strip()
            else:
                # Return the error output for analysis, but don't log warnings
                return result.std_err.decode('utf-8').strip() if result.std_err else None
        except Exception as e:
            # Silent failure for expected command failures
            return None
    
    def run_ssh_command(self, command: str) -> Optional[str]:
        """Execute command over SSH"""
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command) # type: ignore
            return stdout.read().decode('utf-8').strip()
        except Exception as e:
            if not self.in_detection_mode:
                logger.warning(f"SSH command failed on {self.hostname}: {command} - {e}")
                return None
        
    def collect_system_info(self) -> Dict:
        """Collect comprehensive system information using cascade connection"""
        from modules.utils import set_device_context, clear_device_context
        
        # Set device context for this thread
        set_device_context(self.hostname)
        
        try:
            # Log the start of collection for this device
            logger.info(f"{'='*60}")
            logger.info(f"STARTING DATA COLLECTION: {self.hostname}")
            logger.info(f"{'='*60}")
            
            info = {
                'hostname': self.hostname,
                'timestamp': datetime.now().isoformat(),
                'reachable': False
            }
            
            # Try connection cascade
            connected, platform_type = self.try_connection_cascade()
            if not connected:
                info['connection_failure_reason'] = self.connection_failure_reason
                logger.warning(f"FAILED TO CONNECT: {self.hostname} - {self.connection_failure_reason}")
                logger.info(f"{'='*60}")
                logger.info(f"FINISHED DATA COLLECTION: {self.hostname} (FAILED)")
                logger.info(f"{'='*60}")
                return info
            
            # Refine platform detection (important for TrueNAS)
            final_platform_type = self.refine_platform_detection()
            
            info['reachable'] = True
            info['platform_type'] = final_platform_type
            info['platform_detection'] = self.platform_info
            info['connection_type'] = self.connection_type
            
            # Initialize specialized collectors based on final platform type
            if self.connection_type in ['ssh_key', 'ssh_password']:
                self.kubernetes_collector = KubernetesCollector(self.run_command)
                self.proxmox_collector = ProxmoxCollector(self.run_command)
            
            if HAS_WINDOWS_COLLECTOR and self.connection_type == 'winrm':
                # Create Windows collector with silent command runner for feature detection
                self.windows_collector = WindowsCollector(self.run_winrm_command_silent) # type: ignore
            
            # NAS collector might already be initialized in refine_platform_detection
            if HAS_NAS_COLLECTOR and final_platform_type == 'nas' and not self.nas_collector:
                self.nas_collector = NASCollector(self.run_command) # type: ignore
            
            # Get actual hostname
            actual_hostname = self.get_actual_hostname()
            if actual_hostname:
                info['actual_hostname'] = actual_hostname
            
            # Route to platform-specific collection based on FINAL platform type
            if final_platform_type == 'windows':
                info.update(self.collect_windows_info())
            elif final_platform_type == 'nas':
                info.update(self.collect_nas_info())
            else:  # linux
                info.update(self.collect_linux_info())
            
            # Always try to collect Kubernetes and Proxmox info (Linux/NAS only)
            if final_platform_type in ['linux', 'nas']:
                logger.debug(f"Checking for Kubernetes")
                info['kubernetes_info'] = self.kubernetes_collector.collect_kubernetes_info() # type: ignore
                logger.debug(f"Checking for Proxmox")
                info['proxmox_info'] = self.proxmox_collector.collect_proxmox_info() # type: ignore
            
            # Clean up connections
            self.cleanup_connections()
            
            # Log the completion of collection for this device
            logger.info(f"{'='*60}")
            logger.info(f"FINISHED DATA COLLECTION: {self.hostname} (SUCCESS)")
            logger.info(f"Platform: {final_platform_type}, Connection: {self.connection_type}")
            logger.info(f"{'='*60}")
            
            return info
            
        finally:
            # Always clear device context when done
            clear_device_context()

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
        logger.info(f"Collecting Windows information")
        
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
        logger.info(f"Collecting NAS information")
        
        info = {}
        
        if self.nas_collector:
            nas_data = self.nas_collector.collect_nas_info()
            if nas_data:
                info['nas_info'] = nas_data
                
                # Get basic system info using FreeBSD/Linux compatible commands
                basic_commands = {
                    'kernel': 'uname -r',
                    'architecture': 'uname -m',
                    'uptime': 'uptime -p 2>/dev/null || uptime',
                    'memory_total': 'free -h 2>/dev/null | grep Mem | awk \'{print $2}\' || sysctl -n hw.physmem | awk \'{print $1/1024/1024/1024 "GB"}\'',
                    'memory_used': 'free -h 2>/dev/null | grep Mem | awk \'{print $3}\' || echo "Unknown"',
                    'load_average': 'uptime | awk -F"load average:" \'{print $2}\' 2>/dev/null || uptime | grep "load averages" | awk \'{print $10 ", " $11 ", " $12}\'',
                    'ip_addresses': 'ip -4 addr show 2>/dev/null | grep inet | awk \'{print $2}\' | grep -v 127.0.0.1 || ifconfig | grep "inet " | awk \'{print $2}\' | grep -v 127.0.0.1',
                    'cpu_cores': 'nproc 2>/dev/null || sysctl -n hw.ncpu'
                }
                
                for key, command in basic_commands.items():
                    result = self.run_command(command)
                    info[key] = result if result else "Unknown"
                
                # Create OS release info from NAS data
                if 'model_info' in nas_data:
                    model_info = nas_data['model_info']
                    nas_type = nas_data.get('nas_type', 'NAS').title()
                    
                    # Special handling for TrueNAS
                    if nas_data.get('nas_type') in ['truenas', 'freebsd_nas']:
                        info['os_release'] = {
                            'name': 'TrueNAS',
                            'version': model_info.get('firmware_version', info.get('kernel', 'Unknown')),
                            'id': 'truenas',
                            'pretty_name': f"TrueNAS - {model_info.get('firmware_version', info.get('kernel', 'Unknown'))}"
                        }
                    else:
                        info['os_release'] = {
                            'name': f"{nas_type} NAS",
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
        logger.info(f"Collecting Linux information")
        
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
        logger.debug(f"Trying lshw command")
        memory_info = self.run_command('lshw -c memory 2>/dev/null')
        if memory_info and 'command not found' not in memory_info.lower() and len(memory_info.strip()) > 10:
            logger.debug(f"lshw successful")
            return self.parse_lshw_memory_output(memory_info)
        
        return {'bios_info': {}, 'memory_banks': [], 'cache_info': [], 'system_memory': {}, 'error': 'Memory module information not available'}
    
    def parse_lshw_memory_output(self, lshw_output: str) -> dict:
        """Parse lshw memory output into structured data"""
        memory_data = {
            'bios_info': {},
            'memory_banks': [],
            'cache_info': [],
            'system_memory': {}
        }
        
        lines = lshw_output.split('\n')
        current_section = None
        current_bank = {}
        
        for line in lines:
            line = line.strip()
            
            if '*-firmware' in line or '*-bios' in line:
                current_section = 'bios'
                continue
            elif '*-memory' in line and 'UNCLAIMED' not in line:
                if current_bank:
                    memory_data['memory_banks'].append(current_bank)
                current_bank = {}
                current_section = 'memory'
                continue
            elif '*-cache' in line:
                current_section = 'cache'
                continue
            
            if ':' in line and current_section:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                if current_section == 'bios':
                    memory_data['bios_info'][key] = value
                elif current_section == 'memory':
                    current_bank[key] = value
                elif current_section == 'cache':
                    if 'cache_info' not in memory_data:
                        memory_data['cache_info'] = []
                    cache_entry = {key: value}
                    memory_data['cache_info'].append(cache_entry)
        
        if current_bank:
            memory_data['memory_banks'].append(current_bank)
        
        return memory_data
    
    def get_services(self) -> List[Dict]:
        """Get systemd services with enhanced information and auto-updating database"""
        services = []
        
        # Get list of active services
        result = self.run_command(
            "systemctl list-units --type=service --state=active --no-pager --plain | "
            "awk '{print $1}' | grep -v '^UNIT' | head -20"
        )
        
        if not result:
            return services
        
        for service in result.split('\n'):
            if not service.strip():
                continue
                
            service_name = service.strip()
            logger.debug(f"Collecting enhanced data for service: {service_name}")
            
            # Collect enhanced service data
            enhanced_data = self._collect_service_enhanced_data(service_name)
            
            # Use enhanced service database with auto-updating
            enhanced_service = self.services_db.enhance_service(
                service_name, 
                'active',
                enhanced_data
            )
            services.append(enhanced_service)
        
        return services
    
    def _collect_service_enhanced_data(self, service_name: str) -> Dict[str, Any]:
        """Collect comprehensive service metadata"""
        enhanced_data = {}
        
        # Get detailed service information using systemctl show
        show_result = self.run_command(f"systemctl show {service_name} --no-pager")
        if show_result:
            show_data = self._parse_systemctl_show(show_result)
            enhanced_data.update(show_data)
        
        # Get process information for running services
        process_data = self._get_service_process_info(service_name)
        if process_data:
            enhanced_data.update(process_data)
        
        # Get package information
        if enhanced_data.get('binary_path'):
            package_data = self._get_package_info(enhanced_data['binary_path'])
            if package_data:
                enhanced_data.update(package_data)
        
        # Get configuration file paths
        config_files = self._get_service_config_files(service_name, enhanced_data.get('unit_file_path'))
        if config_files:
            enhanced_data['config_files'] = config_files
        
        return enhanced_data
    
    def _parse_systemctl_show(self, show_output: str) -> Dict[str, Any]:
        """Parse systemctl show output for service metadata"""
        data = {}
        
        for line in show_output.split('\n'):
            if '=' not in line:
                continue
                
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            
            # Map systemctl properties to our enhanced data structure
            if key == 'Type':
                data['service_type'] = value
            elif key == 'UnitFileState':
                data['auto_start'] = value
            elif key == 'FragmentPath':
                data['unit_file_path'] = value
            elif key == 'Requires':
                if value:
                    data['dependencies'] = [dep.strip() for dep in value.split() if dep.strip()]
            elif key == 'Wants':
                # Combine with existing dependencies
                wants = [dep.strip() for dep in value.split() if dep.strip()]
                if wants:
                    existing_deps = data.get('dependencies', [])
                    data['dependencies'] = list(set(existing_deps + wants))
        
        return data
    
    def _get_service_process_info(self, service_name: str) -> Dict[str, Any]:
        """Get process information for a running service"""
        data = {}
        
        # Get main PID
        pid_result = self.run_command(f"systemctl show {service_name} --property=MainPID --value")
        if not pid_result or pid_result.strip() == '0':
            return data
        
        pid = pid_result.strip()
        
        # Get process information from /proc
        proc_data = self._get_proc_info(pid)
        if proc_data:
            data.update(proc_data)
        
        # Get process information from ps
        ps_result = self.run_command(f"ps -p {pid} -o pid,user,cmd --no-headers 2>/dev/null")
        if ps_result:
            ps_parts = ps_result.strip().split(None, 2)
            if len(ps_parts) >= 3:
                data['user_context'] = ps_parts[1]
                if not data.get('command_line'):  # Only set if not already set from /proc
                    data['command_line'] = ps_parts[2]
        
        return data
    
    def _get_proc_info(self, pid: str) -> Dict[str, Any]:
        """Get process information from /proc filesystem"""
        data = {}
        
        try:
            # Get command line
            cmdline_result = self.run_command(f"cat /proc/{pid}/cmdline 2>/dev/null | tr '\\0' ' '")
            if cmdline_result:
                data['command_line'] = cmdline_result.strip()
                
                # Extract binary path (first argument)
                cmd_parts = cmdline_result.strip().split()
                if cmd_parts:
                    data['binary_path'] = cmd_parts[0]
            
            # Get working directory
            cwd_result = self.run_command(f"readlink /proc/{pid}/cwd 2>/dev/null")
            if cwd_result:
                data['working_directory'] = cwd_result.strip()
                
        except Exception as e:
            logger.debug(f"Error reading /proc info for PID {pid}: {e}")
        
        return data
    
    def _get_package_info(self, binary_path: str) -> Dict[str, Any]:
        """Get package information for a binary"""
        data = {}
        
        # Try different package managers
        package_commands = [
            # RPM-based systems
            f"rpm -qf {binary_path} 2>/dev/null",
            # Debian-based systems  
            f"dpkg -S {binary_path} 2>/dev/null | cut -d: -f1",
            # Arch-based systems
            f"pacman -Qo {binary_path} 2>/dev/null | awk '{{print $5}}'"
        ]
        
        for cmd in package_commands:
            result = self.run_command(cmd)
            if result and result.strip() and 'not found' not in result.lower():
                package_name = result.strip().split('\n')[0]
                
                # Clean up package name
                if ':' in package_name:
                    package_name = package_name.split(':')[0]
                
                data['package_name'] = package_name
                
                # Try to get version
                version = self._get_package_version(package_name)
                if version:
                    data['version'] = version
                
                break
        
        return data
    
    def _get_package_version(self, package_name: str) -> Optional[str]:
        """Get package version"""
        version_commands = [
            # RPM-based
            f"rpm -q {package_name} --queryformat '%{{VERSION}}-%{{RELEASE}}' 2>/dev/null",
            # Debian-based
            f"dpkg -l {package_name} 2>/dev/null | grep '^ii' | awk '{{print $3}}'",
            # Arch-based
            f"pacman -Q {package_name} 2>/dev/null | awk '{{print $2}}'"
        ]
        
        for cmd in version_commands:
            result = self.run_command(cmd)
            if result and result.strip() and 'not found' not in result.lower():
                return result.strip()
        
        return None
    
    def _get_service_config_files(self, service_name: str, unit_file_path: Optional[str]) -> List[str]:
        """Get configuration files associated with a service"""
        config_files = []
        
        # Add unit file if available
        if unit_file_path:
            config_files.append(unit_file_path)
        
        # Look for common config file patterns
        service_base = service_name.replace('.service', '')
        
        config_patterns = [
            f"/etc/{service_base}.conf",
            f"/etc/{service_base}/{service_base}.conf", 
            f"/etc/{service_base}/config",
            f"/etc/default/{service_base}",
            f"/etc/sysconfig/{service_base}",
            f"/usr/lib/systemd/system/{service_name}",
            f"/etc/systemd/system/{service_name}",
            f"/etc/{service_base}/*.conf"
        ]
        
        for pattern in config_patterns:
            # Check if files exist (handle wildcards)
            if '*' in pattern:
                result = self.run_command(f"ls {pattern} 2>/dev/null")
                if result:
                    config_files.extend([f.strip() for f in result.split('\n') if f.strip()])
            else:
                result = self.run_command(f"test -f {pattern} && echo {pattern}")
                if result and result.strip():
                    config_files.append(result.strip())
        
        # Remove duplicates and return
        return list(set(config_files))
    
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
                        
                        service_info = self.services_db.get_service_info(process_name, {
                            'process_info': process_info,
                            'process_name': process_name
                        })
                        
                        port_info = {
                            'port': parts[3],
                            'process': process_info,
                            'process_name': process_name,
                            'service_info': service_info
                        }
                        ports.append(port_info)
        return ports[:20]

