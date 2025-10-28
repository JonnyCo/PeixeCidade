#!/bin/bash

# Setup script for Raspberry Pi OS
# Installs uv, tailscale, and updates system packages

set -e  # Exit on any error

echo "Starting Raspberry Pi OS setup..."

# Update package lists
echo "Updating package lists..."
sudo apt update

# Remove Firefox
echo "Removing Firefox..."
sudo apt remove -y firefox firefox-esr

# Upgrade installed packages
echo "Upgrading installed packages..."
sudo apt upgrade -y

# Install prerequisites
echo "Installing prerequisites..."
sudo apt install -y curl wget gpg


# Download and extract the latest Mackerel agent for Linux ARM
curl -sL https://github.com/mackerelio/mackerel-agent/releases/latest/download/mackerel-agent_linux_arm.tar.gz | tar xz

# Create directories and copy files
sudo mkdir -p /usr/local/bin /etc/mackerel-agent
sudo cp mackerel-agent_linux_arm/mackerel-agent /usr/local/bin
sudo cp mackerel-agent_linux_arm/mackerel-agent.conf /etc/mackerel-agent



# Clone PeixeCidade repository
echo "Cloning PeixeCidade..."
git clone https://github.com/JonnyCo/PeixeCidade.git

# Clone and install DynamixelSDK
echo "Cloning DynamixelSDK..."
git clone https://github.com/ROBOTIS-GIT/DynamixelSDK.git
cd DynamixelSDK/python
pip install -e . --break-system-packages
pip install python-osc --break-system-packages



# Install tailscale
echo "Installing tailscale..."
curl -fsSL https://tailscale.com/install.sh | sh

# Connect to tailscale with auth key
echo "Connecting to tailscale network..."
tailscale up --authkey=tskey-auth-kxv9t9qrDW11CNTRL-FADUQDwAKCbqWCj3hYG4CbJjnnPsbgpw

echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Reboot or run 'source ~/.bashrc' to ensure all changes take effect"
echo "2. Tailscale is now connected and authenticated"
echo "3. Each new shell will automatically navigate to /opt/fishbot and activate the uv environment"