# Tuwunel Migration Guide

## Overview

This branch (`feature/tuwunel-migration`) replaces the Python-based Synapse Matrix homeserver with **Tuwunel**, a high-performance Rust-based Matrix homeserver that is the official successor to conduwuit.

## Key Changes

### What's Different

1. **Homeserver**: Synapse (Python) → Tuwunel (Rust)
2. **Database**: PostgreSQL → Built-in RocksDB (embedded, no separate DB container needed)
3. **Port**: 8008 (Synapse) → 6167 (Tuwunel internal)
4. **Performance**: Tuwunel is significantly lighter and faster
5. **Configuration**: TOML file + environment variables

### What Stays the Same

- ✅ All Letta integration components (matrix-client, agent-user-manager)
- ✅ MCP servers (mcp-server, letta-agent-mcp)
- ✅ Matrix API service
- ✅ Element web client
- ✅ Nginx reverse proxy
- ✅ Agent auto-discovery and room management
- ✅ Multi-agent support with individual Matrix identities

## Architecture

```
Letta Agents ←→ Agent Manager ←→ Matrix Users ←→ Tuwunel ←→ Matrix Rooms
     ↓              ↓                ↓              ↓            ↓
MCP Server ←→ Matrix API ←→ Matrix Client ←→ Element Web ←→ Bridges
```

### Container Stack

- `tuwunel` - Matrix homeserver (Rust-based, RocksDB storage)
- `element` - Element web client UI
- `nginx` - Reverse proxy (HTTP/HTTPS/Federation)
- `matrix-client` - Custom client with agent sync
- `matrix-api` - REST API for Matrix operations
- `mcp-server` - Matrix MCP tools server
- `letta-agent-mcp` - Inter-agent communication MCP

## Deployment

### Prerequisites

- Docker and Docker Compose installed
- Domain pointed to server (matrix.oculair.ca)
- Cloudflare or reverse proxy handling SSL
- Letta running on http://192.168.50.90:1416

### Fresh Installation

1. **Stop existing Synapse deployment** (if running):
   ```bash
   docker-compose down -v
   ```

2. **Clean up old data** (fresh start):
   ```bash
   rm -rf ./postgres-data
   rm -rf ./synapse-data
   rm -rf ./matrix_store/*
   rm -rf ./matrix_client_data/*
   ```

3. **Create Tuwunel data directory**:
   ```bash
   mkdir -p ./tuwunel-data
   chmod 777 ./tuwunel-data  # Tuwunel runs as specific UID
   ```

4. **Configure environment**:
   ```bash
   cp .env.tuwunel .env
   # Edit .env if needed
   ```

5. **Start Tuwunel stack**:
   ```bash
   docker-compose -f docker-compose.tuwunel.yml up -d
   ```

6. **Monitor logs**:
   ```bash
   docker-compose -f docker-compose.tuwunel.yml logs -f tuwunel
   docker-compose -f docker-compose.tuwunel.yml logs -f matrix-client
   ```

### Verify Installation

1. **Check Tuwunel health**:
   ```bash
   curl http://localhost:6167/_matrix/client/versions
   ```

2. **Access Element**:
   - Navigate to http://matrix.oculair.ca (via Cloudflare)
   - Or http://192.168.50.90:8008 (local)

3. **Register admin user**:
   - Open Element
   - Click "Create Account"
   - Username: `admin`
   - Password: `admin123`
   - First registration becomes server admin

4. **Check agent sync**:
   ```bash
   docker-compose -f docker-compose.tuwunel.yml logs matrix-client | grep "agent sync"
   ```

## Configuration

### Tuwunel Environment Variables

Key settings in `.env.tuwunel`:

```bash
MATRIX_SERVER_NAME=matrix.oculair.ca    # Your Matrix domain
ALLOW_REGISTRATION=true                  # Allow new user registration
REGISTRATION_TOKEN=                      # Optional token for registration
TUWUNEL_DATA_PATH=./tuwunel-data        # Database storage path
```

### Advanced Tuwunel Config

For advanced configuration, create `tuwunel.toml` and mount it:

```toml
[global]
server_name = "matrix.oculair.ca"
database_path = "/var/lib/tuwunel"
allow_registration = true

[global.well_known]
client = "https://matrix.oculair.ca"
server = "matrix.oculair.ca:443"
```

