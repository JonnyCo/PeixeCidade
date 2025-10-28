#!/bin/bash

# Fish OSC Controller Startup Script
# This script will:
# 1. Navigate to the PeixeCidade directory
# 2. Git pull the latest changes
# 3. Run the fish-osc.py program

set -e  # Exit on any error

# Configuration
REPO_DIR="/home/pi/PeixeCidade"
LOG_FILE="$REPO_DIR/fish-osc-startup.log"
PYTHON_SCRIPT="fish-osc.py"

# Function to log messages
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

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

# Check if uv is available
if ! command -v uv &> /dev/null; then
    log_message "ERROR: uv command not found"
    exit 1
fi

# Run the fish-osc program
log_message "Starting fish-osc.py program"
log_message "Command: uv run $PYTHON_SCRIPT --no-gui"

# Run the program and capture output
uv run "$PYTHON_SCRIPT" --no-gui 2>&1 | tee -a "$LOG_FILE"

log_message "fish-osc.py program exited"