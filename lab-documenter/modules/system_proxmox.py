"""
Proxmox information collection for Lab Documenter

Handles Proxmox Virtual Environment cluster information gathering.
"""

import json
import logging
from typing import Dict, List, Callable, Optional
from modules.utils import bytes_to_gb, convert_uptime_seconds

logger = logging.getLogger(__name__)

class ProxmoxCollector:
    def __init__(self, command_runner: Callable[[str], Optional[str]]):
        """
        Initialize the Proxmox collector.
        
        Args:
            command_runner: Function that executes commands and returns output
        """
        self.run_command = command_runner
    
    def collect_proxmox_info(self) -> Dict:
        """Get comprehensive Proxmox information if available"""
        proxmox_info = {}
        
        # Check if Proxmox is installed
        pve_version = self.run_command('pveversion 2>/dev/null')
        if not pve_version:
            return proxmox_info
        
        proxmox_info['pve_version'] = pve_version.strip()
        
        # Collect cluster information
        cluster_info = self.collect_cluster_info()
        if cluster_info:
            proxmox_info.update(cluster_info)
        
        # Collect resource summaries
        cluster_resources = self.collect_cluster_resources()
        if cluster_resources:
            proxmox_info['cluster_resources'] = cluster_resources
        
        # Collect VM information
        vms = self.collect_vms()
        if vms:
            proxmox_info['vms'] = vms
        
        # Collect container information
        containers = self.collect_containers()
        if containers:
            proxmox_info['containers'] = containers
        
        # Collect storage information
        storage = self.collect_storage()
        if storage:
            proxmox_info['storage'] = storage
        
        # Identify problematic resources
        problematic_resources = self.identify_problematic_resources(
            proxmox_info.get('vms', []), 
            proxmox_info.get('containers', [])
        )
        if problematic_resources:
            proxmox_info['problematic_resources'] = problematic_resources
        
        return proxmox_info
    
    def collect_cluster_info(self) -> Dict:
        """Collect Proxmox cluster information"""
        cluster_info = {}
        
        # Try JSON API first for more reliable parsing
        cluster_json = self.run_command('pvesh get /cluster/status --output-format=json 2>/dev/null')
        if cluster_json:
            try:
                cluster_data = json.loads(cluster_json)
                cluster_info['cluster_status'] = self.parse_cluster_json(cluster_data)
                cluster_info['nodes'] = self.parse_nodes_json(cluster_data)
                
                # JSON doesn't include transport info, so get it from text output
                cluster_status = self.run_command('pvecm status 2>/dev/null')
                if cluster_status and 'Transport:' in cluster_status:
                    for line in cluster_status.split('\n'):
                        if 'Transport:' in line:
                            cluster_info['cluster_status']['transport'] = line.split(':', 1)[1].strip()
                            break
                            
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to parse JSON cluster status: {e}")
                # Fall back to text parsing
                cluster_info = self.collect_cluster_info_text()
        else:
            # Fallback to text parsing if JSON not available
            cluster_info = self.collect_cluster_info_text()
        
        return cluster_info
    
    def collect_cluster_info_text(self) -> Dict:
        """Collect cluster info using text parsing as fallback"""
        cluster_info = {}
        
        cluster_status = self.run_command('pvecm status 2>/dev/null')
        if cluster_status and 'Cluster information' in cluster_status:
            cluster_info['cluster_status'] = self.parse_cluster_status(cluster_status)
        else:
            cluster_info['cluster_status'] = {'clustered': False, 'nodes': 1}
        
        # Get node information
        if cluster_info['cluster_status']['clustered']:
            nodes_output = self.run_command('pvecm nodes 2>/dev/null')
            if nodes_output:
                cluster_info['nodes'] = self.parse_cluster_nodes(nodes_output)
        else:
            # Single node setup
            hostname = self.run_command('hostname')
            node_status = self.run_command('pvesh get /nodes/$(hostname)/status --output-format=json 2>/dev/null')
            if node_status:
                try:
                    node_data = json.loads(node_status)
                    cluster_info['nodes'] = [{
                        'name': hostname or 'localhost',
                        'status': 'online',
                        'online': True,
                        'cpu_usage': f"{node_data.get('cpu', 0)*100:.1f}%",
                        'memory_usage': f"{node_data.get('memory', {}).get('used', 0) / (1024*1024*1024):.1f}G / {node_data.get('memory', {}).get('total', 0) / (1024*1024*1024):.1f}G",
                        'uptime': node_data.get('uptime', 'Unknown')
                    }]
                except:
                    cluster_info['nodes'] = [{'name': hostname or 'localhost', 'status': 'online', 'online': True}]
        
        return cluster_info
    
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
                uptime_readable = convert_uptime_seconds(uptime_seconds)
                
                return {
                    'cpu_usage': cpu_percentage,
                    'memory_total': bytes_to_gb(str(status_data.get('memory', {}).get('total', 0))),
                    'memory_used': bytes_to_gb(str(status_data.get('memory', {}).get('used', 0))),
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
    
    def collect_cluster_resources(self) -> Dict:
        """Parse cluster resources summary"""
        cluster_resources = self.run_command('pvesh get /cluster/resources --output-format=json 2>/dev/null')
        if not cluster_resources:
            return {}
        
        try:
            resources_data = json.loads(cluster_resources)
            return self.parse_cluster_resources(resources_data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse cluster resources: {e}")
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
            summary['memory_usage']['total_gb'] = bytes_to_gb(str(int(summary['memory_usage']['total'])))
            summary['memory_usage']['used_gb'] = bytes_to_gb(str(int(summary['memory_usage']['used'])))
            summary['memory_usage']['percentage'] = f"{(summary['memory_usage']['used'] / summary['memory_usage']['total']) * 100:.1f}%"
        
        return summary
    
    def collect_vms(self) -> List[Dict]:
        """Collect Virtual Machine information"""
        vms_output = self.run_command('qm list 2>/dev/null')
        if not vms_output:
            return []
        
        return self.parse_vm_list(vms_output)

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
                uptime_readable = convert_uptime_seconds(uptime_seconds)
                
                detailed_info.update({
                    'uptime': uptime_readable,
                    'uptime_seconds': uptime_seconds,
                    'cpu_usage': cpu_percentage,
                    'memory_current': bytes_to_gb(str(status_data.get('mem', 0))),
                    'pid': status_data.get('pid', 'Unknown'),
                    'ha_state': status_data.get('ha', {}),
                })
                
                # Get network I/O if available
                if 'netin' in status_data and 'netout' in status_data:
                    detailed_info['network_io'] = {
                        'bytes_in': bytes_to_gb(str(status_data.get('netin', 0))),
                        'bytes_out': bytes_to_gb(str(status_data.get('netout', 0)))
                    }
                    
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to get detailed VM info for {vmid} on {node_name}: {e}")
            
        return detailed_info
    
    def collect_containers(self) -> List[Dict]:
        """Collect LXC container information"""
        containers_output = self.run_command('pct list 2>/dev/null')
        if not containers_output:
            return []
        
        return self.parse_container_list(containers_output)

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
                uptime_readable = convert_uptime_seconds(uptime_seconds)
                
                detailed_info.update({
                    'uptime': uptime_readable,
                    'uptime_seconds': uptime_seconds,
                    'cpu_usage': cpu_percentage,
                    'memory_current': bytes_to_gb(str(status_data.get('mem', 0))),
                    'swap_current': bytes_to_gb(str(status_data.get('swap', 0))),
                    'disk_usage': bytes_to_gb(str(status_data.get('disk', 0))),
                    'pid': status_data.get('pid', 'Unknown')
                })
                    
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Failed to get detailed container info for {vmid} on {node_name}: {e}")
            
        return detailed_info
    
    def collect_storage(self) -> List[Dict]:
        """Collect storage information"""
        storage_output = self.run_command('pvesm status 2>/dev/null')
        if not storage_output:
            return []
        
        return self.parse_storage_status(storage_output)

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
                        'total': bytes_to_gb(parts[3]),
                        'used': bytes_to_gb(parts[4]),
                        'available': bytes_to_gb(parts[5]),
                        'usage_percent': parts[6] if len(parts) > 6 else 'Unknown'
                    }
                    storage.append(storage_info)
        
        return storage
    
    def identify_problematic_resources(self, vms: List[Dict], containers: List[Dict]) -> List[Dict]:
        """Identify VMs and containers that have issues"""
        problematic_resources = []
        
        # Check VMs
        for vm in vms:
            # Only consider truly problematic states, not stopped VMs
            if (vm.get('status') not in ['running', 'stopped'] or vm.get('lock')):
                # Don't include stopped VMs unless they have other issues
                if vm.get('status') != 'stopped' or vm.get('lock'):
                    problematic_resources.append(vm)
        
        # Check containers
        for ct in containers:
            # Only consider truly problematic states, not stopped containers
            if (ct.get('status') not in ['running', 'stopped'] or ct.get('lock')):
                # Don't include stopped containers unless they have other issues
                if ct.get('status') != 'stopped' or ct.get('lock'):
                    problematic_resources.append(ct)
        
        return problematic_resources
    
    def get_cluster_health_summary(self) -> Dict:
        """Get a high-level health summary of the Proxmox cluster"""
        health_summary = {
            'cluster_healthy': True,
            'total_nodes': 0,
            'online_nodes': 0,
            'total_vms': 0,
            'running_vms': 0,
            'total_containers': 0,
            'running_containers': 0,
            'problematic_resources': 0,
            'storage_pools': 0
        }
        
        # Get cluster info
        cluster_info = self.collect_cluster_info()
        cluster_status = cluster_info.get('cluster_status', {})
        nodes = cluster_info.get('nodes', [])
        
        # Check cluster health
        if cluster_status.get('clustered') and not cluster_status.get('quorate', True):
            health_summary['cluster_healthy'] = False
        
        # Count nodes
        health_summary['total_nodes'] = len(nodes)
        health_summary['online_nodes'] = len([n for n in nodes if n.get('online', False)])
        
        # Count VMs and containers
        vms = self.collect_vms()
        containers = self.collect_containers()
        
        health_summary['total_vms'] = len(vms)
        health_summary['running_vms'] = len([vm for vm in vms if vm.get('status') == 'running'])
        
        health_summary['total_containers'] = len(containers)
        health_summary['running_containers'] = len([ct for ct in containers if ct.get('status') == 'running'])
        
        # Count problematic resources
        problematic = self.identify_problematic_resources(vms, containers)
        health_summary['problematic_resources'] = len(problematic)
        
        # Count storage pools
        storage = self.collect_storage()
        health_summary['storage_pools'] = len(storage)
        
        return health_summary

