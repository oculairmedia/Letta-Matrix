"""
Letta Webhook Handler

Handles webhooks from Letta server for agent.run.completed events.
Replaces polling-based monitoring with push notifications.

Webhook payload structure (WebhookEvent from Letta):
{
    "id": "evt-xxx",
    "event_type": "agent.run.completed",
    "timestamp": "ISO8601",
    "agent_id": "agent-xxx",
    "organization_id": "org-xxx",
    "data": {
        "run_id": "run-xxx",
        "stop_reason": {...},
        "usage": {...},
        "message_count": N,
        "messages": [...]
    }
}
"""

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Union

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Track active Matrix conversations to avoid duplicate audit messages
# When matrix-client sends a message to Letta, it registers the agent_id here
_active_matrix_conversations: Dict[str, Dict[str, Any]] = {}
CONVERSATION_TTL_SECONDS = 300


def register_matrix_conversation(agent_id: str, opencode_sender: Optional[str] = None) -> None:
    existing = _active_matrix_conversations.get(agent_id)
    if existing and opencode_sender is None:
        opencode_sender = existing.get("opencode_sender")
    _active_matrix_conversations[agent_id] = {
        "registered_at": datetime.now(),
        "opencode_sender": opencode_sender
    }
    logger.debug(f"[Webhook] Registered Matrix conversation for agent {agent_id}, opencode_sender={opencode_sender}")


def is_matrix_conversation(agent_id: str) -> bool:
    if agent_id not in _active_matrix_conversations:
        return False
    conv = _active_matrix_conversations[agent_id]
    age = (datetime.now() - conv["registered_at"]).total_seconds()
    if age > CONVERSATION_TTL_SECONDS:
        del _active_matrix_conversations[agent_id]
        return False
    return True


def get_opencode_sender(agent_id: str) -> Optional[str]:
    conv = _active_matrix_conversations.get(agent_id)
    if conv:
        return conv.get("opencode_sender")
    return None


def clear_matrix_conversation(agent_id: str) -> None:
    _active_matrix_conversations.pop(agent_id, None)


# ============================================================================
# Pydantic Models for Webhook Payloads
# ============================================================================

class LettaContentPart(BaseModel):
    """Content part in array format (Letta v1 style)."""
    type: str
    text: Optional[str] = None
    # Allow additional fields
    class Config:
        extra = "allow"


class LettaMessage(BaseModel):
    """A message in the Letta webhook payload."""
    message_type: str
    content: Optional[Union[str, List[Dict[str, Any]], Dict[str, Any]]] = None
    role: Optional[str] = None
    date: Optional[str] = None
    assistant_message: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    
    class Config:
        extra = "allow"


class LettaRunData(BaseModel):
    """Data payload for agent.run.completed event."""
    run_id: Optional[str] = None
    stop_reason: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    message_count: Optional[int] = None
    messages: Optional[List[LettaMessage]] = None
    
    class Config:
        extra = "allow"


class LettaWebhookPayload(BaseModel):
    """Full webhook payload from Letta."""
    id: Optional[str] = None
    event_type: str
    agent_id: str
    organization_id: Optional[str] = None
    timestamp: Optional[str] = None
    data: Optional[LettaRunData] = None
    
    class Config:
        extra = "allow"


@dataclass
class WebhookResult:
    """Result of processing a webhook."""
    success: bool
    response_posted: bool
    response_content: Optional[str] = None
    error: Optional[str] = None
    agent_id: Optional[str] = None
    room_id: Optional[str] = None


@dataclass
class WebhookConfig:
    """Configuration for webhook handler."""
    webhook_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("LETTA_WEBHOOK_SECRET")
    )
    skip_verification: bool = field(
        default_factory=lambda: os.getenv("NODE_ENV") == "development"
    )
    matrix_api_url: str = field(
        default_factory=lambda: os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
    )
    audit_non_matrix_conversations: bool = field(
        default_factory=lambda: os.getenv("AUDIT_NON_MATRIX") != "false"
    )


# ============================================================================
# Content Extraction Functions
# ============================================================================

def extract_content_text(content: Optional[Union[str, List[Any], Dict[str, Any]]]) -> Optional[str]:
    """
    Extract text from various Letta content formats.
    
    Handles:
    - String content: returned as-is
    - Array content: [{type: 'text', text: '...'}, ...] - concatenates text parts
    - Object content: {text: '...'} - extracts text field
    - Other: JSON stringified
    
    Args:
        content: The content field from a Letta message
        
    Returns:
        Extracted text or None if no valid content
    """
    if content is None:
        return None
    
    # String content - return directly
    if isinstance(content, str):
        return content
    
    # Array content - Letta v1 format: [{type: 'text', text: '...'}, ...]
    if isinstance(content, list):
        text_parts: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
        
        if text_parts:
            return "\n".join(text_parts)
        
        logger.warning(
            f"[Webhook] Content array has no text parts: {json.dumps(content)[:200]}"
        )
        return None
    
    # Object content - extract text field
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    
    # Unknown format - stringify
    logger.warning(
        f"[Webhook] Unknown content format: {type(content).__name__}, {json.dumps(content)[:200]}"
    )
    return json.dumps(content)


