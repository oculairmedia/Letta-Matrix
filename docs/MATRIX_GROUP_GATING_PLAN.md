# Matrix Group Gating Implementation Plan

## Problem Statement
The Matrix bridge (`letta-matrix-client` container) currently responds to every message it receives in agent-mapped rooms, creating noise. The user wants a "silent mode" where the bot ingests all messages for memory/awareness but only responds when explicitly mentioned or tagged.

## Architecture Understanding

**Correct Flow:**
1. Matrix messages → `matrix-tuwunel-deploy` / `letta-matrix-client` container
2. Python bridge (`src/matrix/client.py`) processes messages via `nio` library
3. Bridge sends to LettaBot via WebSocket gateway OR falls back to direct Letta API
4. Responses flow back through bridge to Matrix

**The group gating logic needs to be in the Python Matrix bridge, NOT in LettaBot's TypeScript code.**

## Solution Overview

Add group configuration and mention detection to the Matrix bridge Python code, similar to how the TypeScript adapters handle it in LettaBot, but implemented in the Python bridge.

## Implementation Strategy

### 1. Create Group Configuration Schema
**File:** `src/matrix/group_config.py` (new)

```python
from typing import Optional, Dict, List, Literal
from pydantic import BaseModel

GroupMode = Literal["open", "listen", "mention-only", "disabled"]

class GroupConfig(BaseModel):
    mode: GroupMode = "listen"  # Default: listen (process, don't respond unless mentioned)
    allowed_users: Optional[List[str]] = None  # Only process messages from these users
    mention_patterns: Optional[List[str]] = None  # Regex patterns for mention detection

GroupsConfig = Dict[str, GroupConfig]  # room_id -> config, "*" for wildcard
```

### 2. Load Group Configuration
**File:** `src/core/config.py` (or wherever Config class is defined)

Add to Config class:
```python
class Config:
    # ... existing fields ...
    matrix_groups: Dict[str, GroupConfig] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        # ... existing loading ...
        # Load from MATRIX_GROUPS_JSON env var or config file
        groups_json = os.getenv("MATRIX_GROUPS_JSON")
        if groups_json:
            import json
            groups_data = json.loads(groups_json)
            config.matrix_groups = {
                k: GroupConfig(**v) for k, v in groups_data.items()
            }
```

### 3. Create Mention Detection Module
**File:** `src/matrix/mention_detection.py` (new)

```python
import re
import logging
from typing import Optional, Tuple
from nio import RoomMessageText

logger = logging.getLogger(__name__)

def detect_matrix_mention(
    event: RoomMessageText,
    bot_user_id: str,
    custom_patterns: Optional[list[str]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Detect if the bot was mentioned in a Matrix message.

    Detection methods (in priority order):
    1. Matrix pills (m.mentions field in event content)
    2. Text @username mentions
    3. Custom regex patterns

    Returns:
        (was_mentioned, detection_method)
    """
    text = event.body or ""

    # METHOD 1: Matrix pills (native Matrix mentions)
    # MSC 3952: https://github.com/matrix-org/matrix-spec-proposals/pull/3952
    if hasattr(event, 'source') and isinstance(event.source, dict):
        content = event.source.get('content', {})
        mentions = content.get('m.mentions', {})
        user_ids = mentions.get('user_ids', [])
        if bot_user_id in user_ids:
            return (True, 'pill')

    # METHOD 2: @username text mention
    # Extract username from user_id (@user:homeserver.com)
    bot_username = bot_user_id.split(':')[0][1:]  # Remove @ and :homeserver
    username_pattern = rf'@{re.escape(bot_username)}\b'
    if re.search(username_pattern, text, re.IGNORECASE):
        return (True, 'text')

    # METHOD 3: Custom regex patterns
    if custom_patterns:
        for pattern in custom_patterns:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    return (True, 'custom_regex')
            except re.error:
                logger.warning(f"Invalid mention pattern: {pattern}")

    return (False, None)
```

### 4. Create Group Gating Module
**File:** `src/matrix/group_gating.py` (new)

