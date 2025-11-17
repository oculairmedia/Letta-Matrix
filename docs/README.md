# Letta-Matrix Integration

**Status**: 🟢 Production Ready
**Last Updated**: 2025-11-17

A comprehensive Matrix deployment with Letta AI bot integration and MCP (Model Context Protocol) server support. Each Letta agent gets its own Matrix identity, dedicated room, and can communicate with other agents through Matrix.

---

## Quick Start

### 1. Prerequisites
- Docker and Docker Compose installed on your system

### 2. Deploy
```bash
docker-compose up -d
```

### 3. Access
- **Element Web Client**: http://localhost:8008
- **Matrix Server**: http://localhost:8008/_matrix/
- **Matrix API**: http://localhost:8004
- **MCP Server**: http://localhost:8016

### 4. Verify
```bash
# Check all services are running
docker-compose ps

# Check agent sync is working
docker-compose logs matrix-client | grep "AGENT_SYNC"
```

---

## What's Included

### Core Services
- **Tuwunel/Synapse**: Matrix homeserver (Rust or Python)
- **Element Web**: Modern Matrix web client
- **Nginx**: Reverse proxy for routing
- **PostgreSQL**: Database backend (Synapse only)

### Letta Integration
- **Agent Manager**: Discovers agents, creates Matrix users and rooms
- **Matrix Client**: Event monitoring and message forwarding
- **Matrix API**: RESTful API for Matrix operations
- **MCP Servers**: Tool servers for AI agent integration

### Key Features
✅ **Auto-Discovery**: Agents automatically synced every 0.5s
✅ **Dedicated Identities**: Each agent gets own Matrix user and room
✅ **Matrix Spaces**: All agent rooms organized in "Letta Agents" space
✅ **Inter-Agent Messaging**: Agents can message each other with correct sender identity
✅ **History Import**: Conversation history imported from Letta to Matrix
✅ **MCP Integration**: 8 tools for Matrix and inter-agent operations

---

## Latest Updates (2025-11-17)

### Documentation Consolidation
- **50 → 14 documents**: Consolidated overlapping documentation
- **Clear Organization**: Architecture, Operations, Process sections
- **30+ files archived**: Historical docs preserved but organized
- **Single source of truth**: Each topic has one authoritative document

### Recent Features
- **Stable Agent Usernames** (2025-01-04): Usernames based on agent IDs, stable across renames
- **Inter-Agent Messaging** (2025-11-14): Full context injection, dual detection
- **Session Management Fix** (2025-01-04): Resolved invitation handling issues
- **Tuwunel Migration** (2025-01): High-performance Rust homeserver option

---

## Documentation

### 📐 Architecture

Understand the system design and components:

- **[OVERVIEW](architecture/OVERVIEW.md)** - System architecture, components, data flow
- **[MATRIX_INTEGRATION](architecture/MATRIX_INTEGRATION.md)** - Matrix homeserver, API layer, authentication
- **[AGENT_MANAGEMENT](architecture/AGENT_MANAGEMENT.md)** - Agent discovery, user creation, room management
- **[MCP_SERVERS](architecture/MCP_SERVERS.md)** - MCP server architecture and available tools
- **[INTER_AGENT_MESSAGING](architecture/INTER_AGENT_MESSAGING.md)** - How agents communicate
- **[TUWUNEL_MIGRATION](architecture/TUWUNEL_MIGRATION.md)** - Tuwunel vs Synapse comparison

### ⚙️ Operations

Deploy, test, and troubleshoot the system:

- **[DEPLOYMENT](operations/DEPLOYMENT.md)** - Docker deployment, configuration, service management
- **[CI_CD](operations/CI_CD.md)** - GitHub Actions, releases, pre-built images
- **[TESTING](operations/TESTING.md)** - Test suite, running tests, writing tests, coverage
- **[TROUBLESHOOTING](operations/TROUBLESHOOTING.md)** - Common issues, debug techniques, recovery

### 🔄 Process

Contribute and develop:

- **[CONTRIBUTING](process/CONTRIBUTING.md)** - How to contribute, PR guidelines, code review
- **[DEVELOPMENT](process/DEVELOPMENT.md)** - Local setup, debugging, development workflow
- **[CHANGELOG](process/CHANGELOG.md)** - Version history, sprint summaries, migrations
- **[BEST_PRACTICES](process/BEST_PRACTICES.md)** - Code organization, testing, security, patterns

### 📦 Archive

Historical documentation preserved for reference:

- `archive/sprints/` - Sprint completion summaries
- `archive/sessions/` - Session summaries and status updates
- `archive/test-history/` - Historical test analyses
- `archive/iterations/` - Previous implementations and fixes

---

## Configuration

The deployment is pre-configured with sensible defaults in `.env`. Key settings:

```bash
# Server
SYNAPSE_SERVER_NAME=matrix.oculair.ca  # Change to your domain

# Database (Synapse only)
POSTGRES_DB=synapse
POSTGRES_USER=synapse
POSTGRES_PASSWORD=change_me_in_production

# Admin User
MATRIX_ADMIN_USER=letta
MATRIX_ADMIN_PASSWORD=secure_password

# Letta Integration
LETTA_SERVER_URL=http://localhost:8283
SYNC_INTERVAL_SECONDS=0.5
```

