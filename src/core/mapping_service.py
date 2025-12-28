"""
Centralized Agent Mapping Service

This module provides a single source of truth for agent-to-Matrix mappings.
All code should use this service instead of reading JSON files directly.

The database (PostgreSQL/SQLite) is the authoritative source.
"""
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Cache for performance - invalidated on writes
_mapping_cache: Optional[Dict[str, dict]] = None
_cache_valid = False


@dataclass
class AgentMappingInfo:
    """Simplified agent mapping info for consumers"""
    agent_id: str
    agent_name: str
    matrix_user_id: str
    matrix_password: str
    room_id: Optional[str]
    room_created: bool
    invitation_status: Dict[str, str]


def _get_db():
    """Get database instance"""
    from src.models.agent_mapping import AgentMappingDB
    return AgentMappingDB()


def invalidate_cache():
    """Invalidate the mapping cache - call after any write operation"""
    global _cache_valid
    _cache_valid = False


def get_all_mappings() -> Dict[str, dict]:
    """
    Get all agent mappings as a dictionary.
    
    Returns:
        Dict mapping agent_id to mapping data (compatible with old JSON format)
    """
    global _mapping_cache, _cache_valid
    
    if _cache_valid and _mapping_cache is not None:
        return _mapping_cache
    
    try:
        db = _get_db()
        _mapping_cache = db.export_to_dict()
        _cache_valid = True
        return _mapping_cache
    except Exception as e:
        logger.error(f"Error loading mappings from database: {e}")
        return {}


def get_mapping_by_agent_id(agent_id: str) -> Optional[dict]:
    """
    Get mapping for a specific agent by agent ID.
    
    Args:
        agent_id: The Letta agent ID (e.g., "agent-xxx-xxx")
        
    Returns:
        Mapping dict or None if not found
    """
    try:
        db = _get_db()
        mapping = db.get_by_agent_id(agent_id)
        if mapping:
            return mapping.to_dict()
        return None
    except Exception as e:
        logger.error(f"Error getting mapping for agent {agent_id}: {e}")
        return None


def get_mapping_by_room_id(room_id: str) -> Optional[dict]:
    """
    Get mapping for a specific room.
    
    Args:
        room_id: The Matrix room ID (e.g., "!xxx:matrix.domain")
        
    Returns:
        Mapping dict or None if not found
    """
    try:
        db = _get_db()
        mapping = db.get_by_room_id(room_id)
        if mapping:
            return mapping.to_dict()
        return None
    except Exception as e:
        logger.error(f"Error getting mapping for room {room_id}: {e}")
        return None


def get_mapping_by_matrix_user(matrix_user_id: str) -> Optional[dict]:
    """
    Get mapping for a specific Matrix user.
    
    Args:
        matrix_user_id: The Matrix user ID (e.g., "@agent_xxx:matrix.domain")
        
    Returns:
        Mapping dict or None if not found
    """
    try:
        db = _get_db()
        mapping = db.get_by_matrix_user(matrix_user_id)
        if mapping:
            return mapping.to_dict()
        return None
    except Exception as e:
        logger.error(f"Error getting mapping for user {matrix_user_id}: {e}")
        return None


def upsert_mapping(
    agent_id: str,
    agent_name: str,
    matrix_user_id: str,
    matrix_password: str,
    room_id: Optional[str] = None,
    room_created: bool = False
) -> Optional[dict]:
    """
    Create or update an agent mapping.
    
    Args:
        agent_id: The Letta agent ID
        agent_name: Display name for the agent
        matrix_user_id: Matrix user ID for this agent
        matrix_password: Password for the Matrix user
        room_id: Optional room ID if room has been created
        room_created: Whether the room has been created
        
    Returns:
        Updated mapping dict or None on error
    """
    try:
        db = _get_db()
        mapping = db.upsert(
            agent_id=agent_id,
            agent_name=agent_name,
            matrix_user_id=matrix_user_id,
            matrix_password=matrix_password,
            room_id=room_id,
            room_created=room_created
        )
        invalidate_cache()
        return mapping.to_dict() if mapping else None
    except Exception as e:
        logger.error(f"Error upserting mapping for agent {agent_id}: {e}")
        return None


def update_invitation_status(agent_id: str, invitee: str, status: str) -> bool:
    """
    Update invitation status for an agent-invitee pair.
    
    Args:
        agent_id: The Letta agent ID
        invitee: Matrix user ID of the invitee
        status: Status string (e.g., "joined", "failed", "pending")
        
    Returns:
        True on success, False on error
    """
    try:
        db = _get_db()
        db.update_invitation_status(agent_id, invitee, status)
        invalidate_cache()
        return True
    except Exception as e:
        logger.error(f"Error updating invitation status: {e}")
        return False


def delete_mapping(agent_id: str) -> bool:
    """
    Delete an agent mapping.
    
    Args:
        agent_id: The Letta agent ID to delete
        
    Returns:
        True if deleted, False if not found or error
    """
    try:
        db = _get_db()
        result = db.delete(agent_id)
        if result:
            invalidate_cache()
        return result
    except Exception as e:
        logger.error(f"Error deleting mapping for agent {agent_id}: {e}")
        return False


def get_agents_without_rooms() -> List[dict]:
    """
    Get all agents that don't have rooms created yet.
    
    Returns:
        List of mapping dicts for agents without rooms
    """
    mappings = get_all_mappings()
    return [
        m for m in mappings.values()
        if not m.get("room_id") or not m.get("room_created")
    ]


def get_agents_with_rooms() -> List[dict]:
    """
    Get all agents that have rooms created.
    
    Returns:
        List of mapping dicts for agents with rooms
    """
    mappings = get_all_mappings()
    return [
        m for m in mappings.values()
        if m.get("room_id") and m.get("room_created")
    ]
