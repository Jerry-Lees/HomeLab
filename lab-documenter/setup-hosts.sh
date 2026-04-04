#!/bin/bash
# setup-hosts.sh
# Installs lab-documenter prerequisites on all Linux hosts
# Packages: lldpd, aptitude (Debian only), dmidecode, lshw
#
# Reads ansible inventory location from labinator's config.yaml,
# fetches it from the dev server via SCP, then runs the playbook.
# Override inventory with: ./setup-hosts.sh /path/to/local/inventory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAYBOOK="$SCRIPT_DIR/ansible/setup-lab-documenter-hosts.yml"
LABINATOR_CONFIG="$REPO_ROOT/labinator/config.yaml"
LAB_DOC_CONFIG="$SCRIPT_DIR/config.json"
TEMP_INVENTORY=$(mktemp /tmp/lab-doc-inventory.XXXXXX)

cleanup() {
    rm -f "$TEMP_INVENTORY"
}
trap cleanup EXIT

# Check dependencies
if ! command -v ansible-playbook &>/dev/null; then
    echo "ERROR: ansible-playbook not found. Install with: apt install ansible"
    exit 1
fi

if [ ! -f "$PLAYBOOK" ]; then
    echo "ERROR: Playbook not found: $PLAYBOOK"
    exit 1
fi

# Determine inventory — local override or fetch from dev server
if [ -n "$1" ] && [ -f "$1" ]; then
    cp "$1" "$TEMP_INVENTORY"
    echo "Inventory: $1 (local)"
elif [ -f "$LABINATOR_CONFIG" ] && command -v python3 &>/dev/null; then
    INV_SERVER=$(python3 -c "
import yaml
try:
    c = yaml.safe_load(open('$LABINATOR_CONFIG'))
    print(c.get('ansible_inventory', {}).get('server', ''))
except: print('')
" 2>/dev/null)
    INV_USER=$(python3 -c "
import yaml
try:
    c = yaml.safe_load(open('$LABINATOR_CONFIG'))
    print(c.get('ansible_inventory', {}).get('user', 'root'))
except: print('root')
" 2>/dev/null)
    INV_FILE=$(python3 -c "
import yaml
try:
    c = yaml.safe_load(open('$LABINATOR_CONFIG'))
    print(c.get('ansible_inventory', {}).get('file', ''))
except: print('')
" 2>/dev/null)

    if [ -z "$INV_SERVER" ] || [ -z "$INV_FILE" ]; then
        echo "ERROR: Could not read ansible_inventory settings from $LABINATOR_CONFIG"
        echo "Usage: $0 /path/to/local/inventory"
        exit 1
    fi

    echo "Fetching inventory from ${INV_USER}@${INV_SERVER}:${INV_FILE} ..."
    if ! scp -q -o StrictHostKeyChecking=no "${INV_USER}@${INV_SERVER}:${INV_FILE}" "$TEMP_INVENTORY"; then
        echo "ERROR: Could not fetch inventory from ${INV_SERVER}"
        exit 1
    fi
    echo "Inventory: fetched from ${INV_SERVER}"
else
    echo "ERROR: No inventory specified and labinator config not found."
    echo "Usage: $0 /path/to/ansible/inventory"
    exit 1
fi

# Read SSH settings from lab-documenter config.json
SSH_USER="root"
SSH_KEY=""

if [ -f "$LAB_DOC_CONFIG" ] && command -v python3 &>/dev/null; then
    SSH_USER=$(python3 -c "
import json
try:
    c = json.load(open('$LAB_DOC_CONFIG'))
    print(c.get('ssh_user', 'root'))
except: print('root')
" 2>/dev/null)
    SSH_KEY=$(python3 -c "
import json
try:
    c = json.load(open('$LAB_DOC_CONFIG'))
    print(c.get('ssh_key_path', ''))
except: print('')
" 2>/dev/null)
fi

echo "SSH user:  $SSH_USER"
if [ -n "$SSH_KEY" ]; then
    echo "SSH key:   $SSH_KEY"
fi
echo ""

EXTRA_ARGS="-e ansible_user=$SSH_USER"
if [ -n "$SSH_KEY" ]; then
    EXTRA_ARGS="$EXTRA_ARGS -e ansible_ssh_private_key_file=$SSH_KEY"
fi

ansible-playbook -i "$TEMP_INVENTORY" "$PLAYBOOK" $EXTRA_ARGS \
    --ssh-extra-args="-o StrictHostKeyChecking=no" \
    "${@:2}"
