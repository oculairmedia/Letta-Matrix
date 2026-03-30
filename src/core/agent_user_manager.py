#!/usr/bin/env python3
"""
Agent User Manager — slim orchestrator.

Domain logic lives in focused mixin modules:
  - agent_letta_client: Letta SDK agent fetching
  - agent_mapping_persistence: load/save mappings via SQLAlchemy
  - agent_provisioner: create users and discover rooms
  - agent_sync_orchestrator: space readiness, validation, cleanup, memory sync
  - agent_health: health checks and run_agent_sync entrypoint
"""
import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Set

import aiohttp

from .avatar_service import AvatarService
from .space_manager import MatrixSpaceManager
from .user_manager import MatrixUserManager
from .room_manager import MatrixRoomManager
from .types import AgentUserMapping
from .agent_letta_client import AgentLettaClientMixin
from .agent_mapping_persistence import AgentMappingPersistenceMixin
from .agent_provisioner import AgentProvisionerMixin
from .agent_sync_orchestrator import AgentSyncOrchestratorMixin
from .agent_health import (  # noqa: F401
    check_provisioning_health,
    run_agent_sync,
)

logger = logging.getLogger("matrix_client.agent_user_manager")

# Global session with connection pooling for better performance
_connector = None
global_session = None

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _get_connector():
    """Lazily create connector to avoid event loop issues at import time"""
    global _connector
    if _connector is None:
        _connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=50,
            ttl_dns_cache=300,
            keepalive_timeout=30,
            force_close=False
        )
    return _connector


async def get_global_session():
    """Get or create global aiohttp session with connection pooling"""
    global global_session
    if global_session is None or global_session.closed:
        connector = _get_connector()
        global_session = aiohttp.ClientSession(connector=connector)
    return global_session


