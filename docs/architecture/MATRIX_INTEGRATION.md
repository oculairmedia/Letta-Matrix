# Matrix Homeserver Integration

**Status**: 🟢 Production Ready
**Last Updated**: 2025-11-17
**Owner**: Matrix Integration Team

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Matrix Homeserver](#matrix-homeserver)
- [Matrix API Service](#matrix-api-service)
- [Authentication and Session Management](#authentication-and-session-management)
- [User Bootstrap System](#user-bootstrap-system)
- [Bridge Integration Patterns](#bridge-integration-patterns)
- [Performance Optimization](#performance-optimization)
- [Security Model](#security-model)
- [Deployment](#deployment)
- [Monitoring and Health Checks](#monitoring-and-health-checks)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Matrix homeserver integration provides the foundation for all Letta-Matrix communication. It implements a complete Matrix stack with optimized performance, automatic user provisioning, and production-ready bridge patterns.

### Key Features

- ✅ Dual homeserver support (Synapse/Tuwunel)
- ✅ Automatic core user bootstrap
- ✅ RESTful Matrix API service
- ✅ Optimized rate limiting and connection pooling
- ✅ Bridge-ready application service framework
- ✅ Health checks and monitoring endpoints

---

## Architecture

### Service Topology

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   PostgreSQL    │    │   Matrix        │    │   Nginx Proxy   │
│   Database      │◄───┤   Synapse       │◄───┤   (External)    │
│   Port: 5432    │    │   Port: 8008    │    │   Port: 443/80  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Matrix API    │    │   MCP Server    │    │   Custom Matrix │
│   Service       │    │   HTTP: 8016    │    │   Client        │
│   Port: 8004    │    │   WS: 8015      │    │   (Internal)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Agent User    │    │   GMMessages    │    │   Discord       │
│   Manager       │    │   Bridge        │    │   Bridge        │
│   (Internal)    │    │   (External)    │    │   (External)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Component Overview

1. **Matrix Homeserver**: Synapse (Python) or Tuwunel (Rust) - Core Matrix protocol implementation
2. **Matrix API Service**: FastAPI REST wrapper for Matrix operations
3. **PostgreSQL Database**: Backend storage for application data
4. **Custom Matrix Client**: Agent-aware message routing and event handling
5. **Agent User Manager**: Automated agent-to-Matrix user provisioning
6. **MCP Server**: Tool interface for AI agents

---

## Matrix Homeserver

### Synapse Configuration

**Technology**: Python-based Matrix homeserver
**Storage**: SQLite (development) or PostgreSQL (production)

#### Key Configuration (`homeserver.yaml`)

```yaml
# Rate limiting optimized for agent communication
rc_message:
  per_second: 10000     # High-volume agent messaging
  burst_count: 100000

rc_registration:
  per_second: 1000      # Automatic agent registration
  burst_count: 10000

rc_joins:
  local:
    per_second: 1000    # Fast room joining
    burst_count: 10000

# Application services for bridges
app_service_config_files:
  - /data/gmessages-registration.yaml
  - /data/discord-registration.yaml
  - /data/meta-registration.yaml

# Enable registration for agent users
enable_registration: true
enable_registration_without_verification: true
```

#### Database Configuration

**Development (SQLite)**:
```yaml
database:
  name: sqlite3
  args:
    database: /data/homeserver.db
```

**Production (PostgreSQL)**:
```yaml
database:
  name: psycopg2
  args:
    user: synapse
    password: ${POSTGRES_PASSWORD}
    database: synapse
    host: db
    port: 5432
    cp_min: 5
    cp_max: 10
```

### Tuwunel Alternative

**Technology**: Rust-based Matrix homeserver
**Storage**: RocksDB (embedded)

**Benefits**:
- 10x+ faster performance
- Lower resource usage
- No external database needed
- Simpler deployment

**Configuration**:
```bash
TUWUNEL_ALLOW_REGISTRATION=true
TUWUNEL_SERVER_NAME=matrix.oculair.ca
TUWUNEL_DATABASE_PATH=/data/tuwunel.db
```

See: [TUWUNEL_MIGRATION.md](TUWUNEL_MIGRATION.md)

---

## Matrix API Service

### FastAPI REST Interface

**Port**: 8004
**Technology**: FastAPI with async support

### Available Endpoints

#### Authentication
```python
# Auto-login with environment credentials
GET /login/auto
Returns: {"access_token": "...", "user_id": "@letta:..."}

# Manual login
POST /login
Body: {"username": "...", "password": "..."}
Returns: {"access_token": "...", "device_id": "..."}
```

#### Room Operations
```python
# List joined rooms
GET /rooms/list
Returns: [{"room_id": "!abc...", "name": "Room Name"}, ...]

# Create room
POST /rooms/create
Body: {
  "name": "Room Name",
  "topic": "Description",
  "is_public": false,
  "invite": ["@user:domain"]
}
Returns: {"room_id": "!xyz..."}
```

#### Messaging
```python
# Send message
POST /messages/send
Body: {"room_id": "!abc...", "message": "Hello"}
Returns: {"event_id": "$event..."}

# Get room messages
POST /messages/get
Body: {"room_id": "!abc...", "limit": 50}
Returns: {"messages": [...], "end": "t123..."}

# Get recent messages across all rooms
GET /messages/recent?limit=10
Returns: {"messages": [...], "total_rooms": 5}
```

#### Health Check
```python
GET /health
Returns: {
  "status": "healthy",
  "synapse_connected": true,
  "uptime_seconds": 12345
}
```

### Implementation Example

```python
# matrix_api.py
from fastapi import FastAPI, HTTPException
from matrix_nio import AsyncClient

app = FastAPI()

@app.post("/messages/send")
async def send_message(room_id: str, message: str):
    client = await get_authenticated_client()

    try:
        response = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": message
            }
        )
        return {"event_id": response.event_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

---

## Authentication and Session Management

### Session Persistence Pattern

The system implements sophisticated session management to avoid rate limiting:

```python
class MatrixAuthManager:
    async def get_authenticated_client(self):
        # 1. Try to restore previous session
        self.client.load_store()
        if self.client.access_token and self.client.device_id:
            logger.info("Restored session from store")
            return self.client

        # 2. Only login if no stored credentials
        if not self.client.access_token:
            response = await self.client.login(password=self.password)
            if isinstance(response, LoginError):
                # Handle rate limiting gracefully
                if "429" in str(response.message):
                    logger.error("Rate limited! Using existing session.")
                    return self.client

            # Store session for next time
            self.client.store_path = self.store_path

        return self.client
```

### Key Benefits

1. **Avoids Rate Limiting**: Reuses sessions instead of logging in repeatedly
2. **Persistent Tokens**: Stores access tokens and device IDs
3. **Graceful Recovery**: Falls back to existing sessions on errors
4. **Connection Pooling**: Shares connections across operations

### Storage Locations

- **Session Store**: `/app/matrix_store/{device_name}/`
- **Credentials**: Environment variables only
- **Agent Mappings**: `/app/data/agent_user_mappings.json`

---

## User Bootstrap System

### Automatic Core User Creation

On startup, the system ensures core users exist before syncing agents:

```python
async def ensure_core_users_exist(config):
    """Create core Matrix users if they don't exist"""
    core_users = [
        {
            "username": config.letta_username,    # @letta
            "password": config.letta_password,
        },
        {
            "username": config.admin_username,    # @admin
            "password": config.admin_password,
        }
    ]

    for user in core_users:
        if not await user_exists(user["username"]):
            await create_user(user["username"], user["password"])
            logger.info(f"Created core user: {user['username']}")
```

### Registration API

Uses Matrix's standard registration endpoint:

```python
async def create_user(username: str, password: str):
    """Create user via Matrix registration API"""
    response = await session.post(
        f"{homeserver}/_matrix/client/v3/register",
        json={
            "username": username.split(":")[0].lstrip("@"),
            "password": password,
            "auth": {"type": "m.login.dummy"}
        }
    )
    return response.json()
```

### Bootstrap Flow

```
1. Container Starts
   └─> run_agent_sync()
       └─> ensure_core_users_exist()
           ├─> Check @letta exists
           │   └─> Create if missing
           ├─> Check @admin exists
           │   └─> Create if missing
           └─> Proceed to agent sync
```

### Benefits

- **Zero Manual Setup**: Fresh deployments work immediately
- **Disaster Recovery**: Easy recovery from database loss
- **Development Friendly**: Quick setup for testing
- **Idempotent**: Safe to run multiple times

---

## Bridge Integration Patterns

### Application Service Framework

Following mautrix bridge patterns for professional integration:

```yaml
# example-registration.yaml
id: bridge-name
url: http://bridge:8080
as_token: "secure_random_token_here"
hs_token: "another_secure_token"
sender_localpart: bridge-bot
namespaces:
  users:
    - exclusive: true
      regex: "@bridge_.*:matrix.oculair.ca"
  rooms:
    - exclusive: true
      regex: "#bridge-.*:matrix.oculair.ca"
  aliases:
    - exclusive: true
      regex: "#bridge-.*:matrix.oculair.ca"
rate_limited: false
protocols: ["bridge-protocol"]
```

### Best Practices from mautrix-discord

#### 1. Matrix Space Organization
✅ **We implement this correctly**:
- Main space for all bridged content
- Bidirectional parent-child relationships
- `m.space.child` and `m.space.parent` state events

```python
async def add_room_to_space(self, room_id: str, room_name: str):
    # Set m.space.child on space
    child_data = {
        "via": ["matrix.oculair.ca"],
        "suggested": True,
        "order": room_name
    }

    # Set m.space.parent on room (bidirectional)
    parent_data = {
        "via": ["matrix.oculair.ca"],
        "canonical": True
    }
```

#### 2. Persistent User-to-Identity Mapping
✅ **We implement this correctly**:
- Stable usernames based on agent IDs, not names
- Credentials stored persistently
- Room persistence and reuse

```python
def generate_username(self, agent_name: str, agent_id: str) -> str:
    # Username based on stable agent ID
    clean_id = agent_id.replace('-', '_')
    username = f"agent_{clean_id}"
    return username
```

#### 3. Message Queuing (Recommended)

**Priority**: Medium-High
**Implementation**:
```python
# Add message queue for reliability
self.message_queue = asyncio.Queue(maxsize=128)

async def message_processor(self):
    while True:
        message = await self.message_queue.get()
        await self.process_message(message)
```

### Bridge Architecture Comparison

**mautrix-discord**:
```
Discord API ←→ Bridge Core ←→ Matrix API
                    ↓
              Portal (Room)
                    ↓
              User (Puppet)
                    ↓
             Guild/Space
```

**Letta-Matrix** (Our Implementation):
```
Letta API ←→ Agent Manager ←→ Matrix API
                 ↓
          Agent User (Puppet)
                 ↓
           Agent Room
                 ↓
         Letta Agents Space
```

---

## Performance Optimization

### Connection Pooling

```python
# Global session with connection pooling
connector = aiohttp.TCPConnector(
    limit=100,              # Connection pool size
    limit_per_host=50,      # Per-host connection limit
    ttl_dns_cache=300,      # DNS cache timeout (5 minutes)
    keepalive_timeout=30,   # Keep connections alive
    enable_cleanup_closed=True,
    force_close=False
)

session = aiohttp.ClientSession(
    connector=connector,
    timeout=aiohttp.ClientTimeout(total=30)
)
```

### Rate Limiting Configuration

Synapse is configured for high-volume agent communication:

```yaml
# Effectively unlimited for internal use
rc_message:
  per_second: 10000
  burst_count: 100000
```

### Performance Characteristics

**Measured Performance**:
- Agent message processing: <1 second
- Room creation: <2 seconds
- User creation: <1 second
- Message sending: <1 second
- Agent discovery: 100-200ms

**Sync Performance**:
- Startup sync: Immediate
- Periodic sync: Every 0.5 seconds
- New agent detection: Within 0.5 seconds
- Name change detection: Within 0.5 seconds

### Optimization Patterns

#### 1. DNS Caching
```python
ttl_dns_cache=300  # 5-minute DNS cache
```

#### 2. Keep-Alive Connections
```python
keepalive_timeout=30  # Reuse connections for 30 seconds
```

#### 3. Async Operations
```python
# Parallel operations where possible
await asyncio.gather(
    create_user(),
    create_room(),
    update_space()
)
```

---

## Security Model

### Authentication Security

**Credential Storage**:
- Environment variables only (no hardcoded secrets)
- Session tokens stored in encrypted format
- Per-agent credentials isolated

**Session Management**:
- Automatic token refresh
- Graceful handling of expired sessions
- Rate limit protection

### Authorization Model

**User Levels**:
1. **Admin User** (`@admin`): Full server control
2. **Letta Bot** (`@letta`): Read-only monitoring
3. **Agent Users**: Room-specific permissions
4. **Regular Users**: Standard Matrix permissions

**Room Permissions**:
```python
# Private rooms by default
power_levels = {
    "users": {
        agent_user_id: 100,      # Agent is admin
        "@admin:domain": 100,    # Admin access
        "@letta:domain": 50      # Bot monitoring
    },
    "events": {
        "m.room.message": 0      # Anyone can send
    }
}
```

### Network Security

- **HTTPS/TLS**: All external connections encrypted
- **Internal Network**: Docker bridge for service isolation
- **Federation**: Optional, can be disabled
- **Rate Limiting**: Protects against abuse

---

## Deployment

### Docker Compose Configuration

```yaml
services:
  synapse:
    image: matrixdotorg/synapse:latest
    restart: unless-stopped
    env_file: [.env]
    volumes:
      - ${SYNAPSE_DATA_PATH}:/data
    networks: [matrix-internal]
    ports: ["8008:8008"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8008/_matrix/client/versions"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s

  matrix-api:
    build:
      context: .
      dockerfile: Dockerfile.matrix-api
    restart: unless-stopped
    env_file: [.env]
    environment:
      - MATRIX_HOMESERVER_URL=http://synapse:8008
    ports: ["8004:8000"]
    depends_on:
      synapse: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
```

### Environment Variables

```bash
# Matrix Homeserver
MATRIX_HOMESERVER_URL=http://synapse:8008
SYNAPSE_DATA_PATH=./synapse-data

# Core Users
MATRIX_USERNAME=@letta:matrix.oculair.ca
MATRIX_PASSWORD=letta
MATRIX_ADMIN_USERNAME=@admin:matrix.oculair.ca
MATRIX_ADMIN_PASSWORD=secure_password_here

# Database (if using PostgreSQL)
POSTGRES_DB=synapse
POSTGRES_USER=synapse
POSTGRES_PASSWORD=secure_db_password
```

### Deployment Steps

1. **Prepare environment**:
   ```bash
   cd /opt/stacks/matrix-synapse-deployment
   cp .env.example .env
   # Edit .env with your values
   ```

2. **Build services**:
   ```bash
   docker-compose build
   ```

3. **Start homeserver first**:
   ```bash
   docker-compose up -d synapse
   # Wait for health check to pass
   ```

4. **Start remaining services**:
   ```bash
   docker-compose up -d
   ```

5. **Verify deployment**:
   ```bash
   docker-compose ps
   curl http://localhost:8004/health
   ```

---

## Monitoring and Health Checks

### Health Check Endpoints

```python
# Matrix API Service
GET http://localhost:8004/health
Returns: {
  "status": "healthy",
  "synapse_connected": true,
  "uptime_seconds": 12345,
  "agents_connected": 5
}

# MCP Server
GET http://localhost:8015/health
Returns: {
  "status": "healthy",
  "tools_registered": 5
}

# Synapse
GET http://localhost:8008/_matrix/client/versions
Returns: {"versions": ["r0.6.1", ...]}
```

### Logging

All services use structured JSON logging:

```python
logger.info("Room created", extra={
    "room_id": room_id,
    "agent_id": agent_id,
    "duration_ms": duration
})
```

**View logs**:
```bash
# Matrix client logs
docker logs matrix-client-1 --tail 100 -f

# Matrix API logs
docker logs matrix-api-1 --tail 100 -f

# Synapse logs
docker logs synapse-1 --tail 100 -f
```

### Performance Metrics

**Key Metrics to Monitor**:
- Message throughput (messages/second)
- Room creation rate
- API response times
- Database connection pool usage
- Memory and CPU usage

---

## Best Practices

### 1. Session Management
- ✅ Always reuse sessions when possible
- ✅ Store tokens persistently
- ✅ Handle rate limiting gracefully
- ❌ Don't login on every request

### 2. Connection Pooling
- ✅ Use connection pools for HTTP clients
- ✅ Configure appropriate timeouts
- ✅ Enable DNS caching
- ❌ Don't create new sessions per request

### 3. Error Handling
- ✅ Implement exponential backoff
- ✅ Log errors with context
- ✅ Provide fallback mechanisms
- ❌ Don't fail silently

### 4. Rate Limiting
- ✅ Respect Matrix rate limits
- ✅ Implement client-side queuing
- ✅ Use batch operations where possible
- ❌ Don't hammer the API

### 5. Security
- ✅ Use environment variables for secrets
- ✅ Encrypt session storage
- ✅ Implement proper authorization
- ❌ Don't hardcode credentials

### 6. Scalability
- ✅ Design for horizontal scaling
- ✅ Use database for shared state
- ✅ Implement proper caching
- ❌ Don't rely on in-memory state

---

## Troubleshooting

### Synapse Not Starting

**Check logs**:
```bash
docker logs synapse-1
```

**Common issues**:
- Database connection failed → Check PostgreSQL
- Port already in use → Change port mapping
- Invalid configuration → Validate homeserver.yaml

### Matrix API Connection Failed

**Diagnostic steps**:
```bash
# Check service is running
docker ps | grep matrix-api

# Test homeserver connectivity
docker exec matrix-api-1 curl http://synapse:8008/_matrix/client/versions

# Check environment variables
docker exec matrix-api-1 env | grep MATRIX
```

### Authentication Failures

**Verify user exists**:
```bash
# Check if user was created
docker logs matrix-client-1 | grep "Created core user"

# Try manual login
curl -X POST http://localhost:8004/login \
  -d '{"username": "@letta:matrix.oculair.ca", "password": "letta"}'
```

### Rate Limiting Issues

**Symptoms**: "429 Too Many Requests" errors

**Solutions**:
1. Check Synapse rate limit configuration
2. Implement client-side delays
3. Reuse sessions properly
4. Enable connection pooling

### Permission Errors

**M_FORBIDDEN errors**:
- Verify user is in the room
- Check power levels
- Ensure proper admin permissions
- Review application service registration

---

## Related Documentation

### Architecture
- [OVERVIEW.md](OVERVIEW.md) - System architecture overview
- [AGENT_MANAGEMENT.md](AGENT_MANAGEMENT.md) - Agent sync and room management
- [MCP_SERVERS.md](MCP_SERVERS.md) - MCP server architecture
- [INTER_AGENT_MESSAGING.md](INTER_AGENT_MESSAGING.md) - Inter-agent communication
- [TUWUNEL_MIGRATION.md](TUWUNEL_MIGRATION.md) - Tuwunel migration guide

### Operations
- [DEPLOYMENT.md](../operations/DEPLOYMENT.md) - Deployment procedures
- [TESTING.md](../operations/TESTING.md) - Testing strategies
- [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md) - Common issues

### External References
- [Matrix Specification](https://spec.matrix.org/)
- [Matrix Client-Server API](https://spec.matrix.org/latest/client-server-api/)
- [mautrix-discord](https://github.com/mautrix/discord) - Bridge reference
- [matrix-nio Documentation](https://matrix-nio.readthedocs.io/)

---

**Status**: 🟢 Production Ready
**Performance**: <1s message processing
**Last Verified**: 2025-11-17
