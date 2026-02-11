#!/usr/bin/env python3
"""Fix Meridian's broken room by recreating it"""
import asyncio
import json
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from core.agent_user_manager import AgentUserManager
from dotenv import load_dotenv

async def main():
    load_dotenv()
    
    manager = AgentUserManager(
        homeserver_url=os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008"),
        admin_username=os.getenv("MATRIX_ADMIN_USER", "admin"),
        admin_password=os.getenv("MATRIX_ADMIN_PASSWORD"),
        letta_api_url=os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
    )
    
    agent_id = "agent-597b5756-2915-4560-ba6b-91005f085166"
    agent_name = "Meridian"
    
    print(f"Recreating room for {agent_name} ({agent_id})...")
    
    # Delete old room from mappings
    if agent_id in manager.agent_user_mappings:
        old_room = manager.agent_user_mappings[agent_id].get('room_id')
        print(f"Old room ID: {old_room}")
        print(f"Marking room as not created...")
        manager.agent_user_mappings[agent_id]['room_id'] = None
        manager.agent_user_mappings[agent_id]['room_created'] = False
        manager._save_mappings()
        print("Mappings saved")
    
    # Recreate
    print("Creating new room...")
    result = await manager.create_or_update_agent_room(agent_id, agent_name)
    print(f"Result: {result}")
    
    # Verify
    new_room = manager.agent_user_mappings[agent_id].get('room_id')
    print(f"New room ID: {new_room}")
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
