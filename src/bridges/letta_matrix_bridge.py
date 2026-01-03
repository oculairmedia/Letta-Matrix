"""
Letta <-> Matrix Bridge

Handles posting Letta webhook responses to Matrix rooms.

Responsibilities:
1. Agent-to-room mapping (via Matrix API database)
2. Matrix message posting (via nio client)
3. Audit message formatting for non-Matrix conversations
"""

import html
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

import aiohttp

from src.letta.webhook_handler import WebhookResult

logger = logging.getLogger(__name__)


@dataclass
class BridgeConfig:
    matrix_homeserver: str = field(
        default_factory=lambda: os.getenv("MATRIX_HOMESERVER_URL", "http://tuwunel:6167")
    )
    matrix_admin_username: str = field(
        default_factory=lambda: os.getenv("MATRIX_ADMIN_USERNAME", "@admin:matrix.oculair.ca")
    )
    matrix_admin_password: str = field(
        default_factory=lambda: os.getenv("MATRIX_ADMIN_PASSWORD", "")
    )
    matrix_api_url: str = field(
        default_factory=lambda: os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
    )
    room_cache_ttl_seconds: int = 60


class LettaMatrixBridge:
    """
    Bridge between Letta webhooks and Matrix rooms.
    
    This class:
    1. Looks up Matrix room IDs for agents
    2. Posts webhook responses to the appropriate rooms
    3. Formats audit messages for CLI/API conversations
    """
    
    def __init__(self, config: Optional[BridgeConfig] = None):
        self.config = config or BridgeConfig()
        self._room_cache: Dict[str, tuple[str, datetime]] = {}
        self._access_token: Optional[str] = None
    
    async def _ensure_logged_in(self) -> str:
        """Login to Matrix and return access token."""
        if self._access_token:
            return self._access_token
        
        if not self.config.matrix_admin_password:
            raise ValueError("MATRIX_ADMIN_PASSWORD not configured")
        
        login_url = f"{self.config.matrix_homeserver}/_matrix/client/v3/login"
        login_data = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": self.config.matrix_admin_username},
            "password": self.config.matrix_admin_password,
            "initial_device_display_name": "LettaMatrixBridge"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(login_url, json=login_data, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(f"Matrix login failed: {resp.status} {error_text}")
                result = await resp.json()
                self._access_token = result.get("access_token")
                if not self._access_token:
                    raise RuntimeError("Login succeeded but no access_token returned")
                logger.info(f"[Bridge] Logged in as {self.config.matrix_admin_username}")
                return self._access_token
    
    async def find_matrix_room_for_agent(self, agent_id: str) -> Optional[str]:
        """
        Find the Matrix room ID for a Letta agent.
        
        Uses the Matrix API to query the PostgreSQL database.
        Results are cached for performance.
        
        Args:
            agent_id: Letta agent ID
            
        Returns:
            Matrix room ID or None if not found
        """
        # Check cache
        cached = self._room_cache.get(agent_id)
        if cached:
            room_id, cached_at = cached
            age_seconds = (datetime.now() - cached_at).total_seconds()
            if age_seconds < self.config.room_cache_ttl_seconds:
                logger.debug(f"[Bridge] Using cached room {room_id} for agent {agent_id}")
                return room_id
        
        # Query Matrix API
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.config.matrix_api_url}/agents/{agent_id}/room"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        room_id = data.get("room_id")
                        if room_id:
                            # Cache the result
                            self._room_cache[agent_id] = (room_id, datetime.now())
                            logger.info(
                                f"[Bridge] Found room {room_id} for agent {agent_id} "
                                f"({data.get('agent_name', 'unknown')})"
                            )
                            return room_id
                    elif resp.status == 404:
                        logger.warning(f"[Bridge] Agent {agent_id} not found in Matrix API")
                    else:
                        logger.error(
                            f"[Bridge] Matrix API error: {resp.status} {resp.reason}"
                        )
        except Exception as e:
            logger.exception(f"[Bridge] Failed to query Matrix API: {e}")
        
        logger.warning(f"[Bridge] No room mapping found for agent {agent_id}")
        return None
    
    async def post_silent_audit(
        self,
        room_id: str,
        assistant_content: str,
        source: str,
        user_request: Optional[str] = None,
        run_id: Optional[str] = None
    ) -> None:
        """
        Post a silent audit message to Matrix (m.notice, no notification).
        
        Used for non-Matrix conversations (CLI, API) to maintain audit trail.
        
        Args:
            room_id: Matrix room ID
            assistant_content: The agent's response
            source: Source of the conversation ("external" or "matrix-direct")
            user_request: Optional user request that triggered this response
            run_id: Optional Letta run ID
        """
        max_request_len = 200
        max_response_len = 500
        
        truncated_request = None
        if user_request:
            truncated_request = (
                user_request[:max_request_len] + "..."
                if len(user_request) > max_request_len
                else user_request
            )
        
        truncated_response = (
            assistant_content[:max_response_len] + "..."
            if len(assistant_content) > max_response_len
            else assistant_content
        )
        
        # Format message
        source_emoji = "🖥️" if source == "external" else "💬"
        source_label = "CLI/API" if source == "external" else "Direct"
        
        if truncated_request:
            plain_body = f"{source_emoji} **[{source_label}]**\n**Q:** {truncated_request}\n**A:** {truncated_response}"
            html_body = (
                f"<em>{source_emoji} <strong>[{source_label}]</strong></em>"
                f"<br/><strong>Q:</strong> {html.escape(truncated_request)}"
                f"<br/><strong>A:</strong> {html.escape(truncated_response)}"
            )
        else:
            plain_body = f"{source_emoji} **[{source_label}]** {truncated_response}"
            html_body = (
                f"<em>{source_emoji} <strong>[{source_label}]</strong></em> "
                f"{html.escape(truncated_response)}"
            )
        
        # Send via Matrix client-server API
        await self._send_matrix_message(
            room_id=room_id,
            body=plain_body,
            formatted_body=html_body,
            msgtype="m.notice"  # Silent, no notification
        )
        
        logger.info(f"[Bridge] Posted silent audit to {room_id} ({source})")
    
    async def post_response(
        self,
        room_id: str,
        content: str,
        reply_to_event_id: Optional[str] = None
    ) -> None:
        """
        Post a regular response to Matrix.
        
        Args:
            room_id: Matrix room ID
            content: Message content
            reply_to_event_id: Optional event ID to reply to
        """
        message_content: Dict[str, Any] = {
            "msgtype": "m.text",
            "body": content
        }
        
        if reply_to_event_id:
            message_content["m.relates_to"] = {
                "m.in_reply_to": {
                    "event_id": reply_to_event_id
                }
            }
        
        await self._send_matrix_message(
            room_id=room_id,
            body=content,
            msgtype="m.text",
            relates_to=message_content.get("m.relates_to")
        )
        
        logger.info(f"[Bridge] Posted response to {room_id}")
    
    async def _send_matrix_message(
        self,
        room_id: str,
        body: str,
        msgtype: str = "m.text",
        formatted_body: Optional[str] = None,
        relates_to: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Send a message to Matrix via the client-server API.
        
        Args:
            room_id: Target room ID
            body: Plain text body
            msgtype: Message type (m.text, m.notice)
            formatted_body: Optional HTML formatted body
            relates_to: Optional relation (for replies)
            
        Returns:
            Event ID of sent message
        """
        import time
        
        access_token = await self._ensure_logged_in()
        txn_id = f"bridge_{int(time.time() * 1000)}"
        url = f"{self.config.matrix_homeserver}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
        
        message_content: Dict[str, Any] = {
            "msgtype": msgtype,
            "body": body
        }
        
        if formatted_body:
            message_content["format"] = "org.matrix.custom.html"
            message_content["formatted_body"] = formatted_body
        
        if relates_to:
            message_content["m.relates_to"] = relates_to
        
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            async with session.put(
                url,
                headers=headers,
                json=message_content,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(f"Failed to send Matrix message: {resp.status} {error_text}")
                
                result = await resp.json()
                return result.get("event_id", "")
    
    async def post_webhook_response(
        self,
        agent_id: str,
        user_content: Optional[str],
        assistant_content: str,
        run_id: Optional[str] = None
    ) -> WebhookResult:
        """
        Post a webhook response to Matrix.
        
        This is the main entry point called by the webhook handler.
        
        Args:
            agent_id: Letta agent ID
            user_content: User's request (if available)
            assistant_content: Agent's response
            run_id: Letta run ID
            
        Returns:
            WebhookResult with outcome
        """
        # Find the Matrix room for this agent
        room_id = await self.find_matrix_room_for_agent(agent_id)
        if not room_id:
            logger.warning(f"[Bridge] No Matrix room found for agent {agent_id}")
            return WebhookResult(
                success=False,
                response_posted=False,
                error="no_matrix_room",
                agent_id=agent_id
            )
        
        # For now, always post as silent audit (CLI/API conversations)
        # TODO: Add conversation tracking to determine if this is a cross-run scenario
        try:
            await self.post_silent_audit(
                room_id=room_id,
                assistant_content=assistant_content,
                source="external",
                user_request=user_content,
                run_id=run_id
            )
            
            return WebhookResult(
                success=True,
                response_posted=True,
                response_content=f"[AUDIT] {assistant_content[:100]}...",
                agent_id=agent_id,
                room_id=room_id
            )
        except Exception as e:
            logger.exception(f"[Bridge] Failed to post audit: {e}")
            return WebhookResult(
                success=False,
                response_posted=False,
                error="audit_post_failed",
                agent_id=agent_id,
                room_id=room_id
            )


# ============================================================================
# Module-level singleton
# ============================================================================

_bridge_instance: Optional[LettaMatrixBridge] = None


def get_bridge() -> Optional[LettaMatrixBridge]:
    """Get the global bridge instance."""
    return _bridge_instance


def initialize_bridge(config: Optional[BridgeConfig] = None) -> LettaMatrixBridge:
    """
    Initialize the global bridge.
    
    Args:
        config: Bridge configuration
        
    Returns:
        Initialized bridge instance
    """
    global _bridge_instance
    _bridge_instance = LettaMatrixBridge(config)
    logger.info("[Bridge] Letta-Matrix bridge initialized")
    return _bridge_instance
