#!/bin/bash

# Fish OSC Controller Startup Script
# This script will:
# 1. Navigate to PeixeCidade directory
# 2. Git pull latest changes
# 3. Run fish-osc.py program

set -e  # Exit on any error

# Configuration
REPO_DIR="/home/pi/PeixeCidade"
LOG_FILE="$REPO_DIR/fish-osc-startup.log"
PYTHON_SCRIPT="fish-osc.py"

# Function to log messages
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Wait for display to be ready
log_message "Waiting for display to be ready..."
MAX_WAIT=60
WAIT_COUNT=0

while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
    if [ -n "$DISPLAY" ] && xhost > /dev/null 2>&1; then
        log_message "Display is ready"
        break
    fi
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

if [ $WAIT_COUNT -eq $MAX_WAIT ]; then
    log_message "WARNING: Display not ready after $MAX_WAIT seconds, proceeding anyway"
fi

# Navigate to repository directory
log_message "Navigating to $REPO_DIR"
cd "$REPO_DIR" || {
    log_message "ERROR: Failed to navigate to $REPO_DIR"
    exit 1
}

# Git pull latest changes
log_message "Pulling latest changes from repository"
if git pull origin main; then
    log_message "Git pull successful"
else
    log_message "WARNING: Git pull failed, continuing with existing code"
fi

# Check if Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    log_message "ERROR: $PYTHON_SCRIPT not found in $REPO_DIR"
    exit 1
fi

# Check if python is available
if ! command -v python3 &> /dev/null; then
    log_message "ERROR: python3 command not found"
    exit 1
fi

# Run fish-osc program
log_message "Starting fish-osc.py program"
log_message "Command: python3 $PYTHON_SCRIPT"

# Run program and capture output (GUI mode)
python3 "$PYTHON_SCRIPT" 2>&1 | tee -a "$LOG_FILE"

log_message "fish-osc.py program exited"