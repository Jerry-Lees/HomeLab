#!/usr/bin/env bash
# ============================================================
# vm-onboard setup script
# Installs ALL required system packages and Python dependencies
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
fail() { echo -e "${RED}✗ ERROR:${NC} $*"; exit 1; }
section() { echo ""; echo "─── $* ───"; }

echo "=============================================="
echo "  vm-onboard — Dependency Setup"
echo "=============================================="

# ──────────────────────────────────────────────
# 1. System packages required before pip can work
# ──────────────────────────────────────────────
section "System packages"

# Ensure apt cache is reasonably fresh (only update if > 1 hour old)
if [ "$(find /var/cache/apt/pkgcache.bin -mmin +60 2>/dev/null | wc -l)" -gt 0 ] || \
   [ ! -f /var/cache/apt/pkgcache.bin ]; then
    echo "Updating apt cache..."
    sudo apt-get update -qq
fi

SYSTEM_PKGS=(
    # Python runtime and build toolchain
    python3
    python3-pip
    python3-venv
    python3-dev          # needed by some pip packages that compile C extensions
    build-essential      # gcc, make — needed if pip must build from source

    # SSL/crypto libs — paramiko depends on these
    libssl-dev
    libffi-dev

    # Ansible
    ansible

    # sshpass — lets Ansible authenticate with passwords on first connect
    sshpass

    # SSH client (for the bootstrap pct exec step)
    openssh-client
)

MISSING_PKGS=()
for pkg in "${SYSTEM_PKGS[@]}"; do
    if dpkg -s "$pkg" &>/dev/null 2>&1; then
        ok "$pkg (already installed)"
    else
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo ""
    echo "Installing missing system packages: ${MISSING_PKGS[*]}"
    sudo apt-get install -y "${MISSING_PKGS[@]}"
    echo ""
    for pkg in "${MISSING_PKGS[@]}"; do
        if dpkg -s "$pkg" &>/dev/null 2>&1; then
            ok "$pkg installed"
        else
            fail "Failed to install system package: $pkg"
        fi
    done
fi

# ──────────────────────────────────────────────
# 2. Python version check
# ──────────────────────────────────────────────
section "Python version"

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Python 3.10+ is required. Found: Python $PY_VERSION"
fi
ok "Python $PY_VERSION"

# ──────────────────────────────────────────────
# 3. Python virtualenv
# ──────────────────────────────────────────────
section "Python virtual environment"

VENV_DIR=".venv"

if [ -d "$VENV_DIR" ]; then
    ok "Virtualenv already exists at $VENV_DIR"
else
    echo "Creating virtualenv at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
    ok "Virtualenv created"
fi

PIP="$VENV_DIR/bin/pip"
PYTHON="$VENV_DIR/bin/python3"

# Upgrade pip itself first (old pip can fail on some packages)
echo "Upgrading pip inside virtualenv..."
"$PIP" install --upgrade pip --quiet
ok "pip upgraded: $($PIP --version)"

# ──────────────────────────────────────────────
# 4. Install Python packages from requirements.txt
# ──────────────────────────────────────────────
section "Python packages (from requirements.txt)"

if [ ! -f requirements.txt ]; then
    fail "requirements.txt not found. Run this script from the vm-onboard directory."
fi

echo "Installing packages — this may take a minute..."
echo ""

# Install without -q so every package install/version is visible
"$PIP" install -r requirements.txt

echo ""

# ──────────────────────────────────────────────
# 5. Verify every required Python module imports
# ──────────────────────────────────────────────
section "Verifying Python imports"

MODULES=(
    "proxmoxer:proxmoxer"
    "paramiko:paramiko"
    "questionary:questionary"
    "rich:rich"
    "yaml:PyYAML"
    "requests:requests"
    "urllib3:urllib3"
)

ALL_OK=true
for entry in "${MODULES[@]}"; do
    module="${entry%%:*}"
    pkg="${entry##*:}"
    if "$PYTHON" -c "import $module" 2>/dev/null; then
        VERSION=$("$PYTHON" -c "import $module; print(getattr($module, '__version__', 'ok'))" 2>/dev/null || echo "ok")
        ok "$pkg ($VERSION)"
    else
        echo -e "${RED}✗${NC} $pkg — FAILED to import '$module'"
        ALL_OK=false
    fi
done

if [ "$ALL_OK" = false ]; then
    fail "One or more Python packages failed to import. See above."
fi

# ──────────────────────────────────────────────
# 6. Verify system tools
# ──────────────────────────────────────────────
section "System tools"

ok "ansible:      $(ansible --version | head -1)"
ok "sshpass:      $(sshpass -V 2>&1 | head -1)"
ok "ssh client:   $(ssh -V 2>&1)"

# ──────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────
echo ""
echo "=============================================="
echo -e "  ${GREEN}Setup complete — all dependencies installed${NC}"
echo "=============================================="
echo ""
echo "Next steps:"
echo "  1. Create config.yaml from the example:"
echo "       cp config.yaml.example config.yaml"
echo "     Then edit config.yaml:"
echo "     → Set proxmox.token_secret  (your Proxmox API token secret)"
echo "     → Set proxmox.ssh_key       (path to SSH key authorized on all Proxmox nodes)"
echo ""
echo "  2. Authorize your SSH key on all Proxmox nodes:"
NODE_CMDS=$("$PYTHON" -c "
import yaml, sys
try:
    cfg = yaml.safe_load(open('config.yaml'))
    domain = cfg.get('proxmox', {}).get('node_domain', '')
    nodes = cfg.get('nodes', [])
    for n in nodes:
        host = f'{n}.{domain}' if domain else n
        print(f'       ssh-copy-id root@{host}')
except Exception:
    pass
" 2>/dev/null)
if [ -n "$NODE_CMDS" ]; then
    echo "$NODE_CMDS"
else
    echo "       ssh-copy-id root@<proxmox-node>.<your-domain>"
    echo "       (for each node listed under nodes: in config.yaml)"
fi
echo ""
echo "  3. Run the deploy wizard:"
echo "       source .venv/bin/activate"
echo "       python3 deploy_lxc.py"
echo ""
echo "  4. To decommission a container:"
echo "       python3 decomm_lxc.py"
echo "       python3 decomm_lxc.py --purge   # also deletes local deployment file"
echo ""
