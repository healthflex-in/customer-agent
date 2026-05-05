#!/bin/bash

# Healthflex Customer Agent - AWS Deployment Script
# This script automates the deployment process to AWS EC2

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
ARCHIVE_NAME="healthflex-agent.tar.gz"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse command line arguments
NO_CACHE=false
QUICK_FIX=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --no-cache)
            NO_CACHE=true
            shift
            ;;
        --quick-fix)
            QUICK_FIX=true
            shift
            ;;
        --help|-h)
            echo -e "${BLUE}Healthflex Customer Agent - Deployment Script${NC}"
            echo ""
            echo "Usage: ./deploy.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-cache     Build Docker image from scratch (no cache)"
            echo "  --quick-fix    Quick deployment (uses cache, only updates changed files)"
            echo "  --help, -h     Show this help message"
            echo ""
            echo "Examples:"
            echo "  ./deploy.sh                  # Normal deployment with cache"
            echo "  ./deploy.sh --no-cache       # Full rebuild from scratch"
            echo "  ./deploy.sh --quick-fix      # Fast update for minor fixes"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if [ "$NO_CACHE" = true ]; then
    echo -e "${BLUE}=== Full Rebuild Deployment (No Cache) ===${NC}"
elif [ "$QUICK_FIX" = true ]; then
    echo -e "${BLUE}=== Quick Fix Deployment (Using Cache) ===${NC}"
else
    echo -e "${GREEN}=== Healthflex Customer Agent Deployment ===${NC}"
fi

# Check if key file exists
if [ ! -f "$KEY_FILE" ]; then
    echo -e "${RED}Error: Key file '$KEY_FILE' not found!${NC}"
    echo "Please ensure the key file is in the current directory."
    exit 1
fi

# Set correct permissions on key file
chmod 400 "$KEY_FILE"
echo -e "${GREEN}✓ Key file permissions set${NC}"

# Check if .env file exists
if [ ! -f "DataHandling/.env" ]; then
    echo -e "${YELLOW}Warning: DataHandling/.env not found!${NC}"
    echo "Please create it from .env.example and fill in your credentials."
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if .env exists
if [ ! -f "DataHandling/.env" ]; then
    echo -e "${RED}Error: DataHandling/.env file not found!${NC}"
    echo "Please create it with your credentials before deploying."
    exit 1
fi

# Create tarball (including .env file)
echo -e "${YELLOW}Creating deployment package...${NC}"

# Create temporary directory for packaging
TEMP_PACKAGE_DIR=$(mktemp -d)

# Copy Dockerfile and docker-compose.yml to temp root (EC2 expects them in root)
# Use root docker-compose.yml (has correct context: .) instead of deployment one
cp DataHandling/deployment/Dockerfile "$TEMP_PACKAGE_DIR/Dockerfile" 2>/dev/null || true
if [ -f "docker-compose.yml" ]; then
    cp docker-compose.yml "$TEMP_PACKAGE_DIR/docker-compose.yml" 2>/dev/null || true
else
    # Fallback: copy deployment docker-compose.yml but fix the context
    cp DataHandling/deployment/docker-compose.yml "$TEMP_PACKAGE_DIR/docker-compose.yml" 2>/dev/null || true
    # Fix context from ../.. to . when in root
    sed -i.bak 's|context: \.\./\.\.|context: .|g' "$TEMP_PACKAGE_DIR/docker-compose.yml" 2>/dev/null || true
    rm -f "$TEMP_PACKAGE_DIR/docker-compose.yml.bak" 2>/dev/null || true
fi
if [ -f ".dockerignore" ]; then
    cp .dockerignore "$TEMP_PACKAGE_DIR/.dockerignore" 2>/dev/null || true
fi

# Copy DataHandling directory (excluding large files)
rsync -av --progress \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='audio_files' \
    --exclude='transcripts' \
    --exclude='tts_cache' \
    --exclude='received_audio' \
    --exclude='db' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='deployment' \
    DataHandling/ "$TEMP_PACKAGE_DIR/DataHandling/" 2>/dev/null || true

# Create tarball from temp directory
cd "$TEMP_PACKAGE_DIR"
tar -czf "$PROJECT_ROOT/$ARCHIVE_NAME" .
cd "$PROJECT_ROOT"
rm -rf "$TEMP_PACKAGE_DIR"

echo -e "${GREEN}✓ Deployment package created${NC}"

# Transfer files to EC2
echo -e "${YELLOW}Transferring files to EC2...${NC}"
scp -i "$KEY_FILE" "$ARCHIVE_NAME" "${EC2_USER}@${EC2_HOST}:~/"
echo -e "${GREEN}✓ Files transferred${NC}"

