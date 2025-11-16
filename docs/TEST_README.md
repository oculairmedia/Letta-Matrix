# Testing Guide for Matrix Space Management

## Overview
This guide covers testing the Matrix Space organization feature for Letta agents.

## Test Files

### Unit Tests
- **`test_space_management.py`**: Comprehensive unit tests for space management functionality
  - Space creation and persistence
  - Room addition to space
  - Bidirectional parent-child relationships
  - Migration of existing rooms
  - Edge cases and error handling

## Setup

### Install Test Dependencies
```bash
pip install -r test_requirements.txt
```

### Running Tests

#### Run All Tests
```bash
pytest test_space_management.py -v
```

#### Run with Coverage
```bash
pytest test_space_management.py --cov=agent_user_manager --cov-report=html -v
```

#### Run Specific Test Class
```bash
pytest test_space_management.py::TestSpaceManagement -v
```

#### Run Specific Test
```bash
pytest test_space_management.py::TestSpaceManagement::test_space_creation -v
```

## Test Coverage

### TestSpaceManagement
Core functionality tests:
- ✅ `test_space_creation` - Verifies space is created correctly
- ✅ `test_space_persistence` - Confirms space config is saved/loaded
- ✅ `test_add_room_to_space` - Tests adding rooms to space
- ✅ `test_add_room_without_space` - Handles missing space gracefully
- ✅ `test_migrate_existing_rooms` - Migrates existing rooms to space
- ✅ `test_space_reuse_on_restart` - Reuses existing space on restart
- ✅ `test_space_recreation_if_deleted` - Recreates deleted space
- ✅ `test_get_space_id` - Returns correct space ID
- ✅ `test_space_data_format` - Verifies space config format
- ✅ `test_room_to_space_bidirectional_relationship` - Tests m.space.child and m.space.parent
- ✅ `test_sync_creates_space` - Space creation during sync
- ✅ `test_new_room_added_to_space` - New rooms automatically added

### TestSpaceEdgeCases
Error handling tests:
- ✅ `test_space_creation_failure` - Handles space creation failure
- ✅ `test_add_room_to_space_failure` - Handles room addition failure
- ✅ `test_migrate_with_no_space` - Migration without space

## Manual Integration Testing

### Prerequisites
1. Running Matrix Synapse homeserver
2. Admin credentials configured
3. At least one Letta agent available

### Test Procedure

#### 1. Initial Space Creation
```bash
# Start the matrix-client service
docker-compose up -d matrix-client

# Check logs for space creation
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Creating Letta Agents space"

# Expected output:
# [AGENT_SYNC] Creating Letta Agents space
# [AGENT_SYNC] Successfully created Letta Agents space: !AbCdEfG:matrix.oculair.ca
```

#### 2. Verify Space in Element
1. Open Element web client
2. Click on the spaces icon (left sidebar)
3. Look for "Letta Agents" space
4. Join the space
5. Verify all agent rooms appear as children

#### 3. Test New Agent Addition
```bash
# Create a new agent in Letta (via UI or API)

# Wait for sync (0.5 seconds)
# Check logs
docker logs matrix-synapse-deployment-matrix-client-1 | tail -20

# Expected:
# Processing agent: NewAgent (agent-xyz)
# Created room !RoomId:matrix.oculair.ca for agent NewAgent
# Adding room !RoomId:matrix.oculair.ca to Letta Agents space
# Successfully added room to space
```

#### 4. Test Room Migration
```bash
# If you have existing agent rooms before space implementation:

# Delete the space config to force recreation
docker exec matrix-synapse-deployment-matrix-client-1 rm -f /app/data/letta_space_config.json

# Restart the client
docker-compose restart matrix-client

# Check logs for migration
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Migrating"

# Expected:
# [AGENT_SYNC] Migrating existing agent rooms to the new space
# Migrating room for agent AgentName to space
# Successfully migrated room for AgentName
# [AGENT_SYNC] Migrated N rooms to space
```

#### 5. Verify Space Hierarchy
Using Matrix API:
```bash
# Get space children
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8008/_matrix/client/r0/rooms/!SPACE_ID:matrix.oculair.ca/state/m.space.child"

# Check room's parent
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8008/_matrix/client/r0/rooms/!ROOM_ID:matrix.oculair.ca/state/m.space.parent"
```

#### 6. Test Space Persistence
```bash
# Restart the service
docker-compose restart matrix-client

# Check logs - should reuse existing space
docker logs matrix-synapse-deployment-matrix-client-1 | grep "Using existing"

# Expected:
# [AGENT_SYNC] Using existing Letta Agents space: !AbCdEfG:matrix.oculair.ca
```

## Debugging

### View Space Configuration
```bash
docker exec matrix-synapse-deployment-matrix-client-1 cat /app/data/letta_space_config.json
```

Expected output:
```json
{
  "space_id": "!AbCdEfG:matrix.oculair.ca",
  "created_at": 1704672000.123,
  "name": "Letta Agents"
}
```

### Check Agent Room Mappings
```bash
docker exec matrix-synapse-deployment-matrix-client-1 cat /app/data/agent_user_mappings.json
```

### Common Issues

#### Issue: Space not appearing in Element
**Solution**:
- Check if the main @letta user was invited and joined the space
- Verify in logs: `Successfully joined Letta Agents space`
- Manually accept invitation if needed

#### Issue: Rooms not appearing in space
**Solution**:
- Check logs for "Adding room X to Letta Agents space"
- Verify space_id is set in space config
- Check Matrix API for m.space.child state events

#### Issue: "No space ID available"
**Solution**:
- Space creation may have failed
- Check for login errors or permission issues
- Verify admin credentials in .env file

## Performance Testing

### Load Test: Multiple Agent Rooms
```bash
# Create 10 agents and verify they're all added to space
# Monitor time to create and organize

# Expected performance:
# - Space creation: <2 seconds
# - Room creation: <2 seconds per agent
# - Room-to-space addition: <1 second per room
# - Total for 10 agents: <30 seconds
```

## Continuous Integration

To integrate with CI/CD:
```yaml
# .github/workflows/test.yml
name: Run Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      - name: Install dependencies
        run: |
          pip install -r test_requirements.txt
          pip install -r requirements.txt
      - name: Run tests
        run: pytest test_space_management.py -v --cov=agent_user_manager
```

## Success Criteria

All tests pass when:
- ✅ Space is created on first startup
- ✅ Space configuration persists across restarts
- ✅ New agent rooms are automatically added to space
- ✅ Existing rooms are migrated on first run
- ✅ Bidirectional relationships (m.space.child and m.space.parent) are set
- ✅ Space appears correctly in Matrix clients
- ✅ Error conditions are handled gracefully
- ✅ Performance meets expectations (<2s for space creation)
