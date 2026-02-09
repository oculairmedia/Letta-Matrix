"""
@mention-based agent routing for inter-agent communication.

This module provides functions to:
1. Extract @agent mentions from message bodies
2. Extract @oc_* OpenCode mentions from message bodies
3. Forward messages to mentioned agents' or OpenCode rooms
4. Handle the routing integration in message callbacks
"""
import os
import re
import logging
from typing import Any, List, Tuple, Optional

import aiohttp

from src.core.mapping_service import (
    get_mapping_by_agent_id,
    get_mapping_by_matrix_user,
    get_mapping_by_agent_name,
    get_all_mappings,
)

logger = logging.getLogger(__name__)

# Regex for Matrix user IDs: @localpart:domain
MXID_PATTERN = re.compile(r'@([a-zA-Z0-9._=\-/]+):([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})')

# Regex for friendly @mentions - single word only (multi-word names matched via partial/fuzzy)
FRIENDLY_MENTION_PATTERN = re.compile(r'@([A-Za-z][A-Za-z0-9_\-]*)')

# Special pattern for "Huly - ProjectName" style (with hyphen separator)  
HULY_MENTION_PATTERN = re.compile(r'@(Huly\s*-\s*[A-Za-z][A-Za-z0-9_\-\s]*)', re.IGNORECASE)

# Agent MXID pattern: @agent_UUID_parts:domain
AGENT_MXID_PATTERN = re.compile(r'@(agent_[0-9a-f_]+):([a-zA-Z0-9.\-]+)')

# OpenCode MXID pattern: @oc_projectname_v2:domain
OC_MXID_PATTERN = re.compile(r'@(oc_[a-zA-Z0-9_]+):([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})')

OPENCODE_BRIDGE_URL = os.environ.get("OPENCODE_BRIDGE_URL", "http://192.168.50.90:3201")


def strip_reply_fallback(body: str) -> str:
    """
    Remove Matrix reply quoted content, keeping only the new message.
    
    Matrix replies have format:
    > <@user:domain> original message
    > continued quote
    
    Actual new message here
    """
    if not body.startswith('>'):
        return body
    
    lines = body.split('\n')
    new_message_lines = []
    past_quote = False
    
    for line in lines:
        if past_quote:
            new_message_lines.append(line)
        elif not line.startswith('>'):
            past_quote = True
            if line.strip():
                new_message_lines.append(line)
    
    return '\n'.join(new_message_lines).strip()


def extract_agent_mentions(body: str) -> List[Tuple[str, str, str]]:
    """
    Extract @agent mentions from message body.
    
    Returns list of (matched_text, agent_id, agent_name) tuples.
    Strips reply fallback before parsing to avoid matching quoted content.
    Deduplicates by agent_id.
    """
    clean_body = strip_reply_fallback(body)
    found_agents: dict[str, Tuple[str, str, str]] = {}
    
    # 1. Check for full Matrix user ID mentions (@agent_xxx:domain)
    for match in AGENT_MXID_PATTERN.finditer(clean_body):
        full_mxid = match.group(0)
        mapping = get_mapping_by_matrix_user(full_mxid)
        if mapping:
            agent_id = mapping["agent_id"]
            if agent_id not in found_agents:
                found_agents[agent_id] = (full_mxid, agent_id, mapping["agent_name"])
    
    # 2. Check for friendly @Name mentions
    for match in FRIENDLY_MENTION_PATTERN.finditer(clean_body):
        name = match.group(1)
        full_match = match.group(0)
        
        # Skip if it looks like an email (has @ before and text after with @)
        start = match.start()
        if start > 0 and clean_body[start-1].isalnum():
            continue
            
        mapping = get_mapping_by_agent_name(name, fuzzy=True)
        if mapping:
            agent_id = mapping["agent_id"]
            if agent_id not in found_agents:
                found_agents[agent_id] = (full_match, agent_id, mapping["agent_name"])
    
    return list(found_agents.values())


def extract_opencode_mentions(body: str) -> List[str]:
    clean_body = strip_reply_fallback(body)
    found: set[str] = set()
    for match in OC_MXID_PATTERN.finditer(clean_body):
        found.add(match.group(0))
    return list(found)


async def resolve_opencode_room(mxid: str) -> Optional[Tuple[str, str]]:
    """Returns (room_id, directory) for an OpenCode MXID, or None."""
    localpart = mxid.split(":")[0].lstrip("@")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{OPENCODE_BRIDGE_URL}/registrations",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
    except Exception as e:
        logger.error(f"Failed to query opencode-bridge for {mxid}: {e}")
        return None

    for reg in data.get("registrations", []):
        directory = reg.get("directory", "")
        rooms = reg.get("rooms", [])
        if not rooms:
            continue
        slug = directory.rstrip("/").split("/")[-1].lower().replace("-", "_")
        if localpart == f"oc_{slug}_v2":
            return (rooms[0], directory)
    return None


