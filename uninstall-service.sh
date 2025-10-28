#!/bin/bash

# Uninstallation script for fish-osc systemd service
# This script will:
# 1. Stop and disable the service
# 2. Remove the service file
# 3. Clean up systemd

set -e  # Exit on any error

# Configuration
SERVICE_NAME="fish-osc"
SERVICE_FILE="fish-osc.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Fish OSC Service Uninstallation ==="
echo

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "ERROR: Do not run this script as root. Run as pi user with sudo."
    exit 1
fi

# Stop the service if it's running
echo "Stopping $SERVICE_NAME service..."
sudo systemctl stop "$SERVICE_NAME.service" 2>/dev/null || echo "Service was not running"

# Disable the service from starting on boot
echo "Disabling $SERVICE_NAME service..."
sudo systemctl disable "$SERVICE_NAME.service" 2>/dev/null || echo "Service was not enabled"

# Remove the service file
echo "Removing service file..."
sudo rm -f "$SYSTEMD_DIR/$SERVICE_FILE"

# Reload systemd to remove service
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Reset failed units (cleanup)
sudo systemctl reset-failed 2>/dev/null || echo "No failed units to reset"

echo
echo "=== Uninstallation Complete ==="
echo
echo "The $SERVICE_NAME service has been completely removed."
echo "Log files in /home/pi/PeixeCidade/ have been preserved."
echo
echo "To reinstall, run: ./install-service.sh"