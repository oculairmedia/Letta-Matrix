"""
Reactions and read receipts as agent.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

import aiohttp

from src.matrix.agent_auth import get_agent_token
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


async def send_reaction_as_agent(
    room_id: str,
    event_id: str,
    emoji: str,
    config: Config,
    logger: logging.Logger,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    """Send a reaction (emoji) to a message as the agent user for this room."""
    try:
        agent_mapping = _get_agent_mapping_for_room(room_id, logger)
        agent_id = agent_mapping.get("agent_id") if agent_mapping else None

        if agent_id:
            try:
                pool = get_identity_client_pool(config.homeserver_url)
                reaction_event_id = await pool.react_as_agent(
                    agent_id, room_id, event_id, emoji
                )
                if reaction_event_id:
                    logger.debug(
                        f"[REACTION] Sent {emoji} to {event_id} via pool, event_id: {reaction_event_id}"
                    )
                    return reaction_event_id
            except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
                logger.warning(
                    f"[REACTION] Pool react failed for {agent_id}, falling back to raw HTTP: {e}"
                )

        async with _session_scope(session) as active_session:
            agent_token = await get_agent_token(
                room_id, config, logger, active_session, caller="REACTION"
            )
            if not agent_token:
                return None

            txn_id = str(uuid.uuid4())
            url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.reaction/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            content = {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": event_id,
                    "key": emoji,
                }
            }

            async with active_session.put(
                url, headers=headers, json=content, timeout=_AGENT_SEND_TIMEOUT
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    reaction_event_id = result.get("event_id")
                    logger.debug(
                        f"[REACTION] Sent {emoji} to {event_id}, event_id: {reaction_event_id}"
                    )
                    return reaction_event_id
                else:
                    resp_text = await response.text()
                    logger.warning(
                        f"[REACTION] Failed: {response.status} - {resp_text}"
                    )
                    return None

    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.error(f"[REACTION] Exception: {e}", exc_info=True)
        return None


async def send_read_receipt_as_agent(
    room_id: str,
    event_id: str,
    config: Config,
    logger: logging.Logger,
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    """Send a read receipt for a message as the agent user for this room."""
    try:
        agent_mapping = _get_agent_mapping_for_room(room_id, logger)
        agent_id = agent_mapping.get("agent_id") if agent_mapping else None

        if agent_id:
            try:
                pool = get_identity_client_pool(config.homeserver_url)
                success = await pool.read_receipt_as_agent(agent_id, room_id, event_id)
                if success:
                    logger.debug(
                        f"[READ_RECEIPT] Sent for {event_id} in {room_id} via pool"
                    )
                    return True
            except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
                logger.debug(
                    f"[READ_RECEIPT] Pool receipt failed for {agent_id}, falling back to raw HTTP: {e}"
                )

        async with _session_scope(session) as active_session:
            agent_token = await get_agent_token(
                room_id, config, logger, active_session, caller="READ_RECEIPT"
            )
            if not agent_token:
                return False

            url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/receipt/m.read/{event_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            async with active_session.post(
                url, headers=headers, json={}, timeout=_AGENT_SEND_TIMEOUT
            ) as response:
                if response.status == 200:
                    logger.debug(
                        f"[READ_RECEIPT] Sent for {event_id} in {room_id}"
                    )
                    return True
                else:
                    logger.debug(f"[READ_RECEIPT] Failed: {response.status}")
                    return False

    except _RECOVERABLE_AGENT_ACTION_ERRORS as e:
        logger.debug(f"[READ_RECEIPT] Exception: {e}")
        return False
