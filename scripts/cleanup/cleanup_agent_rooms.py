#!/usr/bin/env python3
"""
Clean up all agent-created Matrix rooms to start fresh
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

async def list_all_rooms(homeserver_url, admin_token):
    """List all rooms on the server"""
    url = f"{homeserver_url}/_synapse/admin/v1/rooms"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    all_rooms = []
    from_token = 0
    
    async with aiohttp.ClientSession() as session:
        while True:
            params = {"from": from_token, "limit": 100}
            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    all_rooms.extend(data.get("rooms", []))
                    
                    # Check if there are more rooms
                    next_token = data.get("next_batch")
                    if not next_token:
                        break
                    from_token = next_token
                else:
                    print(f"Failed to list rooms: {response.status}")
                    break
    
    return all_rooms

async def delete_room_from_server(homeserver_url, admin_token, room_id):
    """Delete a room"""
    url = f"{homeserver_url}/_synapse/admin/v2/rooms/{room_id}"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    # First get room details to show what we're deleting
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                room_data = await response.json()
                room_name = room_data.get("name", "Unnamed")
            else:
                room_name = "Unknown"
    
    # Now delete the room
    data = {
        "purge": True,  # Remove from database
        "message": "Room cleanup - starting fresh with agent rooms"
    }
    
    url = f"{homeserver_url}/_synapse/admin/v2/rooms/{room_id}/delete"
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            if response.status == 200:
                print(f"✓ Deleted room: {room_name} ({room_id})")
                return True
            else:
                error_text = await response.text()
                print(f"✗ Failed to delete {room_name} ({room_id}): {response.status} - {error_text}")
                return False

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
    
    print("Listing all rooms...")
    all_rooms = await list_all_rooms(homeserver_url, admin_token)
    print(f"Found {len(all_rooms)} total rooms")
    
    # Filter rooms to delete - all agent rooms
    rooms_to_delete = []
    for room in all_rooms:
        room_id = room.get("room_id")
        room_name = room.get("name", "")
        
        if not room_id:
            continue
        
        # Delete rooms that are agent-related
        delete_room = False
        
        # Check by room name
        if room_name:
            if any([
                "Letta Agent" in room_name,
                "agent" in room_name.lower() and "chat" in room_name.lower(),
                room_name in ["Meridian", "bombastic", "Bulbasaur", "djange"],
            ]):
                delete_room = True
        
        # Check by room ID
        if room_id in [
            "!jYyKTgDilZnRBnWJVb:matrix.oculair.ca",  # scratch-agent
            "!dTnywYeONAnPELoVlt:matrix.oculair.ca",  # companion-agent-sleeptime
            "!McvskatgCtMPkwQGsI:matrix.oculair.ca",  # Meridian (old)
            "!qUXoBCBVlIsQSCZioI:matrix.oculair.ca",  # Meridian (new)
            "!jaPhmFMnoHvsJyexXA:matrix.oculair.ca",  # character-roleplay
            "!iiOFOuZftXddWVpYSU:matrix.oculair.ca",  # bombastic
            "!rbcMYYliILdVSGjGeb:matrix.oculair.ca",  # companion-agent
            "!GyRiFhfdpdtjqmukuV:matrix.oculair.ca",  # customer-support
        ]:
            delete_room = True
            
        if delete_room:
            rooms_to_delete.append((room_id, room_name))
    
    print(f"\nFound {len(rooms_to_delete)} agent rooms to delete:")
    for room_id, room_name in sorted(rooms_to_delete, key=lambda x: x[1] if x[1] else ""):
        print(f"  - {room_name} ({room_id})")
    
    if rooms_to_delete:
        print("\nDeleting rooms...")
        for room_id, room_name in rooms_to_delete:
            await delete_room_from_server(homeserver_url, admin_token, room_id)
    
    print("\nRoom cleanup complete! Fresh rooms will be created on next sync.")

if __name__ == "__main__":
    asyncio.run(main())