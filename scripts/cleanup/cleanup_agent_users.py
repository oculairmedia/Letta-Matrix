#!/usr/bin/env python3
"""
Clean up all agent-created Matrix users to start fresh
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

async def list_all_users(homeserver_url, admin_token):
    """List all users on the server"""
    url = f"{homeserver_url}/_synapse/admin/v2/users"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    all_users = []
    from_token = 0
    
    async with aiohttp.ClientSession() as session:
        while True:
            params = {"from": from_token, "limit": 100}
            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    all_users.extend(data.get("users", []))
                    
                    # Check if there are more users
                    next_token = data.get("next_token")
                    if not next_token:
                        break
                    from_token = next_token
                else:
                    print(f"Failed to list users: {response.status}")
                    break
    
    return all_users

async def deactivate_user(homeserver_url, admin_token, user_id):
    """Deactivate a user"""
    url = f"{homeserver_url}/_synapse/admin/v1/deactivate/{user_id}"
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/json"
    }
    
    data = {"erase": True}  # Also erase user data
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            if response.status == 200:
                print(f"✓ Deactivated and erased user: {user_id}")
                return True
            else:
                error_text = await response.text()
                print(f"✗ Failed to deactivate {user_id}: {response.status} - {error_text}")
                return False

async def main():
    # Configuration
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
    admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "@matrixadmin:matrix.oculair.ca")
    admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "admin123")
    
    # Users to keep (essential system users)
    keep_users = {
        "@letta:matrix.oculair.ca",
        "@matrixadmin:matrix.oculair.ca", 
        "@admin:matrix.oculair.ca",
        "@slackbot:matrix.oculair.ca",  # Keep if exists
        "@telegram:matrix.oculair.ca",   # Keep if exists
        "@discordbot:matrix.oculair.ca", # Keep if exists
        "@gmessagesbot:matrix.oculair.ca" # Keep if exists
    }
    
    print("Getting admin token...")
    admin_token = await get_admin_token(homeserver_url, admin_username, admin_password)
    if not admin_token:
        print("Failed to get admin token")
        return
    
    print("Listing all users...")
    all_users = await list_all_users(homeserver_url, admin_token)
    print(f"Found {len(all_users)} total users")
    
    # Filter users to delete
    users_to_delete = []
    for user in all_users:
        user_id = user.get("name")
        if not user_id:
            continue
            
        # Skip essential users
        if user_id in keep_users:
            print(f"⏭️  Keeping essential user: {user_id}")
            continue
        
        # Delete all agent users (both old and new format)
        username_part = user_id.split(':')[0].replace('@', '')
        
        # Old format: agent names directly
        # New format: agent_{uuid}
        # Also catch any test agents or temporary names
        if any([
            username_part.startswith('agent_'),  # New format
            'agent' in username_part.lower(),     # Old format with "agent" in name
            username_part.startswith('companion-'),
            username_part.startswith('customer-'),
            username_part.startswith('character-'),
            username_part.startswith('scratch-'),
            username_part.startswith('personal-'),
            username_part in ['meridian', 'bombastic', 'bulbasaur', 'djange']  # Known agent names
        ]):
            users_to_delete.append(user_id)
    
    print(f"\nFound {len(users_to_delete)} agent users to delete:")
    for user in sorted(users_to_delete):
        print(f"  - {user}")
    
    if users_to_delete:
        print("\nDeactivating users...")
        for user_id in users_to_delete:
            await deactivate_user(homeserver_url, admin_token, user_id)
    
    # Clean up the mappings file
    mappings_file = "/app/data/agent_user_mappings.json"
    if os.path.exists(mappings_file):
        print(f"\nBacking up and clearing {mappings_file}")
        # Backup
        with open(mappings_file, 'r') as f:
            backup_data = f.read()
        with open(mappings_file + ".cleanup_backup", 'w') as f:
            f.write(backup_data)
        # Clear
        with open(mappings_file, 'w') as f:
            f.write("{}")
        print("✓ Mappings file cleared")
    
    print("\nCleanup complete! The system will create fresh users on next sync.")

if __name__ == "__main__":
    asyncio.run(main())