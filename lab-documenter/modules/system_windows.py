"""
Windows information collection for Lab Documenter

Handles Windows system information gathering via WinRM.
"""

import logging
from typing import Dict, List, Callable, Optional, Any

logger = logging.getLogger(__name__)

class WindowsCollector:
    def __init__(self, command_runner: Callable[[str], Optional[str]]):
        """
        Initialize Windows collector.
        
        Args:
            command_runner: Function that executes commands via WinRM
        """
        self.run_command = command_runner
        
    def collect_windows_info(self) -> Dict[str, Any]:
        """Get comprehensive Windows information"""
        windows_info: Dict[str, Any] = {}
        
        # Collect basic system information
        windows_info['os_release'] = self.get_windows_version()
        windows_info['system_info'] = self.get_system_info()
        windows_info['memory_info'] = self.get_memory_info()
        windows_info['disk_info'] = self.get_disk_info()
        windows_info['network_info'] = self.get_network_info()
        windows_info['services'] = self.get_windows_services()
        windows_info['features'] = self.get_windows_features()
        windows_info['updates'] = self.get_update_info()
        
        return windows_info
    
    def get_windows_version(self) -> Dict[str, str]:
        """Get Windows version information"""
        version_info = {}
        
        # Get detailed Windows info using PowerShell
        cmd = ('Get-ComputerInfo -Property WindowsProductName,WindowsVersion,'
               'WindowsBuildLabEx,TotalPhysicalMemory | ConvertTo-Json')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            try:
                import json
                data = json.loads(result)
                version_info = {
                    'name': data.get('WindowsProductName', 'Windows'),
                    'version': data.get('WindowsVersion', 'Unknown'),
                    'build': data.get('WindowsBuildLabEx', 'Unknown'),
                    'id': 'windows',
                    'pretty_name': data.get('WindowsProductName', 'Windows')
                }
            except Exception as e:
                logger.warning(f"Failed to parse Windows version info: {e}")
                # Fallback to basic detection
                ver_result = self.run_command('ver')
                version_info = {
                    'name': 'Windows',
                    'version': ver_result if ver_result else 'Unknown',
                    'id': 'windows',
                    'pretty_name': f'Microsoft Windows ({ver_result})' if ver_result else 'Microsoft Windows'
                }
        
        return version_info
    
    def get_system_info(self) -> Dict[str, str]:
        """Get basic system information"""
        system_info = {}
        
        # CPU information
        cpu_cmd = 'Get-WmiObject -Class Win32_Processor | Select-Object Name,NumberOfCores | ConvertTo-Json'
        cpu_result = self.run_command(f'powershell -Command "{cpu_cmd}"')
        if cpu_result:
            try:
                import json
                cpu_data = json.loads(cpu_result)
                if isinstance(cpu_data, list):
                    cpu_data = cpu_data[0]
                system_info['cpu_info'] = cpu_data.get('Name', 'Unknown')
                system_info['cpu_cores'] = str(cpu_data.get('NumberOfCores', 'Unknown'))
            except Exception as e:
                logger.warning(f"Failed to parse CPU info: {e}")
                system_info['cpu_info'] = 'Unknown'
                system_info['cpu_cores'] = 'Unknown'
        
        # Uptime
        uptime_cmd = '(Get-Date) - (Get-CimInstance -ClassName Win32_OperatingSystem).LastBootUpTime'
        uptime_result = self.run_command(f'powershell -Command "{uptime_cmd}"')
        if uptime_result and 'Days' in uptime_result:
            system_info['uptime'] = uptime_result.strip()
        else:
            system_info['uptime'] = 'Unknown'
        
        # Architecture
        arch_result = self.run_command('powershell -Command "$env:PROCESSOR_ARCHITECTURE"')
        system_info['architecture'] = arch_result.strip() if arch_result else 'Unknown'
        
        return system_info
    
    def get_memory_info(self) -> Dict[str, str]:
        """Get memory information"""
        memory_info = {}
        
        cmd = ('Get-WmiObject -Class Win32_PhysicalMemory | '
               'Measure-Object -Property Capacity -Sum | '
               'Select-Object @{Name="TotalGB";Expression={[math]::Round($_.Sum/1GB,2)}} | '
               'ConvertTo-Json')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            try:
                import json
                data = json.loads(result)
                memory_info['memory_total'] = f"{data.get('TotalGB', 'Unknown')}GB"
            except Exception as e:
                logger.warning(f"Failed to parse memory info: {e}")
                memory_info['memory_total'] = 'Unknown'
        
        # Available memory
        avail_cmd = ('Get-Counter "\\Memory\\Available MBytes" | '
                    'Select-Object -ExpandProperty CounterSamples | '
                    'Select-Object -ExpandProperty CookedValue')
        
        avail_result = self.run_command(f'powershell -Command "{avail_cmd}"')
        if avail_result:
            try:
                avail_mb = float(avail_result.strip())
                memory_info['memory_available'] = f"{avail_mb/1024:.1f}GB"
            except Exception as e:
                logger.warning(f"Failed to parse available memory: {e}")
                memory_info['memory_available'] = 'Unknown'
        
        return memory_info
    
    def get_disk_info(self) -> List[Dict[str, str]]:
        """Get disk information"""
        disks = []
        
        cmd = ('Get-WmiObject -Class Win32_LogicalDisk | '
               'Select-Object DeviceID,Size,FreeSpace,FileSystem | '
               'ConvertTo-Json')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            try:
                import json
                data = json.loads(result)
                if not isinstance(data, list):
                    data = [data]
                
                for disk in data:
                    if disk.get('Size'):
                        size_gb = int(disk['Size']) / (1024**3)
                        free_gb = int(disk.get('FreeSpace', 0)) / (1024**3)
                        used_gb = size_gb - free_gb
                        usage_pct = (used_gb / size_gb) * 100
                        
                        disks.append({
                            'device': disk.get('DeviceID', 'Unknown'),
                            'filesystem': disk.get('FileSystem', 'Unknown'),
                            'size': f"{size_gb:.1f}GB",
                            'used': f"{used_gb:.1f}GB",
                            'available': f"{free_gb:.1f}GB",
                            'usage_percent': f"{usage_pct:.1f}%"
                        })
            except Exception as e:
                logger.warning(f"Failed to parse disk info: {e}")
        
        return disks
    
    def get_network_info(self) -> List[Dict[str, str]]:
        """Get network interface information"""
        interfaces = []
        
        cmd = ('Get-NetAdapter | Where-Object {$_.Status -eq "Up"} | '
               'Select-Object Name,InterfaceDescription,LinkSpeed | '
               'ConvertTo-Json')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            try:
                import json
                data = json.loads(result)
                if not isinstance(data, list):
                    data = [data]
                
                for iface in data:
                    interfaces.append({
                        'name': iface.get('Name', 'Unknown'),
                        'description': iface.get('InterfaceDescription', 'Unknown'),
                        'speed': str(iface.get('LinkSpeed', 'Unknown'))
                    })
            except Exception as e:
                logger.warning(f"Failed to parse network info: {e}")
        
        return interfaces
    
    def get_windows_services(self) -> List[Dict[str, str]]:
        """Get Windows services information"""
        services = []
        
        cmd = ('Get-Service | Where-Object {$_.Status -eq "Running"} | '
               'Select-Object Name,DisplayName,Status | '
               'ConvertTo-Json')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            try:
                import json
                data = json.loads(result)
                if not isinstance(data, list):
                    data = [data]
                
                for service in data[:20]:  # Limit to first 20
                    services.append({
                        'name': service.get('Name', 'Unknown'),
                        'display_name': service.get('DisplayName', 'Unknown'),
                        'status': service.get('Status', 'Unknown'),
                        'category': 'windows_service'
                    })
            except Exception as e:
                logger.warning(f"Failed to parse services: {e}")
        
        return services
    
    def get_windows_features(self) -> List[str]:
        """Get installed Windows features"""
        features = []
        
        cmd = ('Get-WindowsFeature | Where-Object {$_.InstallState -eq "Installed"} | '
               'Select-Object -ExpandProperty Name')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            features = [feature.strip() for feature in result.split('\n') if feature.strip()]
        
        return features[:10]  # Limit for display
    
    def get_update_info(self) -> Dict[str, Any]:
        """Get Windows Update information"""
        update_info = {}
        
        # Last update check
        cmd = ('Get-HotFix | Sort-Object InstalledOn -Descending | '
               'Select-Object -First 1 | '
               'Select-Object HotFixID,InstalledOn | '
               'ConvertTo-Json')
        
        result = self.run_command(f'powershell -Command "{cmd}"')
        if result:
            try:
                import json
                data = json.loads(result)
                update_info['last_update'] = {
                    'hotfix_id': data.get('HotFixID', 'Unknown'),
                    'installed_on': data.get('InstalledOn', 'Unknown')
                }
            except Exception as e:
                logger.warning(f"Failed to parse update info: {e}")
                update_info['last_update'] = {'hotfix_id': 'Unknown', 'installed_on': 'Unknown'}
        
        return update_info

