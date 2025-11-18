# Letta-Matrix Integration Project

## ⚠️ CRITICAL: We Use Tuwunel, NOT Synapse! ⚠️

**THIS DEPLOYMENT USES TUWUNEL AS THE MATRIX HOMESERVER, NOT SYNAPSE!**

### Why This Matters

We have made this mistake multiple times, so let's be very clear:

- **Homeserver**: **Tuwunel** (lightweight embedded Matrix server written in Rust)
- **NO Synapse**: This is NOT a Synapse deployment despite the repo name
- **NO PostgreSQL**: Tuwunel uses RocksDB (embedded key-value database)
- **NO Synapse Admin APIs**: Standard Synapse admin endpoints like `/_synapse/admin/v1/*` **DO NOT EXIST**

### Common Mistakes to Avoid

❌ **DON'T DO THIS:**
- Trying to use `/_synapse/admin/v1/*` endpoints (they don't exist in Tuwunel)
- Looking for PostgreSQL database or `psql` connections
- Following Synapse-specific documentation
- Trying to use `docker-compose logs synapse` (service is called `tuwunel`)
- Looking for `homeserver.yaml` configuration (Tuwunel uses different config)

✅ **DO THIS INSTEAD:**
- Use Matrix client API (`/_matrix/client/v3/*`) for all operations
- Check Tuwunel logs: `docker-compose logs tuwunel`
- Data is in `./tuwunel-data/` (RocksDB files)
- Use our Python scripts with Matrix client libraries (in `scripts/`)
- Remember: Tuwunel is a different server implementation!

### Quick Reference

| What | Value |
|------|-------|
| Homeserver Type | **Tuwunel** |
| Database | RocksDB (embedded) |
| Data Directory | `./tuwunel-data/` |
| Admin APIs | Matrix Client API only |
| Container Name | `matrix-synapse-deployment-tuwunel-1` |
| Service Name | `tuwunel` |

---

## Project Overview

This is a production Matrix deployment integrating Letta AI agents with Matrix chat via a custom bridge. Letta agents can communicate through Matrix rooms, managed automatically by the system.

### Architecture Components

1. **Tuwunel** - Matrix homeserver (port 8008 via nginx)
2. **Matrix Client** - Python service that manages agent users and message routing
3. **Matrix API** - FastAPI service exposing REST endpoints for Matrix operations
4. **MCP Server** - Model Context Protocol server for Matrix tools
5. **Letta Agent MCP** - Letta-specific MCP tools and agent management
6. **Element Web** - Web UI for Matrix (optional)
7. **Nginx** - Reverse proxy routing traffic to Tuwunel

### Key Services

```bash
# View all running services
docker-compose ps

# Key services:
# - tuwunel: The Matrix homeserver
# - matrix-client: Agent sync and message routing
# - matrix-api: REST API for Matrix operations
# - mcp-server: MCP tools for Matrix
# - letta-agent-mcp: Letta agent tools
# - nginx: Reverse proxy
# - element: Web client
```

## Data Storage

### Tuwunel Data
- **Location**: `./tuwunel-data/`
- **Type**: RocksDB database files (.sst files)
- **Contains**: ALL Matrix homeserver data (users, rooms, messages, etc.)
- **Backup**: Use `./tuwunel-data.backup-*` directories

### Matrix Client Data
- **Location**: `./matrix_client_data/`
- **Files**:
  - `agent_user_mappings.json` - Maps Letta agents to Matrix users/rooms
  - `letta_space_config.json` - Letta Agents space configuration
  - `async_requests.json` - Async operation tracking

### Other Data
- `./synapse-data/` - Legacy configs, bridge registration files
- `./matrix_store/` - Matrix client session data
- `./scripts/` - Python helper scripts

## Key Workflows

### Space Management (Current Focus)

**Problem Solved**: Space recreation loops causing multiple "Letta Agents" spaces

**Solution**: Automatic space validation with loop prevention
- Validates existing space on every sync cycle
- Recreates space if invalid/missing
- **NEW**: Validates newly created space before accepting it
- Restores old space ID if new space fails validation (prevents loops)

**Key Files**:
- `src/core/agent_user_manager.py` - Space validation logic (lines 346-390)
- `src/core/space_manager.py` - Space creation and management
- `tests/unit/test_agent_user_manager_space.py` - Comprehensive tests (30 tests)

**Current Space**: 
```bash
cat matrix_client_data/letta_space_config.json
# Shows current space ID (should be stable across syncs)
```

### Agent Sync Workflow

Every 60 seconds, the matrix-client service:

1. **Validates Space** - Checks if "Letta Agents" space exists and is valid
2. **Loads Agents** - Fetches all Letta agents from Letta API
3. **Creates/Updates Users** - Ensures each agent has a Matrix user
4. **Creates Rooms** - Creates DM rooms for agents if missing
5. **Adds to Space** - Adds all agent rooms to the Letta Agents space
6. **Validates Rooms** - Checks that rooms are accessible
7. **Monitors Messages** - Listens for messages and routes to agents

### Message Routing

When a message arrives in an agent's room:
1. Matrix client receives message via `/sync`
2. Identifies which agent owns the room
3. Sends message to Letta agent via Letta API
4. Agent processes and responds
5. Response posted back to Matrix room

## Environment Configuration

Key environment variables (`.env`):

```bash
# Letta API
LETTA_API_URL=https://letta.oculair.ca
LETTA_PASSWORD=<letta-admin-token>

# Matrix Server
MATRIX_SERVER_URL=http://nginx:80
MATRIX_HOMESERVER=matrix.oculair.ca
MATRIX_ADMIN_USER=admin
MATRIX_ADMIN_PASSWORD=<admin-password>

# Agent Sync
LETTA_AGENT_POLLING_INTERVAL=60
```

## Development & Testing

### Running Tests

```bash
# All tests
python3 -m pytest

# Specific test file
python3 -m pytest tests/unit/test_agent_user_manager_space.py -v

# Specific test
python3 -m pytest tests/unit/test_agent_user_manager_space.py::TestSpaceValidationAndRecreation::test_sync_recreates_invalid_space -v

# With coverage
python3 -m pytest --cov=src --cov-report=html
```

### Viewing Logs

```bash
# Matrix client (agent sync)
docker logs -f matrix-synapse-deployment-matrix-client-1

# Filter for space operations
docker logs -f matrix-synapse-deployment-matrix-client-1 2>&1 | grep -i space

# Filter for agent sync
docker logs -f matrix-synapse-deployment-matrix-client-1 2>&1 | grep AGENT_SYNC

# Tuwunel homeserver
docker logs -f matrix-synapse-deployment-tuwunel-1
```

### Common Operations

```bash
# Restart matrix-client (reloads code)
docker-compose restart matrix-client

# Pull latest image
docker pull ghcr.io/oculairmedia/letta-matrix-client:latest
docker-compose restart matrix-client

# View agent mappings
cat matrix_client_data/agent_user_mappings.json | python3 -m json.tool

# View current space
cat matrix_client_data/letta_space_config.json
```

## CI/CD Pipeline

GitHub Actions workflows in `.github/workflows/`:

1. **Test Suite** - Runs all pytest tests
2. **Lint and Code Quality** - Ruff, mypy, formatting checks
3. **Build Tuwunel Docker Image** - Builds and pushes Tuwunel image
4. **Docker Build Matrix API** - Builds matrix-api service
5. **Docker Build Matrix Client** - Builds matrix-client service (our main service)

**Build Status**: Check with `gh run list --limit 5`

**Auto-Deploy**: Images pushed to `ghcr.io/oculairmedia/letta-matrix-*:latest`

## Recent Work (November 2025)

### Space Validation & Loop Prevention

**Commits**:
- `1c75af8` - Room validation in sync loop
- `549b04e` - Automatic space validation and recreation
- `a83b154` - Comprehensive test coverage (7 new tests)
- `1c403cc` - Prevent space recreation loops by validating new spaces
- `fe0a0e6` - Fix test mocks for space validation

**Problem**: System was creating multiple "Letta Agents" spaces because:
1. Space validation detected invalid space
2. New space created
3. New space not validated before accepting
4. Next sync cycle: new space also detected as invalid
5. Another new space created (infinite loop)

**Solution**:
- After creating new space, validate it works
- If validation succeeds: use new space
- If validation fails: restore old space ID to config
- Prevents recreation loop by not saving invalid new space

**Status**: ✅ Deployed and working
- All tests passing (30/30)
- CI/CD green
- Deployed to production
- Monitoring logs for stability

## Helper Scripts

Located in `scripts/`:

### Admin Scripts (`scripts/admin/`)
- `create_admin.py` - Create admin user
- `admin_join_rooms.py` - Join admin to all rooms

### Testing Scripts (`scripts/testing/`)
- `check_room_messages.py` - View room messages
- `send_to_admin_simple.py` - Send test message to admin
- `test_agent_identity.py` - Test agent identities
- `test_agent_routing.py` - Test message routing

### Cleanup Scripts (`scripts/cleanup/`)
- `cleanup_agent_rooms.py` - Remove agent rooms
- `cleanup_agent_users.py` - Remove agent users
- `cleanup_old_agents.py` - Remove obsolete agents

## Troubleshooting

### Space Issues

**Symptom**: Multiple "Letta Agents" spaces appearing

**Check**:
```bash
# View current space
cat matrix_client_data/letta_space_config.json

# Check logs for space recreation
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep -i "space"
```

**Fix**: Already deployed! Space validation prevents loops.

### Agent Not Responding

**Check**:
1. Agent exists in Letta: `curl -H "Authorization: Bearer $LETTA_PASSWORD" $LETTA_API_URL/v1/agents`
2. Agent has mapping: `cat matrix_client_data/agent_user_mappings.json`
3. Room exists and is valid: Check logs for room validation
4. Matrix client is running: `docker ps | grep matrix-client`

### Connection Issues

**Remember**: We use Tuwunel, not Synapse!

```bash
# Check Tuwunel is running
docker ps | grep tuwunel

# Check nginx proxy
docker ps | grep nginx

# Test Matrix API (should work)
curl http://localhost:8008/_matrix/client/versions

# Test Synapse admin API (will NOT work - we don't use Synapse!)
# DON'T: curl http://localhost:8008/_synapse/admin/v1/...
```

## Useful Documentation

- `docs/README.md` - Main documentation (updated with Tuwunel warnings)
- `docs/TUWUNEL_MIGRATION.md` - Migration from Synapse to Tuwunel
- `docs/TEST_COVERAGE_SUMMARY.md` - Testing documentation
- `docs/QUICK_TEST_REFERENCE.md` - Quick test commands
- `docs/MATRIX_FIXES_2025_01_07.md` - Recent fixes

## Project Structure

```
.
├── src/
│   ├── core/              # Core business logic
│   │   ├── agent_user_manager.py   # Agent sync & space management
│   │   └── space_manager.py        # Space operations
│   ├── matrix/            # Matrix client wrapper
│   ├── api/               # REST API (FastAPI)
│   ├── mcp/               # MCP server & tools
│   └── utils/             # Utilities
├── tests/
│   ├── unit/              # Unit tests
│   └── integration/       # Integration tests
├── scripts/               # Helper scripts
├── docs/                  # Documentation
├── docker/                # Dockerfiles
├── .github/workflows/     # CI/CD pipelines
├── tuwunel-data/          # Tuwunel database (RocksDB)
├── matrix_client_data/    # Agent mappings & config
└── docker-compose.yml     # Service definitions
```

## Git Workflow

```bash
# Check status
git status

# Current branch
git branch

# Recent commits
git log --oneline -10

# View recent work
gh run list --limit 5

# Pull latest
git pull origin main

# Push changes (triggers CI/CD)
git push origin main
```

## Production URLs

- **Matrix Server**: https://matrix.oculair.ca
- **Element Web**: https://chat.oculair.ca
- **Letta API**: https://letta.oculair.ca
- **Matrix API**: http://localhost:8004 (internal)

## Contact & Support

- **Repository**: https://github.com/oculairmedia/Letta-Matrix
- **Issues**: Report via GitHub Issues
- **Documentation**: See `docs/` directory

---

## Quick Command Reference

```bash
# View all services
docker-compose ps

# Restart matrix-client
docker-compose restart matrix-client

# View matrix-client logs
docker logs -f matrix-synapse-deployment-matrix-client-1

# View Tuwunel logs (homeserver)
docker logs -f matrix-synapse-deployment-tuwunel-1

# Run tests
python3 -m pytest tests/unit/test_agent_user_manager_space.py -v

# Check CI/CD status
gh run list --limit 5

# View current space
cat matrix_client_data/letta_space_config.json

# View agent mappings
cat matrix_client_data/agent_user_mappings.json | python3 -m json.tool

# Pull and deploy latest
docker pull ghcr.io/oculairmedia/letta-matrix-client:latest
docker-compose restart matrix-client
```

---

**Last Updated**: 2025-11-18  
**Current Focus**: Space validation and loop prevention (✅ Complete and deployed)
