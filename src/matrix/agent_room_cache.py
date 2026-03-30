"""
Agent-to-room mapping cache — room-level agent mapping lookup with TTL.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from src.core import mapping_service

_ROOM_MAPPING_CACHE_TTL_SECONDS = 45.0
_ROOM_AGENT_MAPPING_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _get_agent_mapping_for_room(
    room_id: str,
    logger: logging.Logger,
) -> Optional[Dict[str, Any]]:
    cached = _ROOM_AGENT_MAPPING_CACHE.get(room_id)
    now = time.monotonic()
    if cached and (now - cached[0]) <= _ROOM_MAPPING_CACHE_TTL_SECONDS:
        return cached[1]

    agent_mapping = mapping_service.get_mapping_by_room_id(room_id)
    if not agent_mapping:
        portal_link = mapping_service.get_portal_link_by_room_id(room_id)
        if portal_link:
            agent_mapping = mapping_service.get_mapping_by_agent_id(portal_link["agent_id"])

    if agent_mapping:
        _ROOM_AGENT_MAPPING_CACHE[room_id] = (now, agent_mapping)
        return agent_mapping

    logger.warning(f"No agent mapping found for room {room_id}")
    return None
