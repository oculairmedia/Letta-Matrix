# Matrix Identity Bridge MCP - Deployment Guide

## Quick Start

### 1. Configure Environment

Edit `.env` file with your Matrix credentials:

```bash
# Required
MATRIX_HOMESERVER_URL=http://tuwunel:6167
MATRIX_ADMIN_USERNAME=@admin:matrix.oculair.ca
MATRIX_ADMIN_PASSWORD=your_admin_password

# Optional
MCP_SERVER_PORT=3100
WEBHOOK_PORT=3101
LOG_LEVEL=info
```

### 2. Start Server

```bash
# Using Docker Compose
docker-compose up -d

# Check logs
docker-compose logs -f

# Check health
curl http://localhost:3101/health
```

### 3. Test MCP Endpoint

```bash
# MCP endpoint (JSON-RPC)
curl -X POST http://localhost:3100/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Architecture

### Core Components

- **Storage** (`/app/data/`)
  - `identities.json` - Matrix identity mappings
  - `dm_rooms.json` - DM room cache
  - `metadata.json` - Schema version info
  - `clients/` - Per-identity Matrix client storage

- **IdentityManager** - Auto-provision Matrix users via registration
- **ClientPool** - Manage per-identity Matrix clients
- **RoomManager** - DM creation and room operations
- **AdminAuth** - Centralized admin token management with caching

### Endpoints

| Port | Endpoint | Description |
|------|----------|-------------|
| 3100 | `/mcp` | MCP JSON-RPC endpoint |
| 3101 | `/webhook/tool-selector` | Webhook for tool calls |
| 3101 | `/webhooks/letta/agent-response` | Letta agent responses |
| 3101 | `/health` | Health check |

## Operations Reference

The `matrix_messaging` tool supports 30+ operations via the `operation` parameter.

### Message Operations

| Operation | Description |
|-----------|-------------|
| `send` | Send message to user/room |
| `read` | Read room message history |
| `react` | Add reaction to message |
| `edit` | Edit existing message |
| `typing` | Send typing indicator |

### Room Operations

| Operation | Description |
|-----------|-------------|
| `room_join` | Join a room by ID or alias |
| `room_leave` | Leave a room |
| `room_info` | Get room details (name, topic, members) |
| `room_list` | List joined rooms. Use `scope: 'server'` for all admin rooms |
| `room_create` | Create new room |
| `room_invite` | Invite user to room |
| `room_search` | Search messages in a room |
| `room_find` | Search room names server-wide (admin auth) |
| `room_members` | Get room member list (admin auth) |

### Identity Operations

| Operation | Description |
|-----------|-------------|
| `identity_create` | Create new Matrix identity |
| `identity_get` | Get identity details |
| `identity_list` | List all identities |
| `identity_derive` | Derive identity from context |

### Letta Operations

| Operation | Description |
|-----------|-------------|
| `letta_send` | Send message to Letta agent |
| `letta_chat` | Chat with Letta agent (with response tracking) |
| `letta_lookup` | Look up agent by name |
| `letta_list` | List available agents |
| `letta_identity` | Get/create agent's Matrix identity |
| `talk_to_agent` | High-level agent communication |

### OpenCode Operations

| Operation | Description |
|-----------|-------------|
| `opencode_connect` | Connect OpenCode session |
| `opencode_send` | Send message from OpenCode |
| `opencode_notify` | Send notification |
| `opencode_status` | Get connection status |

### Subscription Operations

| Operation | Description |
|-----------|-------------|
| `subscribe` | Subscribe to room events |
| `unsubscribe` | Unsubscribe from room |

## Examples

### Send a Message

```json
{
  "operation": "send",
  "identity_id": "my_bot",
  "room_id": "!roomid:matrix.oculair.ca",
  "message": "Hello!"
}
```

### Search for a Room (Server-wide)

```json
{
  "operation": "room_find",
  "query": "sapphic"
}
```

Returns rooms matching the query from all rooms the admin is in.

### List All Server Rooms

```json
{
  "operation": "room_list",
  "scope": "server"
}
```

Returns all 186+ rooms the admin account is member of (vs just identity's joined rooms).

### Get Room Members

```json
{
  "operation": "room_members",
  "room_id": "!pyppN2xmF9KN0r7RD9:matrix.oculair.ca"
}
```

Returns all members with display names and membership status.

### Chat with Letta Agent

```json
{
  "operation": "letta_chat",
  "agent_id": "agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
  "message": "Hello, how are you?"
}
```

### Create Identity

```json
{
  "operation": "identity_create",
  "id": "my_bot",
  "localpart": "mybot",
  "display_name": "My Bot",
  "type": "custom"
}
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MATRIX_HOMESERVER_URL` | Yes | - | Matrix homeserver URL (internal) |
| `MATRIX_ADMIN_USERNAME` | Yes | - | Admin user MXID |
| `MATRIX_ADMIN_PASSWORD` | Yes | - | Admin user password |
| `MCP_SERVER_PORT` | No | 3100 | MCP server port |
| `WEBHOOK_PORT` | No | 3101 | Webhook server port |
| `DATA_DIR` | No | ./data | Storage directory |
| `LOG_LEVEL` | No | info | Log level |
| `LETTA_BASE_URL` | No | - | Letta API URL |
| `LETTA_API_KEY` | No | - | Letta API key |

## Admin Authentication

Server-wide operations (`room_find`, `room_list` with server scope, `room_members`) use admin credentials to access rooms beyond the calling identity's membership.

The `admin-auth.ts` module provides:
- Cached token management (5-minute TTL)
- Automatic token refresh on expiry
- Centralized config via `getAdminConfig()` and `getAdminToken()`

## Development

### Local Development

```bash
# Install dependencies
npm install

# Run in dev mode (watch)
npm run dev

# Build
npm run build

# Run tests
npm test
```

### Building Docker Image

```bash
# Build locally
docker build -t matrix-identity-bridge .

# Or use GitHub Actions (auto-builds on push to main)
```

The Dockerfile includes a `CACHEBUST` arg to ensure fresh builds:
```dockerfile
ARG CACHEBUST=1
```

GitHub Actions passes `CACHEBUST=${{ github.sha }}` to invalidate cache on each commit.

## Monitoring

### Health Check

```bash
curl http://localhost:3101/health
```

### Logs

```bash
docker logs matrix-messaging-mcp --tail 50 -f
```

## Troubleshooting

### Admin Auth Fails

**Issue:** `MATRIX_ADMIN_PASSWORD not configured`

**Solution:** Ensure `.env` has `MATRIX_ADMIN_PASSWORD` set.

### Room Operations Return Empty

**Issue:** `room_list` returns few rooms

**Solution:** Use `scope: 'server'` to see all rooms admin is in:
```json
{"operation": "room_list", "scope": "server"}
```

### Docker Cache Issues

**Issue:** New code not deployed after push

**Solution:** The CACHEBUST build-arg should handle this. If issues persist:
```bash
docker pull ghcr.io/oculairmedia/letta-matrix-identity-bridge:latest
docker restart matrix-messaging-mcp
```

### Storage Persistence

Data is stored in `./data` directory. To backup:

```bash
docker-compose down
tar -czf mcp-backup-$(date +%Y%m%d).tar.gz data/
```

## Security Notes

- Admin password grants server-wide room access - protect carefully
- Store `.env` securely, never commit to git
- Identities are auto-provisioned with random passwords
- Access tokens are stored in `identities.json`
- Admin tokens are cached in memory only (not persisted)
