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
from modules.utils import bytes_to_gb, convert_uptime_seconds, check_port_open
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

try:
    from modules.system_mac import MacCollector
    HAS_MAC_COLLECTOR = True
except ImportError:
    HAS_MAC_COLLECTOR = False
    logger = logging.getLogger(__name__)
    logger.warning("Mac collector not available")

try:
    from modules.system_bigip import BigIPCollector
    HAS_BIGIP_COLLECTOR = True
except ImportError:
    HAS_BIGIP_COLLECTOR = False
    logger = logging.getLogger(__name__)
    logger.warning("BIG-IP collector not available")

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
        self.ssh_port = 22
        
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
        self.mac_collector = None
        self.bigip_collector = None
    
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
        
        # Quick port check before attempting SSH methods (saves time on hosts without SSH)
        if check_port_open(self.hostname, port=22, timeout=2.0):
            self.ssh_port = 22
        elif check_port_open(self.hostname, port=22222, timeout=2.0):
            self.ssh_port = 22222
        else:
            logger.debug(f"No SSH port open on {self.hostname}, skipping SSH attempts")
            self.connection_failure_reason = "SSH port 22 not accessible (connection refused or filtered)"
            self.in_detection_mode = False
            return False, 'unreachable'
        
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
        """After successful connection, refine platform detection"""
        if self.platform_type != 'linux':
            return self.platform_type

        # Check if this is macOS/Darwin
        if HAS_MAC_COLLECTOR and self.connection_type in ['ssh_key', 'ssh_password']:
            temp_mac_collector = MacCollector(self.run_command) # type: ignore
            if temp_mac_collector.detect_mac():
                logger.info(f"Refined detection: {self.hostname} is macOS")
                self.platform_type = 'mac'
                self.platform_info['detection_method'] += ' (refined to macOS)'
                self.mac_collector = temp_mac_collector
                return 'mac'

        # Check if this is an F5 BIG-IP
        if HAS_BIGIP_COLLECTOR and self.connection_type in ['ssh_key', 'ssh_password']:
            temp_bigip_collector = BigIPCollector(self.run_command) # type: ignore
            if temp_bigip_collector.detect_bigip():
                logger.info(f"Refined detection: {self.hostname} is F5 BIG-IP")
                self.platform_type = 'bigip'
                self.platform_info['detection_method'] += ' (refined to F5 BIG-IP)'
                self.bigip_collector = temp_bigip_collector
                return 'bigip'

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
                    port=self.ssh_port,
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
                    port=self.ssh_port,
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
            elif final_platform_type == 'mac':
                info.update(self.collect_mac_info())
            elif final_platform_type == 'bigip':
                info.update(self.collect_bigip_info())
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
    
    def collect_mac_info(self) -> Dict:
        """Collect macOS-specific information"""
        logger.info(f"Collecting macOS information")

        info: Dict[str, Any] = {}

        # Use macOS-compatible commands for basic system info
        commands = {
            'kernel': 'uname -r',
            'architecture': 'uname -m',
            'uptime': "uptime | sed 's/.*up /up /' | sed 's/,  [0-9]* user.*//'",
            'load_average': "uptime | awk -F'load averages:' '{print $2}'",
            'disk_usage': "df -h / | tail -1 | awk '{print $3 \"/\" $2 \" (\" $5 \")\"}'",
            'cpu_cores': 'sysctl -n hw.ncpu',
            'ip_addresses': "ifconfig | grep 'inet ' | grep -v 127.0.0.1 | awk '{print $2}'",
        }

        for key, command in commands.items():
            result = self.run_command(command)
            info[key] = result if result else 'Unknown'

        # CPU name: try Intel first, fall back to model identifier for Apple Silicon
        cpu_name = self.run_command('sysctl -n machdep.cpu.brand_string 2>/dev/null')
        if not cpu_name:
            cpu_name = self.run_command('sysctl -n hw.model 2>/dev/null')
        info['cpu_info'] = cpu_name or 'Unknown'

        # Total memory
        mem_bytes = self.run_command("sysctl -n hw.memsize 2>/dev/null")
        if mem_bytes:
            try:
                info['memory_total'] = f"{int(mem_bytes) / 1024 / 1024 / 1024:.1f} GB"
            except (ValueError, TypeError):
                info['memory_total'] = 'Unknown'
        else:
            info['memory_total'] = 'Unknown'

        # Active memory usage from vm_stat
        vm_stat = self.run_command("vm_stat 2>/dev/null | grep 'Pages active' | awk '{print $3}' | tr -d '.'")
        page_size = self.run_command("sysctl -n hw.pagesize 2>/dev/null")
        if vm_stat and page_size:
            try:
                used_bytes = int(vm_stat) * int(page_size)
                info['memory_used'] = f"{used_bytes / 1024 / 1024 / 1024:.1f} GB"
            except (ValueError, TypeError):
                info['memory_used'] = 'Unknown'
        else:
            info['memory_used'] = 'Unknown'

        # Build os_release from sw_vers
        product_name = self.run_command('sw_vers -productName 2>/dev/null') or 'macOS'
        product_version = self.run_command('sw_vers -productVersion 2>/dev/null') or 'Unknown'
        build_version = self.run_command('sw_vers -buildVersion 2>/dev/null') or 'Unknown'
        info['os_release'] = {
            'name': product_name,
            'version': product_version,
            'id': 'macos',
            'pretty_name': f"{product_name} {product_version} ({build_version})"
        }

        # Collect Mac-specific hardware info
        if self.mac_collector:
            mac_data = self.mac_collector.collect_mac_info()
            if mac_data:
                info['mac_info'] = mac_data

        # Listening ports (Mac-compatible)
        if self.mac_collector:
            info['listening_ports'] = self.mac_collector.get_listening_ports()
        else:
            info['listening_ports'] = []

        # Docker may be present via Docker Desktop
        info['docker_containers'] = self.get_docker_containers()
        info['services'] = self.get_services_launchd()
        info['memory_modules'] = {}
        info['bios_info'] = {}

        return info

    def collect_bigip_info(self) -> Dict:
        """Collect F5 BIG-IP-specific information"""
        logger.info(f"Collecting BIG-IP information")

        info: Dict[str, Any] = {}

        # BIG-IP runs Linux under the hood — most standard commands work
        commands = {
            'kernel': 'uname -r',
            'architecture': 'uname -m',
            'uptime': 'uptime -p 2>/dev/null || uptime',
            'load_average': "uptime | awk -F'load average:' '{print $2}'",
            'memory_total': "free -h 2>/dev/null | grep Mem | awk '{print $2}'",
            'memory_used': "free -h 2>/dev/null | grep Mem | awk '{print $3}'",
            'disk_usage': "df -h / | tail -1 | awk '{print $3 \"/\" $2 \" (\" $5 \")\"}'",
            'cpu_info': "lscpu 2>/dev/null | grep 'Model name' | cut -d: -f2 | xargs",
            'cpu_cores': 'nproc 2>/dev/null',
            'ip_addresses': "ip -4 addr show 2>/dev/null | grep inet | awk '{print $2}' | grep -v 127.0.0.1",
        }

        for key, command in commands.items():
            result = self.run_command(command)
            info[key] = result if result else 'Unknown'

        # Build os_release from BIG-IP version info via tmsh
        version_raw = self.run_command('tmsh show sys version 2>/dev/null')
        product = 'BIG-IP'
        version = 'Unknown'
        if version_raw:
            for line in version_raw.split('\n'):
                parts = line.split(None, 1)
                if len(parts) == 2:
                    if parts[0].lower() == 'product':
                        product = parts[1].strip()
                    elif parts[0].lower() == 'version':
                        version = parts[1].strip()
        info['os_release'] = {
            'name': product,
            'version': version,
            'id': 'bigip',
            'pretty_name': f"{product} {version}"
        }

        # Collect BIG-IP-specific info
        if self.bigip_collector:
            bigip_data = self.bigip_collector.collect_bigip_info()
            if bigip_data:
                info['bigip_info'] = bigip_data

        info['services'] = self.get_services()
        info['docker_containers'] = []
        info['listening_ports'] = self.get_listening_ports()
        info['memory_modules'] = {}
        info['bios_info'] = {}

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
        info['installed_packages'] = self.get_installed_packages()
        info['cron_jobs'] = self.get_cron_jobs()
        info['firewall_info'] = self.get_firewall_rules()
        info['local_users'] = self.get_local_users()
        info['login_history'] = self.get_login_history()
        info['lldp_uplinks'] = self.get_lldp_info()
        info['bonding_info'] = self.get_bonding_info()

        return info

    def get_installed_packages(self) -> List[Dict]:
        """Get manually/explicitly installed packages (not auto-dependencies)"""
        packages = []

        # Debian/Ubuntu: aptitude '~i!~M' = installed AND not auto-installed (best filter)
        result = self.run_command(
            "aptitude search '~i!~M' -F '%p\t%V' 2>/dev/null | sort"
        )
        if result and 'command not found' not in result.lower() and result.strip():
            for line in result.strip().split('\n'):
                parts = line.split('\t', 1)
                if parts[0].strip():
                    packages.append({'name': parts[0].strip(), 'version': parts[1].strip() if len(parts) > 1 else ''})
            return packages

        # Debian/Ubuntu fallback: apt-mark showmanual + xargs to dpkg-query for versions
        result = self.run_command(
            "apt-mark showmanual 2>/dev/null | sort | "
            "xargs dpkg-query -W -f='${Package}\\t${Version}\\n' 2>/dev/null"
        )
        if result and 'command not found' not in result.lower() and result.strip():
            for line in result.strip().split('\n'):
                parts = line.split('\t', 1)
                if parts[0].strip():
                    packages.append({'name': parts[0].strip(), 'version': parts[1].strip() if len(parts) > 1 else ''})
            return packages

        # RHEL/CentOS/Fedora: dnf repoquery --userinstalled (explicitly installed only)
        result = self.run_command(
            "dnf repoquery --userinstalled --qf '%{name}\t%{version}-%{release}' 2>/dev/null | sort"
        )
        if result and 'command not found' not in result.lower() and result.strip():
            for line in result.strip().split('\n'):
                parts = line.split('\t', 1)
                if parts[0].strip():
                    packages.append({'name': parts[0].strip(), 'version': parts[1].strip() if len(parts) > 1 else ''})
            return packages

        # openSUSE: zypper packages --installed-only
        # Columns: S | Repository | Name | Version | Arch  (data starts at row 5)
        result = self.run_command(
            "zypper packages --installed-only 2>/dev/null | "
            "awk -F'|' 'NR>4 {gsub(/ /,\"\",$3); gsub(/ /,\"\",$4); gsub(/ /,\"\",$5); "
            "if($3==\"i\") print $4\"\\t\"$5}' | sort"
        )
        if result and 'command not found' not in result.lower() and result.strip():
            for line in result.strip().split('\n'):
                parts = line.split('\t', 1)
                if parts[0].strip():
                    packages.append({'name': parts[0].strip(), 'version': parts[1].strip() if len(parts) > 1 else ''})
            return packages

        # RPM fallback: all installed packages
        result = self.run_command(
            "rpm -qa --queryformat '%{NAME}\\t%{VERSION}-%{RELEASE}\\n' 2>/dev/null | sort"
        )
        if result and 'command not found' not in result.lower():
            for line in result.strip().split('\n'):
                parts = line.split('\t', 1)
                if parts[0].strip():
                    packages.append({'name': parts[0].strip(), 'version': parts[1].strip() if len(parts) > 1 else ''})
            return packages

        return packages

    def get_cron_jobs(self) -> List[Dict]:
        """Collect cron jobs from root crontab, /etc/crontab, and /etc/cron.d"""
        cron_jobs = []

        # Root's personal crontab
        root_cron = self.run_command(
            "crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$'"
        )
        if root_cron:
            for line in root_cron.strip().split('\n'):
                if line.strip():
                    cron_jobs.append({'source': 'root crontab', 'entry': line.strip()})

        # /etc/crontab (skip comments, blanks, and variable assignments)
        etc_cron = self.run_command(
            "grep -v '^#' /etc/crontab 2>/dev/null | grep -v '^$' "
            "| grep -v '^SHELL' | grep -v '^PATH' | grep -v '^MAILTO'"
        )
        if etc_cron:
            for line in etc_cron.strip().split('\n'):
                if line.strip():
                    cron_jobs.append({'source': '/etc/crontab', 'entry': line.strip()})

        # /etc/cron.d/ files - list each file name and its non-comment entries
        cron_d_list = self.run_command('ls /etc/cron.d/ 2>/dev/null')
        if cron_d_list:
            for fname in cron_d_list.strip().split('\n'):
                fname = fname.strip()
                if not fname:
                    continue
                entries = self.run_command(
                    f"grep -v '^#' /etc/cron.d/{fname} 2>/dev/null | grep -v '^$' "
                    f"| grep -v '^SHELL' | grep -v '^PATH' | grep -v '^MAILTO'"
                )
                if entries:
                    for line in entries.strip().split('\n'):
                        if line.strip():
                            cron_jobs.append({'source': f'/etc/cron.d/{fname}', 'entry': line.strip()})

        return cron_jobs

    def get_firewall_rules(self) -> Dict:
        """Collect firewall rules — tries ufw, firewalld, then iptables"""
        # ufw (Ubuntu/Debian)
        ufw = self.run_command('ufw status verbose 2>/dev/null')
        if ufw and 'Status:' in ufw:
            status = 'active' if 'Status: active' in ufw else 'inactive'
            return {'type': 'ufw', 'status': status, 'rules_text': ufw}

        # firewalld (RHEL/CentOS/Fedora)
        fwd = self.run_command('firewall-cmd --list-all 2>/dev/null')
        if fwd and 'not running' not in fwd.lower() and 'command not found' not in fwd.lower() and fwd.strip():
            return {'type': 'firewalld', 'status': 'active', 'rules_text': fwd}

        # iptables fallback
        ipt = self.run_command('iptables -L -n --line-numbers 2>/dev/null | head -60')
        if ipt and 'command not found' not in ipt.lower() and ipt.strip():
            return {'type': 'iptables', 'status': 'active', 'rules_text': ipt}

        return {}

    def get_local_users(self) -> List[Dict]:
        """Collect local user accounts (UID >= 1000) with sudo access info"""
        users = []

        # Get root (UID 0) and regular users (UID >= 1000)
        passwd = self.run_command(
            "getent passwd 2>/dev/null | awk -F: '$3 == 0 || $3 >= 1000 {print $1\":\"$5\":\"$6\":\"$7}'"
        )
        if not passwd:
            return users

        # Get all members of sudo/wheel/admin groups in one shot
        sudo_members_raw = self.run_command(
            "getent group sudo wheel admin 2>/dev/null | cut -d: -f4 | tr ',' '\\n' | sort -u"
        )
        sudo_members = set()
        if sudo_members_raw:
            sudo_members = {u.strip() for u in sudo_members_raw.strip().split('\n') if u.strip()}

        for line in passwd.strip().split('\n'):
            parts = line.split(':')
            if len(parts) < 4:
                continue
            username, full_name, home, shell = parts[0], parts[1], parts[2], parts[3]
            if not username:
                continue
            # Skip service accounts with no-login shells (but always keep root)
            if username != 'root' and ('nologin' in shell or 'false' in shell):
                continue
            users.append({
                'username': username,
                'full_name': full_name or '',
                'home': home or '',
                'shell': shell or '',
                'sudo': username in sudo_members
            })

        return users

    def get_login_history(self) -> Dict:
        """Collect last boot time and recent login history"""
        history: Dict[str, Any] = {}

        # Last system boot — uptime -s gives full YYYY-MM-DD HH:MM:SS
        boot = self.run_command("uptime -s 2>/dev/null || who -b 2>/dev/null | awk '{print $3, $4}'")
        if boot and boot.strip():
            history['last_boot'] = boot.strip()

        # Recent logins — parse 'last' output into structured rows
        last_raw = self.run_command(
            "last -n 10 2>/dev/null | grep -v '^reboot' | grep -v '^wtmp' | grep -v '^$' | head -10"
        )
        logins = []
        if last_raw:
            for line in last_raw.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 5:
                    logins.append({
                        'user': parts[0],
                        'terminal': parts[1],
                        'from': parts[2] if not parts[2].startswith(':') else 'local',
                        'when': ' '.join(parts[3:7]) if len(parts) > 6 else ' '.join(parts[3:])
                    })
        if logins:
            history['logins'] = logins

        return history

    def get_lldp_info(self) -> List[Dict]:
        """Collect LLDP neighbor info for physical switch uplinks (requires lldpd)"""
        import json as _json

        result = self.run_command('lldpctl -f json 2>/dev/null')
        if not result or not result.strip():
            return []

        try:
            data = _json.loads(result)
        except Exception:
            return []

        lldp_data = data.get('lldp', {})
        if not lldp_data:
            return []

        # lldpctl may return a single dict or a list for 'interface'
        iface_data = lldp_data.get('interface', [])
        if isinstance(iface_data, dict):
            iface_data = [iface_data]

        # Virtual/internal interface prefixes to ignore
        virtual_prefixes = ('fwpr', 'fwln', 'veth', 'docker', 'br-', 'virbr', 'lxc', 'tap')

        uplinks = []
        for iface in iface_data:
            if not isinstance(iface, dict):
                continue

            # Each item is {"nic4": {...}} — interface name is the key
            iface_name = list(iface.keys())[0]
            iface_content = iface[iface_name]
            if not isinstance(iface_content, dict):
                continue

            if not iface_name or any(iface_name.startswith(p) for p in virtual_prefixes):
                continue

            chassis_map = iface_content.get('chassis', {})
            if not isinstance(chassis_map, dict):
                continue

            for switch_name, chassis_data in chassis_map.items():
                if not isinstance(chassis_data, dict):
                    continue

                sys_descr = chassis_data.get('descr', '')
                # Skip Linux hosts advertising themselves via internal Proxmox veth pairs
                if 'Linux' in sys_descr:
                    continue

                # Port description and link speed
                port_info = iface_content.get('port', {})
                port_descr = port_info.get('descr', '') if isinstance(port_info, dict) else ''
                link_speed = ''
                if isinstance(port_info, dict):
                    autoneg = port_info.get('auto-negotiation', {})
                    if isinstance(autoneg, dict):
                        raw_speed = autoneg.get('current', '')
                        link_speed = raw_speed.split(' - ')[0] if raw_speed else ''

                # ethtool fallback if LLDP didn't provide link speed
                if not link_speed:
                    ethtool_out = self.run_command(f'ethtool {iface_name} 2>/dev/null | grep -i "speed:"')
                    if ethtool_out and ethtool_out.strip():
                        parts = ethtool_out.strip().split(':', 1)
                        if len(parts) == 2:
                            link_speed = parts[1].strip()

                # VLAN — find the pvid (native VLAN)
                vlan_id = ''
                vlan_info = iface_content.get('vlan', {})
                if isinstance(vlan_info, dict):
                    vlan_id = str(vlan_info.get('vlan-id', ''))
                elif isinstance(vlan_info, list):
                    for v in vlan_info:
                        if isinstance(v, dict) and v.get('pvid'):
                            vlan_id = str(v.get('vlan-id', ''))
                            break

                # Switch MAC from chassis ID
                switch_mac = ''
                chassis_id = chassis_data.get('id', {})
                if isinstance(chassis_id, dict):
                    switch_mac = chassis_id.get('value', '')

                # Management IP — may be a string or list
                mgmt_ip = ''
                raw_mgmt = chassis_data.get('mgmt-ip', '')
                if isinstance(raw_mgmt, list):
                    mgmt_ip = raw_mgmt[0] if raw_mgmt else ''
                elif isinstance(raw_mgmt, str):
                    mgmt_ip = raw_mgmt

                uplinks.append({
                    'local_interface': iface_name,
                    'switch_name': switch_name,
                    'switch_descr': sys_descr,
                    'switch_mac': switch_mac,
                    'switch_mgmt_ip': mgmt_ip,
                    'switch_port': port_descr,
                    'link_speed': link_speed,
                    'vlan': vlan_id
                })

        return uplinks

    def get_bonding_info(self) -> List[Dict]:
        """Collect bonding/LACP interface info from /proc/net/bonding/"""
        bonds = []

        bond_list = self.run_command('ls /proc/net/bonding/ 2>/dev/null')
        if not bond_list or not bond_list.strip():
            return []

        for bond_name in bond_list.strip().split():
            bond_name = bond_name.strip()
            if not bond_name:
                continue

            content = self.run_command(f'cat /proc/net/bonding/{bond_name} 2>/dev/null')
            if not content:
                continue

            bond = {'name': bond_name, 'mode': '', 'slaves': []}
            current_slave = None

            for line in content.strip().split('\n'):
                line = line.strip()
                if line.startswith('Bonding Mode:'):
                    bond['mode'] = line.split(':', 1)[1].strip()
                elif line.startswith('Slave Interface:'):
                    if current_slave:
                        bond['slaves'].append(current_slave)
                    current_slave = {
                        'name': line.split(':', 1)[1].strip(),
                        'status': '',
                        'speed': '',
                        'duplex': ''
                    }
                elif current_slave:
                    if line.startswith('MII Status:'):
                        current_slave['status'] = line.split(':', 1)[1].strip()
                    elif line.startswith('Speed:'):
                        current_slave['speed'] = line.split(':', 1)[1].strip()
                    elif line.startswith('Duplex:'):
                        current_slave['duplex'] = line.split(':', 1)[1].strip()

            if current_slave:
                bond['slaves'].append(current_slave)

            bonds.append(bond)

        return bonds

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

        logger.debug(f"lshw unavailable or empty, trying dmidecode")
        dmi_info = self.run_command('dmidecode -t memory 2>/dev/null')
        if dmi_info and 'command not found' not in dmi_info.lower() and 'Memory Device' in dmi_info:
            logger.debug(f"dmidecode successful")
            return self.parse_dmidecode_memory_output(dmi_info)

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
        in_memory_section = False
        
        for line in lines:
            line = line.strip()
            
            # Detect main sections
            if '*-firmware' in line or '*-bios' in line:
                current_section = 'bios'
                in_memory_section = False
                continue
            elif '*-memory' in line and 'UNCLAIMED' not in line:
                current_section = 'system_memory'
                in_memory_section = True
                continue
            elif '*-cache' in line:
                current_section = 'cache'
                in_memory_section = False
                continue
            elif line.startswith('*-bank:') or line.startswith('*-slot:') or line.startswith('*-dimm:'):
                # Found an individual memory bank/slot
                if current_bank:
                    memory_data['memory_banks'].append(current_bank)
                current_bank = {}
                current_section = 'memory_bank'
                in_memory_section = True
                continue
            elif line.startswith('*-') and in_memory_section:
                # Another section started while in memory, save current bank
                if current_bank and current_section == 'memory_bank':
                    memory_data['memory_banks'].append(current_bank)
                    current_bank = {}
                current_section = None
                in_memory_section = False
                continue
            
            # Parse key-value pairs
            if ':' in line and current_section:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                
                if current_section == 'bios':
                    memory_data['bios_info'][key] = value
                elif current_section == 'system_memory':
                    memory_data['system_memory'][key] = value
                elif current_section == 'memory_bank':
                    current_bank[key] = value
                elif current_section == 'cache':
                    # Handle cache as a list of entries
                    if not memory_data['cache_info']:
                        memory_data['cache_info'] = [{}]
                    if key in memory_data['cache_info'][-1]:
                        # Start a new cache entry if key already exists
                        memory_data['cache_info'].append({key: value})
                    else:
                        memory_data['cache_info'][-1][key] = value
        
        # Don't forget the last memory bank
        if current_bank and current_section == 'memory_bank':
            memory_data['memory_banks'].append(current_bank)

        return memory_data

    def parse_dmidecode_memory_output(self, dmi_output: str) -> dict:
        """Parse dmidecode -t memory output into structured data matching the lshw format"""
        memory_data = {
            'bios_info': {},
            'memory_banks': [],
            'cache_info': [],
            'system_memory': {}
        }

        current_bank = None
        total_size_mb = 0

        for line in dmi_output.split('\n'):
            line = line.strip()

            if line == 'Memory Device':
                if current_bank:
                    memory_data['memory_banks'].append(current_bank)
                current_bank = {}
                continue

            if current_bank is None:
                continue

            if ':' in line:
                key, _, value = line.partition(':')
                key = key.strip()
                value = value.strip()

                if key == 'Size':
                    if 'No Module Installed' in value or 'Not Installed' in value:
                        current_bank['size'] = 'Empty'
                    else:
                        current_bank['size'] = value
                        # Accumulate total for system_memory
                        try:
                            if 'GB' in value:
                                total_size_mb += int(value.split()[0]) * 1024
                            elif 'MB' in value:
                                total_size_mb += int(value.split()[0])
                        except (ValueError, IndexError):
                            pass
                elif key == 'Locator':
                    current_bank['slot'] = value
                elif key == 'Bank Locator':
                    if value and value != 'Not Specified':
                        current_bank['bank'] = value
                elif key == 'Type':
                    current_bank['type'] = value
                elif key == 'Speed':
                    current_bank['speed'] = value
                elif key == 'Manufacturer':
                    if value and value.upper() not in ('UNKNOWN', 'NOT SPECIFIED', ''):
                        current_bank['vendor'] = value.strip()
                elif key == 'Part Number':
                    if value and value.upper() not in ('NOT AVAILABLE', 'NOT SPECIFIED', ''):
                        current_bank['product'] = value.strip()
                elif key == 'Serial Number':
                    if value and value.upper() not in ('NOT SPECIFIED', 'NOT PROVIDED', ''):
                        current_bank['serial'] = value.strip()

        if current_bank:
            memory_data['memory_banks'].append(current_bank)

        if total_size_mb:
            if total_size_mb >= 1024:
                memory_data['system_memory']['size'] = f"{total_size_mb // 1024} GiB"
            else:
                memory_data['system_memory']['size'] = f"{total_size_mb} MiB"

        return memory_data

    def get_services_launchd(self) -> List[Dict]:
        """Get running launchd services on macOS (non-Apple only)"""
        services = []
        result = self.run_command(
            "launchctl list 2>/dev/null | awk 'NR>1 && $1 != \"-\" {print $3}'"
            " | grep -v '^com.apple' | grep -v '^com.openssh' | head -30"
        )
        if not result:
            return services

        for label in result.strip().split('\n'):
            label = label.strip()
            if not label:
                continue
            enhanced_service = self.services_db.enhance_service(label, 'active', {})
            services.append(enhanced_service)

        return services

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

