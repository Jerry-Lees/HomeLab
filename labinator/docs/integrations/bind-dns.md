[← Back to README](../../README.md)

# BIND DNS Integration

### About

Labinator registers and removes DNS A records (and PTR records where reverse zones exist)
on a BIND DNS server via SSH. This happens automatically at the end of every deployment
and at the start of every decommission.

---

## How it Works

Labinator SSHes to the DNS server and runs a small Python script (written to `/tmp/` on
the DNS server, executed, then deleted) that directly modifies the BIND zone files and
reloads the service. No BIND API or `nsupdate` is used — zone files are edited in place.

### Deploy (A record registration)

Runs via the Ansible playbook `ansible/register-dns.yml`:

1. Writes a Python script to `/tmp/add_dns_forward.py` on the DNS server.
2. Runs it — inserts an A record into the forward zone file.
3. Writes a Python script to `/tmp/add_dns_reverse.py`.
4. Runs it — inserts a PTR record into the reverse zone file (if it exists).
5. Reloads BIND (`rndc reload` or `systemctl reload bind9`).
6. Cleans up both scripts.

### Decommission (record removal)

Runs via `ansible/remove-dns.yml` — same pattern in reverse.

---

## Configuration

Controlled by the `dns` section in `config.yaml`:

```yaml
dns:
  enabled: true
  provider: bind
  server: 192.168.1.4
  ssh_user: root
  forward_zone_file: /var/lib/bind/example.com.hosts
```

See `docs/specs/config-schema.md` for full field descriptions.

---

## Forward Zone

The forward zone file path is specified explicitly in `config.yaml` as
`dns.forward_zone_file`. The A record is inserted as:

```
<hostname>    IN    A    <ip_address>
```

---

## Reverse Zone

The reverse zone file path is **derived automatically** from the deployed IP address at
runtime. It is not configured in `config.yaml`.

**Derivation logic:** For IP `10.220.220.141`, the reverse zone file is looked up at:
```
/var/lib/bind/220.220.10.in-addr.arpa.hosts
```

If this file does not exist on the DNS server, the PTR record step is skipped with a
warning:
```
Reverse zone file /var/lib/bind/220.220.10.in-addr.arpa.hosts not found — skipping PTR record
```

This is **expected behavior** if reverse zones have not been configured on your BIND
server. It is not a labinator bug. The forward A record is always registered regardless.

**To enable PTR records:** Create the reverse zone files on the BIND server for each
subnet in use. For subnet `10.220.220.0/24`, create:
```
/var/lib/bind/220.220.10.in-addr.arpa.hosts
```

---

## DHCP Deployments and DNS

For DHCP-deployed resources, the IP is not known at the start of deployment. Labinator
waits for the DHCP lease to be discovered (via qemu-guest-agent for VMs, or polling for
LXC), then stores the discovered IP in `assigned_ip` in the deployment file.

DNS registration always uses `assigned_ip` (falling back to `ip_address` if
`assigned_ip` is not set). Decommission scripts do the same — this ensures DNS cleanup
works correctly for DHCP deployments even if the IP was never static.

---

## Known Limitations

- **No reverse zones by default** — PTR records are skipped unless reverse zone files
  exist on the BIND server. This is an infrastructure gap, not a labinator limitation.
- **Direct zone file editing** — labinator modifies zone files in place rather than using
  `nsupdate` or the BIND API. This is intentional for simplicity and compatibility with
  plain BIND installations, but means BIND must be reloaded after each change.
- **Single provider** — only BIND is currently supported. PowerDNS, Technitium, and
  other providers are planned as future additions via the `dns.provider` config field.

---

## Disabling DNS

Set `dns.enabled: false` in `config.yaml` to skip all DNS registration and removal.
DNS records will need to be managed manually.

---

[← Back to README](../../README.md)
