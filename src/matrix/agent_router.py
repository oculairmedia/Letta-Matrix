"""Agent routing and conversation resolution helpers for Matrix rooms."""

import asyncio
import logging
from typing import Optional, Tuple

import aiohttp
from sqlalchemy.exc import SQLAlchemyError

from src.matrix.config import Config


_router_session: Optional[aiohttp.ClientSession] = None
_router_session_lock = asyncio.Lock()


async def _get_router_session() -> aiohttp.ClientSession:
    global _router_session
    if _router_session is not None and not _router_session.closed:
        return _router_session

    async with _router_session_lock:
        if _router_session is not None and not _router_session.closed:
            return _router_session
        connector = aiohttp.TCPConnector(
            limit=50,
            limit_per_host=20,
            ttl_dns_cache=300,
            keepalive_timeout=30,
        )
        _router_session = aiohttp.ClientSession(connector=connector)
        return _router_session


async def get_agent_from_room_members(
    room_id: str, config: Config, logger: logging.Logger
) -> Optional[tuple]:
    """
    Extract agent ID from room members by finding agent Matrix users.
    Returns (agent_id, agent_name) or None if not found.
    """
    try:
        admin_token = config.matrix_token
        members_url = (
            f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/members"
        )
        headers = {"Authorization": f"Bearer {admin_token}"}

        session = await _get_router_session()
        async with session.get(members_url, headers=headers) as resp:
            if resp.status != 200:
                logger.warning(f"Failed to get room members: {resp.status}")
                return None

            members_data = await resp.json()
            members = members_data.get("chunk", [])

            from src.models.agent_mapping import AgentMappingDB

            db = AgentMappingDB()
            all_mappings = db.get_all()

            for member in members:
                user_id = member.get("state_key")
                if not user_id:
                    continue
                for mapping in all_mappings:
                    if mapping.matrix_user_id == user_id:
                        logger.info(
                            f"Found agent via room members: {mapping.agent_name} ({mapping.agent_id})"
                        )
                        return (mapping.agent_id, mapping.agent_name)

            logger.warning(
                f"No agent users found in room {room_id} members"
            )
            return None

    except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, SQLAlchemyError) as e:
        logger.warning(
            f"Error extracting agent from room members: {e}", exc_info=True
        )
        return None


async def _resolve_agent_for_room(
    room_id: str, config: Config, logger: logging.Logger, tag: str = ""
) -> Tuple[str, str]:
    """
    Resolve (agent_id, agent_name) for a room.
    Falls back through: DB mapping → portal links → room members → default.
    """
    agent_id_to_use = config.letta_agent_id
    agent_name_found = "DEFAULT"

    if room_id:
        try:
            from src.models.agent_mapping import AgentMappingDB

            db = AgentMappingDB()
            mapping = db.get_by_room_id(room_id)
            if mapping:
                agent_id_to_use = str(mapping.agent_id)
                agent_name_found = str(mapping.agent_name)
                if tag:
                    logger.info(
                        f"[{tag}] Found agent mapping: {agent_name_found} ({agent_id_to_use})"
                    )
            else:
                portal_link = db.get_portal_link_by_room_id(room_id)
                if portal_link:
                    portal_mapping = db.get_by_agent_id(portal_link["agent_id"])
                    if portal_mapping:
                        agent_id_to_use = str(portal_mapping.agent_id)
                        agent_name_found = str(portal_mapping.agent_name)
                        if tag:
                            logger.info(
                                f"[{tag}] Portal link match: {agent_name_found} ({agent_id_to_use})"
                            )
                    else:
                        member_result = await get_agent_from_room_members(
                            room_id, config, logger
                        )
                        if member_result:
                            agent_id_to_use, agent_name_found = member_result
                else:
                    member_result = await get_agent_from_room_members(
                        room_id, config, logger
                    )
                    if member_result:
                        agent_id_to_use, agent_name_found = member_result
        except (ImportError, RuntimeError, ValueError, SQLAlchemyError) as e:
            logger.warning(f"[{tag}] Could not query agent mappings: {e}")

    return agent_id_to_use, agent_name_found


async def _resolve_conversation_id(
    config: Config,
    room_id: str,
    agent_id: str,
    sender_id: str,
    room_member_count: int,
    logger: logging.Logger,
) -> Optional[str]:
    """Resolve or create a conversation_id for context isolation."""
    if not config.letta_conversations_enabled:
        return None
    try:
        from src.letta.client import get_letta_client, LettaConfig

        sdk_config = LettaConfig(
            base_url=config.letta_api_url,
            api_key=config.letta_token,
            timeout=config.letta_streaming_timeout,
            max_retries=3,
        )
        letta_client = get_letta_client(sdk_config)
        from src.core.conversation_service import get_conversation_service

        conv_service = get_conversation_service(letta_client)
        conversation_id, created = await conv_service.get_or_create_room_conversation(
            room_id=room_id,
            agent_id=agent_id,
            room_member_count=room_member_count,
            user_mxid=sender_id if room_member_count == 2 else None,
        )
        logger.info(
            f"[CONVERSATIONS] Using conversation {conversation_id} (created={created})"
        )
        return conversation_id
    except (ImportError, RuntimeError, ValueError, TypeError, aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning(
            f"[CONVERSATIONS] Failed to get conversation, falling back to agents API: {e}"
        )
        return None
