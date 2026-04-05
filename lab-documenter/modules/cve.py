"""
CVE vulnerability scanning via Trivy for Lab Documenter.

Scans collected package lists against the Trivy vulnerability database.
Results are cached by (os_id, version_id, pkg_name, pkg_version) so that
packages shared across multiple hosts are only looked up once per scan run.
"""

import json
import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# OS families → package type for PURL generation
_DEB_IDS = {'ubuntu', 'debian', 'raspbian', 'pop', 'linuxmint', 'elementary', 'kali'}
_RPM_IDS = {'rhel', 'centos', 'rocky', 'almalinux', 'fedora', 'ol', 'amzn', 'scientific'}
_SUSE_IDS = {'opensuse', 'opensuse-leap', 'opensuse-tumbleweed', 'sles', 'sle'}

_SEVERITY_ORDER = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3, 'UNKNOWN': 4}

# Trivy status values that warrant visual attention
_WARN_STATUSES = {'will_not_fix', 'end_of_life', 'fix_deferred'}


class CVEScanner:
    def __init__(self):
        # Cache: {(os_id, version_id, pkg_name, pkg_version): [vuln_dicts]}
        self._cache: Dict[Tuple, List[Dict]] = {}
        self._db_updated = False
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is None:
            result = subprocess.run(['which', 'trivy'], capture_output=True, text=True)
            self._available = result.returncode == 0
            if not self._available:
                logger.warning("trivy not found — CVE scanning disabled. Run install.sh to install.")
        return self._available

    def update_db(self):
        """
        Update Trivy vulnerability database. Called once per scan run.
        Trivy v0.51+ auto-updates before each scan — no explicit command needed.
        For older versions, try 'trivy db update' and fall back gracefully.
        """
        if not self.is_available() or self._db_updated:
            return
        self._db_updated = True
        try:
            result = subprocess.run(
                ['trivy', 'db', 'update'],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                logger.info("Trivy DB updated successfully")
            elif 'unknown command' in result.stderr or 'unknown command' in result.stdout:
                logger.info("Trivy DB auto-updates before each scan (v0.51+ behavior)")
            else:
                logger.warning(f"Trivy DB update: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            logger.warning("Trivy DB update timed out — using existing DB")
        except Exception as e:
            logger.warning(f"Trivy DB update skipped: {e}")

    def _pkg_type(self, os_id: str, id_like: str = '') -> Optional[str]:
        """Determine package type (deb/rpm) from OS ID."""
        ids_to_check = [os_id.lower()] + id_like.lower().split()
        for oid in ids_to_check:
            if oid in _DEB_IDS or 'debian' in oid or 'ubuntu' in oid:
                return 'deb'
            if oid in _RPM_IDS or 'rhel' in oid or 'centos' in oid or 'rocky' in oid:
                return 'rpm'
            if oid in _SUSE_IDS or 'suse' in oid:
                return 'rpm'
        return None

    def _make_purl(self, name: str, version: str, os_id: str, version_id: str, pkg_type: str) -> str:
        distro = f"{os_id}-{version_id}".lower()
        return f"pkg:{pkg_type}/{os_id.lower()}/{name}@{version}?distro={distro}"

    def _generate_sbom(self, packages: List[Dict], os_id: str, version_id: str, pkg_type: str) -> Dict:
        """Generate a CycloneDX 1.4 SBOM from a package list."""
        components = []
        for pkg in packages:
            name = pkg.get('name', '').strip()
            version = pkg.get('version', '').strip()
            if not name or not version:
                continue
            components.append({
                'type': 'library',
                'name': name,
                'version': version,
                'purl': self._make_purl(name, version, os_id, version_id, pkg_type)
            })
        return {
            'bomFormat': 'CycloneDX',
            'specVersion': '1.4',
            'version': 1,
            'metadata': {
                'component': {
                    'type': 'operating-system',
                    'name': os_id.lower(),
                    'version': version_id
                }
            },
            'components': components
        }

    def _run_trivy(self, sbom_data: Dict) -> List[Dict]:
        """Write SBOM to a temp file, run trivy sbom, parse and return vulnerability dicts."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(sbom_data, f)
            sbom_path = f.name

        try:
            result = subprocess.run(
                ['trivy', 'sbom', '--format', 'json', '--quiet', sbom_path],
                capture_output=True, text=True, timeout=180
            )
            # returncode 1 = vulnerabilities found (still valid output)
            if result.returncode not in (0, 1):
                logger.warning(f"Trivy exited {result.returncode}: {result.stderr[:300]}")
                return []

            if not result.stdout.strip():
                return []

            data = json.loads(result.stdout)
            vulns = []
            for entry in data.get('Results', []):
                for v in (entry.get('Vulnerabilities') or []):
                    score = self._extract_cvss(v.get('CVSS', {}))
                    status = v.get('Status', 'affected').lower().replace(' ', '_')
                    refs = v.get('References') or []
                    vulns.append({
                        'pkg_name':      v.get('PkgName', ''),
                        'pkg_version':   v.get('InstalledVersion', ''),
                        'vuln_id':       v.get('VulnerabilityID', ''),
                        'severity':      v.get('Severity', 'UNKNOWN'),
                        'cvss_score':    score,
                        'title':         v.get('Title', ''),
                        'description':   v.get('Description', ''),
                        'fixed_version': v.get('FixedVersion', ''),
                        'status':        status,
                        'primary_url':   v.get('PrimaryURL', ''),
                        'references':    refs[:3],
                    })
            return vulns

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Trivy JSON output: {e}")
            return []
        except subprocess.TimeoutExpired:
            logger.warning("Trivy scan timed out")
            return []
        except Exception as e:
            logger.warning(f"Trivy scan failed: {e}")
            return []
        finally:
            try:
                os.unlink(sbom_path)
            except Exception:
                pass

    def _extract_cvss(self, cvss: Dict) -> Optional[float]:
        """Extract best available CVSS v3 (or v2) score from Trivy CVSS dict."""
        for source in ('nvd', 'redhat', 'ghsa', 'alma', 'rocky', 'ubuntu', 'debian'):
            s = cvss.get(source, {})
            score = s.get('V3Score') or s.get('V2Score')
            if score is not None:
                return float(score)
        return None

    def scan_host(self, host_data: Dict) -> Dict:
        """
        Scan a host's packages for CVEs. Returns a cve_data dict suitable for
        template rendering. Uses per-package cache to avoid redundant Trivy calls.
        """
        empty = {'available': False, 'summary': {}, 'vulnerabilities': []}

        if not self.is_available():
            return empty

        os_release = host_data.get('os_release', {})
        os_id = (os_release.get('id') or '').lower()
        id_like = os_release.get('id_like') or ''
        version_id = os_release.get('version_id') or 'unknown'

        pkg_type = self._pkg_type(os_id, id_like)
        if not pkg_type:
            return empty

        packages = host_data.get('installed_packages') or host_data.get('packages') or []
        if not packages:
            return empty

        # Split into cached and uncached packages
        uncached = []
        for pkg in packages:
            name = (pkg.get('name') or '').strip()
            version = (pkg.get('version') or '').strip()
            if not name:
                continue
            if (os_id, version_id, name, version) not in self._cache:
                uncached.append(pkg)

        # Run Trivy only for packages not yet in cache
        if uncached:
            sbom = self._generate_sbom(uncached, os_id, version_id, pkg_type)
            new_vulns = self._run_trivy(sbom)

            # Index results by package
            by_pkg: Dict[Tuple, List] = {}
            for v in new_vulns:
                key = (os_id, version_id, v['pkg_name'], v['pkg_version'])
                by_pkg.setdefault(key, []).append(v)

            # Cache all scanned packages (empty list = no CVEs)
            for pkg in uncached:
                name = (pkg.get('name') or '').strip()
                version = (pkg.get('version') or '').strip()
                key = (os_id, version_id, name, version)
                self._cache[key] = by_pkg.get(key, [])

        # Assemble full CVE list from cache
        all_vulns: List[Dict] = []
        for pkg in packages:
            name = (pkg.get('name') or '').strip()
            version = (pkg.get('version') or '').strip()
            if not name:
                continue
            key = (os_id, version_id, name, version)
            all_vulns.extend(self._cache.get(key, []))

        # Sort: severity first, then CVSS score descending
        all_vulns.sort(key=lambda v: (
            _SEVERITY_ORDER.get(v['severity'], 4),
            -(v['cvss_score'] or 0.0)
        ))

        # Build severity summary
        summary: Dict[str, int] = {}
        for v in all_vulns:
            sev = v['severity']
            summary[sev] = summary.get(sev, 0) + 1

        # Pre-compute display strings so templates need no conditional logic in cells
        for v in all_vulns:
            sev = v['severity']
            status = v['status']
            uid = v['vuln_id']
            url = v['primary_url']
            score = v['cvss_score']
            title = (v['title'] or uid).replace('|', '/').replace('\n', ' ')[:200]
            desc = (v['description'] or '').replace('|', '/').replace('\n', ' ')[:400]
            refs = v['references']

            # Wiki display fields
            if sev in ('CRITICAL', 'HIGH'):
                v['severity_wiki'] = f"<span style=\"color:red\">'''{sev}'''</span>"
            else:
                v['severity_wiki'] = sev

            if status == 'will_not_fix':
                v['status_wiki'] = "<span style=\"color:darkorange\">'''WILL NOT FIX'''</span>"
            elif status == 'end_of_life':
                v['status_wiki'] = "<span style=\"color:darkorange\">'''End of Life'''</span>"
            elif status == 'fix_deferred':
                v['status_wiki'] = "<span style=\"color:darkorange\">Fix Deferred</span>"
            else:
                v['status_wiki'] = status.replace('_', ' ').title()

            v['vuln_id_wiki'] = f"[{url} {uid}]" if url else uid
            v['score_wiki'] = str(score) if score is not None else '-'
            v['fixed_wiki'] = v['fixed_version'] or '-'
            ref_links = '<br/>'.join(f"[{r} {urlparse(r).netloc or r}]" for r in refs)
            v['details_wiki'] = f"'''{title}'''" + (f"<br/><small>{desc}</small>" if desc else '') + (f"<br/><small>{ref_links}</small>" if ref_links else '')

            # Markdown display fields
            if sev in ('CRITICAL', 'HIGH'):
                v['severity_md'] = f'<span style="color:red">**{sev}**</span>'
            else:
                v['severity_md'] = sev

            if status == 'will_not_fix':
                v['status_md'] = '<span style="color:darkorange">**WILL NOT FIX**</span>'
            elif status == 'end_of_life':
                v['status_md'] = '<span style="color:darkorange">**End of Life**</span>'
            elif status == 'fix_deferred':
                v['status_md'] = '<span style="color:darkorange">Fix Deferred</span>'
            else:
                v['status_md'] = status.replace('_', ' ').title()

            v['vuln_id_md'] = f"[{uid}]({url})" if url else uid
            v['score_md'] = str(score) if score is not None else '-'
            v['fixed_md'] = v['fixed_version'] or '-'
            ref_md = '<br>'.join(f"[{urlparse(r).netloc or r}]({r})" for r in refs)
            v['details_md'] = f"**{title}**" + (f"<br><small>{desc}</small>" if desc else '') + (f"<br><small>{ref_md}</small>" if ref_md else '')

        # Pre-compute summary line
        parts_wiki = []
        parts_md = []
        for sev in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'):
            count = summary.get(sev, 0)
            if count == 0:
                continue
            if sev in ('CRITICAL', 'HIGH'):
                parts_wiki.append(f"<span style=\"color:red\">'''{sev.capitalize()}: {count}'''</span>")
                parts_md.append(f'<span style="color:red">**{sev.capitalize()}: {count}**</span>')
            else:
                parts_wiki.append(f"{sev.capitalize()}: {count}")
                parts_md.append(f"{sev.capitalize()}: {count}")

        return {
            'available': True,
            'summary': summary,
            'summary_wiki': ' &nbsp; '.join(parts_wiki),
            'summary_md': ' '.join(parts_md),
            'vulnerabilities': all_vulns,
        }
