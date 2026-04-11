# Lab Documenter - Feature Ideas

This document contains potential future features for Lab Documenter, organized by implementation difficulty. These are brainstorming ideas - not commitments - to explore possibilities for home lab documentation.

---

## Regressions / Broken Features

There are no known regressions at this time.


## In progress features

These features are currently operational, but are a Work In Progress.

- **Cacti Config Generation** - Export inventory.json as Cacti-compatible configuration (XML/SQL) for automatic device addition


## Easy Features

### Change Detection & Alerting
- **Last-Run Comparison** - Compare current scan with previous inventory.json to highlight what changed (new hosts, removed hosts, service changes, version updates)
- **Change Summary Report** - Generate a "What Changed" report showing additions, removals, and modifications since last run
- **Email Notifications** - Send email summary of scan results and changes via SMTP
- **Scan Duration Tracking** - Log and report how long each scan takes, track performance over time

### Documentation Enhancements
- **Host Groups/Tags** - Add ability to tag hosts (production, testing, lab, critical) in CSV and display groupings
- **Custom Host Notes** - Free-form notes field in CSV that appears in documentation
- **Quick Search Page** - Generate searchable index.html with JavaScript filtering
- **PDF Export** - Convert Markdown documentation to PDF format per host
- **Hardware Age Tracking** - Add "installed_date" field to CSV, calculate and display age in documentation

### Package Repository Documentation
- **Configured Package Repositories** - Document all configured package repositories per host; Debian/Ubuntu via `/etc/apt/sources.list` and `/etc/apt/sources.list.d/`; RHEL/Rocky/Fedora via `/etc/yum.repos.d/`; openSUSE via `zypper repos`; show repo name, URL, enabled/disabled state, and GPG check status; flag disabled or unauthenticated repos

