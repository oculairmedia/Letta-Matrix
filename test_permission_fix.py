#!/usr/bin/env python3
"""
Test script to verify the permission fix works correctly
"""
import asyncio
import logging
import sys
from agent_user_manager import AgentUserManager

# Simple config class for testing
class TestConfig:
    def __init__(self):
        self.homeserver_url = "http://matrix.oculair.ca:8008"
        self.username = "@letta:matrix.oculair.ca"
        self.password = "letta_password"
        self.log_level = "INFO"
        self.letta_token = "lettaSecurePass123"
        self.letta_api_url = "http://192.168.50.90:8283"

async def test_fix():
    """Test the permission fix"""
    config = TestConfig()
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("test_fix")
    
    manager = AgentUserManager(config)
    
    # Load existing mappings
    await manager.load_existing_mappings()
    
    logger.info(f"Loaded {len(manager.mappings)} agent mappings")
    
    # Test a specific room that was failing
    test_room_id = "!uVDZegkxMnvWCbwXmW:matrix.oculair.ca"
    test_user_id = "@admin:matrix.oculair.ca"
    
    # Find the agent name for this room
    agent_name = None
    for agent_id, mapping in manager.mappings.items():
        if mapping.room_id == test_room_id:
            agent_name = mapping.agent_name
            break
    
    if agent_name is None:
        logger.error(f"Could not find agent for room {test_room_id}")
        return False
    
    logger.info(f"Testing invitation fix for room {test_room_id} (agent: {agent_name})")
    
    # Test the fixed invitation function
    success = await manager._invite_user_with_retry(test_room_id, test_user_id, agent_name)
    
    if success:
        logger.info("✅ Permission fix test PASSED - invitation succeeded")
        return True
    else:
        logger.error("❌ Permission fix test FAILED - invitation failed")
        return False

if __name__ == "__main__":
    try:
        result = asyncio.run(test_fix())
        sys.exit(0 if result else 1)
    except Exception as e:
        print(f"Test failed with exception: {e}")
        sys.exit(1)