"""
Core module for Letta-Matrix integration

Contains:
- AgentUserManager: Orchestrates agent-to-user synchronization
- MatrixUserManager: Manages Matrix user accounts (Sprint 3)
- MatrixSpaceManager: Manages Matrix spaces (Sprint 2)
"""

from .agent_user_manager import AgentUserManager, AgentUserMapping
from .user_manager import MatrixUserManager
from .space_manager import MatrixSpaceManager

__all__ = [
    "AgentUserManager",
    "AgentUserMapping",
    "MatrixUserManager",
    "MatrixSpaceManager",
]
