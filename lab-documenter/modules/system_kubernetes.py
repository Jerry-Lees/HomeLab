"""
Kubernetes information collection for Lab Documenter

Handles Kubernetes cluster information gathering via kubectl commands.
"""

import logging
from typing import Dict, List, Callable, Optional, Union, Any

logger = logging.getLogger(__name__)

class KubernetesCollector:
    def __init__(self, command_runner: Callable[[str], Optional[str]]):
        """
        Initialize the Kubernetes collector.
        
        Args:
            command_runner: Function that executes commands and returns output
        """
        self.run_command = command_runner
    
    def collect_kubernetes_info(self) -> Dict[str, Any]:
        """Get comprehensive Kubernetes information if kubectl is available"""
        k8s_info: Dict[str, Any] = {}
        
        # Check if kubectl is available
        kubectl_version = self.run_command('kubectl version --client -o json 2>/dev/null | grep gitVersion || kubectl version --client 2>/dev/null | head -1')
        if not kubectl_version:
            return k8s_info
            
        k8s_info['kubectl_version'] = kubectl_version.strip()
        
        # Gather cluster information
        cluster_info = self.run_command('kubectl cluster-info 2>/dev/null | head -3')
        if cluster_info:
            k8s_info['cluster_info'] = cluster_info
        
        # Collect nodes information
        nodes_data = self.collect_nodes()
        if nodes_data:
            k8s_info['nodes'] = nodes_data
        
        # Collect namespaces
        namespaces_data = self.collect_namespaces()
        if namespaces_data:
            k8s_info['namespaces'] = namespaces_data
        
        # Collect pods information
        pods_data = self.collect_pods()
        if pods_data['pods']:
            k8s_info['pods'] = pods_data['pods']
            if pods_data['problematic_pods']:
                k8s_info['problematic_pods'] = pods_data['problematic_pods']
        
        # Collect services
        services_data = self.collect_services()
        if services_data:
            k8s_info['services'] = services_data
        
        # Collect deployments
        deployments_data = self.collect_deployments()
        if deployments_data:
            k8s_info['deployments'] = deployments_data
        
        return k8s_info
    
    def collect_nodes(self) -> List[Dict[str, Optional[str]]]:
        """Collect Kubernetes nodes information"""
        nodes = self.run_command('kubectl get nodes --no-headers -o wide 2>/dev/null')
        if not nodes:
            return []
        
        nodes_list: List[Dict[str, Optional[str]]] = []
        for line in nodes.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    node_info: Dict[str, Optional[str]] = {
                        'name': parts[0],
                        'status': parts[1],
                        'roles': parts[2] if len(parts) > 2 else 'Unknown',
                        'age': parts[3] if len(parts) > 3 else 'Unknown',
                        'version': parts[4] if len(parts) > 4 else 'Unknown'
                    }
                    
                    # Add additional node details if available
                    if len(parts) > 5:
                        node_info['internal_ip'] = parts[5]
                    if len(parts) > 6:
                        node_info['external_ip'] = parts[6] if parts[6] != '<none>' else None
                    if len(parts) > 7:
                        node_info['os_image'] = parts[7]
                    if len(parts) > 8:
                        node_info['kernel_version'] = parts[8]
                    if len(parts) > 9:
                        node_info['container_runtime'] = parts[9]
                    
                    nodes_list.append(node_info)
        
        return nodes_list
    
    def collect_namespaces(self) -> List[str]:
        """Collect Kubernetes namespaces"""
        namespaces = self.run_command('kubectl get namespaces --no-headers 2>/dev/null')
        if not namespaces:
            return []
        
        return [line.split()[0] for line in namespaces.split('\n') if line.strip()]
    
    def collect_pods(self) -> Dict[str, List[Dict[str, Optional[str]]]]:
        """Collect Kubernetes pods information and identify problematic ones"""
        pods = self.run_command('kubectl get pods --all-namespaces --no-headers -o wide 2>/dev/null')
        if not pods:
            return {'pods': [], 'problematic_pods': []}
        
        pods_list: List[Dict[str, Optional[str]]] = []
        problematic_pods: List[Dict[str, Optional[str]]] = []
        
        for line in pods.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 5:
                    pod_info: Dict[str, Optional[str]] = {
                        'namespace': parts[0],
                        'name': parts[1],
                        'ready': parts[2],
                        'status': parts[3],
                        'restarts': parts[4],
                        'age': parts[5] if len(parts) > 5 else 'Unknown'
                    }
                    
                    # Add additional pod details if available
                    if len(parts) > 6:
                        pod_info['ip'] = parts[6] if parts[6] != '<none>' else None
                    if len(parts) > 7:
                        pod_info['node'] = parts[7]
                    if len(parts) > 8:
                        pod_info['nominated_node'] = parts[8] if parts[8] != '<none>' else None
                    if len(parts) > 9:
                        pod_info['readiness_gates'] = parts[9] if parts[9] != '<none>' else None
                    
                    pods_list.append(pod_info)
                    
                    # Identify problematic pods
                    if self.is_pod_problematic(pod_info):
                        problematic_pods.append(pod_info)
        
        return {'pods': pods_list, 'problematic_pods': problematic_pods}
    
    def is_pod_problematic(self, pod_info: Dict[str, Optional[str]]) -> bool:
        """Determine if a pod has issues that need attention"""
        status = pod_info.get('status', '')
        ready = pod_info.get('ready', '')
        restarts = pod_info.get('restarts', '0')
        
        # Check for problematic statuses
        problematic_statuses = [
            'Failed', 'Error', 'CrashLoopBackOff', 'ImagePullBackOff', 
            'Pending', 'ContainerCreating', 'Terminating', 'Unknown'
        ]
        
        if status in problematic_statuses:
            return True
        
        # Check if pod is not ready (but should be running)
        if status == 'Running' and ready and '/' in ready:
            ready_parts = ready.split('/')
            if len(ready_parts) == 2 and ready_parts[0] != ready_parts[1]:
                return True
        
        # Check for excessive restarts
        try:
            restart_count = int(restarts or '0')
            if restart_count > 5:
                return True
        except (ValueError, TypeError):
            pass
        
        return False
    
    def collect_services(self) -> List[Dict[str, Optional[str]]]:
        """Collect Kubernetes services information"""
        services = self.run_command('kubectl get services --all-namespaces --no-headers 2>/dev/null')
        if not services:
            return []
        
        services_list: List[Dict[str, Optional[str]]] = []
        for line in services.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 4:
                    service_info: Dict[str, Optional[str]] = {
                        'namespace': parts[0],
                        'name': parts[1],
                        'type': parts[2],
                        'cluster_ip': parts[3],
                        'external_ip': parts[4] if len(parts) > 4 and parts[4] != '<none>' else None,
                        'ports': parts[5] if len(parts) > 5 else 'Unknown',
                        'age': parts[6] if len(parts) > 6 else 'Unknown'
                    }
                    services_list.append(service_info)
        
        return services_list
    
    def collect_deployments(self) -> List[Dict[str, str]]:
        """Collect Kubernetes deployments information"""
        deployments = self.run_command('kubectl get deployments --all-namespaces --no-headers 2>/dev/null')
        if not deployments:
            return []
        
        deployments_list: List[Dict[str, str]] = []
        for line in deployments.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 4:
                    deployment_info: Dict[str, str] = {
                        'namespace': parts[0],
                        'name': parts[1],
                        'ready': parts[2],
                        'up_to_date': parts[3],
                        'available': parts[4] if len(parts) > 4 else 'Unknown',
                        'age': parts[5] if len(parts) > 5 else 'Unknown'
                    }
                    deployments_list.append(deployment_info)
        
        return deployments_list
    
    def collect_persistent_volumes(self) -> List[Dict[str, str]]:
        """Collect Kubernetes persistent volumes information"""
        pvs = self.run_command('kubectl get pv --no-headers 2>/dev/null')
        if not pvs:
            return []
        
        pv_list: List[Dict[str, str]] = []
        for line in pvs.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 4:
                    pv_info: Dict[str, str] = {
                        'name': parts[0],
                        'capacity': parts[1],
                        'access_modes': parts[2],
                        'reclaim_policy': parts[3],
                        'status': parts[4] if len(parts) > 4 else 'Unknown',
                        'claim': parts[5] if len(parts) > 5 else 'Unknown',
                        'storageclass': parts[6] if len(parts) > 6 else 'Unknown',
                        'reason': parts[7] if len(parts) > 7 else 'Unknown',
                        'age': parts[8] if len(parts) > 8 else 'Unknown'
                    }
                    pv_list.append(pv_info)
        
        return pv_list
    
    def collect_ingresses(self) -> List[Dict[str, Optional[str]]]:
        """Collect Kubernetes ingresses information"""
        ingresses = self.run_command('kubectl get ingresses --all-namespaces --no-headers 2>/dev/null')
        if not ingresses:
            return []
        
        ingress_list: List[Dict[str, Optional[str]]] = []
        for line in ingresses.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    ingress_info: Dict[str, Optional[str]] = {
                        'namespace': parts[0],
                        'name': parts[1],
                        'class': parts[2] if parts[2] != '<none>' else None,
                        'hosts': parts[3] if len(parts) > 3 and parts[3] != '*' else None,
                        'address': parts[4] if len(parts) > 4 else None,
                        'ports': parts[5] if len(parts) > 5 else None,
                        'age': parts[6] if len(parts) > 6 else 'Unknown'
                    }
                    ingress_list.append(ingress_info)
        
        return ingress_list
    
    def get_cluster_health_summary(self) -> Dict[str, int]:
        """Get a high-level health summary of the cluster"""
        health_summary: Dict[str, int] = {
            'total_nodes': 0,
            'ready_nodes': 0,
            'total_pods': 0,
            'running_pods': 0,
            'problematic_pods': 0,
            'total_deployments': 0,
            'available_deployments': 0
        }
        
        # Count nodes
        nodes = self.collect_nodes()
        health_summary['total_nodes'] = len(nodes)
        health_summary['ready_nodes'] = len([n for n in nodes if n.get('status') == 'Ready'])
        
        # Count pods
        pods_data = self.collect_pods()
        health_summary['total_pods'] = len(pods_data['pods'])
        health_summary['running_pods'] = len([p for p in pods_data['pods'] if p.get('status') == 'Running'])
        health_summary['problematic_pods'] = len(pods_data['problematic_pods'])
        
        # Count deployments
        deployments = self.collect_deployments()
        health_summary['total_deployments'] = len(deployments)
        health_summary['available_deployments'] = len([d for d in deployments if d.get('available', '0') != '0'])
        
        return health_summary

