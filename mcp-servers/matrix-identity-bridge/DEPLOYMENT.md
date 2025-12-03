# Matrix Messaging MCP - Deployment Guide

## Quick Start

### 1. Configure Environment

Edit `.env` file with your Synapse admin token:

```bash
# Required
MATRIX_HOMESERVER_URL=https://matrix.oculair.ca
MATRIX_ADMIN_TOKEN=your_synapse_admin_token

# Optional
MCP_SERVER_PORT=3100
LOG_LEVEL=info
```

### 2. Start Server

```bash
# Using Docker Compose
docker-compose up -d

# Check logs
docker-compose logs -f

# Check health
curl http://localhost:3100/health
```

### 3. Test MCP Endpoint

```bash
# SSE endpoint (for MCP clients)
curl http://localhost:3100/mcp
```

## Architecture

### Core Components

- **Storage** (`/app/data/`)
  - `identities.json` - Matrix identity mappings
  - `dm_rooms.json` - DM room cache
  - `metadata.json` - Schema version info
  - `clients/` - Per-identity Matrix client storage

- **IdentityManager** - Auto-provision Matrix users via Synapse Admin API
- **ClientPool** - Manage per-identity Matrix clients
- **RoomManager** - DM creation and room operations

### MCP Tools (Universal Layer)

#### msg_send
Send messages as any Matrix identity. Auto-creates DM rooms.

```json
{
  "identity_id": "my_bot",
  "to_mxid": "@user:matrix.org",
  "message": "Hello!",
  "msgtype": "m.text"
}
```

#### msg_read
Read message history from a room.

```json
{
  "identity_id": "my_bot",
  "room_id": "!roomid:matrix.org",
  "limit": 50
}
```

#### room_join
Join a Matrix room.

```json
{
  "identity_id": "my_bot",
  "room_id_or_alias": "#room:matrix.org"
}
```

#### room_leave
Leave a Matrix room.

```json
{
  "identity_id": "my_bot",
  "room_id": "!roomid:matrix.org"
}
```

#### room_info
Get room information (name, topic, members).

```json
{
  "identity_id": "my_bot",
  "room_id": "!roomid:matrix.org"
}
```

#### room_list
List all joined rooms for an identity.

```json
{
  "identity_id": "my_bot"
}
```

#### identity_create
Create a new Matrix identity (provisions user via Synapse Admin API).

```json
{
  "id": "my_bot",
  "localpart": "mybot",
  "display_name": "My Bot",
  "avatar_url": "mxc://matrix.org/...",
  "type": "custom"
}
```

#### identity_get
Get identity information.

```json
{
  "identity_id": "my_bot"
}
```

## Integration with Letta

Add as MCP server in Letta:

```bash
# Using Letta API
curl -X POST https://letta.oculair.ca/v1/tools/mcp/servers \
  -H "Authorization: Bearer $LETTA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "matrix-messaging",
    "url": "http://192.168.50.90:3100/mcp",
    "transport": "sse"
  }'
```

## Development

### Local Development

```bash
# Install dependencies
npm install

# Run in dev mode (watch)
npm run dev

# Build
npm run build

# Run built version
npm start
```

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MATRIX_HOMESERVER_URL` | Yes | - | Matrix homeserver URL |
| `MATRIX_ADMIN_TOKEN` | Yes | - | Synapse admin token |
| `MCP_SERVER_PORT` | No | 3100 | HTTP server port |
| `MCP_SERVER_HOST` | No | 0.0.0.0 | HTTP server host |
| `MCP_TRANSPORT` | No | stdio | Transport mode (http/stdio) |
| `DATA_DIR` | No | ./data | Storage directory |
| `LOG_LEVEL` | No | info | Log level |

## Monitoring

### Health Check

```bash
curl http://localhost:3100/health
```

Response:
```json
{
  "status": "healthy",
  "service": "matrix-messaging-mcp",
  "timestamp": "2025-11-30T05:00:00.000Z"
}
```

### Logs

```bash
# Docker logs
docker-compose logs -f

# View specific service
docker-compose logs -f matrix-messaging-mcp
```

## Troubleshooting

### Identity Creation Fails

**Issue:** `Failed to create Synapse user: 403`

**Solution:** Verify `MATRIX_ADMIN_TOKEN` has admin permissions:

```bash
curl -H "Authorization: Bearer $MATRIX_ADMIN_TOKEN" \
  https://matrix.oculair.ca/_synapse/admin/v1/server_version
```

### Client Connection Issues

**Issue:** Matrix clients fail to connect

**Solution:** Check homeserver URL is accessible from container:

```bash
docker-compose exec matrix-messaging-mcp wget -O- https://matrix.oculair.ca/_matrix/client/versions
```

### Storage Persistence

Data is stored in `./data` directory. To backup:

```bash
# Stop server
docker-compose down

# Backup data
tar -czf mcp-backup-$(date +%Y%m%d).tar.gz data/

# Restore
tar -xzf mcp-backup-YYYYMMDD.tar.gz
```

## Security Notes

- Admin token grants full Synapse access - protect carefully
- Store `.env` securely, never commit to git
- Identities are auto-provisioned with random passwords
- Access tokens are stored in `identities.json`

## Next Steps

### Phase 2: Universal Layer Tools
- Advanced room operations
- Message reactions
- File uploads
- Typing indicators

### Phase 3: Letta Integration Layer
- Auto-identity from `agent_id`
- Memory block storage
- Agent lookup tools

### Phase 4: OpenCode Integration Layer
- Auto-identity from `directory`
- SSE bridge for bidirectional messaging
- Session status tools
