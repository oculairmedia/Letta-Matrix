# Deployment Guide

Quick start guide and operational procedures for deploying and managing the Letta-Matrix integration.

## Quick Start

### Prerequisites
- Docker and Docker Compose installed
- Minimum 2GB RAM available
- Port 8008 available for Matrix homeserver

### Deploy in 3 Steps

```bash
# 1. Clone and navigate to directory
cd /home/user/Letta-Matrix

# 2. Start all services
docker-compose up -d

# 3. Access services
# Element Web Client: http://localhost:8008
# Matrix API: http://localhost:8004/health
# MCP Server: http://localhost:8016/health
```

Services will automatically:
- Pull pre-built images from GitHub Container Registry
- Initialize Matrix homeserver on external Tuwunel server
- Create Letta agent Matrix users
- Set up agent rooms and spaces
- Start message monitoring

## Architecture Overview

### Services Stack

```
Letta Agents → Matrix Client → Matrix API → Matrix Homeserver (Tuwunel)
                    ↓              ↓
                MCP Server → Claude Integration
```

**Core Services:**
- **matrix-client**: Main bot handling agent messages and routing
- **matrix-api**: FastAPI service for Matrix operations (port 8004)
- **mcp-server**: Model Context Protocol server for Claude (ports 8015, 8016)
- **letta-agent-mcp**: Inter-agent communication server (port 8017)
- **nginx**: Reverse proxy for Element web client
- **element**: Matrix web client UI

**External Dependencies:**
- **Tuwunel Matrix Homeserver**: matrix.oculair.ca (port 6167)
- **Letta API**: http://192.168.50.90:8289 (agent discovery)

## Environment Configuration

### Required Environment Variables

Create/edit `.env` file:

```bash
# Matrix Homeserver
MATRIX_HOMESERVER_URL=http://tuwunel:6167
SYNAPSE_SERVER_NAME=matrix.oculair.ca

# Matrix Admin Credentials
MATRIX_ADMIN_USERNAME=@matrixadmin:matrix.oculair.ca
MATRIX_ADMIN_PASSWORD=admin123

# Main Bot Credentials
MATRIX_USERNAME=@letta:matrix.oculair.ca
MATRIX_PASSWORD=letta

# Letta API
LETTA_API_URL=http://192.168.50.90:8289
LETTA_API_KEY=your_api_key_here

# Development Mode (simple passwords)
DEV_MODE=true

# Service Ports
NGINX_HTTP_PORT=8008
MATRIX_API_PORT=8004
MCP_WEBSOCKET_PORT=8015
MCP_HTTP_PORT=8016

# File Paths
ELEMENT_CONFIG_PATH=./element-config.json
NGINX_CONFIG_PATH=./nginx_matrix_proxy.conf
```

### Configuration Files

**element-config.json** - Element web client configuration:
```json
{
  "default_server_config": {
    "m.homeserver": {
      "base_url": "http://matrix.oculair.ca",
      "server_name": "matrix.oculair.ca"
    }
  },
  "brand": "Element",
  "integrations_ui_url": "https://scalar.vector.im/",
  "integrations_rest_url": "https://scalar.vector.im/api",
  "integrations_widgets_urls": ["https://scalar.vector.im/api"],
  "defaultCountryCode": "US",
  "showLabsSettings": true
}
```

**nginx_matrix_proxy.conf** - Nginx routing configuration:
```nginx
server {
    listen 80;
    server_name matrix.oculair.ca;

    location / {
        proxy_pass http://element:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## Service Management

### Starting Services

```bash
# Start all services
docker-compose up -d

# Start specific service
docker-compose up -d matrix-client

# View startup logs
docker-compose logs -f matrix-client

# Wait for initialization (2-3 minutes)
sleep 180
```

### Stopping Services

```bash
# Stop all services
docker-compose down

# Stop specific service
docker-compose stop matrix-client

# Stop and remove volumes (DESTRUCTIVE)
docker-compose down -v
```

### Restarting Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart matrix-client

# Rebuild and restart
docker-compose up -d --build matrix-client
```

### Checking Service Status

```bash
# View running services
docker-compose ps

# Check service health
docker-compose ps | grep healthy

# View resource usage
docker stats matrix-client matrix-api mcp-server
```

## Data Persistence

### Volume Mappings

```
./matrix_store/          # Matrix client session data
./matrix_client_data/    # Agent mappings and space config
./mcp_data/              # MCP server state
```

### Important Data Files

