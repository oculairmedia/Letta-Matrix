"""
Send messages as agent — send_as_agent and send_as_agent_with_event_id.
"""

import asyncio
from contextlib import asynccontextmanager
import logging
import uuid
from typing import AsyncIterator, Optional

import aiohttp

from src.matrix.agent_auth import get_agent_token
from src.matrix.agent_message_content import _build_message_content
from src.matrix.agent_room_cache import _get_agent_mapping_for_room
from src.matrix.config import Config
from src.matrix.identity_client_pool import get_identity_client_pool

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


@asynccontextmanager
async def _session_scope(
    session: Optional[aiohttp.ClientSession] = None,
) -> AsyncIterator[aiohttp.ClientSession]:
    """Context manager: yield provided session or create a temporary one."""
    if session is not None:
        yield session
        return
    async with aiohttp.ClientSession() as owned_session:
        yield owned_session


async def send_as_agent_with_event_id(
    room_id: str,
    message: str,
    config: Config,
    logger: logging.Logger,
    msgtype: str = "m.text",
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
    reply_to_body: Optional[str] = None,
    thread_event_id: Optional[str] = None,
    thread_latest_event_id: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    """Send a message as the agent user for this room and return the event ID."""
    try:
        agent_mapping = _get_agent_mapping_for_room(room_id, logger)
        if not agent_mapping:
            return None

        agent_id = agent_mapping.get("agent_id")
        agent_name = agent_mapping.get("agent_name", "Unknown")
        logger.debug(
            f"[SEND_AS_AGENT] Sending as agent: {agent_name} in room {room_id}"
        )

        message_data = _build_message_content(
            message,
            msgtype,
            reply_to_event_id,
            reply_to_sender,
            reply_to_body,
            room_id,
            thread_event_id,
            thread_latest_event_id,
        )

        if reply_to_event_id:
            logger.debug(
                f"[SEND_AS_AGENT] Threading response under event {reply_to_event_id}"
            )

        if agent_id:
            try:
                pool = get_identity_client_pool(config.homeserver_url)
                event_id = await pool._send_with_recovery(
                    f"letta_{agent_id}", room_id, message_data
                )
                if event_id:
                    logger.debug(
                        f"[SEND_AS_AGENT] Sent message via pool, event_id: {event_id}"
                        + (
                            f" (reply to {reply_to_event_id})"
                            if reply_to_event_id
                            else ""
                        )
                    )
                    return event_id
            except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
                logger.warning(
                    f"[SEND_AS_AGENT] Pool send failed for {agent_id}, falling back to raw HTTP: {e}"
                )

        async with _session_scope(session) as active_session:
            agent_token = await get_agent_token(
                room_id, config, logger, active_session, caller="SEND_AS_AGENT"
            )
            if not agent_token:
                return None

            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            async with active_session.put(
                message_url,
                headers=headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    event_id = result.get("event_id")
                    logger.debug(
                        f"[SEND_AS_AGENT] Sent message, event_id: {event_id}"
                        + (
                            f" (reply to {reply_to_event_id})"
                            if reply_to_event_id
                            else ""
                        )
                    )
                    return event_id
                else:
                    response_text = await response.text()
                    logger.error(
                        f"[SEND_AS_AGENT] Failed to send message: {response.status} - {response_text}"
                    )
                    return None

    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.error(f"[SEND_AS_AGENT] Exception occurred: {e}", exc_info=True)
        return None


async def send_as_agent(
    room_id: str,
    message: str,
    config: Config,
    logger: logging.Logger,
    msgtype: str = "m.text",
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
    thread_event_id: Optional[str] = None,
    thread_latest_event_id: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    """Send a message as the agent user for this room. Returns True on success."""
    event_id = await send_as_agent_with_event_id(
        room_id,
        message,
        config,
        logger,
        msgtype=msgtype,
        reply_to_event_id=reply_to_event_id,
        reply_to_sender=reply_to_sender,
        thread_event_id=thread_event_id,
        thread_latest_event_id=thread_latest_event_id,
        session=session,
    )
    return event_id is not None