# Clean up local tarball
rm "$ARCHIVE_NAME"

# Deploy on EC2
echo -e "${YELLOW}Deploying on EC2...${NC}"

# Set build options based on flags
BUILD_OPTS=""
if [ "$NO_CACHE" = true ]; then
    BUILD_OPTS="--no-cache"
    echo -e "${YELLOW}Building with --no-cache (full rebuild)${NC}"
elif [ "$QUICK_FIX" = true ]; then
    BUILD_OPTS="--pull"
    echo -e "${YELLOW}Building with cache (quick fix mode)${NC}"
fi

ssh -i "$KEY_FILE" "${EC2_USER}@${EC2_HOST}" bash -s -- "$BUILD_OPTS" << 'ENDSSH'
    set -e
    
    # Get build options from parameter
    BUILD_OPTS="$1"
    
    # Create directory if it doesn't exist
    mkdir -p ~/healthflex-agent
    
    # Extract files
    echo "Extracting files..."
    tar -xzf ~/healthflex-agent.tar.gz -C ~/healthflex-agent
    cd ~/healthflex-agent
    
    # Create necessary directories
    echo "Creating directories..."
    mkdir -p DataHandling/audio_files
    mkdir -p DataHandling/transcripts
    mkdir -p DataHandling/tts_cache
    mkdir -p DataHandling/output
    mkdir -p DataHandling/received_audio
    mkdir -p DataHandling/db/vector
    mkdir -p DataHandling/db/mongo
    
    # Set permissions (use sudo for files that might be owned by root from previous Docker runs)
    sudo chown -R ubuntu:ubuntu DataHandling/ 2>/dev/null || true
    chmod -R 755 DataHandling/ 2>/dev/null || true
    
    # Verify .env file exists
    if [ -f "DataHandling/.env" ]; then
        echo "✓ .env file found"
        echo "Environment variables in .env:"
        grep -E "^[A-Z_]+=" DataHandling/.env | cut -d= -f1 | head -5
    else
        echo "✗ WARNING: DataHandling/.env file not found!"
    fi
    
    # Check if Docker is installed
    if ! command -v docker &> /dev/null; then
        echo "Docker not found. Installing Docker..."
        sudo apt-get update
        sudo apt-get install -y docker.io
        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $USER
        echo "Docker installed successfully!"
    fi
    
    # Check if docker-compose is installed
    if ! command -v docker-compose &> /dev/null; then
        echo "Docker Compose not found. Installing Docker Compose..."
        sudo apt-get install -y docker-compose
        echo "Docker Compose installed successfully!"
    fi
    
    # Stop existing containers (use sudo if docker group not active yet)
    echo "Stopping existing containers..."
    sudo docker-compose down 2>/dev/null || true
    
    # Build and start (use sudo if docker group not active yet)
    echo "Building Docker image with options: $BUILD_OPTS"
    sudo docker-compose build $BUILD_OPTS
    
    echo "Starting application..."
    sudo docker-compose up -d
    
    # Wait a bit for the container to start
    sleep 5
    
    # Check status
    echo "Checking container status..."
    sudo docker-compose ps
    
    echo ""
    echo "Container logs (last 20 lines):"
    sudo docker-compose logs --tail=20
    
    # Clean up
    rm ~/healthflex-agent.tar.gz
    
    echo "Deployment complete!"
ENDSSH

echo -e "${GREEN}✓ Deployment complete!${NC}"

# Test the deployment
echo -e "${YELLOW}Testing deployment...${NC}"
sleep 3
if curl -f -s "http://${EC2_HOST}:8000/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Application is running and healthy!${NC}"
    echo -e "Access your application at: ${GREEN}http://${EC2_HOST}:8000${NC}"
else
    echo -e "${YELLOW}Warning: Health check failed. The application might still be starting up.${NC}"
    echo "Check logs with: ssh -i $KEY_FILE ${EC2_USER}@${EC2_HOST} 'cd ~/healthflex-agent && docker-compose logs -f'"
fi

echo -e "${GREEN}=== Deployment Script Complete ===${NC}"
echo ""
echo "Useful commands:"
echo "  View logs:    ssh -i $KEY_FILE ${EC2_USER}@${EC2_HOST} 'cd ~/healthflex-agent && docker-compose logs -f'"
echo "  Restart:      ssh -i $KEY_FILE ${EC2_USER}@${EC2_HOST} 'cd ~/healthflex-agent && docker-compose restart'"
echo "  Stop:         ssh -i $KEY_FILE ${EC2_USER}@${EC2_HOST} 'cd ~/healthflex-agent && docker-compose down'"
echo "  SSH access:   ssh -i $KEY_FILE ${EC2_USER}@${EC2_HOST}"