def extract_user_content(messages: Optional[List[LettaMessage]]) -> Optional[str]:
    """
    Extract user message content from webhook messages.
    
    Args:
        messages: List of Letta messages from webhook
        
    Returns:
        User's message content or None
    """
    if not messages:
        return None
    
    for msg in messages:
        if msg.message_type == "user_message":
            extracted = extract_content_text(msg.content)
            if extracted:
                return extracted
    
    return None


def extract_assistant_content(messages: Optional[List[LettaMessage]]) -> Optional[str]:
    """
    Extract assistant message content from webhook messages.
    
    Concatenates ALL assistant messages (for streaming chunk consolidation).
    
    Args:
        messages: List of Letta messages from webhook
        
    Returns:
        Assistant's concatenated message content or None
    """
    if not messages:
        return None
    
    all_parts: List[str] = []
    
    for msg in messages:
        if msg.message_type == "assistant_message":
            extracted = extract_content_text(msg.content) or msg.assistant_message
            if extracted:
                all_parts.append(extracted)
    
    if not all_parts:
        return None
    
    return "".join(all_parts)


def is_inter_agent_relay(content: str) -> bool:
    """
    Check if message is an inter-agent relay (should be skipped).
    
    Args:
        content: Message content to check
        
    Returns:
        True if this is an inter-agent relay message
    """
    relay_markers = [
        "[INTER-AGENT MESSAGE from",
        "[MESSAGE FROM OPENCODE USER]",
        "[FORWARDED FROM",
    ]
    return any(marker in content for marker in relay_markers)


# ============================================================================
# Signature Verification
# ============================================================================

def verify_webhook_signature(
    payload: str,
    signature: Optional[str],
    secret: Optional[str],
    skip_verification: bool = False
) -> bool:
    """
    Verify webhook signature using HMAC-SHA256.
    
    Letta uses Stripe-style format: t=timestamp,v1=signature
    
    Args:
        payload: Raw request body as string
        signature: Signature header value
        secret: Webhook secret
        skip_verification: Skip verification (dev mode)
        
    Returns:
        True if signature is valid or verification is skipped
    """
    if skip_verification:
        logger.debug("[Webhook] Signature verification skipped (dev mode)")
        return True
    
    if not secret:
        logger.warning("[Webhook] No webhook secret configured, skipping verification")
        return True
    
    if not signature:
        logger.error("[Webhook] No signature provided in request")
        return False
    
    # Parse Stripe-style signature format: t=timestamp,v1=signature
    parts = signature.split(",")
    timestamp: Optional[str] = None
    provided_sig: Optional[str] = None
    
    for part in parts:
        if "=" in part:
            key, value = part.split("=", 1)
            if key == "t":
                timestamp = value
            elif key == "v1":
                provided_sig = value
    
    if not timestamp or not provided_sig:
        logger.error(
            "[Webhook] Invalid signature format, expected t=timestamp,v1=signature"
        )
        return False
    
    # Compute expected signature: HMAC-SHA256(timestamp.payload)
    signed_payload = f"{timestamp}.{payload}"
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    # Constant-time comparison
    is_valid = hmac.compare_digest(provided_sig, expected_sig)
    
    if not is_valid:
        logger.error("[Webhook] Signature verification failed")
    
    return is_valid


# ============================================================================
# Webhook Handler Class
# ============================================================================

