# Agent Mapping Synchronization Guide

## Overview

Agent-to-room mappings are now stored in **PostgreSQL database** (since commit `db5dcf8`). This ensures reliable routing of Matrix messages to the correct Letta agents.

## Problem & Solution

### The Problem
- Messages from all rooms were being routed to the default agent
- The PostgreSQL database was empty while JSON file had 56 mappings
- The routing code (`src/matrix/client.py`) queries the database, not the JSON file

### The Solution
We created a sync script that migrates mappings from JSON to PostgreSQL and can be run whenever mappings go out of sync.

## Quick Fix (If Routing is Broken)

```bash
# 1. Copy the sync script to the container
docker cp /opt/stacks/matrix-synapse-deployment/scripts/admin/sync_mappings_to_db.py \
    matrix-synapse-deployment-matrix-client-1:/app/

# 2. Run the sync
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py
```

## Sync Script Usage

### Check Sync Status (Dry Run)
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py --dry-run
```

### Verify Current Sync Status
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py --verify
```

### Sync Mappings to Database
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py
```

### Use Custom JSON File
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py --json-file /path/to/custom/mappings.json
```

## Architecture

### Data Flow
```
Letta Agent Creation
    ↓
agent_user_mappings.json (legacy)
    ↓
PostgreSQL Database (current)
    ↓
Matrix Message Routing (src/matrix/client.py)
    ↓
Correct Letta Agent
```

### Database Schema
```sql
CREATE TABLE agent_mappings (
    agent_id VARCHAR PRIMARY KEY,
    agent_name VARCHAR NOT NULL,
    matrix_user_id VARCHAR UNIQUE NOT NULL,
    matrix_password VARCHAR NOT NULL,
    room_id VARCHAR UNIQUE,
    room_created BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_room_id ON agent_mappings(room_id);
```

### Routing Code
The routing logic in `src/matrix/client.py:send_to_letta_api()`:

```python
# Check database for agent mapping (dynamic routing)
if room_id:
    try:
        from src.models.agent_mapping import AgentMappingDB
        db = AgentMappingDB()
        mapping = db.get_by_room_id(room_id)
        if mapping:
            agent_id_to_use = mapping.agent_id
            agent_name_found = mapping.agent_name
        else:
            # Falls back to default agent
            agent_id_to_use = config.letta_agent_id
    except Exception as e:
        logger.warning(f"Could not query agent mappings database: {e}")
        # Falls back to default agent
```

## Maintaining Sync

### When to Run Sync

1. **After creating new agents** - If agents are created via Letta API or CLI
2. **After manual JSON edits** - If you manually edit the JSON file
3. **After database reset** - If the PostgreSQL database is recreated
4. **When routing breaks** - If all messages go to the default agent

### Automated Sync (Recommended)

Add this to your agent creation workflow:

```python
from src.models.agent_mapping import AgentMappingDB

db = AgentMappingDB()
db.upsert(
    agent_id=agent_id,
    agent_name=agent_name,
    matrix_user_id=matrix_user_id,
    matrix_password=matrix_password,
    room_id=room_id,
    room_created=True
)
```

### Monitoring Sync Status

Check if mappings are in sync:
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py --verify
```

Expected output when in sync:
```
🔍 VERIFICATION REPORT
============================================================
✅ All mappings are in sync!
============================================================
```

## Troubleshooting

### All Messages Route to Default Agent

**Symptoms:**
- All Matrix rooms send messages to the same agent
- Logs show "using default agent" for all rooms

**Solution:**
```bash
# 1. Verify database is out of sync
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py --verify

# 2. Sync mappings
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py

# 3. Verify sync completed
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py --verify
```

### Database Connection Errors

**Symptoms:**
- `could not translate host name "synapse-db"`
- `psycopg2.OperationalError`

**Solution:**
Run the sync script from **inside the matrix-client container**, not from the host:
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py
```

### Mappings in JSON But Not Database

**Symptoms:**
- Verification shows missing mappings
- JSON has more entries than database

**Solution:**
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py
```

This will create all missing mappings in the database.

### Room ID Mismatches

**Symptoms:**
- Verification shows room ID differences
- Messages route to wrong agent

**Solution:**
```bash
# Sync will update room IDs from JSON to database
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 /app/sync_mappings_to_db.py
```

## Migration Path (JSON → PostgreSQL)

### Phase 1: Dual Storage (Current - Completed ✅)
- JSON file still used by some legacy code
- PostgreSQL database used for routing
- Sync script bridges the two

### Phase 2: PostgreSQL-First (Recommended Next Step)
- Update `AgentUserManager` to write directly to PostgreSQL
- Keep JSON file as backup only
- Auto-sync after agent creation

### Phase 3: PostgreSQL-Only (Future)
- Remove all JSON file dependencies
- Pure database-driven architecture
- No manual sync needed

## API Endpoints for Mappings

### Read Mappings (Legacy - Still Uses JSON)
```bash
curl http://localhost:8004/agents/mappings
```

### Read Specific Agent Room
```bash
curl http://localhost:8004/agents/{agent_id}/room
```

**Note:** These API endpoints still read from JSON. They should be updated to read from PostgreSQL in Phase 2.

## Database Operations

### Direct Database Access
```bash
# Connect to database
docker exec -it matrix-synapse-deployment-matrix-client-1 \
    python3 -c "
from src.models.agent_mapping import AgentMappingDB
db = AgentMappingDB()

# List all mappings
for m in db.get_all():
    print(f'{m.agent_name}: {m.room_id} -> {m.agent_id}')
"
```

### Query Specific Mapping
```bash
docker exec matrix-synapse-deployment-matrix-client-1 \
    python3 -c "
from src.models.agent_mapping import AgentMappingDB
db = AgentMappingDB()

# By room ID
mapping = db.get_by_room_id('!room_id_here:matrix.oculair.ca')
if mapping:
    print(f'Room routes to: {mapping.agent_name} ({mapping.agent_id})')
else:
    print('Room not found - will use default agent')
"
```

## Best Practices

1. **Always verify after sync**: Run `--verify` after syncing
2. **Dry-run first**: Use `--dry-run` to preview changes
3. **Backup before sync**: Keep a copy of JSON file before major changes
4. **Monitor routing**: Check logs for "AGENT ROUTING" messages
5. **Automate**: Integrate sync into agent creation workflow

## Files

- **Sync Script**: `scripts/admin/sync_mappings_to_db.py`
- **Database Model**: `src/models/agent_mapping.py`
- **Routing Code**: `src/matrix/client.py` (lines 149-163)
- **JSON File**: `matrix_client_data/agent_user_mappings.json`
- **This Guide**: `docs/AGENT_MAPPING_SYNC.md`

## Related Documentation

- [Database Migration](docs/LETTA_SDK_V1_MIGRATION.md)
- [Agent Management](docs/CLAUDE.md)
- [Testing Strategy](docs/TESTING_STRATEGY.md)
