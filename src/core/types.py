#!/usr/bin/env python3
"""
Shared type definitions for the core module.

This module contains dataclasses and types used across multiple core modules
to avoid circular import issues.
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class AgentUserMapping:
    """Data class for agent-to-user mappings.
    
    This is the canonical definition used by:
    - AgentUserManager
    - MatrixRoomManager
    """
    agent_id: str
    agent_name: str
    matrix_user_id: str
    matrix_password: str
    created: bool = False
    room_id: Optional[str] = None
    room_created: bool = False
    invitation_status: Optional[Dict[str, str]] = None
