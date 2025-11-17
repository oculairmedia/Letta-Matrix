# Development Guide

## Overview

This guide covers local development setup, running services, development workflows, and debugging for the Letta-Matrix integration.

## Table of Contents

- [Architecture Quick Reference](#architecture-quick-reference)
- [Local Development Setup](#local-development-setup)
- [Running Services](#running-services)
- [Development Workflow](#development-workflow)
- [Debugging Tips](#debugging-tips)
- [Testing](#testing)
- [Common Tasks](#common-tasks)
- [Troubleshooting](#troubleshooting)

## Architecture Quick Reference

### System Components

```
Letta Agents ←→ Agent Manager ←→ Matrix Users ←→ Matrix Rooms ←→ Letta Agents Space
     ↓              ↓                ↓              ↓                    ↓
MCP Server ←→ Matrix API ←→ Matrix Synapse ←→ GMMessages Bridge ←→ SMS/RCS
```

### Core Services

1. **Matrix Synapse** - Matrix homeserver (port 8008)
2. **Agent User Manager** - Agent discovery and user sync
3. **Matrix Client** - Message routing and agent responses
4. **MCP HTTP Server** - Matrix tools for agents (port 8016)
5. **Matrix API** - FastAPI service (port 8004)
6. **GMMessages Bridge** - SMS/RCS integration (optional)

### File Structure

```
src/
├── core/          # Core business logic
│   ├── agent_user_manager.py      # Agent-user orchestration
│   ├── space_manager.py           # Matrix space management
│   ├── user_manager.py            # User account management
│   └── room_manager.py            # Room creation/management
├── matrix/        # Matrix client code
│   ├── client.py                  # Main Matrix client
│   ├── auth.py                    # Authentication manager
│   └── event_dedupe.py            # Event deduplication
├── letta/         # Letta API integration
├── mcp/           # MCP server implementations
├── api/           # FastAPI endpoints
└── utils/         # Shared utilities
```

See `/docs/architecture/OVERVIEW.md` for detailed architecture.

## Local Development Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.10+ (for local testing)
- Git
- Text editor or IDE

### 1. Clone Repository

```bash
git clone https://github.com/oculairmedia/Letta-Matrix.git
cd Letta-Matrix
```

### 2. Environment Configuration

Create `.env` file:

```bash
# Matrix Configuration
MATRIX_HOMESERVER_URL=http://synapse:8008
MATRIX_ADMIN_USERNAME=@matrixadmin:matrix.oculair.ca
MATRIX_ADMIN_PASSWORD=admin123
MATRIX_USERNAME=@letta:matrix.oculair.ca
MATRIX_PASSWORD=letta

# Letta Configuration
LETTA_API_URL=http://192.168.50.90:1416
LETTA_TOKEN=your_letta_token_here

# Development Mode (simple passwords)
DEV_MODE=true

# Event Deduplication
MATRIX_EVENT_DEDUPE_TTL=3600

# Optional: SMS Bridge
GMESSAGES_BRIDGE_ENABLED=false
```

### 3. Install Python Dependencies (for local testing)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install pytest pytest-asyncio pytest-cov pytest-mock
```

### 4. Initialize Matrix Synapse

First time setup:

```bash
# Start only Synapse to generate config
docker-compose up -d synapse

# Wait for initialization
docker logs -f matrix-synapse-deployment-synapse-1

# Create admin user
docker exec -it matrix-synapse-deployment-synapse-1 \
    register_new_matrix_user http://localhost:8008 -c /data/homeserver.yaml -a

# Follow prompts to create @matrixadmin user
```

## Running Services

### Start All Services

```bash
# Start everything
docker-compose up -d

# View logs
docker-compose logs -f

# View specific service logs
docker-compose logs -f matrix-client
```

### Start Individual Services

```bash
# Just Matrix Synapse
docker-compose up -d synapse

# Matrix client (agent manager + message handler)
docker-compose up -d matrix-client

# MCP HTTP server
docker-compose up -d mcp-server

# Matrix API service
docker-compose up -d matrix-api
```

### Stop Services

```bash
# Stop all
docker-compose down

# Stop and remove volumes (clean slate)
docker-compose down -v

# Stop specific service
docker-compose stop matrix-client
```

### Restart Services

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart matrix-client

# Rebuild and restart
docker-compose up -d --build matrix-client
```

## Development Workflow

### Sprint-Based Refactoring

For major changes, we use sprint-based development:

1. **Create sprint branch**:
   ```bash
   git checkout -b sprint-X-description
   ```

2. **Implement changes** with tests

3. **Run test suite**:
   ```bash
   pytest tests/unit/
   # Ensure 100% pass rate
   ```

4. **Commit with descriptive message**:
   ```bash
   git add .
   git commit -m "refactor: Sprint X - Description (Completion Summary)"
   ```

5. **Push and create PR**:
   ```bash
   git push -u origin sprint-X-description
   ```

See completed sprints in `/docs/process/CHANGELOG.md`.

### Feature Development

1. **Create feature branch**:
   ```bash
   git checkout -b feature/description
   ```

2. **Implement feature** with tests

3. **Test locally**:
   ```bash
   # Run tests
   pytest tests/

   # Test in Docker
   docker-compose up --build
   ```

4. **Commit and push**:
   ```bash
   git add .
   git commit -m "feat: Add feature description"
   git push -u origin feature/description
   ```

### Bug Fixes

1. **Create fix branch**:
   ```bash
   git checkout -b fix/issue-description
   ```

2. **Add test** that reproduces the bug

3. **Fix the bug**

4. **Verify test passes**:
   ```bash
   pytest tests/ -v
   ```

5. **Commit**:
   ```bash
   git commit -m "fix: Description of bug fix

   Fixes #123"
   ```

## Debugging Tips

### Viewing Logs

```bash
# All services
docker-compose logs -f

# Specific service with timestamps
docker-compose logs -f --timestamps matrix-client

# Last 100 lines
docker-compose logs --tail=100 matrix-client

# Follow from specific time
docker-compose logs --since 2025-01-15T10:00:00 matrix-client
```

### Debugging Agent Sync

```bash
# Watch agent discovery
docker logs -f matrix-synapse-deployment-matrix-client-1 | grep "agent sync"

# Check agent count
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Found .* Letta agents"

# Verify agent processing
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Processing agent:"
```

### Debugging Message Flow

```bash
# Watch message handling
docker logs -f matrix-synapse-deployment-matrix-client-1 | grep "message_callback"

# Check for duplicate events
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Duplicate Matrix event"

# Watch agent responses
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Successfully sent message as agent"
```

### Debugging Space Management

```bash
# Check space creation
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Letta Agents space"

# Verify room-to-space relationships
docker logs matrix-synapse-deployment-matrix-client-1 | grep "add_room_to_space"

# Check space hierarchy
docker logs matrix-synapse-deployment-matrix-client-1 | grep "m.space.child"
```

### Interactive Debugging

#### Enter Container

```bash
# Enter matrix-client container
docker exec -it matrix-synapse-deployment-matrix-client-1 /bin/bash

# Check Python environment
python --version
pip list

# Check file structure
ls -la /app/src/

# Check data files
ls -la /app/data/
cat /app/data/agent_user_mappings.json
```

#### Python Debugger

Add breakpoints in code:

```python
import pdb; pdb.set_trace()  # Classic debugger

# Or use ipdb for better experience
import ipdb; ipdb.set_trace()
```

Then run in interactive mode:

```bash
docker-compose run --rm matrix-client python -m pdb src/matrix/client.py
```

#### Live Code Reload

For development, mount source as volume:

```yaml
# docker-compose.yml
services:
  matrix-client:
    volumes:
      - ./src:/app/src:ro  # Read-only to prevent accidental changes
```

### Health Checks

```bash
# MCP server health
curl http://localhost:8016/health

# Matrix API health
curl http://localhost:8004/health

# Check agent sync status
curl http://localhost:8004/agents
```

### Database Inspection

```bash
# Event deduplication database
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    sqlite3 /app/data/matrix_event_dedupe.db "SELECT COUNT(*) FROM processed_events;"

# View recent events
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    sqlite3 /app/data/matrix_event_dedupe.db \
    "SELECT * FROM processed_events ORDER BY processed_at DESC LIMIT 10;"

# Check database size
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    ls -lh /app/data/matrix_event_dedupe.db
```

## Testing

### Running Tests

```bash
# All tests
pytest tests/

# Unit tests only
pytest tests/unit/

# Integration tests
pytest tests/integration/

# Smoke tests
pytest tests/integration/smoke/

# Specific test file
pytest tests/unit/test_agent_user_manager.py

# Specific test function
pytest tests/unit/test_agent_user_manager.py::test_generate_username

# With coverage report
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html
```

### Test Development

1. **Write test first** (TDD approach):
   ```python
   def test_new_feature():
       """Test description"""
       # Arrange
       manager = MatrixUserManager(...)

       # Act
       result = manager.new_feature(...)

       # Assert
       assert result == expected
   ```

2. **Run test** (should fail):
   ```bash
   pytest tests/unit/test_new_feature.py -v
   ```

3. **Implement feature**

4. **Run test** (should pass):
   ```bash
   pytest tests/unit/test_new_feature.py -v
   ```

### Test Categories

- **Unit Tests**: Test individual functions/methods in isolation
- **Integration Tests**: Test component interactions
- **Smoke Tests**: Test critical user workflows end-to-end

See `/docs/operations/TESTING.md` for comprehensive testing guide.

## Common Tasks

### Add New Agent

Agents are auto-discovered from Letta API:

1. Create agent in Letta (via Letta UI or API)
2. Wait up to 0.5 seconds (automatic detection)
3. Check Matrix client logs:
   ```bash
   docker logs -f matrix-synapse-deployment-matrix-client-1 | grep "Found .* Letta agents"
   ```
4. Join "Letta Agents" space in Element to see new room

### Update Agent Display Name

1. Rename agent in Letta
2. System automatically updates:
   - Matrix user display name
   - Room name
3. Changes appear in Matrix clients within 5-10 seconds

### Clean Up Agent Rooms

```bash
# Use cleanup script
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    python /app/scripts/cleanup/cleanup_agent_rooms.py

# Or delete specific room
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    python /app/scripts/cleanup/delete_all_agent_rooms.py
```

### Send Test Message

```bash
# Use admin script
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    python /app/scripts/testing/send_to_admin.py "Test message"

# Or use Matrix API
curl -X POST http://localhost:8004/send-message \
  -H "Content-Type: application/json" \
  -d '{"room_id": "!roomid:domain", "message": "Test"}'
```

### View Agent Mappings

```bash
# View mapping file
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    cat /app/data/agent_user_mappings.json | python -m json.tool

# Check space config
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    cat /app/data/letta_space_config.json | python -m json.tool
```

### Rebuild Services

```bash
# Rebuild all services
docker-compose build

# Rebuild specific service
docker-compose build matrix-client

# Rebuild without cache
docker-compose build --no-cache matrix-client

# Rebuild and restart
docker-compose up -d --build matrix-client
```

## Troubleshooting

### Agent Not Responding

1. **Check agent discovery**:
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "Found .* Letta agents"
   ```

2. **Check agent mapping**:
   ```bash
   docker exec -it matrix-synapse-deployment-matrix-client-1 \
       cat /app/data/agent_user_mappings.json | grep agent-id
   ```

3. **Check Letta API**:
   ```bash
   curl http://192.168.50.90:1416/v1/models
   ```

4. **Check message routing**:
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "Processing message"
   ```

### Duplicate Messages

1. **Check event deduplication**:
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "Duplicate Matrix event"
   ```

2. **Verify dedupe database**:
   ```bash
   docker exec -it matrix-synapse-deployment-matrix-client-1 \
       sqlite3 /app/data/matrix_event_dedupe.db "SELECT COUNT(*) FROM processed_events;"
   ```

3. **Check for multiple clients**:
   ```bash
   docker ps | grep matrix-client
   # Should only show ONE container
   ```

See `/docs/operations/TROUBLESHOOTING.md` for more solutions.

### Space Not Showing

1. **Check space creation**:
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "Letta Agents space"
   ```

2. **Verify space config**:
   ```bash
   docker exec -it matrix-synapse-deployment-matrix-client-1 \
       cat /app/data/letta_space_config.json
   ```

3. **Join space manually** in Element:
   - Use room ID from space config
   - Join via room directory or /join command

### Performance Issues

1. **Check sync interval** (default: 0.5 seconds):
   ```python
   # In src/matrix/client.py
   async def periodic_agent_sync(config, logger, interval=0.5):
   ```

2. **Monitor resource usage**:
   ```bash
   docker stats
   ```

3. **Check Matrix sync timeout** (default: 5 seconds):
   ```python
   # In src/matrix/client.py
   await client.sync_forever(timeout=5000, ...)
   ```

### Connection Issues

1. **Check Synapse is running**:
   ```bash
   curl http://localhost:8008/_matrix/client/versions
   ```

2. **Check network connectivity**:
   ```bash
   docker-compose ps
   docker network ls
   ```

3. **Check authentication**:
   ```bash
   # Test login
   curl -X POST http://localhost:8008/_matrix/client/r0/login \
     -H "Content-Type: application/json" \
     -d '{"type":"m.login.password","user":"letta","password":"letta"}'
   ```

## Performance Optimization

### Agent Sync Performance

- **Startup sync**: Immediate on container start
- **Periodic sync**: Every 0.5 seconds (configurable)
- **New agent detection**: Within 0.5 seconds
- **Name change detection**: Within 0.5 seconds

### Response Times

- **Agent message processing**: <1 second typical
- **Room creation**: <2 seconds
- **User creation**: <1 second
- **Message sending**: <1 second
- **Room/display name updates**: 5-10 seconds to appear in clients

### Network Optimizations

- HTTP connection pooling (100 concurrent connections)
- DNS caching (5 minutes)
- Keep-alive connections (30 seconds)
- Sync timeout: 5s (faster message delivery)

See `/docs/architecture/OVERVIEW.md` for detailed performance characteristics.

## Additional Resources

- **Architecture**: `/docs/architecture/OVERVIEW.md`
- **Inter-Agent Messaging**: `/docs/architecture/INTER_AGENT_MESSAGING.md`
- **Matrix Integration**: `/docs/architecture/MATRIX_INTEGRATION.md`
- **MCP Servers**: `/docs/architecture/MCP_SERVERS.md`
- **Testing Guide**: `/docs/operations/TESTING.md`
- **CI/CD**: `/docs/operations/CI_CD.md`
- **Deployment**: `/docs/operations/DEPLOYMENT.md`
- **Best Practices**: `/docs/process/BEST_PRACTICES.md`
- **Contributing**: `/docs/process/CONTRIBUTING.md`

## Getting Help

1. Check documentation in `/docs/`
2. Review troubleshooting guide
3. Search closed issues on GitHub
4. Create issue with detailed description
5. Tag maintainers if urgent

## Next Steps

- Review `/docs/architecture/OVERVIEW.md` for system design
- Set up local environment following this guide
- Run test suite to verify setup
- Try making a small change and submitting PR
- Review `/docs/process/CONTRIBUTING.md` for contribution guidelines
