#!/usr/bin/env python3
"""
Agent User Manager - Creates Matrix users for each Letta agent
"""
import asyncio
import logging
import os
import json
import aiohttp
import random
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
# Removed Letta client imports - now using OpenAI endpoint

logger = logging.getLogger("matrix_client.agent_user_manager")

# Global session with connection pooling for better performance
connector = aiohttp.TCPConnector(
    limit=100,  # Connection pool size
    limit_per_host=50,  # Per-host connection limit
    ttl_dns_cache=300,  # DNS cache timeout
    keepalive_timeout=30,  # Keep connections alive
    force_close=False
)
global_session = None

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)

async def get_global_session():
    """Get or create global aiohttp session with connection pooling"""
    global global_session
    if global_session is None or global_session.closed:
        global_session = aiohttp.ClientSession(
            connector=connector
            # Timeout will be set per-request to avoid the context manager error
        )
    return global_session

@dataclass
class AgentUserMapping:
    agent_id: str
    agent_name: str
    matrix_user_id: str
    matrix_password: str
    created: bool = False
    room_id: Optional[str] = None
    room_created: bool = False
    invitation_status: Optional[Dict[str, str]] = None  # user_id -> "invited"|"joined"|"failed"

class AgentUserManager:
    """Manages Matrix users for Letta agents"""

    def __init__(self, config):
        self.config = config
        self.matrix_api_url = config.matrix_api_url if hasattr(config, 'matrix_api_url') else "http://matrix-api:8000"
        self.homeserver_url = config.homeserver_url
        self.letta_token = config.letta_token
        self.letta_api_url = config.letta_api_url
        self.mappings_file = "/app/data/agent_user_mappings.json"
        self.space_config_file = "/app/data/letta_space_config.json"
        self.mappings: Dict[str, AgentUserMapping] = {}
        self.admin_token = None  # Will be obtained programmatically
        self.space_id: Optional[str] = None  # Letta Agents space ID

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
                        # Handle backward compatibility for new invitation_status field
                        if "invitation_status" not in mapping_data:
                            mapping_data["invitation_status"] = None
                        self.mappings[agent_id] = AgentUserMapping(**mapping_data)
                logger.info(f"Loaded {len(self.mappings)} existing agent-user mappings")
            else:
                logger.info("No existing mappings file found")
        except Exception as e:
            logger.error(f"Error loading mappings: {e}")

    async def load_space_config(self):
        """Load the Letta Agents space configuration"""
        try:
            if os.path.exists(self.space_config_file):
                with open(self.space_config_file, 'r') as f:
                    data = json.load(f)
                    self.space_id = data.get("space_id")
                    logger.info(f"Loaded space configuration: {self.space_id}")
            else:
                logger.info("No existing space configuration found")
        except Exception as e:
            logger.error(f"Error loading space config: {e}")

    async def save_space_config(self):
        """Save the Letta Agents space configuration"""
        try:
            data = {
                "space_id": self.space_id,
                "created_at": time.time(),
                "name": "Letta Agents"
            }
            with open(self.space_config_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved space configuration: {self.space_id}")
        except Exception as e:
            logger.error(f"Error saving space config: {e}")
    
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
                    "room_created": mapping.room_created,
                    "invitation_status": mapping.invitation_status
                }
            
            with open(self.mappings_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(self.mappings)} agent-user mappings")
        except Exception as e:
            logger.error(f"Error saving mappings: {e}")
    
    async def get_letta_agents(self) -> List[dict]:
        """Get all Letta agents from agents endpoint"""
        try:
            # Use proper agents endpoint (port 1416 for models which represent agents)
            agents_endpoint = "http://192.168.50.90:1416/v1/models"
            
            # Set up authentication headers
            headers = {
                "Authorization": "Bearer lettaSecurePass123",
                "Content-Type": "application/json"
            }
            
            # Create a fresh session to avoid timeout context errors
            async with aiohttp.ClientSession() as session:
                async with session.get(agents_endpoint, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status != 200:
                        logger.error(f"Failed to get agents from agents endpoint: {response.status}")
                        return []
                    
                    agents_data = await response.json()
                    
                    agent_list = []
                    # Handle /v1/models response format with data array
                    agents_array = agents_data.get("data", []) if isinstance(agents_data, dict) else agents_data
                    for agent in agents_array:
                        agent_id = agent.get("id", "")
                        agent_name = agent.get("name", agent_id)
                        
                        if agent_id:
                            agent_list.append({
                                "id": agent_id,
                                "name": agent_name
                            })
                    
                    logger.info(f"Found {len(agent_list)} Letta agents from agents endpoint")
                    return agent_list
            
        except Exception as e:
            logger.error(f"Error getting Letta agents from agents endpoint: {e}")
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
                async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
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
                async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
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
                async with session.put(url, headers=headers, json=data, timeout=DEFAULT_TIMEOUT) as response:
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

    async def create_letta_agents_space(self) -> Optional[str]:
        """Create the Letta Agents space if it doesn't exist"""
        try:
            # Check if we already have a space
            if self.space_id:
                # Verify it still exists
                exists = await self.check_room_exists(self.space_id)
                if exists:
                    logger.info(f"Letta Agents space already exists: {self.space_id}")
                    return self.space_id
                else:
                    logger.warning(f"Stored space {self.space_id} doesn't exist, creating new one")
                    self.space_id = None

            # Login as admin to create the space
            admin_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            admin_username_local = self.admin_username.split(':')[0].replace('@', '')

            login_data = {
                "type": "m.login.password",
                "user": admin_username_local,
                "password": self.admin_password
            }

            async with aiohttp.ClientSession() as session:
                # Login
                async with session.post(admin_login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to login as admin to create space: {response.status} - {error_text}")
                        return None

                    auth_data = await response.json()
                    admin_token = auth_data.get("access_token")

                if not admin_token:
                    logger.error("No token received for admin user")
                    return None

                # Create the space
                space_url = f"{self.homeserver_url}/_matrix/client/r0/createRoom"

                # Invite key users to the space
                invites = [
                    "@admin:matrix.oculair.ca",
                    self.config.username  # Main Letta bot
                ]

                space_data = {
                    "name": "Letta Agents",
                    "topic": "All Letta AI agents - organized by the Letta Matrix bridge",
                    "preset": "private_chat",
                    "invite": invites,
                    "power_level_content_override": {
                        "events": {
                            "m.space.child": 50  # Allow room moderators to add children
                        }
                    },
                    "creation_content": {
                        "type": "m.space"
                    },
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
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json"
                }

                logger.info("Creating Letta Agents space")
                async with session.post(space_url, headers=headers, json=space_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        space_id = data.get("room_id")
                        logger.info(f"Created Letta Agents space: {space_id}")

                        # Store the space ID
                        self.space_id = space_id
                        await self.save_space_config()

                        return space_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create space: {response.status} - {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error creating Letta Agents space: {e}")
            return None

    async def add_room_to_space(self, room_id: str, room_name: str) -> bool:
        """Add a room as a child of the Letta Agents space"""
        try:
            if not self.space_id:
                logger.warning("No space ID available, cannot add room to space")
                return False

            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot add room to space")
                return False

            # Add the room as a child of the space
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{self.space_id}/state/m.space.child/{room_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            child_data = {
                "via": ["matrix.oculair.ca"],
                "suggested": True,
                "order": room_name  # Use room name for alphabetical ordering
            }

            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=child_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Added room {room_id} ({room_name}) to Letta Agents space")

                        # Also add the space as a parent of the room (bidirectional relationship)
                        parent_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.space.parent/{self.space_id}"
                        parent_data = {
                            "via": ["matrix.oculair.ca"],
                            "canonical": True
                        }

                        async with session.put(parent_url, headers=headers, json=parent_data, timeout=DEFAULT_TIMEOUT) as parent_response:
                            if parent_response.status == 200:
                                logger.info(f"Set space as parent of room {room_id}")
                            else:
                                logger.warning(f"Failed to set space as parent: {parent_response.status}")

                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to add room to space: {response.status} - {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error adding room {room_id} to space: {e}")
            return False

    async def migrate_existing_rooms_to_space(self) -> int:
        """Migrate all existing agent rooms to the Letta Agents space"""
        if not self.space_id:
            logger.warning("No space ID available, cannot migrate rooms")
            return 0

        migrated_count = 0
        for agent_id, mapping in self.mappings.items():
            if mapping.room_id and mapping.room_created:
                logger.info(f"Migrating room for agent {mapping.agent_name} to space")
                success = await self.add_room_to_space(mapping.room_id, mapping.agent_name)
                if success:
                    migrated_count += 1
                    logger.info(f"Successfully migrated room for {mapping.agent_name}")
                else:
                    logger.warning(f"Failed to migrate room for {mapping.agent_name}")

        logger.info(f"Migrated {migrated_count} existing rooms to space")
        return migrated_count

    def get_space_id(self) -> Optional[str]:
        """Get the current Letta Agents space ID"""
        return self.space_id
    
    def generate_username(self, agent_name: str, agent_id: str) -> str:
        """Generate a safe Matrix username from agent ID"""
        # Use the agent ID as the base for the username
        # This ensures the username is stable even if the agent is renamed
        # Format: agent-{uuid} -> agent_{uuid with underscores}
        import re
        
        # Remove 'agent-' prefix if present and replace hyphens with underscores
        if agent_id.startswith("agent-"):
            clean_id = agent_id[6:]  # Remove 'agent-' prefix
        else:
            clean_id = agent_id
            
        # Replace hyphens with underscores for Matrix compatibility
        clean_id = clean_id.replace('-', '_')
        
        # Ensure it only contains valid characters
        clean_id = re.sub(r'[^a-zA-Z0-9_]', '', clean_id)
        
        # Create username as 'agent_{id}'
        username = f"agent_{clean_id}"
        
        return username
    
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

        # Load existing mappings and space config
        await self.load_existing_mappings()
        await self.load_space_config()
        print(f"[AGENT_SYNC] Loaded {len(self.mappings)} existing mappings", flush=True)

        # Ensure the Letta Agents space exists
        space_just_created = False
        if not self.space_id:
            logger.info("Creating Letta Agents space")
            print("[AGENT_SYNC] Creating Letta Agents space", flush=True)
            space_id = await self.create_letta_agents_space()
            if space_id:
                logger.info(f"Successfully created Letta Agents space: {space_id}")
                print(f"[AGENT_SYNC] Successfully created Letta Agents space: {space_id}", flush=True)
                space_just_created = True
            else:
                logger.warning("Failed to create Letta Agents space, rooms will not be organized")
                print("[AGENT_SYNC] Failed to create Letta Agents space, rooms will not be organized", flush=True)
        else:
            logger.info(f"Using existing Letta Agents space: {self.space_id}")
            print(f"[AGENT_SYNC] Using existing Letta Agents space: {self.space_id}", flush=True)

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
                    # Check if agent name has changed
                    if mapping.agent_name != agent['name']:
                        logger.info(f"Agent name changed from '{mapping.agent_name}' to '{agent['name']}'")
                        print(f"[AGENT_SYNC] Agent name changed from '{mapping.agent_name}' to '{agent['name']}'", flush=True)
                        
                        # Update the stored agent name
                        old_name = mapping.agent_name
                        mapping.agent_name = agent['name']
                        
                        # Update room name if room exists
                        if mapping.room_id and mapping.room_created:
                            logger.info(f"Updating room name for {mapping.room_id}")
                            success = await self.update_room_name(mapping.room_id, agent['name'])
                            if success:
                                print(f"[AGENT_SYNC] Successfully updated room name from '{old_name}' to '{agent['name']}'", flush=True)
                            else:
                                print(f"[AGENT_SYNC] Failed to update room name", flush=True)
                        
                        # Update display name for the Matrix user
                        if mapping.matrix_user_id:
                            logger.info(f"Updating display name for {mapping.matrix_user_id}")
                            display_success = await self.update_display_name(mapping.matrix_user_id, agent['name'])
                            if display_success:
                                print(f"[AGENT_SYNC] Successfully updated display name for '{mapping.matrix_user_id}' to '{agent['name']}'", flush=True)
                            else:
                                print(f"[AGENT_SYNC] Failed to update display name", flush=True)
                    
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
                        logger.info(f"Skipping invitation process for room {mapping.room_id} (temporarily disabled)")
                        # await self.auto_accept_invitations(mapping.room_id)
        
        # TODO: Optionally handle removed agents (deactivate users?)
        removed_agents = existing_agent_ids - current_agent_ids
        if removed_agents:
            logger.info(f"Found {len(removed_agents)} agents that no longer exist: {removed_agents}")
        
        # Save updated mappings
        await self.save_mappings()

        # If space was just created, migrate all existing rooms to it
        if space_just_created and self.space_id:
            logger.info("Migrating existing agent rooms to the new space")
            print("[AGENT_SYNC] Migrating existing agent rooms to the new space", flush=True)
            migrated = await self.migrate_existing_rooms_to_space()
            logger.info(f"Migrated {migrated} rooms to space")
            print(f"[AGENT_SYNC] Migrated {migrated} rooms to space", flush=True)

        # Temporarily disabled to prevent blocking message processing
        # TODO: Fix permission issues before re-enabling
        # await self.invite_admin_to_existing_rooms()

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
            
            # Set the display name to the agent name
            display_success = await self.update_display_name(matrix_user_id, agent_name)
            if display_success:
                logger.info(f"Successfully set display name to '{agent_name}' for {matrix_user_id}")
            else:
                logger.warning(f"Failed to set display name for {matrix_user_id}")
            
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
                async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
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
    
    async def update_room_name(self, room_id: str, new_name: str) -> bool:
        """Update the name of an existing room"""
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot update room name")
                return False
            
            # Use the room state API to update room name
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.name"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            room_name_data = {
                "name": f"{new_name} - Letta Agent Chat"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=room_name_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Successfully updated room name for {room_id} to '{new_name} - Letta Agent Chat'")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to update room name: {response.status} - {error_text}")
                        return False
                    
        except Exception as e:
            logger.error(f"Error updating room name for {room_id}: {e}")
            return False
    
    async def update_display_name(self, user_id: str, display_name: str) -> bool:
        """Update the display name of a Matrix user"""
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot update display name")
                return False
            
            # Use the profile API to update display name
            url = f"{self.homeserver_url}/_matrix/client/r0/profile/{user_id}/displayname"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            display_name_data = {
                "displayname": display_name
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=display_name_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Successfully updated display name for {user_id} to '{display_name}'")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to update display name: {response.status} - {error_text}")
                        return False
                    
        except Exception as e:
            logger.error(f"Error updating display name for {user_id}: {e}")
            return False
    
    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        """Find an existing room for an agent by searching room names"""
        # TEMPORARY: Always return None to force creation of new rooms
        return None
        
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
                async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
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
                
                # Now create the room as the agent user (inside the session)
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
                
                async with session.post(room_url, headers=headers, json=room_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        room_id = data.get("room_id")
                        logger.info(f"Created room {room_id} for agent {mapping.agent_name}")

                        # Update mapping with room info
                        mapping.room_id = room_id
                        mapping.room_created = True

                        # Initialize invitation status tracking
                        mapping.invitation_status = {user_id: "invited" for user_id in invites}

                        # Save updated mappings
                        await self.save_mappings()

                        # Add the room to the Letta Agents space
                        if self.space_id:
                            logger.info(f"Adding room {room_id} to Letta Agents space")
                            space_success = await self.add_room_to_space(room_id, mapping.agent_name)
                            if space_success:
                                logger.info(f"Successfully added room to space")
                            else:
                                logger.warning(f"Failed to add room to space")

                        # Now auto-accept the invitations for admin and letta users
                        await self.auto_accept_invitations_with_tracking(room_id, mapping)

                        return room_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create room for agent {mapping.agent_name}: {response.status} - {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"Error creating room for agent {agent_id}: {e}")
            return None
    
    async def auto_accept_invitations_with_tracking(self, room_id: str, mapping: AgentUserMapping):
        """Auto-accept room invitations for admin and letta users with status tracking"""
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
                    async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status != 200:
                            logger.error(f"Failed to login as {username} to accept invitation")
                            if mapping.invitation_status:
                                mapping.invitation_status[username] = "failed"
                            continue
                        
                        auth_data = await response.json()
                        user_token = auth_data.get("access_token")
                    
                    if not user_token:
                        logger.error(f"No token received for {username}")
                        if mapping.invitation_status:
                            mapping.invitation_status[username] = "failed"
                        continue
                    
                    # Accept the invitation
                    join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
                    headers = {
                        "Authorization": f"Bearer {user_token}",
                        "Content-Type": "application/json"
                    }
                    
                    async with session.post(join_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status == 200:
                            logger.info(f"User {username} successfully joined room {room_id}")
                            if mapping.invitation_status:
                                mapping.invitation_status[username] = "joined"
                        elif response.status == 403:
                            error_text = await response.text()
                            if "already in the room" in error_text or "already joined" in error_text:
                                logger.info(f"User {username} is already in room {room_id}")
                                if mapping.invitation_status:
                                    mapping.invitation_status[username] = "joined"
                            else:
                                logger.warning(f"User {username} forbidden from joining room {room_id}: {error_text}")
                                if mapping.invitation_status:
                                    mapping.invitation_status[username] = "failed"
                        else:
                            error_text = await response.text()
                            logger.warning(f"User {username} could not join room {room_id}: {response.status} - {error_text}")
                            if mapping.invitation_status:
                                mapping.invitation_status[username] = "failed"
                            
            except Exception as e:
                logger.error(f"Error accepting invitation for {username}: {e}")
                if mapping.invitation_status:
                    mapping.invitation_status[username] = "failed"
        
        # Save updated invitation status
        await self.save_mappings()
    
    # Removed problematic invitation functions that caused endless loops
    # The agent-based invitation system in room creation is sufficient

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