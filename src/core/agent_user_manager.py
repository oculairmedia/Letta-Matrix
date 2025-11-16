#!/usr/bin/env python3
"""
Agent User Manager - Creates Matrix users for each Letta agent
"""
import asyncio
import logging
import os
import json
import time
import aiohttp
import random
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
# Removed Letta client imports - now using OpenAI endpoint

from .space_manager import MatrixSpaceManager
from .user_manager import MatrixUserManager
from .room_manager import MatrixRoomManager

logger = logging.getLogger("matrix_client.agent_user_manager")

# Global session with connection pooling for better performance
_connector = None
global_session = None

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)

def _get_connector():
    """Lazily create connector to avoid event loop issues at import time"""
    global _connector
    if _connector is None:
        _connector = aiohttp.TCPConnector(
            limit=100,  # Connection pool size
            limit_per_host=50,  # Per-host connection limit
            ttl_dns_cache=300,  # DNS cache timeout
            keepalive_timeout=30,  # Keep connections alive
            force_close=False
        )
    return _connector

async def get_global_session():
    """Get or create global aiohttp session with connection pooling"""
    global global_session
    if global_session is None or global_session.closed:
        connector = _get_connector()
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
        
        # Configure data directory - use env var or default to /app/data
        self.data_dir = os.getenv("MATRIX_DATA_DIR", "/app/data")
        self.mappings_file = os.path.join(self.data_dir, "agent_user_mappings.json")
        self.mappings: Dict[str, AgentUserMapping] = {}
        # Note: admin_token is now a property that proxies to user_manager.admin_token

        # Matrix admin credentials - try to use a dedicated admin account
        # Fall back to main letta user if not specified
        self.admin_username = os.getenv("MATRIX_ADMIN_USERNAME", config.username)
        self.admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", config.password)
        logger.info(f"Using admin account: {self.admin_username} (from env: {os.getenv('MATRIX_ADMIN_USERNAME')})")

        # Ensure data directory exists
        os.makedirs(self.data_dir, exist_ok=True)

        # Initialize Matrix Space Manager
        self.space_manager = MatrixSpaceManager(
            homeserver_url=self.homeserver_url,
            admin_username=self.admin_username,
            admin_password=self.admin_password,
            main_bot_username=config.username,
            space_config_file=os.path.join(self.data_dir, "letta_space_config.json")
        )

        # Initialize Matrix User Manager
        self.user_manager = MatrixUserManager(
            homeserver_url=self.homeserver_url,
            admin_username=self.admin_username,
            admin_password=self.admin_password
        )

        # Initialize Matrix Room Manager
        self.room_manager = MatrixRoomManager(
            homeserver_url=self.homeserver_url,
            space_manager=self.space_manager,
            user_manager=self.user_manager,
            config=config,
            admin_username=self.admin_username,
            get_admin_token_callback=self.get_admin_token,
            save_mappings_callback=self.save_mappings
        )

    @property
    def admin_token(self) -> Optional[str]:
        """Backward compatibility property for admin_token"""
        return self.user_manager.admin_token

    @admin_token.setter
    def admin_token(self, value: Optional[str]):
        """Backward compatibility setter for admin_token"""
        self.user_manager.admin_token = value

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
        """Get all Letta agents from agents endpoint with pagination support"""
        try:
            # Use Letta proxy endpoint (port 8289) as recommended
            # The proxy provides better stability and performance improvements
            base_endpoint = "http://192.168.50.90:8289/v1/agents"

            # Set up authentication headers
            headers = {
                "Authorization": "Bearer lettaSecurePass123",
                "Content-Type": "application/json"
            }

            agent_list = []
            seen_agent_ids = set()
            after_cursor = None
            page_count = 0
            max_pages = 10  # Safety limit to prevent infinite loops (56 agents / 50 per page = ~2 pages needed)
            last_cursor = None  # Track if cursor is changing

            # Create a fresh session to avoid timeout context errors
            async with aiohttp.ClientSession() as session:
                while page_count < max_pages:
                    page_count += 1
                    # Build URL with pagination cursor if available
                    if after_cursor:
                        agents_endpoint = f"{base_endpoint}?after={after_cursor}&limit=100"
                    else:
                        agents_endpoint = f"{base_endpoint}?limit=100"

                    logger.info(f"Fetching agents page {page_count} from: {agents_endpoint}")

                    async with session.get(agents_endpoint, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status != 200:
                            error_body = await response.text()
                            logger.error(f"Failed to get agents from agents endpoint: {response.status} - {error_body[:500]}")
                            break

                        agents_data = await response.json()

                        # Handle /v1/agents response format (returns array directly)
                        agents_array = agents_data.get("data", []) if isinstance(agents_data, dict) else agents_data

                        if not agents_array:
                            logger.info(f"No more agents found on page {page_count}, ending pagination")
                            break

                        logger.info(f"Page {page_count}: Received {len(agents_array)} agents from API")

                        # Track new agents added this page
                        new_agents_this_page = 0
                        first_agent_id = None
                        last_agent_id = None

                        for agent in agents_array:
                            agent_id = agent.get("id", "")
                            agent_name = agent.get("name", agent_id)

                            if not first_agent_id:
                                first_agent_id = agent_id
                            last_agent_id = agent_id

                            if agent_id and agent_id not in seen_agent_ids:
                                seen_agent_ids.add(agent_id)
                                agent_list.append({
                                    "id": agent_id,
                                    "name": agent_name
                                })
                                new_agents_this_page += 1

                        logger.info(f"Page {page_count}: Added {new_agents_this_page} new unique agents (total so far: {len(agent_list)})")
                        logger.debug(f"Page {page_count}: First agent: {first_agent_id}, Last agent: {last_agent_id}")

                        # If we got less than 50 agents, this is the last page
                        if len(agents_array) < 50:
                            logger.info(f"Page {page_count} has {len(agents_array)} agents (less than 50), this is the last page")
                            break

                        # If cursor hasn't changed, we're in an infinite loop
                        if last_cursor == last_agent_id:
                            logger.warning(f"Cursor hasn't changed from {last_cursor}, stopping to prevent infinite loop")
                            break

                        # If no new agents were found and we got a full page, we might be seeing duplicates
                        if new_agents_this_page == 0 and len(agents_array) >= 50:
                            logger.warning(f"No new agents on page {page_count} but got full page - possible API pagination issue")
                            break

                        # Use the last agent ID as the cursor for the next page
                        after_cursor = last_agent_id
                        last_cursor = last_agent_id

                        if not after_cursor:
                            logger.warning("No ID found for last agent, stopping pagination")
                            break

                        logger.info(f"Next page will use cursor: {after_cursor}")

                logger.info(f"Found {len(agent_list)} Letta agents across {page_count} pages from agents endpoint")
                return agent_list

        except Exception as e:
            logger.error(f"Error getting Letta agents from agents endpoint: {e}")
            return []
    async def get_admin_token(self) -> Optional[str]:
        """Get an admin access token - delegates to user_manager"""
        return await self.user_manager.get_admin_token()

    async def check_user_exists(self, username: str) -> bool:
        """Check if a Matrix user exists - delegates to user_manager"""
        return await self.user_manager.check_user_exists(username)
    async def create_matrix_user(self, username: str, password: str, display_name: str) -> bool:
        """Create a new Matrix user - delegates to user_manager"""
        return await self.user_manager.create_matrix_user(username, password, display_name)

    async def set_user_display_name(self, user_id: str, display_name: str, access_token: str) -> bool:
        """Set display name for a user - delegates to user_manager"""
        return await self.user_manager.set_user_display_name(user_id, display_name, access_token)

    def generate_username(self, agent_name: str, agent_id: str) -> str:
        """Generate a safe Matrix username - delegates to user_manager"""
        return self.user_manager.generate_username(agent_name, agent_id)

    def generate_password(self) -> str:
        """Generate a secure password - delegates to user_manager"""
        return self.user_manager.generate_password()

    async def ensure_core_users_exist(self):
        """Ensure required core Matrix users exist - delegates to user_manager"""
        core_users = []

        # Main Letta bot user
        if getattr(self.config, "username", None) and getattr(self.config, "password", None):
            core_users.append(
                (self.config.username, self.config.password, "Letta Bot")
            )

        # Matrix admin user
        if self.admin_username and self.admin_password:
            core_users.append(
                (self.admin_username, self.admin_password, "Matrix Admin")
            )

        # Optional MCP bot user
        mcp_username = os.getenv("MATRIX_MCP_USERNAME")
        mcp_password = os.getenv("MATRIX_MCP_PASSWORD")
        if mcp_username and mcp_password:
            core_users.append(
                (mcp_username, mcp_password, "Matrix MCP Bot")
            )

        await self.user_manager.ensure_core_users_exist(core_users)

    async def sync_agents_to_users(self):
        """Main function to sync Letta agents to Matrix users"""
        logger.info("Starting agent-to-user sync process")
        print("[AGENT_SYNC] Starting agent-to-user sync process", flush=True)

        # Ensure core users exist before syncing agents and rooms
        await self.ensure_core_users_exist()


        # Load existing mappings and space config
        await self.load_existing_mappings()
        await self.space_manager.load_space_config()
        print(f"[AGENT_SYNC] Loaded {len(self.mappings)} existing mappings", flush=True)

        # Ensure the Letta Agents space exists
        space_just_created = False
        if not self.space_manager.get_space_id():
            logger.info("Creating Letta Agents space")
            print("[AGENT_SYNC] Creating Letta Agents space", flush=True)
            space_id = await self.space_manager.create_letta_agents_space()
            if space_id:
                logger.info(f"Successfully created Letta Agents space: {space_id}")
                print(f"[AGENT_SYNC] Successfully created Letta Agents space: {space_id}", flush=True)
                space_just_created = True
            else:
                logger.warning("Failed to create Letta Agents space, rooms will not be organized")
                print("[AGENT_SYNC] Failed to create Letta Agents space, rooms will not be organized", flush=True)
        else:
            logger.info(f"Using existing Letta Agents space: {self.space_manager.get_space_id()}")
            print(f"[AGENT_SYNC] Using existing Letta Agents space: {self.space_manager.get_space_id()}", flush=True)

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
                        logger.info(f"Ensuring invitations are accepted for room {mapping.room_id}")
                        await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
        # TODO: Optionally handle removed agents (deactivate users?)
        removed_agents = existing_agent_ids - current_agent_ids
        if removed_agents:
            logger.info(f"Found {len(removed_agents)} agents that no longer exist: {removed_agents}")

        # Save updated mappings
        await self.save_mappings()

        # If space was just created, migrate all existing rooms to it
        if space_just_created and self.space_manager.get_space_id():
            logger.info("Migrating existing agent rooms to the new space")
            print("[AGENT_SYNC] Migrating existing agent rooms to the new space", flush=True)
            migrated = await self.space_manager.migrate_existing_rooms_to_space(self.mappings)
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
        """Check if a room exists on the server

        Delegates to space_manager.check_room_exists to avoid code duplication
        """
        return await self.space_manager.check_room_exists(room_id)

    async def update_room_name(self, room_id: str, new_name: str) -> bool:
        """Update the name of an existing room - delegates to room_manager"""
        return await self.room_manager.update_room_name(room_id, new_name)

    async def update_display_name(self, user_id: str, display_name: str) -> bool:
        """Update the display name of a Matrix user - delegates to user_manager"""
        return await self.user_manager.update_display_name(user_id, display_name)

    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        """Find an existing room for an agent by searching room names - delegates to room_manager"""
        return await self.room_manager.find_existing_agent_room(agent_name)

    async def create_or_update_agent_room(self, agent_id: str):
        """Create or update a Matrix room for agent communication - delegates to room_manager"""
        mapping = self.mappings.get(agent_id)
        return await self.room_manager.create_or_update_agent_room(agent_id, mapping)

    async def import_recent_history(
        self,
        agent_id: str,
        agent_username: str,
        agent_password: str,
        room_id: str,
        limit: int = 15
    ):
        """Import recent Letta conversation history for UI continuity - delegates to room_manager"""
        return await self.room_manager.import_recent_history(
            agent_id=agent_id,
            agent_username=agent_username,
            agent_password=agent_password,
            room_id=room_id,
            limit=limit
        )

    async def auto_accept_invitations_with_tracking(self, room_id: str, mapping: AgentUserMapping):
        """Auto-accept room invitations for admin and letta users with status tracking - delegates to room_manager"""
        return await self.room_manager.auto_accept_invitations_with_tracking(room_id, mapping)

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