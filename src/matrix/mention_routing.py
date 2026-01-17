"""
@mention-based agent routing for inter-agent communication.

This module provides functions to:
1. Extract @agent mentions from message bodies
2. Forward messages to mentioned agents' rooms
3. Handle the routing integration in message callbacks
"""
import re
import logging
from typing import List, Tuple, Optional

from src.core.mapping_service import (
    get_mapping_by_agent_id,
    get_mapping_by_matrix_user,
    get_all_mappings,
)

logger = logging.getLogger(__name__)


def get_mapping_by_agent_name(name: str, fuzzy: bool = True) -> Optional[dict]:
    """
    Get mapping for an agent by display name.
    
    This is a temporary stub - will be moved to mapping_service.py
    """
    # TODO: Implement in mapping_service.py
    raise NotImplementedError("get_mapping_by_agent_name not yet implemented")


def strip_reply_fallback(body: str) -> str:
    """
    Remove Matrix reply quoted content, keeping only the new message.
    
    Matrix replies have format:
    > <@user:domain> original message
    > continued quote
    
    Actual new message here
    """
    # TODO: Implement
    raise NotImplementedError("strip_reply_fallback not yet implemented")


def extract_agent_mentions(body: str) -> List[Tuple[str, str, str]]:
    """
    Extract @agent mentions from message body.
    
    Returns list of (matched_text, agent_id, agent_name) tuples.
    """
    # TODO: Implement
    raise NotImplementedError("extract_agent_mentions not yet implemented")


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
    # TODO: Implement
    raise NotImplementedError("forward_to_agent_room not yet implemented")


async def handle_agent_mention_routing(
    room,
    event,
    sender_mxid: str,
    sender_agent_id: str,
    sender_agent_name: str,
    config,
    logger,
) -> None:
    """
    Check for @agent mentions and forward to mentioned agents' rooms.
    """
    # TODO: Implement
    raise NotImplementedError("handle_agent_mention_routing not yet implemented")
