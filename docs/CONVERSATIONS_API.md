# Letta Conversations API Integration

Context isolation for Matrix rooms using the Letta 0.16.2 Conversations API.

## Overview

When `LETTA_CONVERSATIONS_ENABLED=true`, the bridge creates isolated conversation contexts per room+agent pair. Messages in Room A won't pollute Room B's agent memory.

**Key behaviors:**
- Each room+agent combination gets its own Letta conversation
- DMs (2 members) use per-user isolation; groups (3+) use per-room isolation
- Automatic retry with exponential backoff on 409 CONVERSATION_BUSY errors
- Graceful fallback to legacy Agents API if anything fails

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LETTA_CONVERSATIONS_ENABLED` | `false` | Enable Conversations API integration |

**Example docker-compose:**
```yaml
services:
  matrix-client:
    environment:
      LETTA_CONVERSATIONS_ENABLED: "true"
```

## How It Works

### Message Flow

```
User sends message in Room A
         |
         v
+------------------+
| Feature enabled? |--No--> Use legacy agents.messages.create()
+------------------+
         | Yes
         v
+------------------+
| Lookup mapping   |  (room_conversations table)
| room + agent     |
+------------------+
         |
    +----+----+
    |         |
  Found    Not Found
    |         |
    v         v
Verify     Create new
exists     Letta conversation
in Letta   + save mapping
    |         |
    +----+----+
         |
         v
conversations.messages.create(conversation_id, ...)
```

### Isolation Strategy

The service automatically detects room type and applies the appropriate strategy:

| Room Type | Member Count | Strategy | Key |
|-----------|--------------|----------|-----|
| DM | 2 | `per-user` | room_id + agent_id + user_mxid |
| Group | 3+ | `per-room` | room_id + agent_id |

**Why per-user for DMs?** In a DM room, there's only one human user. Per-user isolation ensures that if the same agent is in multiple DM rooms with different users, each user has a private conversation history.

### Retry Logic

When Letta returns a 409 `CONVERSATION_BUSY` error (another message is being processed), the bridge retries with exponential backoff:

```
Attempt 1: immediate
Attempt 2: wait 1s
Attempt 3: wait 2s
Attempt 4: wait 4s (max 8s cap)
```

If all retries fail, raises `ConversationBusyError` and the message is not delivered.

### Fallback Behavior

If conversation creation/lookup fails for any reason, the bridge falls back to the legacy Agents API (`agents.messages.create`). This means:
- The message is still delivered
- Context isolation is lost (agent sees all rooms)
- A warning is logged

## Database Schema

### room_conversations

Maps Matrix rooms to Letta conversation IDs per agent.

```sql
CREATE TABLE room_conversations (
    id SERIAL PRIMARY KEY,
    room_id VARCHAR(255) NOT NULL,
    agent_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) NOT NULL,
    strategy VARCHAR(50) NOT NULL DEFAULT 'per-room',
    user_mxid VARCHAR(255),  -- only for per-user strategy
    created_at TIMESTAMP NOT NULL,
    last_message_at TIMESTAMP,
    CONSTRAINT uq_room_agent_user UNIQUE (room_id, agent_id, user_mxid)
);
```

**Indexes:**
- `idx_room_conv_room_id` - Fast lookup by room
- `idx_room_conv_agent_id` - Fast lookup by agent
- `idx_room_conv_conversation_id` - Reverse lookup from Letta conversation
- `idx_room_conv_last_msg` - Cleanup queries for stale conversations

### inter_agent_conversations

Tracks conversations for agent-to-agent @mentions.

```sql
CREATE TABLE inter_agent_conversations (
    id SERIAL PRIMARY KEY,
    source_agent_id VARCHAR(255) NOT NULL,
    target_agent_id VARCHAR(255) NOT NULL,
    room_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) NOT NULL,
    user_mxid VARCHAR(255),  -- original human who triggered the mention
    created_at TIMESTAMP NOT NULL,
    last_message_at TIMESTAMP,
    CONSTRAINT uq_inter_agent_conv UNIQUE (source_agent_id, target_agent_id, room_id, user_mxid)
);
```

**Purpose:** When Agent A mentions @AgentB, this creates a separate conversation so inter-agent chatter doesn't pollute room conversations.

## Running the Migration

```bash
# From project root
psql -h localhost -U postgres -d matrix_bridge -f scripts/migration/002_create_conversation_tables.sql
```

## Troubleshooting

### "Conversation X is busy after N retry attempts"

The conversation is processing another message. This can happen when:
- Multiple users message the same agent simultaneously
- Long-running tool calls block the conversation

**Solutions:**
- Wait and retry (automatic)
- Increase retry count: modify `max_retries` in `retry.py` (default: 3)
- Check Letta server load

### Stale Conversations

If a conversation exists in the DB but was deleted from Letta:
1. The bridge detects this via `_verify_letta_conversation()`
2. Deletes the stale DB record
3. Creates a new conversation

No manual intervention needed.

### Checking Conversation State

```bash
# List all room conversations
psql -d matrix_bridge -c "SELECT room_id, agent_id, conversation_id, strategy, last_message_at FROM room_conversations ORDER BY last_message_at DESC LIMIT 20;"

# Find conversation for a specific room
psql -d matrix_bridge -c "SELECT * FROM room_conversations WHERE room_id = '!abc123:matrix.oculair.ca';"
```

### Cleanup Stale Conversations

The service provides a cleanup method for inactive conversations:

```python
from src.core.conversation_service import get_conversation_service

service = get_conversation_service()
room_deleted, inter_deleted = service.cleanup_stale_conversations(days=30)
print(f"Cleaned up {room_deleted} room + {inter_deleted} inter-agent conversations")
```

## Key Files

| File | Purpose |
|------|---------|
| `src/core/conversation_service.py` | Main service: create/get conversations |
| `src/core/retry.py` | Retry logic for CONVERSATION_BUSY errors |
| `src/models/conversation.py` | SQLAlchemy models and DB helpers |
| `src/matrix/client.py` | Integration point (calls ConversationService) |
| `src/matrix/streaming.py` | Streaming support with conversation_id |
| `scripts/migration/002_create_conversation_tables.sql` | DB migration |

## Isolated Block Labels

When creating a conversation, the service specifies which memory blocks should be conversation-specific:

```python
DEFAULT_ISOLATED_BLOCK_LABELS = ["room_context", "conversation_summary", "active_tasks"]
```

These blocks are not shared across conversations, enabling true context isolation.
