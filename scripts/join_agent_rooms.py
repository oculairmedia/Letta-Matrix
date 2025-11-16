#!/usr/bin/env python3
"""
Make @letta user join all agent rooms
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
    
    # Login as @letta
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
    username = "@letta:matrix.oculair.ca"
    password = "letta"
    
    login_url = f"{homeserver_url}/_matrix/client/r0/login"
    login_data = {
        "type": "m.login.password",
        "user": "letta",
        "password": password
    }
    
    async with aiohttp.ClientSession() as session:
        # Login
        async with session.post(login_url, json=login_data) as response:
            if response.status != 200:
                print(f"Failed to login as {username}")
                return
            
            auth_data = await response.json()
            token = auth_data.get("access_token")
            
        if not token:
            print("No token received")
            return
            
        print(f"Successfully logged in as {username}")
        
        # Join each room
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        for agent_id, mapping in mappings.items():
            room_id = mapping.get("room_id")
            agent_name = mapping.get("agent_name")
            
            if not room_id:
                continue
                
            join_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
            
            async with session.post(join_url, headers=headers, json={}) as response:
                if response.status == 200:
                    print(f"✓ Joined room for {agent_name} ({room_id})")
                elif response.status == 403:
                    print(f"✗ Already in room for {agent_name}")
                else:
                    error_text = await response.text()
                    print(f"✗ Failed to join room for {agent_name}: {response.status} - {error_text}")
        
        print("\nAll done!")

if __name__ == "__main__":
    asyncio.run(main())