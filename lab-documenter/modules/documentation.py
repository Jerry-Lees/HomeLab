"""
Documentation generation for Lab Documenter

Handles Markdown and MediaWiki content generation and file management.
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

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
- **IP Addresses:**
```
{host_data.get('ip_addresses', 'Unknown')}
```

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
                service_line += f" - *auto-discovered*"
            
            if description and description != 'Unknown service':
                if service.get('_auto_generated'):
                    service_line += f" - Please update service information"
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
    
    if host_data.get('docker_containers'):
        content += "\n## Docker Containers\n"
        for container in host_data['docker_containers']:
            content += f"- **Name:** {container['name']}, **Image:** {container['image']}, **Status:** {container['status']}\n"
    
    if host_data.get('kubernetes_info'):
        k8s = host_data['kubernetes_info']
        content += "\n## Kubernetes\n"
        
        if 'kubectl_version' in k8s:
            content += f"- **Version:** {k8s['kubectl_version']}\n"
        
        if 'nodes' in k8s:
            content += f"\n### Nodes ({len(k8s['nodes'])})\n"
            for node in k8s['nodes']:
                status_indicator = "Ready" if node['status'] == 'Ready' else "NotReady"
                content += f"- **{node['name']}** ({node['roles']}) - {status_indicator}\n"
        
        if 'pods' in k8s:
            running_pods = len([p for p in k8s['pods'] if p['status'] == 'Running'])
            total_pods = len(k8s['pods'])
            content += f"\n### Pods Summary\n"
            content += f"- **Total Pods:** {total_pods}\n"
            content += f"- **Running:** {running_pods}\n"
            
            if k8s.get('problematic_pods'):
                content += f"- **Problematic Pods:** {len(k8s['problematic_pods'])}\n"
                content += f"\n#### Issues Found\n"
                for pod in k8s['problematic_pods'][:5]:  # Show first 5 problematic pods
                    content += f"- **{pod['namespace']}/{pod['name']}** - Status: {pod['status']}, Ready: {pod['ready']}, Restarts: {pod['restarts']}\n"
        
        if 'services' in k8s and k8s['services']:
            content += f"\n### Services ({len(k8s['services'])})\n"
            for svc in k8s['services'][:10]:  # Limit to first 10
                external_info = f" (External: {svc['external_ip']})" if svc.get('external_ip') else ""
                content += f"- **{svc['namespace']}/{svc['name']}** ({svc['type']}) - {svc['cluster_ip']}{external_info}\n"
    
    if host_data.get('proxmox_info'):
        proxmox = host_data['proxmox_info']
        content += "\n## Proxmox\n"
        
        if 'version' in proxmox:
            content += f"- **Version:** {proxmox['version']}\n"
        
        if 'vms' in proxmox:
            content += f"\n### Virtual Machines\n"
            for vm in proxmox['vms']:
                content += f"- {vm}\n"
        
        if 'containers' in proxmox:
            content += f"\n### Containers\n"
            for container in proxmox['containers']:
                content += f"- {container}\n"
    
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