```python
import logging
from typing import Optional
from .group_config import GroupMode, GroupConfig, GroupsConfig
from .mention_detection import detect_matrix_mention

logger = logging.getLogger(__name__)

def resolve_group_mode(
    groups_config: GroupsConfig,
    room_id: str
) -> GroupMode:
    """Resolve effective mode for a room."""
    if room_id in groups_config:
        return groups_config[room_id].mode
    if "*" in groups_config:
        return groups_config["*"].mode
    return "listen"  # Safe default

def is_user_allowed(
    groups_config: GroupsConfig,
    room_id: str,
    sender_id: str
) -> bool:
    """Check if user is in the allowed list for this room."""
    config = groups_config.get(room_id) or groups_config.get("*")
    if not config or not config.allowed_users:
        return True  # No allowlist = everyone allowed
    return sender_id in config.allowed_users

def apply_group_gating(
    room_id: str,
    sender_id: str,
    event,  # RoomMessageText
    bot_user_id: str,
    groups_config: GroupsConfig,
    logger: logging.Logger
) -> Optional[dict]:
    """
    Apply group gating logic.

    Returns None if message should be filtered out.
    Returns dict with {was_mentioned, mode} if message should be processed.
    """
    # Step 1: Check user allowlist
    if not is_user_allowed(groups_config, room_id, sender_id):
        logger.debug(f"User {sender_id} not in allowlist for room {room_id}")
        return None

    # Step 2: Resolve mode
    mode = resolve_group_mode(groups_config, room_id)

    if mode == "disabled":
        logger.debug(f"Group mode disabled for room {room_id}")
        return None

    # Step 3: Detect mentions
    config = groups_config.get(room_id) or groups_config.get("*")
    custom_patterns = config.mention_patterns if config else None
    was_mentioned, method = detect_matrix_mention(
        event,
        bot_user_id,
        custom_patterns
    )

    # Step 4: Apply mode logic
    if mode == "open":
        # Always process, respond if agent generates output
        return {"was_mentioned": was_mentioned, "mode": mode, "method": method}

    elif mode == "listen":
        # Always process for memory, suppress response unless mentioned
        return {"was_mentioned": was_mentioned, "mode": mode, "method": method}

    elif mode == "mention-only":
        # Only process if mentioned
        if was_mentioned:
            return {"was_mentioned": True, "mode": mode, "method": method}
        else:
            logger.debug(f"Mention required but not mentioned in {room_id}")
            return None

    return None
```

### 5. Update message_callback
**File:** `src/matrix/client.py` (modify existing)

In the `message_callback` function (around line 2780), add group gating:

```python
async def message_callback(room, event, config: Config, logger: logging.Logger, client: Optional[AsyncClient] = None):
    """Callback function for handling new text messages."""
    if isinstance(event, RoomMessageText):
        # ... existing duplicate checks ...

        # ... existing self-message checks ...

        # ... existing agent mapping checks ...

        # NEW: Apply group gating
        from src.matrix.group_gating import apply_group_gating

        gating_result = apply_group_gating(
            room_id=room.room_id,
            sender_id=event.sender,
            event=event,
            bot_user_id=client.user_id if client else None,
            groups_config=config.matrix_groups,
            logger=logger
        )

        if not gating_result:
            # Message filtered by group gating
            return

        was_mentioned = gating_result["was_mentioned"]
        group_mode = gating_result["mode"]

        logger.info(
            f"Group gating: room={room.room_id}, mode={group_mode}, "
            f"mentioned={was_mentioned}, method={gating_result.get('method')}"
        )

        # ... rest of existing message processing ...

        # When sending to Letta, include metadata about mention status
        # This allows the agent to know if it was explicitly mentioned

        # NEW: For listen mode without mention, set silent flag
        silent_mode = (group_mode == "listen" and not was_mentioned)

        if silent_mode:
            # Process for memory but tell agent not to respond
            # Add metadata to message content or use special flag
            message_with_context = f"[SILENT MODE - Process for memory, do not respond unless critical]\n\n{message_body}"
            # OR pass as metadata to send_to_letta_api_streaming
        else:
            message_with_context = message_body

        # Continue with existing Letta API call...
        # await send_to_letta_api_streaming(..., message_with_context, ...)
```

