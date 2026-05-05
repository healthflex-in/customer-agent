#!/bin/bash

# Quick script to check what's happening on EC2

# Get the script directory and change to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

KEY_FILE="clinician-agent-key.pem"
EC2_HOST="ec2-13-204-235-217.ap-south-1.compute.amazonaws.com"
EC2_USER="ubuntu"

echo "Checking EC2 status..."
echo ""

ssh -i "$KEY_FILE" "${EC2_USER}@${EC2_HOST}" << 'ENDSSH'
    echo "=== Docker Processes ==="
    ps aux | grep docker | grep -v grep
    
    echo ""
    echo "=== Docker Containers ==="
    sudo docker ps -a
    
    echo ""
    echo "=== Docker Images ==="
    sudo docker images
    
    echo ""
    echo "=== Disk Usage ==="
    df -h
    
    echo ""
    echo "=== Memory Usage ==="
    free -h
    
    echo ""
    echo "=== Recent Docker Build Logs (if any) ==="
    cd ~/healthflex-agent 2>/dev/null && sudo docker-compose logs --tail=50 2>/dev/null || echo "No logs yet"
ENDSSH

