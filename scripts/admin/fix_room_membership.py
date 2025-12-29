#!/usr/bin/env python3
"""
Fix room membership for all agent rooms.

This script ensures all required members are in every agent room:
- @admin:matrix.oculair.ca
- @letta:matrix.oculair.ca
- @agent_mail_bridge:matrix.oculair.ca

Usage:
    python scripts/admin/fix_room_membership.py [--dry-run]
"""
import asyncio
import aiohttp
import argparse
import os
import sys
import time
import psycopg2
from typing import Dict, List, Optional

# Configuration
HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://127.0.0.1:6167")
ADMIN_USERNAME = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("MATRIX_ADMIN_PASSWORD", "m6kvcVMWiSYzi6v")
ADMIN_ROOM = "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca"  # Tuwunel admin room

DB_HOST = os.getenv("POSTGRES_HOST", "192.168.50.90")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "matrix_letta")
DB_USER = os.getenv("POSTGRES_USER", "letta")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "letta")

# Required members
REQUIRED_MEMBERS = [
    "@admin:matrix.oculair.ca",
    "@letta:matrix.oculair.ca",
    "@agent_mail_bridge:matrix.oculair.ca",
]

# Known passwords for required users (will be reset via admin if needed)
USER_PASSWORDS = {
    "@agent_mail_bridge:matrix.oculair.ca": "MailBridge2024!",
}


async def get_admin_token(session: aiohttp.ClientSession) -> Optional[str]:
    """Get admin access token"""
    login_url = f"{HOMESERVER_URL}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "user": ADMIN_USERNAME,
        "password": ADMIN_PASSWORD
    }
    async with session.post(login_url, json=login_data) as response:
        if response.status == 200:
            data = await response.json()
            return data.get("access_token")
    return None


async def send_admin_command(session: aiohttp.ClientSession, admin_token: str, command: str) -> Optional[str]:
    """Send command to Tuwunel admin room"""
    txn_id = int(time.time() * 1000)
    url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{ADMIN_ROOM}/send/m.room.message/{txn_id}"
    headers = {"Authorization": f"Bearer {admin_token}"}
    data = {"msgtype": "m.text", "body": command}
    
    async with session.put(url, headers=headers, json=data) as response:
        if response.status != 200:
            return None
    
    await asyncio.sleep(0.5)
    
    # Read response
    messages_url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{ADMIN_ROOM}/messages?dir=b&limit=2"
    async with session.get(messages_url, headers=headers) as response:
        if response.status == 200:
            data = await response.json()
            for msg in data.get("chunk", []):
                body = msg.get("content", {}).get("body", "")
                if body and command not in body:
                    return body
    return None


async def get_room_members(session: aiohttp.ClientSession, admin_token: str, room_id: str) -> List[str]:
    """Get list of joined members in a room"""
    url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{room_id}/joined_members"
    headers = {"Authorization": f"Bearer {admin_token}"}
    async with session.get(url, headers=headers) as response:
        if response.status == 200:
            data = await response.json()
            return list(data.get("joined", {}).keys())
    return []


async def get_user_token(session: aiohttp.ClientSession, admin_token: str, user_id: str) -> Optional[str]:
    """Get or create access token for a user"""
    username = user_id.split(":")[0].replace("@", "")
    password = USER_PASSWORDS.get(user_id, f"TempPass_{username}!")
    
    # Try login
    login_url = f"{HOMESERVER_URL}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "user": username,
        "password": password
    }
    
    async with session.post(login_url, json=login_data) as response:
        if response.status == 200:
            data = await response.json()
            return data.get("access_token")
    
    # Reset password via admin command
    print(f"    Resetting password for {username}...")
    result = await send_admin_command(session, admin_token, f"!admin users reset-password {username} {password}")
    if result and "Successfully" in result:
        await asyncio.sleep(0.3)
        # Try login again
        async with session.post(login_url, json=login_data) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("access_token")
    
    return None


