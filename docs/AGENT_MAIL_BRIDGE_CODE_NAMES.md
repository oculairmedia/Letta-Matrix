# Agent Mail Bridge - Code Name Mapping System

## Overview

The Agent Mail Bridge successfully connects Agent Mail to Matrix, but there's a critical detail: **Agent Mail uses internal code names** (like "BlueCreek", "WhiteStone") instead of the human-readable names we provide during registration.

## How It Works

### Registration Process

1. Bridge calls `register_agent` with parameters:
   - `name`: Our desired name (e.g., "BMO", "Meridian")
   - `task_description`: Display name for the agent
   - Other metadata (program, model, etc.)

2. Agent Mail creates the agent with:
   - **Internal code name**: Auto-generated (e.g., "BlueCreek")
   - **Task description**: Stores our original name (e.g., "BMO")

3. Bridge must use the **code name** (not our original name) for all subsequent operations like `fetch_inbox`

### Current Mappings (as of Dec 24, 2025)

Key agent mappings:
- **BMO** → BlueCreek
- **Meridian** → WhiteStone
- **GraphitiExplorer** → WhiteMountain

Full mapping stored in: `/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_mail_mappings.json`

## Database Structure

Agent Mail stores agents in SQLite at `/opt/stacks/mcp_agent_mail/storage.sqlite3`:

```sql
-- Get code name mapping
SELECT a.name as code_name, a.task_description as original_name
FROM agents a
JOIN projects p ON a.project_id = p.id
WHERE p.slug = 'opt-stacks-matrix-synapse-deployment';
```

## Syncing Code Names

After registration, run this script to update mappings with assigned code names:

```python
import json
import sqlite3

# Load current mapping
with open('/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_mail_mappings.json', 'r') as f:
    mappings = json.load(f)

# Connect to Agent Mail database
conn = sqlite3.connect('/opt/stacks/mcp_agent_mail/storage.sqlite3')
cursor = conn.cursor()

# Get all agents for our project
cursor.execute("""
    SELECT a.name, a.task_description 
    FROM agents a 
    JOIN projects p ON a.project_id = p.id 
    WHERE p.slug = 'opt-stacks-matrix-synapse-deployment'
""")

# Create reverse mapping: task_description -> code_name
db_mapping = {}
for code_name, task_desc in cursor.fetchall():
    db_mapping[task_desc] = code_name

# Update mappings
for agent_id, info in mappings.items():
    search_names = [info.get('matrix_name'), info.get('agent_mail_name')]
    for search_name in search_names:
        if search_name and search_name in db_mapping:
            info['agent_mail_name'] = db_mapping[search_name]
            break

# Save updated mappings
with open('/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_mail_mappings.json', 'w') as f:
    json.dump(mappings, f, indent=2)

conn.close()
```

## Bridge Configuration

The bridge now runs with:
- **Network mode**: host (to access Agent Mail as localhost)
- **Agent Mail URL**: http://localhost:8766/mcp/
- **Homeserver URL**: http://localhost:6167 (tuwunel proxy)
- **Poll interval**: 30 seconds

### Why Host Network?

Agent Mail's `.env` has:
```
HTTP_ALLOW_LOCALHOST_UNAUTHENTICATED=true
```

This allows unauthenticated access from localhost. By running the bridge with `--network host`, it appears as localhost and bypasses bearer token authentication.

Alternative: Uncomment `HTTP_BEARER_TOKEN` in Agent Mail's `.env` and pass token to bridge, but this adds complexity.

## Verification

Check bridge is working:
```bash
# See registration logs
docker logs agent-mail-bridge | grep "Successfully registered"

# Verify code names in mapping file
cat matrix_client_data/agent_mail_mappings.json | jq '.[] | {matrix: .matrix_name, mail: .agent_mail_name}' | head -20

# Check polling activity
docker logs agent-mail-bridge --tail 20
```

## Testing Message Flow

