#!/usr/bin/env python3
"""
Fix Agent Display Names

This script fixes agent Matrix user display names by:
1. Getting the correct agent_name from the database
2. Reset password via Tuwunel admin command if needed
3. Logging in as each agent user
4. Setting their display name using their own access token

This is necessary because Matrix doesn't allow admins to change
other users' display names via the client API.
"""

import asyncio
import aiohttp
import asyncpg
import sys
import time
from typing import Optional, Tuple

# Configuration
HOMESERVER_URL = "http://127.0.0.1:6167"
DATABASE_URL = "postgresql://letta:letta@192.168.50.90:5432/matrix_letta"
ADMIN_USER = "@admin:matrix.oculair.ca"
ADMIN_PASSWORD = "m6kvcVMWiSYzi6v"
ADMIN_ROOM = "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca"
AGENT_PASSWORD = "password"  # Standard password for all agents


async def get_admin_token(session: aiohttp.ClientSession) -> Optional[str]:
    """Login as admin and return token"""
    url = f"{HOMESERVER_URL}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": ADMIN_USER},
        "password": ADMIN_PASSWORD
    }
    try:
        async with session.post(url, json=login_data) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("access_token")
    except Exception as e:
        print(f"Error getting admin token: {e}")
    return None


async def send_admin_command(session: aiohttp.ClientSession, admin_token: str, command: str) -> Optional[str]:
    """Send an admin command to the admin room and read the response"""
    # Send command
    url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{ADMIN_ROOM}/send/m.room.message/{int(time.time() * 1000)}"
    headers = {"Authorization": f"Bearer {admin_token}"}
    data = {"msgtype": "m.text", "body": command}
    
    try:
        async with session.put(url, headers=headers, json=data) as response:
            if response.status != 200:
                return None
        
        # Wait for response
        await asyncio.sleep(0.5)
        
        # Read response
        messages_url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{ADMIN_ROOM}/messages?dir=b&limit=2"
        async with session.get(messages_url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                for msg in data.get("chunk", []):
                    body = msg.get("content", {}).get("body", "")
                    if body and body != command:
                        return body
    except Exception as e:
        print(f"Error sending admin command: {e}")
    return None


async def reset_password(session: aiohttp.ClientSession, admin_token: str, user_id: str, password: str) -> bool:
    """Reset a user's password via Tuwunel admin command"""
    # Extract username from @user:domain
    username = user_id.split(":")[0].replace("@", "")
    command = f"!admin users reset-password {username} {password}"
    
    result = await send_admin_command(session, admin_token, command)
    if result and "Successfully" in result:
        return True
    elif result:
        # Check if it's a non-error response
        if "password" in result.lower() and "reset" in result.lower():
            return True
    return False


async def login_user(session: aiohttp.ClientSession, user_id: str, password: str) -> Optional[str]:
    """Login as a user and return their access token"""
    url = f"{HOMESERVER_URL}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user_id},
        "password": password
    }
    
    try:
        async with session.post(url, json=login_data) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("access_token")
    except Exception:
        pass
    return None


async def get_display_name(session: aiohttp.ClientSession, user_id: str) -> Optional[str]:
    """Get current display name of a user"""
    url = f"{HOMESERVER_URL}/_matrix/client/v3/profile/{user_id}/displayname"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("displayname")
    except Exception:
        pass
    return None


async def set_display_name(session: aiohttp.ClientSession, user_id: str, display_name: str, token: str) -> bool:
    """Set display name for a user using their access token"""
    url = f"{HOMESERVER_URL}/_matrix/client/v3/profile/{user_id}/displayname"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = {"displayname": display_name}
    
    try:
        async with session.put(url, headers=headers, json=data) as response:
            return response.status == 200
    except Exception:
        pass
    return False


async def fix_single_agent(
    session: aiohttp.ClientSession, 
    admin_token: str,
    agent_name: str, 
    matrix_user_id: str,
    dry_run: bool
) -> Tuple[str, str]:
    """Fix display name for a single agent. Returns (status, message)"""
    
    current_name = await get_display_name(session, matrix_user_id)
    
    if current_name == agent_name:
        return ("skip", "already correct")
    
    if dry_run:
        return ("would_fix", f"'{current_name}' -> '{agent_name}'")
    
    # Try to login with standard password
    token = await login_user(session, matrix_user_id, AGENT_PASSWORD)
    
    if not token:
        # Reset password via admin command
        success = await reset_password(session, admin_token, matrix_user_id, AGENT_PASSWORD)
        if not success:
            return ("failed", "could not reset password")
        
        # Try login again
        await asyncio.sleep(0.2)
        token = await login_user(session, matrix_user_id, AGENT_PASSWORD)
        if not token:
            return ("failed", "login failed after password reset")
    
    # Set display name
    success = await set_display_name(session, matrix_user_id, agent_name, token)
    if success:
        return ("fixed", f"'{current_name}' -> '{agent_name}'")
    else:
        return ("failed", "could not set display name")


async def fix_display_names(dry_run: bool = True):
    """Fix display names for all agents"""
    
    print(f"{'DRY RUN - ' if dry_run else ''}Fixing agent display names...")
    print("=" * 70)
    
    # Connect to database
    conn = await asyncpg.connect(DATABASE_URL)
    
    try:
        # Get all agents with matrix users
        agents = await conn.fetch("""
            SELECT agent_id, agent_name, matrix_user_id 
            FROM agent_mappings 
            WHERE matrix_user_id IS NOT NULL
            ORDER BY agent_name
        """)
        
        print(f"Found {len(agents)} agents with Matrix users\n")
        
        stats = {"skip": 0, "would_fix": 0, "fixed": 0, "failed": 0}
        
        async with aiohttp.ClientSession() as session:
            # Get admin token for password resets
            admin_token = await get_admin_token(session)
            if not admin_token:
                if not dry_run:
                    print("ERROR: Could not get admin token")
                    return
                admin_token = ""  # Placeholder for dry run
            
            for agent in agents:
                agent_name = agent["agent_name"]
                matrix_user_id = agent["matrix_user_id"]
                
                status, message = await fix_single_agent(
                    session, admin_token, agent_name, matrix_user_id, dry_run
                )
                
                stats[status] += 1
                
                if status != "skip":
                    status_emoji = {"would_fix": "[DRY]", "fixed": "[OK]", "failed": "[ERR]"}[status]
                    print(f"{status_emoji} {agent_name}")
                    print(f"      User: {matrix_user_id}")
                    print(f"      {message}\n")
        
        print("=" * 70)
        print("Summary:")
        print(f"  Already correct: {stats['skip']}")
        if dry_run:
            print(f"  Would fix: {stats['would_fix']}")
        else:
            print(f"  Fixed: {stats['fixed']}")
            print(f"  Failed: {stats['failed']}")
        
    finally:
        await conn.close()


async def main():
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    
    if len(sys.argv) > 1 and sys.argv[1] not in ["--dry-run", "-n", "--fix"]:
        print("Usage: fix_agent_display_names.py [--dry-run|-n|--fix]")
        print("  --dry-run, -n  Show what would be fixed without making changes")
        print("  --fix          Actually fix the display names")
        sys.exit(1)
    
    if "--fix" not in sys.argv and not dry_run:
        dry_run = True
        print("Running in dry-run mode. Use --fix to apply changes.\n")
    
    await fix_display_names(dry_run=dry_run)


if __name__ == "__main__":
    asyncio.run(main())
