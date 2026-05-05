#!/bin/bash

# Cleanup script to free up space on EC2 and remove all Docker images/containers

set -e

# Get the script directory and change to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Configuration
EC2_HOST="ec2-13-204-235-217.ap-south-1.compute.amazonaws.com"
EC2_USER="ubuntu"
KEY_FILE="clinician-agent-key.pem"

echo -e "${YELLOW}Cleaning up EC2 Docker images and freeing space...${NC}"
echo ""

# Check if key file exists
if [ ! -f "$KEY_FILE" ]; then
    echo -e "${RED}Error: Key file '$KEY_FILE' not found!${NC}"
    exit 1
fi

chmod 400 "$KEY_FILE"

echo -e "${YELLOW}Connecting to EC2...${NC}"

ssh -i "$KEY_FILE" "${EC2_USER}@${EC2_HOST}" << 'ENDSSH'
    set -e
    
    echo "=== Current Disk Usage ==="
    df -h
    echo ""
    
    echo "=== Stopping all containers ==="
    sudo docker stop $(sudo docker ps -aq) 2>/dev/null || echo "No containers to stop"
    
    echo "=== Removing all containers ==="
    sudo docker rm $(sudo docker ps -aq) 2>/dev/null || echo "No containers to remove"
    
    echo "=== Removing all images ==="
    sudo docker rmi $(sudo docker images -q) -f 2>/dev/null || echo "No images to remove"
    
    echo "=== Removing all volumes ==="
    sudo docker volume rm $(sudo docker volume ls -q) 2>/dev/null || echo "No volumes to remove"
    
    echo "=== Cleaning Docker system ==="
    sudo docker system prune -a -f --volumes
    
    echo "=== Cleaning apt cache ==="
    sudo apt-get clean
    sudo apt-get autoclean
    sudo rm -rf /var/lib/apt/lists/*
    
    echo "=== Cleaning old logs ==="
    sudo journalctl --vacuum-time=1d 2>/dev/null || true
    
    echo ""
    echo "=== Disk Usage After Cleanup ==="
    df -h
    echo ""
    
    echo "✓ Cleanup complete!"
ENDSSH

echo ""
echo -e "${GREEN}✓ EC2 cleanup complete!${NC}"
echo ""
echo -e "${YELLOW}Now you can deploy with the optimized build:${NC}"
echo "  ./deploy.sh"
echo ""

