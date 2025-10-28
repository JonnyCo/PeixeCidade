#!/bin/bash

# Installation script for fish-osc systemd service
# This script will:
# 1. Make startup script executable
# 2. Install systemd service
# 3. Enable and start the service

set -e  # Exit on any error

# Configuration
SERVICE_NAME="fish-osc"
SERVICE_FILE="fish-osc.service"
STARTUP_SCRIPT="fish-osc-startup.sh"
REPO_DIR="/home/pi/PeixeCidade"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Fish OSC Service Installation ==="
echo

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "ERROR: Do not run this script as root. Run as pi user with sudo."
    exit 1
fi

# Navigate to repository directory
echo "Navigating to $REPO_DIR"
cd "$REPO_DIR" || {
    echo "ERROR: Failed to navigate to $REPO_DIR"
    exit 1
}

# Make startup script executable
echo "Making startup script executable..."
chmod +x "$STARTUP_SCRIPT"

# Copy service file to systemd directory
echo "Installing systemd service..."
sudo cp "$SERVICE_FILE" "$SYSTEMD_DIR/"

# Reload systemd to recognize new service
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable service to start on boot
echo "Enabling service to start on boot..."
sudo systemctl enable "$SERVICE_NAME.service"

# Start the service now
echo "Starting service..."
sudo systemctl start "$SERVICE_NAME.service"

# Wait a moment for service to start
sleep 2

# Check service status
echo
echo "=== Service Status ==="
sudo systemctl status "$SERVICE_NAME.service" --no-pager

echo
echo "=== Installation Complete ==="
echo
echo "Service commands:"
echo "  Check status:     sudo systemctl status $SERVICE_NAME"
echo "  View logs:        sudo journalctl -u $SERVICE_NAME -f"
echo "  Restart service:   sudo systemctl restart $SERVICE_NAME"
echo "  Stop service:      sudo systemctl stop $SERVICE_NAME"
echo "  Disable startup:    sudo systemctl disable $SERVICE_NAME"
echo
echo "Log files location:"
echo "  Service log:      $REPO_DIR/fish-osc-service.log"
echo "  Error log:        $REPO_DIR/fish-osc-error.log"
echo "  Startup log:       $REPO_DIR/fish-osc-startup.log"
echo
echo "The service will automatically start on system boot and will:"
echo "  1. Wait for display to be ready"
echo "  2. Git pull latest changes from repository"
echo "  3. Run fish-osc.py with GUI using python3"
echo "  4. Restart automatically if it crashes"
echo "  5. Log all output for debugging"