**agent_user_mappings.json** - Agent-to-Matrix user mappings:
```json
{
  "agent-597b5756-2915-4560-ba6b-91005f085166": {
    "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
    "agent_name": "Meridian",
    "matrix_user_id": "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca",
    "matrix_password": "password",
    "room_id": "!8I9YBvbr4KpXNedbph:matrix.oculair.ca",
    "created": true,
    "room_created": true
  }
}
```

**letta_space_config.json** - Letta Agents space configuration:
```json
{
  "space_id": "!AbCdEfG:matrix.oculair.ca",
  "created_at": 1704672000.123,
  "name": "Letta Agents"
}
```

### Backup Procedures

```bash
# Backup agent mappings
cp matrix_client_data/agent_user_mappings.json \
   matrix_client_data/agent_user_mappings.json.backup

# Backup space config
cp matrix_client_data/letta_space_config.json \
   matrix_client_data/letta_space_config.json.backup

# Backup all data
tar -czf letta-matrix-backup-$(date +%Y%m%d).tar.gz \
  matrix_store/ matrix_client_data/ mcp_data/
```

### Clean Slate Reboot

To completely reset the system:

```bash
# 1. Stop all services
docker-compose down

# 2. Backup current data (optional)
tar -czf backup-$(date +%Y%m%d_%H%M%S).tar.gz \
  matrix_client_data/ matrix_store/

# 3. Clear agent mappings
echo '{}' > matrix_client_data/agent_user_mappings.json

# 4. Remove space config
rm -f matrix_client_data/letta_space_config.json

# 5. Clear Matrix session stores
rm -rf matrix_store/*

# 6. Restart services
docker-compose up -d

# 7. Monitor agent discovery and room creation
docker-compose logs -f matrix-client
```

## Agent Synchronization

### How It Works

The matrix-client service automatically:
1. **Discovers agents** from Letta API every 0.5 seconds
2. **Creates Matrix users** with stable usernames based on agent IDs
3. **Creates dedicated rooms** for each agent
4. **Organizes rooms** in "Letta Agents" space
5. **Monitors messages** and routes to correct agents
6. **Sends responses** using agent's Matrix identity

### Monitoring Sync

```bash
# Watch agent sync logs
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep "agent sync"

# Check agent count
jq '. | length' matrix_client_data/agent_user_mappings.json

# View specific agent mapping
jq '.["agent-597b5756-2915-4560-ba6b-91005f085166"]' \
  matrix_client_data/agent_user_mappings.json
```

### Expected Behavior

On startup:
```
[AGENT_SYNC] Fetching agents from http://192.168.50.90:8289/v1/models
[AGENT_SYNC] Found 56 Letta agents
[AGENT_SYNC] Creating Letta Agents space
[AGENT_SYNC] Successfully created space: !XYZ:matrix.oculair.ca
Processing agent: Meridian (agent-597b5756...)
Created room !8I9YBvbr4KpXNedbph:matrix.oculair.ca for Meridian
Adding room to Letta Agents space
Successfully added room to space
[AGENT_SYNC] Completed sync: 56 agents, 56 rooms, 1 space
```

## Image Management

### Using Pre-Built Images

The deployment uses pre-built images from GitHub Container Registry:

```yaml
# docker-compose.yml
services:
  matrix-client:
    image: ghcr.io/oculairmedia/letta-matrix-client:latest
  matrix-api:
    image: ghcr.io/oculairmedia/letta-matrix-api:latest
  mcp-server:
    image: ghcr.io/oculairmedia/letta-matrix-mcp:latest
```

### Pulling Latest Images

```bash
# Pull latest images
docker-compose pull

# Pull specific image
docker pull ghcr.io/oculairmedia/letta-matrix-client:latest

# Use specific version
IMAGE_TAG=v1.0.0 docker-compose pull
```

### Building Locally (Development)

```bash
# Build all images
docker-compose build

# Build specific service
docker-compose build matrix-client

# Build with no cache
docker-compose build --no-cache matrix-client
```

## Post-Deployment Verification

### Health Checks

```bash
# 1. Check all services running
docker-compose ps
# Expected: All services "Up" and "healthy"

# 2. Verify Matrix API
curl http://localhost:8004/health
# Expected: {"status": "healthy"}

# 3. Verify MCP Server
curl http://localhost:8016/health
# Expected: {"status": "ok"}

# 4. Check agent sync
jq '. | length' matrix_client_data/agent_user_mappings.json
# Expected: Number matching your Letta agent count

# 5. Verify space created
jq '.space_id' matrix_client_data/letta_space_config.json
# Expected: "!<space_id>:matrix.oculair.ca"
```

### Testing Agent Communication

