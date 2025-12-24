#!/usr/bin/env python3
"""
Invite Agent Mail Bridge to all existing agent rooms
"""
import asyncio
import json
import logging
from pathlib import Path
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

HOMESERVER_URL = "https://matrix.oculair.ca"
BRIDGE_USER_ID = "@agent_mail_bridge:matrix.oculair.ca"
MAPPINGS_FILE = Path(__file__).parent.parent.parent / "matrix_client_data" / "agent_user_mappings.json"


async def invite_bridge_to_room(session: aiohttp.ClientSession, room_id: str, agent_name: str, agent_user_id: str, agent_password: str) -> bool:
    """
    Invite bridge user to a room by logging in as the agent and sending an invitation
    
    Args:
        session: aiohttp session
        room_id: Matrix room ID
        agent_name: Agent's display name (for logging)
        agent_user_id: Agent's Matrix user ID
        agent_password: Agent's Matrix password
        
    Returns:
        True if invitation sent successfully, False otherwise
    """
    try:
        # First check if bridge is already in the room
        # (We can't check directly without being in the room, so we'll just try to invite)
        
        # Login as the agent
        agent_username = agent_user_id.split(':')[0].replace('@', '')
        login_url = f"{HOMESERVER_URL}/_matrix/client/r0/login"
        
        login_data = {
            "type": "m.login.password",
            "user": agent_username,
            "password": agent_password
        }
        
        async with session.post(login_url, json=login_data) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"  Failed to login as {agent_name}: {response.status} - {error_text}")
                return False
            
            auth_data = await response.json()
            agent_token = auth_data.get("access_token")
        
        if not agent_token:
            logger.error(f"  No token received for {agent_name}")
            return False
        
        # Send invitation to bridge user
        invite_url = f"{HOMESERVER_URL}/_matrix/client/r0/rooms/{room_id}/invite"
        headers = {
            "Authorization": f"Bearer {agent_token}",
            "Content-Type": "application/json"
        }
        
        invite_data = {
            "user_id": BRIDGE_USER_ID
        }
        
        async with session.post(invite_url, headers=headers, json=invite_data) as response:
            if response.status == 200:
                logger.info(f"  ✅ Invited bridge to {agent_name}'s room")
                return True
            elif response.status == 403:
                error_text = await response.text()
                error_json = await response.json() if response.content_type == 'application/json' else {}
                
                # Check if user is already in the room
                if "already in the room" in error_text.lower() or error_json.get("errcode") == "M_FORBIDDEN":
                    logger.info(f"  ℹ️  Bridge already in {agent_name}'s room")
                    return True
                else:
                    logger.warning(f"  ⚠️  Cannot invite bridge to {agent_name}'s room: {error_text}")
                    return False
            else:
                error_text = await response.text()
                logger.warning(f"  ⚠️  Failed to invite bridge to {agent_name}'s room: {response.status} - {error_text}")
                return False
    
    except Exception as e:
        logger.error(f"  Error inviting bridge to {agent_name}'s room: {e}")
        return False


async def main():
    """Main function to invite bridge to all agent rooms"""
    logger.info("Starting bulk invitation of Agent Mail Bridge to all agent rooms")
    
    # Load agent mappings
    if not MAPPINGS_FILE.exists():
        logger.error(f"Agent mappings file not found: {MAPPINGS_FILE}")
        return
    
    with open(MAPPINGS_FILE, 'r') as f:
        mappings = json.load(f)
    
    logger.info(f"Loaded {len(mappings)} agent mappings")
    
    # Process each agent room
    success_count = 0
    failed_count = 0
    skipped_count = 0
    
    async with aiohttp.ClientSession() as session:
        for agent_id, mapping in mappings.items():
            agent_name = mapping.get("agent_name", agent_id)
            room_id = mapping.get("room_id")
            room_created = mapping.get("room_created", False)
            agent_user_id = mapping.get("matrix_user_id")
            agent_password = mapping.get("matrix_password")
            
            # Skip if room not created or missing data
            if not room_created or not room_id:
                logger.debug(f"Skipping {agent_name} - no room created")
                skipped_count += 1
                continue
            
            if not agent_user_id or not agent_password:
                logger.warning(f"Skipping {agent_name} - missing credentials")
                skipped_count += 1
                continue
            
            logger.info(f"Processing {agent_name} ({room_id})")
            
            # Invite bridge to the room
            success = await invite_bridge_to_room(
                session=session,
                room_id=room_id,
                agent_name=agent_name,
                agent_user_id=agent_user_id,
                agent_password=agent_password
            )
            
            if success:
                success_count += 1
            else:
                failed_count += 1
            
            # Small delay to avoid rate limiting
            await asyncio.sleep(0.5)
    
    # Summary
    logger.info("=" * 60)
    logger.info("Bulk invitation complete!")
    logger.info(f"  ✅ Success: {success_count}")
    logger.info(f"  ❌ Failed: {failed_count}")
    logger.info(f"  ⏭️  Skipped: {skipped_count}")
    logger.info(f"  📊 Total: {len(mappings)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
