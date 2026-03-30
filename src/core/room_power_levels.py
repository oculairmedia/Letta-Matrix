"""
Room power level management — get, set, and build power levels for Matrix rooms.

Deduplicates the power-level value parsing that was previously copy-pasted 5x
across set_topic, set_user_power_level, set_event_power_level, and
apply_multi_agent_power_hierarchy.
"""

import asyncio
import logging
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger("matrix_client.room_manager")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ── Parsing helpers (dedup) ──────────────────────────────────────────────────


def parse_power_level_value(raw) -> Optional[int]:
    """Safely coerce a single power-level value to int, or None on failure."""
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def parse_users_dict(power_levels: Dict) -> Dict[str, int]:
    """Parse the ``users`` sub-dict from a power_levels event."""
    raw = power_levels.get("users")
    result: Dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            parsed = parse_power_level_value(value)
            if parsed is not None:
                result[str(key)] = parsed
    return result


def parse_users_default(power_levels: Dict) -> int:
    """Parse ``users_default`` from a power_levels event (defaults to 0)."""
    parsed = parse_power_level_value(power_levels.get("users_default", 0))
    return parsed if parsed is not None else 0


def parse_events_dict(power_levels: Dict) -> Dict[str, int]:
    """Parse the ``events`` sub-dict from a power_levels event."""
    raw = power_levels.get("events")
    result: Dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            parsed = parse_power_level_value(value)
            if parsed is not None:
                result[str(key)] = parsed
    return result


def get_actor_level(
    users: Dict[str, int],
    users_default: int,
    acting_user_id: Optional[str],
) -> int:
    """Resolve the effective power level for an acting user."""
    if not acting_user_id:
        return 100
    return users.get(acting_user_id, users_default)


# ── Mixin ────────────────────────────────────────────────────────────────────


class RoomPowerLevelsMixin:
    """Power-level methods mixed into MatrixRoomManager."""

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

        users = parse_users_dict(power_levels)
        users_default = parse_users_default(power_levels)
        actor_level = get_actor_level(users, users_default, acting_user_id)

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

        users = parse_users_dict(power_levels)
        users_default = parse_users_default(power_levels)
        actor_level = get_actor_level(users, users_default, acting_user_id)

        if acting_user_id and level >= int(actor_level):
            logger.warning(
                "Refusing event PL escalation in %s: actor=%s actor_level=%s requested=%s",
                room_id,
                acting_user_id,
                actor_level,
                level,
            )
            return False

        events = parse_events_dict(power_levels)
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

        users = parse_users_dict(power_levels)
        users_default = parse_users_default(power_levels)
        actor_level = get_actor_level(users, users_default, acting_user_id)

        role_assignments: Dict[str, int] = {coordinator_user_id: 90}
        for worker in worker_user_ids or []:
            role_assignments[worker] = 50
        for observer in observer_user_ids or []:
            role_assignments[observer] = 10

        if acting_user_id:
            for uid, level in role_assignments.items():
                if level >= int(actor_level):
                    logger.warning(
                        "Refusing hierarchy update in %s for %s -> %s (actor=%s level=%s)",
                        room_id,
                        uid,
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
