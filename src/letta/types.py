"""
Type definitions for Letta SDK integration.

Re-exports commonly used types from the letta_client package
for easier imports throughout the application.
"""

from typing import List, Optional, Dict, Any, Union
from enum import Enum

# Re-export SDK types
from letta_client.types import (
    AgentState,
    AgentType,
    AgentCreateParams,
    AgentUpdateParams,
)

# Custom type aliases for our application
AgentId = str
RoomId = str
UserId = str


class MessageRole(str, Enum):
    """Message role types."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageType(str, Enum):
    """Letta message types."""
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    SYSTEM_MESSAGE = "system_message"
    TOOL_CALL_MESSAGE = "tool_call_message"
    TOOL_RETURN_MESSAGE = "tool_return_message"
    REASONING_MESSAGE = "reasoning_message"
    HEARTBEAT = "heartbeat"


# Type aliases for responses
AgentResponse = AgentState
MessageResponse = Dict[str, Any]


def is_assistant_message(message: Dict[str, Any]) -> bool:
    """Check if a message is an assistant message."""
    msg_type = message.get("message_type", "")
    return msg_type == MessageType.ASSISTANT_MESSAGE.value


def is_tool_call(message: Dict[str, Any]) -> bool:
    """Check if a message is a tool call."""
    msg_type = message.get("message_type", "")
    return msg_type == MessageType.TOOL_CALL_MESSAGE.value


def extract_assistant_content(messages: List[Dict[str, Any]]) -> Optional[str]:
    """
    Extract the assistant's text content from a list of messages.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        The assistant's message content or None
    """
    for message in messages:
        if is_assistant_message(message):
            content = message.get("content")
            if isinstance(content, str):
                return content
            elif isinstance(content, dict):
                return content.get("text", str(content))
    return None


def extract_tool_calls(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extract all tool calls from a list of messages.
    
    Args:
        messages: List of message dictionaries
        
    Returns:
        List of tool call messages
    """
    return [m for m in messages if is_tool_call(m)]


__all__ = [
    # SDK types
    "AgentState",
    "AgentType", 
    "AgentCreateParams",
    "AgentUpdateParams",
    # Custom types
    "AgentId",
    "RoomId",
    "UserId",
    "MessageRole",
    "MessageType",
    "AgentResponse",
    "MessageResponse",
    # Helper functions
    "is_assistant_message",
    "is_tool_call",
    "extract_assistant_content",
    "extract_tool_calls",
]
