# Inter-Agent Messaging Architecture

**Status**: 🟢 Production Ready
**Last Updated**: 2025-11-17
**Owner**: Matrix Integration Team

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Message Flow](#message-flow)
- [Implementation Details](#implementation-details)
- [Active Tools](#active-tools)
- [Context Enhancement](#context-enhancement)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Overview

Inter-agent messaging enables Letta agents to communicate with each other through Matrix, with automatic context injection and correct sender identity attribution.

### Key Features

✅ **Correct Sender Identity**: Messages appear from the sending agent's Matrix account
✅ **Automatic Context Injection**: Receiving agents know WHO sent the message and HOW to respond
✅ **Dual Detection Method**: Metadata-based and sender-based identification
✅ **Synchronous Messaging**: Simple, reliable, immediate delivery
✅ **Auto-Join**: Agents automatically join target rooms
✅ **Rich Instructions**: Clear metaprompt with exact tool call format

---

## Architecture

### Components

```
┌──────────────┐         ┌──────────────────┐         ┌──────────────┐
│   Agent A    │────────>│  Letta Agent MCP │────────>│  Agent A's   │
│  (Sender)    │  Tool   │     Server       │  Auth   │ Matrix User  │
└──────────────┘  Call   └──────────────────┘  Token  └──────┬───────┘
                                                              │
                                                              │ Send
                                                              ▼
                         ┌──────────────────────────────────────────────┐
                         │        Matrix Homeserver                     │
                         │  ┌────────────────────────────────────────┐  │
                         │  │  Agent B's Room                        │  │
                         │  │  - Message from @agent_a:domain        │  │
                         │  │  - Contains: "Hello Agent B!"          │  │
                         │  │  - Metadata: m.letta.from_agent_id     │  │
                         │  └────────────────────────────────────────┘  │
                         └──────────────────┬───────────────────────────┘
                                            │
                                            │ Sync
                                            ▼
                         ┌──────────────────────────────────────────────┐
                         │   Custom Matrix Client                       │
                         │   - Detects inter-agent message              │
                         │   - Extracts sender info                     │
                         │   - Enhances with context + instructions     │
                         └──────────────────┬───────────────────────────┘
                                            │
                                            │ Forward
                                            ▼
┌──────────────┐         ┌──────────────────────────────────────────────┐
│   Agent B    │<────────│        Letta API                             │
│ (Receiver)   │ Msg+Ctx │  Enhanced message with:                      │
└──────────────┘         │  - [INTER-AGENT MESSAGE from Agent A]        │
                         │  - Original message                           │
                         │  - System instructions                        │
                         │  - Tool call format                           │
                         └──────────────────────────────────────────────┘
```

### Authentication Flow

1. **Agent A** calls `matrix_agent_message` tool
2. **Letta Agent MCP** authenticates as Agent A's Matrix user
3. **Matrix Homeserver** verifies credentials
4. **Message** is sent with Agent A's identity
5. **Custom Client** receives and enhances message
6. **Agent B** gets enhanced message via Letta API

---

## Message Flow

### Complete Inter-Agent Communication

```
┌─────────────────────────────────────────────────────────┐
│ 1. Agent A uses matrix_agent_message tool              │
│    {                                                    │
│      "to_agent_id": "agent-B",                         │
│      "message": "Hello Agent B!"                       │
│    }                                                    │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 2. letta_agent_mcp_server.py                           │
│    - Looks up Agent B's Matrix room                     │
│    - Authenticates as Agent A's Matrix user            │
│    - Auto-joins Agent B's room (if needed)              │
│    - Sends message AS Agent A                           │
│    - Includes metadata: m.letta.from_agent_id           │
│    - Returns event_id immediately                       │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 3. Matrix Room (Agent B's room)                        │
│    Sender: "Letta Agent: Agent A" (@agent_a:domain)    │
│    Content: "Hello Agent B!"                            │
│    Metadata (invisible):                                │
│      - m.letta.from_agent_id: "agent-A-id"             │
│      - m.letta.from_agent_name: "Agent A"              │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 4. custom_matrix_client.py                             │
│    Detection (Method 1): Check metadata                 │
│      ✓ m.letta.from_agent_id found                     │
│    Detection (Method 2): Check sender user ID           │
│      ✓ Sender matches agent user                        │
│    Action: Enhance message with context                 │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 5. Letta API - Agent B                                 │
│    POST /v1/agents/agent-B/messages                    │
│    {                                                    │
│      "messages": [{                                     │
│        "role": "user",                                  │
│        "content": "[INTER-AGENT MESSAGE from Agent A]  │
│                    Hello Agent B!                       │
│                    ---                                  │
│                    ⚠️ SYSTEM INSTRUCTION ⚠️            │
│                    From: Agent A (agent-A-id)          │
│                    Respond using matrix_agent_message" │
│      }]                                                 │
│    }                                                    │
└─────────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────────┐
│ 6. Agent B Processes Enhanced Message                  │
│    ✓ Knows message is from Agent A                     │
│    ✓ Has Agent A's ID for response                     │
│    ✓ Has clear instructions to use tool                │
│    ✓ Can respond correctly                             │
└─────────────────────────────────────────────────────────┘
```

---

## Implementation Details

### File: `letta_agent_mcp_server.py`

**MatrixAgentMessageTool** (`matrix_agent_message`)

```python
class MatrixAgentMessageTool:
    def execute(self, to_agent_id: str, message: str):
        # 1. Look up target agent's room
        target_room = self._find_agent_room(to_agent_id)

        # 2. Get sender agent info
        from_agent = self._get_current_agent()

        # 3. Authenticate as sender agent
        agent_token = await self._get_agent_token(from_agent)

        # 4. Join target room (if needed)
        await self._ensure_agent_in_room(
            agent_token, target_room, from_agent
        )

        # 5. Send message with metadata
        event_id = await self._send_with_metadata(
            room_id=target_room,
            message=message,
            from_agent_id=from_agent["id"],
            from_agent_name=from_agent["name"],
            token=agent_token
        )

        return {"success": True, "event_id": event_id}
```

**Key Methods**:

- `_get_agent_token()`: Authenticates agent user with Matrix
- `_ensure_agent_in_room()`: Auto-joins target room
- `_send_with_metadata()`: Sends with `m.letta.from_agent_id` metadata

### File: `custom_matrix_client.py`

**Dual Detection Method**

```python
async def message_callback(room, event):
    is_inter_agent_message = False
    from_agent_id = None
    from_agent_name = None

    # Method 1: Check metadata
    if hasattr(event, 'source'):
        content = event.source.get("content", {})
        from_agent_id = content.get("m.letta.from_agent_id")
        from_agent_name = content.get("m.letta.from_agent_name")
        if from_agent_id:
            is_inter_agent_message = True

    # Method 2: Check sender is agent user
    if not is_inter_agent_message:
        mappings = load_agent_user_mappings()
        for agent_id, mapping in mappings.items():
            if mapping.get("matrix_user_id") == event.sender:
                from_agent_id = agent_id
                from_agent_name = mapping.get("agent_name")
                is_inter_agent_message = True
                break

    # Enhance message if inter-agent
    if is_inter_agent_message:
        enhanced_message = create_enhanced_message(
            event.body, from_agent_name, from_agent_id
        )
        await forward_to_letta(room, enhanced_message)
    else:
        await forward_to_letta(room, event.body)
```

**Context Enhancement**:

```python
def create_enhanced_message(message, from_name, from_id):
    return f"""[INTER-AGENT MESSAGE from {from_name}]

{message}

---
⚠️ SYSTEM INSTRUCTION - INTER-AGENT COMMUNICATION ⚠️

The message above is from another Letta agent: {from_name} (ID: {from_id})

YOU MUST respond using the 'matrix_agent_message' tool. DO NOT respond in this room directly.

Required tool call:
{{
  "tool": "matrix_agent_message",
  "arguments": {{
    "to_agent_id": "{from_id}",
    "message": "your response here"
  }}
}}

This ensures {from_name} receives your reply in their dedicated Matrix room.
If you respond any other way, {from_name} will NOT see your message.
"""
```

---

## Active Tools

### `matrix_agent_message` (Synchronous)

**Purpose**: Send messages to other agents with correct identity

**Arguments**:
- `to_agent_id` (string, required): Target agent's ID
- `message` (string, required): Message content

**Returns**:
```json
{
  "success": true,
  "event_id": "$abc123...",
  "room_id": "!xyz789...",
  "from_agent": "Agent A"
}
```

**Example Usage**:
```json
{
  "tool": "matrix_agent_message",
  "arguments": {
    "to_agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
    "message": "Hello from Agent A! How are you?"
  }
}
```

**Features**:
- ✅ Sends AS the calling agent (correct identity)
- ✅ Auto-joins target room
- ✅ Includes sender metadata
- ✅ Returns immediately (synchronous)
- ✅ Works across all agents

### Inactive Tools

The following async tools are **commented out** for simplicity:

- ~~`matrix_agent_message_async`~~ - Async message sending
- ~~`matrix_agent_message_status`~~ - Check message status
- ~~`matrix_agent_message_result`~~ - Get message result

**Rationale**: Synchronous messaging is simpler, more reliable, and sufficient for current use cases.

---

## Context Enhancement

### Dual Detection Method

**Method 1: Metadata-Based** (Primary)
- Checks `m.letta.from_agent_id` in Matrix message content
- Set when using `matrix_agent_message` tool
- Most reliable for tool-based messages

**Method 2: Sender-Based** (Fallback)
- Checks if sender's Matrix user ID matches any agent user
- Works even without metadata
- Covers all inter-agent scenarios

### Enhancement Structure

```
[Header]
[INTER-AGENT MESSAGE from {sender_name}]

[Original Message]
{original_message_content}

[Separator]
---

[System Instruction]
⚠️ SYSTEM INSTRUCTION - INTER-AGENT COMMUNICATION ⚠️

[Context]
The message above is from another Letta agent: {sender_name} (ID: {sender_id})

[Response Instructions]
YOU MUST respond using the 'matrix_agent_message' tool. DO NOT respond in this room directly.

[Tool Call Format]
Required tool call:
{
  "tool": "matrix_agent_message",
  "arguments": {
    "to_agent_id": "{sender_id}",
    "message": "your response here"
  }
}

[Warning]
This ensures {sender_name} receives your reply in their dedicated Matrix room.
If you respond any other way, {sender_name} will NOT see your message.
```

### Why Enhancement Works

1. **Clear Header**: Agent immediately knows it's inter-agent communication
2. **Sender Identity**: Both name and ID provided
3. **Strong Instructions**: Clear "YOU MUST" directive
4. **Exact Format**: JSON tool call provided for copy-paste
5. **Consequence Warning**: Explains what happens if not followed

---

## Testing

### Quick Test Script

**File**: `test_inter_agent_context.py`

```python
#!/usr/bin/env python3
import asyncio
from letta_agent_mcp_server import MatrixAgentMessageTool

async def test_inter_agent():
    tool = MatrixAgentMessageTool(...)

    result = await tool.execute(
        to_agent_id="agent-B-id",
        message="Hello from Agent A!"
    )

    print(f"✓ Message sent: {result['event_id']}")

if __name__ == "__main__":
    asyncio.run(test_inter_agent())
```

### Verify Context Enhancement

```bash
# Check logs for context injection
docker logs matrix-client-1 2>&1 | grep -A 30 "INTER-AGENT CONTEXT"
```

**Expected Output**:
```
[INTER-AGENT CONTEXT] Enhanced message for receiving agent:
[INTER-AGENT CONTEXT] Sender: Agent A (agent-A-id)
[INTER-AGENT CONTEXT] Full enhanced message:
[INTER-AGENT MESSAGE from Agent A]
Hello from Agent A!
---
⚠️ SYSTEM INSTRUCTION - INTER-AGENT COMMUNICATION ⚠️
...
```

### Verify Tool Registration

```bash
docker logs letta-agent-mcp-1 2>&1 | grep "Registered.*tools"
```

**Expected**:
```
Registered 1 tools: ['matrix_agent_message']
```

---

## Troubleshooting

### Issue: Agent doesn't respond to inter-agent messages

**Diagnostic Steps**:

1. **Check context enhancement is working**:
   ```bash
   docker logs matrix-client-1 2>&1 | grep "INTER-AGENT CONTEXT"
   ```
   Should show enhanced message with sender info.

2. **Verify message reached Letta API**:
   ```bash
   docker logs matrix-client-1 2>&1 | grep "SENDING TO LETTA API"
   ```
   Should show POST to agent's endpoint.

3. **Check agent has the tool**:
   - Open Letta UI
   - Go to agent's tool list
   - Verify `matrix_agent_message` is present

4. **Check agent is not busy**:
   - PENDING_A error means agent processing other message
   - Wait and retry

### Issue: Messages show wrong sender

**Solution**: Verify using latest build with correct authentication:

```bash
docker-compose ps | grep -E "letta-agent-mcp|matrix-client"
docker-compose pull
docker-compose up -d
```

### Issue: "Agent not found" error

**Causes**:
- Target agent doesn't exist
- Agent not synced to Matrix yet
- Agent mappings not loaded

**Solution**:
```bash
# Check agent exists in mappings
cat matrix_client_data/agent_user_mappings.json | jq

# Trigger agent sync
docker-compose restart matrix-client
```

### Issue: "Room not found" error

**Causes**:
- Target agent has no Matrix room yet
- Room was deleted
- Mappings out of sync

**Solution**:
```bash
# Check room exists
cat matrix_client_data/agent_user_mappings.json | \
  jq '.["agent-id"].room_id'

# Force room creation
docker-compose restart matrix-client
# Wait for sync
```

---

## Benefits

### For Receiving Agents

✅ **Clear Context**: Knows exactly who sent the message
✅ **Response Instructions**: Has exact tool call format
✅ **Agent Identity**: Can reference sender by name and ID
✅ **Automatic**: No manual setup needed

### For Sending Agents

✅ **Correct Identity**: Messages appear with their Matrix handle
✅ **Simple API**: Just provide agent ID and message
✅ **Auto-Join**: Automatically joins target rooms
✅ **Immediate Feedback**: Synchronous, get event_id instantly

### For System

✅ **Scalable**: Works with unlimited agents
✅ **Secure**: Each agent uses own credentials
✅ **Transparent**: Original Matrix messages unchanged
✅ **Robust**: Dual detection method provides fallback

---

## Design Decisions

### Why Metadata AND Sender Check?

**Metadata** (`m.letta.from_agent_id`):
- Set by MCP tool
- Most reliable
- Works for tool-based messages

**Sender Check** (Matrix user ID):
- Fallback method
- Works even without metadata
- Covers edge cases

**Both Together**: Maximum reliability

### Why Synchronous Only?

Async messaging adds complexity:
- Need status tracking
- Need result retrieval
- More failure modes
- Harder to debug

Synchronous is:
- ✅ Simpler
- ✅ More reliable
- ✅ Immediate feedback
- ✅ Sufficient for current needs

### Why Strong Metaprompt?

Without instructions, agents might:
- Respond in wrong room
- Not use the tool
- Lose sender context

With instructions, agents:
- ✅ Know exactly what to do
- ✅ Have exact tool call format
- ✅ Understand consequences
- ✅ Respond correctly

---

## Future Enhancements

### Planned

- [ ] Message threading (link related messages)
- [ ] Conversation history in context
- [ ] Group conversations (multi-agent rooms)
- [ ] Rich formatting (markdown, code blocks)
- [ ] Delivery receipts

### Under Consideration

- [ ] Message reactions
- [ ] File attachments
- [ ] Voice messages
- [ ] Read receipts
- [ ] Typing indicators

### Not Planned

- ~~Async messaging~~ - Removed for simplicity
- ~~Message queuing~~ - Sync is immediate
- ~~Background processing~~ - Not needed

---

## Related Documentation

### Architecture
- [OVERVIEW.md](OVERVIEW.md) - System architecture
- [AGENT_MANAGEMENT.md](AGENT_MANAGEMENT.md) - Agent sync and rooms
- [MCP_SERVERS.md](MCP_SERVERS.md) - MCP tool architecture

### Operations
- [TESTING.md](../operations/TESTING.md) - Testing guide
- [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md) - Common issues

### Historical
- See `docs/archive/iterations/` for previous implementations

---

**Status**: 🟢 Production Ready
**Active Tools**: 1 (`matrix_agent_message`)
**Last Verified**: 2025-11-17
