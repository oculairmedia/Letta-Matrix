#!/usr/bin/env python3
"""
Agent User Manager - Creates Matrix users for each Letta agent
"""
import asyncio
import logging
import os
import json
import aiohttp
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from letta_client import AsyncLetta
from letta_client.core import ApiError

logger = logging.getLogger("matrix_client.agent_user_manager")

@dataclass
class AgentUserMapping:
    agent_id: str
    agent_name: str
    matrix_user_id: str
    matrix_password: str
    created: bool = False
    room_id: Optional[str] = None
    room_created: bool = False

class AgentUserManager:
    """Manages Matrix users for Letta agents"""
    
    def __init__(self, config):
        self.config = config
        self.matrix_api_url = config.matrix_api_url if hasattr(config, 'matrix_api_url') else "http://matrix-api:8000"
        self.homeserver_url = config.homeserver_url
        self.letta_token = config.letta_token
        self.letta_api_url = config.letta_api_url
        self.mappings_file = "/app/data/agent_user_mappings.json"
        self.mappings: Dict[str, AgentUserMapping] = {}
        self.admin_token = None  # Will be obtained programmatically
        
        # Matrix admin credentials - try to use a dedicated admin account
        # Fall back to main letta user if not specified
        self.admin_username = os.getenv("MATRIX_ADMIN_USERNAME", config.username)
        self.admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", config.password)
        logger.info(f"Using admin account: {self.admin_username} (from env: {os.getenv('MATRIX_ADMIN_USERNAME')})")
        
        # Ensure data directory exists
        os.makedirs("/app/data", exist_ok=True)
        
    async def load_existing_mappings(self):
        """Load existing agent-user mappings from file"""
        try:
            if os.path.exists(self.mappings_file):
                with open(self.mappings_file, 'r') as f:
                    data = json.load(f)
                    for agent_id, mapping_data in data.items():
                        self.mappings[agent_id] = AgentUserMapping(**mapping_data)
                logger.info(f"Loaded {len(self.mappings)} existing agent-user mappings")
            else:
                logger.info("No existing mappings file found")
        except Exception as e:
            logger.error(f"Error loading mappings: {e}")
    
    async def save_mappings(self):
        """Save agent-user mappings to file"""
        try:
            data = {}
            for agent_id, mapping in self.mappings.items():
                data[agent_id] = {
                    "agent_id": mapping.agent_id,
                    "agent_name": mapping.agent_name,
                    "matrix_user_id": mapping.matrix_user_id,
                    "matrix_password": mapping.matrix_password,
                    "created": mapping.created,
                    "room_id": mapping.room_id,
                    "room_created": mapping.room_created
                }
            
            with open(self.mappings_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(self.mappings)} agent-user mappings")
        except Exception as e:
            logger.error(f"Error saving mappings: {e}")
    
    async def get_letta_agents(self) -> List[dict]:
        """Get all Letta agents"""
        try:
            letta_client = AsyncLetta(
                token=self.letta_token,
                base_url=self.letta_api_url,
                timeout=60.0
            )
            
            agents = await letta_client.agents.list()
            agent_list = []
            for agent in agents:
                agent_list.append({
                    "id": agent.id,
                    "name": getattr(agent, 'name', f"agent-{agent.id[:8]}")
                })
            
            logger.info(f"Found {len(agent_list)} Letta agents")
            return agent_list
            
        except Exception as e:
            logger.error(f"Error getting Letta agents: {e}")
            return []
    
    async def get_admin_token(self) -> Optional[str]:
        """Get an admin access token by logging in as the admin user"""
        if self.admin_token:
            logger.debug("Using cached admin token")
            return self.admin_token
            
        try:
            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            username = self.admin_username.split(':')[0].replace('@', '')  # Extract just username
            login_data = {
                "type": "m.login.password",
                "user": username,
                "password": self.admin_password
            }
            
            logger.info(f"Attempting to get admin token for user: {username}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(login_url, json=login_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.admin_token = data.get("access_token")
                        logger.info(f"Successfully obtained admin access token for user {username}")
                        return self.admin_token
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get admin token for {username}: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error getting admin token: {e}")
            return None
    
    async def check_user_exists(self, username: str) -> bool:
        """Check if a Matrix user already exists"""
        try:
            # Use Synapse admin API to check if user exists
            url = f"{self.homeserver_url}/_synapse/admin/v2/users/@{username}:matrix.oculair.ca"
            
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check user existence")
                return False
            
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        user_data = await response.json()
                        return not user_data.get("deactivated", True)  # User exists and is not deactivated
                    elif response.status == 404:
                        return False  # User doesn't exist
                    else:
                        logger.warning(f"Unexpected response checking user {username}: {response.status}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error checking if user {username} exists: {e}")
            return False

    async def create_matrix_user(self, username: str, password: str, display_name: str) -> bool:
        """Create a new Matrix user via admin API"""
        try:
            # Use Synapse admin API to create user
            url = f"{self.homeserver_url}/_synapse/admin/v2/users/@{username}:matrix.oculair.ca"
            
            # Get admin access token programmatically
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, user creation will fail")
                return False
            
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            data = {
                "password": password,
                "displayname": display_name,
                "admin": False,
                "deactivated": False
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data) as response:
                    if response.status == 201:
                        logger.info(f"Created Matrix user: @{username}:matrix.oculair.ca")
                        return True
                    elif response.status == 200:
                        logger.info(f"Matrix user already exists: @{username}:matrix.oculair.ca")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create user {username}: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error creating Matrix user {username}: {e}")
            return False
    
    def generate_username(self, agent_name: str, agent_id: str) -> str:
        """Generate a safe Matrix username from agent info"""
        # Clean agent name for Matrix username (only lowercase, numbers, hyphens, periods, underscores)
        import re
        clean_name = re.sub(r'[^a-zA-Z0-9\-\._]', '', agent_name.lower())
        if not clean_name:
            clean_name = f"agent-{agent_id[:8]}"
        
        # Ensure it starts with a letter
        if not clean_name[0].isalpha():
            clean_name = f"letta-{clean_name}"
        
        return clean_name
    
    def generate_password(self) -> str:
        """Generate a secure password for the Matrix user"""
        # Development override - use simple password if DEV_MODE is set
        if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
            return "password"
        
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(16))
    
    async def sync_agents_to_users(self):
        """Main function to sync Letta agents to Matrix users"""
        logger.info("Starting agent-to-user sync process")
        print("[AGENT_SYNC] Starting agent-to-user sync process", flush=True)
        
        # Load existing mappings
        await self.load_existing_mappings()
        print(f"[AGENT_SYNC] Loaded {len(self.mappings)} existing mappings", flush=True)
        
        # Get current Letta agents
        agents = await self.get_letta_agents()
        
        current_agent_ids = {agent["id"] for agent in agents}
        existing_agent_ids = set(self.mappings.keys())
        
        # Create users for new agents
        new_agents = current_agent_ids - existing_agent_ids
        for agent in agents:
            if agent["id"] in new_agents:
                await self.create_user_for_agent(agent)
        
        # Also check existing agents that haven't been successfully created or don't have rooms
        logger.info(f"Checking {len(existing_agent_ids)} existing agents for failed creation status or missing rooms")
        print(f"[AGENT_SYNC] Checking {len(existing_agent_ids)} existing agents for failed creation status or missing rooms", flush=True)
        for agent in agents:
            if agent["id"] in existing_agent_ids:
                mapping = self.mappings.get(agent["id"])
                logger.debug(f"Agent {agent['name']} - created: {mapping.created if mapping else 'No mapping'}, room: {mapping.room_created if mapping else 'No room'}")
                print(f"[AGENT_SYNC] Agent {agent['name']} - created: {mapping.created if mapping else 'No mapping'}, room: {mapping.room_created if mapping else 'No room'}", flush=True)
                
                if mapping:
                    # Retry user creation if failed
                    if not mapping.created:
                        logger.info(f"Retrying creation for existing agent {agent['name']} with failed status")
                        print(f"[AGENT_SYNC] Retrying creation for existing agent {agent['name']} with failed status", flush=True)
                        await self.create_user_for_agent(agent)
                    # Create room if user exists but room doesn't
                    elif mapping.created and not mapping.room_created:
                        logger.info(f"Creating room for existing agent {agent['name']}")
                        print(f"[AGENT_SYNC] Creating room for existing agent {agent['name']}", flush=True)
                        await self.create_or_update_agent_room(agent["id"])
                    # If room exists, ensure invitations are accepted
                    elif mapping.created and mapping.room_created and mapping.room_id:
                        logger.info(f"Ensuring invitations are accepted for room {mapping.room_id}")
                        await self.auto_accept_invitations(mapping.room_id)
        
        # TODO: Optionally handle removed agents (deactivate users?)
        removed_agents = existing_agent_ids - current_agent_ids
        if removed_agents:
            logger.info(f"Found {len(removed_agents)} agents that no longer exist: {removed_agents}")
        
        # Save updated mappings
        await self.save_mappings()
        
        # Ensure @admin:matrix.oculair.ca is invited to all rooms
        await self.invite_admin_to_existing_rooms()
        
        logger.info(f"Sync complete. Total mappings: {len(self.mappings)}")
    
    async def create_user_for_agent(self, agent: dict):
        """Create a Matrix user for a specific agent"""
        agent_id = agent["id"]
        agent_name = agent["name"]
        
        logger.info(f"Processing agent: {agent_name} ({agent_id})")
        
        # Check if we already have a complete mapping for this agent
        if agent_id in self.mappings:
            existing_mapping = self.mappings[agent_id]
            logger.info(f"Found existing mapping for agent {agent_name}")
            logger.info(f"  User: {existing_mapping.matrix_user_id}, Created: {existing_mapping.created}")
            logger.info(f"  Room: {existing_mapping.room_id}, Room Created: {existing_mapping.room_created}")
            
            # If both user and room exist, we're done
            if existing_mapping.created and existing_mapping.room_created and existing_mapping.room_id:
                logger.info(f"Agent {agent_name} already has user and room configured, skipping")
                return
            
            # If user exists but room doesn't, just create the room
            if existing_mapping.created and not existing_mapping.room_created:
                logger.info(f"User exists but room missing for agent {agent_name}, creating room only")
                await self.create_or_update_agent_room(agent_id)
                return
        
        # If we get here, we need to create the user (and then the room)
        logger.info(f"Creating Matrix user for agent: {agent_name} ({agent_id})")
        
        # Generate Matrix username
        username = self.generate_username(agent_name, agent_id)
        matrix_user_id = f"@{username}:matrix.oculair.ca"
        
        # Use existing password if we have one, otherwise generate new
        if agent_id in self.mappings and self.mappings[agent_id].matrix_password:
            password = self.mappings[agent_id].matrix_password
            logger.info(f"Using existing password for agent {agent_name}")
        else:
            password = self.generate_password()
            logger.info(f"Generated new password for agent {agent_name}")
        
        # Create the Matrix user
        success = await self.create_matrix_user(username, password, f"Letta Agent: {agent_name}")
        
        # Update or create the mapping
        if agent_id in self.mappings:
            self.mappings[agent_id].created = success
            self.mappings[agent_id].matrix_user_id = matrix_user_id
            self.mappings[agent_id].matrix_password = password
        else:
            mapping = AgentUserMapping(
                agent_id=agent_id,
                agent_name=agent_name,
                matrix_user_id=matrix_user_id,
                matrix_password=password,
                created=success,
                room_id=None,
                room_created=False
            )
            self.mappings[agent_id] = mapping
        
        if success:
            logger.info(f"Successfully created Matrix user {matrix_user_id} for agent {agent_name}")
            # Now create/update the room for this agent
            await self.create_or_update_agent_room(agent_id)
        else:
            logger.error(f"Failed to create Matrix user for agent {agent_name}")
    
    async def get_agent_user_mapping(self, agent_id: str) -> Optional[AgentUserMapping]:
        """Get the Matrix user mapping for a specific agent"""
        return self.mappings.get(agent_id)
    
    async def list_agent_users(self) -> List[AgentUserMapping]:
        """Get all agent-user mappings"""
        return list(self.mappings.values())
    
    async def check_room_exists(self, room_id: str) -> bool:
        """Check if a room exists on the server"""
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check room existence")
                return False
            
            # Use the room state API to check if room exists
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        logger.info(f"Room {room_id} exists")
                        return True
                    elif response.status == 404:
                        logger.info(f"Room {room_id} does not exist")
                        return False
                    elif response.status == 403:
                        # Room exists but we don't have access - still counts as existing
                        logger.info(f"Room {room_id} exists but access denied")
                        return True
                    else:
                        logger.warning(f"Unexpected response checking room {room_id}: {response.status}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error checking if room {room_id} exists: {e}")
            return False
    
    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        """Find an existing room for an agent by searching room names"""
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot search rooms")
                return None
            
            # Get list of rooms
            url = f"{self.homeserver_url}/_matrix/client/r0/joined_rooms"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Failed to get joined rooms: {response.status}")
                        return None
                    
                    data = await response.json()
                    room_ids = data.get("joined_rooms", [])
                    
                    # Check each room to see if it matches our agent
                    expected_name = f"{agent_name} - Letta Agent Chat"
                    for room_id in room_ids:
                        # Get room state to check name
                        state_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.name"
                        async with session.get(state_url, headers=headers) as state_response:
                            if state_response.status == 200:
                                state_data = await state_response.json()
                                room_name = state_data.get("name", "")
                                if room_name == expected_name:
                                    logger.info(f"Found existing room for agent {agent_name}: {room_id}")
                                    return room_id
                    
                    logger.info(f"No existing room found for agent {agent_name}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error searching for agent room: {e}")
            return None
    
    async def create_or_update_agent_room(self, agent_id: str):
        """Create or update a Matrix room for agent communication"""
        mapping = self.mappings.get(agent_id)
        if not mapping or not mapping.created:
            logger.error(f"Cannot create room for agent {agent_id} - user not created")
            return
        
        # Check if room already exists in our mapping and on the server
        if mapping.room_id and mapping.room_created:
            # Verify the room actually exists on the server
            room_exists = await self.check_room_exists(mapping.room_id)
            if room_exists:
                logger.info(f"Room already exists for agent {mapping.agent_name}: {mapping.room_id}")
                # Ensure invitations are accepted
                await self.auto_accept_invitations(mapping.room_id)
                return
            else:
                logger.warning(f"Room {mapping.room_id} in mapping doesn't exist on server, checking for existing rooms")
                # Clear the invalid room info
                mapping.room_id = None
                mapping.room_created = False
        
        # Check if a room already exists for this agent on the server
        existing_room_id = await self.find_existing_agent_room(mapping.agent_name)
        if existing_room_id:
            logger.info(f"Found existing room for agent {mapping.agent_name}: {existing_room_id}")
            mapping.room_id = existing_room_id
            mapping.room_created = True
            await self.save_mappings()
            # Ensure invitations are accepted
            await self.auto_accept_invitations(existing_room_id)
            return
        
        try:
            # First, we need to login as the agent user to create the room
            agent_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')
            
            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": mapping.matrix_password
            }
            
            # Login as the agent user
            async with aiohttp.ClientSession() as session:
                async with session.post(agent_login_url, json=login_data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to login as agent user {agent_username}: {response.status} - {error_text}")
                        return None
                    
                    agent_auth = await response.json()
                    agent_token = agent_auth.get("access_token")
                    
                    if not agent_token:
                        logger.error(f"No access token received for agent user {agent_username}")
                        return None
                
                # Now create the room as the agent user
                room_url = f"{self.homeserver_url}/_matrix/client/r0/createRoom"
                
                # Define the users to invite: admin users and main letta bot
                invites = [
                    "@admin:matrix.oculair.ca",  # Your actual admin account
                    self.admin_username,  # Admin user (matrixadmin) 
                    self.config.username  # Main Letta bot (@letta)
                ]
                
                room_data = {
                    "name": f"{mapping.agent_name} - Letta Agent Chat",
                    "topic": f"Private chat with Letta agent: {mapping.agent_name}",
                    "preset": "trusted_private_chat",  # Allows invited users to see history
                    "invite": invites,
                    "is_direct": False,
                    "initial_state": [
                        {
                            "type": "m.room.guest_access",
                            "state_key": "",
                            "content": {"guest_access": "forbidden"}
                        },
                        {
                            "type": "m.room.history_visibility",
                            "state_key": "",
                            "content": {"history_visibility": "shared"}
                        }
                    ]
                }
                
                headers = {
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json"
                }
                
                logger.info(f"Creating room as agent {agent_username} for {mapping.agent_name} with invites: {invites}")
                
                async with session.post(room_url, headers=headers, json=room_data) as response:
                    if response.status == 200:
                        data = await response.json()
                        room_id = data.get("room_id")
                        logger.info(f"Created room {room_id} for agent {mapping.agent_name}")
                        
                        # Update mapping with room info
                        mapping.room_id = room_id
                        mapping.room_created = True
                        
                        # Save updated mappings
                        await self.save_mappings()
                        
                        # Now auto-accept the invitations for admin and letta users
                        await self.auto_accept_invitations(room_id)
                        
                        return room_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create room for agent {mapping.agent_name}: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error creating room for agent {agent_id}: {e}")
            return None
    
    async def auto_accept_invitations(self, room_id: str):
        """Auto-accept room invitations for admin and letta users"""
        users_to_accept = [
            (self.admin_username, self.admin_password),
            (self.config.username, self.config.password)
        ]
        
        for username, password in users_to_accept:
            try:
                # Login as the user
                login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
                user_local = username.split(':')[0].replace('@', '')
                
                login_data = {
                    "type": "m.login.password",
                    "user": user_local,
                    "password": password
                }
                
                async with aiohttp.ClientSession() as session:
                    # Login
                    async with session.post(login_url, json=login_data) as response:
                        if response.status != 200:
                            logger.error(f"Failed to login as {username} to accept invitation")
                            continue
                        
                        auth_data = await response.json()
                        user_token = auth_data.get("access_token")
                        
                        if not user_token:
                            logger.error(f"No token received for {username}")
                            continue
                    
                    # Accept the invitation
                    join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
                    headers = {
                        "Authorization": f"Bearer {user_token}",
                        "Content-Type": "application/json"
                    }
                    
                    async with session.post(join_url, headers=headers, json={}) as response:
                        if response.status == 200:
                            logger.info(f"User {username} successfully joined room {room_id}")
                        else:
                            error_text = await response.text()
                            logger.warning(f"User {username} could not join room {room_id}: {response.status} - {error_text}")
                            
            except Exception as e:
                logger.error(f"Error accepting invitation for {username}: {e}")
    
    async def invite_admin_to_existing_rooms(self):
        """Invite @admin:matrix.oculair.ca to all existing agent rooms"""
        admin_to_invite = "@admin:matrix.oculair.ca"
        
        for agent_id, mapping in self.mappings.items():
            if mapping.room_id and mapping.room_created:
                try:
                    # Get admin token to invite users
                    admin_token = await self.get_admin_token()
                    if not admin_token:
                        logger.error("Cannot invite without admin token")
                        continue
                    
                    # Invite the admin user
                    invite_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{mapping.room_id}/invite"
                    headers = {
                        "Authorization": f"Bearer {admin_token}",
                        "Content-Type": "application/json"
                    }
                    
                    invite_data = {
                        "user_id": admin_to_invite
                    }
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.post(invite_url, headers=headers, json=invite_data) as response:
                            if response.status == 200:
                                logger.info(f"Successfully invited {admin_to_invite} to room {mapping.room_id} for agent {mapping.agent_name}")
                            else:
                                error_text = await response.text()
                                # Check if user is already in the room
                                if "already in the room" in error_text:
                                    logger.info(f"User {admin_to_invite} is already in room {mapping.room_id}")
                                else:
                                    logger.warning(f"Failed to invite {admin_to_invite} to room {mapping.room_id}: {response.status} - {error_text}")
                                    
                except Exception as e:
                    logger.error(f"Error inviting admin to room {mapping.room_id}: {e}")

async def run_agent_sync(config):
    """Run the agent sync process"""
    # Configure logger for this module with same level as main
    import sys
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, config.log_level.upper()))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, config.log_level.upper()))
    
    logger.info("Starting agent sync process from run_agent_sync")
    manager = AgentUserManager(config)
    await manager.sync_agents_to_users()
    return manager