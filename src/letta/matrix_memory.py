"""
Matrix Memory Block Manager - maintains shared matrix_capabilities block for Letta agents.
"""

import hashlib
import logging
from typing import Optional, List
from letta_client import Letta

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

## Responding to Matrix Users
When you receive a message with `[Matrix: @user in Room]` context:
1. Your response will be posted to that Matrix room
2. The user will see it in their Matrix client
3. You can use the matrix_messaging tool for advanced operations

## Tips
- Use reactions (👍 ✅ 🎉) to acknowledge without verbose replies
- You can create rooms for specific topics/projects
- Invite relevant users or agents to collaborate
"""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def get_letta_client() -> Letta:
    import os
    base_url = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
    api_key = os.getenv("LETTA_API_KEY") or os.getenv("LETTA_TOKEN", "")
    return Letta(base_url=base_url, api_key=api_key) if api_key else Letta(base_url=base_url)


async def get_or_create_matrix_block(client: Optional[Letta] = None) -> Optional[str]:
    """Get existing matrix block or create it. Returns block_id."""
    if client is None:
        client = get_letta_client()
    
    target_hash = _content_hash(MATRIX_CAPABILITIES_CONTENT)
    
    try:
        blocks = client.blocks.list()
        for block in blocks:
            if block.label == MATRIX_BLOCK_LABEL:
                current_hash = _content_hash(block.value or "")
                if current_hash != target_hash:
                    client.blocks.update(block_id=block.id, value=MATRIX_CAPABILITIES_CONTENT)
                    logger.info(f"[MatrixMemory] Updated block {block.id} (hash changed)")
                else:
                    logger.debug(f"[MatrixMemory] Block {block.id} content unchanged")
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
    """Attach block to agent if not already attached."""
    if client is None:
        client = get_letta_client()
    
    try:
        agent = client.agents.retrieve(agent_id=agent_id)
        attached_ids = [b.id for b in (agent.blocks or [])]
        
        if block_id in attached_ids:
            logger.debug(f"[MatrixMemory] Agent {agent_id} already has block")
            return True
        
        client.agents.blocks.attach(agent_id=agent_id, block_id=block_id)
        logger.info(f"[MatrixMemory] Attached block to agent {agent_id}")
        return True
        
    except Exception as e:
        logger.error(f"[MatrixMemory] Failed to attach block to {agent_id}: {e}")
        return False


async def sync_matrix_block_to_agents(agent_ids: List[str]) -> dict:
    """Sync matrix_capabilities block to all specified agents."""
    client = get_letta_client()
    
    block_id = await get_or_create_matrix_block(client)
    if not block_id:
        return {"error": "Failed to get/create block", "synced": 0, "failed": len(agent_ids)}
    
    synced = 0
    skipped = 0
    failed = 0
    
    for agent_id in agent_ids:
        try:
            agent = client.agents.retrieve(agent_id=agent_id)
            attached_ids = [b.id for b in (agent.blocks or [])]
            
            if block_id in attached_ids:
                skipped += 1
            else:
                client.agents.blocks.attach(agent_id=agent_id, block_id=block_id)
                synced += 1
                logger.info(f"[MatrixMemory] Attached to {agent_id}")
        except Exception as e:
            failed += 1
            logger.warning(f"[MatrixMemory] Failed for {agent_id}: {e}")
    
    logger.info(f"[MatrixMemory] Sync: {synced} attached, {skipped} already had, {failed} failed")
    return {"synced": synced, "skipped": skipped, "failed": failed, "block_id": block_id}


def format_matrix_context(sender: str, room_name: Optional[str] = None) -> str:
    """Format minimal Matrix context for message prefix."""
    if room_name:
        return f"[Matrix: {sender} in {room_name}]"
    return f"[Matrix: {sender}]"