### 6. Handle Silent Mode in Letta Response
When `silent_mode=True`, the bridge should:
1. Send message to Letta for processing (agent learns from it)
2. **Suppress sending response back to Matrix room** (unless agent explicitly chooses to respond)

```python
# After getting response from Letta
if silent_mode:
    # Check if agent explicitly wants to respond
    # (e.g., by looking for special marker in response or checking response length)
    if response_is_empty_or_trivial(response):
        logger.info(f"Silent mode: suppressing response for {room.room_id}")
        return  # Don't send anything back to Matrix
    else:
        logger.info(f"Silent mode: agent chose to respond despite not being mentioned")
        # Continue with sending response

# Normal: send response to Matrix
```

## Configuration Example

### Environment Variable
```bash
# In matrix-tuwunel-deploy .env
MATRIX_GROUPS_JSON='{
  "*": {
    "mode": "listen",
    "mention_patterns": ["\\bmeridian\\b", "\\bhey bot\\b"]
  },
  "!PPBT0ouhNr9W2TGjUk:matrix.oculair.ca": {
    "mode": "open"
  },
  "!ABCDEF:matrix.oculair.ca": {
    "mode": "mention-only"
  },
  "!DISABLED:matrix.oculair.ca": {
    "mode": "disabled"
  }
}'
```

### Config File Alternative
```yaml
# config.yaml (if using file-based config)
matrix_groups:
  "*":
    mode: listen
    mention_patterns:
      - "\\bmeridian\\b"
      - "\\bhey bot\\b"

  "!PPBT0ouhNr9W2TGjUk:matrix.oculair.ca":
    mode: open

  "!ABCDEF:matrix.oculair.ca":
    mode: mention-only
```

## Benefits

1. **Silent Observation:** Rooms with `mode: listen` process messages for memory without responding unless explicitly mentioned
2. **Flexible Routing:** Different rooms can have different behaviors (open, listen, mention-only, disabled)
3. **Explicit Control:** Admin can control which rooms the bot participates in and how
4. **Matrix-Native:** Uses Matrix's built-in mention system (m.mentions field)
5. **No LettaBot Changes:** All logic stays in the Matrix bridge; LettaBot remains channel-agnostic

## Files to Create/Modify

### Create (Python - Matrix Bridge)
1. `src/matrix/group_config.py` - Group configuration schema
2. `src/matrix/mention_detection.py` - Mention detection logic
3. `src/matrix/group_gating.py` - Group gating logic
4. `tests/unit/test_matrix_group_gating.py` - Unit tests

### Modify (Python - Matrix Bridge)
1. `src/core/config.py` - Add matrix_groups field to Config class
2. `src/matrix/client.py` - Add group gating to message_callback

## Testing Strategy

1. **Unit Tests:**
   - Test mention detection with Matrix pills, @username, and custom patterns
   - Test group mode resolution (specific room vs wildcard)
   - Test user allowlist enforcement
   - Test filtering logic for each mode

2. **Integration Tests:**
   - Test with real Matrix events in a test room
   - Verify responses are suppressed in listen mode
   - Verify responses work in open mode
   - Verify filtering in mention-only mode

## Estimated Effort

- Implementation: 2-3 hours
- Testing: 1 hour
- Documentation: 30 minutes

## Deployment

1. Add environment variable to `matrix-tuwunel-deploy/.env`
2. Rebuild `letta-matrix-client` container
3. Restart container: `docker compose restart matrix-client`
4. Monitor logs for `[GROUP_GATING]` messages

## Rollback Plan

If issues arise:
1. Set `MATRIX_GROUPS_JSON='{"*": {"mode": "open"}}'` to restore original behavior
2. Restart container
3. All rooms will process and respond to all messages (current behavior)