### Service Enhancements  
- **Service URL Auto-Detection** - Detect common service URLs (http://host:port) and create clickable links
- **Service Health Indicators** - Traffic light indicators (green/yellow/red) based on service status
- **Common Ports Database** - Expand to include common port/service mappings beyond current database
- **Service Categories Report** - Summary page showing all web services, all databases, all monitoring tools, etc.

### Network Documentation
- **Subnet Summary** - Generate summary showing how many hosts in each subnet, IP utilization
- **DNS Records Export** - Generate suggested DNS zone file entries for discovered hosts
- **DNS Zone Documentation** - SSH to BIND server, pull existing zone files, and document all A/AAAA/CNAME/MX/PTR records alongside the host that owns each record
- **DHCP Leases & Reservations** - SSH to DHCP server (ISC dhcpd, dnsmasq, pfSense, etc.), pull current leases and static reservations, document alongside MAC/hostname inventory
- **Router/Firewall Rules Summary** - Connect to pfSense, OPNsense, or similar via SSH/API, pull and document firewall rules, NAT rules, and interface assignments in a readable table
- **IP Address History** - Track when hosts change IP addresses
- **Network Inventory CSV** - Export network-focused CSV with IP, MAC, vendor, hostname
- **Cross-Subnet MAC Resolution via SSH Jump Hosts** - For failed hosts where the MAC isn't in the local ARP cache (common for devices on remote VLANs/subnets), SSH to a reachable host on the same subnet, run `ping -c 1 <ip> && arp -n <ip>`, and capture the MAC from there. Config file would need a per-VLAN/subnet jump host mapping (e.g. `192.168.0.0/24: jumphost.example.com`). Could also auto-select a jump host by finding any already-scanned reachable host on the same subnet, avoiding the need for dedicated infrastructure.

### Usability Improvements
- **Progress Bar** - Show real-time progress during scanning (X of Y hosts complete)
- **Web Dashboard** - Simple web UI to view documentation without MediaWiki
- **Configuration Wizard** - Interactive setup script to create config.json
- **Dry-Run Preview** - Show what would be documented without actually connecting
- **Auto-suggest Ignore Candidates** - After a scan, flag hosts that consistently fail SSH (printers, IoT, smart devices) and suggest adding them to ignore.csv, rather than requiring manual identification
- **Ignore List CLI Helper** - Add/remove entries from ignore.csv via a CLI flag (e.g. `--ignore <ip> "reason"`) rather than editing the file by hand

---

## Medium Features

### Advanced Network Discovery
- **VLAN Detection** - Identify and document VLAN memberships for hosts
- **Network Topology Map** - Generate visual network diagram showing connections. Using data already collected (hostnames, IPs, VLANs, platform types, Proxmox/K8s cluster membership), auto-generate a diagram in Graphviz DOT, Mermaid, or Draw.io XML format. Nodes grouped by type (Proxmox cluster, K8s cluster, NAS, BIG-IP pair, standalone Linux, Mac, Windows). Logical topology only (subnet/VLAN grouping) — physical topology requires switch SNMP/CDP/LLDP. Mermaid renders inline in MediaWiki with a plugin; Graphviz outputs PNG/SVG with just `apt install graphviz`. Diagram updates automatically every scan run.
- **Gateway/Router Documentation** - Special handling for network equipment (routers, firewalls, switches)
- **WiFi Network Documentation** - Scan and document wireless networks (SSIDs, channels, encryption)

### Hardware & Environmental Monitoring
- **Temperature Tracking** - Collect and trend CPU/disk temperatures over time
- **Power Consumption** - Integrate with smart PDUs or IPMI for power usage data
- **SMART Data Trending** - Track disk health metrics over time, predict failures
- **Fan Speed Monitoring** - Document and alert on fan failures
- **UPS Integration** - Document UPS status, battery health, runtime estimates

### Security & Compliance
- **SSL/TLS Certificate Tracking** - Discover certificates, track expiration dates, alert on upcoming expirations
- **Open Port Change Detection** - Alert when new ports open or close on hosts
- **Password Age Tracking** - Track when host passwords were last changed (manual entry)
- **Compliance Checklist** - Customizable security baseline checks (SSH keys only, no root login, etc.)

### Proxmox Enhancements
- **Storage Pool Usage** - Document all Proxmox storage pools (local, NFS, Ceph, etc.) with usage, capacity, and type — not just node-level disk info
- **Resource Allocation vs Actual Usage** - Show allocated vCPU/RAM across all VMs/containers vs physical node capacity; surface over-provisioning at a glance
- **Backup Job Status** - Query Proxmox Backup Server or vzdump job history, show last backup date and result per VM/container

### Kubernetes Enhancements
- **Resource Requests vs Limits** - Show CPU/memory requests and limits per namespace, flag namespaces with no limits set

### BIG-IP Enhancements

#### Configuration Documentation
- **iRules** - Document all iRules with full code via `tmsh list ltm rule`; these are often critical and undocumented elsewhere
- **SSL Profiles** - Document client and server SSL profiles including cipher suites and associated certificate names
- **Certificate Inventory** - List all installed certificates with expiry dates via `tmsh list sys crypto cert`; flag certs expiring within 30/60/90 days
- **Health Monitors** - Document all monitor definitions (HTTP, TCP, ICMP, custom) and their parameters via `tmsh list ltm monitor`
- **SNAT Pools & Translations** - Document source NAT pool members and translation addresses
- **LTM Policies** - Document traffic steering policies and their rules
- **Data Groups** - Document named data groups used by iRules (IP lists, string lists, etc.)
- **HTTP/TCP Profiles** - Document per-virtual-server tuning profiles and their key parameters
- **Self IPs** - Document interface self IPs, netmasks, traffic group assignments, and port lockdown settings via `tmsh list net self`
- **Routes** - Document static route table via `tmsh list net route`

#### Health & Status
- **Pool Member Status** - Show current up/down/disabled state per pool member via `tmsh show ltm pool`; highlight degraded or offline members
- **Virtual Server Statistics** - Current connections and bits in/out per virtual server via `tmsh show ltm virtual`
- **SSL Certificate Expiry Alerts** - Flag certificates expiring within configurable thresholds (30/60/90 days) on the host documentation page
- **CPU & Memory Utilization** - System performance snapshot via `tmsh show sys performance`
- **License Expiry** - Document license module list and expiry date via `tmsh list sys license`

#### HA / Clustering
- **Device Group Sync Status** - Current config sync state between HA peers via `tmsh show cm sync-status`
- **Traffic Group Failover State** - Which device owns which traffic groups and current failover state
- **Config Sync Timestamp** - When the last successful config sync occurred

#### Security (if licensed)
- **AFM Firewall Rules** - Document Advanced Firewall Manager policies and rules if AFM is provisioned
- **ASM Policy List** - Document Application Security Manager policies and enforcement modes if ASM is provisioned
- **DoS Profiles** - Document configured DoS protection profiles

#### DNS / GTM (if licensed)
- **Wide IPs** - Document GSLB wide IP definitions and their associated pools
- **GTM Pools & Members** - Document GTM pool members and load balancing method across data centers

#### Backup
- **UCS Archive Backup** - Generate a UCS archive via `tmsh save sys ucs /var/tmp/<hostname>.ucs`, download it via SCP/SFTP, and store in `backups/bigip/<hostname>/` alongside a timestamp; provides a complete configuration backup restorable via `tmsh load sys ucs`

### Backup & Recovery Documentation
- **Backup Solution Integration** - Query Veeam, Proxmox Backup Server, or Restic for backup status
- **Recovery Point Objectives** - Document and verify backup schedules
- **Backup Size Tracking** - Monitor backup storage consumption over time
- **Disaster Recovery Playbook** - Auto-generate DR procedures based on dependencies

### External Integrations
- **Uptime Kuma Integration** - Query Uptime Kuma REST API for monitor status per host; display current up/down state and response time on each host's documentation page; link directly to the relevant Uptime Kuma monitor; match monitors to hosts by hostname or IP
- **Docker Hub Image Currency** - For each running container collected via SSH, query the Docker Hub API (or GitHub Container Registry / GHCR for ghcr.io images) to compare the running image tag/digest against the latest available; flag outdated containers on the host documentation page with the current tag, latest available tag, and how far behind they are; support pinned tags (e.g. `nginx:1.25`) as well as floating tags (e.g. `nginx:latest`); respect private registries where credentials are configured; rate limiting handled via Docker Hub's unauthenticated (100 pulls/6hr) or authenticated (200 pulls/6hr) tier — cache results within a scan run to avoid redundant API calls
- **Prometheus Integration** - Query Prometheus for metrics, incorporate into documentation
- **Grafana Dashboard Links** - Auto-discover and link to relevant Grafana dashboards
- **Zabbix Host Import** - Generate Zabbix host definitions from discovered infrastructure
- **Nagios/Icinga Configuration** - Export monitoring configurations for Nagios-compatible systems
- **Git Version Control** - Commit documentation to Git repository automatically after each run
- **Webhook Notifications** - POST scan results to arbitrary webhooks (Slack, Discord, Teams, custom)
- **Calendar Integration** - Track maintenance windows, scheduled reboots, planned changes

### Advanced Service Discovery
- **Database Discovery** - Connect to databases, document schemas, table sizes, user counts
- **Web Application Detection** - Identify web frameworks (WordPress, GitLab, Nextcloud) and versions
- **API Documentation** - Discover and document REST/GraphQL APIs
- **Message Queue Detection** - Document RabbitMQ, Kafka, Redis configurations

---

## Challenging Features

### Intelligent Analysis & AI
- **Anomaly Detection** - Use ML to detect unusual patterns (high CPU, abnormal network traffic, service outages)
- **Predictive Maintenance** - Analyze trends to predict failures (disk fills in 30 days, certificate expires soon)
- **Capacity Planning** - Recommend infrastructure upgrades based on growth trends
- **Configuration Drift Detection** - Identify hosts that have drifted from standard configurations
- **Natural Language Queries** - Ask questions like "Which hosts are running out of disk space?"
- **Automated Remediation Suggestions** - Suggest fixes for detected issues

### Network Topology & Visualization
- **Auto-Generated Network Diagrams** - Create Visio/draw.io diagrams showing physical and logical topology
- **Layer 2/3 Discovery** - Map complete network topology using SNMP, CDP, LLDP, ARP tables
- **Rack Elevation Diagrams** - Visual representation of equipment in racks
- **Cable Management Documentation** - Track physical connections, cable types, lengths
- **Interactive Network Map** - Web-based clickable topology with real-time status

### Dependency Mapping & Impact Analysis
- **Service Dependency Graph** - Map which services depend on which (DB depends on NAS, web depends on DB)
- **Failure Impact Analysis** - "If this host fails, what breaks?"
- **Startup Order Documentation** - Determine correct boot sequence for infrastructure
- **Circular Dependency Detection** - Identify and warn about circular dependencies
- **Change Impact Prediction** - "If I upgrade this, what else is affected?"

### Advanced Monitoring Integration
- **Multi-Source Metric Aggregation** - Combine data from Prometheus, Grafana, Zabbix, PRTG, etc.
- **Historical Performance Trending** - Store and analyze months/years of performance data
- **SLA Monitoring** - Track uptime, calculate availability percentages
- **Cost Tracking** - Integrate with cloud providers for cost data, track on-prem TCO
- **Energy Efficiency Reporting** - Calculate and optimize power usage effectiveness (PUE)

### Automation & Orchestration
- **Configuration Management Integration** - Document Ansible, Puppet, Chef, Salt configurations
- **Infrastructure as Code Documentation** - Parse and document Terraform, CloudFormation, Pulumi
- **Automated Patching Documentation** - Track patch levels, generate patch plans
- **Compliance as Code** - Automated compliance checking with policy-as-code (OPA, Sentinel)
- **Self-Healing Documentation** - Auto-update documentation when infrastructure changes detected

### Enterprise Features
- **Multi-Site Support** - Document multiple physical locations, data centers, or cloud regions
- **RBAC for Documentation** - Role-based access control for who can view what documentation
- **Audit Trail** - Complete history of all documentation changes with attribution
- **Documentation API** - RESTful API to query infrastructure data programmatically
- **Custom Dashboards** - Drag-and-drop dashboard builder for stakeholders
- **Report Scheduling** - Automated report generation and distribution

### Advanced Platform Support
- **Cloud Provider Integration** - Document AWS, Azure, GCP resources alongside on-prem
- **Container Runtime Support** - Podman, containerd, CRI-O beyond just Docker
- **Hypervisor Expansion** - VMware ESXi, Hyper-V, Xen, KVM/QEMU direct integration
- **Storage Systems** - TrueNAS API, Synology API, QNAP API for deeper storage insights
- **Networking Equipment** - Ubiquiti UniFi, Cisco, Juniper, MikroTik SNMP/API integration
- **Ubiquiti UniFi Integration** - Query UniFi Controller REST API (port 443/8443) for APs, switches, port configs, connected clients with MACs/IPs/VLANs, and VLAN definitions; cross-reference with scanned host inventory; `pyunifi` or `aiounifi` Python libraries available
- **Cisco Nexus API Integration** - Query NX-API (REST/JSON) on Nexus switches for interface status/speed/duplex, VLAN assignments, CDP/LLDP neighbors (complements per-host lldpd data), and port-channel/vPC configs; falls back to SSH CLI scraping if NX-API (`feature nxapi`) is not enabled
- **Netgear Orbi Integration** *(very low priority)* - Pull connected client inventory (hostname, MAC, IP, connection type) and basic router status via the reverse-engineered SOAP/HNAP1 interface using `pynetgear`; satellites are not individually queryable — all data flows through the router; no official API, prone to breaking on firmware updates

### Novel Capabilities
- **Time-Series Database Integration** - Store all metrics in InfluxDB/TimescaleDB for unlimited history
- **Voice Assistant Integration** - "Alexa, what's the status of my home lab?"
- **Mobile App** - Companion mobile app for on-the-go infrastructure visibility
- **AR/VR Visualization** - Augmented reality view of infrastructure layout
- **Blockchain Audit Log** - Immutable audit trail for compliance-heavy environments
- **Federated Documentation** - Multiple Lab Documenter instances sharing data across organizations

---

## Infrastructure Modernization Ideas

### Architecture Improvements
- **Plugin System** - Allow third-party plugins for custom collectors and documentation formats
- **Event-Driven Architecture** - Real-time updates via message queue instead of periodic scanning
- **Microservices** - Split into smaller services (scanner, collector, renderer, API)
- **Unify Wiki Template Structure** - `server.wiki.j2` is a standalone file while `server.md.j2` extends `base/server_base.md.j2`. Refactor the wiki side to use a base template (`base/server_base.wiki.j2`) for consistency. Low risk but cosmetic — do carefully to avoid breaking existing output.
- **GraphQL API** - Modern API for querying infrastructure data
- **WebSocket Support** - Real-time updates to documentation viewers

### Data Storage Evolution
- **Time-Series Storage** - Move from JSON files to proper time-series database
- **Graph Database** - Store relationships in Neo4j for dependency analysis
- **Caching Layer** - Redis cache for frequently accessed data
- **Search Engine** - Elasticsearch integration for full-text search
- **Data Lake** - Store raw metrics for advanced analytics

---

## Community & Ecosystem

### Collaboration Features
- **Template Marketplace** - Community-contributed Jinja2 templates
- **Plugin Repository** - Share custom collectors and integrations
- **Best Practices Database** - Crowdsourced service descriptions and configurations
- **Vendor Integration Partners** - Official integrations with hardware/software vendors
- **Certification Program** - Training and certification for Lab Documenter experts

---

## Notes

**Implementation Priority:**
- Focus on features that provide immediate value with minimal complexity
- Leverage existing infrastructure and patterns when possible
- Consider user demand and common pain points
- Balance new features with maintenance and stability

**Philosophy:**
- Keep the core simple and extensible
- Don't add features that require external services unless optional
- Maintain backward compatibility
- Prefer auto-learning over manual configuration

**User Feedback:**
- These ideas should be validated with actual users
- Some "challenging" features may not be needed
- Some "easy" features may be more impactful than complex ones

---

*This is a living document. Ideas will be added, removed, or reprioritized based on user feedback and project evolution.*

