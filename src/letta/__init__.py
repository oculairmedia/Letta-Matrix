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

from src.letta.webhook_handler import (
    LettaWebhookHandler,
    LettaWebhookPayload,
    WebhookConfig,
    WebhookResult,
    initialize_webhook_handler,
    get_webhook_handler,
    extract_content_text,
    extract_user_content,
    extract_assistant_content as extract_webhook_assistant_content,
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
    # Webhook
    "LettaWebhookHandler",
    "LettaWebhookPayload",
    "WebhookConfig",
    "WebhookResult",
    "initialize_webhook_handler",
    "get_webhook_handler",
    "extract_content_text",
    "extract_user_content",
    "extract_webhook_assistant_content",
]
