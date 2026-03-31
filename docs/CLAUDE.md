# Letta Matrix Integration — Developer Reference

## Overview

Multi-agent Matrix integration where each Letta AI agent gets its own Matrix identity. Agents participate in conversations through their own accounts with automatic provisioning, room creation, and space organization.

**Homeserver:** Tuwunel (Rust-based, embedded RocksDB — no PostgreSQL)

## Architecture

```
Letta Agents ←→ Webhook Handler ←→ Matrix Client ←→ Tuwunel Homeserver
     ↓                ↓                  ↓                    ↓
 Agent API    Streaming/Live Edit   Agent Identity Pool   "Letta Agents" Space
     ↓                                   ↓
 MCP Tools ←→ Matrix Messaging MCP    Room Manager
```

### Core Components

1. **Tuwunel Homeserver** (`tuwunel` service)
   - RocksDB embedded database (no PostgreSQL)
   - Port 6167 internal, exposed via nginx
   - Admin users: `@letta`, `@matrixadmin`, `@admin` at `matrix.oculair.ca`

2. **Matrix Client** (`src/matrix/client.py`)
   - Main sync loop and event handling
   - Agent-specific message routing (`src/matrix/message_router.py`)
   - Mention detection and routing (`src/matrix/mention_routing.py`)
   - File handling (images, audio, documents) in `src/matrix/file_*.py`
   - Streaming responses with live edit (`src/matrix/streaming*.py`)

3. **Letta Bridge** (`src/bridges/letta_matrix_bridge.py`, `src/letta/`)
   - Webhook handler for agent responses (`src/letta/webhook_handler.py`)
   - Letta client wrapper (`src/letta/client.py`)
   - Message retry buffer (`src/letta/message_retry_buffer.py`)
   - Gateway stream reader (`src/letta/gateway_stream_reader.py`)
   - Approval manager (`src/letta/approval_manager.py`)

4. **Agent Management** (`src/core/`)
   - User provisioning (`src/core/agent_user_manager.py`)
   - Room lifecycle (`src/core/room_lifecycle.py`, `room_manager.py`)
   - Space management (`src/core/space_manager.py`)
   - Identity storage (`src/core/identity_storage.py`)
   - Agent sync orchestrator (`src/core/agent_sync_orchestrator.py`)
   - Avatar service (`src/core/avatar_service.py`)

5. **Matrix API** (`src/api/`)
   - FastAPI REST interface on port 8004
   - Routes: identity, agent sync, messaging, DM rooms, portal links
   - Auth middleware (`src/api/auth.py`)

6. **MCP Server** (`matrix-messaging-mcp` service)
   - Matrix messaging tools via MCP protocol
   - Identity bridge (`mcp-servers/matrix-identity-bridge/`)

## Multi-Agent System

### Agent Identity
- Each agent gets a dedicated Matrix user
- Username: `@agent_{uuid}:matrix.oculair.ca` (stable, ID-based)
- Display name: Agent's human-readable name (auto-updates)
- Credentials stored in `matrix_client_data/agent_user_mappings.json`

### Agent Rooms
- Dedicated room per agent: `{name} - Letta Agent Chat`
- Organized under "Letta Agents" Matrix Space
- Members: agent user, @letta, @matrixadmin, @admin
- Persistent across restarts

### Message Flow
1. User sends message in agent room
2. `matrix-client` detects and routes to Letta agent
3. Agent response streamed back via webhook handler
4. Response posted as the agent's own Matrix identity
5. Optional: live edit streaming for long responses

## File Structure

### Source Code
```
src/
├── api/           # FastAPI routes (identity, agent sync, messaging)
├── bridges/       # Letta ↔ Matrix bridge
├── core/          # Agent provisioning, rooms, spaces, identity
├── letta/         # Letta client, webhooks, streaming, approval
├── matrix/        # Matrix client, message processing, files, streaming
├── models/        # Data models (agent mapping, conversation, identity)
├── utils/         # Password, SSRF protection
└── voice/         # TTS, transcription
```

### Configuration
- `.env` — Environment variables and credentials
- `docker-compose.yml` — All service definitions
- `nginx_tuwunel_proxy.conf` — Nginx routing
- `element-config.json` — Element web client config
- `livekit.yaml` — LiveKit voice/video config

### Data
- `tuwunel-data/` — Tuwunel's RocksDB database
- `matrix_store/` — Matrix client session data
- `matrix_client_data/` — Agent mappings and space config

## Key Environment Variables

```bash
DEV_MODE=true                                        # Simple passwords
MATRIX_SERVER_NAME=matrix.oculair.ca                 # Matrix domain
MATRIX_ADMIN_USERNAME=@matrixadmin:matrix.oculair.ca # Admin user
MATRIX_USERNAME=@letta:matrix.oculair.ca             # Bot user
LETTA_BASE_URL=http://192.168.50.90:8283             # Letta API
```

## Testing

```bash
# Run all tests
./run_tests.sh

# Run specific test suites
pytest tests/unit/ -v
pytest tests/integration/ -v
```

See [TESTING.md](./TESTING.md) and [TESTING_STRATEGY.md](./TESTING_STRATEGY.md) for details.

## Monitoring

```bash
# Service health
curl http://localhost:8004/health

# Tuwunel status
curl http://localhost:8008/_matrix/client/versions

# Agent sync logs
docker logs matrix-tuwunel-deploy-matrix-client-1 | grep "agent sync"
```
