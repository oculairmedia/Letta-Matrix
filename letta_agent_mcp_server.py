#!/usr/bin/env python3
"""
Letta Agent Inter-Communication MCP Server
Enables Letta agents to communicate with each other via Matrix as a substrate
Based on the robust design with comprehensive failure handling
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field
import aiohttp
from aiohttp import web
from aiohttp.web import Request, Response, StreamResponse
import secrets
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env')


# Custom exceptions
class AgentNotFoundError(Exception):
    """Raised when an agent is not found in Letta"""
    pass


class MatrixAuthError(Exception):
    """Raised when Matrix authentication fails"""
    pass


class RoomNotFoundError(Exception):
    """Raised when a Matrix room is not found"""
    pass


@dataclass
class Session:
    """Represents an MCP session"""
    id: str
    created_at: datetime
    last_activity: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    pending_responses: Dict[str, StreamResponse] = field(default_factory=dict)
    event_counter: int = 0

    def generate_event_id(self) -> str:
        """Generate a unique event ID for SSE"""
        self.event_counter += 1
        return f"{self.id}-{self.event_counter}"


class MCPTool:
    """Base class for MCP tools"""
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.parameters = {}

    async def execute(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the tool with given parameters"""
        raise NotImplementedError


class MatrixAgentMessageTool(MCPTool):
    """Tool for inter-agent communication via Matrix"""

    def __init__(
        self,
        matrix_api_url: str,
        matrix_homeserver: str,
        letta_api_url: str,
        admin_username: str,
        admin_password: str
    ):
        super().__init__(
            name="matrix_agent_message",
            description="Send a message to another Letta agent via Matrix. Your agent ID is automatically detected from the x-agent-id header."
        )
        self.matrix_api_url = matrix_api_url
        self.matrix_homeserver = matrix_homeserver
        self.letta_api_url = letta_api_url
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.admin_token = None
        self.mappings_cache = {}
        self.cache_ttl = 60  # seconds

        self.parameters = {
            "to_agent_id": {
                "type": "string",
                "description": "Target agent ID to send message to (e.g., 'agent-597b5756-2915-4560-ba6b-91005f085166')"
            },
            "message": {
                "type": "string",
                "description": "Message content to send to the target agent"
            },
            "from_agent_id": {
                "type": "string",
                "description": "Your agent ID (optional - auto-detected from x-agent-id header)",
                "default": None
            },
            "verify_delivery": {
                "type": "boolean",
                "description": "Wait for delivery confirmation (optional, default: false)",
                "default": False
            }
        }

    async def execute(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute inter-agent message sending with comprehensive error handling"""
        try:
            logger.info(f"Inter-agent message request: {params}")

            # 1. SENDER DETECTION with fallback
            from_agent_id = self._resolve_sender(context, params)
            to_agent_id = params.get("to_agent_id")
            message = params.get("message")

            if not to_agent_id:
                return {"error": "Missing required parameter: to_agent_id"}
            if not message:
                return {"error": "Missing required parameter: message"}

            # 2. DYNAMIC AGENT DISCOVERY
            from_agent = await self._get_agent_info(from_agent_id)
            to_agent = await self._get_agent_info(to_agent_id)

            # 3. ROOM RESOLUTION with on-demand creation
            target_room = await self._ensure_agent_room(to_agent)

            # 4. AGENT AUTHENTICATION (send as the from_agent, not admin)
            # Get the sending agent's token so message appears with their identity
            if from_agent_id == "system":
                # For system messages, use admin token
                if not self.admin_token:
                    self.admin_token = await self._get_admin_token()
                sender_token = self.admin_token
            else:
                # For agent messages, authenticate as that agent
                sender_token = await self._get_agent_token(from_agent)
                
                # 4b. Ensure sender is in the target room (join if needed)
                await self._ensure_agent_in_room(
                    from_agent["matrix_user_id"],
                    target_room,
                    sender_token
                )

            # 5. SEND WITH METADATA
            result = await self._send_inter_agent_message(
                from_agent=from_agent,
                to_room=target_room,
                message=message,
                sender_token=sender_token  # Pass the sender's token
            )

            # 6. VERIFY DELIVERY (optional)
            if params.get("verify_delivery", False):
                delivered = await self._verify_delivery(
                    result["event_id"],
                    timeout=5.0
                )
                result["delivered"] = delivered

            logger.info(f"Inter-agent message sent successfully: {result}")
            return result

        except AgentNotFoundError as e:
            error_msg = f"Agent not found: {e}"
            logger.error(error_msg)
            return {
                "error": error_msg,
                "error_type": "agent_not_found",
                "suggestion": "Verify agent ID exists in Letta"
            }
        except MatrixAuthError as e:
            error_msg = f"Matrix authentication error: {e}"
            logger.error(error_msg)
            # FALLBACK: Try alternative sending method
            return await self._fallback_send(params, context, str(e))
        except RoomNotFoundError as e:
            error_msg = f"Room not found: {e}"
            logger.error(error_msg)
            return {
                "error": error_msg,
                "error_type": "room_not_found",
                "suggestion": "Target agent may not have a Matrix room yet"
            }
        except Exception as e:
            logger.error(f"Inter-agent message failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "error_type": "unknown",
                "fallback_available": True
            }

    def _resolve_sender(self, context: Optional[Dict], params: Dict) -> str:
        """Extract sender with multiple fallbacks"""
        # Priority: header > parameter > system default

        # Try context (from x-agent-id header)
        if context and context.get("agentId"):
            sender = context["agentId"]
            logger.info(f"Using sender from header: {sender}")
            return sender

        # Try parameter
        if params.get("from_agent_id"):
            sender = params["from_agent_id"]
            logger.info(f"Using sender from parameter: {sender}")
            return sender

        # Fallback to system
        logger.warning("No sender agent ID found, using system")
        return "system"

    async def _get_agent_info(self, agent_id: str) -> Dict:
        """Get agent info with caching"""
        # Handle system sender
        if agent_id == "system":
            return {
                "agent_id": "system",
                "agent_name": "System",
                "matrix_user_id": self.admin_username,
                "room_id": None
            }

        # Check cache first
        cache_key = f"agent_{agent_id}"
        if cache_key in self.mappings_cache:
            cached = self.mappings_cache[cache_key]
            if time.time() - cached["timestamp"] < self.cache_ttl:
                logger.info(f"Cache hit for agent {agent_id}")
                return cached["data"]

        logger.info(f"Fetching agent info for {agent_id}")

        # Get Matrix mapping from agent_user_mappings.json
        matrix_info = await self._get_matrix_mapping(agent_id)

        result = {
            "agent_id": agent_id,
            "agent_name": matrix_info.get("agent_name", "Unknown"),
            "matrix_user_id": matrix_info.get("matrix_user_id"),
            "matrix_password": matrix_info.get("matrix_password"),  # Include password for authentication
            "room_id": matrix_info.get("room_id")
        }

        # Update cache
        self.mappings_cache[cache_key] = {
            "data": result,
            "timestamp": time.time()
        }

        return result

    async def _get_matrix_mapping(self, agent_id: str) -> Dict:
        """Get Matrix mapping from agent_user_mappings.json"""
        try:
            mappings_file = "/app/data/agent_user_mappings.json"

            # Read the mappings file
            if not os.path.exists(mappings_file):
                logger.warning(f"Mappings file not found: {mappings_file}")
                raise AgentNotFoundError(f"Agent {agent_id} not found in mappings")

            with open(mappings_file, 'r') as f:
                mappings = json.load(f)

            # Check if mappings is a dict (new format) or list (old format)
            if isinstance(mappings, dict):
                # New format: dict with agent_id as key
                if agent_id in mappings:
                    return mappings[agent_id]
            else:
                # Old format: list of mappings
                for mapping in mappings:
                    if mapping.get("agent_id") == agent_id:
                        return mapping

            raise AgentNotFoundError(f"Agent {agent_id} not found in mappings")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse mappings file: {e}")
            raise AgentNotFoundError(f"Invalid mappings file format")
        except Exception as e:
            logger.error(f"Error reading mappings: {e}")
            raise

    async def _ensure_agent_room(self, agent_info: Dict) -> str:
        """Ensure agent has a Matrix room"""
        if agent_info.get("room_id"):
            # Verify room still exists
            if await self._room_exists(agent_info["room_id"]):
                return agent_info["room_id"]

        # Room doesn't exist or not set
        raise RoomNotFoundError(
            f"Agent {agent_info['agent_id']} does not have a valid Matrix room. "
            f"Ensure the agent sync service has created the room."
        )

    async def _room_exists(self, room_id: str) -> bool:
        """Check if a Matrix room exists"""
        try:
            if not self.admin_token:
                self.admin_token = await self._get_admin_token()

            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/state"
                headers = {"Authorization": f"Bearer {self.admin_token}"}

                async with session.get(url, headers=headers) as resp:
                    return resp.status == 200

        except Exception as e:
            logger.warning(f"Error checking room existence: {e}")
            return False

    async def _get_admin_token(self) -> str:
        """Get admin access token"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/login"
                payload = {
                    "homeserver": self.matrix_homeserver,
                    "user_id": self.admin_username,
                    "password": self.admin_password,
                    "device_name": "letta_agent_mcp_server"
                }

                logger.info(f"Logging in as admin: {self.admin_username}")

                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise MatrixAuthError(f"Admin login failed: {error_text}")

                    result = await response.json()
                    if result.get("success"):
                        logger.info("Admin login successful")
                        return result.get("access_token")
                    else:
                        raise MatrixAuthError(f"Admin login failed: {result.get('message', 'Unknown error')}")

        except Exception as e:
            logger.error(f"Admin authentication error: {e}")
            raise MatrixAuthError(f"Failed to authenticate as admin: {e}")

    async def _get_agent_token(self, agent_info: Dict) -> str:
        """Get access token for a specific agent user"""
        try:
            agent_user_id = agent_info.get("matrix_user_id")
            agent_password = agent_info.get("matrix_password")

            if not agent_user_id or not agent_password:
                raise MatrixAuthError(f"Missing credentials for agent {agent_info.get('agent_name')}")

            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/login"
                payload = {
                    "homeserver": self.matrix_homeserver,
                    "user_id": agent_user_id,
                    "password": agent_password,
                    "device_name": f"letta_agent_mcp_{agent_info.get('agent_name', 'agent')}"
                }

                logger.info(f"Logging in as agent: {agent_user_id}")

                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise MatrixAuthError(f"Agent login failed for {agent_user_id}: {error_text}")

                    result = await response.json()
                    if result.get("success"):
                        logger.info(f"Agent login successful for {agent_info.get('agent_name')}")
                        return result.get("access_token")
                    else:
                        raise MatrixAuthError(f"Agent login failed: {result.get('message', 'Unknown error')}")

        except Exception as e:
            logger.error(f"Agent authentication error: {e}")
            raise MatrixAuthError(f"Failed to authenticate agent: {e}")

    async def _send_inter_agent_message(
        self,
        from_agent: Dict,
        to_room: str,
        message: str,
        sender_token: str
    ) -> Dict:
        """Send message with proper formatting and metadata AS the sending agent"""

        # Format message with sender info
        formatted_text = f"[Inter-Agent Message from {from_agent['agent_name']}]\n{message}"
        formatted_html = f"<b>[From: {from_agent['agent_name']}]</b><br/>{message}"

        try:
            async with aiohttp.ClientSession() as session:
                # Use Matrix API service for sending
                url = f"{self.matrix_api_url}/messages/send"
                payload = {
                    "room_id": to_room,
                    "message": formatted_text,
                    "access_token": sender_token,  # Use sender's token, not admin
                    "homeserver": self.matrix_homeserver,
                    "inter_agent_metadata": {
                        "from_agent_id": from_agent["agent_id"],
                        "from_agent_name": from_agent["agent_name"],
                        "timestamp": time.time(),
                        "type": "inter_agent_communication"
                    }
                }

                async with session.post(url, json=payload) as resp:
                    result = await resp.json()

                    if resp.status == 200 and result.get("success"):
                        return {
                            "success": True,
                            "event_id": result.get("event_id"),
                            "room_id": to_room,
                            "from_agent": from_agent["agent_name"],
                            "timestamp": time.time()
                        }
                    else:
                        raise MatrixAuthError(f"Failed to send: {result.get('message', 'Unknown error')}")

        except Exception as e:
            logger.error(f"Error sending inter-agent message: {e}")
            raise

    async def _verify_delivery(self, event_id: str, timeout: float = 5.0) -> bool:
        """Verify message delivery (simplified - just return True for now)"""
        # TODO: Implement actual delivery verification via Matrix receipts
        logger.info(f"Delivery verification requested for {event_id} (not yet implemented)")
        return True

    async def _fallback_send(self, params: Dict, context: Optional[Dict], error: str) -> Dict:
        """Fallback sending mechanism if primary fails"""
        logger.warning(f"Primary send failed: {error}, trying fallback")

        # For now, just return error with fallback info
        # TODO: Implement message queuing for retry
        return {
            "success": False,
            "error": error,
            "fallback": "not_implemented",
            "message": "Primary send failed. Message queuing not yet implemented."
        }
    
    async def _ensure_agent_in_room(self, agent_user_id: str, room_id: str, agent_token: str):
        """Ensure an agent is a member of a room, join if not"""
        try:
            async with aiohttp.ClientSession() as session:
                # Try to join the room (idempotent - no-op if already member)
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/join"
                headers = {
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json"
                }

                async with session.post(url, headers=headers, json={}) as resp:
                    if resp.status in [200, 201]:
                        logger.info(f"Agent {agent_user_id} successfully joined/is in room {room_id}")
                    elif resp.status == 403:
                        # Not invited, need admin to invite first
                        logger.info(f"Agent not invited to room, using admin to invite")
                        await self._invite_agent_to_room(agent_user_id, room_id)

                        # Retry join after invitation
                        async with session.post(url, headers=headers, json={}) as retry_resp:
                            if retry_resp.status in [200, 201]:
                                logger.info(f"Agent {agent_user_id} joined room after invitation")
                            else:
                                logger.warning(f"Failed to join after invite: {await retry_resp.text()}")
                    else:
                        logger.warning(f"Unexpected status joining room: {resp.status}")

        except Exception as e:
            logger.error(f"Error ensuring agent in room: {e}")
    
    async def _invite_agent_to_room(self, agent_user_id: str, room_id: str):
        """Invite an agent to a room using admin privileges"""
        try:
            if not self.admin_token:
                self.admin_token = await self._get_admin_token()

            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/invite"
                headers = {
                    "Authorization": f"Bearer {self.admin_token}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "user_id": agent_user_id
                }

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in [200, 201]:
                        logger.info(f"Successfully invited {agent_user_id} to room {room_id}")
                    else:
                        error_text = await resp.text()
                        logger.warning(f"Failed to invite agent: {error_text}")

        except Exception as e:
            logger.error(f"Error inviting agent to room: {e}")


class MatrixAgentMessageAsyncTool(MCPTool):
    """Tool for async inter-agent communication via Matrix"""

    def __init__(
        self,
        matrix_api_url: str,
        matrix_homeserver: str,
        letta_api_url: str,
        admin_username: str,
        admin_password: str
    ):
        super().__init__(
            name="matrix_agent_message_async",
            description="Send a message to another Letta agent asynchronously. Returns a tracking ID to check status later. Use this for long-running tasks."
        )
        self.matrix_api_url = matrix_api_url
        self.matrix_homeserver = matrix_homeserver
        self.letta_api_url = letta_api_url
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.admin_token = None
        self.requests_file = "/app/data/async_requests.json"

        self.parameters = {
            "to_agent_id": {
                "type": "string",
                "description": "Target agent ID to send message to"
            },
            "message": {
                "type": "string",
                "description": "Message content to send to the target agent"
            },
            "from_agent_id": {
                "type": "string",
                "description": "Your agent ID (optional - auto-detected from x-agent-id header)",
                "default": None
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "How long to wait for response before marking as timed out (default: 300 seconds / 5 minutes)",
                "default": 300
            }
        }

    async def execute(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute async inter-agent message sending"""
        try:
            tracking_id = str(uuid.uuid4())

            # Extract parameters
            from_agent_id = self._resolve_sender(context, params)
            to_agent_id = params.get("to_agent_id")
            message = params.get("message")
            timeout_seconds = params.get("timeout_seconds", 300)

            if not to_agent_id or not message:
                return {"error": "Missing required parameters: to_agent_id, message"}

            # Create async request record
            request_record = {
                "tracking_id": tracking_id,
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "message": message,
                "status": "pending",
                "created_at": time.time(),
                "timeout_at": time.time() + timeout_seconds,
                "response": None,
                "error": None
            }

            # Save to requests file
            await self._save_request(request_record)

            # Send the message asynchronously (don't wait for response)
            asyncio.create_task(self._send_async_message(request_record))

            logger.info(f"Created async request {tracking_id} from {from_agent_id} to {to_agent_id}")

            return {
                "success": True,
                "tracking_id": tracking_id,
                "status": "pending",
                "message": "Message sent. Use matrix_agent_message_status to check progress."
            }

        except Exception as e:
            logger.error(f"Async message failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "error_type": "async_send_failed"
            }

    def _resolve_sender(self, context: Optional[Dict], params: Dict) -> str:
        """Extract sender with fallbacks"""
        if context and context.get("agentId"):
            return context["agentId"]
        if params.get("from_agent_id"):
            return params["from_agent_id"]
        return "system"

    async def _get_agent_token(self, agent_info: Dict) -> str:
        """Get access token for a specific agent user"""
        try:
            agent_user_id = agent_info.get("matrix_user_id")
            agent_password = agent_info.get("matrix_password")

            if not agent_user_id or not agent_password:
                raise MatrixAuthError(f"Missing credentials for agent {agent_info.get('agent_name')}")

            async with aiohttp.ClientSession() as session:
                # Login directly to Matrix homeserver
                url = f"{self.matrix_homeserver}/_matrix/client/r0/login"
                payload = {
                    "type": "m.login.password",
                    "user": agent_user_id,
                    "password": agent_password,
                    "device_name": f"letta_agent_mcp_{agent_info.get('agent_name', 'agent')}"
                }

                logger.info(f"Logging in as agent: {agent_user_id}")

                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        access_token = result.get("access_token")
                        logger.info(f"Agent login successful for {agent_info.get('agent_name')}")
                        return access_token
                    else:
                        error_text = await response.text()
                        raise MatrixAuthError(f"Agent login failed for {agent_user_id}: {error_text}")

        except Exception as e:
            logger.error(f"Agent authentication error: {e}")
            raise MatrixAuthError(f"Failed to authenticate agent: {e}")

    async def _get_admin_token(self) -> str:
        """Get admin access token"""
        try:
            async with aiohttp.ClientSession() as session:
                # Login directly to Matrix homeserver
                url = f"{self.matrix_homeserver}/_matrix/client/r0/login"
                payload = {
                    "type": "m.login.password",
                    "user": self.admin_username,
                    "password": self.admin_password,
                    "device_name": "letta_agent_mcp_async"
                }

                logger.info(f"Logging in as admin: {self.admin_username}")

                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        access_token = result.get("access_token")
                        logger.info("Admin login successful")
                        return access_token
                    else:
                        error_text = await response.text()
                        raise MatrixAuthError(f"Admin login failed: {error_text}")

        except Exception as e:
            logger.error(f"Admin authentication error: {e}")
            raise MatrixAuthError(f"Failed to authenticate as admin: {e}")

    async def _save_request(self, request_record: Dict):
        """Save async request to file"""
        try:
            # Load existing requests
            requests = {}
            if os.path.exists(self.requests_file):
                with open(self.requests_file, 'r') as f:
                    requests = json.load(f)

            # Add new request
            requests[request_record["tracking_id"]] = request_record

            # Save back
            with open(self.requests_file, 'w') as f:
                json.dump(requests, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save async request: {e}")
            raise

    async def _send_async_message(self, request_record: Dict):
        """Background task to send message and monitor for response"""
        tracking_id = request_record["tracking_id"]
        from_agent_id = request_record["from_agent_id"]
        to_agent_id = request_record["to_agent_id"]
        message = request_record["message"]
        timeout_at = request_record["timeout_at"]

        try:
            logger.info(f"Background task started for {tracking_id}")

            # Step 1: Get agent info and room
            from_agent = await self._get_agent_info(from_agent_id)
            to_agent = await self._get_agent_info(to_agent_id)

            if not to_agent.get("room_id"):
                raise Exception(f"Target agent {to_agent_id} has no Matrix room")

            target_room = to_agent["room_id"]

            # Step 2: Get sender agent's token (or admin token for system)
            if from_agent_id == "system":
                # For system sender, use admin token
                if not self.admin_token:
                    self.admin_token = await self._get_admin_token()
                sender_token = self.admin_token
                logger.info("Using admin token for system sender")
            else:
                # For real agents, authenticate as that agent
                sender_token = await self._get_agent_token(from_agent)

                # Step 2b: Ensure sender is in the target room
                # First, try to join the room as the sender
                await self._ensure_agent_in_room(
                    from_agent["matrix_user_id"],
                    target_room,
                    sender_token
                )

            # Step 3: Send message as the sender agent (appears naturally in target's room)
            event_id = await self._send_matrix_message(
                target_room,
                message,  # Send the message directly, no special formatting
                tracking_id,
                sender_token,  # Authenticate as sending agent
                from_agent_id=from_agent_id,  # Include sender agent ID
                from_agent_name=from_agent.get("agent_name", "Unknown Agent")  # Include sender name
            )

            logger.info(f"Sent Matrix message as {from_agent['matrix_user_id']} for {tracking_id}, event_id: {event_id}")

            # Update request with event_id
            await self._update_request_field(tracking_id, "matrix_event_id", event_id)
            await self._update_request_status(tracking_id, "sent")

            # Step 4: Monitor for response from target agent
            response = await self._monitor_for_response(
                target_room,
                to_agent["matrix_user_id"],  # Look for messages from target agent
                event_id,
                timeout_at,
                sender_token  # Use sender token to read room
            )

            if response:
                await self._update_request_status(
                    tracking_id,
                    "completed",
                    response=response
                )
                logger.info(f"Request {tracking_id} completed with response")
            else:
                await self._update_request_status(
                    tracking_id,
                    "timeout",
                    error="No response received within timeout period"
                )
                logger.warning(f"Request {tracking_id} timed out")

        except Exception as e:
            logger.error(f"Async message background task failed: {e}", exc_info=True)
            await self._update_request_status(
                tracking_id,
                "failed",
                error=str(e)
            )

    async def _get_agent_info(self, agent_id: str) -> Dict:
        """Get agent info from mappings"""
        if agent_id == "system":
            return {
                "agent_id": "system",
                "agent_name": "System",
                "matrix_user_id": self.admin_username,
                "room_id": None
            }

        # Read from mappings file
        mappings_file = "/app/data/agent_user_mappings.json"

        if not os.path.exists(mappings_file):
            raise AgentNotFoundError(f"Mappings file not found")

        with open(mappings_file, 'r') as f:
            mappings = json.load(f)

        if isinstance(mappings, dict):
            if agent_id in mappings:
                return mappings[agent_id]
        else:
            for mapping in mappings:
                if mapping.get("agent_id") == agent_id:
                    return mapping

        raise AgentNotFoundError(f"Agent {agent_id} not found in mappings")

    async def _ensure_agent_in_room(self, agent_user_id: str, room_id: str, agent_token: str):
        """Ensure an agent is a member of a room, join if not"""
        try:
            async with aiohttp.ClientSession() as session:
                # Try to join the room (idempotent - no-op if already member)
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/join"
                headers = {
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json"
                }

                async with session.post(url, headers=headers, json={}) as resp:
                    if resp.status in [200, 201]:
                        logger.info(f"Agent {agent_user_id} successfully joined/is in room {room_id}")
                    elif resp.status == 403:
                        # Not invited, need admin to invite first
                        logger.info(f"Agent not invited to room, using admin to invite")
                        await self._invite_agent_to_room(agent_user_id, room_id)

                        # Retry join after invitation
                        async with session.post(url, headers=headers, json={}) as retry_resp:
                            if retry_resp.status in [200, 201]:
                                logger.info(f"Agent {agent_user_id} joined room after invitation")
                            else:
                                logger.warning(f"Failed to join after invite: {await retry_resp.text()}")
                    else:
                        logger.warning(f"Unexpected status joining room: {resp.status}")

        except Exception as e:
            logger.error(f"Error ensuring agent in room: {e}")

    async def _invite_agent_to_room(self, agent_user_id: str, room_id: str):
        """Invite an agent to a room using admin privileges"""
        try:
            if not self.admin_token:
                self.admin_token = await self._get_admin_token()

            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/invite"
                headers = {
                    "Authorization": f"Bearer {self.admin_token}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "user_id": agent_user_id
                }

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in [200, 201]:
                        logger.info(f"Successfully invited {agent_user_id} to room {room_id}")
                    else:
                        error_text = await resp.text()
                        logger.warning(f"Failed to invite agent: {error_text}")

        except Exception as e:
            logger.error(f"Error inviting agent to room: {e}")

    async def _send_matrix_message(self, room_id: str, message: str, tracking_id: str, access_token: str, from_agent_id: Optional[str] = None, from_agent_name: Optional[str] = None) -> str:
        """Send message directly to Matrix room as the authenticated user"""
        try:
            async with aiohttp.ClientSession() as session:
                # Send directly to Matrix homeserver, not through our API
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/send/m.room.message"
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }

                # Standard Matrix message format with sender metadata
                payload = {
                    "msgtype": "m.text",
                    "body": message,
                    "m.letta.tracking_id": tracking_id,  # Add tracking as custom field
                    "m.letta.type": "async_inter_agent_request",
                    "m.letta.from_agent_id": from_agent_id,  # Sender agent ID
                    "m.letta.from_agent_name": from_agent_name  # Sender agent name
                }

                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        event_id = result.get("event_id")
                        logger.info(f"Sent message directly to Matrix, event_id: {event_id}")
                        return event_id
                    else:
                        error_text = await resp.text()
                        raise Exception(f"Failed to send message: {resp.status} - {error_text}")

        except Exception as e:
            logger.error(f"Error sending Matrix message: {e}")
            raise

    async def _monitor_for_response(
        self,
        room_id: str,
        target_agent_user_id: str,
        request_event_id: str,
        timeout_at: float,
        access_token: str
    ) -> Optional[str]:
        """Monitor room for response from target agent"""
        logger.info(f"Starting response monitoring for messages from {target_agent_user_id} in room {room_id}")

        # Poll every 2 seconds
        poll_interval = 2
        last_checked_timestamp = time.time()

        while time.time() < timeout_at:
            try:
                # Get recent messages from the room
                messages = await self._get_recent_room_messages(room_id, limit=20, access_token=access_token)

                # Look for responses from the target agent after our request
                for msg in messages:
                    msg_timestamp = msg.get("timestamp", 0) / 1000  # Convert ms to seconds
                    msg_sender = msg.get("sender", "")

                    # Only check messages after our request
                    if msg_timestamp <= last_checked_timestamp:
                        continue

                    # Check if this message is from the target agent
                    if msg_sender == target_agent_user_id:
                        body = msg.get("body", "")
                        logger.info(f"Found response from {target_agent_user_id}: {body[:100]}")
                        return body

                    # Update last checked timestamp
                    if msg_timestamp > last_checked_timestamp:
                        last_checked_timestamp = msg_timestamp

                # Wait before next poll
                await asyncio.sleep(poll_interval)

            except Exception as e:
                logger.error(f"Error monitoring for response: {e}")
                await asyncio.sleep(poll_interval)

        logger.info(f"Response monitoring timed out for {target_agent_user_id}")
        return None

    async def _get_recent_room_messages(self, room_id: str, limit: int = 20, access_token: Optional[str] = None) -> List[Dict]:
        """Get recent messages directly from Matrix room"""
        try:
            # Use provided token or fall back to admin token
            token = access_token if access_token else self.admin_token

            async with aiohttp.ClientSession() as session:
                # Get messages directly from Matrix homeserver
                url = f"{self.matrix_homeserver}/_matrix/client/r0/rooms/{room_id}/messages"
                headers = {
                    "Authorization": f"Bearer {token}"
                }
                params = {
                    "dir": "b",  # backwards from most recent
                    "limit": limit
                }

                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        messages = []
                        for event in result.get("chunk", []):
                            if event.get("type") == "m.room.message":
                                messages.append({
                                    "sender": event.get("sender"),
                                    "body": event.get("content", {}).get("body", ""),
                                    "timestamp": event.get("origin_server_ts", 0),
                                    "event_id": event.get("event_id")
                                })
                        return messages
                    else:
                        logger.warning(f"Failed to get room messages: {resp.status}")
                        return []

        except Exception as e:
            logger.error(f"Error getting room messages: {e}")
            return []

    async def _update_request_field(self, tracking_id: str, field: str, value: Any):
        """Update a specific field in the request record"""
        try:
            if not os.path.exists(self.requests_file):
                return

            with open(self.requests_file, 'r') as f:
                requests = json.load(f)

            if tracking_id in requests:
                requests[tracking_id][field] = value

                with open(self.requests_file, 'w') as f:
                    json.dump(requests, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to update request field: {e}")

    async def _update_request_status(self, tracking_id: str, status: str, response: Optional[str] = None, error: Optional[str] = None):
        """Update async request status"""
        try:
            if not os.path.exists(self.requests_file):
                return

            with open(self.requests_file, 'r') as f:
                requests = json.load(f)

            if tracking_id in requests:
                requests[tracking_id]["status"] = status
                if response:
                    requests[tracking_id]["response"] = response
                if error:
                    requests[tracking_id]["error"] = error
                requests[tracking_id]["completed_at"] = time.time()

                with open(self.requests_file, 'w') as f:
                    json.dump(requests, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to update request status: {e}")


class MatrixAgentMessageStatusTool(MCPTool):
    """Tool for checking status of async inter-agent messages"""

    def __init__(self):
        super().__init__(
            name="matrix_agent_message_status",
            description="Check the status of an async inter-agent message using its tracking ID"
        )
        self.requests_file = "/app/data/async_requests.json"
        self.parameters = {
            "tracking_id": {
                "type": "string",
                "description": "Tracking ID returned from matrix_agent_message_async"
            }
        }

    async def execute(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Check status of async request"""
        try:
            tracking_id = params.get("tracking_id")
            if not tracking_id:
                return {"error": "Missing required parameter: tracking_id"}

            if not os.path.exists(self.requests_file):
                return {
                    "error": "No async requests found",
                    "tracking_id": tracking_id,
                    "status": "not_found"
                }

            with open(self.requests_file, 'r') as f:
                requests = json.load(f)

            if tracking_id not in requests:
                return {
                    "error": "Tracking ID not found",
                    "tracking_id": tracking_id,
                    "status": "not_found"
                }

            request = requests[tracking_id]

            # Check for timeout
            if request["status"] == "pending" and time.time() > request["timeout_at"]:
                request["status"] = "timeout"
                request["error"] = "Request timed out waiting for response"
                # Save updated status
                requests[tracking_id] = request
                with open(self.requests_file, 'w') as f:
                    json.dump(requests, f, indent=2)

            return {
                "tracking_id": tracking_id,
                "status": request["status"],
                "from_agent_id": request["from_agent_id"],
                "to_agent_id": request["to_agent_id"],
                "created_at": request["created_at"],
                "elapsed_seconds": time.time() - request["created_at"],
                "has_response": request.get("response") is not None,
                "has_error": request.get("error") is not None
            }

        except Exception as e:
            logger.error(f"Status check failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "error_type": "status_check_failed"
            }


class MatrixAgentMessageResultTool(MCPTool):
    """Tool for retrieving results of async inter-agent messages"""

    def __init__(self):
        super().__init__(
            name="matrix_agent_message_result",
            description="Retrieve the result/response of an async inter-agent message using its tracking ID"
        )
        self.requests_file = "/app/data/async_requests.json"
        self.parameters = {
            "tracking_id": {
                "type": "string",
                "description": "Tracking ID returned from matrix_agent_message_async"
            },
            "delete_after_read": {
                "type": "boolean",
                "description": "Delete the request record after reading (default: false)",
                "default": False
            }
        }

    async def execute(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Retrieve result of async request"""
        try:
            tracking_id = params.get("tracking_id")
            delete_after_read = params.get("delete_after_read", False)

            if not tracking_id:
                return {"error": "Missing required parameter: tracking_id"}

            if not os.path.exists(self.requests_file):
                return {
                    "error": "No async requests found",
                    "tracking_id": tracking_id
                }

            with open(self.requests_file, 'r') as f:
                requests = json.load(f)

            if tracking_id not in requests:
                return {
                    "error": "Tracking ID not found",
                    "tracking_id": tracking_id
                }

            request = requests[tracking_id]

            result = {
                "tracking_id": tracking_id,
                "status": request["status"],
                "from_agent_id": request["from_agent_id"],
                "to_agent_id": request["to_agent_id"],
                "message": request["message"],
                "created_at": request["created_at"],
                "completed_at": request.get("completed_at"),
                "response": request.get("response"),
                "error": request.get("error")
            }

            # Delete if requested
            if delete_after_read and request["status"] in ["completed", "failed", "timeout"]:
                del requests[tracking_id]
                with open(self.requests_file, 'w') as f:
                    json.dump(requests, f, indent=2)
                result["deleted"] = True

            return result

        except Exception as e:
            logger.error(f"Result retrieval failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "error_type": "result_retrieval_failed"
            }


class LettaAgentMCPServer:
    """MCP HTTP Server for Letta inter-agent communication"""

    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self.sessions: Dict[str, Session] = {}
        self.app = web.Application()

        # Configuration
        self.matrix_api_url = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
        self.matrix_homeserver = os.getenv("MATRIX_HOMESERVER_URL", "http://synapse:8008")
        self.letta_api_url = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
        self.admin_username = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
        self.admin_password = os.getenv("MATRIX_PASSWORD", "letta")

        # Register tools
        self._register_tools()

        # Setup routes
        self._setup_routes()

    def _register_tools(self):
        """Register available MCP tools"""
        # Synchronous inter-agent communication
        self.tools["matrix_agent_message"] = MatrixAgentMessageTool(
            self.matrix_api_url,
            self.matrix_homeserver,
            self.letta_api_url,
            self.admin_username,
            self.admin_password
        )

        # ASYNC TOOLS COMMENTED OUT - Using only synchronous messaging
        # Async inter-agent communication (for long-running tasks)
        # self.tools["matrix_agent_message_async"] = MatrixAgentMessageAsyncTool(
        #     self.matrix_api_url,
        #     self.matrix_homeserver,
        #     self.letta_api_url,
        #     self.admin_username,
        #     self.admin_password
        # )

        # Status checking for async messages
        # self.tools["matrix_agent_message_status"] = MatrixAgentMessageStatusTool()

        # Result retrieval for async messages
        # self.tools["matrix_agent_message_result"] = MatrixAgentMessageResultTool()

        logger.info(f"Registered {len(self.tools)} tools: {list(self.tools.keys())}")

    def _setup_routes(self):
        """Setup HTTP routes"""
        # Main MCP endpoint
        self.app.router.add_post('/mcp', self.handle_mcp_post)
        self.app.router.add_get('/mcp', self.handle_mcp_get)

        # Health check
        self.app.router.add_get('/health', self.handle_health)

    def _get_or_create_session(self, request: Request) -> Optional[Session]:
        """Get existing session or create new one"""
        session_id = request.headers.get('Mcp-Session-Id')

        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            session.last_activity = datetime.now()
            return session

        # Create new session if no ID provided
        if not session_id:
            session_id = secrets.token_urlsafe(32)
            session = Session(
                id=session_id,
                created_at=datetime.now(),
                last_activity=datetime.now()
            )
            self.sessions[session_id] = session
            return session

        return None

    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint"""
        return web.json_response({
            "status": "healthy",
            "service": "letta-agent-mcp-server",
            "sessions": len(self.sessions),
            "tools": list(self.tools.keys()),
            "timestamp": datetime.now().isoformat()
        })

    async def handle_mcp_post(self, request: Request) -> Response:
        """Handle POST requests to MCP endpoint"""
        session = self._get_or_create_session(request)

        try:
            body = await request.json()
            logger.info(f"Received MCP request: {body.get('method')}")

            # Extract x-agent-id from headers and add to request context
            agent_id = request.headers.get('x-agent-id')
            if agent_id:
                logger.info(f"Detected agent ID from header: {agent_id}")
                # Add agent ID to the params._meta context
                if 'params' not in body:
                    body['params'] = {}
                if '_meta' not in body['params']:
                    body['params']['_meta'] = {}
                body['params']['_meta']['agentId'] = agent_id

            result = await self._process_request(session, body)

            response = web.json_response(result)
            if session:
                response.headers['Mcp-Session-Id'] = session.id

            return response

        except json.JSONDecodeError:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
                status=400
            )
        except Exception as e:
            logger.error(f"Error handling POST: {e}")
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}},
                status=500
            )

    async def handle_mcp_get(self, request: Request) -> StreamResponse:
        """Handle GET requests (SSE support - minimal implementation)"""
        return web.Response(text="Use POST for MCP requests", status=405)

    async def _process_request(self, session: Optional[Session], request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single JSON-RPC request"""
        method = request.get('method')
        params = request.get('params', {})
        request_id = request.get('id')

        try:
            # Handle different methods
            if method == 'initialize':
                result = await self._handle_initialize(session, params)
            elif method == 'initialized':
                result = {"success": True}
            elif method == 'tools/list':
                result = await self._handle_list_tools()
            elif method == 'tools/call':
                result = await self._handle_tool_call(params)
            else:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id
                }

            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": request_id
            }

        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)},
                "id": request_id
            }

    async def _handle_initialize(self, session: Optional[Session], params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle initialization request"""
        return {
            "protocolVersion": "2025-03-26",
            "serverInfo": {
                "name": "letta-agent-mcp-server",
                "version": "1.0.0"
            },
            "capabilities": {
                "tools": {
                    "available": True
                }
            }
        }

    async def _handle_list_tools(self) -> Dict[str, Any]:
        """Handle tools list request"""
        tools = []
        for name, tool in self.tools.items():
            required_params = []
            properties = {}

            for param_name, param_config in tool.parameters.items():
                properties[param_name] = {
                    "type": param_config["type"],
                    "description": param_config["description"]
                }
                if "default" not in param_config:
                    required_params.append(param_name)

            tools.append({
                "name": name,
                "description": tool.description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required_params
                }
            })

        return {"tools": tools}

    async def _handle_tool_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool execution request"""
        tool_name = params.get('name')
        tool_args = params.get('arguments', {})

        # Extract context (for x-agent-id header)
        context = params.get('_meta', {})

        logger.info(f"Tool call: {tool_name} with args: {tool_args}")

        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")

        tool = self.tools[tool_name]
        result = await tool.execute(tool_args, context)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2)
                }
            ],
            "isError": "error" in result
        }

    async def start(self, host: str = "0.0.0.0", port: int = 8017):
        """Start the HTTP server"""
        runner = web.AppRunner(self.app)
        await runner.setup()

        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info(f"Letta Agent MCP Server running on http://{host}:{port}")
        logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
        logger.info(f"Health check: http://{host}:{port}/health")

        # Keep running
        await asyncio.Future()


async def main():
    """Main entry point"""
    server = LettaAgentMCPServer()

    # Get configuration from environment
    host = os.getenv("LETTA_AGENT_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("LETTA_AGENT_MCP_PORT", "8017"))

    try:
        await server.start(host, port)
    except KeyboardInterrupt:
        logger.info("Letta Agent MCP server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
