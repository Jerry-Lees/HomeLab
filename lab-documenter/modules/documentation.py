"""
Documentation generation for Lab Documenter (Jinja2-based version)

Handles Markdown and MediaWiki content generation using Jinja2 templates.
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Any

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False

logger = logging.getLogger(__name__)

class DocumentationManager:
    def __init__(self, docs_dir: str = 'documentation', template_dir: str = 'templates'):
        self.docs_dir = docs_dir
        self.template_dir = template_dir
        
        if HAS_JINJA2:
            self.jinja_env = Environment(  # type: ignore
                loader=FileSystemLoader(template_dir),   # type: ignore
                autoescape=select_autoescape(['html', 'xml']),  # type: ignore
                trim_blocks=True,
                lstrip_blocks=True
            )
            self._register_filters()
            logger.info(f"Jinja2 template engine initialized with template directory: {template_dir}")
        else:
            logger.warning("Jinja2 not available, falling back to minimal content generation")
            self.jinja_env = None
        
        self.ensure_docs_directory()
    
    def _register_filters(self):
        """Register custom Jinja2 filters for lab-specific formatting"""
        if not self.jinja_env:
            return
            
        # Standard filters for data processing
        self.jinja_env.filters['selectattr_ne'] = lambda seq, attr, value: [x for x in seq if getattr(x, attr, None) != value]
        self.jinja_env.filters['group_by_attr'] = self._group_by_attr
        self.jinja_env.filters['count_status'] = self._count_status
        
        # Global functions available in templates
        self.jinja_env.globals['status_icon'] = self._status_icon
        self.jinja_env.globals['format_service'] = self._format_service
        self.jinja_env.globals['format_port'] = self._format_port
        self.jinja_env.globals['format_container'] = self._format_container
    
    def _group_by_attr(self, seq: List[Dict], attr: str) -> List[tuple]:
        """Group sequence by attribute value"""
        groups = {}
        for item in seq:
            key = item.get(attr, 'Unknown')
            if key not in groups:
                groups[key] = []
            groups[key].append(item)
        return list(groups.items())
    
    def _count_status(self, seq: List[Dict], status_value: str) -> int:
        """Count items with specific status"""
        return len([item for item in seq if item.get('status') == status_value])
    
    def _status_icon(self, status: str) -> str:
        """Get status icon for resource"""
        if status in ['running', 'Running']:
            return "✅"
        elif status == 'stopped':
            return "ℹ️"
        elif status in ['Failed', 'Error', 'CrashLoopBackOff']:
            return "❌"
        else:
            return "⚠️"
    
    def _format_service(self, service: Dict[str, Any]) -> str:
        """Format service for display"""
        display_name = service.get('display_name', service.get('name', 'Unknown'))
        description = service.get('description', '')
        category = service.get('category', '')
        
        service_line = f"**{display_name}** ({service.get('status', 'Unknown')})"
        if category and category != 'unknown':
            service_line += f" - *{category}*"
        elif service.get('_auto_generated'):
            service_line += f" - *auto-discovered*"
        
        if description and description != 'Unknown service':
            if service.get('_auto_generated'):
                service_line += f" - Please update service information"
            else:
                service_line += f" - {description}"
        
        return service_line
    
    def _format_port(self, port: Dict[str, Any]) -> str:
        """Format listening port for display"""
        port_line = f"**{port.get('port', 'Unknown')}**"
        
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
        
        return port_line
    
    def _format_container(self, container: Dict[str, Any]) -> str:
        """Format Docker container for display"""
        return f"**Name:** {container.get('name', 'Unknown')}, **Image:** {container.get('image', 'Unknown')}, **Status:** {container.get('status', 'Unknown')}"
    
    def generate_markdown_content(self, host_data: Dict[str, Any]) -> str:
        """Generate Markdown content for a host using Jinja2 templates"""
        if not self.jinja_env:
            return self._generate_fallback_content(host_data, 'markdown')
        
        context = self._prepare_context(host_data)
        
        try:
            template = self.jinja_env.get_template('pages/server.md.j2')
            return template.render(context)
        except Exception as e:
            logger.error(f"Error rendering Markdown template: {e}")
            return self._generate_fallback_content(host_data, 'markdown')
    
    def generate_mediawiki_content(self, host_data: Dict[str, Any]) -> str:
        """Generate MediaWiki content for a host using Jinja2 templates"""
        if not self.jinja_env:
            return self._generate_fallback_content(host_data, 'mediawiki')
        
        context = self._prepare_context(host_data)
        
        try:
            template = self.jinja_env.get_template('pages/server.wiki.j2')
            return template.render(context)
        except Exception as e:
            logger.error(f"Error rendering MediaWiki template: {e}")
            return self._generate_fallback_content(host_data, 'mediawiki')
    
    def generate_wiki_index_content(self, inventory: Dict[str, Any]) -> str:
        """Generate MediaWiki content for the server index page"""
        if not self.jinja_env:
            return self._generate_fallback_index_content(inventory)
        
        context = self._prepare_index_context(inventory)
        
        try:
            template = self.jinja_env.get_template('pages/index.wiki.j2')
            return template.render(context)
        except Exception as e:
            logger.error(f"Error rendering wiki index template: {e}")
            return self._generate_fallback_index_content(inventory)
    
    def _prepare_context(self, host_data: Dict[str, Any]) -> Dict[str, Any]:
            """Prepare template context from host data"""
            # Use actual hostname if available, otherwise fall back to original
            display_hostname = host_data.get('actual_hostname', host_data.get('hostname', 'Unknown Host'))
            
            context = host_data.copy()
            context['display_hostname'] = display_hostname
            
            # Add navigation context for index page links
            # Try to get from global CONFIG first, then fallback to defaults
            try:
                from modules.config import CONFIG
                index_page_title = CONFIG.get('mediawiki_index_page', 'Server Documentation')
            except (ImportError, AttributeError):
                index_page_title = 'Server Documentation'
            
            context['index_page_title'] = index_page_title
            context['index_page_link'] = 'index.md'  # For Markdown templates
            
            # Ensure nested objects exist even if empty to prevent template errors
            defaults = {
                'os_release': {},
                'memory_modules': {},
                'services': [],
                'listening_ports': [],
                'docker_containers': [],
                'kubernetes_info': {},
                'proxmox_info': {},
                'bios_info': {}
            }
            
            for key, default_value in defaults.items():
                if key not in context:
                    context[key] = default_value
            
            return context

    def _prepare_index_context(self, inventory: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare template context for index page"""
        # Separate reachable and unreachable hosts
        reachable_hosts = [(hostname, data) for hostname, data in inventory.items() if data.get('reachable')]
        unreachable_hosts = [(hostname, data) for hostname, data in inventory.items() if not data.get('reachable')]
        
        # Count by OS
        os_counts = {}
        service_counts = {'kubernetes': 0, 'docker': 0, 'proxmox': 0}
        
        for hostname, data in reachable_hosts:
            if data.get('reachable'):
                os_info = data.get('os_release', {})
                os_name = os_info.get('name', 'Unknown')
                os_counts[os_name] = os_counts.get(os_name, 0) + 1
                
                # Count special services
                if data.get('kubernetes_info'):
                    service_counts['kubernetes'] += 1
                if data.get('docker_containers'):
                    service_counts['docker'] += 1
                if data.get('proxmox_info'):
                    service_counts['proxmox'] += 1
        
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_servers': len(inventory),
            'reachable_count': len(reachable_hosts),
            'unreachable_count': len(unreachable_hosts),
            'reachable_hosts': reachable_hosts,
            'unreachable_hosts': unreachable_hosts,
            'os_counts': os_counts,
            'service_counts': service_counts
        }
    
    def _generate_fallback_content(self, host_data: Dict[str, Any], format_type: str) -> str:
        """Fallback content generation when Jinja2 is not available"""
        display_hostname = host_data.get('actual_hostname', host_data.get('hostname', 'Unknown Host'))
        os_info = host_data.get('os_release', {})
        
        if format_type == 'markdown':
            return f"""# {display_hostname}

**Last Updated:** {host_data.get('timestamp', 'Unknown')}

## System Information
- **OS:** {os_info.get('pretty_name', os_info.get('name', 'Unknown'))}
- **Version:** {os_info.get('version', 'Unknown')}
- **Kernel:** {host_data.get('kernel', 'Unknown')}
- **Architecture:** {host_data.get('architecture', 'Unknown')}
- **Uptime:** {host_data.get('uptime', 'Unknown')}
- **CPU:** {host_data.get('cpu_info', 'Unknown')} ({host_data.get('cpu_cores', 'Unknown')} cores)

## Resources
- **Memory:** {host_data.get('memory_used', 'Unknown')} / {host_data.get('memory_total', 'Unknown')}
- **Disk Usage:** {host_data.get('disk_usage', 'Unknown')}
- **Load Average:** {host_data.get('load_average', 'Unknown')}

*Jinja2 template system not available - showing basic information only*
*To see full documentation with all sections, install Jinja2: pip install jinja2*
"""
        else:  # mediawiki
            return f"""= {display_hostname} =

'''Last Updated:''' {host_data.get('timestamp', 'Unknown')}

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
* '''Load Average:''' {host_data.get('load_average', 'Unknown')}

''Jinja2 template system not available - showing basic information only''
''To see full documentation with all sections, install Jinja2: pip install jinja2''
"""
    
    def _generate_fallback_index_content(self, inventory: Dict[str, Any]) -> str:
        """Fallback index content when Jinja2 is not available"""
        reachable_count = len([data for data in inventory.values() if data.get('reachable')])
        
        return f"""= Server Documentation =

'''Last Updated:''' {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

== Quick Statistics ==
* '''Total Servers:''' {len(inventory)}
* '''Reachable:''' {reachable_count}
* '''Unreachable:''' {len(inventory) - reachable_count}

''Jinja2 template system not available - showing basic information only''
''To see full index with server links and details, install Jinja2: pip install jinja2''
"""
    
    def ensure_docs_directory(self):
        """Create documentation directory if it doesn't exist"""
        try:
            if not os.path.exists(self.docs_dir):
                os.makedirs(self.docs_dir)
                logger.info(f"Created documentation directory: {self.docs_dir}")
        except Exception as e:
            logger.error(f"Failed to create documentation directory {self.docs_dir}: {e}")
            raise
    
    def save_host_documentation(self, hostname: str, host_data: Dict[str, Any]):
        """Save documentation file for a single host"""
        if not host_data.get('reachable'):
            logger.warning(f"Skipping documentation for unreachable host: {hostname}")
            return False
        
        # Sanitize hostname for filename
        safe_hostname = self.sanitize_filename(hostname)
        doc_path = os.path.join(self.docs_dir, f"{safe_hostname}.md")
        json_path = os.path.join(self.docs_dir, f"{safe_hostname}.json")
        
        try:
            # Save Markdown documentation using template
            content = self.generate_markdown_content(host_data)
            with open(doc_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Documentation saved: {doc_path}")
            
            # Save individual JSON file
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump({hostname: host_data}, f, indent=2)
            logger.info(f"Individual JSON saved: {json_path}")
            
            return True
        except Exception as e:
            logger.error(f"Error saving documentation for {hostname}: {e}")
            return False
    
    def save_all_documentation(self, inventory: Dict[str, Any]):
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
    
    def create_index_file(self, inventory: Dict[str, Any]):
        """Create an index.md file listing all documented hosts"""
        index_path = os.path.join(self.docs_dir, 'index.md')
        
        try:
            if self.jinja_env:
                try:
                    template = self.jinja_env.get_template('pages/index.md.j2')
                    context = self._prepare_index_context(inventory)
                    content = template.render(context)
                except Exception as e:
                    logger.warning(f"Template rendering failed, using fallback: {e}")
                    content = self._generate_simple_markdown_index(inventory)
            else:
                content = self._generate_simple_markdown_index(inventory)
            
            with open(index_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Index file created: {index_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating index file: {e}")
            return False
    
    def _generate_simple_markdown_index(self, inventory: Dict[str, Any]) -> str:
        """Generate simple markdown index"""
        reachable_hosts = [(hostname, data) for hostname, data in inventory.items() if data.get('reachable')]
        unreachable_hosts = [(hostname, data) for hostname, data in inventory.items() if not data.get('reachable')]
        
        content = f"# Lab Documentation Index\n\n"
        content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        content += "## Documented Servers\n\n"
        
        if reachable_hosts:
            content += "### Active Servers\n\n"
            for hostname, data in sorted(reachable_hosts):
                safe_hostname = self.sanitize_filename(hostname)
                os_info = data.get('os_release', {})
                os_display = os_info.get('pretty_name', os_info.get('name', 'Unknown OS'))
                uptime = data.get('uptime', 'Unknown uptime')
                content += f"- **[{hostname}]({safe_hostname}.md)** - {os_display} - {uptime}\n"
        
        if unreachable_hosts:
            content += "\n### Unreachable Servers\n\n"
            for hostname, data in sorted(unreachable_hosts):
                last_seen = data.get('timestamp', 'Never')
                content += f"- **{hostname}** - Last attempt: {last_seen}\n"
        
        content += f"\n---\n\n**Total Servers:** {len(inventory)} ({len(reachable_hosts)} reachable, {len(unreachable_hosts)} unreachable)\n"
        
        return content
    
    def sanitize_filename(self, hostname: str) -> str:
        """Sanitize hostname for use as filename"""
        import re
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', hostname)
        safe_name = safe_name.replace(' ', '_')
        return safe_name


# Convenience functions for backward compatibility
def generate_markdown_content(host_data: Dict[str, Any]) -> str:
    """Generate Markdown content for a host (backward compatibility)"""
    docs_manager = DocumentationManager()
    return docs_manager.generate_markdown_content(host_data)

def generate_mediawiki_content(host_data: Dict[str, Any]) -> str:
    """Generate MediaWiki-specific content for a host (backward compatibility)"""
    docs_manager = DocumentationManager()
    return docs_manager.generate_mediawiki_content(host_data)

def generate_wiki_index_content(inventory: Dict[str, Any]) -> str:
    """Generate MediaWiki content for the server index page (backward compatibility)"""
    docs_manager = DocumentationManager()
    return docs_manager.generate_wiki_index_content(inventory)

