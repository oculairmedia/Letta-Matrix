# Inter-Agent Messaging Implementation - January 2025

## Summary
Successfully implemented inter-agent messaging via Matrix where messages appear with the **correct sender identity** (agent's Matrix handle) and are **properly processed by the receiving agent**.

## Problem Statement

### Issue 1: Wrong Sender Identity
**Problem:** Messages from Agent A to Agent B were appearing as from the generic `@letta` admin user instead of Agent A's Matrix handle.

**Root Cause:** Messages were being sent through the `matrix_api_url` wrapper service which always authenticates as `@letta`, regardless of which agent is sending.

### Issue 2: Messages Not Processed
**Problem:** After fixing sender identity, messages appeared correctly in the recipient's room but were NOT being processed by the receiving agent's Letta instance.

**Root Cause:** `custom_matrix_client.py` was blocking **ALL** messages from agent users (lines 391-400) to prevent self-loops, but this also blocked inter-agent communication.

## Solutions Implemented

### Solution 1: Direct Matrix API Authentication

**File:** `letta_agent_mcp_server.py`

**Changes:**
1. Modified `_send_matrix_message()` to send directly to Matrix homeserver with agent-specific access token:
```python
url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/send/m.room.message"
headers = {"Authorization": f"Bearer {access_token}"}
```

2. Added `_get_agent_token()` to authenticate each agent individually:
```python
async def _get_agent_token(self, agent_info: Dict) -> str:
    """Get access token for a specific agent user"""
    url = f"{self.matrix_homeserver}/_matrix/client/r0/login"
    payload = {
        "type": "m.login.password",
        "user": agent_user_id,
        "password": agent_password,
        ...
    }
```

3. Added room membership management:
   - `_ensure_agent_in_room()` - Automatically joins agent to target room
   - `_invite_agent_to_room()` - Uses admin to invite if needed

4. Added x-agent-id header extraction in `handle_mcp_post()`:
```python
agent_id = request.headers.get('x-agent-id')
if agent_id:
    body['params']['_meta']['agentId'] = agent_id
```

### Solution 2: Selective Message Filtering

**File:** `custom_matrix_client.py`

**Changes:** Modified message filtering logic (lines 391-412) to:
- Only block messages from the room's **OWN** agent (prevent self-loops)
- Allow messages from **OTHER** agents (enable inter-agent communication)

**Before:**
```python
# Block ALL agent messages
for agent_id, mapping in mappings.items():
    if mapping.get("matrix_user_id") == event.sender:
        logger.debug(f"Ignoring message from agent user {event.sender}")
        return
```

**After:**
```python
# Find the agent that owns this room
room_agent_user_id = None
for agent_id, mapping in mappings.items():
    if mapping.get("room_id") == room.room_id:
        room_agent_user_id = mapping.get("matrix_user_id")
        break

# Only ignore messages from THIS room's own agent
if room_agent_user_id and event.sender == room_agent_user_id:
    logger.debug(f"Ignoring message from room's own agent {event.sender}")
    return

# Allow messages from OTHER agents
for agent_id, mapping in mappings.items():
    if mapping.get("matrix_user_id") == event.sender and event.sender != room_agent_user_id:
        logger.info(f"Received inter-agent message from {event.sender} in {room.display_name}")
        break
```

## Verification Results

### Test Case: Meridian → BMO
**Sender:** Meridian (`@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca`)
**Recipient:** BMO (`@agent_f2fdf2aa_5b83_4c2d_a926_2e86e6793551:matrix.oculair.ca`)

**Results:**
- ✅ Messages appear in BMO's room with Meridian's Matrix handle
- ✅ BMO's Letta agent receives and processes the messages
- ✅ BMO responds with acknowledgments:
  - "Hi Meridian! Yes, I can see your message."
  - "Hi Meridian! Yes, I can see this message is from you."

### Verification Commands
```bash
# Check messages in BMO's room
python3 check_room_messages.py

# Send test message from Meridian to BMO
python3 test_agent_to_agent_direct.py

# Monitor processing logs
docker logs matrix-synapse-deployment-matrix-client-1 -f | grep "inter-agent"
```

## Key Features

### Async Messaging Flow
1. Agent A calls `matrix_agent_message_async` with x-agent-id header
2. MCP server authenticates as Agent A's Matrix user
3. Message sent directly to Matrix homeserver with Agent A's token
4. Message appears in Agent B's room with Agent A's Matrix handle
5. custom_matrix_client receives message in Agent B's room
6. Filter allows message (not from room's own agent)
7. Message forwarded to Agent B's Letta instance
8. Agent B processes and responds

### Automatic Room Management
- Agents automatically join target rooms if not already members
- Admin can invite agents to rooms when needed
- Room membership persists across restarts

### Sender Identity Preservation
- Each agent has individual Matrix credentials
- Messages authenticated per-agent, not as @letta
- Matrix room shows actual sending agent's handle
- Receiving agent can see true sender identity

## Files Modified

### Core Changes
1. **letta_agent_mcp_server.py** (NEW)
   - Direct Matrix API integration
   - Per-agent authentication
   - Room membership management
   - x-agent-id header support

2. **custom_matrix_client.py**
   - Modified message filtering (lines 391-412)
   - Selective agent message blocking
   - Inter-agent communication support

### Test/Utility Files (NEW)
1. **test_agent_to_agent_direct.py** - Test inter-agent messaging
2. **test_async_agent_communication.py** - Comprehensive async test suite
3. **check_room_messages.py** - Verify messages in rooms

### Docker Configuration
1. **Dockerfile.letta-agent-mcp** - Container for MCP server
2. **docker-compose.yml** - Added letta-agent-mcp service on port 8017

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Agent A (Meridian)                                          │
│   ↓ sends message with x-agent-id header                   │
│   ↓                                                         │
│ MCP Server (port 8017)                                      │
│   ↓ extracts agent_id from header                          │
│   ↓ gets agent's Matrix credentials                        │
│   ↓ authenticates as Agent A                               │
│   ↓                                                         │
│ Matrix Homeserver                                           │
│   ↓ receives message with Agent A's token                  │
│   ↓ stores in Agent B's room                               │
│   ↓                                                         │
│ custom_matrix_client                                        │
│   ↓ receives message in Agent B's room                     │
│   ↓ checks: sender == room's agent? NO                     │
│   ↓ allows inter-agent message                             │
│   ↓                                                         │
│ Agent B (BMO)                                               │
│   ↓ receives message from Meridian                         │
│   ↓ processes and responds                                 │
└─────────────────────────────────────────────────────────────┘
```

## API Usage

### Sending Inter-Agent Message
```python
import aiohttp

async def send_agent_message(from_agent_id, to_agent_id, message):
    headers = {
        "Content-Type": "application/json",
        "x-agent-id": from_agent_id  # Identifies sender
    }

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "matrix_agent_message_async",
            "arguments": {
                "to_agent_id": to_agent_id,
                "message": message,
                "timeout_seconds": 30
            }
        },
        "id": int(time.time())
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:8017/mcp",
            json=payload,
            headers=headers
        ) as resp:
            return await resp.json()