See [DEPLOYMENT](operations/DEPLOYMENT.md) for full configuration guide.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Letta Backend                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Agent A  │  │ Agent B  │  │ Agent C  │  │  Agent N │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
└───────┼─────────────┼─────────────┼─────────────┼──────────────┘
        │             │             │             │
        └─────────────┴─────────────┴─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │    Agent User Manager     │
        │    - Space Manager        │
        │    - User Manager         │
        │    - Room Manager         │
        └─────────────┬─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │      Matrix API Layer     │
        │       (FastAPI)           │
        └─────────────┬─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │   Matrix Homeserver       │
        │   (Tuwunel/Synapse)       │
        └─────────────┬─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │   Matrix Clients          │
        │   - Element Web           │
        │   - Mobile Apps           │
        └───────────────────────────┘
```

See [architecture/OVERVIEW.md](architecture/OVERVIEW.md) for detailed architecture documentation.

---

## Common Tasks

### View Agent Sync Logs
```bash
docker-compose logs -f matrix-client | grep "AGENT_SYNC"
```

### Check Service Health
```bash
# Matrix API
curl http://localhost:8004/health

# MCP Server
curl http://localhost:8016/health

# Matrix Homeserver
curl http://localhost:8008/_matrix/client/versions
```

### Restart Services
```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart matrix-client

# Rebuild and restart
docker-compose up -d --build matrix-client
```

### View Agent Mappings
```bash
docker-compose exec matrix-client cat /app/data/agent_user_mappings.json | jq
```

See [operations/DEPLOYMENT.md](operations/DEPLOYMENT.md) for more operational tasks.

---

## Data Persistence

All data is stored in local directories:

- `./synapse-data/` - Synapse configuration and database
- `./postgres-data/` - PostgreSQL database files (Synapse only)
- `./matrix_store/` - Matrix client session data
- `./matrix_client_data/` - Agent mappings and space config

### Backup
```bash
# Backup all data
tar -czf letta-matrix-backup-$(date +%Y%m%d).tar.gz \
  synapse-data matrix_store matrix_client_data postgres-data
```

### Clean Slate Reboot
```bash
# Stop and remove containers
docker-compose down

# Remove all data (WARNING: destructive)
rm -rf synapse-data postgres-data matrix_store matrix_client_data

# Start fresh
docker-compose up -d
```

See [operations/DEPLOYMENT.md](operations/DEPLOYMENT.md#data-persistence) for more details.

---

## Security Notes

### Development Mode (Default)
- Registration enabled without verification
- Default passwords in `.env`
- HTTP only (no SSL/TLS)
- Suitable for local testing

### Production Mode
- ✅ Change all passwords in `.env`
- ✅ Configure SSL/TLS termination
- ✅ Disable open registration
- ✅ Enable federation (if needed)
- ✅ Use strong admin credentials
- ✅ Set up firewall rules

See [operations/DEPLOYMENT.md](operations/DEPLOYMENT.md#security-considerations) for production deployment guide.

---

## Performance Expectations

### Startup Times
- **Homeserver**: 5-15 seconds
- **Agent Manager**: 2-5 seconds
- **First Agent Sync**: <10 seconds
- **Total Ready Time**: ~30 seconds

### Response Times
- **Agent Creation**: <3 seconds
- **Message Delivery**: <500ms
- **API Calls**: <200ms
- **Sync Interval**: 0.5 seconds

### Resource Usage
- **Tuwunel**: 50-100MB RAM
- **Synapse**: 200-500MB RAM
- **Agent Manager**: 50-100MB RAM
- **Total**: ~500MB RAM minimum

See [architecture/OVERVIEW.md](architecture/OVERVIEW.md#scalability-considerations) for scaling guidance.

---

## Troubleshooting

### Agents Not Responding
```bash
# Check agent sync
docker-compose logs matrix-client | grep "AGENT_SYNC"

# Check message forwarding
docker-compose logs matrix-client | grep "SENDING TO LETTA"

# Verify Letta connection
curl http://localhost:8283/v1/agents
```

### Service Won't Start
```bash
# Check logs
docker-compose logs [service-name]

# Check ports
netstat -tulpn | grep -E "8008|8004|8016"

# Rebuild
docker-compose build [service-name]
docker-compose up -d [service-name]
```

### Matrix Homeserver Issues
```bash
# Check homeserver logs
docker-compose logs synapse

# Check database connection (Synapse only)
docker-compose logs db

# Verify configuration
docker-compose exec synapse cat /data/homeserver.yaml
```

See [operations/TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) for comprehensive troubleshooting guide.

---

## Getting Help

1. **Check Documentation**: See [docs/](.) for comprehensive guides
2. **Review Logs**: `docker-compose logs [service]`
3. **Check Issues**: Search GitHub issues for similar problems
4. **Ask for Help**: Open a new issue with logs and configuration

---

## Contributing

We welcome contributions! See [process/CONTRIBUTING.md](process/CONTRIBUTING.md) for:
- How to contribute
- Pull request process
- Code review guidelines
- Testing requirements
- Coding standards

---

## License

[License information here]

---

## Related Projects

- [Letta](https://github.com/letta-ai/letta) - AI agent platform
- [Matrix](https://matrix.org/) - Decentralized communication protocol
- [Element](https://element.io/) - Matrix client
- [Tuwunel](https://github.com/girlbossceo/tuwunel) - Rust Matrix homeserver
- [MCP](https://modelcontextprotocol.io/) - Model Context Protocol

---

**Questions?** See [operations/TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) or open an issue.
