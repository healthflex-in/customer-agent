#!/bin/bash

# Healthflex Customer Agent - Quick Code Update Script
# This script updates only the code files and restarts the container
# Much faster than full deployment for code-only changes

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

echo -e "${BLUE}=== Quick Code Update ===${NC}"

# Check if key file exists
if [ ! -f "$KEY_FILE" ]; then
    echo -e "${RED}Error: Key file '$KEY_FILE' not found!${NC}"
    echo "Please ensure the key file is in the current directory."
    exit 1
fi

# Set correct permissions on key file
chmod 400 "$KEY_FILE"

# Create temporary directory for code files
TEMP_DIR=$(mktemp -d)
echo -e "${YELLOW}Preparing code files for update...${NC}"

# Copy only Python code files and necessary configs (excluding large files)
rsync -av --progress \
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
    DataHandling/ "$TEMP_DIR/DataHandling/"

# Copy docker-compose.yml and Dockerfile if they exist (from deployment folder)
if [ -f "DataHandling/deployment/docker-compose.yml" ]; then
    cp DataHandling/deployment/docker-compose.yml "$TEMP_DIR/docker-compose.yml"
fi
if [ -f "DataHandling/deployment/Dockerfile" ]; then
    cp DataHandling/deployment/Dockerfile "$TEMP_DIR/Dockerfile"
fi
if [ -f ".dockerignore" ]; then
    cp .dockerignore "$TEMP_DIR/"
fi

# Create tarball of code files only
ARCHIVE_NAME="code-update.tar.gz"
cd "$TEMP_DIR"
tar -czf "$ARCHIVE_NAME" .
cd - > /dev/null

echo -e "${GREEN}✓ Code package created${NC}"

# Transfer files to EC2
echo -e "${YELLOW}Transferring code files to EC2...${NC}"
scp -i "$KEY_FILE" "$TEMP_DIR/$ARCHIVE_NAME" "${EC2_USER}@${EC2_HOST}:~/"

# Clean up local temp directory
rm -rf "$TEMP_DIR"

# Update code on EC2 and restart container
echo -e "${YELLOW}Updating code on EC2 and restarting container...${NC}"

ssh -i "$KEY_FILE" "${EC2_USER}@${EC2_HOST}" bash -s << 'ENDSSH'
    set -e
    
    cd ~/healthflex-agent
    
    # Extract updated code files
    echo "Extracting updated code files..."
    tar -xzf ~/code-update.tar.gz
    
    # Set permissions
    sudo chown -R ubuntu:ubuntu DataHandling/ 2>/dev/null || true
    chmod -R 755 DataHandling/ 2>/dev/null || true
    
    # Fix docker-compose.yml context path if needed
    # The context should be . (current directory) when docker-compose.yml is in ~/healthflex-agent
    # The Dockerfile expects DataHandling/ to be in the build context
    if [ -f "docker-compose.yml" ]; then
        echo "Fixing docker-compose.yml context path..."
        # Change context from ../.. to . (current directory)
        # This is needed because on EC2, docker-compose.yml is in ~/healthflex-agent/
        # and DataHandling/ is also in ~/healthflex-agent/, so context should be .
        sed -i.bak 's|context: \.\./\.\.|context: .|g' docker-compose.yml 2>/dev/null || true
        # Clean up backup file
        rm -f docker-compose.yml.bak 2>/dev/null || true
        echo "✓ Fixed docker-compose.yml context path (changed from ../.. to .)"
    fi
    
    # Rebuild Docker image (using cache for faster build)
    echo "Rebuilding Docker image (using cache)..."
    sudo docker-compose build
    
    # Restart container to pick up changes
    echo "Restarting container..."
    sudo docker-compose restart
    
    # Wait a moment for container to start
    sleep 3
    
    # Check status
    echo ""
    echo "Container status:"
    sudo docker-compose ps
    
    echo ""
    echo "Recent logs (last 15 lines):"
    sudo docker-compose logs --tail=15
    
    # Clean up
    rm ~/code-update.tar.gz
    
    echo ""
    echo "✓ Code update complete!"
ENDSSH

echo -e "${GREEN}✓ Code update complete!${NC}"

# Test the deployment
echo -e "${YELLOW}Testing deployment...${NC}"
sleep 2
if curl -f -s "http://${EC2_HOST}:8000/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Application is running and healthy!${NC}"
else
    echo -e "${YELLOW}Warning: Health check failed. The application might still be starting up.${NC}"
    echo "Check logs with: ssh -i $KEY_FILE ${EC2_USER}@${EC2_HOST} 'cd ~/healthflex-agent && sudo docker-compose logs -f'"
fi

echo -e "${GREEN}=== Update Complete ===${NC}"

