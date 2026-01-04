"""
Matrix Memory Block Manager - maintains shared matrix_capabilities block for Letta agents.
"""

import hashlib
import logging
import os
import json
from typing import Optional, List
from letta_client import Letta
from src.letta.client import get_letta_client as _get_client

logger = logging.getLogger(__name__)

MATRIX_BLOCK_LABEL = "matrix_capabilities"

MATRIX_CAPABILITIES_CONTENT = """# Matrix Integration

You are connected to Matrix, a decentralized chat platform. Messages from Matrix users include context like:
`[Matrix: @user:domain in RoomName]`

## Available Actions (via matrix_messaging tool)

### Messaging
- **send**: Send message to a room or user
- **react**: Add emoji reaction to a message
- **edit**: Edit your previous message
- **typing**: Show typing indicator

### Room Management  
- **room_create**: Create a new room
- **room_invite**: Invite user to a room
- **room_join/room_leave**: Join or leave rooms
- **room_info**: Get room details (name, topic, members)
- **room_list**: List rooms you're in

### Users
- **identity_list**: See available identities
- **letta_list**: List other Letta agents you can message

## Poll Commands (in-message)

Create and manage polls directly in your responses:

### Create Poll
```
/poll "Question?" "Option 1" "Option 2" "Option 3"
/poll disclosed "Show results while voting?" "Yes" "No"
/poll undisclosed "Secret ballot?" "A" "B" "C"
```

### Get Results
```
/poll-results $poll_event_id
```

### Close Poll
```
/poll-close $poll_event_id
```

After creating a poll, you receive the poll_event_id. Users vote in Matrix clients (Element, etc.). Use /poll-results to see current votes, /poll-close to end and announce results.

## Responding to Matrix Users
When you receive a message with `[Matrix: @user in Room]` context:
1. Your response will be posted to that Matrix room
2. The user will see it in their Matrix client
3. You can use the matrix_messaging tool for advanced operations

## Tips
- Use reactions (👍 ✅ 🎉) to acknowledge without verbose replies
- You can create rooms for specific topics/projects
- Invite relevant users or agents to collaborate
- Use polls for quick decisions (lunch orders, meeting times, etc.)
"""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_letta_client() -> Letta:
    return _get_client()


async def get_or_create_matrix_block(client: Optional[Letta] = None) -> Optional[str]:
    if client is None:
        client = get_letta_client()
    
    target_hash = _content_hash(MATRIX_CAPABILITIES_CONTENT)
    
    try:
        for block in client.blocks.list(label=MATRIX_BLOCK_LABEL):
            current_hash = _content_hash(block.value or "")
            if current_hash != target_hash:
                client.blocks.update(block_id=block.id, value=MATRIX_CAPABILITIES_CONTENT)
                logger.info(f"[MatrixMemory] Updated block {block.id}")
            else:
                logger.debug(f"[MatrixMemory] Block {block.id} unchanged")
            return block.id
        
        block = client.blocks.create(
            label=MATRIX_BLOCK_LABEL,
            value=MATRIX_CAPABILITIES_CONTENT,
            description="Matrix chat integration capabilities"
        )
        logger.info(f"[MatrixMemory] Created block {block.id}")
        return block.id
        
    except Exception as e:
        logger.error(f"[MatrixMemory] Failed to get/create block: {e}")
        return None


async def ensure_agent_has_block(agent_id: str, block_id: str, client: Optional[Letta] = None) -> bool:
    if client is None:
        client = get_letta_client()
    
    try:
        current_blocks = client.agents.blocks.list(agent_id=agent_id)
        for block in current_blocks:
            if block.label == MATRIX_BLOCK_LABEL:
                if block.id == block_id:
                    logger.debug(f"[MatrixMemory] Agent {agent_id} already has current block")
                    return True
                client.agents.blocks.detach(agent_id=agent_id, block_id=block.id)
                logger.info(f"[MatrixMemory] Detached old block {block.id} from {agent_id}")
                break
        
        client.agents.blocks.attach(agent_id=agent_id, block_id=block_id)
        logger.info(f"[MatrixMemory] Attached block to agent {agent_id}")
        return True
        
    except Exception as e:
        logger.error(f"[MatrixMemory] Failed for {agent_id}: {e}")
        return False


async def sync_matrix_block_to_agents(agent_ids: List[str]) -> dict:
    client = get_letta_client()
    
    block_id = await get_or_create_matrix_block(client)
    if not block_id:
        return {"error": "Failed to get/create block", "synced": 0, "failed": len(agent_ids)}
    
    synced = 0
    failed = 0
    
    for agent_id in agent_ids:
        if await ensure_agent_has_block(agent_id, block_id, client):
            synced += 1
        else:
            failed += 1
    
    logger.info(f"[MatrixMemory] Sync complete: {synced} ok, {failed} failed")
    return {"synced": synced, "failed": failed, "block_id": block_id}


def format_matrix_context(sender: str, room_name: Optional[str] = None) -> str:
    if room_name:
        return f"[Matrix: {sender} in {room_name}]"
    return f"[Matrix: {sender}]"
