"""
NAS system information collection for Lab Documenter

Handles Synology, QNAP, Asustor, Buffalo, TrueNAS and other NAS systems.
"""

import logging
from typing import Dict, List, Callable, Optional, Any

logger = logging.getLogger(__name__)

class NASCollector:
    def __init__(self, command_runner: Callable[[str], Optional[str]]):
        """
        Initialize NAS collector.
        
        Args:
            command_runner: Function that executes commands and returns output
        """
        self.run_command = command_runner
        self.nas_type = None
        
    def detect_nas_type(self) -> Optional[str]:
        """Detect NAS system type"""
        if self.nas_type is None:
            # Check for Synology DSM
            if self.run_command('test -f /etc/synoinfo.conf && echo "synology"'):
                self.nas_type = 'synology'
            # Check for QNAP QTS
            elif self.run_command('test -f /etc/config/uLinux.conf && echo "qnap"'):
                self.nas_type = 'qnap'
            # Check for Asustor ADM
            elif self.run_command('test -d /usr/builtin/etc && echo "asustor"'):
                self.nas_type = 'asustor'
            # Check for Buffalo TeraStation
            elif self.run_command('test -f /etc/nas_feature && echo "buffalo"'):
                self.nas_type = 'buffalo'
            # Check for Netgear ReadyNAS
            elif self.run_command('test -f /etc/raidiator_version && echo "netgear"'):
                self.nas_type = 'netgear'
            # Check for TrueNAS (FreeBSD-based)
            elif self.run_command('test -f /etc/version && grep -i truenas /etc/version && echo "truenas"'):
                self.nas_type = 'truenas'
            # Alternative TrueNAS detection methods
            elif self.run_command('test -f /data/freenas-v1.db && echo "truenas"'):
                self.nas_type = 'truenas'
            elif self.run_command('test -d /usr/local/www/freenasUI && echo "truenas"'):
                self.nas_type = 'truenas'
            # Check for FreeBSD (could be TrueNAS or generic FreeBSD)
            elif self.run_command('uname -s | grep -i freebsd && echo "freebsd"'):
                # Try to determine if it's TrueNAS specifically
                if self.run_command('ps aux | grep -i truenas | grep -v grep'):
                    self.nas_type = 'truenas'
                else:
                    self.nas_type = 'freebsd_nas'
            # Check for other common NAS indicators
            elif self.run_command('ls /volume* 2>/dev/null | head -1'):
                self.nas_type = 'generic_nas'
            else:
                self.nas_type = 'unknown'
                
        return self.nas_type if self.nas_type != 'unknown' else None
    
    def collect_nas_info(self) -> Dict[str, Any]:
        """Get comprehensive NAS information"""
        nas_type = self.detect_nas_type()
        if not nas_type:
            return {}
            
        nas_info: Dict[str, Any] = {
            'nas_type': nas_type,
            'model_info': self.get_model_info(),
            'volumes': self.get_volume_info(),
            'shares': self.get_share_info(),
            'storage_pools': self.get_storage_pools(),
            'disk_health': self.get_disk_health(),
            'network_interfaces': self.get_network_interfaces(),
            'installed_packages': self.get_installed_packages(),
            'system_status': self.get_system_status()
        }
        
        return nas_info
    
    def get_model_info(self) -> Dict[str, str]:
        """Get NAS model and firmware information"""
        model_info = {}
        
        if self.nas_type == 'synology':
            model_info.update(self._get_synology_model())
        elif self.nas_type == 'qnap':
            model_info.update(self._get_qnap_model())
        elif self.nas_type == 'asustor':
            model_info.update(self._get_asustor_model())
        elif self.nas_type == 'truenas':
            model_info.update(self._get_truenas_model())
        else:
            model_info.update(self._get_generic_model())
            
        return model_info
    
    def _get_synology_model(self) -> Dict[str, str]:
        """Get Synology-specific model information"""
        info = {}
        
        # Model name
        model = self.run_command('cat /proc/sys/kernel/syno_hw_version 2>/dev/null')
        if model:
            info['model'] = model.strip()
            
        # DSM version
        version = self.run_command('cat /etc.defaults/VERSION | grep productversion | cut -d"=" -f2 | tr -d \'"\' 2>/dev/null')
        if version:
            info['firmware_version'] = f"DSM {version.strip()}"
            
        # Build number
        build = self.run_command('cat /etc.defaults/VERSION | grep buildnumber | cut -d"=" -f2 | tr -d \'"\' 2>/dev/null')
        if build:
            info['build_number'] = build.strip()
            
        return info
    
    def _get_qnap_model(self) -> Dict[str, str]:
        """Get QNAP-specific model information"""
        info = {}
        
        # Model name
        model = self.run_command('cat /etc/platform.conf | grep PLATFORM | cut -d"=" -f2 2>/dev/null')
        if model:
            info['model'] = model.strip()
            
        # QTS version
        version = self.run_command('cat /etc/version 2>/dev/null')
        if version:
            info['firmware_version'] = f"QTS {version.strip()}"
            
        return info
    
    def _get_asustor_model(self) -> Dict[str, str]:
        """Get Asustor-specific model information"""
        info = {}
        
        # Model name from hostname or other sources
        model = self.run_command('hostname 2>/dev/null')
        if model:
            info['model'] = model.strip()
            
        return info
    
    def _get_truenas_model(self) -> Dict[str, str]:
        """Get TrueNAS-specific model information"""
        info = {}
        
        # Try to get TrueNAS version
        version = self.run_command('cat /etc/version 2>/dev/null')
        if version:
            info['firmware_version'] = version.strip()
        
        # Try alternative version detection
        if not version:
            version = self.run_command('freenas-debug -V 2>/dev/null | head -1')
            if version:
                info['firmware_version'] = version.strip()
        
        # Get FreeBSD version
        freebsd_version = self.run_command('uname -r 2>/dev/null')
        if freebsd_version:
            info['base_os'] = f"FreeBSD {freebsd_version.strip()}"
        
        # Try to get model from DMI
        model = self.run_command('dmidecode -s system-product-name 2>/dev/null')
        if model and model.strip() and model.strip() not in ['Unknown', 'To be filled by O.E.M.']:
            info['model'] = model.strip()
        
        # Fallback to hostname
        if not info.get('model'):
            hostname = self.run_command('hostname 2>/dev/null')
            if hostname:
                info['model'] = hostname.strip()
        
        return info
    
    def _get_generic_model(self) -> Dict[str, str]:
        """Get generic NAS model information"""
        info = {}
        
        # Try common methods
        hostname = self.run_command('hostname 2>/dev/null')
        if hostname:
            info['hostname'] = hostname.strip()
            
        return info
    
    def get_volume_info(self) -> List[Dict[str, str]]:
        """Get volume/storage information"""
        volumes = []
        
        if self.nas_type == 'synology':
            volumes = self._get_synology_volumes()
        elif self.nas_type == 'qnap':
            volumes = self._get_qnap_volumes()
        elif self.nas_type == 'truenas':
            volumes = self._get_truenas_volumes()
        else:
            volumes = self._get_generic_volumes()
            
        return volumes
    
    def _get_synology_volumes(self) -> List[Dict[str, str]]:
        """Get Synology volume information"""
        volumes = []
        
        # Use df for basic volume info
        df_output = self.run_command('df -h | grep "/volume"')
        if df_output:
            for line in df_output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 6:
                        volumes.append({
                            'device': parts[0],
                            'mount_point': parts[5],
                            'size': parts[1],
                            'used': parts[2],
                            'available': parts[3],
                            'usage_percent': parts[4],
                            'type': 'volume'
                        })
        
        return volumes
    
    def _get_qnap_volumes(self) -> List[Dict[str, str]]:
        """Get QNAP volume information"""
        volumes = []
        
        # QNAP uses /share directories
        df_output = self.run_command('df -h | grep -E "/share|/mnt"')
        if df_output:
            for line in df_output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 6:
                        volumes.append({
                            'device': parts[0],
                            'mount_point': parts[5],
                            'size': parts[1],
                            'used': parts[2],
                            'available': parts[3],
                            'usage_percent': parts[4],
                            'type': 'share'
                        })
        
        return volumes
    
    def _get_truenas_volumes(self) -> List[Dict[str, str]]:
        """Get TrueNAS volume information"""
        volumes = []
        
        # TrueNAS uses ZFS pools
        zfs_output = self.run_command('zfs list -H -o name,used,avail,refer,mountpoint 2>/dev/null')
        if zfs_output:
            for line in zfs_output.split('\n'):
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 5:
                        # Skip child datasets, focus on main pools/datasets
                        name = parts[0]
                        if '/' not in name or name.count('/') <= 1:
                            volumes.append({
                                'device': name,
                                'mount_point': parts[4] if parts[4] != '-' else f'/mnt/{name}',
                                'used': parts[1],
                                'available': parts[2],
                                'size': 'Unknown',  # ZFS doesn't report total size this way
                                'usage_percent': 'Unknown',
                                'type': 'zfs_dataset'
                            })
        
        # Also check traditional mount points
        df_output = self.run_command('df -h | grep -E "/mnt|/tank"')
        if df_output:
            for line in df_output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 6:
                        volumes.append({
                            'device': parts[0],
                            'mount_point': parts[5],
                            'size': parts[1],
                            'used': parts[2],
                            'available': parts[3],
                            'usage_percent': parts[4],
                            'type': 'filesystem'
                        })
        
        return volumes
    
    def _get_generic_volumes(self) -> List[Dict[str, str]]:
        """Get generic volume information"""
        volumes = []
        
        # Generic df output filtering for large filesystems
        df_output = self.run_command('df -h | awk \'$2 ~ /[0-9]+G/ && $6 !~ /^\\/proc|^\\/sys|^\\/dev\\/pts/\'')
        if df_output:
            for line in df_output.split('\n'):
                if line.strip() and not line.startswith('Filesystem'):
                    parts = line.split()
                    if len(parts) >= 6:
                        volumes.append({
                            'device': parts[0],
                            'mount_point': parts[5],
                            'size': parts[1],
                            'used': parts[2],
                            'available': parts[3],
                            'usage_percent': parts[4],
                            'type': 'filesystem'
                        })
        
        return volumes
    
    def get_share_info(self) -> List[Dict[str, str]]:
        """Get SMB/NFS share information"""
        shares = []
        
        # Check for SMB shares
        smb_shares = self.run_command('testparm -s 2>/dev/null | grep "\\[" | grep -v "\\[global\\]" | tr -d "[]"')
        if smb_shares:
            for share in smb_shares.split('\n'):
                if share.strip():
                    shares.append({
                        'name': share.strip(),
                        'type': 'SMB',
                        'protocol': 'CIFS/SMB'
                    })
        
        # Check for NFS exports
        nfs_exports = self.run_command('cat /etc/exports 2>/dev/null | grep -v "^#" | awk \'{print $1}\'')
        if nfs_exports:
            for export in nfs_exports.split('\n'):
                if export.strip():
                    shares.append({
                        'name': export.strip(),
                        'type': 'NFS',
                        'protocol': 'NFS'
                    })
        
        # TrueNAS specific sharing
        if self.nas_type == 'truenas':
            # Check for TrueNAS SMB shares via CLI
            truenas_smb = self.run_command('midclt call sharing.smb.query 2>/dev/null | grep -o \'"name": "[^"]*"\' | cut -d\'"\' -f4')
            if truenas_smb:
                for share in truenas_smb.split('\n'):
                    if share.strip():
                        shares.append({
                            'name': share.strip(),
                            'type': 'SMB',
                            'protocol': 'TrueNAS SMB'
                        })
        
        return shares
    
    def get_storage_pools(self) -> List[Dict[str, str]]:
        """Get storage pool information (RAID arrays, ZFS pools)"""
        pools = []
        
        # TrueNAS ZFS pools
        if self.nas_type == 'truenas':
            zpool_output = self.run_command('zpool status 2>/dev/null | grep "pool:" | awk \'{print $2}\'')
            if zpool_output:
                for pool_name in zpool_output.split('\n'):
                    if pool_name.strip():
                        # Get pool health
                        pool_health = self.run_command(f'zpool status {pool_name.strip()} 2>/dev/null | grep "state:" | awk \'{{print $2}}\'')
                        pools.append({
                            'name': pool_name.strip(),
                            'type': 'zfs_pool',
                            'state': pool_health.strip() if pool_health else 'unknown'
                        })
        
        # Check for mdadm RAID arrays
        raid_status = self.run_command('cat /proc/mdstat 2>/dev/null | grep "^md"')
        if raid_status:
            for line in raid_status.split('\n'):
                if line.strip() and line.startswith('md'):
                    parts = line.split()
                    if len(parts) >= 4:
                        pools.append({
                            'name': parts[0],
                            'level': parts[3],
                            'state': 'active' if 'active' in line else 'unknown',
                            'type': 'mdadm_raid'
                        })
        
        # Check for LVM volume groups
        vg_info = self.run_command('vgdisplay 2>/dev/null | grep "VG Name"')
        if vg_info:
            for line in vg_info.split('\n'):
                if 'VG Name' in line:
                    vg_name = line.split(':')[1].strip()
                    pools.append({
                        'name': vg_name,
                        'type': 'lvm_volume_group',
                        'state': 'active'
                    })
        
        return pools
    
    def get_disk_health(self) -> List[Dict[str, str]]:
        """Get disk health information"""
        disks = []
        
        # Different commands for different platforms
        if self.nas_type == 'truenas':
            # FreeBSD disk listing
            disk_list = self.run_command('geom disk status 2>/dev/null | grep -v "Geom" | awk \'{print $1, $3}\'')
        else:
            # Linux disk listing
            disk_list = self.run_command('lsblk -d -o NAME,SIZE,MODEL | grep -v "NAME"')
        
        if disk_list:
            for line in disk_list.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        disk_name = parts[0]
                        
                        # Try to get SMART status
                        smart_status = self.run_command(f'smartctl -H /dev/{disk_name} 2>/dev/null | grep "SMART overall-health"')
                        health = 'Unknown'
                        if smart_status:
                            if 'PASSED' in smart_status:
                                health = 'PASSED'
                            elif 'FAILED' in smart_status:
                                health = 'FAILED'
                        
                        disks.append({
                            'device': disk_name,
                            'size': parts[1] if len(parts) > 1 else 'Unknown',
                            'model': ' '.join(parts[2:]) if len(parts) > 2 else 'Unknown',
                            'health': health
                        })
        
        return disks[:8]  # Limit for display
    
    def get_network_interfaces(self) -> List[Dict[str, str]]:
        """Get network interface information"""
        interfaces = []
        
        # Handle both Linux and FreeBSD interface listing
        if self.nas_type == 'truenas':
            # FreeBSD interface listing
            ifconfig_output = self.run_command('ifconfig | grep "^[a-z]" | grep "flags=" | awk \'{print $1}\' | tr -d ":"')
        else:
            # Linux interface listing
            ifconfig_output = self.run_command('ip addr show | grep "state UP" | awk \'{print $2}\' | tr -d ":"')
        
        if ifconfig_output:
            for iface_name in ifconfig_output.split('\n'):
                if iface_name.strip():
                    # Get IP address for interface
                    if self.nas_type == 'truenas':
                        ip_addr = self.run_command(f'ifconfig {iface_name.strip()} | grep "inet " | awk \'{{print $2}}\'')
                    else:
                        ip_addr = self.run_command(f'ip addr show {iface_name.strip()} | grep "inet " | awk \'{{print $2}}\'')
                    
                    interfaces.append({
                        'name': iface_name.strip(),
                        'state': 'UP',
                        'ip_address': ip_addr.strip() if ip_addr else 'No IP'
                    })
        
        return interfaces
    
    def get_installed_packages(self) -> List[str]:
        """Get installed packages/applications"""
        packages = []
        
        if self.nas_type == 'synology':
            # Check for Synology packages
            pkg_output = self.run_command('synopkg list 2>/dev/null')
            if pkg_output:
                packages = [line.strip() for line in pkg_output.split('\n') if line.strip()]
        elif self.nas_type == 'qnap':
            # Check for QNAP applications
            app_output = self.run_command('ls /share/CACHEDEV*_DATA/.qpkg/ 2>/dev/null')
            if app_output:
                packages = [app.strip() for app in app_output.split('\n') if app.strip()]
        elif self.nas_type == 'truenas':
            # TrueNAS plugins/jails
            jail_output = self.run_command('jls name 2>/dev/null | grep -v "NAME"')
            if jail_output:
                packages.extend([f"Jail: {jail.strip()}" for jail in jail_output.split('\n') if jail.strip()])
            
            # FreeBSD packages
            pkg_output = self.run_command('pkg info 2>/dev/null | head -20 | awk \'{print $1}\'')
            if pkg_output:
                packages.extend([pkg.strip() for pkg in pkg_output.split('\n') if pkg.strip()])
        else:
            # Generic package detection
            if self.run_command('which dpkg 2>/dev/null'):
                pkg_output = self.run_command('dpkg -l | grep "^ii" | head -20')
                if pkg_output:
                    for line in pkg_output.split('\n'):
                        if line.strip():
                            parts = line.split()
                            if len(parts) >= 2:
                                packages.append(parts[1])
            elif self.run_command('which rpm 2>/dev/null'):
                pkg_output = self.run_command('rpm -qa | head -20')
                if pkg_output:
                    packages = [pkg.strip() for pkg in pkg_output.split('\n') if pkg.strip()]
        
        return packages[:10]  # Limit for display
    
    def get_system_status(self) -> Dict[str, str]:
        """Get overall system status"""
        status = {}
        
        # Uptime
        uptime = self.run_command('uptime -p 2>/dev/null || uptime')
        if uptime:
            status['uptime'] = uptime.strip()
        
        # Load average
        load_avg = self.run_command('uptime | awk -F"load average:" \'{print $2}\'')
        if load_avg:
            status['load_average'] = load_avg.strip()
        
        # Temperature (if available)
        temp = self.run_command('cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null')
        if temp and temp.strip().isdigit():
            temp_celsius = int(temp.strip()) / 1000
            status['temperature'] = f"{temp_celsius:.1f}°C"
        
        return status

