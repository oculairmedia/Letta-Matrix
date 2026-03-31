# Matrix Tuwunel Deploy

A comprehensive Matrix deployment with Letta AI agent integration, MCP tooling, and multi-agent identity management.

## Architecture

This deployment uses **Tuwunel** as the Matrix homeserver — a lightweight, Rust-based Matrix server using embedded RocksDB. There is no PostgreSQL database.

### Services

| Service | Purpose |
|---------|---------|
| `tuwunel` | Matrix homeserver (port 6167 internal, exposed via nginx) |
| `nginx` | Reverse proxy — routes Matrix, Element, and API traffic |
| `element` | Element web client |
| `matrix-client` | Core bot — agent routing, message processing, streaming |
| `matrix-api` | FastAPI REST API (port 8004) — identity, agent sync, messaging |
| `matrix-messaging-mcp` | MCP server for Matrix messaging tools |
| `opencode-bridge` | Bridge for OpenCode ↔ Matrix integration |
| `temporal-worker` | Temporal workflow worker for async tasks |
| `ntfy` / `ntfy-bridge` | Push notification relay |
| `livekit` / `lk-jwt-service` / `element-call` | Voice/video calling stack |
| `matrix-internal` | Internal Matrix utilities |

### Key Differences from Synapse

- **No PostgreSQL** — Tuwunel uses RocksDB (embedded)
- **No `/_synapse/admin/*` endpoints** — use Matrix client APIs or Tuwunel-specific admin endpoints
- **No `synapse-data/`** — all homeserver data lives in `./tuwunel-data/`
- **Service name** is `tuwunel`, not `synapse`

## Quick Start

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your domain and secrets

# Start everything
docker-compose up -d

# Check logs
docker-compose logs -f tuwunel
docker-compose logs -f matrix-client
```

**Access:**
- Element Web: https://your-domain (via nginx)
- Matrix API: `/_matrix/client/v3/*`

## Source Structure

```
src/
├── api/              # FastAPI routes and schemas
│   ├── routes/       # Agent sync, identity, messaging, DM rooms, portal links
│   └── schemas/      # Pydantic models
├── bridges/          # Letta ↔ Matrix bridge
├── core/             # Agent provisioning, room management, identity, spaces
├── letta/            # Letta client, webhook handler, streaming, approval
├── matrix/           # Matrix client, message processing, file handling, streaming
├── models/           # Data models (agent mapping, conversation, identity)
├── utils/            # Password utilities, SSRF protection
└── voice/            # TTS, transcription, directive parsing
```

### Key Modules

- **`src/matrix/client.py`** — Main Matrix client with sync loop and event handling
- **`src/letta/webhook_handler.py`** — Processes Letta agent responses into Matrix messages
- **`src/core/agent_user_manager.py`** — Agent ↔ Matrix user provisioning
- **`src/core/room_manager.py`** — Room lifecycle management
- **`src/core/space_manager.py`** — Letta Agents space organization
- **`src/matrix/streaming.py`** — Live streaming of agent responses

## Configuration

All configuration is in `.env`. Key settings:

```bash
MATRIX_SERVER_NAME=matrix.oculair.ca    # Your Matrix domain
TUWUNEL_DATA_DIR=./tuwunel-data          # Homeserver data directory
MATRIX_ADMIN_USERNAME=@matrixadmin:matrix.oculair.ca
MATRIX_USERNAME=@letta:matrix.oculair.ca
DEV_MODE=true                            # Simple passwords for development
```

## Data Persistence

- `./tuwunel-data/` — Tuwunel's RocksDB database (all homeserver data)
- `./matrix_store/` — Matrix client session data
- `./matrix_client_data/` — Agent mappings and space configuration

## Multi-Agent System

Each Letta agent gets its own Matrix identity:

- **Username**: `@agent_{uuid}:matrix.oculair.ca` (stable, ID-based)
- **Display name**: Agent's human-readable name (auto-updates on rename)
- **Dedicated room**: `{agent-name} - Letta Agent Chat`
- **Space**: All agent rooms organized under "Letta Agents" space

### Message Flow

1. User sends message in agent room
2. `matrix-client` routes to the correct Letta agent
3. Agent processes and returns response (optionally streamed)
4. Response posted as the agent's own Matrix user

## Troubleshooting

```bash
# View service logs
docker-compose logs -f tuwunel
docker-compose logs -f matrix-client
docker-compose logs -f matrix-api

# Check agent sync
docker logs matrix-tuwunel-deploy-matrix-client-1 | grep "agent sync"

# Health checks
curl http://localhost:8004/health        # Matrix API
curl http://localhost:8008/_matrix/client/versions  # Tuwunel
```

## Documentation

- [Architecture Diagram](./ARCHITECTURE_DIAGRAM.md) — Full system architecture
- [Tuwunel Migration](./TUWUNEL_MIGRATION.md) — Migration details from Synapse
- [Matrix MCP Tools](./MATRIX_MCP_TOOLS.md) — MCP tool reference
- [Testing](./TESTING.md) — Test suite documentation
- [CI/CD Setup](./CI_CD_SETUP.md) — GitHub Actions pipeline
- [OpenCode Integration](./OPENCODE_MATRIX_INTEGRATION.md) — OpenCode bridge docs
