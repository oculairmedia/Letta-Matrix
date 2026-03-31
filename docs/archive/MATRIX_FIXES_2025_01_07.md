# Matrix Tuwunel Deployment Fixes - January 7, 2025

## Overview
This document details the fixes applied to resolve issues with the Matrix Tuwunel deployment where agents were not responding to messages and the system was stuck in invitation retry loops.

## Issues Identified

### 1. Agent Discovery Configuration Error
- **Problem**: The agent_user_manager was configured to use port 8283 instead of the correct port 1416
- **Impact**: System couldn't find active Letta agents, leading to stale agent data
- **Solution**: Updated endpoint from `http://192.168.50.90:8283/v1/agents/` to `http://192.168.50.90:1416/v1/models`

### 2. Stale Agent Mappings
- **Problem**: The system had mappings for 6 deleted agents that no longer existed
- **Impact**: Unnecessary processing and confusion about which agents were active
- **Solution**: Cleaned agent_user_mappings.json to only include the active Meridian agent

### 3. Blocking Invitation Loops
- **Problem**: The matrix-client was stuck in infinite retry loops trying to invite users to rooms
- **Impact**: Message processing was completely blocked - no messages were being sent to agents
- **Root Cause**: Permission errors (M_FORBIDDEN) because @matrixadmin wasn't in the rooms

### 4. Message Content Parsing Issues
- **Problem**: Agent responses showed "Letta responded but no clear message content found"
- **Impact**: Even when agents did respond, the content wasn't being extracted properly
- **Solution**: Enhanced message parsing to handle multiple response formats

## Fixes Applied

### 1. Updated Agent Discovery Endpoint
```python
# /opt/stacks/matrix-synapse-deployment/agent_user_manager.py
# Changed from:
agents_endpoint = "http://192.168.50.90:8283/v1/agents/"
# To:
agents_endpoint = "http://192.168.50.90:1416/v1/models"
```

### 2. Enhanced Response Parsing
```python
# Added support for /v1/models response format
agents_array = agents_data.get("data", []) if isinstance(agents_data, dict) else agents_data
```

### 3. Improved Message Content Extraction
Added multiple format support in custom_matrix_client.py:
- Check for `message_type == 'assistant_message'`
- Check for `role == 'assistant'`
- Fallback to any message with content
- Enhanced debug logging for response structure

### 4. Disabled Blocking Processes (Temporary)
To allow message processing to work:
```python
# Disabled periodic agent sync
# sync_task = asyncio.create_task(periodic_agent_sync(config, logger))

# Disabled initial agent sync
# agent_manager = await run_agent_sync(config)

# Disabled invitation processes
# await self.invite_admin_to_existing_rooms()
# await self.auto_accept_invitations(mapping.room_id)
```

## Current Configuration

### Active Agent
- **Name**: Meridian
- **ID**: `agent-4bea3f4e-ecf7-40d3-871d-4c52595d60a1`
- **Matrix User**: `@agent_4bea3f4e_ecf7_40d3_871d_4c52595d60a1:matrix.oculair.ca`
- **Room**: `!uVDZegkxMnvWCbwXmW:matrix.oculair.ca`

### Endpoints
- **Letta Agent Discovery**: `http://192.168.50.90:1416/v1/models`
- **Matrix Homeserver**: `http://synapse:8008`
- **Matrix API**: Port 8004
- **MCP Server**: Ports 8015 (WebSocket) and 8016 (HTTP)

## Temporary Workarounds

The following features are temporarily disabled to ensure message processing works:
1. Automatic agent synchronization
2. Room invitation management
3. Admin user auto-invitation

These should be re-enabled once the permission issues are resolved.

## Next Steps

1. **Fix Permission Issues**: Resolve the M_FORBIDDEN errors by ensuring @matrixadmin is properly added to agent rooms
2. **Re-enable Agent Sync**: Once permissions are fixed, re-enable the periodic agent synchronization
3. **Optimize Invitation Logic**: Implement better error handling and skip logic for invitation processes
4. **Add Health Checks**: Implement proper health monitoring for message processing

## Testing

To verify the fixes are working:
1. Check that only the active agent appears in agent_user_mappings.json
2. Send a message to the Meridian agent room
3. Verify the message is received and processed
4. Check that the agent's response is properly displayed

## Files Modified

1. `/opt/stacks/matrix-synapse-deployment/agent_user_manager.py`
   - Updated agent discovery endpoint
   - Enhanced response parsing for /v1/models format
   - Disabled blocking invitation processes

2. `/opt/stacks/matrix-synapse-deployment/custom_matrix_client.py`
   - Improved message content extraction
   - Added debug logging for response structure
   - Disabled periodic and initial agent sync

3. `/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_user_mappings.json`
   - Cleaned up stale agent entries
   - Kept only active Meridian agent

## Performance Impact

- **Before**: System was stuck in invitation loops, no messages processed
- **After**: Messages are processed immediately, agent responds properly
- **Trade-off**: Some administrative features temporarily disabled for stability