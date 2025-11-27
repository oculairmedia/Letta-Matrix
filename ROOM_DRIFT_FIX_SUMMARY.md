# Room Drift Self-Correction System

## Problem Solved
Database room IDs were becoming stale when Matrix rooms were recreated, causing message routing failures.

## Solution Implemented
**Automatic room drift detection and correction** runs on every agent sync cycle.

### How It Works

1. **Every sync cycle** (~1.4 seconds), for each agent:
   - Calls `discover_agent_room(matrix_user_id)`
   - Reads the JSON file (`/app/data/agent_user_mappings.json`)
   - Compares JSON room_id vs database room_id
   
2. **If drift detected**:
   - Logs warning with both room IDs
   - Updates in-memory mapping
   - Saves to database
   - Logs success message

3. **Result**: Database automatically corrects itself within seconds

### Code Location
- **File**: `src/core/agent_user_manager.py`
- **Function**: `sync_agents_to_users()` 
- **Lines**: 518-549

### Key Functions
- `discover_agent_room()` - Reads JSON file to find actual room
- Room drift detection in sync loop

### Statistics (Current System)
- **Total agents**: 56
- **Agents with rooms**: 56 (100%)
- **Sync frequency**: ~1 every 1.4 seconds
- **Checks per 10 min**: ~24,000 room validations
- **Current drift**: 0 (all fixed)

### Verification
```bash
# Check for drift
docker exec matrix-synapse-deployment-matrix-client-1 python3 /app/check_all_drift.py

# Watch drift detection in action
docker logs matrix-synapse-deployment-matrix-client-1 -f | grep "room drift"
```

### Benefits
✅ Self-healing - no manual intervention needed
✅ Works for ALL agents, not just Meridian
✅ Runs automatically on every sync
✅ Database stays synchronized with actual room assignments
✅ Prevents routing failures from stale data

### Technical Details

**Source of Truth**: JSON file (`/app/data/agent_user_mappings.json`)
- Updated when Matrix client joins rooms
- Reflects actual current room assignments

**Database**: PostgreSQL (`matrix_letta.agent_mappings`)
- Can drift out of sync
- Now auto-corrected every ~1.4 seconds

**Detection Method**: Compare JSON vs DB room_id for each agent

**Fix Applied**: 2025-11-19 00:03:19 UTC
