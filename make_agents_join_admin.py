#!/usr/bin/env python3
"""
Make agent users join @matrixadmin to their rooms
"""
import asyncio
import aiohttp
import json
import os

async def main():
    # Read mappings to get room IDs
    mappings_file = "/app/data/agent_user_mappings.json"
    if not os.path.exists(mappings_file):
        print("No mappings file found")
        return
        
    with open(mappings_file, 'r') as f:
        mappings = json.load(f)
    
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
    
    for agent_id, mapping in mappings.items():
        agent_username = mapping.get("matrix_user_id")
        agent_password = mapping.get("matrix_password")
        room_id = mapping.get("room_id")
        agent_name = mapping.get("agent_name")
        
        if not all([agent_username, agent_password, room_id]):
            continue
            
        print(f"\nProcessing {agent_name}...")
        
        # Login as agent user
        login_url = f"{homeserver_url}/_matrix/client/r0/login"
        user_local = agent_username.split(':')[0].replace('@', '')
        login_data = {
            "type": "m.login.password",
            "user": user_local,
            "password": agent_password
        }
        
        async with aiohttp.ClientSession() as session:
            # Login
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    print(f"✗ Failed to login as {agent_username}")
                    continue
                
                auth_data = await response.json()
                token = auth_data.get("access_token")
                
            if not token:
                print(f"✗ No token received for {agent_username}")
                continue
                
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            # Join @matrixadmin to room
            invite_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/invite"
            invite_data = {"user_id": "@matrixadmin:matrix.oculair.ca"}
            
            async with session.post(invite_url, headers=headers, json=invite_data) as response:
                if response.status == 200:
                    print(f"✓ Invited @matrixadmin to {agent_name}'s room")
                elif response.status == 403:
                    # Already invited or in room
                    print(f"- @matrixadmin already invited/joined to {agent_name}'s room")
                else:
                    error_text = await response.text()
                    print(f"✗ Failed to invite @matrixadmin: {response.status} - {error_text}")

if __name__ == "__main__":
    asyncio.run(main())