class AgentUserManager(
    AgentLettaClientMixin,
    AgentMappingPersistenceMixin,
    AgentProvisionerMixin,
    AgentSyncOrchestratorMixin,
):
    """Manages Matrix users for Letta agents"""

    def __init__(self, config):
        self.config = config
        self.logger = logger
        self.matrix_api_url = config.matrix_api_url if hasattr(config, 'matrix_api_url') else "http://matrix-api:8000"
        self.homeserver_url = config.homeserver_url
        self.letta_token = config.letta_token
        self.letta_api_url = config.letta_api_url

        self.data_dir = os.getenv("MATRIX_DATA_DIR", "/app/data")
        self.mappings: Dict[str, AgentUserMapping] = {}
        self._removed_agents_last_sync: Set[str] = set()

        self.admin_username = os.getenv("MATRIX_ADMIN_USERNAME", config.username)
        self.admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", config.password)
        logger.info(f"Using admin account: {self.admin_username} (from env: {os.getenv('MATRIX_ADMIN_USERNAME')})")

        os.makedirs(self.data_dir, exist_ok=True)

        self.space_manager = MatrixSpaceManager(
            homeserver_url=self.homeserver_url,
            admin_username=self.admin_username,
            admin_password=self.admin_password,
            main_bot_username=config.username,
            space_config_file=os.path.join(self.data_dir, "letta_space_config.json")
        )

        self.user_manager = MatrixUserManager(
            homeserver_url=self.homeserver_url,
            admin_username=self.admin_username,
            admin_password=self.admin_password
        )

        self.room_manager = MatrixRoomManager(
            homeserver_url=self.homeserver_url,
            space_manager=self.space_manager,
            user_manager=self.user_manager,
            config=config,
            admin_username=self.admin_username,
            get_admin_token_callback=self.get_admin_token,
            save_mappings_callback=self.save_mappings
        )

        self._avatar_service = AvatarService(self.config, self.logger)
        self._avatar_service.user_manager = self.user_manager
        self._avatar_service.mappings = self.mappings

    @property
    def admin_token(self) -> Optional[str]:
        return self.user_manager.admin_token

    @admin_token.setter
    def admin_token(self, value: Optional[str]):
        self.user_manager.admin_token = value

    async def get_admin_token(self) -> Optional[str]:
        return await self.user_manager.get_admin_token()

    async def check_user_exists(self, username: str) -> str:
        return await self.user_manager.check_user_exists(username)

    async def create_matrix_user(self, username: str, password: str, display_name: str) -> bool:
        return await self.user_manager.create_matrix_user(username, password, display_name)

    async def set_user_display_name(self, user_id: str, display_name: str, access_token: str) -> bool:
        return await self.user_manager.set_user_display_name(user_id, display_name, access_token)

    def generate_username(self, agent_name: str, agent_id: str) -> str:
        return self.user_manager.generate_username(agent_name, agent_id)

    def generate_password(self) -> str:
        return self.user_manager.generate_password()

    async def ensure_core_users_exist(self):
        core_users = []

        if getattr(self.config, "username", None) and getattr(self.config, "password", None):
            core_users.append(
                (self.config.username, self.config.password, "Letta Bot")
            )

        if self.admin_username and self.admin_password:
            core_users.append(
                (self.admin_username, self.admin_password, "Matrix Admin")
            )

        mcp_username = os.getenv("MATRIX_MCP_USERNAME")
        mcp_password = os.getenv("MATRIX_MCP_PASSWORD")
        if mcp_username and mcp_password:
            core_users.append(
                (mcp_username, mcp_password, "Matrix MCP Bot")
            )

        await self.user_manager.ensure_core_users_exist(core_users)

    async def sync_agents_to_users(self):
        """Main function to sync Letta agents to Matrix users"""
        sync_start = time.time()
        logger.info("Starting agent-to-user sync process")
        sync_metrics = {
            "cache_hits": 0,
            "api_checks": 0,
            "login_attempts": 0,
            "rooms_processed": 0
        }

        space_just_created = await self._ensure_space_ready()
        agents = await self.get_letta_agents()
        if agents is None:
            logger.error("Letta agent fetch failed — aborting sync to prevent data loss")
            return
        current_agent_ids = {agent["id"] for agent in agents}
        existing_agent_ids = set(self.mappings.keys())
        existing_agents = [agent for agent in agents if agent["id"] in existing_agent_ids]
        await self._provision_new_agents(agents, existing_agent_ids)
        await self._validate_existing_agents(existing_agents)
        await self._set_missing_avatars(existing_agents)
        await self._cleanup_removed_agents(current_agent_ids, existing_agent_ids)
        await self.save_mappings()
        if space_just_created and self.space_manager.get_space_id():
            logger.info("Migrating existing agent rooms to the new space")
            migrated = await self.space_manager.migrate_existing_rooms_to_space(self.mappings)
            logger.info(f"Migrated {migrated} rooms to space")
        sync_duration = time.time() - sync_start
        logger.info(f"Sync complete. Total mappings: {len(self.mappings)}, Duration: {sync_duration:.2f}s")
        logger.info(f"Sync metrics - Cache hits: {sync_metrics['cache_hits']}, API checks: {sync_metrics['api_checks']}, Login attempts: {sync_metrics['login_attempts']}, Rooms: {sync_metrics['rooms_processed']}")

        await self._sync_matrix_memory()

    async def get_agent_user_mapping(self, agent_id: str) -> Optional[AgentUserMapping]:
        return self.mappings.get(agent_id)

    async def list_agent_users(self) -> List[AgentUserMapping]:
        return list(self.mappings.values())

    async def check_room_exists(self, room_id: str) -> bool:
        return await self.space_manager.check_room_exists(room_id)

    async def update_room_name(self, room_id: str, new_name: str) -> bool:
        return await self.room_manager.update_room_name(room_id, new_name)

    async def update_display_name(self, user_id: str, display_name: str, password: Optional[str] = None) -> bool:
        return await self.user_manager.update_display_name(user_id, display_name, password)

    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        return await self.room_manager.find_existing_agent_room(agent_name)

    async def create_or_update_agent_room(self, agent_id: str):
        mapping = self.mappings.get(agent_id)
        if not mapping:
            logger.error(f"No mapping found for agent {agent_id}, cannot create room")
            return None

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
        self, agent_id: str, agent_username: str, agent_password: str,
        room_id: str, limit: int = 15
    ):
        return await self.room_manager.import_recent_history(
            agent_id=agent_id, agent_username=agent_username,
            agent_password=agent_password, room_id=room_id, limit=limit
        )

    async def auto_accept_invitations_with_tracking(self, room_id: str, mapping: AgentUserMapping):
        return await self.room_manager.auto_accept_invitations_with_tracking(room_id, mapping)

    async def set_default_avatar_for_agent(self, agent_name: str, matrix_user_id: str) -> bool:
        return await self._avatar_service.set_default_avatar_for_agent(agent_name, matrix_user_id)

    def _generate_avatar_image(self, agent_name: str, size: int = 128) -> Optional[bytes]:
        return self._avatar_service._generate_avatar_image(agent_name, size)
