#!/usr/bin/env python3
"""
Matrix Room Manager — slim orchestrator.

All domain logic lives in focused mixin modules:
  - room_power_levels: get/set/build power levels, hierarchy
  - room_topic: get/set topic, build agent room topic
  - room_membership: check/ensure members, invitations, auto-accept
  - room_lifecycle: leave, forget, rename, remove from space
  - room_agent_auth: agent login with recovery, password reset, failure tracking
  - room_history_import: import recent Letta history into rooms
"""
import asyncio
import logging
import os
from typing import Dict, Optional

import aiohttp

from .types import AgentUserMapping
from .room_power_levels import RoomPowerLevelsMixin
from .room_topic import RoomTopicMixin
from .room_membership import RoomMembershipMixin
from .room_lifecycle import RoomLifecycleMixin
from .room_agent_auth import RoomAgentAuthMixin
from .room_history_import import RoomHistoryImportMixin

logger = logging.getLogger("matrix_client.room_manager")

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class MatrixRoomManager(
    RoomPowerLevelsMixin,
    RoomTopicMixin,
    RoomMembershipMixin,
    RoomLifecycleMixin,
    RoomAgentAuthMixin,
    RoomHistoryImportMixin,
):
    """Manages Matrix rooms for Letta agents"""

    def __init__(
        self,
        homeserver_url: str,
        space_manager,
        user_manager,
        config,
        admin_username: str,
        get_admin_token_callback,
        save_mappings_callback
    ):
        """
        Initialize the room manager

        Args:
            homeserver_url: Matrix homeserver URL
            space_manager: MatrixSpaceManager instance
            user_manager: MatrixUserManager instance
            config: Configuration object with username/password
            admin_username: Admin username for invitations
            get_admin_token_callback: Async callback to get admin token
            save_mappings_callback: Async callback to save mappings
        """
        self.homeserver_url = homeserver_url
        self.space_manager = space_manager
        self.user_manager = user_manager
        self.config = config
        self.admin_username = admin_username
        self.get_admin_token = get_admin_token_callback
        self.save_mappings = save_mappings_callback
        # Cache to track which users are already joined to which rooms
        # Format: {(room_id, username): True}
        self._membership_cache: Dict[tuple, bool] = {}
        self._agent_auth_failures: Dict[str, int] = {}
        self._agent_auth_last_reason: Dict[str, str] = {}
        self._agent_auth_last_status: Dict[str, int] = {}
        self._agent_auth_next_retry_at: Dict[str, float] = {}
        self._agent_auth_last_password: Dict[str, str] = {}
        self.agent_auth_retry_limit = int(os.getenv("AGENT_AUTH_RETRY_LIMIT", "3"))
        self.agent_auth_backoff_seconds = float(os.getenv("AGENT_AUTH_BACKOFF_SECONDS", "0.5"))
        self.agent_auth_cooldown_seconds = float(os.getenv("AGENT_AUTH_COOLDOWN_SECONDS", "300"))
        self.topic_update_interval_seconds = float(os.getenv("ROOM_TOPIC_UPDATE_INTERVAL_SECONDS", "30"))
        self._topic_update_last_at: Dict[str, float] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session

        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=50,
                ttl_dns_cache=300,
                keepalive_timeout=30,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=DEFAULT_TIMEOUT,
            )
            return self._session

    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        """Find an existing room for an agent by searching room names"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot search rooms")
                return None

            url = f"{self.homeserver_url}/_matrix/client/r0/joined_rooms"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    logger.error(f"Failed to get joined rooms: {response.status}")
                    return None

                data = await response.json()
                room_ids = data.get("joined_rooms", [])

            expected_name = f"{agent_name} - Letta Agent Chat"
            for room_id in room_ids:
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

    async def create_or_update_agent_room(self, agent_id: str, mapping: AgentUserMapping):
        """Create or update a Matrix room for agent communication"""
        if not mapping or not mapping.created:
            logger.error(f"Cannot create room for agent {agent_id} - user not created")
            return None

        # Check if room already exists in our mapping and on the server
        if mapping.room_id and mapping.room_created:
            room_exists = await self.space_manager.check_room_exists(mapping.room_id)
            if room_exists:
                logger.info(f"Room already exists for agent {mapping.agent_name}: {mapping.room_id}")

                admin_in_room = await self.check_admin_in_room(mapping.room_id)
                if not admin_in_room:
                    logger.warning(f"⚠️  Admin not in room {mapping.room_id} for agent {mapping.agent_name}")
                    logger.warning(f"⚠️  Room exists but admin has no access - not recreating to prevent drift")
                    logger.warning(f"⚠️  Manual intervention required: invite @admin:matrix.oculair.ca to {mapping.room_id}")
                    await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                    return mapping.room_id

                await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                return mapping.room_id
            else:
                logger.warning(f"Room {mapping.room_id} in mapping doesn't exist on server, checking for existing rooms")
                mapping.room_id = None
                mapping.room_created = False

        # Check if a room already exists for this agent on the server
        existing_room_id = await self.find_existing_agent_room(mapping.agent_name)
        if existing_room_id:
            logger.info(f"Found existing room for agent {mapping.agent_name}: {existing_room_id}")
            mapping.room_id = existing_room_id
            mapping.room_created = True
            await self.save_mappings()
            await self.auto_accept_invitations_with_tracking(existing_room_id, mapping)
            return existing_room_id

        try:
            agent_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')

            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": mapping.matrix_password
            }

            session = await self._get_session()
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

            room_url = f"{self.homeserver_url}/_matrix/client/r0/createRoom"

            invites = [
                "@admin:matrix.oculair.ca",
                self.admin_username,
                self.config.username,
                "@oc_matrix_tuwunel_deploy:matrix.oculair.ca",
            ]

            room_data = {
                "name": f"{mapping.agent_name} - Letta Agent Chat",
                "topic": self._build_agent_room_topic(mapping.agent_name),
                "preset": "trusted_private_chat",
                "invite": invites,
                "is_direct": False,
                "power_level_content_override": self._build_room_power_levels(
                    room_creator_user_id=mapping.matrix_user_id,
                    invited_users=invites,
                ),
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

                    mapping.room_id = room_id
                    mapping.room_created = True
                    mapping.invitation_status = {user_id: "invited" for user_id in invites}

                    await self.save_mappings()

                    if self.space_manager.get_space_id():
                        logger.info(f"Adding room {room_id} to Letta Agents space")
                        space_success = await self.space_manager.add_room_to_space(room_id, mapping.agent_name)
                        if space_success:
                            logger.info(f"Successfully added room to space")
                        else:
                            logger.warning(f"Failed to add room to space")

                    await self.auto_accept_invitations_with_tracking(room_id, mapping)

                    member_results = await self.ensure_required_members(room_id, agent_id)
                    for user_id, status in member_results.items():
                        if status == "invited":
                            logger.info(f"✅ Invited {user_id} to new room {room_id}")
                        elif status == "failed":
                            logger.warning(f"⚠️  Failed to add {user_id} to new room {room_id}")

                    logger.info(f"Importing recent history for agent {mapping.agent_name}")
                    await self.import_recent_history(
                        agent_id=agent_id,
                        agent_username=mapping.matrix_user_id,
                        agent_password=mapping.matrix_password,
                        room_id=room_id
                    )

                    return room_id
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to create room for agent {mapping.agent_name}: {response.status} - {error_text}")
                    return None

        except Exception as e:
            logger.error(f"Error creating room for agent {agent_id}: {e}")
            return None
