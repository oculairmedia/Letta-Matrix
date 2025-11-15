#!/usr/bin/env python3
import asyncio
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import after path is set
from agent_user_manager import AgentUserManager

async def test_sync():
    # Create a test config object
    class Config:
        homeserver_url = "http://synapse:8008"
        username = "@letta:matrix.oculair.ca"
        password = "letta"
        letta_api_url = "https://letta.oculair.ca"
        letta_token = "lettaSecurePass123"
        log_level = "INFO"
    
    config = Config()
    
    print("Creating AgentUserManager...")
    manager = AgentUserManager(config)
    
    print("Running sync_agents_to_users...")
    
    # First, let's see what agents we get
    agents = await manager.get_letta_agents()
    print(f"Found {len(agents)} Letta agents:")
    for agent in agents:
        print(f"  - {agent['name']} ({agent['id']})")
    
    await manager.sync_agents_to_users()
    
    print("Sync complete!")
    print(f"Total mappings: {len(manager.mappings)}")
    
    for agent_id, mapping in manager.mappings.items():
        print(f"  - {mapping.agent_name}: {mapping.matrix_user_id} (created: {mapping.created})")
    
    # Let's force a user creation attempt to see what happens
    print("\nForcing user creation for first agent...")
    if agents:
        first_agent = agents[0]
        
        # First, let's try to get an admin token
        print("Attempting to get admin token...")
        token = await manager.get_admin_token()
        print(f"Admin token result: {token[:20] + '...' if token else 'None'}")
        
        await manager.create_user_for_agent(first_agent)

if __name__ == "__main__":
    asyncio.run(test_sync())