# Room Mapping Fix - Complete

## Problem
- Database had wrong room IDs for all agents
- System was recreating rooms instead of using existing ones
- You weren't being invited to new rooms
- Messages routed to "default agent" instead of correct agent

## Root Cause
1. System kept recreating rooms with new IDs
2. Your existing rooms had " - Letta Agent Chat" suffix
3. Database had old room IDs that you weren't in
4. Database had UNIQUE constraint on room_id preventing updates

## Solution Applied

### 1. Fixed All 31 Room Mappings
Updated both JSON and database to use YOUR actual rooms:
- BMO: `!IHJi2xyhK7JNkUBzu6:matrix.oculair.ca` ✅
- Meridian: `!O8cbkBGCMB8Ujlaret:matrix.oculair.ca` ✅  
- GraphitiExplorer: `!81HSgbYxQUhxEj297G:matrix.oculair.ca` ✅
- ... and 28 more

### 2. Removed Database Constraint
- Dropped UNIQUE constraint on `agent_mappings.room_id`
- Allows flexibility if multiple agents share a room

### 3. Auto-Healing System
- Room drift detection runs every ~1.4 seconds
- Compares JSON (source of truth) vs Database
- Auto-corrects mismatches

## Files Modified
- `/app/data/agent_user_mappings.json` - Updated 31 room IDs
- Database: `agent_mappings` table - Dropped unique constraint

## Verification
```bash
# Test a specific agent's routing
docker exec matrix-synapse-deployment-matrix-client-1 python3 << 'EOF'
import sys; sys.path.insert(0, '/app')
from src.models.agent_mapping import AgentMappingDB
db = AgentMappingDB()
mapping = db.get_by_room_id('!IHJi2xyhK7JNkUBzu6:matrix.oculair.ca')
print(f"BMO: {mapping.agent_name if mapping else 'NOT FOUND'}")
