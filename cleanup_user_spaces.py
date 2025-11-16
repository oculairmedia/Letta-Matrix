#!/usr/bin/env python3
import asyncio
import aiohttp
import os

async def get_user_token(username, password):
    """Get user token"""
    login_url = "http://synapse:8008/_matrix/client/r0/login"
    login_data = {
        "type": "m.login.password",
        "user": username,
        "password": password
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(login_url, json=login_data, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("access_token")
    return None

async def get_user_rooms(token):
    """Get all rooms user is in"""
    url = "http://synapse:8008/_matrix/client/r0/joined_rooms"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("joined_rooms", [])
    return []

async def leave_room(token, room_id):
    """Leave a room"""
    url = f"http://synapse:8008/_matrix/client/r0/rooms/{room_id}/leave"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={}, timeout=aiohttp.ClientTimeout(total=10)) as response:
            return response.status

async def forget_room(token, room_id):
    """Forget a room"""
    url = f"http://synapse:8008/_matrix/client/r0/rooms/{room_id}/forget"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={}, timeout=aiohttp.ClientTimeout(total=10)) as response:
            return response.status

async def cleanup_user_spaces():
    """Clean up ghost spaces from user's view"""
    
    # Get admin user token
    admin_token = await get_user_token("admin", "admin")
    if not admin_token:
        print("❌ Failed to get admin token")
        return
    
    print("✓ Got admin user token")
    
    # Get all rooms admin is in
    rooms = await get_user_rooms(admin_token)
    print(f"✓ Found {len(rooms)} rooms user is in")
    
    # Current active space
    active_space = "!yGWLcQviqiMvfTzcdd:matrix.oculair.ca"
    
    left_count = 0
    forgot_count = 0
    failed_count = 0
    
    for i, room_id in enumerate(rooms, 1):
        if room_id == active_space:
            print(f"[{i}/{len(rooms)}] ⏭️  SKIPPED (active space): {room_id}")
            continue
        
        # Try to leave
        leave_status = await leave_room(admin_token, room_id)
        if leave_status in [200, 404]:
            left_count += 1
        
        # Try to forget
        forget_status = await forget_room(admin_token, room_id)
        if forget_status in [200, 404]:
            forgot_count += 1
            print(f"[{i}/{len(rooms)}] ✓ Forgotten: {room_id}")
        else:
            failed_count += 1
            print(f"[{i}/{len(rooms)}] ✗ Failed: {room_id}")
        
        if i % 50 == 0:
            print(f"Progress: {i}/{len(rooms)} processed...")
    
    print(f"\n{'='*60}")
    print(f"✅ Left: {left_count}")
    print(f"✅ Forgotten: {forgot_count}")
    print(f"❌ Failed: {failed_count}")
    print(f"{'='*60}")
    print("\n💡 Tip: Refresh your Element client (Ctrl+R) to see changes")

if __name__ == "__main__":
    asyncio.run(cleanup_user_spaces())
