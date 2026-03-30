"""
Edit and delete messages as agent.
"""

import asyncio
import logging
import uuid
from typing import Optional

import aiohttp

from src.matrix.agent_auth import get_agent_token
from src.matrix.agent_message_content import _build_edit_content
from src.matrix.agent_room_cache import _get_agent_mapping_for_room
from src.matrix.agent_send import _session_scope
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


async def delete_message_as_agent(
    room_id: str,
    event_id: str,
    config: Config,
    logger: logging.Logger,
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    """Redact (delete) a message as the agent user for this room."""
    try:
        agent_mapping = _get_agent_mapping_for_room(room_id, logger)
        if not agent_mapping:
            return False

        agent_id = agent_mapping.get("agent_id")
        agent_name = agent_mapping.get("agent_name", "Unknown")
        logger.debug(
            f"[DELETE_AS_AGENT] Attempting to delete message as agent: {agent_name} in room {room_id}"
        )

        if agent_id:
            try:
                pool = get_identity_client_pool(config.homeserver_url)
                success = await pool.redact_as_agent(
                    agent_id, room_id, event_id, reason="Progress message replaced"
                )
                if success:
                    logger.debug(
                        f"[DELETE_AS_AGENT] Successfully deleted message {event_id} via pool"
                    )
                    return True
            except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
                logger.warning(
                    f"[DELETE_AS_AGENT] Pool redact failed for {agent_id}, falling back to raw HTTP: {e}"
                )

        async with _session_scope(session) as active_session:
            agent_token = await get_agent_token(
                room_id, config, logger, active_session, caller="DELETE_AS_AGENT"
            )
            if not agent_token:
                return False

            txn_id = str(uuid.uuid4())
            redact_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            redact_data = {"reason": "Progress message replaced"}

            async with active_session.put(
                redact_url,
                headers=headers,
                json=redact_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as response:
                if response.status == 200:
                    logger.debug(
                        f"[DELETE_AS_AGENT] Successfully deleted message {event_id}"
                    )
                    return True
                else:
                    response_text = await response.text()
                    logger.warning(
                        f"[DELETE_AS_AGENT] Failed to delete message: {response.status} - {response_text}"
                    )
                    return False

    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.error(f"[DELETE_AS_AGENT] Exception occurred: {e}", exc_info=True)
        return False


async def edit_message_as_agent(
    room_id: str,
    event_id: str,
    new_body: str,
    config: Config,
    logger: logging.Logger,
    msgtype: str = "m.text",
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    """Edit a message as the agent user for this room."""
    try:
        agent_mapping = _get_agent_mapping_for_room(room_id, logger)
        agent_id = agent_mapping.get("agent_id") if agent_mapping else None

        message_data = _build_edit_content(event_id, new_body, msgtype)

        if agent_id:
            try:
                pool = get_identity_client_pool(config.homeserver_url)
                edit_event_id = await pool._send_with_recovery(
                    f"letta_{agent_id}", room_id, message_data
                )
                if edit_event_id:
                    logger.debug(
                        f"[EDIT_AS_AGENT] Edited message {event_id} via pool"
                    )
                    return True
            except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
                logger.warning(
                    f"[EDIT_AS_AGENT] Pool edit failed for {agent_id}, falling back to raw HTTP: {e}"
                )

        async with _session_scope(session) as active_session:
            agent_token = await get_agent_token(
                room_id, config, logger, active_session, caller="EDIT_AS_AGENT"
            )
            if not agent_token:
                return False

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            async with active_session.put(
                msg_url,
                headers=headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as response:
                if response.status == 200:
                    logger.debug(f"[EDIT_AS_AGENT] Edited message {event_id}")
                    return True
                else:
                    resp_text = await response.text()
                    logger.warning(
                        f"[EDIT_AS_AGENT] Edit failed: {response.status} - {resp_text}"
                    )
                    return False

    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.error(f"[EDIT_AS_AGENT] Exception: {e}", exc_info=True)
        return False