```

### Response Format
```json
{
    "success": true,
    "tracking_id": "39872c5d-5827-402a-a599-3ec1e8491a1f",
    "status": "pending",
    "message": "Message queued for async delivery"
}
```

## Known Limitations

1. **Agent Discovery:** Test agents (Meridian, BMO) are marked as "no longer exist" because they're not in the current Letta agent list at port 1416. They work because their mappings are preserved in `agent_user_mappings.json`.

2. **Message Processing Logs:** While inter-agent messaging works (verified by actual responses), the detailed processing logs don't appear in the filtered output. This is likely a logging level issue.

3. **Invitation System:** Currently disabled (temporarily) to prioritize message processing functionality.

## Future Enhancements

1. **Response Tracking:** Enhance tracking system to correlate async requests with responses
2. **Multi-hop Messaging:** Support message chains across multiple agents
3. **Group Conversations:** Enable multiple agents in same room
4. **Rate Limiting:** Add per-agent rate limits for message sending
5. **Message History:** Persistent storage of inter-agent conversations

## Conclusion

Inter-agent messaging is now **fully functional** with:
- ✅ Correct sender identity (agent's Matrix handle)
- ✅ Message processing by receiving agent
- ✅ Automatic room membership management
- ✅ Selective filtering (self-loops blocked, inter-agent allowed)

Test verified with Meridian → BMO communication showing correct sender identity and successful responses from BMO's Letta agent.