```bash
# 1. Open Element web client
# http://localhost:8008

# 2. Login as main user
# Username: @letta:matrix.oculair.ca
# Password: letta

# 3. Join "Letta Agents" space

# 4. Select an agent room (e.g., "Meridian - Letta Agent Chat")

# 5. Send test message
# "Hello Meridian!"

# 6. Verify response
# - Check agent responds within 5 seconds
# - Response comes from agent user (not @letta)
# - Check logs for routing confirmation
docker logs matrix-client 2>&1 | grep "AGENT ROUTING" | tail -5
```

## Known Issues and Fixes

### Issue: Agents Not Responding (January 2025)

**Problem**: Messages not being processed, stuck in invitation loops

**Root Causes**:
1. Wrong Letta API port (8283 instead of 1416)
2. Stale agent mappings
3. Blocking invitation retry loops

**Solution Applied**:
```python
# Updated endpoint in agent_user_manager.py
agents_endpoint = "http://192.168.50.90:1416/v1/models"

# Cleaned stale mappings
# Only active agents remain in agent_user_mappings.json

# Disabled blocking operations (temporary)
# - Periodic agent sync (causing loops)
# - Initial agent sync on startup
# - Auto-invitation processes
```

**Verification**:
```bash
# Check correct endpoint
docker exec matrix-client grep "1416" /app/agent_user_manager.py

# Verify message processing
docker logs matrix-client 2>&1 | grep "Successfully sent message"
```

### Issue: Permission Errors (M_FORBIDDEN)

**Problem**: @matrixadmin cannot be invited to agent rooms

**Temporary Workaround**: Invitation management disabled

**Permanent Fix** (pending):
1. Ensure @matrixadmin is properly added to rooms during creation
2. Re-enable invitation processes with better error handling
3. Implement skip logic for existing room members

### Issue: Stale Agent Data

**Problem**: Deleted agents still in mappings

**Solution**:
```bash
# Manual cleanup
docker exec matrix-client python3 << 'EOF'
import json
with open('/app/data/agent_user_mappings.json', 'r') as f:
    mappings = json.load(f)

# Remove stale agent
del mappings['agent-old-id']

with open('/app/data/agent_user_mappings.json', 'w') as f:
    json.dump(mappings, f, indent=2)
EOF

# Restart service
docker-compose restart matrix-client
```

## Performance Expectations

### Startup Times
- **Initial startup**: 2-3 minutes (first time)
- **Subsequent starts**: 30-60 seconds
- **Agent sync**: 0.5 seconds per cycle
- **Space creation**: < 2 seconds
- **Room creation**: < 2 seconds per agent

### Response Times
- **Agent message processing**: < 1 second
- **Matrix message sending**: < 1 second
- **Room name updates**: 5-10 seconds to appear in clients
- **Agent discovery**: 0.5 second interval

### Resource Usage (56 agents)
- **matrix-client**: 200-400 MB RAM
- **matrix-api**: 100-200 MB RAM
- **mcp-server**: 100-200 MB RAM
- **Total**: ~600-1000 MB RAM

## Security Considerations

### Development Mode

When `DEV_MODE=true`:
- Agent passwords set to "password"
- Simplified authentication
- Reduced security for easier testing

**DO NOT use in production!**

### Production Mode

When `DEV_MODE=false` or unset:
- Secure random passwords generated
- Passwords stored in agent_user_mappings.json
- Full authentication required

### Access Control

**Admin Users**:
- @matrixadmin:matrix.oculair.ca - System administration
- @letta:matrix.oculair.ca - Main bot account
- @admin:matrix.oculair.ca - Additional admin

**Agent Users**:
- Limited to their own rooms
- Cannot create users or rooms
- No administrative access

### Network Security

```yaml
# docker-compose.yml
networks:
  matrix-internal:
    driver: bridge
```

All services communicate on isolated internal network. Only specified ports exposed to host.

## Maintenance Tasks

### Daily
- [ ] Monitor service health
- [ ] Check disk space
- [ ] Review error logs

### Weekly
- [ ] Verify agent count matches Letta
- [ ] Check for stale agent mappings
- [ ] Review resource usage

### Monthly
- [ ] Update Docker images
- [ ] Backup configuration and data
- [ ] Review and cleanup logs

### Quarterly
- [ ] Security updates
- [ ] Performance optimization review
- [ ] Disaster recovery test

## References

- **Architecture**: See docs/architecture/OVERVIEW.md
- **Testing**: See docs/operations/TESTING.md
- **CI/CD**: See docs/operations/CI_CD.md
- **Troubleshooting**: See docs/operations/TROUBLESHOOTING.md
- **Matrix Bridge Documentation**: docs/CLAUDE.md

---

**Last Updated**: 2025-01-17
**Version**: 1.0
**Maintainers**: OculairMedia Development Team
