"""
Typing indicators as agent — one-shot and heartbeat-based.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

from src.matrix.agent_auth import get_agent_token
from src.matrix.agent_room_cache import _get_agent_mapping_for_room
from src.matrix.config import Config

_AGENT_SEND_TIMEOUT = aiohttp.ClientTimeout(total=15)
_RECOVERABLE_AGENT_ACTION_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    KeyError,
    ValueError,
    TypeError,
    RuntimeError,
    OSError,
)
_TYPING_HEARTBEAT_INTERVAL = 4.0
_TYPING_TIMEOUT_MS = 5000


async def _get_agent_typing_context(
    room_id: str, config: Config, logger: logging.Logger
) -> Optional[Dict[str, str]]:
    """Resolve agent credentials and build reusable typing context for a room."""
    agent_mapping = _get_agent_mapping_for_room(room_id, logger)
    if not agent_mapping:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            token = await get_agent_token(
                room_id, config, logger, session, caller="TYPING"
            )
            if not token:
                return None
    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.debug(f"[TYPING] Login failed: {e}")
        return None

    encoded_user_id = quote(agent_mapping["matrix_user_id"], safe="")
    typing_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/typing/{encoded_user_id}"

    return {"token": token, "typing_url": typing_url}


async def _put_typing(
    session: aiohttp.ClientSession,
    typing_url: str,
    token: str,
    typing: bool,
    timeout_ms: int,
    logger: logging.Logger,
) -> bool:
    """Fire a single typing PUT request using a pre-authenticated token."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    typing_data: Dict[str, Any] = {"typing": typing}
    if typing:
        typing_data["timeout"] = timeout_ms

    try:
        async with session.put(
            typing_url,
            headers=headers,
            json=typing_data,
            timeout=_AGENT_SEND_TIMEOUT,
        ) as response:
            if response.status == 200:
                if not typing:
                    expire_data = {"typing": True, "timeout": 1}
                    async with session.put(
                        typing_url,
                        headers=headers,
                        json=expire_data,
                        timeout=_AGENT_SEND_TIMEOUT,
                    ):
                        pass
                return True
            else:
                logger.debug(f"[TYPING] PUT failed: {response.status}")
                return False
    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.debug(f"[TYPING] PUT exception: {e}")
        return False


async def set_typing_as_agent(
    room_id: str,
    typing: bool,
    config: Config,
    logger: logging.Logger,
    timeout_ms: int = 5000,
) -> bool:
    """Set typing indicator as the agent user (one-shot, re-authenticates each call)."""
    ctx = await _get_agent_typing_context(room_id, config, logger)
    if not ctx:
        return False
    async with aiohttp.ClientSession() as session:
        return await _put_typing(
            session, ctx["typing_url"], ctx["token"], typing, timeout_ms, logger
        )


class TypingIndicatorManager:
    """Typing heartbeat with cached auth. Logs in once, refreshes every 4s."""

    def __init__(self, room_id: str, config: Config, logger: logging.Logger):
        self.room_id = room_id
        self.config = config
        self.logger = logger
        self._typing_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._ctx: Optional[Dict[str, str]] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _typing_loop(self):
        try:
            while not self._stop_event.is_set():
                if self._ctx and self._session:
                    await _put_typing(
                        self._session,
                        self._ctx["typing_url"],
                        self._ctx["token"],
                        True,
                        _TYPING_TIMEOUT_MS,
                        self.logger,
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=_TYPING_HEARTBEAT_INTERVAL,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            if self._ctx and self._session:
                await _put_typing(
                    self._session,
                    self._ctx["typing_url"],
                    self._ctx["token"],
                    False,
                    _TYPING_TIMEOUT_MS,
                    self.logger,
                )

    async def start(self):
        if self._typing_task is not None:
            await self.stop()
        self._stop_event.clear()
        self._ctx = await _get_agent_typing_context(
            self.room_id, self.config, self.logger
        )
        if not self._ctx:
            self.logger.debug(
                f"[TYPING] No agent context for room {self.room_id}, skipping"
            )
            return
        self._session = aiohttp.ClientSession()
        self._typing_task = asyncio.create_task(self._typing_loop())
        self.logger.debug(
            f"[TYPING] Started 4s heartbeat for room {self.room_id}"
        )

    async def stop(self):
        self._stop_event.set()
        if self._typing_task:
            self._typing_task.cancel()
            try:
                await self._typing_task
            except asyncio.CancelledError:
                pass
            self._typing_task = None
        if self._ctx and self._session:
            await _put_typing(
                self._session,
                self._ctx["typing_url"],
                self._ctx["token"],
                False,
                _TYPING_TIMEOUT_MS,
                self.logger,
            )
        if self._session:
            await self._session.close()
            self._session = None
        self._ctx = None
        self.logger.debug(f"[TYPING] Stopped typing for room {self.room_id}")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
        return False
