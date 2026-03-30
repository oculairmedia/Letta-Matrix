"""
Presence management for Matrix agent identities.

Sets online/unavailable/offline presence via the Matrix client-server API
so agents show status dots in Element (green=online, amber=busy, grey=offline).

Presence is per-user (per-identity), not per-room. Updates are rate-limited
per identity to avoid spamming the homeserver.
"""

import asyncio
import logging
import os
import time
from enum import Enum
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

_RATE_LIMIT_SECONDS = float(
    os.environ.get("PRESENCE_UPDATE_INTERVAL_SECONDS", "5")
)


class PresenceState(str, Enum):
    ONLINE = "online"
    UNAVAILABLE = "unavailable"
    OFFLINE = "offline"


class PresenceManager:
    _instance: Optional["PresenceManager"] = None
    _initialized: bool = False

    def __new__(cls, *args: object, **kwargs: object) -> "PresenceManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, homeserver_url: Optional[str] = None) -> None:
        if self._initialized:
            return
        self._homeserver = (
            homeserver_url
            or os.environ.get("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
        )
        self._last_update: Dict[str, float] = {}
        self._current_state: Dict[str, PresenceState] = {}
        self._initialized = True

    async def set_presence(
        self,
        user_id: str,
        access_token: str,
        state: PresenceState,
        status_msg: Optional[str] = None,
        *,
        force: bool = False,
    ) -> bool:
        now = time.monotonic()
        last = self._last_update.get(user_id, 0.0)
        current = self._current_state.get(user_id)

        if not force and current == state and (now - last) < _RATE_LIMIT_SECONDS:
            return True

        url = (
            f"{self._homeserver}/_matrix/client/v3"
            f"/presence/{_url_encode(user_id)}/status"
        )
        body: Dict[str, str] = {"presence": state.value}
        if status_msg is not None:
            body["status_msg"] = status_msg

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        self._last_update[user_id] = now
                        self._current_state[user_id] = state
                        logger.debug(
                            "Presence set: %s -> %s (%s)",
                            user_id,
                            state.value,
                            status_msg or "",
                        )
                        return True
                    text = await resp.text()
                    logger.warning(
                        "Presence update failed for %s: %s %s",
                        user_id,
                        resp.status,
                        text[:200],
                    )
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            logger.warning("Presence update error for %s: %s", user_id, exc)
            return False

    async def set_agent_busy(
        self,
        user_id: str,
        access_token: str,
        status_msg: str = "Thinking...",
    ) -> bool:
        return await self.set_presence(
            user_id, access_token, PresenceState.UNAVAILABLE, status_msg
        )

    async def set_agent_ready(
        self,
        user_id: str,
        access_token: str,
        status_msg: str = "Ready",
    ) -> bool:
        return await self.set_presence(
            user_id, access_token, PresenceState.ONLINE, status_msg
        )

    async def set_all_online(
        self, identities: Dict[str, str], status_msg: str = "Ready"
    ) -> int:
        count = 0
        tasks = [
            self.set_presence(
                uid, token, PresenceState.ONLINE, status_msg, force=True
            )
            for uid, token in identities.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ok in results:
            if ok is True:
                count += 1
        return count

    async def set_all_offline(self, identities: Dict[str, str]) -> int:
        count = 0
        tasks = [
            self.set_presence(uid, token, PresenceState.OFFLINE, force=True)
            for uid, token in identities.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ok in results:
            if ok is True:
                count += 1
        return count

    def get_current_state(self, user_id: str) -> Optional[PresenceState]:
        return self._current_state.get(user_id)

    @classmethod
    def reset(cls) -> None:
        cls._instance = None
        cls._initialized = False


def _url_encode(user_id: str) -> str:
    return user_id.replace("@", "%40").replace(":", "%3A")


def _resolve_agent_identity(agent_id: str) -> Optional[tuple[str, str]]:
    try:
        from src.core.identity_storage import get_identity_service

        svc = get_identity_service()
        identity = svc.get(f"letta_{agent_id}")
        if identity is not None:
            mxid = str(identity.mxid) if identity.mxid is not None else ""
            token = str(identity.access_token) if identity.access_token is not None else ""
            if mxid and token:
                return (mxid, token)
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("Cannot resolve identity for agent %s: %s", agent_id, exc)
    return None


_manager: Optional[PresenceManager] = None


def get_presence_manager(
    homeserver_url: Optional[str] = None,
) -> PresenceManager:
    global _manager
    if _manager is None:
        _manager = PresenceManager(homeserver_url)
    return _manager


async def notify_agent_busy(agent_id: str, status_msg: str = "Thinking...") -> None:
    resolved = _resolve_agent_identity(agent_id)
    if not resolved:
        return
    mxid, token = resolved
    mgr = get_presence_manager()
    await mgr.set_agent_busy(mxid, token, status_msg)


async def notify_agent_ready(agent_id: str, status_msg: str = "Ready") -> None:
    resolved = _resolve_agent_identity(agent_id)
    if not resolved:
        return
    mxid, token = resolved
    mgr = get_presence_manager()
    await mgr.set_agent_ready(mxid, token, status_msg)
