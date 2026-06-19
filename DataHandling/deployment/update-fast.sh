#!/bin/bash

# Healthflex Customer Agent - Ultra-Fast Code Update Script
# This script updates code files and restarts the container WITHOUT rebuilding
# Requires code directory to be mounted as a volume (see docker-compose.yml)

set -e  # Exit on error

# Get the script directory and change to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Configuration
EC2_HOST="ec2-13-204-235-217.ap-south-1.compute.amazonaws.com"
EC2_USER="ubuntu"
KEY_FILE="clinician-agent-key.pem"
REMOTE_DIR="~/healthflex-agent"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Ultra-Fast Code Update (No Rebuild) ===${NC}"

# Check if key file exists
if [ ! -f "$KEY_FILE" ]; then
    echo -e "${RED}Error: Key file '$KEY_FILE' not found!${NC}"
    exit 1
fi

chmod 400 "$KEY_FILE"

# Use rsync to sync only code files directly
echo -e "${YELLOW}Syncing code files to EC2...${NC}"

rsync -avz --progress \
    -e "ssh -i $KEY_FILE" \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='audio_files' \
    --exclude='transcripts' \
    --exclude='tts_cache' \
    --exclude='received_audio' \
    --exclude='db' \
    --exclude='output' \
    --exclude='node_modules' \
    --exclude='*.log' \
    --exclude='.env' \
    DataHandling/ "${EC2_USER}@${EC2_HOST}:${REMOTE_DIR}/DataHandling/"

echo -e "${GREEN}✓ Code files synced${NC}"

# Restart container to pick up changes (no rebuild needed)
echo -e "${YELLOW}Restarting container...${NC}"

ssh -i "$KEY_FILE" "${EC2_USER}@${EC2_HOST}" bash -s << 'ENDSSH'
    cd ~/healthflex-agent
    
    # Set permissions
    sudo chown -R ubuntu:ubuntu DataHandling/ 2>/dev/null || true
    chmod -R 755 DataHandling/ 2>/dev/null || true

    # Clear Python bytecode cache — stale .pyc files cause old code to run
    # even after source files are updated via rsync
    find DataHandling/src -name "*.pyc" -delete 2>/dev/null || true
    find DataHandling/src -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
    echo "✓ .pyc cache cleared"

    # Restart container (code is mounted as volume, so changes are immediate)
    echo "Restarting container..."
    sudo docker-compose restart
    
    # Wait a moment
    sleep 2
    
    # Check status
    echo ""
    echo "Container status:"
    sudo docker-compose ps
    
    echo ""
    echo "Recent logs (last 10 lines):"
    sudo docker-compose logs --tail=10
    
    echo ""
    echo "✓ Fast update complete!"
ENDSSH

echo -e "${GREEN}✓ Code update complete!${NC}"

# Quick health check
sleep 2
if curl -f -s "http://${EC2_HOST}:8000/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Application is healthy!${NC}"
else
    echo -e "${YELLOW}Warning: Health check failed. Check logs if needed.${NC}"
fi

echo -e "${GREEN}=== Update Complete ===${NC}"