async def invite_and_join_user(
    session: aiohttp.ClientSession, 
    admin_token: str, 
    room_id: str, 
    user_id: str, 
    agent_token: str
) -> bool:
    """Invite a user to a room and have them join"""
    # Invite using agent token (agent has power to invite)
    invite_url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{room_id}/invite"
    headers = {"Authorization": f"Bearer {agent_token}"}
    invite_data = {"user_id": user_id}
    
    async with session.post(invite_url, headers=headers, json=invite_data) as response:
        if response.status not in [200, 403]:  # 403 might mean already in room
            error = await response.text()
            print(f"    Failed to invite {user_id}: {response.status} - {error}")
            return False
    
    # Get user token to accept invite
    user_token = await get_user_token(session, admin_token, user_id)
    if not user_token:
        print(f"    Could not get token for {user_id}")
        return False
    
    # Join room
    join_url = f"{HOMESERVER_URL}/_matrix/client/v3/join/{room_id}"
    headers = {"Authorization": f"Bearer {user_token}"}
    async with session.post(join_url, headers=headers, json={}) as response:
        if response.status == 200:
            return True
        elif response.status == 403:
            error = await response.text()
            if "already" in error.lower():
                return True
            print(f"    Join failed for {user_id}: {error}")
            return False
        else:
            error = await response.text()
            print(f"    Join failed for {user_id}: {response.status} - {error}")
            return False


def get_all_agent_rooms() -> List[Dict]:
    """Get all agent rooms from database"""
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT agent_id, agent_name, matrix_user_id, matrix_password, room_id 
            FROM agent_mappings 
            WHERE room_id IS NOT NULL AND room_created = true
            ORDER BY agent_name
        """)
        rows = cursor.fetchall()
        return [
            {
                "agent_id": row[0], 
                "agent_name": row[1], 
                "matrix_user_id": row[2],
                "matrix_password": row[3],
                "room_id": row[4]
            }
            for row in rows
        ]
    finally:
        conn.close()


async def fix_room_membership(dry_run: bool = False):
    """Fix room membership for all agent rooms"""
    print("=" * 60)
    print("Room Membership Fixer")
    print("=" * 60)
    print(f"Dry run: {dry_run}")
    print(f"Required members: {', '.join(REQUIRED_MEMBERS)}")
    print()
    
    # Get all agent rooms
    print("Loading agent rooms from database...")
    agent_rooms = get_all_agent_rooms()
    print(f"Found {len(agent_rooms)} agent rooms")
    print()
    
    async with aiohttp.ClientSession() as session:
        # Get admin token
        admin_token = await get_admin_token(session)
        if not admin_token:
            print("ERROR: Could not get admin token")
            return
        
        fixed = 0
        already_ok = 0
        failed = 0
        
        for agent in agent_rooms:
            room_id = agent["room_id"]
            agent_name = agent["agent_name"]
            
            # Get current members
            members = await get_room_members(session, admin_token, room_id)
            missing = [m for m in REQUIRED_MEMBERS if m not in members]
            
            if not missing:
                already_ok += 1
                continue
            
            print(f"\n{agent_name} ({room_id})")
            print(f"  Missing: {', '.join(missing)}")
            
            if dry_run:
                continue
            
            # Get agent token to invite
            agent_username = agent["matrix_user_id"].split(":")[0].replace("@", "")
            agent_password = agent["matrix_password"]
            
            login_url = f"{HOMESERVER_URL}/_matrix/client/v3/login"
            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": agent_password
            }
            
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    # Try to reset password
                    print(f"  Resetting agent password...")
                    new_password = f"AgentPass_{agent_username[:8]}!"
                    result = await send_admin_command(
                        session, admin_token, 
                        f"!admin users reset-password {agent_username} {new_password}"
                    )
                    if result and "Successfully" in result:
                        login_data["password"] = new_password
                        await asyncio.sleep(0.3)
                        async with session.post(login_url, json=login_data) as response2:
                            if response2.status != 200:
                                print(f"  ERROR: Could not login as agent")
                                failed += 1
                                continue
                            data = await response2.json()
                            agent_token = data.get("access_token")
                    else:
                        print(f"  ERROR: Could not reset agent password")
                        failed += 1
                        continue
                else:
                    data = await response.json()
                    agent_token = data.get("access_token")
            
            # Fix each missing member
            all_fixed = True
            for user_id in missing:
                print(f"  Adding {user_id}...")
                success = await invite_and_join_user(session, admin_token, room_id, user_id, agent_token)
                if success:
                    print(f"    OK")
                else:
                    print(f"    FAILED")
                    all_fixed = False
            
            if all_fixed:
                fixed += 1
            else:
                failed += 1
    
    print()
    print("=" * 60)
    print(f"Summary:")
    print(f"  Already OK: {already_ok}")
    print(f"  Fixed: {fixed}")
    print(f"  Failed: {failed}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Fix room membership for agent rooms")
    parser.add_argument("--dry-run", action="store_true", help="Don't make changes, just show what would be done")
    args = parser.parse_args()
    
    asyncio.run(fix_room_membership(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
