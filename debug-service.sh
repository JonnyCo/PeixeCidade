#!/bin/bash

# Debug script for fish-osc service
# This script helps troubleshoot why service isn't working

echo "=== Fish OSC Service Debug ==="
echo

# Check if service is installed
if [ ! -f "/etc/systemd/system/fish-osc.service" ]; then
    echo "❌ Service file not found in /etc/systemd/system/"
    echo "Run ./install-service.sh first"
    exit 1
fi

echo "✅ Service file found"
echo

# Check service status
echo "=== Service Status ==="
sudo systemctl status fish-osc.service --no-pager
echo

# Check if service is enabled
echo "=== Service Enabled Status ==="
if sudo systemctl is-enabled fish-osc.service; then
    echo "✅ Service is enabled to start on boot"
else
    echo "❌ Service is NOT enabled to start on boot"
fi
echo

# Check recent logs
echo "=== Recent Service Logs ==="
sudo journalctl -u fish-osc.service --since "5 minutes ago" --no-pager
echo

# Check log files
echo "=== Log Files ==="
LOG_DIR="/home/pi/PeixeCidade"
for log_file in "$LOG_DIR"/fish-osc*.log; do
    if [ -f "$log_file" ]; then
        echo "📄 $log_file (last 10 lines):"
        tail -10 "$log_file"
        echo "---"
    fi
done

# Check if script is executable
echo "=== Script Permissions ==="
if [ -x "$LOG_DIR/fish-osc-startup.sh" ]; then
    echo "✅ fish-osc-startup.sh is executable"
else
    echo "❌ fish-osc-startup.sh is NOT executable"
    echo "Run: chmod +x fish-osc-startup.sh"
fi

# Check display environment
echo "=== Display Environment ==="
echo "DISPLAY: $DISPLAY"
echo "XAUTHORITY: $XAUTHORITY"
if xhost > /dev/null 2>&1; then
    echo "✅ X server is accessible"
else
    echo "❌ X server is NOT accessible"
fi

# Test manual script run
echo "=== Manual Script Test ==="
echo "Testing if script runs manually (will run for 5 seconds)..."
timeout 5s "$LOG_DIR/fish-osc-startup.sh" || echo "Script test completed or timed out"
echo

echo "=== Debug Complete ==="
echo "If service still doesn't work, try:"
echo "1. sudo systemctl restart fish-osc"
echo "2. Check if GUI is available: echo \$DISPLAY"
echo "3. Run manually: ./fish-osc-startup.sh"