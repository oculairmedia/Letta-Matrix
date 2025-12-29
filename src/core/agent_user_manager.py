#!/usr/bin/env python3
"""
Agent User Manager - Creates Matrix users for each Letta agent
"""
import asyncio
import logging
import os
import time
import aiohttp
import random
from typing import Dict, List, Optional, Set

from .space_manager import MatrixSpaceManager
from .user_manager import MatrixUserManager
from .room_manager import MatrixRoomManager
from .types import AgentUserMapping

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
        """Load existing agent-user mappings from database"""
        try:
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            db_mappings = db.get_all()

            for db_mapping in db_mappings:
                mapping_dict = db_mapping.to_dict()
                # Handle backward compatibility for new invitation_status field
                if "invitation_status" not in mapping_dict:
                    mapping_dict["invitation_status"] = None
                agent_id = str(db_mapping.agent_id)
                self.mappings[agent_id] = AgentUserMapping(**mapping_dict)

            logger.info(f"Loaded {len(self.mappings)} existing agent-user mappings from database")
        except Exception as e:
            logger.error(f"Error loading mappings from database: {e}")
            # Database is the single source of truth - no JSON fallback
            logger.warning("Database is unavailable. Agent mappings will be empty until DB is restored.")


    async def save_mappings(self):
        """Save agent-user mappings to database"""
        try:
            from src.models.agent_mapping import AgentMappingDB, get_session_maker
            from src.models.agent_mapping import AgentMapping as DBAgentMapping, InvitationStatus
            from sqlalchemy.dialects.postgresql import insert

            Session = get_session_maker()
            session = Session()

            try:
                for agent_id, mapping in self.mappings.items():
                    # Upsert agent mapping (insert or update)
                    stmt = insert(DBAgentMapping).values(
                        agent_id=mapping.agent_id,
                        agent_name=mapping.agent_name,
                        matrix_user_id=mapping.matrix_user_id,
                        matrix_password=mapping.matrix_password,
                        room_id=mapping.room_id,
                        room_created=mapping.room_created
                    ).on_conflict_do_update(
                        index_elements=['agent_id'],
                        set_={
                            'agent_name': mapping.agent_name,
                            'room_id': mapping.room_id,
                            'room_created': mapping.room_created
                        }
                    )
                    session.execute(stmt)

                    # Update invitation statuses
                    if mapping.invitation_status:
                        for invitee, status in mapping.invitation_status.items():
                            stmt = insert(InvitationStatus).values(
                                agent_id=agent_id,
                                invitee=invitee,
                                status=status
                            ).on_conflict_do_update(
                                index_elements=['agent_id', 'invitee'],
                                set_={'status': status}
                            )
                            session.execute(stmt)

                session.commit()
                logger.info(f"Saved {len(self.mappings)} agent-user mappings to database")

            except Exception as db_error:
                session.rollback()
                raise db_error
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error saving mappings to database: {e}")
            # Database is the single source of truth - no JSON fallback
            logger.warning("Failed to save mappings. Changes will be lost if not retried.")

    async def get_letta_agents(self) -> List[dict]:
        """Get all Letta agents using the Letta SDK with pagination support"""
        try:
            from src.letta.client import get_letta_client, LettaConfig
            from concurrent.futures import ThreadPoolExecutor
            import asyncio

            # Configure SDK client
            sdk_config = LettaConfig(
                base_url="http://192.168.50.90:8289",  # Use Letta proxy endpoint
                api_key="lettaSecurePass123",
                timeout=30.0,
                max_retries=3
            )
            client = get_letta_client(sdk_config)

            agent_list = []
            seen_agent_ids = set()
            
            # Run sync SDK call in thread pool (SDK is synchronous)
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as executor:
                # SDK handles pagination internally - just request a high limit
                agents = await loop.run_in_executor(
                    executor,
                    lambda: list(client.agents.list(limit=500))  # Get all agents
                )

            logger.info(f"Retrieved {len(agents)} agents from SDK")

            for agent in agents:
                # SDK returns AgentState objects with id and name attributes
                agent_id = str(agent.id) if agent.id else ""
                agent_name = str(agent.name) if agent.name else agent_id

                if agent_id and agent_id not in seen_agent_ids:
                    seen_agent_ids.add(agent_id)
                    agent_list.append({
                        "id": agent_id,
                        "name": agent_name
                    })

            logger.info(f"Found {len(agent_list)} unique Letta agents via SDK")
            return agent_list

        except Exception as e:
            logger.error(f"Error getting Letta agents via SDK: {e}")
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
        import time
        sync_start = time.time()
        
        logger.info("Starting agent-to-user sync process")
        
        # Track metrics for this sync cycle
        sync_metrics = {
            "cache_hits": 0,
            "api_checks": 0,
            "login_attempts": 0,
            "rooms_processed": 0
        }

        # Ensure core users exist before syncing agents and rooms
        await self.ensure_core_users_exist()


        # Load existing mappings and space config
        await self.load_existing_mappings()
        await self.space_manager.load_space_config()

        # Ensure the Letta Agents space exists
        space_just_created = False
        if not self.space_manager.get_space_id():
            logger.info("Creating Letta Agents space")
            space_id = await self.space_manager.create_letta_agents_space()
            if space_id:
                logger.info(f"Successfully created Letta Agents space: {space_id}")
                space_just_created = True
            else:
                logger.warning("Failed to create Letta Agents space, rooms will not be organized")
        else:
            # Validate existing space
            existing_space_id = self.space_manager.get_space_id()
            if existing_space_id:
                logger.info(f"Validating existing Letta Agents space: {existing_space_id}")
                
                space_valid = await self.space_manager.check_room_exists(existing_space_id)
                if not space_valid:
                    logger.warning(f"Space {existing_space_id} is invalid, will recreate")
                    
                    # Clear the invalid space ID but don't save yet
                    old_space_id = existing_space_id
                    self.space_manager.space_id = None
                    
                    # Create a new space
                    space_id = await self.space_manager.create_letta_agents_space()
                    if space_id:
                        logger.info(f"Successfully recreated Letta Agents space: {space_id}")
                        
                        # Validate the new space works before proceeding
                        new_space_valid = await self.space_manager.check_room_exists(space_id)
                        if new_space_valid:
                            logger.info(f"New space {space_id} validated successfully")
                            space_just_created = True
                        else:
                            logger.error(f"New space {space_id} failed validation, keeping old space config")
                            # Restore old space ID to prevent recreation loop
                            self.space_manager.space_id = old_space_id
                            await self.space_manager.save_space_config()
                    else:
                        logger.error("Failed to recreate Letta Agents space")
                        # Restore old space ID to prevent recreation loop
                        self.space_manager.space_id = old_space_id
                        await self.space_manager.save_space_config()
                else:
                    logger.info(f"Using existing Letta Agents space: {existing_space_id}")

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
        for agent in agents:
            if agent["id"] in existing_agent_ids:
                mapping = self.mappings.get(agent["id"])
                logger.debug(f"Agent {agent['name']} - created: {mapping.created if mapping else 'No mapping'}, room: {mapping.room_created if mapping else 'No room'}")

                if mapping:
                    # Check if agent name has changed
                    if mapping.agent_name != agent['name']:
                        logger.info(f"Agent name changed from '{mapping.agent_name}' to '{agent['name']}'")

                        # Update the stored agent name
                        old_name = mapping.agent_name
                        mapping.agent_name = agent['name']

                        # Update room name if room exists
                        if mapping.room_id and mapping.room_created:
                            logger.info(f"Updating room name for {mapping.room_id}")
                            success = await self.update_room_name(mapping.room_id, agent['name'])
                            if success:
                                logger.debug(f"Successfully updated room name to '{agent['name']}'")
                            else:
                                logger.warning(f"Failed to update room name for {mapping.room_id}")

                        # Update display name for the Matrix user
                        if mapping.matrix_user_id and mapping.matrix_password:
                            logger.info(f"Updating display name for {mapping.matrix_user_id}")
                            display_success = await self.update_display_name(
                                mapping.matrix_user_id, agent['name'], mapping.matrix_password
                            )
                            if display_success:
                                logger.debug(f"Successfully updated display name for {mapping.matrix_user_id}")
                            else:
                                logger.warning(f"Failed to update display name for {mapping.matrix_user_id}")

                    # Retry user creation if failed
                    if not mapping.created:
                        logger.info(f"Retrying creation for existing agent {agent['name']} with failed status")
                        await self.create_user_for_agent(agent)
                    # Create room if user exists but room doesn't
                    elif mapping.created and not mapping.room_created:
                        logger.info(f"Creating room for existing agent {agent['name']}")
                        await self.create_or_update_agent_room(agent["id"])
                    # If room exists, validate it and check for room drift
                    elif mapping.created and mapping.room_created and mapping.room_id:
                        # Try to discover the actual room the agent is in
                        try:
                            actual_room_id = await self.discover_agent_room(mapping.matrix_user_id)
                            
                            if actual_room_id and actual_room_id != mapping.room_id:
                                logger.warning(f"🔄 Room drift detected for {agent['name']}!")
                                logger.warning(f"  Stored room:  {mapping.room_id}")
                                logger.warning(f"  Actual room:  {actual_room_id}")
                                mapping.room_id = actual_room_id
                                logger.info(f"✅ Fixed room mapping for {agent['name']}")
                            elif not actual_room_id:
                                # Could not discover room - fall back to existence check
                                room_exists = await self.space_manager.check_room_exists(mapping.room_id)
                                if not room_exists:
                                    logger.warning(f"Room {mapping.room_id} for {agent['name']} is invalid, recreating")
                                    mapping.room_id = None
                                    mapping.room_created = False
                                    # Recreate the room
                                    await self.create_or_update_agent_room(agent["id"])
                                    continue  # Skip invitation acceptance below
                        except Exception as e:
                            logger.error(f"Error checking room drift for {agent['name']}: {e}")
                        
                        # Ensure invitations are accepted and admin has access
                        if mapping.room_id:
                            logger.info(f"Ensuring invitations are accepted for room {mapping.room_id}")
                            await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                            
                            # Ensure all required members are in the room (admin, letta, agent_mail_bridge)
                            member_results = await self.room_manager.ensure_required_members(mapping.room_id, agent['id'])
                            for user_id, status in member_results.items():
                                if status == "invited":
                                    logger.info(f"✅ Invited {user_id} to {agent['name']}'s room")
                                elif status == "failed":
                                    logger.warning(f"⚠️  Failed to ensure {user_id} in {agent['name']}'s room")
        # TODO: Optionally handle removed agents (deactivate users?)
        removed_agents = existing_agent_ids - current_agent_ids
        if removed_agents:
            logger.info(f"Found {len(removed_agents)} agents that no longer exist: {removed_agents}")

        # Save updated mappings
        await self.save_mappings()

        # If space was just created, migrate all existing rooms to it
        if space_just_created and self.space_manager.get_space_id():
            logger.info("Migrating existing agent rooms to the new space")
            migrated = await self.space_manager.migrate_existing_rooms_to_space(self.mappings)
            logger.info(f"Migrated {migrated} rooms to space")

        # Temporarily disabled to prevent blocking message processing
        # TODO: Fix permission issues before re-enabling
        # await self.invite_admin_to_existing_rooms()

        sync_duration = time.time() - sync_start
        logger.info(f"Sync complete. Total mappings: {len(self.mappings)}, Duration: {sync_duration:.2f}s")
        logger.info(f"Sync metrics - Cache hits: {sync_metrics['cache_hits']}, API checks: {sync_metrics['api_checks']}, Login attempts: {sync_metrics['login_attempts']}, Rooms: {sync_metrics['rooms_processed']}")

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

            # If both user and room exist, validate the room is still valid
            if existing_mapping.created and existing_mapping.room_created and existing_mapping.room_id:
                # Always try to discover the actual room the agent is in
                # This handles cases where rooms were recreated or the mapping is stale
                logger.debug(f"Validating room mapping for {agent_name}")
                
                try:
                    actual_room_id = await self.discover_agent_room(existing_mapping.matrix_user_id)
                    
                    if actual_room_id:
                        # Check if discovered room matches stored room
                        if actual_room_id != existing_mapping.room_id:
                            logger.warning(f"Room drift detected for {agent_name}!")
                            logger.warning(f"  Stored room:  {existing_mapping.room_id}")
                            logger.warning(f"  Actual room:  {actual_room_id}")
                            logger.info(f"Updating mapping to use actual room")
                            existing_mapping.room_id = actual_room_id
                            await self.save_mappings()
                            logger.info(f"✅ Fixed room mapping for {agent_name}")
                        else:
                            logger.debug(f"Room mapping for {agent_name} is correct")
                        return
                    else:
                        # Could not discover room - check if stored room exists
                        room_exists = await self.space_manager.check_room_exists(existing_mapping.room_id)
                        if room_exists:
                            logger.info(f"Agent {agent_name} has valid room, no drift detected")
                            return
                        else:
                            logger.warning(f"Stored room {existing_mapping.room_id} for {agent_name} is invalid and no room discovered")
                            existing_mapping.room_id = None
                            existing_mapping.room_created = False
                            await self.save_mappings()
                            # Fall through to create room below
                            
                except Exception as e:
                    logger.error(f"Error during room validation for {agent_name}: {e}")
                    # Fall back to simple existence check
                    room_exists = await self.space_manager.check_room_exists(existing_mapping.room_id or "")
                    if room_exists:
                        logger.info(f"Agent {agent_name} has valid room (discovery failed, using basic check)")
                        return
                    else:
                        logger.warning(f"Room {existing_mapping.room_id} for {agent_name} is invalid")
                        existing_mapping.room_id = None
                        existing_mapping.room_created = False
                        await self.save_mappings()


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

        # Create the Matrix user with the agent name as display name
        success = await self.create_matrix_user(username, password, agent_name)

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
            # Display name is set during user creation via set_user_display_name
            # No need to call update_display_name here

            # Now create/update the room for this agent
            await self.create_or_update_agent_room(agent_id)
        else:
            logger.error(f"Failed to create Matrix user for agent {agent_name}")

    async def discover_agent_room(self, agent_user_id: str) -> Optional[str]:
        """
        Discover the actual room for an agent by checking the database.
        The database is the source of truth for current room assignments.
        Returns the room_id if found, None otherwise.
        """
        try:
            from src.core.mapping_service import get_mapping_by_matrix_user
            
            mapping = get_mapping_by_matrix_user(agent_user_id)
            if mapping:
                room_id = mapping.get("room_id")
                if room_id:
                    logger.info(f"Found room in database for {agent_user_id}: {room_id}")
                    return room_id
                else:
                    logger.warning(f"Agent {agent_user_id} has no room_id in database")
                    return None
            
            logger.warning(f"Agent {agent_user_id} not found in database mappings")
            return None
                    
        except Exception as e:
            logger.error(f"Error discovering room from database for {agent_user_id}: {e}")
            return None
    
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

    async def update_display_name(self, user_id: str, display_name: str, password: Optional[str] = None) -> bool:
        """Update the display name of a Matrix user - delegates to user_manager"""
        return await self.user_manager.update_display_name(user_id, display_name, password)

    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        """Find an existing room for an agent by searching room names - delegates to room_manager"""
        return await self.room_manager.find_existing_agent_room(agent_name)

    async def create_or_update_agent_room(self, agent_id: str):
        """Create or update a Matrix room for agent communication with validation"""
        mapping = self.mappings.get(agent_id)
        if not mapping:
            logger.error(f"No mapping found for agent {agent_id}, cannot create room")
            return None

        # Validate existing room (ensure create event/version)
        if mapping.room_id and mapping.room_created:
            room_exists = await self.room_manager.space_manager.check_room_exists(mapping.room_id)
            if room_exists:
                logger.info(f"Room exists for agent {agent_id}: {mapping.room_id}")
                await self.room_manager.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                return mapping.room_id
            else:
                logger.warning(f"Room {mapping.room_id} invalid/missing, clearing and recreating")
                mapping.room_id = None
                mapping.room_created = False
                await self.save_mappings()

        logger.info(f"Creating room for agent {agent_id}: {mapping.agent_name}")
        result = await self.room_manager.create_or_update_agent_room(agent_id, mapping)
        if result:
            logger.info(f"Room created for agent {agent_id}: {result}")
        else:
            logger.error(f"Failed to create room for agent {agent_id}")
        return result


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

