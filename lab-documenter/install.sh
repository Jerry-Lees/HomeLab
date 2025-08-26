#!/bin/bash
set -e

# Lab Documenter Installation Script
# This script sets up the complete lab documentation system in the current directory

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
INSTALL_DIR="$(pwd)"
SERVICE_USER="$(logname 2>/dev/null || echo $SUDO_USER || whoami)"
LOG_DIR="$INSTALL_DIR/logs"

# Functions
print_header() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}         Lab Documenter Installation${NC}"
    echo -e "${BLUE}================================================${NC}"
    echo
}

print_step() {
    echo -e "${GREEN}[STEP]${NC} $1"
}

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    # No longer require root since we're installing locally
    if [[ $EUID -eq 0 ]]; then
        print_warning "Running as root. Consider running as regular user instead."
        SERVICE_USER="root"
    fi
}

detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$NAME
        VER=$VERSION_ID
    else
        print_error "Cannot detect operating system"
        exit 1
    fi
    print_info "Detected OS: $OS $VER"
}

install_dependencies() {
    print_step "Checking system dependencies..."
    
    # Check if we need sudo for package installation
    NEED_SUDO=""
    if [[ $EUID -ne 0 ]]; then
        NEED_SUDO="sudo"
    fi
    
    if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        print_info "Installing system packages..."
        $NEED_SUDO apt-get update
        $NEED_SUDO apt-get install -y python3 python3-pip python3-venv git openssh-client iputils-ping
    elif [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]] || [[ "$OS" == *"Rocky"* ]]; then
        print_info "Installing system packages..."
        $NEED_SUDO yum update -y
        $NEED_SUDO yum install -y python3 python3-pip git openssh-clients iputils
    elif [[ "$OS" == *"Fedora"* ]]; then
        print_info "Installing system packages..."
        $NEED_SUDO dnf update -y
        $NEED_SUDO dnf install -y python3 python3-pip git openssh-clients iputils
    else
        print_warning "Unknown OS. You may need to install dependencies manually:"
        print_info "Required: python3, python3-pip, git, openssh-client, ping"
    fi
}

create_user() {
    print_step "Setting up user environment..."
    print_info "Using current user: $SERVICE_USER"
    print_info "Installation directory: $INSTALL_DIR"
}

setup_directories() {
    print_step "Setting up directories..."
    
    # Create log directory
    mkdir -p $LOG_DIR
    
    print_info "Created directories: $LOG_DIR"
}

install_python_deps() {
    print_step "Installing Python dependencies..."
    
    # Create requirements.txt
    cat > requirements.txt << 'EOF'
paramiko>=2.11.0
requests>=2.28.0
ipaddress>=1.0.23
EOF
    
    # Install in virtual environment
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
    
    print_info "Python dependencies installed in virtual environment"
}

create_config_files() {
    print_step "Creating configuration files..."
    
    # Create config.json
    cat > config.json << EOF
{
    "ssh_user": "$SERVICE_USER",
    "ssh_key_path": "$INSTALL_DIR/.ssh/homelab_key",
    "network_range": "192.168.1.0/24",
    "ssh_timeout": 10,
    "max_workers": 5,
    "output_file": "$INSTALL_DIR/inventory.json",
    "csv_file": "$INSTALL_DIR/servers.csv",
    "mediawiki_api": "http://wiki.homelab.local/api.php",
    "mediawiki_user": "documentation_bot",
    "mediawiki_password": "change_me"
}
EOF
    
    # Create sample servers.csv if it doesn't exist
    if [ ! -f "servers.csv" ]; then
        cat > servers.csv << 'EOF'
hostname,description,role,location
# Add your servers here
# server1.homelab.local,Main file server,NAS,Rack 1
# k8s-master.homelab.local,Kubernetes master node,K8s Master,Rack 1
# proxmox1.homelab.local,Proxmox hypervisor,Virtualization,Rack 2
# 192.168.1.100,Docker host,Container Host,Rack 1
EOF
        print_info "Created sample servers.csv"
    else
        print_info "servers.csv already exists, keeping existing file"
    fi
    
    print_info "Configuration files created"
}

install_main_script() {
    print_step "Setting up lab-documenter.py..."
    
    # Check if script exists in current directory
    if [ -f "./lab-documenter.py" ]; then
        chmod +x ./lab-documenter.py
        print_info "lab-documenter.py found and made executable"
    else
        print_warning "lab-documenter.py not found in current directory"
        print_info "Please place the script in this directory"
        touch lab-documenter.py
    fi
}