class LettaWebhookHandler:
    """
    Handles Letta webhook events.
    
    This class processes agent.run.completed events from Letta and:
    1. Verifies the webhook signature
    2. Extracts user request and assistant response
    3. Delegates to the bridge for Matrix posting
    """
    
    def __init__(self, config: Optional[WebhookConfig] = None):
        """
        Initialize the webhook handler.
        
        Args:
            config: Handler configuration
        """
        self.config = config or WebhookConfig()
        self._bridge: Optional[Any] = None  # LettaMatrixBridge, set later
    
    def set_bridge(self, bridge: Any) -> None:
        """Set the Matrix bridge for posting messages."""
        self._bridge = bridge
    
    def verify_signature(self, payload: str, signature: Optional[str]) -> bool:
        """
        Verify webhook signature.
        
        Args:
            payload: Raw request body
            signature: Signature header
            
        Returns:
            True if valid
        """
        return verify_webhook_signature(
            payload,
            signature,
            self.config.webhook_secret,
            self.config.skip_verification
        )
    
    async def handle_run_completed(
        self, payload: LettaWebhookPayload
    ) -> WebhookResult:
        """
        Handle agent.run.completed webhook event.
        
        Args:
            payload: Parsed webhook payload
            
        Returns:
            WebhookResult with processing outcome
        """
        agent_id = payload.agent_id
        data = payload.data
        run_id = data.run_id if data else None
        messages = data.messages if data else None
        
        logger.info(
            f"[Webhook] Received agent.run.completed for agent {agent_id}, "
            f"run {run_id or 'unknown'}, messages: {len(messages or [])}"
        )
        
        disabled_agent_ids = [a.strip() for a in os.getenv("DISABLED_AGENT_IDS", "").split(",") if a.strip()]
        if agent_id in disabled_agent_ids:
            logger.info(f"[Webhook] Skipping disabled agent {agent_id}")
            return WebhookResult(
                success=True,
                response_posted=False,
                error="disabled_agent",
                agent_id=agent_id
            )
        
        # Extract content
        user_content = extract_user_content(messages)
        assistant_content = extract_assistant_content(messages)
        
        if not assistant_content:
            logger.info(
                f"[Webhook] No assistant message content in webhook for agent {agent_id}"
            )
            return WebhookResult(
                success=True,
                response_posted=False,
                error="no_assistant_content",
                agent_id=agent_id
            )
        
        # Skip inter-agent relay messages
        if is_inter_agent_relay(assistant_content):
            logger.info(
                f"[Webhook] Skipping inter-agent relay message for agent {agent_id}"
            )
            return WebhookResult(
                success=True,
                response_posted=False,
                error="inter_agent_relay",
                agent_id=agent_id
            )
        
        if is_matrix_conversation(agent_id):
            opencode_sender = get_opencode_sender(agent_id)
            clear_matrix_conversation(agent_id)
            
            if opencode_sender:
                logger.info(
                    f"[Webhook] Forwarding response for OpenCode/ClaudeCode sender {opencode_sender}"
                )
                try:
                    import aiohttp
                    mcp_url = os.getenv("MCP_SERVER_URL", "http://matrix-messaging-mcp:3101")
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"{mcp_url}/conversations/response",
                            json={
                                "agent_id": agent_id,
                                "response": assistant_content,
                                "opencode_sender": opencode_sender
                            }
                        ) as resp:
                            if resp.status == 200:
                                logger.info(f"[Webhook] Forwarded response to MCP for {opencode_sender}")
                            else:
                                logger.warning(f"[Webhook] MCP forward failed: {resp.status}")
                except Exception as e:
                    logger.warning(f"[Webhook] Failed to forward to MCP: {e}")
                
                return WebhookResult(
                    success=True,
                    response_posted=True,
                    response_content=assistant_content,
                    agent_id=agent_id
                )
            
            logger.info(
                f"[Webhook] Skipping audit for Matrix conversation agent {agent_id}"
            )
            return WebhookResult(
                success=True,
                response_posted=False,
                error="matrix_conversation",
                agent_id=agent_id
            )
        
        # Post audit for CLI/API conversations
        if self._bridge is None:
            logger.error("[Webhook] No bridge configured for Matrix posting")
            return WebhookResult(
                success=False,
                response_posted=False,
                error="no_bridge",
                agent_id=agent_id
            )
        
        try:
            result = await self._bridge.post_webhook_response(
                agent_id=agent_id,
                user_content=user_content,
                assistant_content=assistant_content,
                run_id=run_id
            )
            return result
        except Exception as e:
            logger.exception(f"[Webhook] Error posting to Matrix: {e}")
            return WebhookResult(
                success=False,
                response_posted=False,
                error=str(e),
                agent_id=agent_id
            )


# ============================================================================
# Module-level singleton
# ============================================================================

_handler_instance: Optional[LettaWebhookHandler] = None


def get_webhook_handler() -> Optional[LettaWebhookHandler]:
    """Get the global webhook handler instance."""
    return _handler_instance


def initialize_webhook_handler(config: Optional[WebhookConfig] = None) -> LettaWebhookHandler:
    """
    Initialize the global webhook handler.
    
    Args:
        config: Handler configuration
        
    Returns:
        Initialized handler instance
    """
    global _handler_instance
    _handler_instance = LettaWebhookHandler(config)
    logger.info("[Webhook] Handler initialized")
    return _handler_instance
