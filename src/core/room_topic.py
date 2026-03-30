"""
Room topic management — get, set, and build room topics.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp

from src.core.room_power_levels import (
    get_actor_level,
    parse_power_level_value,
    parse_users_dict,
    parse_users_default,
)

logger = logging.getLogger("matrix_client.room_manager")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class RoomTopicMixin:
    """Topic methods mixed into MatrixRoomManager."""

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

        users = parse_users_dict(power_levels)
        users_default = parse_users_default(power_levels)

        raw_events = power_levels.get("events")
        topic_required = 50
        if isinstance(raw_events, dict):
            parsed = parse_power_level_value(raw_events.get("m.room.topic"))
            if parsed is not None:
                topic_required = parsed

        actor_level = get_actor_level(users, users_default, acting_user_id)
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
