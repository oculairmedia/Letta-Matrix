#!/usr/bin/env python3
"""
Provision Matrix rooms for all Letta agents missing them.

This script:
1. Queries Letta for all agents
2. Compares with existing room mappings
3. Creates Matrix users and rooms for any missing agents
4. Updates BOTH the JSON file AND the database

Run from inside the matrix-client container or with access to the database.
"""

import asyncio
import aiohttp
import json
import os
import sys

# Add parent directory to path for imports when running standalone
sys.path.insert(0, '/opt/stacks/matrix-synapse-deployment')

MATRIX_HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://127.0.0.1:6167")
LETTA_API_URL = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
LETTA_TOKEN = os.getenv("LETTA_TOKEN", "lettaSecurePass123")
MATRIX_ADMIN_USERNAME = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
MATRIX_ADMIN_PASSWORD = os.getenv("MATRIX_ADMIN_PASSWORD", "m6kvcVMWiSYzi6v")
MATRIX_REGISTRATION_TOKEN = os.getenv("MATRIX_REGISTRATION_TOKEN", "matrix_mcp_secret_token_2024")
AGENT_USER_MAPPINGS_PATH = os.getenv("AGENT_USER_MAPPINGS_PATH", "/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_user_mappings.json")

# Try to import database module (may not be available when running standalone)
HAS_DB = False
try:
    from src.models.agent_mapping import AgentMappingDB
    HAS_DB = True
except ImportError:
    print("Warning: Database module not available, will only update JSON file")


async def get_admin_token():
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": MATRIX_ADMIN_USERNAME},
                "password": MATRIX_ADMIN_PASSWORD
            }
        ) as resp:
            data = await resp.json()
            return data.get("access_token")


async def get_letta_agents():
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{LETTA_API_URL}/v1/agents",
            headers={"Authorization": f"Bearer {LETTA_TOKEN}"}
        ) as resp:
            return await resp.json()


def load_mappings():
    if os.path.exists(AGENT_USER_MAPPINGS_PATH):
        with open(AGENT_USER_MAPPINGS_PATH, 'r') as f:
            return json.load(f)
    return {}


def save_mappings(mappings):
    with open(AGENT_USER_MAPPINGS_PATH, 'w') as f:
        json.dump(mappings, f, indent=2)


def update_database(agent_id, user_id, password, room_id, agent_name):
    """Update the database with the new mapping"""
    if not HAS_DB:
        return False
    
    try:
        from src.models.agent_mapping import AgentMappingDB as DB
        db = DB()
        existing = db.get_by_agent_id(agent_id)
        
        if existing:
            # Update existing record
            db.update(
                agent_id,
                room_id=room_id,
                room_created=True,
                matrix_password=password
            )
        else:
            # Create new record
            db.upsert(
                agent_id=agent_id,
                agent_name=agent_name,
                matrix_user_id=user_id,
                matrix_password=password,
                room_id=room_id,
                room_created=True
            )
        return True
    except Exception as e:
        print(f"  Warning: Database update failed: {e}")
        return False


async def register_user(localpart, password):
    """Register a new Matrix user with registration token flow"""
    async with aiohttp.ClientSession() as session:
        # Step 1: Initial request to get session
        async with session.post(
            f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/register",
            json={"username": localpart, "password": password}
        ) as resp:
            data = await resp.json()
            session_id = data.get("session")
        
        if not session_id:
            return None, None
        
        # Step 2: Complete with registration token
        async with session.post(
            f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/register",
            json={
                "username": localpart,
                "password": password,
                "auth": {
                    "type": "m.login.registration_token",
                    "token": MATRIX_REGISTRATION_TOKEN,
                    "session": session_id
                }
            }
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("user_id"), data.get("access_token")
            else:
                error = await resp.text()
                # User may already exist
                if "M_USER_IN_USE" in error:
                    # Try to login
                    async with session.post(
                        f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/login",
                        json={
                            "type": "m.login.password",
                            "identifier": {"type": "m.id.user", "user": localpart},
                            "password": password
                        }
                    ) as login_resp:
                        if login_resp.status == 200:
                            data = await login_resp.json()
                            return data.get("user_id"), data.get("access_token")
                print(f"    Registration failed: {error[:100]}")
                return None, None


async def create_user_and_room(admin_token, agent_id, agent_name):
    """Create Matrix user and room for an agent"""
    
    uuid_part = agent_id.replace("agent-", "").replace("-", "_")
    localpart = f"agent_{uuid_part}"
    password = f"AgentPass_{uuid_part[:8]}!"
    
    # Register or login
    user_id, access_token = await register_user(localpart, password)
    
    if not access_token:
        print(f"  ERROR: Could not get access token")
        return None, None, None
    
    print(f"  User: {user_id}")
    
    # Check if user already has a room
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/joined_rooms",
            headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                rooms = data.get("joined_rooms", [])
                if rooms:
                    print(f"  Already in room: {rooms[0]}")
                    return user_id, rooms[0], password
    
    # Create room
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/createRoom",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "name": agent_name,
                "topic": f"Room for {agent_name}",
                "preset": "private_chat",
                "initial_state": [
                    {"type": "m.room.history_visibility", "content": {"history_visibility": "shared"}}
                ]
            }
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                room_id = data.get("room_id")
                print(f"  Created room: {room_id}")
                
                # Invite admin
                await session.post(
                    f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/rooms/{room_id}/invite",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={"user_id": "@admin:matrix.oculair.ca"}
                )
                
                # Admin joins
                await session.post(
                    f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/join/{room_id}",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={}
                )
                print(f"  Admin joined")
                
                return user_id, room_id, password
            else:
                print(f"  Failed to create room: {await resp.text()}")
                return user_id, None, password


async def main():
    print("=== Provisioning Missing Agent Rooms ===\n")
    
    # Get admin token
    admin_token = await get_admin_token()
    if not admin_token:
        print("ERROR: Could not get admin token")
        return
    
    # Get all agents
    agents = await get_letta_agents()
    print(f"Found {len(agents)} Letta agents\n")
    
    # Load current mappings
    mappings = load_mappings()
    
    # Find missing agents
    missing = []
    for agent in agents:
        agent_id = agent.get("id", "")
        if not agent_id.startswith("agent-"):
            agent_id = f"agent-{agent_id}"
        
        if agent_id not in mappings:
            missing.append({
                "id": agent_id,
                "name": agent.get("name", "Unknown")
            })
    
    print(f"Found {len(missing)} agents without room mappings\n")
    
    if not missing:
        print("All agents have rooms!")
        return
    
    # Create rooms for missing agents
    created = 0
    db_updated = 0
    for agent in missing:
        print(f"Processing: {agent['name']} ({agent['id']})")
        
        user_id, room_id, password = await create_user_and_room(admin_token, agent['id'], agent['name'])
        
        if room_id:
            # Update JSON mappings
            mappings[agent['id']] = {
                "agent_id": agent['id'],
                "agent_name": agent['name'],
                "matrix_user_id": user_id,
                "matrix_room_id": room_id,
                "matrix_password": password
            }
            created += 1
            
            # Update database
            if update_database(agent['id'], user_id, password, room_id, agent['name']):
                db_updated += 1
                print(f"  Database updated")
        print()
    
    # Save updated mappings to JSON
    save_mappings(mappings)
    print(f"\n=== Done. Created {created} rooms. Database updated: {db_updated} ===")


if __name__ == "__main__":
    asyncio.run(main())