# Global manager instance to preserve cache between sync runs
_global_manager = None


async def check_provisioning_health(config) -> dict:
    """
    Check the health of agent room provisioning.
    
    Returns a dict with:
    - total_agents: Number of Letta agents
    - agents_with_rooms: Number with valid room mappings
    - agents_missing_rooms: List of agents without rooms
    - status: 'healthy', 'degraded', or 'unhealthy'
    """
    global _global_manager
    
    if _global_manager is None:
        _global_manager = AgentUserManager(config)
    
    manager = _global_manager
    
    try:
        # Get all Letta agents
        agents = await manager.get_letta_agents()
        total_agents = len(agents)
        
        # Load current mappings
        await manager.load_existing_mappings()
        
        # Check which agents have rooms
        agents_with_rooms = 0
        agents_missing_rooms = []
        
        for agent in agents:
            agent_id = agent.get("id", "")
            agent_name = agent.get("name", "Unknown")
            
            mapping = manager.mappings.get(agent_id)
            if mapping and mapping.room_id and mapping.room_created:
                agents_with_rooms += 1
            else:
                agents_missing_rooms.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name
                })
        
        # Determine status
        missing_count = len(agents_missing_rooms)
        if missing_count == 0:
            status = "healthy"
        elif missing_count <= 3:
            status = "degraded"
        else:
            status = "unhealthy"
        
        return {
            "total_agents": total_agents,
            "agents_with_rooms": agents_with_rooms,
            "agents_missing_rooms": agents_missing_rooms,
            "missing_count": missing_count,
            "status": status
        }
        
    except Exception as e:
        logger.error(f"Error checking provisioning health: {e}")
        return {
            "total_agents": 0,
            "agents_with_rooms": 0,
            "agents_missing_rooms": [],
            "missing_count": -1,
            "status": "error",
            "error": str(e)
        }


async def run_agent_sync(config):
    """Run the agent sync process"""
    global _global_manager
    
    # Configure logger for this module with same level as main
    import sys
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, config.log_level.upper()))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, config.log_level.upper()))

    logger.info("Starting agent sync process from run_agent_sync")
    
    # Reuse existing manager to preserve cache
    if _global_manager is None:
        logger.info("Creating new AgentUserManager instance")
        _global_manager = AgentUserManager(config)
    else:
        logger.debug("Reusing existing AgentUserManager instance (cache preserved)")
    
    manager = _global_manager
    
    # Ensure core users exist before syncing agents
    logger.info("Ensuring core Matrix users exist...")
    core_users = [
        (config.username, config.password, "Letta Bot"),
        (manager.admin_username, manager.admin_password, "Matrix Admin")
    ]
    await manager.user_manager.ensure_core_users_exist(core_users)
    
    await manager.sync_agents_to_users()
    return manager