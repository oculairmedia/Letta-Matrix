#!/usr/bin/env python3
"""
Delete all agent rooms using Synapse Admin API
"""
import asyncio
import aiohttp
import json
import os

async def get_admin_token(homeserver_url, admin_username, admin_password):
    """Get admin access token"""
    login_url = f"{homeserver_url}/_matrix/client/r0/login"
    username = admin_username.split(':')[0].replace('@', '')
    login_data = {
        "type": "m.login.password",
        "user": username,
        "password": admin_password
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(login_url, json=login_data) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("access_token")
    return None

async def leave_and_forget_room(homeserver_url, admin_token, room_id, user_id):
    """Make a user leave and forget a room"""
    # First leave the room
    leave_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/leave"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(leave_url, headers=headers, json={}) as response:
            if response.status not in [200, 404]:
                print(f"Could not leave room {room_id}: {response.status}")
        
        # Then forget the room
        forget_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/forget"
        async with session.post(forget_url, headers=headers, json={}) as response:
            if response.status == 200:
                print(f"✓ Left and forgot room {room_id}")

async def shutdown_room(homeserver_url, admin_token, room_id, room_name=None):
    """Shutdown a room using admin API"""
    url = f"{homeserver_url}/_synapse/admin/v1/shutdown_room/{room_id}"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    data = {
        "message": "This room has been closed by administration"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            if response.status == 200:
                result = await response.json()
                print(f"✓ Shut down room: {room_name or room_id}")
                print(f"  - Kicked {result.get('kicked_users', 0)} users")
                print(f"  - Moved to: {result.get('new_room_id', 'N/A')}")
                return True
            else:
                error_text = await response.text()
                print(f"✗ Failed to shutdown {room_name or room_id}: {response.status} - {error_text}")
                return False

async def list_rooms_for_user(homeserver_url, admin_token, user_id):
    """List all rooms a user is in"""
    url = f"{homeserver_url}/_matrix/client/r0/joined_rooms"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("joined_rooms", [])
    return []

async def main():
    # Configuration
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
    admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "@matrixadmin:matrix.oculair.ca")
    admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "admin123")
    
    print("Getting admin token...")
    admin_token = await get_admin_token(homeserver_url, admin_username, admin_password)
    if not admin_token:
        print("Failed to get admin token")
        return
    
    # Get list of agent rooms from mappings
    agent_rooms = []
    mappings_file = "/app/data/agent_user_mappings.json"
    if os.path.exists(mappings_file):
        with open(mappings_file, 'r') as f:
            mappings = json.load(f)
            for agent_id, mapping in mappings.items():
                if mapping.get("room_id"):
                    agent_rooms.append({
                        "room_id": mapping["room_id"],
                        "agent_name": mapping.get("agent_name", "Unknown")
                    })
    
    # Also include known room IDs
    known_agent_rooms = [
        "!jYyKTgDilZnRBnWJVb:matrix.oculair.ca",  # scratch-agent
        "!dTnywYeONAnPELoVlt:matrix.oculair.ca",  # companion-agent-sleeptime
        "!McvskatgCtMPkwQGsI:matrix.oculair.ca",  # Meridian (old)
        "!qUXoBCBVlIsQSCZioI:matrix.oculair.ca",  # Meridian (new)
        "!jaPhmFMnoHvsJyexXA:matrix.oculair.ca",  # character-roleplay
        "!iiOFOuZftXddWVpYSU:matrix.oculair.ca",  # bombastic
        "!rbcMYYliILdVSGjGeb:matrix.oculair.ca",  # companion-agent
        "!GyRiFhfdpdtjqmukuV:matrix.oculair.ca",  # customer-support
        "!OCgiszqsyYiSpEnDsX:matrix.oculair.ca",  # Meridian duplicate
        "!RaxgFueeidKTYnIaUT:matrix.oculair.ca",  # Meridian duplicate
        "!LZHZsJSPEWuhSmgijF:matrix.oculair.ca",  # companion duplicate
        "!TvVsMnIEKtFfkGmihq:matrix.oculair.ca",  # companion duplicate
        "!ViIDVxugoXqKwgxmfB:matrix.oculair.ca",  # companion duplicate
        "!QoXMNjQLyKeBuLTDut:matrix.oculair.ca",  # scratch duplicate
        "!RpZQNiTdSrscJIVrKR:matrix.oculair.ca",  # scratch duplicate
        "!TIZdzuugESnZRlipJo:matrix.oculair.ca",  # scratch duplicate
    ]
    
    # Combine all rooms
    all_room_ids = set()
    for room in agent_rooms:
        all_room_ids.add(room["room_id"])
    all_room_ids.update(known_agent_rooms)
    
    print(f"\nFound {len(all_room_ids)} agent rooms to delete")
    
    # First, make admin leave all these rooms
    print("\nMaking admin leave rooms...")
    for room_id in all_room_ids:
        await leave_and_forget_room(homeserver_url, admin_token, room_id, admin_username)
    
    # Then shutdown the rooms
    print("\nShutting down rooms...")
    for room_id in all_room_ids:
        room_name = next((r["agent_name"] for r in agent_rooms if r["room_id"] == room_id), None)
        await shutdown_room(homeserver_url, admin_token, room_id, room_name)
    
    # Clear the mappings file
    print("\nClearing mappings...")
    with open(mappings_file, 'w') as f:
        f.write("{}")
    print("✓ Mappings cleared")
    
    print("\nRoom deletion complete! Restart the matrix-client container to create fresh rooms.")

if __name__ == "__main__":
    asyncio.run(main())