setup_cron_job() {
    print_step "Setting up cron job..."
    
    # Create wrapper script for cron
    cat > lab-documenter-cron.sh << EOF
#!/bin/bash
# Lab Documenter cron wrapper script

cd "$INSTALL_DIR"
./venv/bin/python ./lab-documenter.py --scan >> ./logs/cron.log 2>&1
EOF
    
    chmod +x lab-documenter-cron.sh
    
    # Add cron job (runs daily at 6 AM)
    CRON_JOB="0 6 * * * $INSTALL_DIR/lab-documenter-cron.sh"
    
    # Check if cron job already exists
    if crontab -l 2>/dev/null | grep -q "lab-documenter-cron.sh"; then
        print_info "Cron job already exists"
    else
        # Add cron job
        (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
        print_info "Added daily cron job (runs at 6 AM)"
    fi
    
    print_info "Cron job configured to run: $INSTALL_DIR/lab-documenter-cron.sh"
}

setup_ssh_key() {
    print_step "Setting up SSH key..."
    
    SSH_DIR="$INSTALL_DIR/.ssh"
    mkdir -p $SSH_DIR
    chmod 700 $SSH_DIR
    
    # Generate homelab_key if it doesn't exist
    SSH_KEY_PATH="$SSH_DIR/homelab_key"
    if [ ! -f "$SSH_KEY_PATH" ]; then
        print_info "Generating SSH key pair (homelab_key)..."
        ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "lab-documenter@$(hostname)"
        print_info "SSH key generated at $SSH_KEY_PATH"
    else
        print_info "SSH key already exists at $SSH_KEY_PATH"
    fi
    
    # Set proper permissions
    chmod 600 "$SSH_KEY_PATH"
    chmod 644 "$SSH_KEY_PATH.pub"
    
    # Add to ssh-agent if running
    print_info "Adding SSH key to ssh-agent..."
    if pgrep -u $USER ssh-agent > /dev/null; then
        ssh-add "$SSH_KEY_PATH" 2>/dev/null || true
        print_info "SSH key added to ssh-agent"
    else
        print_info "No ssh-agent running, key not added"
    fi
    
    print_warning "IMPORTANT: You need to copy the public key to your servers:"
    print_info "Public key location: $SSH_KEY_PATH.pub"
    print_info "Copy this key to ~/.ssh/authorized_keys on each server you want to document"
    echo
    print_info "Quick copy commands:"
    echo "  ssh-copy-id -i $SSH_KEY_PATH.pub user@your-server"
    echo "  # OR manually:"
    echo "  cat $SSH_KEY_PATH.pub | ssh user@server 'cat >> ~/.ssh/authorized_keys'"
}

create_helper_scripts() {
    print_step "Creating helper scripts..."
    
    # Create ssh-agent setup script
    cat > setup-ssh-agent.sh << 'EOF'
#!/bin/bash
# Helper script to add lab-documenter SSH key to your ssh-agent

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY="$CURRENT_DIR/.ssh/homelab_key"

if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found at $SSH_KEY"
    exit 1
fi

# Start ssh-agent if not running
if ! pgrep -u $USER ssh-agent > /dev/null; then
    echo "Starting ssh-agent..."
    eval $(ssh-agent -s)
    echo "export SSH_AUTH_SOCK=$SSH_AUTH_SOCK" >> ~/.bashrc
    echo "export SSH_AGENT_PID=$SSH_AGENT_PID" >> ~/.bashrc
fi

# Add the key
echo "Adding lab-documenter SSH key to ssh-agent..."
ssh-add "$SSH_KEY"

if [ $? -eq 0 ]; then
    echo "SUCCESS: SSH key added to ssh-agent"
    echo "You can now use: ssh-copy-id -i $SSH_KEY.pub user@server"
else
    echo "ERROR: Failed to add SSH key to ssh-agent"
    exit 1
fi
EOF
    
    chmod +x setup-ssh-agent.sh
    
    # Create key distribution helper
    cat > distribute-key.sh << 'EOF'
#!/bin/bash
# Helper script to distribute SSH key to servers

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_KEY_PUB="$CURRENT_DIR/.ssh/homelab_key.pub"

if [ ! -f "$SSH_KEY_PUB" ]; then
    echo "ERROR: Public key not found at $SSH_KEY_PUB"
    exit 1
fi

if [ $# -eq 0 ]; then
    echo "Usage: $0 user@server [user@server2 ...]"
    echo "Example: $0 admin@192.168.1.100 ubuntu@server.local"
    exit 1
fi

echo "Distributing SSH key to servers..."
for server in "$@"; do
    echo "Copying key to: $server"
    ssh-copy-id -i "$SSH_KEY_PUB" "$server"
    if [ $? -eq 0 ]; then
        echo "✓ Successfully copied key to $server"
    else
        echo "✗ Failed to copy key to $server"
    fi
done
EOF
    
    chmod +x distribute-key.sh
    
    # Create run script
    cat > run-lab-documenter.sh << 'EOF'
#!/bin/bash
# Helper script to run lab-documenter with proper environment

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$CURRENT_DIR"

# Activate virtual environment and run
./venv/bin/python ./lab-documenter.py "$@"
EOF
    
    chmod +x run-lab-documenter.sh
    
    print_info "Created helper scripts:"
    print_info "  ./setup-ssh-agent.sh       # Add key to ssh-agent"
    print_info "  ./distribute-key.sh        # Copy key to servers"
    print_info "  ./run-lab-documenter.sh    # Run with proper environment"
}

create_wrapper_script() {
    print_step "Creating system-wide wrapper..."
    
    # Only create system wrapper if running as root
    if [[ $EUID -eq 0 ]]; then
        cat > /usr/local/bin/lab-documenter << EOF
#!/bin/bash
# Lab Documenter system wrapper script

cd "$INSTALL_DIR"
exec ./venv/bin/python ./lab-documenter.py "\$@"
EOF
        
        chmod +x /usr/local/bin/lab-documenter
        print_info "Created system wrapper at /usr/local/bin/lab-documenter"
    else
        print_info "Not running as root, skipping system wrapper creation"
        print_info "Use ./run-lab-documenter.sh to run the script"
    fi
}

setup_logrotate() {
    print_step "Setting up log rotation..."
    
    # Only set up system logrotate if running as root
    if [[ $EUID -eq 0 ]]; then
        cat > /etc/logrotate.d/lab-documenter << EOF
$LOG_DIR/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 0644 $SERVICE_USER $SERVICE_USER
}
EOF
        print_info "System log rotation configured"
    else
        print_info "Not running as root, skipping system log rotation setup"
        print_info "Logs will be stored in $LOG_DIR/"
    fi
}

print_post_install() {
    echo
    print_header
    echo -e "${GREEN}Installation completed successfully!${NC}"
    echo
    print_info "Installation directory: $INSTALL_DIR"
    print_info "Running as user: $SERVICE_USER"
    print_info "Log directory: $LOG_DIR"
    echo
    print_step "Next steps:"
    echo "1. Edit config.json with your network settings"
    echo "2. Add your servers to servers.csv"
    echo "3. Distribute the SSH key to your servers:"
    echo "   ./distribute-key.sh user@server1 user@server2"
    echo "   # OR manually: ssh-copy-id -i .ssh/homelab_key.pub user@server"
    echo "4. Test the script: ./run-lab-documenter.sh --scan"
    echo "5. Check the cron job: crontab -l"
    echo
    print_step "Helper scripts:"
    echo "  ./setup-ssh-agent.sh       # Add SSH key to ssh-agent"
    echo "  ./distribute-key.sh        # Distribute key to multiple servers"
    echo "  ./run-lab-documenter.sh    # Run with proper environment"
    echo
    print_step "Usage examples:"
    echo "  ./run-lab-documenter.sh --scan              # Scan network and create local docs"
    echo "  ./run-lab-documenter.sh --csv-only          # Only scan servers from CSV"
    echo "  ./run-lab-documenter.sh --scan --update-wiki # Scan and update MediaWiki"
    echo
    print_step "Generated files:"
    echo "  ./inventory.json             # Raw data (JSON)"
    echo "  ./documentation/             # Individual server docs (Markdown)"
    echo "  ./documentation/index.md     # Master index of all servers"
    echo "  ./logs/                      # Log files"
    echo
    print_step "Automation:"
    echo "  Cron job runs daily at 6 AM: $INSTALL_DIR/lab-documenter-cron.sh"
    echo "  View cron logs: tail -f ./logs/cron.log"
    echo "  Edit cron schedule: crontab -e"
    echo
    print_info "SSH public key: $INSTALL_DIR/.ssh/homelab_key.pub"
    echo
}

# Main installation process
main() {
    print_header
    
    check_root
    detect_os
    
    print_info "Starting installation in: $INSTALL_DIR"
    print_info "Running as user: $SERVICE_USER"
    echo
    
    install_dependencies
    create_user
    setup_directories
    install_python_deps
    create_config_files
    install_main_script
    setup_cron_job
    setup_ssh_key
    create_helper_scripts
    create_wrapper_script
    setup_logrotate
    
    print_post_install
}

# Handle command line arguments
case "${1:-}" in
    --uninstall)
        print_step "Uninstalling lab-documenter..."
        
        # Remove cron job
        crontab -l 2>/dev/null | grep -v "lab-documenter-cron.sh" | crontab - || true
        
        # Remove system files if they exist
        if [[ $EUID -eq 0 ]]; then
            rm -f /usr/local/bin/lab-documenter
            rm -f /etc/logrotate.d/lab-documenter
        fi
        
        # Remove local files (be careful here)
        read -p "Remove all files in current directory? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf venv/ logs/ .ssh/
            rm -f lab-documenter-cron.sh setup-ssh-agent.sh distribute-key.sh run-lab-documenter.sh
            rm -f requirements.txt inventory.json
            rm -rf documentation/
            print_info "Local files removed"
        else
            print_info "Kept local files, only removed system integration"
        fi
        
        print_info "Lab Documenter uninstalled"
        ;;
    --help|-h)
        echo "Lab Documenter Installation Script"
        echo
        echo "Usage: $0 [OPTIONS]"
        echo
        echo "Options:"
        echo "  (no options)  Install lab-documenter in current directory"
        echo "  --uninstall   Remove lab-documenter"
        echo "  --help, -h    Show this help message"
        echo
        echo "This installer sets up lab-documenter in the current directory."
        echo "Run from the directory where you want to install it."
        ;;
    *)
        main
        ;;
esac

