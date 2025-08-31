"""
Documentation generation for Lab Documenter

Handles Markdown and MediaWiki content generation with collapsible sections.

CONTENT SYNCHRONIZATION REQUIREMENT:
Both generate_wiki_content() and generate_mediawiki_content() must maintain 
feature parity and similar information depth. When adding new sections or 
features to one function, always update the other function as well.

Required sections in both outputs:
- System Information (including BIOS when available)
- Resources (with structured memory details)
- Network (IP addresses)
- Services (with collapsible sections for large lists)
- Listening Ports (with service information)
- Docker Containers (when present)
- Kubernetes (comprehensive: namespaces, nodes, pods by namespace, services, deployments)
- Proxmox (when present)

The only differences should be syntax formatting:
- Markdown uses HTML <details> tags for collapsible sections
- MediaWiki uses {| wikitable mw-collapsible |} syntax for collapsible sections
- Content depth and information should be identical
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

def generate_wiki_content(host_data: Dict) -> str:
    """Generate Markdown content for a host"""
    os_info = host_data.get('os_release', {})
    
    # Use actual hostname if available, otherwise fall back to original
    display_hostname = host_data.get('actual_hostname', host_data.get('hostname', 'Unknown Host'))
    
    # Build content using consistent string concatenation
    content = f"# {display_hostname}\n\n"
    content += f"**Last Updated:** {host_data.get('timestamp', 'Unknown')}\n\n"
    
    # System Information
    content += "## System Information\n"
    content += f"- **OS:** {os_info.get('pretty_name', os_info.get('name', 'Unknown'))}\n"
    content += f"- **Version:** {os_info.get('version', 'Unknown')}\n"
    content += f"- **Distribution:** {os_info.get('id', 'unknown').title()}\n"
    content += f"- **Kernel:** {host_data.get('kernel', 'Unknown')}\n"
    content += f"- **Architecture:** {host_data.get('architecture', 'Unknown')}\n"
    content += f"- **Uptime:** {host_data.get('uptime', 'Unknown')}\n"
    content += f"- **CPU:** {host_data.get('cpu_info', 'Unknown')} ({host_data.get('cpu_cores', 'Unknown')} cores)\n\n"

    # Add BIOS information if available
    if host_data.get('bios_info'):
        bios = host_data['bios_info']
        content += "### BIOS Information\n"
        if bios.get('vendor'):
            content += f"- **Vendor:** {bios['vendor']}\n"
        if bios.get('version'):
            content += f"- **Version:** {bios['version']}\n"
        if bios.get('date'):
            content += f"- **Date:** {bios['date']}\n"
        if bios.get('capabilities'):
            content += f"- **Capabilities:** {bios['capabilities']}\n"
        content += "\n"
    
    # Resources
    content += "## Resources\n"
    content += f"- **Memory:** {host_data.get('memory_used', 'Unknown')} / {host_data.get('memory_total', 'Unknown')}\n\n"
    
    # Handle structured memory module information
    memory_data = host_data.get('memory_modules', {})
    
    if memory_data and isinstance(memory_data, dict):
        memory_banks = memory_data.get('memory_banks', [])
        
        if memory_banks:
            # Separate populated and empty slots
            populated_slots = [bank for bank in memory_banks if not bank.get('empty') and bank.get('size')]
            empty_slots = [bank for bank in memory_banks if bank.get('empty') or not bank.get('size')]
            
            if populated_slots:
                content += f"<details>\n<summary><strong>Installed Memory Modules ({len(populated_slots)} slots populated)</strong></summary>\n\n"
                for bank in populated_slots:
                    slot = bank.get('slot', 'Unknown slot')
                    size = bank.get('size', 'Unknown size')
                    speed = bank.get('clock', 'Unknown speed')
                    desc = bank.get('description', '')
                    
                    # Extract memory type from description
                    mem_type = 'Unknown type'
                    if 'DDR4' in desc:
                        mem_type = 'DDR4'
                    elif 'DDR3' in desc:
                        mem_type = 'DDR3'
                    elif 'DDR5' in desc:
                        mem_type = 'DDR5'
                    
                    content += f"- **{slot}**: {size} {mem_type}"
                    if speed and speed != 'Unknown speed':
                        content += f" @ {speed}"
                    content += "\n"
                content += "\n</details>\n\n"
            
            if empty_slots:
                content += f"<details>\n<summary><strong>Show {len(empty_slots)} empty memory slots</strong></summary>\n\n"
                for bank in empty_slots:
                    slot = bank.get('slot', 'Unknown slot')
                    width = bank.get('width', '')
                    content += f"- **{slot}**: Empty"
                    if width:
                        content += f" ({width})"
                    content += "\n"
                content += "\n</details>\n\n"
                
        # Show system memory info if available
        sys_mem = memory_data.get('system_memory', {})
        if sys_mem:
            if sys_mem.get('capabilities'):
                content += f"**Memory Features**: {sys_mem['capabilities']}\n"
            if sys_mem.get('configuration'):
                content += f"**Error Detection**: {sys_mem['configuration']}\n"
    
    elif isinstance(memory_data, str):
        # Fallback for old text format
        content += f"<details>\n<summary><strong>Memory Module Details</strong></summary>\n\n```\n{memory_data}\n```\n\n</details>\n\n"
    else:
        content += "*Memory module information not available*\n\n"
    
    content += f"- **Disk Usage:** {host_data.get('disk_usage', 'Unknown')}\n"
    content += f"- **Load Average:** {host_data.get('load_average', 'Unknown')}\n\n"

    # Network section with IP addresses
    content += "## Network\n"
    content += "- **IP Addresses:**\n"
    content += "```\n"
    content += host_data.get('ip_addresses', 'Unknown')
    content += "\n```\n\n"

    # Services with collapsible section for large lists
    content += "## Services\n"
    if host_data.get('services'):
        services = host_data['services']
        
        if len(services) <= 5:
            # Show services directly for small lists
            for service in services:
                display_name = service.get('display_name', service.get('name', 'Unknown'))
                description = service.get('description', '')
                category = service.get('category', '')
                
                service_line = f"- **{display_name}** ({service.get('status', 'Unknown')})"
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
        else:
            # Use collapsible section for large lists
            content += f"<details>\n<summary><strong>All Services ({len(services)} total)</strong></summary>\n\n"
            
            for service in services:
                display_name = service.get('display_name', service.get('name', 'Unknown'))
                description = service.get('description', '')
                category = service.get('category', '')
                
                service_line = f"- **{display_name}** ({service.get('status', 'Unknown')})"
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
            
            content += "\n</details>\n"
    
    content += "\n"

    # Listening Ports with collapsible section for large lists
    if host_data.get('listening_ports'):
        ports = host_data['listening_ports']
        
        content += "## Listening Ports\n"
        
        if len(ports) <= 8:
            # Show ports directly for small lists
            for port in ports:
                port_line = f"- **{port.get('port', 'Unknown')}**"
                
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
        else:
            # Show first few ports, then use collapsible section for the rest
            ports_to_show_first = ports[:4]
            ports_remaining = ports[4:]
            
            # Show first few ports
            for port in ports_to_show_first:
                port_line = f"- **{port.get('port', 'Unknown')}**"
                
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
            
            # Add collapsible section for remaining ports
            content += f"\n<details>\n<summary><strong>Show {len(ports_remaining)} more ports (total: {len(ports)})</strong></summary>\n\n"
            
            for port in ports_remaining:
                port_line = f"- **{port.get('port', 'Unknown')}**"
                
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
            
            content += "\n</details>\n"
        
        content += "\n"

    # Docker Containers with collapsible section for large lists
    if host_data.get('docker_containers'):
        containers = host_data['docker_containers']
        
        content += "## Docker Containers\n"
        
        if len(containers) <= 5:
            # Show containers directly for small lists
            for container in containers:
                content += f"- **Name:** {container.get('name', 'Unknown')}, **Image:** {container.get('image', 'Unknown')}, **Status:** {container.get('status', 'Unknown')}\n"
        else:
            # Show first few containers, then use collapsible section
            containers_to_show_first = containers[:3]
            containers_remaining = containers[3:]
            
            # Show first few containers
            for container in containers_to_show_first:
                content += f"- **Name:** {container.get('name', 'Unknown')}, **Image:** {container.get('image', 'Unknown')}, **Status:** {container.get('status', 'Unknown')}\n"
            
            # Add collapsible section for remaining containers
            content += f"\n<details>\n<summary><strong>Show {len(containers_remaining)} more containers (total: {len(containers)})</strong></summary>\n\n"
            
            for container in containers_remaining:
                content += f"- **Name:** {container.get('name', 'Unknown')}, **Image:** {container.get('image', 'Unknown')}, **Status:** {container.get('status', 'Unknown')}\n"
            
            content += "\n</details>\n"
        
        content += "\n"

    # Kubernetes with smart truncation
    if host_data.get('kubernetes_info'):
        k8s = host_data['kubernetes_info']
        content += "## Kubernetes\n"
        
        if 'kubectl_version' in k8s:
            content += f"- **Version:** {k8s['kubectl_version']}\n"
        
        # Show namespaces
        if 'namespaces' in k8s and k8s['namespaces']:
            content += f"\n### Namespaces ({len(k8s['namespaces'])})\n"
            for namespace in sorted(k8s['namespaces']):
                content += f"- {namespace}\n"
        
        # Always show nodes (usually not too many)
        if 'nodes' in k8s:
            content += f"\n### Nodes ({len(k8s['nodes'])})\n"
            for node in k8s['nodes']:
                status_indicator = "Ready" if node.get('status') == 'Ready' else "NotReady"
                content += f"- **{node.get('name', 'Unknown')}** ({node.get('roles', 'Unknown')}) - {status_indicator}\n"
        
        # Pods summary with issues highlighted
        if 'pods' in k8s:
            running_pods = len([p for p in k8s['pods'] if p.get('status') == 'Running'])
            total_pods = len(k8s['pods'])
            content += f"\n### Pods Summary\n"
            content += f"- **Total Pods:** {total_pods}\n"
            content += f"- **Running:** {running_pods}\n\n"
            
            # Comprehensive pods list organized by namespace
            content += f"<details>\n<summary><strong>All Pods by Namespace ({total_pods} total)</strong></summary>\n\n"
            
            # Group pods by namespace
            pods_by_namespace = {}
            for pod in k8s['pods']:
                namespace = pod.get('namespace', 'Unknown')
                if namespace not in pods_by_namespace:
                    pods_by_namespace[namespace] = []
                pods_by_namespace[namespace].append(pod)
            
            # Display pods organized by namespace
            for namespace in sorted(pods_by_namespace.keys()):
                namespace_pods = sorted(pods_by_namespace[namespace], key=lambda p: p.get('name', ''))
                content += f"**{namespace}** ({len(namespace_pods)} pods):\n"
                
                for pod in namespace_pods:
                    status_icon = "✅" if pod.get('status') == 'Running' else "❌" if pod.get('status') in ['Failed', 'Error', 'CrashLoopBackOff'] else "⚠️"
                    restart_info = f"({pod.get('restarts', '0')} restarts)" if pod.get('restarts', '0') != '0' else ""
                    age_info = f"Age: {pod.get('age', 'Unknown')}" if pod.get('age', 'Unknown') != 'Unknown' else ""
                    
                    content += f"  - {status_icon} **{pod.get('name', 'Unknown')}** - {pod.get('status', 'Unknown')} - Ready: {pod.get('ready', 'Unknown')} {restart_info} {age_info}\n"
                content += "\n"
            
            content += "</details>\n\n"
            
            # Always show problematic pods
            if k8s.get('problematic_pods'):
                problem_count = len(k8s['problematic_pods'])
                content += f"- **Problematic Pods:** {problem_count}\n"
                content += f"\n#### Issues Found\n"
                for pod in k8s['problematic_pods'][:8]:  # Show up to 8 problem pods
                    content += f"- **{pod.get('namespace', 'Unknown')}/{pod.get('name', 'Unknown')}** - Status: {pod.get('status', 'Unknown')}, Ready: {pod.get('ready', 'Unknown')}, Restarts: {pod.get('restarts', 'Unknown')}\n"
                if problem_count > 8:
                    content += f"\n*... and {problem_count - 8} more problematic pods*\n"
        
        # Services with collapsible section for large lists
        if 'services' in k8s and k8s['services']:
            service_count = len(k8s['services'])
            
            content += f"\n### Kubernetes Services ({service_count})\n"
            
            if service_count <= 8:
                # Show services directly for small lists
                for svc in k8s['services']:
                    external_info = f" (External: {svc.get('external_ip')})" if svc.get('external_ip') else ""
                    content += f"- **{svc.get('namespace', 'Unknown')}/{svc.get('name', 'Unknown')}** ({svc.get('type', 'Unknown')}) - {svc.get('cluster_ip', 'Unknown')}{external_info}\n"
            else:
                # Show first few services, then use collapsible section
                services_to_show_first = k8s['services'][:4]
                services_remaining = k8s['services'][4:]
                
                # Show first few services
                for svc in services_to_show_first:
                    external_info = f" (External: {svc.get('external_ip')})" if svc.get('external_ip') else ""
                    content += f"- **{svc.get('namespace', 'Unknown')}/{svc.get('name', 'Unknown')}** ({svc.get('type', 'Unknown')}) - {svc.get('cluster_ip', 'Unknown')}{external_info}\n"
                
                # Add collapsible section for remaining services
                content += f"\n<details>\n<summary><strong>Show {len(services_remaining)} more Kubernetes services</strong></summary>\n\n"
                
                for svc in services_remaining:
                    external_info = f" (External: {svc.get('external_ip')})" if svc.get('external_ip') else ""
                    content += f"- **{svc.get('namespace', 'Unknown')}/{svc.get('name', 'Unknown')}** ({svc.get('type', 'Unknown')}) - {svc.get('cluster_ip', 'Unknown')}{external_info}\n"
                
                content += "\n</details>\n"
        
        # Deployments with collapsible section for large lists
        if 'deployments' in k8s and k8s['deployments']:
            deploy_count = len(k8s['deployments'])
            
            content += f"\n### Deployments ({deploy_count})\n"
            
            if deploy_count <= 8:
                # Show deployments directly for small lists
                for dep in k8s['deployments']:
                    content += f"- **{dep.get('namespace', 'Unknown')}/{dep.get('name', 'Unknown')}** - Ready: {dep.get('ready', 'Unknown')}, Up-to-date: {dep.get('up_to_date', 'Unknown')}, Available: {dep.get('available', 'Unknown')}\n"
            else:
                # Show first few deployments, then use collapsible section
                deployments_to_show_first = k8s['deployments'][:4]
                deployments_remaining = k8s['deployments'][4:]
                
                # Show first few deployments
                for dep in deployments_to_show_first:
                    content += f"- **{dep.get('namespace', 'Unknown')}/{dep.get('name', 'Unknown')}** - Ready: {dep.get('ready', 'Unknown')}, Up-to-date: {dep.get('up_to_date', 'Unknown')}, Available: {dep.get('available', 'Unknown')}\n"
                
                # Add collapsible section for remaining deployments
                content += f"\n<details>\n<summary><strong>Show {len(deployments_remaining)} more deployments</strong></summary>\n\n"
                
                for dep in deployments_remaining:
                    content += f"- **{dep.get('namespace', 'Unknown')}/{dep.get('name', 'Unknown')}** - Ready: {dep.get('ready', 'Unknown')}, Up-to-date: {dep.get('up_to_date', 'Unknown')}, Available: {dep.get('available', 'Unknown')}\n"
                
                content += "\n</details>\n"
        
        content += "\n"
    
    # Proxmox with truncation
    if host_data.get('proxmox_info'):
        proxmox = host_data['proxmox_info']
        content += "## Proxmox\n"
        
        if 'version' in proxmox:
            content += f"- **Version:** {proxmox['version']}\n"
        
        # VMs with collapsible section for large lists
        if 'vms' in proxmox:
            vm_count = len(proxmox['vms'])
            
            content += f"\n### Virtual Machines ({vm_count})\n"
            
            if vm_count <= 8:
                # Show VMs directly for small lists
                for vm in proxmox['vms']:
                    content += f"- {vm}\n"
            else:
                # Show first few VMs, then use collapsible section
                vms_to_show_first = proxmox['vms'][:4]
                vms_remaining = proxmox['vms'][4:]
                
                # Show first few VMs
                for vm in vms_to_show_first:
                    content += f"- {vm}\n"
                
                # Add collapsible section for remaining VMs
                content += f"\n<details>\n<summary><strong>Show {len(vms_remaining)} more VMs</strong></summary>\n\n"
                
                for vm in vms_remaining:
                    content += f"- {vm}\n"
                
                content += "\n</details>\n"
        
        # Containers with collapsible section for large lists
        if 'containers' in proxmox:
            container_count = len(proxmox['containers'])
            
            content += f"\n### Containers ({container_count})\n"
            
            if container_count <= 8:
                # Show containers directly for small lists
                for container in proxmox['containers']:
                    content += f"- {container}\n"
            else:
                # Show first few containers, then use collapsible section
                containers_to_show_first = proxmox['containers'][:4]
                containers_remaining = proxmox['containers'][4:]
                
                # Show first few containers
                for container in containers_to_show_first:
                    content += f"- {container}\n"
                
                # Add collapsible section for remaining containers
                content += f"\n<details>\n<summary><strong>Show {len(containers_remaining)} more containers</strong></summary>\n\n"
                
                for container in containers_remaining:
                    content += f"- {container}\n"
                
                content += "\n</details>\n"
        
        content += "\n"
    
    return content

def generate_mediawiki_content(host_data: Dict) -> str:
    """Generate MediaWiki-specific content for a host"""
    os_info = host_data.get('os_release', {})
    
    # Use actual hostname if available, otherwise fall back to original
    display_hostname = host_data.get('actual_hostname', host_data.get('hostname', 'Unknown Host'))
    
    content = f"= {display_hostname} =\n\n"
    content += f"'''Last Updated:''' {host_data.get('timestamp', 'Unknown')}\n\n"
    
    # System Information
    content += "== System Information ==\n"
    content += f"* '''OS:''' {os_info.get('pretty_name', os_info.get('name', 'Unknown'))}\n"
    content += f"* '''Version:''' {os_info.get('version', 'Unknown')}\n"
    content += f"* '''Distribution:''' {os_info.get('id', 'unknown').title()}\n"
    content += f"* '''Kernel:''' {host_data.get('kernel', 'Unknown')}\n"
    content += f"* '''Architecture:''' {host_data.get('architecture', 'Unknown')}\n"
    content += f"* '''Uptime:''' {host_data.get('uptime', 'Unknown')}\n"
    content += f"* '''CPU:''' {host_data.get('cpu_info', 'Unknown')} ({host_data.get('cpu_cores', 'Unknown')} cores)\n\n"

    # Add BIOS information if available
    if host_data.get('bios_info'):
        bios = host_data['bios_info']
        content += "=== BIOS Information ===\n"
        if bios.get('vendor'):
            content += f"* '''Vendor:''' {bios['vendor']}\n"
        if bios.get('version'):
            content += f"* '''Version:''' {bios['version']}\n"
        if bios.get('date'):
            content += f"* '''Date:''' {bios['date']}\n"
        if bios.get('capabilities'):
            content += f"* '''Capabilities:''' {bios['capabilities']}\n"
        content += "\n"
    
    # Resources
    content += "== Resources ==\n"
    content += f"* '''Memory:''' {host_data.get('memory_used', 'Unknown')} / {host_data.get('memory_total', 'Unknown')}\n\n"
    
    # Handle structured memory module information
    memory_data = host_data.get('memory_modules', {})
    
    if memory_data and isinstance(memory_data, dict):
        memory_banks = memory_data.get('memory_banks', [])
        
        if memory_banks:
            # Separate populated and empty slots
            populated_slots = [bank for bank in memory_banks if not bank.get('empty') and bank.get('size')]
            empty_slots = [bank for bank in memory_banks if bank.get('empty') or not bank.get('size')]
            
            if populated_slots:
                content += f"{{| class='wikitable mw-collapsible mw-collapsed'\n"
                content += f"|+ Installed Memory Modules ({len(populated_slots)} slots populated)\n"
                content += "|-\n"
                content += "! Slot !! Size !! Type !! Speed\n"
                
                for bank in populated_slots:
                    slot = bank.get('slot', 'Unknown slot')
                    size = bank.get('size', 'Unknown size')
                    speed = bank.get('clock', 'Unknown speed')
                    desc = bank.get('description', '')
                    
                    # Extract memory type from description
                    mem_type = 'Unknown type'
                    if 'DDR4' in desc:
                        mem_type = 'DDR4'
                    elif 'DDR3' in desc:
                        mem_type = 'DDR3'
                    elif 'DDR5' in desc:
                        mem_type = 'DDR5'
                    
                    content += "|-\n"
                    content += f"| {slot} || {size} || {mem_type} || {speed}\n"
                content += "|}\n\n"
            
            if empty_slots:
                content += f"{{| class='wikitable mw-collapsible mw-collapsed'\n"
                content += f"|+ Empty Memory Slots ({len(empty_slots)} slots)\n"
                content += "|-\n"
                content += "! Slot !! Width\n"
                
                for bank in empty_slots:
                    slot = bank.get('slot', 'Unknown slot')
                    width = bank.get('width', 'Unknown')
                    content += "|-\n"
                    content += f"| {slot} || {width}\n"
                content += "|}\n\n"
                
        # Show system memory info if available
        sys_mem = memory_data.get('system_memory', {})
        if sys_mem:
            if sys_mem.get('capabilities'):
                content += f"'''Memory Features:''' {sys_mem['capabilities']}\n\n"
            if sys_mem.get('configuration'):
                content += f"'''Error Detection:''' {sys_mem['configuration']}\n\n"
    
    elif isinstance(memory_data, str):
        # Fallback for old text format
        content += f"{{| class='wikitable mw-collapsible mw-collapsed'\n"
        content += "|+ Memory Module Details\n"
        content += "|-\n"
        content += f"| <pre>{memory_data}</pre>\n"
        content += "|}\n\n"
    else:
        content += "''Memory module information not available''\n\n"
    
    content += f"* '''Disk Usage:''' {host_data.get('disk_usage', 'Unknown')}\n"
    content += f"* '''Load Average:''' {host_data.get('load_average', 'Unknown')}\n\n"

    # Network section with IP addresses
    content += "== Network ==\n"
    content += "* '''IP Addresses:'''\n"
    content += f"<pre>\n{host_data.get('ip_addresses', 'Unknown')}\n</pre>\n\n"

    # Services
    content += "== Services ==\n"
    
    if host_data.get('services'):
        services = host_data['services']
        if len(services) <= 5:
            # Show services directly for small lists
            for service in services:
                display_name = service.get('display_name', service.get('name', 'Unknown'))
                description = service.get('description', '')
                category = service.get('category', '')
                
                service_line = f"* '''{display_name}''' ({service.get('status', 'Unknown')})"
                if category and category != 'unknown':
                    service_line += f" - ''{category}''"
                elif service.get('_auto_generated'):
                    service_line += f" - ''auto-discovered''"
                
                if description and description != 'Unknown service':
                    if service.get('_auto_generated'):
                        service_line += f" - Please update service information"
                    else:
                        service_line += f" - {description}"
                content += service_line + "\n"
        else:
            # Use MediaWiki collapsible table for large lists
            content += f"{{| class='wikitable mw-collapsible mw-collapsed'\n"
            content += f"|+ All Services ({len(services)} total)\n"
            content += "|-\n"
            content += "! Service Name !! Status !! Category !! Description\n"
            
            for service in services:
                display_name = service.get('display_name', service.get('name', 'Unknown'))
                description = service.get('description', '')[:60] + "..." if len(service.get('description', '')) > 60 else service.get('description', '')
                category = service.get('category', 'unknown')
                
                content += "|-\n"
                content += f"| {display_name} || {service.get('status', 'Unknown')} || {category} || {description}\n"
            content += "|}\n\n"
    
    content += "\n"

    # Listening Ports
    if host_data.get('listening_ports'):
        ports = host_data['listening_ports']
        
        content += "== Listening Ports ==\n"
        
        if len(ports) <= 8:
            # Show ports directly for small lists
            for port in ports:
                if port.get('service_info') and port['service_info'].get('display_name'):
                    service_info = port['service_info']
                    port_desc = f" - '''{service_info['display_name']}'''"
                    if service_info.get('description') and service_info['description'] != 'Unknown service':
                        port_desc += f" - {service_info['description']}"
                    if service_info.get('access'):
                        port_desc += f" - ''Access: {service_info['access']}''"
                else:
                    process_name = port.get('process_name', 'unknown')
                    port_desc = f" - {process_name}"
                
                content += f"* '''{port.get('port', 'Unknown')}'''{port_desc}\n"
        else:
            # Show first few ports, then use collapsible section
            ports_to_show_first = ports[:4]
            ports_remaining = ports[4:]
            
            # Show first few ports
            for port in ports_to_show_first:
                if port.get('service_info') and port['service_info'].get('display_name'):
                    service_info = port['service_info']
                    port_desc = f" - '''{service_info['display_name']}'''"
                    if service_info.get('description') and service_info['description'] != 'Unknown service':
                        port_desc += f" - {service_info['description']}"
                    if service_info.get('access'):
                        port_desc += f" - ''Access: {service_info['access']}''"
                else:
                    process_name = port.get('process_name', 'unknown')
                    port_desc = f" - {process_name}"
                
                content += f"* '''{port.get('port', 'Unknown')}'''{port_desc}\n"
            
            # Add collapsible table for remaining ports
            content += f"\n{{| class='wikitable mw-collapsible mw-collapsed'\n"
            content += f"|+ Additional Ports ({len(ports_remaining)} more, total: {len(ports)})\n"
            content += "|-\n"
            content += "! Port !! Service !! Description !! Access\n"
            
            for port in ports_remaining:
                if port.get('service_info') and port['service_info'].get('display_name'):
                    service_info = port['service_info']
                    service_name = service_info['display_name']
                    description = service_info.get('description', '')
                    access = service_info.get('access', '')
                else:
                    service_name = port.get('process_name', 'unknown')
                    description = ''
                    access = ''
                
                content += "|-\n"
                content += f"| {port.get('port', 'Unknown')} || {service_name} || {description} || {access}\n"
            content += "|}\n\n"
        
        content += "\n"

    # Docker Containers
    if host_data.get('docker_containers'):
        containers = host_data['docker_containers']
        
        content += "== Docker Containers ==\n"
        
        if len(containers) <= 5:
            # Show containers directly for small lists
            for container in containers:
                content += f"* '''Name:''' {container.get('name', 'Unknown')}, '''Image:''' {container.get('image', 'Unknown')}, '''Status:''' {container.get('status', 'Unknown')}\n"
        else:
            # Show first few containers, then use collapsible section
            containers_to_show_first = containers[:3]
            containers_remaining = containers[3:]
            
            # Show first few containers
            for container in containers_to_show_first:
                content += f"* '''Name:''' {container.get('name', 'Unknown')}, '''Image:''' {container.get('image', 'Unknown')}, '''Status:''' {container.get('status', 'Unknown')}\n"
            
            # Add collapsible table for remaining containers
            content += f"\n{{| class='wikitable mw-collapsible mw-collapsed'\n"
            content += f"|+ Additional Containers ({len(containers_remaining)} more, total: {len(containers)})\n"
            content += "|-\n"
            content += "! Name !! Image !! Status\n"
            
            for container in containers_remaining:
                content += "|-\n"
                content += f"| {container.get('name', 'Unknown')} || {container.get('image', 'Unknown')} || {container.get('status', 'Unknown')}\n"
            content += "|}\n\n"
        
        content += "\n"

    # Kubernetes
    if host_data.get('kubernetes_info'):
        k8s = host_data['kubernetes_info']
        content += "== Kubernetes ==\n"
        
        if 'kubectl_version' in k8s:
            content += f"* '''Version:''' {k8s['kubectl_version']}\n\n"
        
        # Show namespaces
        if 'namespaces' in k8s and k8s['namespaces']:
            content += f"=== Namespaces ({len(k8s['namespaces'])}) ===\n"
            for namespace in sorted(k8s['namespaces']):
                content += f"* {namespace}\n"
            content += "\n"
        
        # Always show nodes (usually not too many)
        if 'nodes' in k8s:
            content += f"=== Nodes ({len(k8s['nodes'])}) ===\n"
            for node in k8s['nodes']:
                status_indicator = "Ready" if node.get('status') == 'Ready' else "NotReady"
                content += f"* '''{node.get('name', 'Unknown')}''' ({node.get('roles', 'Unknown')}) - {status_indicator}\n"
            content += "\n"
        
        # Pods summary with issues highlighted
        if 'pods' in k8s:
            running_pods = len([p for p in k8s['pods'] if p.get('status') == 'Running'])
            total_pods = len(k8s['pods'])
            content += f"=== Pods Summary ===\n"
            content += f"* '''Total Pods:''' {total_pods}\n"
            content += f"* '''Running:''' {running_pods}\n\n"
            
            # Comprehensive pods list organized by namespace
            content += f"{{| class='wikitable mw-collapsible mw-collapsed'\n"
            content += f"|+ All Pods by Namespace ({total_pods} total)\n"
            content += "|-\n"
            content += "|\n"
            
            # Group pods by namespace
            pods_by_namespace = {}
            for pod in k8s['pods']:
                namespace = pod.get('namespace', 'Unknown')
                if namespace not in pods_by_namespace:
                    pods_by_namespace[namespace] = []
                pods_by_namespace[namespace].append(pod)
            
            # Display pods organized by namespace
            for namespace in sorted(pods_by_namespace.keys()):
                namespace_pods = sorted(pods_by_namespace[namespace], key=lambda p: p.get('name', ''))
                content += f"'''{namespace}''' ({len(namespace_pods)} pods):<br/>\n"
                
                for pod in namespace_pods:
                    # Use MediaWiki-friendly status indicators
                    status_icon = "✅" if pod.get('status') == 'Running' else "❌" if pod.get('status') in ['Failed', 'Error', 'CrashLoopBackOff'] else "⚠️"
                    restart_info = f"({pod.get('restarts', '0')} restarts)" if pod.get('restarts', '0') != '0' else ""
                    age_info = f"Age: {pod.get('age', 'Unknown')}" if pod.get('age', 'Unknown') != 'Unknown' else ""
                    
                    content += f":: {status_icon} '''{pod.get('name', 'Unknown')}''' - {pod.get('status', 'Unknown')} - Ready: {pod.get('ready', 'Unknown')} {restart_info} {age_info}<br/>\n"
                content += "<br/>\n"
            
            content += "|}\n\n"
            
            # Always show problematic pods
            if k8s.get('problematic_pods'):
                problem_count = len(k8s['problematic_pods'])
                content += f"* '''Problematic Pods:''' {problem_count}\n\n"
                content += "==== Issues Found ====\n"
                for pod in k8s['problematic_pods'][:8]:  # Show up to 8 problem pods
                    content += f"* '''{pod.get('namespace', 'Unknown')}/{pod.get('name', 'Unknown')}''' - Status: {pod.get('status', 'Unknown')}, Ready: {pod.get('ready', 'Unknown')}, Restarts: {pod.get('restarts', 'Unknown')}\n"
                if problem_count > 8:
                    content += f"\n''... and {problem_count - 8} more problematic pods''\n\n"
        
        # Services
        if 'services' in k8s and k8s['services']:
            service_count = len(k8s['services'])
            
            content += f"=== Kubernetes Services ({service_count}) ===\n"
            
            if service_count <= 8:
                # Show services directly for small lists
                for svc in k8s['services']:
                    external_info = f" (External: {svc.get('external_ip')})" if svc.get('external_ip') else ""
                    content += f"* '''{svc.get('namespace', 'Unknown')}/{svc.get('name', 'Unknown')}''' ({svc.get('type', 'Unknown')}) - {svc.get('cluster_ip', 'Unknown')}{external_info}\n"
            else:
                # Show first few services, then use collapsible section
                services_to_show_first = k8s['services'][:4]
                services_remaining = k8s['services'][4:]
                
                # Show first few services
                for svc in services_to_show_first:
                    external_info = f" (External: {svc.get('external_ip')})" if svc.get('external_ip') else ""
                    content += f"* '''{svc.get('namespace', 'Unknown')}/{svc.get('name', 'Unknown')}''' ({svc.get('type', 'Unknown')}) - {svc.get('cluster_ip', 'Unknown')}{external_info}\n"
                
                # Add collapsible table for remaining services
                content += f"\n{{| class='wikitable mw-collapsible mw-collapsed'\n"
                content += f"|+ Additional Kubernetes Services ({len(services_remaining)} more)\n"
                content += "|-\n"
                content += "! Namespace/Name !! Type !! Cluster IP !! External IP\n"
                
                for svc in services_remaining:
                    external_ip = svc.get('external_ip', 'None')
                    content += "|-\n"
                    content += f"| {svc.get('namespace', 'Unknown')}/{svc.get('name', 'Unknown')} || {svc.get('type', 'Unknown')} || {svc.get('cluster_ip', 'Unknown')} || {external_ip}\n"
                content += "|}\n\n"
        
        # Deployments
        if 'deployments' in k8s and k8s['deployments']:
            deploy_count = len(k8s['deployments'])
            
            content += f"=== Deployments ({deploy_count}) ===\n"
            
            if deploy_count <= 8:
                # Show deployments directly for small lists
                for dep in k8s['deployments']:
                    content += f"* '''{dep.get('namespace', 'Unknown')}/{dep.get('name', 'Unknown')}''' - Ready: {dep.get('ready', 'Unknown')}, Up-to-date: {dep.get('up_to_date', 'Unknown')}, Available: {dep.get('available', 'Unknown')}\n"
            else:
                # Show first few deployments, then use collapsible section
                deployments_to_show_first = k8s['deployments'][:4]
                deployments_remaining = k8s['deployments'][4:]
                
                # Show first few deployments
                for dep in deployments_to_show_first:
                    content += f"* '''{dep.get('namespace', 'Unknown')}/{dep.get('name', 'Unknown')}''' - Ready: {dep.get('ready', 'Unknown')}, Up-to-date: {dep.get('up_to_date', 'Unknown')}, Available: {dep.get('available', 'Unknown')}\n"
                
                # Add collapsible table for remaining deployments
                content += f"\n{{| class='wikitable mw-collapsible mw-collapsed'\n"
                content += f"|+ Additional Deployments ({len(deployments_remaining)} more)\n"
                content += "|-\n"
                content += "! Namespace/Name !! Ready !! Up-to-date !! Available\n"
                
                for dep in deployments_remaining:
                    content += "|-\n"
                    content += f"| {dep.get('namespace', 'Unknown')}/{dep.get('name', 'Unknown')} || {dep.get('ready', 'Unknown')} || {dep.get('up_to_date', 'Unknown')} || {dep.get('available', 'Unknown')}\n"
                content += "|}\n\n"
        
        content += "\n"
    
    # Proxmox
    if host_data.get('proxmox_info'):
        proxmox = host_data['proxmox_info']
        content += "== Proxmox ==\n"
        
        if 'version' in proxmox:
            content += f"* '''Version:''' {proxmox['version']}\n\n"
        
        # VMs
        if 'vms' in proxmox:
            vm_count = len(proxmox['vms'])
            
            content += f"=== Virtual Machines ({vm_count}) ===\n"
            
            if vm_count <= 8:
                # Show VMs directly for small lists
                for vm in proxmox['vms']:
                    content += f"* {vm}\n"
            else:
                # Show first few VMs, then use collapsible section
                vms_to_show_first = proxmox['vms'][:4]
                vms_remaining = proxmox['vms'][4:]
                
                # Show first few VMs
                for vm in vms_to_show_first:
                    content += f"* {vm}\n"
                
                # Add collapsible table for remaining VMs
                content += f"\n{{| class='wikitable mw-collapsible mw-collapsed'\n"
                content += f"|+ Additional VMs ({len(vms_remaining)} more)\n"
                content += "|-\n"
                content += "! VM Information\n"
                
                for vm in vms_remaining:
                    content += "|-\n"
                    content += f"| {vm}\n"
                content += "|}\n\n"
        
        # Containers
        if 'containers' in proxmox:
            container_count = len(proxmox['containers'])
            
            content += f"=== Containers ({container_count}) ===\n"
            
            if container_count <= 8:
                # Show containers directly for small lists
                for container in proxmox['containers']:
                    content += f"* {container}\n"
            else:
                # Show first few containers, then use collapsible section
                containers_to_show_first = proxmox['containers'][:4]
                containers_remaining = proxmox['containers'][4:]
                
                # Show first few containers
                for container in containers_to_show_first:
                    content += f"* {container}\n"
                
                # Add collapsible table for remaining containers
                content += f"\n{{| class='wikitable mw-collapsible mw-collapsed'\n"
                content += f"|+ Additional Containers ({len(containers_remaining)} more)\n"
                content += "|-\n"
                content += "! Container Information\n"
                
                for container in containers_remaining:
                    content += "|-\n"
                    content += f"| {container}\n"
                content += "|}\n\n"
        
        content += "\n"
    
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
            content = f"# Lab Documentation Index\n\n"
            content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            content += "## Documented Servers\n\n"
            
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

