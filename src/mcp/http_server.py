#!/usr/bin/env python3
"""
MCP (Model Context Protocol) HTTP Streaming Server for Matrix Integration
Implements the Streamable HTTP transport as per MCP protocol specification

Consolidated Tools:
- matrix_room: Room discovery, management, and reading
- matrix_message: Sending, replying, and reacting to messages
- matrix_user: User profiles, invites, and kicks
"""
import asyncio
import json
import logging
import os
import sys
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field
import aiohttp
from aiohttp import web
from aiohttp.web import Request, Response, StreamResponse
import secrets
import time
from dotenv import load_dotenv
from urllib.parse import quote

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env')


@dataclass
class Session:
    """Represents an MCP session"""
    id: str
    created_at: datetime
    last_activity: datetime
    metadata: Dict[str, str] = field(default_factory=dict)
    pending_responses: Dict[str, StreamResponse] = field(default_factory=dict)
    event_counter: int = 0
    
    def generate_event_id(self) -> str:
        """Generate a unique event ID for SSE"""
        self.event_counter += 1
        return f"{self.id}-{self.event_counter}"


class MCPTool:
    """Backward-compatible base class for MCP tools used in tests."""
    def __init__(self, name: str, description: str, parameters: Optional[Dict[str, Any]] = None):
        self.name = name
        self.description = description
        self.parameters = parameters or {}
    
    async def execute(self, params: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
        raise NotImplementedError("MCPTool.execute must be implemented by subclasses")


class MatrixSendMessageTool(MCPTool):
    """Legacy single-purpose tool kept for test compatibility."""
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, letta_username: str, letta_password: str):
        super().__init__(
            name="matrix_send_message",
            description="Send a message to a Matrix room via the Matrix API"
        )
        self.matrix_api_url = matrix_api_url
        self.matrix_homeserver = matrix_homeserver
        self.letta_username = letta_username
        self.letta_password = letta_password
        self.access_token: Optional[str] = None
        self.parameters = {
            "room_id": {
                "type": "string",
                "description": "Matrix room ID to send the message to"
            },
            "message": {
                "type": "string",
                "description": "Message body to send"
            }
        }
    
    async def _login(self) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            payload = {
                "homeserver": self.matrix_homeserver,
                "user_id": self.letta_username,
                "password": self.letta_password,
                "device_name": "matrix_mcp"
            }
            async with session.post(f"{self.matrix_api_url}/login", json=payload) as response:
                if response.status != 200:
                    return {"error": await response.text()}
                result = await response.json()
                if result.get("success"):
                    return {"access_token": result.get("access_token")}
                return {"error": result.get("message", "Login failed")}
    
    async def execute(self, params: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
        room_id = (params.get("room_id", "") or "").strip()
        message = (params.get("message", "") or "").strip()
        
        if not room_id or not message:
            return {"error": "Missing required parameters: room_id, message"}
        
        if not self.access_token:
            login_result = await self._login()
            if "error" in login_result:
                return login_result
            self.access_token = login_result.get("access_token")
            if not self.access_token:
                return {"error": "Login response missing access token"}
        
        payload = {
            "room_id": room_id,
            "message": message,
            "access_token": self.access_token,
            "homeserver": self.matrix_homeserver
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.matrix_api_url}/messages/send", json=payload) as response:
                    if response.status != 200:
                        return {"error": f"Matrix API request failed: {response.status}"}
                    result = await response.json()
                    if result.get("success", True):
                        return {"success": True, "event_id": result.get("event_id", "")}
                    return {"error": result.get("message", "Failed to send message")}
        except Exception as exc:
            return {"error": str(exc)}


class MatrixAuth:
    """Shared authentication manager for Matrix API"""
    def __init__(self, matrix_api_url: str, matrix_homeserver: str, username: str, password: str):
        self.matrix_api_url = matrix_api_url
        self.matrix_homeserver = matrix_homeserver
        self.username = username
        self.password = password
        self.access_token: Optional[str] = None
        self.token_expires: Optional[datetime] = None
    
    async def get_token(self) -> str:
        """Get a valid access token, refreshing if needed"""
        if self.access_token:
            return self.access_token
        
        async with aiohttp.ClientSession() as session:
            url = f"{self.matrix_api_url}/login"
            payload = {
                "homeserver": self.matrix_homeserver,
                "user_id": self.username,
                "password": self.password,
                "device_name": "mcp_server"
            }
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"Login failed: {error_text}")
                result = await response.json()
                if result.get("success"):
                    self.access_token = result.get("access_token")
                    if not self.access_token:
                        raise Exception("No access token in response")
                    
                    # Auto-join all agent rooms after successful login
                    await self._auto_join_rooms()
                    
                    return self.access_token
                raise Exception(f"Login failed: {result.get('message', 'Unknown error')}")
    
    async def _auto_join_rooms(self) -> None:
        """Auto-join all agent rooms after login"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.matrix_api_url}/rooms/auto-join"
                payload = {
                    "user_id": self.username,
                    "access_token": self.access_token,
                    "homeserver": self.matrix_homeserver
                }
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Auto-join complete: {result.get('message')}")
                    else:
                        error_text = await response.text()
                        logger.warning(f"Auto-join failed: {error_text}")
        except Exception as e:
            logger.error(f"Error in auto-join: {e}")


class MatrixRoomTool:
    """
    Consolidated tool for room operations.
    
    Actions:
    - list: List all joined rooms
    - search: Search rooms by query
    - create: Create a new room
    - join: Join a room
    - leave: Leave a room
    - members: Get room members
    - topic: Set room topic
    - read: Read messages from a room
    """
    def __init__(self, auth: MatrixAuth):
        self.auth = auth
        self.name = "matrix_room"
        self.description = "Manage Matrix rooms: list, search, create, join, leave, get members, set topic, read messages"
        self.parameters = {
            "action": {
                "type": "string",
                "description": "Action to perform: list, search, create, join, leave, members, topic, read",
                "enum": ["list", "search", "create", "join", "leave", "members", "topic", "read"]
            },
            "room_id": {
                "type": "string",
                "description": "Room ID (required for: join, leave, members, topic, read)",
                "default": ""
            },
            "query": {
                "type": "string",
                "description": "Search query (for: search)",
                "default": ""
            },
            "name": {
                "type": "string",
                "description": "Room name (for: create)",
                "default": ""
            },
            "topic": {
                "type": "string",
                "description": "Room topic (for: create, topic)",
                "default": ""
            },
            "is_public": {
                "type": "boolean",
                "description": "Whether room is public (for: create)",
                "default": False
            },
            "limit": {
                "type": "integer",
                "description": "Max results (for: search, read)",
                "default": 20
            }
        }
    
    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        action = params.get("action", "")
        
        if not action:
            return {"error": "Missing required parameter: action"}
        
        try:
            if action == "list":
                return await self._list_rooms()
            elif action == "search":
                query = params.get("query", "")
                limit = int(params.get("limit", 20))
                return await self._search_rooms(query, limit)
            elif action == "create":
                name = params.get("name", "")
                topic = params.get("topic", "")
                is_public = params.get("is_public", False)
                if isinstance(is_public, str):
                    is_public = is_public.lower() == "true"
                return await self._create_room(name, topic, bool(is_public))
            elif action == "join":
                room_id = params.get("room_id", "")
                return await self._join_room(room_id)
            elif action == "leave":
                room_id = params.get("room_id", "")
                return await self._leave_room(room_id)
            elif action == "members":
                room_id = params.get("room_id", "")
                return await self._get_members(room_id)
            elif action == "topic":
                room_id = params.get("room_id", "")
                topic = params.get("topic", "")
                return await self._set_topic(room_id, topic)
            elif action == "read":
                room_id = params.get("room_id", "")
                limit = int(params.get("limit", 10))
                return await self._read_room(room_id, limit)
            else:
                return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _list_rooms(self) -> Dict[str, Any]:
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_api_url}/rooms/list"
            params = {"access_token": token, "homeserver": self.auth.matrix_homeserver}
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return {"error": f"Failed to list rooms: {await response.text()}"}
                result = await response.json()
                if result.get("success"):
                    return {"success": True, "rooms": result.get("rooms", [])}
                return {"error": result.get("message", "Unknown error")}
    
    async def _search_rooms(self, query: str, limit: int) -> Dict[str, Any]:
        if not query:
            return {"error": "Missing required parameter: query"}
        
        result = await self._list_rooms()
        if "error" in result:
            return result
        
        rooms = result.get("rooms", [])
        query_lower = query.lower()
        matching = []
        
        for room in rooms:
            if not isinstance(room, dict):
                continue
            room_name = str(room.get("room_name", "") or room.get("name", "") or "").lower()
            room_topic = str(room.get("topic", "") or "").lower()
            room_alias = str(room.get("canonical_alias", "") or "").lower()
            room_id = str(room.get("room_id", "") or "").lower()
            
            if query_lower in room_name or query_lower in room_topic or query_lower in room_alias or query_lower in room_id:
                matching.append(room)
                if len(matching) >= limit:
                    break
        
        return {"success": True, "query": query, "total_matches": len(matching), "rooms": matching}
    
    async def _create_room(self, name: str, topic: str, is_public: bool) -> Dict[str, Any]:
        if not name:
            return {"error": "Missing required parameter: name"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/createRoom"
            headers = {"Authorization": f"Bearer {token}"}
            payload: Dict[str, str] = {
                "name": name,
                "visibility": "public" if is_public else "private",
                "preset": "public_chat" if is_public else "private_chat"
            }
            if topic:
                payload["topic"] = topic
            
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"success": True, "room_id": data.get("room_id", "")}
                return {"error": f"Failed to create room: {await response.text()}"}
    
    async def _join_room(self, room_id: str) -> Dict[str, Any]:
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/join/{quote(room_id, safe='')}"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.post(url, headers=headers, json={}) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"success": True, "room_id": data.get("room_id", room_id)}
                return {"error": f"Failed to join room: {await response.text()}"}
    
    async def _leave_room(self, room_id: str) -> Dict[str, Any]:
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/leave"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.post(url, headers=headers, json={}) as response:
                if response.status == 200:
                    return {"success": True, "room_id": room_id}
                return {"error": f"Failed to leave room: {await response.text()}"}
    
    async def _get_members(self, room_id: str) -> Dict[str, Any]:
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/members"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(url, headers=headers, params={"membership": "join"}) as response:
                if response.status == 200:
                    data = await response.json()
                    members = []
                    for event in data.get("chunk", []):
                        if event.get("type") == "m.room.member":
                            content = event.get("content", {})
                            members.append({
                                "user_id": event.get("state_key", ""),
                                "display_name": content.get("displayname", ""),
                                "membership": content.get("membership", "")
                            })
                    return {"success": True, "room_id": room_id, "members": members, "count": len(members)}
                return {"error": f"Failed to get members: {await response.text()}"}
    
    async def _set_topic(self, room_id: str, topic: str) -> Dict[str, Any]:
        if not room_id or not topic:
            return {"error": "Missing required parameters: room_id, topic"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/state/m.room.topic"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.put(url, headers=headers, json={"topic": topic}) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"success": True, "room_id": room_id, "topic": topic, "event_id": data.get("event_id", "")}
                return {"error": f"Failed to set topic: {await response.text()}"}
    
    async def _read_room(self, room_id: str, limit: int) -> Dict[str, Any]:
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_api_url}/messages/get"
            payload = {
                "room_id": room_id,
                "access_token": token,
                "homeserver": self.auth.matrix_homeserver,
                "limit": limit
            }
            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("success"):
                        messages = result.get("messages", [])
                        return {"success": True, "room_id": room_id, "messages": messages, "count": len(messages)}
                    return {"error": result.get("message", "Unknown error")}
                return {"error": f"Failed to read room: {await response.text()}"}


class MatrixMessageTool:
    """
    Consolidated tool for message operations.
    
    Actions:
    - send: Send a message to a room
    - reply: Reply to a specific message
    - react: Add a reaction to a message
    """
    def __init__(self, auth: MatrixAuth):
        self.auth = auth
        self.name = "matrix_message"
        self.description = "Send, reply to, and react to Matrix messages"
        self.parameters = {
            "action": {
                "type": "string",
                "description": "Action to perform: send, reply, react",
                "enum": ["send", "reply", "react"]
            },
            "room_id": {
                "type": "string",
                "description": "Room ID (required for all actions)"
            },
            "message": {
                "type": "string",
                "description": "Message text (for: send, reply)",
                "default": ""
            },
            "event_id": {
                "type": "string",
                "description": "Event ID to reply to or react to (for: reply, react)",
                "default": ""
            },
            "reaction": {
                "type": "string",
                "description": "Reaction emoji (for: react)",
                "default": ""
            }
        }
    
    async def execute(self, params: Dict[str, str]) -> Dict[str, Any]:
        action = params.get("action", "")
        room_id = params.get("room_id", "")
        
        if not action:
            return {"error": "Missing required parameter: action"}
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        try:
            if action == "send":
                message = params.get("message", "")
                return await self._send_message(room_id, message)
            elif action == "reply":
                message = params.get("message", "")
                event_id = params.get("event_id", "")
                return await self._reply_message(room_id, event_id, message)
            elif action == "react":
                event_id = params.get("event_id", "")
                reaction = params.get("reaction", "")
                return await self._react_message(room_id, event_id, reaction)
            else:
                return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _send_message(self, room_id: str, message: str) -> Dict[str, Any]:
        if not message:
            return {"error": "Missing required parameter: message"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_api_url}/messages/send"
            payload = {
                "room_id": room_id,
                "message": message,
                "access_token": token,
                "homeserver": self.auth.matrix_homeserver
            }
            async with session.post(url, json=payload) as response:
                result = await response.json()
                if response.status == 200 and result.get("success"):
                    return {"success": True, "event_id": result.get("event_id", ""), "room_id": room_id}
                return {"error": f"Failed to send message: {result.get('message', 'Unknown error')}"}
    
    async def _reply_message(self, room_id: str, event_id: str, message: str) -> Dict[str, Any]:
        if not event_id:
            return {"error": "Missing required parameter: event_id"}
        if not message:
            return {"error": "Missing required parameter: message"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            # Fetch original event for reply fallback
            orig_url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/event/{quote(event_id, safe='')}"
            headers = {"Authorization": f"Bearer {token}"}
            
            original_body = ""
            original_sender = ""
            async with session.get(orig_url, headers=headers) as orig_response:
                if orig_response.status == 200:
                    orig_data = await orig_response.json()
                    original_body = orig_data.get("content", {}).get("body", "")
                    original_sender = orig_data.get("sender", "")
            
            # Send reply
            txn_id = f"mcp_reply_{int(time.time() * 1000)}"
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.room.message/{txn_id}"
            
            fallback_text = f"> <{original_sender}> {original_body[:50]}{'...' if len(original_body) > 50 else ''}\n\n{message}"
            
            payload = {
                "msgtype": "m.text",
                "body": fallback_text,
                "m.relates_to": {
                    "m.in_reply_to": {"event_id": event_id}
                }
            }
            
            async with session.put(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"success": True, "event_id": data.get("event_id", ""), "room_id": room_id, "reply_to": event_id}
                return {"error": f"Failed to send reply: {await response.text()}"}
    
    async def _react_message(self, room_id: str, event_id: str, reaction: str) -> Dict[str, Any]:
        if not event_id:
            return {"error": "Missing required parameter: event_id"}
        if not reaction:
            return {"error": "Missing required parameter: reaction"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            txn_id = f"mcp_react_{int(time.time() * 1000)}"
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/send/m.reaction/{txn_id}"
            headers = {"Authorization": f"Bearer {token}"}
            payload = {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": event_id,
                    "key": reaction
                }
            }
            
            async with session.put(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    return {"success": True, "event_id": data.get("event_id", ""), "room_id": room_id, "target_event": event_id, "reaction": reaction}
                return {"error": f"Failed to add reaction: {await response.text()}"}


class MatrixUserTool:
    """
    Consolidated tool for user operations.
    
    Actions:
    - profile: Get user profile
    - invite: Invite user to a room
    - kick: Kick user from a room
    """
    def __init__(self, auth: MatrixAuth):
        self.auth = auth
        self.name = "matrix_user"
        self.description = "Manage Matrix users: get profile, invite to room, kick from room"
        self.parameters = {
            "action": {
                "type": "string",
                "description": "Action to perform: profile, invite, kick",
                "enum": ["profile", "invite", "kick"]
            },
            "user_id": {
                "type": "string",
                "description": "User ID (required for all actions)"
            },
            "room_id": {
                "type": "string",
                "description": "Room ID (for: invite, kick)",
                "default": ""
            },
            "reason": {
                "type": "string",
                "description": "Reason for kick (for: kick)",
                "default": ""
            }
        }
    
    async def execute(self, params: Dict[str, str]) -> Dict[str, Any]:
        action = params.get("action", "")
        user_id = params.get("user_id", "")
        
        if not action:
            return {"error": "Missing required parameter: action"}
        if not user_id:
            return {"error": "Missing required parameter: user_id"}
        
        try:
            if action == "profile":
                return await self._get_profile(user_id)
            elif action == "invite":
                room_id = params.get("room_id", "")
                return await self._invite_user(room_id, user_id)
            elif action == "kick":
                room_id = params.get("room_id", "")
                reason = params.get("reason", "")
                return await self._kick_user(room_id, user_id, reason)
            else:
                return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": str(e)}
    
    async def _get_profile(self, user_id: str) -> Dict[str, Any]:
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/profile/{quote(user_id, safe='')}"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        "success": True,
                        "user_id": user_id,
                        "display_name": data.get("displayname", ""),
                        "avatar_url": data.get("avatar_url", "")
                    }
                elif response.status == 404:
                    return {"error": f"User not found: {user_id}"}
                return {"error": f"Failed to get profile: {await response.text()}"}
    
    async def _invite_user(self, room_id: str, user_id: str) -> Dict[str, Any]:
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/invite"
            headers = {"Authorization": f"Bearer {token}"}
            async with session.post(url, headers=headers, json={"user_id": user_id}) as response:
                if response.status == 200:
                    return {"success": True, "room_id": room_id, "invited_user": user_id}
                return {"error": f"Failed to invite user: {await response.text()}"}
    
    async def _kick_user(self, room_id: str, user_id: str, reason: str) -> Dict[str, Any]:
        if not room_id:
            return {"error": "Missing required parameter: room_id"}
        
        token = await self.auth.get_token()
        async with aiohttp.ClientSession() as session:
            url = f"{self.auth.matrix_homeserver}/_matrix/client/v3/rooms/{quote(room_id, safe='')}/kick"
            headers = {"Authorization": f"Bearer {token}"}
            payload: Dict[str, str] = {"user_id": user_id}
            if reason:
                payload["reason"] = reason
            
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    return {"success": True, "room_id": room_id, "kicked_user": user_id}
                return {"error": f"Failed to kick user: {await response.text()}"}


class MCPHTTPServer:
    """MCP HTTP Streaming Server implementation"""
    
    def __init__(self) -> None:
        self.tools: Dict[str, MatrixRoomTool | MatrixMessageTool | MatrixUserTool] = {}
        self.sessions: Dict[str, Session] = {}
        self.app = web.Application()
        
        # Configuration
        matrix_api_url = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
        # Use MATRIX_HOMESERVER_API_URL for API calls (from API's network perspective)
        # Fall back to MATRIX_HOMESERVER_URL for backward compatibility
        matrix_homeserver = os.getenv("MATRIX_HOMESERVER_API_URL") or os.getenv("MATRIX_HOMESERVER_URL", "http://synapse:8008")
        letta_username = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
        letta_password = os.getenv("MATRIX_PASSWORD", "letta")
        
        # Shared auth manager
        self.auth = MatrixAuth(matrix_api_url, matrix_homeserver, letta_username, letta_password)
        
        # Register consolidated tools
        self._register_tools()
        self._setup_routes()
    
    def _register_tools(self) -> None:
        """Register the 3 consolidated MCP tools"""
        self.tools["matrix_room"] = MatrixRoomTool(self.auth)
        self.tools["matrix_message"] = MatrixMessageTool(self.auth)
        self.tools["matrix_user"] = MatrixUserTool(self.auth)
        
        logger.info(f"Registered {len(self.tools)} tools: {list(self.tools.keys())}")
    
    def _setup_routes(self) -> None:
        """Setup HTTP routes"""
        self.app.router.add_post('/mcp', self.handle_mcp_post)
        self.app.router.add_get('/mcp', self.handle_mcp_get)
        self.app.router.add_delete('/mcp', self.handle_session_delete)
        self.app.router.add_get('/health', self.handle_health)
    
    def _validate_origin(self, request: Request) -> bool:
        """Validate Origin header to prevent DNS rebinding attacks"""
        origin = request.headers.get('Origin', '')
        allowed_origins = [
            'http://localhost',
            'http://127.0.0.1',
            'https://matrix.oculair.ca',
            'http://192.168.50.90',
            'http://192.168.50.1',
            'https://claude.ai'
        ]
        if not origin:
            return True
        return any(origin.startswith(allowed) for allowed in allowed_origins)
    
    def _get_or_create_session(self, request: Request) -> Optional[Session]:
        """Get existing session or create new one"""
        session_id = request.headers.get('Mcp-Session-Id')
        
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
            session.last_activity = datetime.now()
            return session
        
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
        tool_info = []
        for name, tool in self.tools.items():
            actions = []
            if "action" in tool.parameters and "enum" in tool.parameters["action"]:
                actions = tool.parameters["action"]["enum"]
            tool_info.append({"name": name, "actions": actions})
        
        return web.json_response({
            "status": "healthy",
            "sessions": len(self.sessions),
            "tools": tool_info
        })
    
    async def handle_mcp_post(self, request: Request) -> Response | StreamResponse:
        """Handle POST requests to MCP endpoint"""
        if not self._validate_origin(request):
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Invalid origin"}},
                status=403
            )
        
        accept = request.headers.get('Accept', '')
        if 'application/json' not in accept and 'text/event-stream' not in accept:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Accept header must include application/json or text/event-stream"}},
                status=400
            )
        
        session = self._get_or_create_session(request)
        if not session and request.headers.get('Mcp-Session-Id'):
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32600, "message": "Session not found"}},
                status=404
            )
        
        try:
            body = await request.json()
            messages = body if isinstance(body, list) else [body]
            
            requests = []
            notifications = []
            
            for msg in messages:
                if 'method' in msg and 'id' in msg:
                    requests.append(msg)
                elif 'method' in msg:
                    notifications.append(msg)
            
            if not requests:
                return web.Response(status=202)
            
            # Ensure session exists
            if session is None:
                session = Session(
                    id=secrets.token_urlsafe(32),
                    created_at=datetime.now(),
                    last_activity=datetime.now()
                )
                self.sessions[session.id] = session
            
            if 'text/event-stream' in accept:
                return await self._handle_sse_response(request, session, requests)
            else:
                return await self._handle_json_response(session, requests[0])
        
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
    
    async def handle_mcp_get(self, request: Request) -> Response | StreamResponse:
        """Handle GET requests to open SSE stream"""
        if not self._validate_origin(request):
            return web.Response(text="Forbidden", status=403)
        
        if 'text/event-stream' not in request.headers.get('Accept', ''):
            return web.Response(text="Method Not Allowed", status=405)
        
        session = self._get_or_create_session(request)
        if not session and request.headers.get('Mcp-Session-Id'):
            return web.Response(text="Session not found", status=404)
        
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        
        if session and not request.headers.get('Mcp-Session-Id'):
            response.headers['Mcp-Session-Id'] = session.id
        
        await response.prepare(request)
        
        stream_id: Optional[str] = None
        if session:
            stream_id = str(uuid.uuid4())
            session.pending_responses[stream_id] = response
        
        try:
            while True:
                await asyncio.sleep(30)
                await response.write(b': keepalive\n\n')
        except Exception:
            pass
        finally:
            if session and stream_id is not None and stream_id in session.pending_responses:
                del session.pending_responses[stream_id]
        
        return response
    
    async def handle_session_delete(self, request: Request) -> Response:
        """Handle DELETE request to terminate session"""
        session_id = request.headers.get('Mcp-Session-Id')
        
        if not session_id:
            return web.Response(text="Bad Request", status=400)
        
        if session_id in self.sessions:
            session = self.sessions[session_id]
            for resp in session.pending_responses.values():
                try:
                    await resp.write_eof()
                except Exception:
                    pass
            del self.sessions[session_id]
            return web.Response(status=204)
        
        return web.Response(text="Not Found", status=404)
    
    async def _handle_json_response(self, session: Session, request: Dict[str, str]) -> Response:
        """Handle single JSON response"""
        result = await self._process_request(request)
        response = web.json_response(result)
        response.headers['Mcp-Session-Id'] = session.id
        return response
    
    async def _handle_sse_response(self, request: Request, session: Session, requests: List[Dict[str, str]]) -> StreamResponse:
        """Handle SSE stream response"""
        response = web.StreamResponse()
        response.headers['Content-Type'] = 'text/event-stream'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['Mcp-Session-Id'] = session.id
        
        await response.prepare(request)
        
        try:
            for req in requests:
                result = await self._process_request(req)
                event_id = session.generate_event_id()
                data = f"id: {event_id}\ndata: {json.dumps(result)}\n\n"
                await response.write(data.encode('utf-8'))
            await response.write_eof()
        except Exception as e:
            logger.error(f"Error in SSE stream: {e}")
        
        return response
    
    async def _process_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single JSON-RPC request"""
        method = request.get('method', '')
        params = request.get('params', {})
        request_id = request.get('id')
        
        try:
            if method == 'initialize':
                result = {
                    "protocolVersion": "2025-03-26",
                    "serverInfo": {"name": "matrix-mcp-server", "version": "2.0.0"},
                    "capabilities": {"tools": {"available": True}}
                }
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
            
            return {"jsonrpc": "2.0", "result": result, "id": request_id}
        
        except Exception as e:
            logger.error(f"Error processing request: {e}")
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)},
                "id": request_id
            }
    
    async def _handle_list_tools(self) -> Dict[str, Any]:
        """Handle tools list request"""
        tools = []
        for name, tool in self.tools.items():
            properties = {}
            required = []
            
            for param_name, param_config in tool.parameters.items():
                prop: Dict[str, str | List[str]] = {
                    "type": param_config["type"],
                    "description": param_config["description"]
                }
                if "enum" in param_config:
                    prop["enum"] = param_config["enum"]
                properties[param_name] = prop
                
                if "default" not in param_config:
                    required.append(param_name)
            
            tools.append({
                "name": name,
                "description": tool.description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            })
        
        return {"tools": tools}
    
    async def _handle_tool_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool execution request"""
        tool_name = str(params.get('name', ''))
        tool_args = params.get('arguments', {})
        if not isinstance(tool_args, dict):
            tool_args = {}
        
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")
        
        tool = self.tools[tool_name]
        result = await tool.execute(tool_args)
        
        return {
            "content": [{"type": "text", "text": json.dumps(result)}],
            "isError": "error" in result
        }
    
    async def cleanup_sessions(self) -> None:
        """Periodic cleanup of inactive sessions"""
        while True:
            try:
                await asyncio.sleep(300)
                now = datetime.now()
                expired = [
                    sid for sid, s in self.sessions.items()
                    if (now - s.last_activity).total_seconds() > 3600
                ]
                for sid in expired:
                    session = self.sessions[sid]
                    for resp in session.pending_responses.values():
                        try:
                            await resp.write_eof()
                        except Exception:
                            pass
                    del self.sessions[sid]
                    logger.info(f"Cleaned up expired session: {sid}")
            except Exception as e:
                logger.error(f"Error in session cleanup: {e}")
    
    async def start(self, host: str = "0.0.0.0", port: int = 8006) -> None:
        """Start the HTTP server"""
        asyncio.create_task(self.cleanup_sessions())
        
        runner = web.AppRunner(self.app)
        await runner.setup()
        
        site = web.TCPSite(runner, host, port)
        await site.start()
        
        logger.info(f"MCP HTTP Server running on http://{host}:{port}")
        logger.info(f"MCP endpoint: http://{host}:{port}/mcp")
        
        await asyncio.Future()


async def main() -> None:
    """Main entry point"""
    server = MCPHTTPServer()
    
    host = os.getenv("LETTA_AGENT_MCP_HOST") or os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    port = int(os.getenv("LETTA_AGENT_MCP_PORT") or os.getenv("MCP_HTTP_PORT", "8006"))
    
    try:
        await server.start(host, port)
    except KeyboardInterrupt:
        logger.info("MCP HTTP server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
