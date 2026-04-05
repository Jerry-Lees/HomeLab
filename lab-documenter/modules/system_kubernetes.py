"""
Kubernetes information collection for Lab Documenter

Handles Kubernetes cluster information gathering via kubectl commands.
"""

import json
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

        # Collect statefulsets
        statefulsets_data = self.collect_statefulsets()
        if statefulsets_data:
            k8s_info['statefulsets'] = statefulsets_data

        # Collect daemonsets
        daemonsets_data = self.collect_daemonsets()
        if daemonsets_data:
            k8s_info['daemonsets'] = daemonsets_data

        # Collect ingresses
        ingresses_data = self.collect_ingresses()
        if ingresses_data:
            k8s_info['ingresses'] = ingresses_data

        # Collect persistent volumes
        pvs_data = self.collect_persistent_volumes()
        if pvs_data:
            k8s_info['persistent_volumes'] = pvs_data

        # Collect persistent volume claims
        pvcs_data = self.collect_pvcs()
        if pvcs_data:
            k8s_info['pvcs'] = pvcs_data

        # Collect storage classes
        storageclasses_data = self.collect_storageclasses()
        if storageclasses_data:
            k8s_info['storageclasses'] = storageclasses_data

        # Collect configmaps
        configmaps_data = self.collect_configmaps()
        if configmaps_data:
            k8s_info['configmaps'] = configmaps_data

        # Collect secrets (names/types only — full YAML in backup files)
        secrets_data = self.collect_secrets()
        if secrets_data:
            k8s_info['secrets'] = secrets_data

        # Collect service accounts
        serviceaccounts_data = self.collect_serviceaccounts()
        if serviceaccounts_data:
            k8s_info['serviceaccounts'] = serviceaccounts_data

        # Collect roles
        roles_data = self.collect_roles()
        if roles_data:
            k8s_info['roles'] = roles_data

        # Collect cluster roles
        clusterroles_data = self.collect_clusterroles()
        if clusterroles_data:
            k8s_info['clusterroles'] = clusterroles_data

        # Collect role bindings
        rolebindings_data = self.collect_rolebindings()
        if rolebindings_data:
            k8s_info['rolebindings'] = rolebindings_data

        # Collect cluster role bindings
        clusterrolebindings_data = self.collect_clusterrolebindings()
        if clusterrolebindings_data:
            k8s_info['clusterrolebindings'] = clusterrolebindings_data

        # Collect helm releases
        helm_data = self.collect_helm_releases()
        if helm_data:
            k8s_info['helm_releases'] = helm_data

        # Collect YAML manifests for backup (written to disk by documentation.py)
        yaml_backups = self.collect_yaml_backups(k8s_info)
        if yaml_backups:
            k8s_info['yaml_backups'] = yaml_backups

        return k8s_info

    def collect_nodes(self) -> List[Dict[str, Optional[str]]]:
        """Collect Kubernetes nodes information including taints"""
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

        # Collect taints for all nodes in one command
        taints_map = self._collect_node_taints()
        for node in nodes_list:
            node['taints'] = taints_map.get(node.get('name', ''), '')

        return nodes_list

    def _collect_node_taints(self) -> Dict[str, str]:
        """Get taints for all nodes via kubectl describe"""
        taints_map: Dict[str, str] = {}
        result = self.run_command(
            "kubectl describe nodes 2>/dev/null | grep -E '^Name:|^Taints:'"
        )
        if not result:
            return taints_map

        current_node = None
        for line in result.strip().split('\n'):
            if line.startswith('Name:'):
                current_node = line.split(':', 1)[1].strip()
            elif line.startswith('Taints:') and current_node:
                taint_val = line.split(':', 1)[1].strip()
                if taint_val and taint_val != '<none>':
                    taints_map[current_node] = taint_val

        return taints_map

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
                    # Restarts field may include "(Xd ago)" annotation, e.g. "6 (20d ago)"
                    # which splits into 3 tokens and shifts all subsequent columns.
                    restarts = parts[4]
                    idx = 5
                    if idx < len(parts) and parts[idx].startswith('('):
                        while idx < len(parts) and not parts[idx].endswith(')'):
                            idx += 1
                        idx += 1  # skip closing ')'

                    pod_info: Dict[str, Optional[str]] = {
                        'namespace': parts[0],
                        'name': parts[1],
                        'ready': parts[2],
                        'status': parts[3],
                        'restarts': restarts,
                        'age': parts[idx] if idx < len(parts) else 'Unknown'
                    }

                    # Add additional pod details if available
                    if idx + 1 < len(parts):
                        pod_info['ip'] = parts[idx + 1] if parts[idx + 1] != '<none>' else None
                    if idx + 2 < len(parts):
                        pod_info['node'] = parts[idx + 2] if parts[idx + 2] != '<none>' else None
                    if idx + 3 < len(parts):
                        pod_info['nominated_node'] = parts[idx + 3] if parts[idx + 3] != '<none>' else None
                    if idx + 4 < len(parts):
                        pod_info['readiness_gates'] = parts[idx + 4] if parts[idx + 4] != '<none>' else None

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

    def collect_statefulsets(self) -> List[Dict[str, str]]:
        """Collect Kubernetes statefulsets information"""
        output = self.run_command('kubectl get statefulsets --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'ready': parts[2],
                        'age': parts[3] if len(parts) > 3 else 'Unknown',
                    })
        return result

    def collect_daemonsets(self) -> List[Dict[str, str]]:
        """Collect Kubernetes daemonsets information"""
        output = self.run_command('kubectl get daemonsets --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 4:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'desired': parts[2],
                        'current': parts[3],
                        'ready': parts[4] if len(parts) > 4 else 'Unknown',
                        'up_to_date': parts[5] if len(parts) > 5 else 'Unknown',
                        'available': parts[6] if len(parts) > 6 else 'Unknown',
                        'age': parts[7] if len(parts) > 7 else 'Unknown',
                    })
        return result

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

    def collect_pvcs(self) -> List[Dict[str, str]]:
        """Collect Kubernetes persistent volume claims information"""
        output = self.run_command('kubectl get pvc --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'status': parts[2],
                        'volume': parts[3] if len(parts) > 3 else '-',
                        'capacity': parts[4] if len(parts) > 4 else 'Unknown',
                        'access_modes': parts[5] if len(parts) > 5 else 'Unknown',
                        'storageclass': parts[6] if len(parts) > 6 else '-',
                        'age': parts[7] if len(parts) > 7 else 'Unknown',
                    })
        return result

    def collect_storageclasses(self) -> List[Dict[str, str]]:
        """Collect Kubernetes storage classes information"""
        output = self.run_command('kubectl get storageclasses --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append({
                        'name': parts[0],
                        'provisioner': parts[1],
                        'reclaim_policy': parts[2] if len(parts) > 2 else 'Unknown',
                        'volume_binding_mode': parts[3] if len(parts) > 3 else 'Unknown',
                        'allow_volume_expansion': parts[4] if len(parts) > 4 else 'Unknown',
                        'age': parts[5] if len(parts) > 5 else 'Unknown',
                    })
        return result

    def collect_configmaps(self) -> List[Dict[str, str]]:
        """Collect Kubernetes configmaps information"""
        output = self.run_command('kubectl get configmaps --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'data': parts[2] if len(parts) > 2 else '0',
                        'age': parts[3] if len(parts) > 3 else 'Unknown',
                    })
        return result

    def collect_secrets(self) -> List[Dict[str, str]]:
        """Collect Kubernetes secrets information (names and types only — no values)"""
        output = self.run_command('kubectl get secrets --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'type': parts[2] if len(parts) > 2 else 'Unknown',
                        'data': parts[3] if len(parts) > 3 else '0',
                        'age': parts[4] if len(parts) > 4 else 'Unknown',
                    })
        return result

    def collect_serviceaccounts(self) -> List[Dict[str, str]]:
        """Collect Kubernetes service accounts information"""
        output = self.run_command('kubectl get serviceaccounts --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'secrets': parts[2] if len(parts) > 2 else '0',
                        'age': parts[3] if len(parts) > 3 else 'Unknown',
                    })
        return result

    def collect_roles(self) -> List[Dict[str, str]]:
        """Collect Kubernetes roles information"""
        output = self.run_command('kubectl get roles --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'age': parts[2] if len(parts) > 2 else 'Unknown',
                    })
        return result

    def collect_clusterroles(self) -> List[Dict[str, str]]:
        """Collect Kubernetes cluster roles information"""
        output = self.run_command('kubectl get clusterroles --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 1:
                    result.append({
                        'name': parts[0],
                        'age': parts[1] if len(parts) > 1 else 'Unknown',
                    })
        return result

    def collect_rolebindings(self) -> List[Dict[str, str]]:
        """Collect Kubernetes role bindings information"""
        output = self.run_command('kubectl get rolebindings --all-namespaces --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    result.append({
                        'namespace': parts[0],
                        'name': parts[1],
                        'role': parts[2] if len(parts) > 2 else 'Unknown',
                        'age': parts[3] if len(parts) > 3 else 'Unknown',
                    })
        return result

    def collect_clusterrolebindings(self) -> List[Dict[str, str]]:
        """Collect Kubernetes cluster role bindings information"""
        output = self.run_command('kubectl get clusterrolebindings --no-headers 2>/dev/null')
        if not output:
            return []
        result = []
        for line in output.split('\n'):
            if line.strip():
                parts = line.split()
                if len(parts) >= 1:
                    result.append({
                        'name': parts[0],
                        'role': parts[1] if len(parts) > 1 else 'Unknown',
                        'age': parts[2] if len(parts) > 2 else 'Unknown',
                    })
        return result

    def collect_helm_releases(self) -> List[Dict[str, str]]:
        """Collect Helm releases via 'helm list -A -o json'"""
        output = self.run_command('helm list --all-namespaces -o json 2>/dev/null')
        if not output or not output.strip():
            return []
        try:
            releases = json.loads(output)
            if not isinstance(releases, list):
                return []
            result = []
            for r in releases:
                result.append({
                    'name': r.get('name', 'Unknown'),
                    'namespace': r.get('namespace', 'Unknown'),
                    'revision': r.get('revision', '-'),
                    'updated': r.get('updated', 'Unknown'),
                    'status': r.get('status', 'Unknown'),
                    'chart': r.get('chart', 'Unknown'),
                    'app_version': r.get('app_version', 'Unknown'),
                })
            return result
        except Exception:
            return []

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

    def collect_yaml_backups(self, k8s_info: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        """
        Collect individual YAML manifests for each resource using already-collected item lists.
        Returns {backup_dir: {filename: yaml_content}}.
        Written to backups/kubernetes/<backup_dir>/<filename> by documentation.py.
        """
        backups: Dict[str, Dict[str, str]] = {}

        # (k8s_info_key, kubectl_singular, backup_dir)
        namespaced_types = [
            ('deployments',     'deployment',            'deployments'),
            ('statefulsets',    'statefulset',           'statefulsets'),
            ('daemonsets',      'daemonset',             'daemonsets'),
            ('services',        'service',               'services'),
            ('ingresses',       'ingress',               'ingresses'),
            ('pvcs',            'persistentvolumeclaim', 'persistentvolumeclaims'),
            ('configmaps',      'configmap',             'configmaps'),
            ('secrets',         'secret',                'secrets'),
            ('serviceaccounts', 'serviceaccount',        'serviceaccounts'),
            ('roles',           'role',                  'roles'),
            ('rolebindings',    'rolebinding',           'rolebindings'),
        ]
        for info_key, resource, backup_dir in namespaced_types:
            items = k8s_info.get(info_key, [])
            type_files: Dict[str, str] = {}
            for item in items:
                ns = item.get('namespace', '')
                name = item.get('name', '')
                if not ns or not name:
                    continue
                yaml_out = self.run_command(
                    f'kubectl get {resource} -n {ns} {name} -o yaml 2>/dev/null'
                )
                if yaml_out and yaml_out.strip():
                    type_files[f'{ns}-{name}.yaml'] = yaml_out
            if type_files:
                backups[backup_dir] = type_files

        # Cluster-wide resources — no namespace
        cluster_types = [
            ('persistent_volumes',  'persistentvolume',   'persistentvolumes'),
            ('storageclasses',      'storageclass',        'storageclasses'),
            ('clusterroles',        'clusterrole',         'clusterroles'),
            ('clusterrolebindings', 'clusterrolebinding',  'clusterrolebindings'),
            ('nodes',               'node',                'nodes'),
        ]
        for info_key, resource, backup_dir in cluster_types:
            items = k8s_info.get(info_key, [])
            type_files = {}
            for item in items:
                name = item.get('name', '') if isinstance(item, dict) else str(item)
                if not name:
                    continue
                yaml_out = self.run_command(
                    f'kubectl get {resource} {name} -o yaml 2>/dev/null'
                )
                if yaml_out and yaml_out.strip():
                    type_files[f'{name}.yaml'] = yaml_out
            if type_files:
                backups[backup_dir] = type_files

        # Namespaces (stored as list of strings)
        ns_files: Dict[str, str] = {}
        for ns_name in k8s_info.get('namespaces', []):
            if not ns_name:
                continue
            yaml_out = self.run_command(
                f'kubectl get namespace {ns_name} -o yaml 2>/dev/null'
            )
            if yaml_out and yaml_out.strip():
                ns_files[f'{ns_name}.yaml'] = yaml_out
        if ns_files:
            backups['namespaces'] = ns_files

        return backups

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