Then update docker-compose.tuwunel.yml:
```yaml
volumes:
  - ./tuwunel-data:/var/lib/tuwunel
  - ./tuwunel.toml:/etc/tuwunel.toml:ro
```

## Agent Integration

### How It Works

1. **Agent Discovery**: `agent_user_manager.py` polls Letta proxy every 0.5s
2. **User Creation**: Each agent gets a Matrix user `@agent_{uuid}:matrix.oculair.ca`
3. **Room Creation**: Each agent gets a dedicated chat room
4. **Space Organization**: All agent rooms organized in "Letta Agents" space
5. **Auto-Response**: Agents respond in their rooms as their Matrix user

### Agent Sync Process

```
Letta Proxy (port 1416) → Agent Discovery → Create Matrix User → Create Room → Add to Space
                                                    ↓
                                            Store in mappings.json
                                                    ↓
                                            Auto-join admins to room
```

### Mappings File

Agent mappings stored in `./matrix_client_data/agent_user_mappings.json`:

```json
{
  "agent_id": "agent-e54fc601-4773-4116-9c6c-cf45da2e269e",
  "agent_name": "Meridian",
  "matrix_user_id": "@agent_e54fc601_4773_4116_9c6c_cf45da2e269e:matrix.oculair.ca",
  "matrix_password": "password",
  "room_id": "!xyz123:matrix.oculair.ca",
  "created": true
}
```

## Differences from Synapse

### Performance

- **Startup Time**: Tuwunel starts in ~5s vs Synapse ~30s
- **Memory Usage**: ~50MB vs Synapse ~500MB
- **Database**: Embedded RocksDB (no PostgreSQL needed)
- **CPU**: Rust efficiency vs Python overhead

### Features

- ✅ Full Matrix Client-Server API support
- ✅ Federation support
- ✅ Application services (bridges)
- ✅ Media repository
- ✅ Push notifications
- ⚠️ Admin API slightly different (see Tuwunel docs)

### Migration Notes

- **No automatic migration** from Synapse yet (planned)
- Fresh start required (all users/rooms recreated)
- Bridges need reconfiguration
- Room aliases need recreation

## Troubleshooting

### Tuwunel Won't Start

```bash
# Check logs
docker-compose -f docker-compose.tuwunel.yml logs tuwunel

# Common issues:
# 1. Data directory permissions
sudo chown -R 1000:1000 ./tuwunel-data

# 2. Port conflicts
sudo lsof -i :6167
```

### Agent Sync Not Working

```bash
# Check agent discovery endpoint
curl http://192.168.50.90:1416/v1/models

# Check matrix-client logs
docker-compose -f docker-compose.tuwunel.yml logs matrix-client

# Verify Letta proxy is running
docker ps | grep letta-proxy
```

### Can't Register Users

```bash
# Check registration is enabled
docker exec tuwunel-tuwunel-1 cat /var/lib/tuwunel/tuwunel.toml | grep allow_registration

# If disabled, update .env:
ALLOW_REGISTRATION=true

# Then restart:
docker-compose -f docker-compose.tuwunel.yml restart tuwunel
```

### Federation Not Working

```bash
# Check federation port is exposed
docker-compose -f docker-compose.tuwunel.yml ps

# Test federation
curl https://matrix.oculair.ca:8448/_matrix/federation/v1/version

# Check nginx config
docker exec tuwunel-nginx-1 nginx -t
```

## Rollback to Synapse

If you need to rollback:

```bash
# Stop Tuwunel
docker-compose -f docker-compose.tuwunel.yml down -v

# Switch back to main branch
git checkout main

# Start Synapse
docker-compose up -d
```

## Resources

- **Tuwunel Docs**: https://matrix-construct.github.io/tuwunel/
- **Tuwunel GitHub**: https://github.com/matrix-construct/tuwunel
- **Matrix Spec**: https://spec.matrix.org/
- **Element Help**: https://element.io/help

## Support

- Tuwunel Community: [#tuwunel:matrix.org](https://matrix.to/#/#tuwunel:matrix.org)
- Issues: https://github.com/matrix-construct/tuwunel/issues
- Direct contact: [@jason:tuwunel.me](https://matrix.to/#/@jason:tuwunel.me)

## Next Steps

1. ✅ Deploy Tuwunel with Letta integration
2. ⏭️ Test agent discovery and room creation
3. ⏭️ Configure bridges (GMMessages, Discord)
4. ⏭️ Set up federation
5. ⏭️ Performance tuning and monitoring
