#!/usr/bin/env python3
"""
Matrix Room Manager - Handles room creation and management
"""
import asyncio
import logging
import aiohttp
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .types import AgentUserMapping
from src.core.password_consistency import sync_agent_password_consistently

logger = logging.getLogger("matrix_client.room_manager")

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class MatrixRoomManager:
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

    def _build_agent_room_topic(self, agent_name: str) -> str:
        created = datetime.now(timezone.utc).date().isoformat()
        return f"🤖 {agent_name} — Letta agent workspace (created {created})"

    async def get_topic(self, room_id: str) -> Optional[str]:
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot get room topic")
                return None

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.topic"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    payload = await response.json()
                    if isinstance(payload, dict):
                        topic = payload.get("topic")
                        return str(topic) if isinstance(topic, str) else None
                    return None
                if response.status == 404:
                    return None
                error_text = await response.text()
                logger.warning(
                    "Failed to get topic for %s: %s - %s",
                    room_id,
                    response.status,
                    error_text,
                )
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.error("Error getting topic for %s: %s", room_id, exc)
            return None

    async def set_topic(
        self,
        room_id: str,
        topic_text: str,
        acting_user_id: Optional[str] = None,
    ) -> bool:
        now = time.monotonic()
        last_updated = self._topic_update_last_at.get(room_id)
        if last_updated is not None and (now - last_updated) < self.topic_update_interval_seconds:
            logger.info(
                "Skipping room topic update for %s due to rate limit (%ss)",
                room_id,
                self.topic_update_interval_seconds,
            )
            return False

        power_levels = await self.get_power_levels(room_id)
        if not power_levels:
            return False

        raw_users = power_levels.get("users")
        users: Dict[str, int] = {}
        if isinstance(raw_users, dict):
            for key, value in raw_users.items():
                if isinstance(value, bool):
                    users[str(key)] = int(value)
                elif isinstance(value, int):
                    users[str(key)] = value
                elif isinstance(value, float):
                    users[str(key)] = int(value)
                elif isinstance(value, str):
                    try:
                        users[str(key)] = int(value)
                    except ValueError:
                        continue

        users_default_raw = power_levels.get("users_default", 0)
        if isinstance(users_default_raw, bool):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, int):
            users_default = users_default_raw
        elif isinstance(users_default_raw, float):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, str):
            try:
                users_default = int(users_default_raw)
            except ValueError:
                users_default = 0
        else:
            users_default = 0

        raw_events = power_levels.get("events")
        topic_required = 50
        if isinstance(raw_events, dict):
            topic_level_raw = raw_events.get("m.room.topic")
            if isinstance(topic_level_raw, bool):
                topic_required = int(topic_level_raw)
            elif isinstance(topic_level_raw, int):
                topic_required = topic_level_raw
            elif isinstance(topic_level_raw, float):
                topic_required = int(topic_level_raw)
            elif isinstance(topic_level_raw, str):
                try:
                    topic_required = int(topic_level_raw)
                except ValueError:
                    topic_required = 50

        actor_level = users.get(acting_user_id, users_default) if acting_user_id else 100
        if int(actor_level) < int(topic_required):
            logger.warning(
                "Refusing topic update in %s due to insufficient power level: actor=%s actor_level=%s required=%s",
                room_id,
                acting_user_id,
                actor_level,
                topic_required,
            )
            return False

        admin_token = await self.get_admin_token()
        if not admin_token:
            logger.warning("Failed to get admin token, cannot set room topic")
            return False

        url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.topic"
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }

        session = await self._get_session()
        try:
            async with session.put(
                url,
                headers=headers,
                json={"topic": topic_text},
                timeout=DEFAULT_TIMEOUT,
            ) as response:
                if response.status == 200:
                    self._topic_update_last_at[room_id] = now
                    logger.info("Updated room topic for %s", room_id)
                    return True
                error_text = await response.text()
                logger.warning(
                    "Failed to set topic for %s: %s - %s",
                    room_id,
                    response.status,
                    error_text,
                )
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.error("Error setting topic for %s: %s", room_id, exc)
            return False

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

    def _build_room_power_levels(
        self,
        *,
        room_creator_user_id: str,
        invited_users: List[str],
    ) -> Dict[str, object]:
        users = {room_creator_user_id: 100}
        for user_id in invited_users:
            users[user_id] = 50

        return {
            "users": users,
            "users_default": 0,
            "events": {
                "m.room.name": 50,
                "m.room.topic": 50,
                "m.room.power_levels": 100,
            },
            "events_default": 0,
            "state_default": 50,
            "ban": 50,
            "kick": 50,
            "redact": 50,
            "invite": 0,
        }

    async def get_power_levels(self, room_id: str) -> Optional[Dict[str, object]]:
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot get power levels")
                return None

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.power_levels"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    payload = await response.json()
                    if isinstance(payload, dict):
                        return payload
                    logger.warning("Unexpected power levels payload type for room %s", room_id)
                    return None
                error_text = await response.text()
                logger.warning(
                    "Failed to get power levels for %s: %s - %s",
                    room_id,
                    response.status,
                    error_text,
                )
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.error("Error getting power levels for %s: %s", room_id, exc)
            return None

    async def _put_power_levels(self, room_id: str, content: Dict[str, object]) -> bool:
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot update power levels")
                return False

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.power_levels"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json",
            }

            session = await self._get_session()
            async with session.put(url, headers=headers, json=content, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info("Updated power levels for room %s", room_id)
                    return True
                error_text = await response.text()
                logger.warning(
                    "Failed to update power levels for %s: %s - %s",
                    room_id,
                    response.status,
                    error_text,
                )
                return False
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, TypeError, OSError) as exc:
            logger.error("Error updating power levels for %s: %s", room_id, exc)
            return False

    async def set_user_power_level(
        self,
        room_id: str,
        user_id: str,
        level: int,
        acting_user_id: Optional[str] = None,
    ) -> bool:
        if level >= 100:
            logger.warning("Refusing to set PL >= 100 for %s in room %s", user_id, room_id)
            return False

        power_levels = await self.get_power_levels(room_id)
        if not power_levels:
            return False

        raw_users = power_levels.get("users")
        users: Dict[str, int] = {}
        if isinstance(raw_users, dict):
            for key, value in raw_users.items():
                if isinstance(value, bool):
                    users[str(key)] = int(value)
                elif isinstance(value, int):
                    users[str(key)] = value
                elif isinstance(value, float):
                    users[str(key)] = int(value)
                elif isinstance(value, str):
                    try:
                        users[str(key)] = int(value)
                    except ValueError:
                        continue

        users_default_raw = power_levels.get("users_default", 0)
        if isinstance(users_default_raw, bool):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, int):
            users_default = users_default_raw
        elif isinstance(users_default_raw, float):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, str):
            try:
                users_default = int(users_default_raw)
            except ValueError:
                users_default = 0
        else:
            users_default = 0

        actor_level = users.get(acting_user_id, users_default) if acting_user_id else 100
        if acting_user_id and level >= int(actor_level):
            logger.warning(
                "Refusing power level escalation in %s: actor=%s actor_level=%s requested=%s",
                room_id,
                acting_user_id,
                actor_level,
                level,
            )
            return False

        users[user_id] = int(level)
        power_levels["users"] = users
        logger.info(
            "Setting user power level in room %s: %s -> %s",
            room_id,
            user_id,
            level,
        )
        return await self._put_power_levels(room_id, power_levels)

    async def set_event_power_level(
        self,
        room_id: str,
        event_type: str,
        level: int,
        acting_user_id: Optional[str] = None,
    ) -> bool:
        if level >= 100 and event_type != "m.room.power_levels":
            logger.warning("Refusing to set %s PL >= 100 in room %s", event_type, room_id)
            return False

        power_levels = await self.get_power_levels(room_id)
        if not power_levels:
            return False

        raw_users = power_levels.get("users")
        users: Dict[str, int] = {}
        if isinstance(raw_users, dict):
            for key, value in raw_users.items():
                if isinstance(value, bool):
                    users[str(key)] = int(value)
                elif isinstance(value, int):
                    users[str(key)] = value
                elif isinstance(value, float):
                    users[str(key)] = int(value)
                elif isinstance(value, str):
                    try:
                        users[str(key)] = int(value)
                    except ValueError:
                        continue

        users_default_raw = power_levels.get("users_default", 0)
        if isinstance(users_default_raw, bool):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, int):
            users_default = users_default_raw
        elif isinstance(users_default_raw, float):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, str):
            try:
                users_default = int(users_default_raw)
            except ValueError:
                users_default = 0
        else:
            users_default = 0

        actor_level = users.get(acting_user_id, users_default) if acting_user_id else 100
        if acting_user_id and level >= int(actor_level):
            logger.warning(
                "Refusing event PL escalation in %s: actor=%s actor_level=%s requested=%s",
                room_id,
                acting_user_id,
                actor_level,
                level,
            )
            return False

        raw_events = power_levels.get("events")
        events: Dict[str, int] = {}
        if isinstance(raw_events, dict):
            for key, value in raw_events.items():
                if isinstance(value, bool):
                    events[str(key)] = int(value)
                elif isinstance(value, int):
                    events[str(key)] = value
                elif isinstance(value, float):
                    events[str(key)] = int(value)
                elif isinstance(value, str):
                    try:
                        events[str(key)] = int(value)
                    except ValueError:
                        continue
        events[event_type] = int(level)
        power_levels["events"] = events
        logger.info(
            "Setting event power level in room %s: %s -> %s",
            room_id,
            event_type,
            level,
        )
        return await self._put_power_levels(room_id, power_levels)

    async def make_room_read_only(self, room_id: str, acting_user_id: Optional[str] = None) -> bool:
        return await self.set_event_power_level(
            room_id,
            "m.room.message",
            50,
            acting_user_id=acting_user_id,
        )

    async def make_room_writable(self, room_id: str, acting_user_id: Optional[str] = None) -> bool:
        return await self.set_event_power_level(
            room_id,
            "m.room.message",
            0,
            acting_user_id=acting_user_id,
        )

    async def apply_multi_agent_power_hierarchy(
        self,
        room_id: str,
        coordinator_user_id: str,
        worker_user_ids: Optional[List[str]] = None,
        observer_user_ids: Optional[List[str]] = None,
        acting_user_id: Optional[str] = None,
    ) -> bool:
        power_levels = await self.get_power_levels(room_id)
        if not power_levels:
            return False

        raw_users = power_levels.get("users")
        users: Dict[str, int] = {}
        if isinstance(raw_users, dict):
            for key, value in raw_users.items():
                if isinstance(value, bool):
                    users[str(key)] = int(value)
                elif isinstance(value, int):
                    users[str(key)] = value
                elif isinstance(value, float):
                    users[str(key)] = int(value)
                elif isinstance(value, str):
                    try:
                        users[str(key)] = int(value)
                    except ValueError:
                        continue

        users_default_raw = power_levels.get("users_default", 0)
        if isinstance(users_default_raw, bool):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, int):
            users_default = users_default_raw
        elif isinstance(users_default_raw, float):
            users_default = int(users_default_raw)
        elif isinstance(users_default_raw, str):
            try:
                users_default = int(users_default_raw)
            except ValueError:
                users_default = 0
        else:
            users_default = 0

        actor_level = users.get(acting_user_id, users_default) if acting_user_id else 100

        role_assignments: Dict[str, int] = {coordinator_user_id: 90}
        for worker in worker_user_ids or []:
            role_assignments[worker] = 50
        for observer in observer_user_ids or []:
            role_assignments[observer] = 10

        if acting_user_id:
            for user_id, level in role_assignments.items():
                if level >= int(actor_level):
                    logger.warning(
                        "Refusing hierarchy update in %s for %s -> %s (actor=%s level=%s)",
                        room_id,
                        user_id,
                        level,
                        acting_user_id,
                        actor_level,
                    )
                    return False

        users.update(role_assignments)
        power_levels["users"] = users
        logger.info(
            "Applying multi-agent hierarchy in %s: coordinator=%s workers=%s observers=%s",
            room_id,
            coordinator_user_id,
            len(worker_user_ids or []),
            len(observer_user_ids or []),
        )
        return await self._put_power_levels(room_id, power_levels)

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

            session = await self._get_session()
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

    async def check_admin_in_room(self, room_id: str) -> bool:
        """Check if admin user is a member of the given room"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check room membership")
                return False

            # Check room members
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    members = data.get("joined", {})
                    # Check if @admin:matrix.oculair.ca is in the room
                    is_member = "@admin:matrix.oculair.ca" in members
                    logger.debug(f"Admin membership check for room {room_id}: {is_member}")
                    return is_member
                elif response.status == 403:
                    # Admin not in room (forbidden access)
                    logger.debug(f"Admin not in room {room_id} (403 forbidden)")
                    return False
                else:
                    logger.warning(f"Failed to check room members: {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Error checking admin membership in room {room_id}: {e}")
            return False

    async def check_user_in_room(self, room_id: str, user_id: str) -> bool:
        """Check if a specific user is a member of the given room using Matrix API"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check room membership")
                return False

            # Use Matrix API to check room members
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    members = data.get("joined", {})
                    is_member = user_id in members
                    logger.debug(f"Membership check for {user_id} in room {room_id}: {is_member}")
                    return is_member
                elif response.status == 403:
                    # Not in room (forbidden access)
                    logger.debug(f"User {user_id} not in room {room_id} (403 forbidden)")
                    return False
                else:
                    logger.warning(f"Failed to check room members: {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Error checking membership for {user_id} in room {room_id}: {e}")
            return False

    # Required members that should be in every agent room
    REQUIRED_ROOM_MEMBERS = [
        "@admin:matrix.oculair.ca",  # Admin user for oversight
        "@letta:matrix.oculair.ca",  # Main Letta bridge bot
    ]

    async def get_room_members(self, room_id: str) -> List[str]:
        """Get list of all members in a room"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot get room members")
                return []

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    return list(data.get("joined", {}).keys())
                else:
                    logger.warning(f"Failed to get room members: {response.status}")
                    return []

        except Exception as e:
            logger.error(f"Error getting room members for {room_id}: {e}")
            return []

    # Known service user passwords - these are reset via admin commands if login fails
    SERVICE_USER_PASSWORDS: Dict[str, str] = {}
    
    async def _get_service_user_token(self, session: aiohttp.ClientSession, user_id: str) -> Optional[str]:
        """Get access token for a service user, resetting password if needed."""
        import secrets
        import string
        
        username = user_id.split(':')[0].replace('@', '')
        
        # Try cached password first
        password = self.SERVICE_USER_PASSWORDS.get(user_id)
        if password:
            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            login_data = {"type": "m.login.password", "user": username, "password": password}
            async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("access_token")
        
        # Generate new password and reset via admin
        new_password = f"{username}_" + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12)) + "!"
        
        # Get admin token
        admin_token = await self.get_admin_token()
        if not admin_token:
            logger.warning(f"Cannot reset password for {username} - no admin token")
            return None
        
        import time
        from .admin_room import resolve_admin_room_id, AdminRoomResolutionError
        try:
            admin_room = await resolve_admin_room_id(access_token=admin_token, homeserver_url=self.homeserver_url)
        except AdminRoomResolutionError as exc:
            logger.warning("Cannot reset password for %s: %s", username, exc)
            return None
        txn_id = int(time.time() * 1000)
        url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{admin_room}/send/m.room.message/{txn_id}"
        headers = {"Authorization": f"Bearer {admin_token}"}
        command = f"!admin users reset-password {username} {new_password}"
        
        async with session.put(url, headers=headers, json={"msgtype": "m.text", "body": command}, timeout=DEFAULT_TIMEOUT) as response:
            if response.status != 200:
                logger.warning(f"Failed to send password reset command for {username}")
                return None
        
        # Wait for command to process
        await asyncio.sleep(0.5)
        
        # Try login with new password
        login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
        login_data = {"type": "m.login.password", "user": username, "password": new_password}
        async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                # Cache the password for future use
                self.SERVICE_USER_PASSWORDS[user_id] = new_password
                return data.get("access_token")
        
        logger.warning(f"Failed to login as {username} after password reset")
        return None

    def _current_time(self) -> float:
        return time.monotonic()

    def _agent_login_suppressed(self, agent_id: str, password: str) -> bool:
        if self._agent_auth_last_password.get(agent_id) != password:
            return False
        next_retry = self._agent_auth_next_retry_at.get(agent_id, 0.0)
        return self._current_time() < next_retry

    def _record_agent_auth_failure(
        self,
        agent_id: str,
        password: str,
        reason: str,
        status: int,
        agent_username: str,
    ) -> None:
        failure_count = self._agent_auth_failures.get(agent_id, 0) + 1
        self._agent_auth_failures[agent_id] = failure_count
        self._agent_auth_last_reason[agent_id] = reason
        self._agent_auth_last_status[agent_id] = status
        self._agent_auth_last_password[agent_id] = password
        self._agent_auth_next_retry_at[agent_id] = self._current_time() + self.agent_auth_cooldown_seconds

        logger.warning(
            "agent_auth_failure agent_id=%s agent_username=%s count=%s status=%s reason=%s cooldown_until=%.3f",
            agent_id,
            agent_username,
            failure_count,
            status,
            reason,
            self._agent_auth_next_retry_at[agent_id],
        )

    def _record_agent_auth_success(self, agent_id: str, password: str, agent_username: str) -> None:
        previous_failures = self._agent_auth_failures.get(agent_id, 0)
        self._agent_auth_failures[agent_id] = 0
        self._agent_auth_last_reason[agent_id] = "healthy"
        self._agent_auth_last_status[agent_id] = 200
        self._agent_auth_last_password[agent_id] = password
        self._agent_auth_next_retry_at[agent_id] = 0.0

        if previous_failures > 0:
            logger.info(
                "agent_auth_recovered agent_id=%s agent_username=%s previous_failures=%s",
                agent_id,
                agent_username,
                previous_failures,
            )

    async def _reset_agent_password_via_admin_room(
        self,
        session: aiohttp.ClientSession,
        agent_username: str,
        new_password: str,
    ) -> bool:
        admin_token = await self.get_admin_token()
        if not admin_token:
            return False

        from .admin_room import resolve_admin_room_id, AdminRoomResolutionError
        try:
            admin_room = await resolve_admin_room_id(access_token=admin_token, homeserver_url=self.homeserver_url)
        except AdminRoomResolutionError:
            return False
        txn_id = int(self._current_time() * 1000)
        url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{admin_room}/send/m.room.message/{txn_id}"
        headers = {"Authorization": f"Bearer {admin_token}"}
        command = f"!admin users reset-password {agent_username} {new_password}"

        async with session.put(
            url,
            headers=headers,
            json={"msgtype": "m.text", "body": command},
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status != 200:
                return False

        await asyncio.sleep(self.agent_auth_backoff_seconds)
        return True

    async def _login_agent_with_recovery(
        self,
        session: aiohttp.ClientSession,
        agent_id: str,
        agent_username: str,
        agent_password: str,
    ) -> Optional[str]:
        login_url = f"{self.homeserver_url}/_matrix/client/r0/login"

        if self._agent_login_suppressed(agent_id, agent_password):
            self._record_agent_auth_failure(
                agent_id,
                agent_password,
                "suppressed_by_cooldown",
                429,
                agent_username,
            )
            return None

        async def login_with_password(password: str) -> tuple[Optional[str], int, str]:
            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": password,
            }
            async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("access_token"), 200, ""
                body = await response.text()
                return None, response.status, body

        token, status, error_text = await login_with_password(agent_password)
        if token:
            self._record_agent_auth_success(agent_id, agent_password, agent_username)
            return token

        if status != 403 and "M_FORBIDDEN" not in error_text:
            self._record_agent_auth_failure(
                agent_id,
                agent_password,
                "login_failed_non_forbidden",
                status,
                agent_username,
            )
            return None

        for attempt in range(1, self.agent_auth_retry_limit + 1):
            new_password = self.user_manager.generate_agent_password(agent_id)
            reset_ok = await self._reset_agent_password_via_admin_room(
                session,
                agent_username,
                new_password,
            )
            if not reset_ok:
                await asyncio.sleep(self.agent_auth_backoff_seconds * attempt)
                continue

            synced = await sync_agent_password_consistently(agent_id, new_password)
            if not synced:
                await asyncio.sleep(self.agent_auth_backoff_seconds * attempt)
                continue

            token, _, _ = await login_with_password(new_password)
            if token:
                self._record_agent_auth_success(agent_id, new_password, agent_username)
                return token
            await asyncio.sleep(self.agent_auth_backoff_seconds * attempt)

        self._record_agent_auth_failure(
            agent_id,
            agent_password,
            "login_failed_after_recovery",
            403,
            agent_username,
        )
        return None

    async def ensure_required_members(self, room_id: str, agent_id: str) -> Dict[str, str]:
        """
        Ensure all required members are in the room.
        This invites missing members AND has them join the room.
        Returns dict of {user_id: status} where status is 'already_member', 'joined', or 'failed'
        """
        results = {}
        
        try:
            # Get current room members
            current_members = await self.get_room_members(room_id)
            
            # Get agent credentials from database for inviting
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            mapping = db.get_by_agent_id(agent_id)
            
            if not mapping:
                logger.warning(f"Cannot ensure members for room {room_id} - no agent mapping found for {agent_id}")
                return {user: "failed" for user in self.REQUIRED_ROOM_MEMBERS}
            
            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')
            agent_password = str(mapping.matrix_password)
            
            session = await self._get_session()
            agent_token = await self._login_agent_with_recovery(
                session,
                agent_id,
                agent_username,
                agent_password,
            )
            if not agent_token:
                try:
                    from src.matrix.alerting import alert_auth_failure
                    await alert_auth_failure(agent_username, room_id)
                except Exception as alert_error:
                    logger.warning(f"Failed to send auth-failure alert for {agent_username}: {alert_error}")
                return {user: "failed" for user in self.REQUIRED_ROOM_MEMBERS}
                
            for required_user in self.REQUIRED_ROOM_MEMBERS:
                    if required_user in current_members:
                        results[required_user] = "already_member"
                        logger.debug(f"{required_user} already in room {room_id}")
                        continue
                    
                    logger.info(f"Ensuring {required_user} is in room {room_id}")
                    
                    # Step 1: Send invite from agent
                    invite_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/invite"
                    headers = {
                        "Authorization": f"Bearer {agent_token}",
                        "Content-Type": "application/json"
                    }
                    invite_data = {"user_id": required_user}
                    
                    async with session.post(invite_url, headers=headers, json=invite_data, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status == 200:
                            logger.info(f"Invited {required_user} to room {room_id}")
                        elif response.status == 403:
                            error_text = await response.text()
                            if "already in the room" in error_text.lower():
                                results[required_user] = "already_member"
                                continue
                            # User might already be invited, continue to join attempt
                        # Continue regardless - user might already be invited
                    
                    # Step 2: Login as the required user and join
                    user_token = await self._get_service_user_token(session, required_user)
                    if not user_token:
                        logger.warning(f"Could not get token for {required_user}")
                        results[required_user] = "failed"
                        continue
                    
                    # Join the room
                    join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
                    join_headers = {
                        "Authorization": f"Bearer {user_token}",
                        "Content-Type": "application/json"
                    }
                    
                    async with session.post(join_url, headers=join_headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status == 200:
                            logger.info(f"{required_user} joined room {room_id}")
                            results[required_user] = "joined"
                        elif response.status == 403:
                            error_text = await response.text()
                            if "already" in error_text.lower():
                                results[required_user] = "already_member"
                            else:
                                logger.warning(f"{required_user} failed to join: {error_text}")
                                results[required_user] = "failed"
                        else:
                            error_text = await response.text()
                            logger.warning(f"{required_user} failed to join: {response.status} - {error_text}")
                            results[required_user] = "failed"
            
            return results
            
        except Exception as e:
            logger.error(f"Error ensuring required members for room {room_id}: {e}")
            return {user: "failed" for user in self.REQUIRED_ROOM_MEMBERS}

    async def invite_admin_to_room(self, room_id: str, agent_name: str) -> bool:
        """Invite admin user to a room using the agent's credentials"""
        try:
            # We need to invite as a room member (the agent user)
            # First, get the agent mapping to get credentials
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            mapping = db.get_by_room_id(room_id)
            
            if not mapping:
                logger.warning(f"Cannot invite admin to room {room_id} - no agent mapping found")
                return False
            
            # Login as the agent user
            agent_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')
            
            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": mapping.matrix_password
            }
            
            session = await self._get_session()
            async with session.post(agent_login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Failed to login as agent {agent_username} to invite admin: {response.status} - {error_text}")
                    return False
                
                agent_auth = await response.json()
                agent_token = agent_auth.get("access_token")
                
                if not agent_token:
                    logger.error(f"No token received for agent {agent_username}")
                    return False
                
                # Invite admin to the room
                invite_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/invite"
                headers = {
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json"
                }
                
                invite_data = {
                    "user_id": "@admin:matrix.oculair.ca"
                }
                
                async with session.post(invite_url, headers=headers, json=invite_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"✅ Successfully invited admin to room {room_id} for agent {agent_name}")
                        
                        # Now auto-accept the invitation
                        await self._accept_invite_as_admin(room_id)
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to invite admin to room {room_id}: {response.status} - {error_text}")
                        return False
        
        except Exception as e:
            logger.error(f"Error inviting admin to room {room_id}: {e}")
            return False
    
    async def _accept_invite_as_admin(self, room_id: str) -> bool:
        """Accept room invitation as admin user"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot accept invitation")
                return False
            
            join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            
            session = await self._get_session()
            async with session.post(join_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"✅ Admin successfully joined room {room_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.warning(f"Admin could not join room {room_id}: {response.status} - {error_text}")
                    return False
        
        except Exception as e:
            logger.error(f"Error accepting invitation for admin: {e}")
            return False

    async def leave_room(self, room_id: str, access_token: str) -> bool:
        """Leave and forget a room using the provided access token."""
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            session = await self._get_session()
            leave_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/leave"
            async with session.post(leave_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Left room {room_id}")
                elif response.status == 403:
                    logger.debug(f"Already not in room {room_id} (403)")
                else:
                    error_text = await response.text()
                    logger.warning(f"Failed to leave room {room_id}: {response.status} - {error_text}")
                    return False

            forget_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/forget"
            async with session.post(forget_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Forgot room {room_id}")
                else:
                    logger.debug(f"Could not forget room {room_id}: {response.status}")

            return True
        except Exception as e:
            logger.error(f"Error leaving room {room_id}: {e}")
            return False

    async def leave_room_as_admin(self, room_id: str) -> bool:
        admin_token = await self.get_admin_token()
        if not admin_token:
            logger.warning("Cannot leave room — no admin token")
            return False
        return await self.leave_room(room_id, admin_token)

    async def leave_room_as_user(self, room_id: str, username: str, password: str) -> bool:
        try:
            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            login_data = {"type": "m.login.password", "user": username, "password": password}
            session = await self._get_session()
            async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    logger.warning(f"Cannot login as {username} to leave room {room_id}")
                    return False
                token = (await response.json()).get("access_token")
            if not token:
                return False
            return await self.leave_room(room_id, token)
        except Exception as e:
            logger.error(f"Error leaving room {room_id} as {username}: {e}")
            return False

    async def remove_room_from_space(self, room_id: str, space_id: str) -> bool:
        """Remove a child room from a space by sending empty m.space.child state."""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                return False
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{space_id}/state/m.space.child/{room_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            session = await self._get_session()
            async with session.put(url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Removed room {room_id} from space {space_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.warning(f"Failed to remove room {room_id} from space: {response.status} - {error_text}")
                    return False
        except Exception as e:
            logger.error(f"Error removing room {room_id} from space {space_id}: {e}")
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

            session = await self._get_session()
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

    async def create_or_update_agent_room(self, agent_id: str, mapping: AgentUserMapping):
        """Create or update a Matrix room for agent communication"""
        if not mapping or not mapping.created:
            logger.error(f"Cannot create room for agent {agent_id} - user not created")
            return None


        # Check if room already exists in our mapping and on the server
        if mapping.room_id and mapping.room_created:
            # Verify the room actually exists on the server
            room_exists = await self.space_manager.check_room_exists(mapping.room_id)
            if room_exists:
                logger.info(f"Room already exists for agent {mapping.agent_name}: {mapping.room_id}")
                
                # Check if admin is in the room - if not, don't recreate, just log warning
                admin_in_room = await self.check_admin_in_room(mapping.room_id)
                if not admin_in_room:
                    logger.warning(f"⚠️  Admin not in room {mapping.room_id} for agent {mapping.agent_name}")
                    logger.warning(f"⚠️  Room exists but admin has no access - not recreating to prevent drift")
                    logger.warning(f"⚠️  Manual intervention required: invite @admin:matrix.oculair.ca to {mapping.room_id}")
                    # Still try to auto-accept invitations in case invitation exists
                    await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                    return mapping.room_id
                
                # Ensure invitations are accepted
                await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                return mapping.room_id
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
            await self.auto_accept_invitations_with_tracking(existing_room_id, mapping)
            return existing_room_id

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

            # Now create the room as the agent user (inside the session)
            room_url = f"{self.homeserver_url}/_matrix/client/r0/createRoom"

            invites = [
                "@admin:matrix.oculair.ca",  # Your actual admin account
                self.admin_username,  # Admin user (matrixadmin)
                self.config.username,  # Main Letta bot (@letta)
                "@oc_matrix_synapse_deployment:matrix.oculair.ca",  # OpenCode bridge bot for inter-agent messaging
            ]

            room_data = {
                "name": f"{mapping.agent_name} - Letta Agent Chat",
                "topic": self._build_agent_room_topic(mapping.agent_name),
                "preset": "trusted_private_chat",  # Allows invited users to see history
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

                    # Update mapping with room info
                    mapping.room_id = room_id
                    mapping.room_created = True

                    # Initialize invitation status tracking
                    mapping.invitation_status = {user_id: "invited" for user_id in invites}

                    # Save updated mappings
                    await self.save_mappings()

                    # Add the room to the Letta Agents space
                    if self.space_manager.get_space_id():
                        logger.info(f"Adding room {room_id} to Letta Agents space")
                        space_success = await self.space_manager.add_room_to_space(room_id, mapping.agent_name)
                        if space_success:
                            logger.info(f"Successfully added room to space")
                        else:
                            logger.warning(f"Failed to add room to space")

                    # Now auto-accept the invitations for admin and letta users
                    await self.auto_accept_invitations_with_tracking(room_id, mapping)

                    # Ensure all required members have joined (not just invited)
                    member_results = await self.ensure_required_members(room_id, agent_id)
                    for user_id, status in member_results.items():
                        if status == "invited":
                            logger.info(f"✅ Invited {user_id} to new room {room_id}")
                        elif status == "failed":
                            logger.warning(f"⚠️  Failed to add {user_id} to new room {room_id}")

                    # Import recent conversation history for UI continuity
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

    async def auto_accept_invitations_with_tracking(self, room_id: str, mapping: AgentUserMapping):
        """Auto-accept room invitations for admin and letta users with status tracking"""
        users_to_accept = [
            (self.admin_username, self.user_manager.admin_password),
            (self.config.username, self.config.password)
        ]

        for username, password in users_to_accept:
            if not username or not password:
                continue

            # Check cache first - if user is already joined, skip
            cache_key = (room_id, username)
            if cache_key in self._membership_cache and self._membership_cache[cache_key]:
                logger.debug(f"User {username} already joined room {room_id} (cached)")
                if mapping.invitation_status:
                    mapping.invitation_status[username] = "joined"
                continue

            # Check Matrix API for actual membership before attempting login
            # This is the authoritative source and prevents unnecessary login attempts
            is_member = await self.check_user_in_room(room_id, username)
            if is_member:
                logger.info(f"User {username} already in room {room_id} (verified via API), updating cache")
                self._membership_cache[cache_key] = True
                if mapping.invitation_status:
                    mapping.invitation_status[username] = "joined"
                continue

            try:
                # Login as the user
                login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
                user_local = username.split(':')[0].replace('@', '')

                login_data = {
                    "type": "m.login.password",
                    "user": user_local,
                    "password": password
                }

                session = await self._get_session()
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
                        # Cache the successful join
                        self._membership_cache[cache_key] = True
                    elif response.status == 403:
                        error_text = await response.text()
                        if "already in the room" in error_text or "already joined" in error_text:
                            logger.info(f"User {username} is already in room {room_id}")
                            if mapping.invitation_status:
                                mapping.invitation_status[username] = "joined"
                            # Cache the membership
                            self._membership_cache[cache_key] = True
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

        # Note: save_mappings() removed here - caller should batch saves at end of sync

    async def import_recent_history(
        self,
        agent_id: str,
        agent_username: str,
        agent_password: str,
        room_id: str,
        limit: int = 15
    ):
        """Import recent Letta conversation history for UI continuity

        Args:
            agent_id: The Letta agent ID
            agent_username: Matrix username for the agent
            agent_password: Matrix password for the agent
            room_id: Matrix room ID to import messages into
            limit: Number of recent messages to import (default: 15, like letta-code)
        """
        try:
            # 1. Fetch recent messages from Letta proxy
            messages_url = f"http://192.168.50.90:8289/v1/agents/{agent_id}/messages"

            session = await self._get_session()
            async with session.get(messages_url, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    logger.warning(f"Could not fetch history for agent {agent_id}: {response.status}")
                    return

                data = await response.json()
                # Handle both array and object responses
                if isinstance(data, dict):
                    messages = data.get("items", [])
                else:
                    messages = data

            if not messages:
                logger.info(f"No history to import for agent {agent_id}")
                return

            # 2. Take only last N messages (like letta-code does)
            recent_messages = messages[-limit:] if len(messages) > limit else messages

            # 3. Skip if starts with orphaned tool_return (incomplete turn)
            if recent_messages and recent_messages[0].get("message_type") == "tool_return_message":
                recent_messages = recent_messages[1:]

            if not recent_messages:
                logger.info(f"No valid history to import for agent {agent_id}")
                return

            # 4. Login as the agent to send historical messages
            from nio import AsyncClient, LoginResponse
            agent_client = AsyncClient(self.homeserver_url, agent_username)

            try:
                login_response = await agent_client.login(agent_password)

                if not isinstance(login_response, LoginResponse):
                    logger.error(f"Failed to login as {agent_username} for history import")
                    await agent_client.close()
                    return

                # 5. Send each message with historical flag
                imported_count = 0
                for msg in recent_messages:
                    msg_type = msg.get("message_type")

                    # Only import user and assistant messages (skip tool calls, reasoning, etc.)
                    if msg_type == "user_message":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Handle content array format
                            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                            content = " ".join(text_parts)

                        await agent_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={
                                "msgtype": "m.text",
                                "body": f"[History] {content}",
                                "m.letta_historical": True,  # Flag to prevent processing
                                "m.relates_to": {
                                    "rel_type": "m.annotation"  # Mark as annotation
                                }
                            }
                        )
                        imported_count += 1

                    elif msg_type == "assistant_message":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Handle content array format
                            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                            content = " ".join(text_parts)

                        await agent_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={
                                "msgtype": "m.text",
                                "body": content,
                                "m.letta_historical": True,
                                "m.relates_to": {
                                    "rel_type": "m.annotation"
                                }
                            }
                        )
                        imported_count += 1

                logger.info(f"Imported {imported_count} historical messages for agent {agent_id}")

            finally:
                await agent_client.close()

        except Exception as e:
            logger.error(f"Error importing history for agent {agent_id}: {e}")
