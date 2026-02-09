"""Letta SDK integration module."""

from src.letta.client import (
    LettaConfig,
    LettaService,
    get_letta_client,
    get_letta_service,
    reset_client,
    Agent,
)

from src.letta.types import (
    MessageRole,
    MessageType,
    AgentId,
    RoomId,
    UserId,
    is_assistant_message,
    is_tool_call,
    extract_assistant_content,
    extract_tool_calls,
)

__all__ = [
    # Client
    "LettaConfig",
    "LettaService", 
    "get_letta_client",
    "get_letta_service",
    "reset_client",
    "Agent",
    # Types
    "MessageRole",
    "MessageType",
    "AgentId",
    "RoomId",
    "UserId",
    # Helpers
    "is_assistant_message",
    "is_tool_call",
    "extract_assistant_content",
    "extract_tool_calls",
]