async def forward_to_opencode_room(
    target_room_id: str,
    message: str,
    sender_agent_name: str,
    opencode_mxid: str,
    source_room_id: str,
    original_event_id: str,
    admin_client: Any,
) -> Optional[str]:
    try:
        body = message if opencode_mxid in message else f"{opencode_mxid} {message}"
        content = {
            "msgtype": "m.text",
            "body": body,
            "m.forwarded": True,
            "m.forward_source": {
                "room_id": source_room_id,
                "event_id": original_event_id,
                "sender_name": sender_agent_name,
            },
        }
        from nio import RoomSendResponse, RoomSendError

        response = await admin_client.room_send(
            room_id=target_room_id,
            message_type="m.room.message",
            content=content,
        )
        if isinstance(response, RoomSendResponse):
            logger.info(f"Forwarded to OpenCode room {target_room_id}: {response.event_id}")
            return response.event_id
        elif isinstance(response, RoomSendError):
            logger.error(f"Failed to forward to OpenCode room: {response.message}")
            return None
        return None
    except Exception as e:
        logger.error(f"Error forwarding to OpenCode room: {e}")
        return None


async def forward_to_agent_room(
    source_room_id: str,
    target_room_id: str,
    message: str,
    sender_mxid: str,
    sender_agent_name: str,
    target_agent_name: str,
    original_event_id: str,
    config,
    logger,
) -> Optional[str]:
    """
    Forward a message to another agent's room.
    
    Returns event_id of forwarded message, or None on failure.
    """
    from src.matrix.identity_client_pool import get_identity_client_pool
    
    try:
        pool = get_identity_client_pool(config.homeserver_url)
        
        sender_mapping = get_mapping_by_matrix_user(sender_mxid)
        if not sender_mapping:
            logger.error(f"Cannot forward: no mapping for sender {sender_mxid}")
            return None
        
        sender_agent_id = sender_mapping["agent_id"]
        identity_id = f"letta_{sender_agent_id}"
        
        client = await pool.get_client(identity_id)
        if not client:
            logger.error(f"Cannot forward: no client for identity {identity_id}")
            return None
        
        content = {
            "msgtype": "m.text",
            "body": f"[Forwarded from {sender_agent_name}]\n\n{message}",
            "m.forwarded": True,
            "m.forward_source": {
                "room_id": source_room_id,
                "event_id": original_event_id,
                "sender": sender_mxid,
                "sender_name": sender_agent_name,
            },
        }
        
        response = await client.room_send(
            room_id=target_room_id,
            message_type="m.room.message",
            content=content,
        )
        
        from nio import RoomSendResponse, RoomSendError
        if isinstance(response, RoomSendResponse):
            logger.info(f"Forwarded message to {target_agent_name}'s room: {response.event_id}")
            return response.event_id
        elif isinstance(response, RoomSendError):
            logger.error(f"Failed to forward message: {response.message}")
            return None
        else:
            logger.error(f"Unexpected response type: {type(response)}")
            return None
            
    except Exception as e:
        logger.error(f"Error forwarding to {target_agent_name}'s room: {e}")
        return None


async def handle_agent_mention_routing(
    room,
    event,
    sender_mxid: str,
    sender_agent_id: str,
    sender_agent_name: str,
    config,
    logger,
    admin_client: Any = None,
) -> None:
    content = event.source.get("content", {})
    if content.get("m.forwarded"):
        logger.debug(f"Skipping already-forwarded message {event.event_id}")
        return
    
    body = getattr(event, "body", "")
    if not body:
        return
    
    # Agent-to-agent routing
    agent_mentions = extract_agent_mentions(body)
    for matched_text, target_agent_id, target_agent_name in agent_mentions:
        if target_agent_id == sender_agent_id:
            continue
        
        target_mapping = get_mapping_by_agent_id(target_agent_id)
        if not target_mapping:
            logger.warning(f"No mapping found for mentioned agent {target_agent_id}")
            continue
        
        target_room_id = target_mapping.get("room_id")
        if not target_room_id:
            logger.warning(f"No room_id for agent {target_agent_name}")
            continue
        
        logger.info(f"Forwarding message to {target_agent_name}'s room {target_room_id}")
        
        event_id = await forward_to_agent_room(
            source_room_id=room.room_id,
            target_room_id=target_room_id,
            message=body,
            sender_mxid=sender_mxid,
            sender_agent_name=sender_agent_name,
            target_agent_name=target_agent_name,
            original_event_id=event.event_id,
            config=config,
            logger=logger,
        )
        
        if event_id:
            logger.info(f"Forwarded to {target_agent_name}: {event_id}")
        else:
            logger.error(f"Failed to forward to {target_agent_name}")

    # Agent-to-OpenCode routing
    if not admin_client:
        return

    oc_mentions = extract_opencode_mentions(body)
    for oc_mxid in oc_mentions:
        resolved = await resolve_opencode_room(oc_mxid)
        if not resolved:
            logger.debug(f"No active OpenCode instance for {oc_mxid}")
            continue

        target_room_id, directory = resolved
        if target_room_id == room.room_id:
            logger.debug(f"Skipping OpenCode mention in own room: {oc_mxid}")
            continue

        logger.info(f"Forwarding to OpenCode {oc_mxid} room {target_room_id} (dir={directory})")

        event_id = await forward_to_opencode_room(
            target_room_id=target_room_id,
            message=body,
            sender_agent_name=sender_agent_name,
            opencode_mxid=oc_mxid,
            source_room_id=room.room_id,
            original_event_id=event.event_id,
            admin_client=admin_client,
        )

        if event_id:
            logger.info(f"Forwarded to OpenCode {oc_mxid}: {event_id}")
        else:
            logger.error(f"Failed to forward to OpenCode {oc_mxid}")
