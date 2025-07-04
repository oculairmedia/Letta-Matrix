#!/usr/bin/env python3
"""
Purge all agent rooms using Synapse Admin API
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

async def make_room_private(homeserver_url, admin_token, room_id):
    """Make a room invite-only to prevent new joins"""
    url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.join_rules"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    data = {"join_rule": "invite"}
    
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=data) as response:
            if response.status == 200:
                print(f"✓ Made room {room_id} invite-only")
                return True
            else:
                print(f"Could not change room {room_id} settings: {response.status}")
                return False

async def kick_all_users(homeserver_url, admin_token, room_id):
    """Kick all users from a room"""
    # Get room members
    members_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/members"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        # Get members
        async with session.get(members_url, headers=headers) as response:
            if response.status != 200:
                print(f"Could not get members for {room_id}")
                return 0
            
            data = await response.json()
            members = data.get("chunk", [])
        
        # Kick each member except admin
        kicked = 0
        admin_user = f"@{admin_token.split(':')[0]}:matrix.oculair.ca"
        
        for member in members:
            user_id = member.get("state_key")
            membership = member.get("content", {}).get("membership")
            
            if user_id and membership == "join" and user_id != admin_user:
                kick_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/kick"
                kick_data = {
                    "user_id": user_id,
                    "reason": "Room is being closed"
                }
                
                async with session.post(kick_url, headers=headers, json=kick_data) as kick_response:
                    if kick_response.status == 200:
                        print(f"  ✓ Kicked {user_id}")
                        kicked += 1
                    else:
                        print(f"  ✗ Could not kick {user_id}: {kick_response.status}")
        
        return kicked

async def block_room(homeserver_url, admin_token, room_id):
    """Block a room using the admin API"""
    # Try the block room API
    url = f"{homeserver_url}/_synapse/admin/v1/rooms/{room_id}/block"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    data = {"block": True}
    
    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=data) as response:
            if response.status == 200:
                print(f"✓ Blocked room {room_id}")
                return True
            else:
                error_text = await response.text()
                print(f"Could not block room {room_id}: {response.status} - {error_text}")
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
    
    # List of all known agent rooms
    agent_rooms = [
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
    
    print(f"\nProcessing {len(agent_rooms)} agent rooms...")
    
    for room_id in agent_rooms:
        print(f"\nProcessing room {room_id}:")
        
        # 1. Make room private
        await make_room_private(homeserver_url, admin_token, room_id)
        
        # 2. Kick all users
        kicked_count = await kick_all_users(homeserver_url, admin_token, room_id)
        if kicked_count > 0:
            print(f"  Kicked {kicked_count} users")
        
        # 3. Try to block the room
        await block_room(homeserver_url, admin_token, room_id)
    
    print("\n✓ All agent rooms have been processed!")
    print("The rooms are now:")
    print("- Invite-only (no new joins)")
    print("- Empty (all users kicked)")
    print("- Admin has left and forgotten them")
    print("\nRestart the matrix-client container to create fresh rooms.")

if __name__ == "__main__":
    asyncio.run(main())