1. Insert test message in Agent Mail:
```python
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect('/opt/stacks/mcp_agent_mail/storage.sqlite3')
cursor = conn.cursor()

# Get IDs
cursor.execute("SELECT id FROM projects WHERE slug = 'opt-stacks-matrix-synapse-deployment'")
project_id = cursor.fetchone()[0]

cursor.execute("SELECT id FROM agents WHERE project_id = ? AND name = 'WhiteStone'", (project_id,))
sender_id = cursor.fetchone()[0]

cursor.execute("SELECT id FROM agents WHERE project_id = ? AND name = 'BlueCreek'", (project_id,))
recipient_id = cursor.fetchone()[0]

# Insert message
now = datetime.now(timezone.utc).isoformat()
cursor.execute("""
    INSERT INTO messages (project_id, sender_id, subject, body_md, importance, ack_required, created_ts, attachments)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", (project_id, sender_id, "Test", "Test message", "normal", 0, now, "[]"))

message_id = cursor.lastrowid

cursor.execute("""
    INSERT INTO message_recipients (message_id, agent_id, kind)
    VALUES (?, ?, ?)
""", (message_id, recipient_id, "to"))

conn.commit()
conn.close()
```

2. Wait 30 seconds for next poll cycle

3. Check Matrix room for forwarded message:
```bash
docker logs agent-mail-bridge | grep -A5 "Forwarded message"
```

## Multi-Recipient Message Fix (Dec 24, 2025)

### Problem Discovered

When sending a message to multiple recipients via Agent Mail, only the FIRST recipient received the forwarded message in Matrix. The other recipients were silently skipped.

**Example**: Message ID 9 sent to BlueBear, BlueCreek, and WhiteMountain:
- ✅ BlueCreek received it
- ❌ BlueBear kept showing "Fetched 1 messages" but never forwarded
- ❌ WhiteMountain kept showing "Fetched 1 messages" but never forwarded

### Root Cause

The bridge used a global `processed_messages` set keyed ONLY by `message_id`:

```python
# OLD CODE - BROKEN
if msg_id in self.processed_messages:
    logger.debug(f"Message {msg_id} already processed, skipping")
    continue
# ... forward message ...
self.processed_messages.add(msg_id)
```

**Why this broke multi-recipient**:
1. Bridge polls BlueCreek's inbox first → finds msg 9 → forwards it → adds `9` to `processed_messages`
2. Bridge polls BlueBear's inbox → finds msg 9 → SKIPS (already in `processed_messages`)
3. Bridge polls WhiteMountain's inbox → finds msg 9 → SKIPS (already in `processed_messages`)

### Solution

Changed deduplication key from `msg_id` to `(agent_id, msg_id)` tuple:

```python
# NEW CODE - FIXED
dedup_key = (agent_id, msg_id)
if dedup_key in self.processed_messages:
    logger.debug(f"Message {msg_id} for agent {agent_id} already processed")
    continue
# ... forward message ...
self.processed_messages.add(dedup_key)
```

Now the same message ID can be forwarded to multiple recipients, each tracked separately.

### Files Changed

- `src/bridges/agent_mail_bridge.py`:
  - Line ~91: Updated comment for `processed_messages` 
  - Lines ~414-418: Changed deduplication check to use tuple
  - Lines ~439-442: Changed marking as processed to use tuple

### Verification

Message 9 "Team Announcement: Bridge is Live!" successfully forwarded to all three recipients:
- ✅ !IHJi2xyhK7JNkUBzu6:matrix.oculair.ca (BlueCreek/BMO)
- ✅ !V0nzStajx71WSlwEyB:matrix.oculair.ca (BlueBear/Huly)
- ✅ !81HSgbYxQUhxEj297G:matrix.oculair.ca (WhiteMountain/GraphitiExplorer)

Log evidence:
```
2025-12-24 19:45:37 - Forwarded message to !IHJi2xyhK7JNkUBzu6... [200 OK]
2025-12-24 19:45:38 - Forwarded message to !V0nzStajx71WSlwEyB... [200 OK]
2025-12-24 19:45:39 - Forwarded message to !81HSgbYxQUhxEj297G... [200 OK]
```

## Known Issues

1. **Code names change between registrations**: If Agent Mail database is reset, new code names are assigned. Must re-sync.

2. **No response parsing from register_agent**: Current bridge doesn't capture the assigned code name from registration response. Must query database afterward.

3. **Manual sync required**: After registration, must manually run sync script to update mappings.

## Future Improvements

- [ ] Parse `register_agent` response to capture code name immediately
- [ ] Auto-sync code names on bridge startup
- [ ] Add health check endpoint showing registration status
- [ ] Implement bidirectional messaging (Matrix → Agent Mail)
- [x] Fix multi-recipient message forwarding (completed Dec 24, 2025)
