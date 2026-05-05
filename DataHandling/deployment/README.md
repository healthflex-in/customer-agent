# Deployment Scripts and Configuration

This folder contains all deployment-related scripts and configuration files for the Healthflex Customer Agent.

## Files

### Scripts
- **`deploy.sh`** - Full deployment script (builds Docker image from scratch)
- **`update.sh`** - Quick code update script (rebuilds using cache)
- **`update-fast.sh`** - Ultra-fast code update script (no rebuild, just restart)
- **`check-ec2-status.sh`** - Check EC2 instance status and Docker containers
- **`cleanup-ec2.sh`** - Clean up Docker images/containers to free space

### Configuration Files
- **`docker-compose.yml`** - Main Docker Compose configuration (with code volume mount)
- **`docker-compose.dev.yml`** - Development Docker Compose configuration
- **`Dockerfile`** - Docker image build configuration

### Documentation
- **`FORM_INDEPENDENCE_FIX.md`** - Documentation of the form independence fixes
- **`stance-dashboard.customer-info.json`** - Sample MongoDB data export

## Usage

All scripts automatically change to the project root directory, so they can be run from anywhere:

```bash
# From project root
cd /Users/chrisdev/Healthflex/Customer-agent
./DataHandling/deployment/deploy.sh

# Or from deployment folder
cd DataHandling/deployment
./deploy.sh
```

## Requirements

- SSH key file (`clinician-agent-key.pem`) must be in the project root directory
- `.env` file must be in `DataHandling/.env`
- EC2 instance must be accessible at the configured host

## Script Details

### deploy.sh
Full deployment with Docker image rebuild. Use for:
- Initial deployment
- Dependency changes
- Major updates

### update.sh
Quick update with cache-based rebuild. Use for:
- Code changes that need rebuild
- Faster than full deploy

### update-fast.sh
Ultra-fast update without rebuild. Use for:
- Python code changes only
- Fastest option (just syncs files and restarts)

### check-ec2-status.sh
Check EC2 status, Docker containers, disk/memory usage, and recent logs.

### cleanup-ec2.sh
Clean up Docker images, containers, and volumes to free disk space.


