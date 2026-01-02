#!/usr/bin/env python3
"""
Matrix REST API - HTTP endpoints for Matrix functionality
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
import os
import uvicorn

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Import agent sync functionality
try:
    from ..core.agent_user_manager import run_agent_sync
    from ..matrix.client import Config
    AGENT_SYNC_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Agent sync not available: {e}")
    AGENT_SYNC_AVAILABLE = False

# Import Letta webhook functionality
try:
    from ..letta.webhook_handler import (
        LettaWebhookPayload,
        LettaWebhookHandler,
        WebhookConfig,
        initialize_webhook_handler,
        get_webhook_handler,
    )
    from ..bridges.letta_matrix_bridge import (
        LettaMatrixBridge,
        BridgeConfig,
        initialize_bridge,
        get_bridge,
    )
    LETTA_WEBHOOK_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Letta webhook not available: {e}")
    LETTA_WEBHOOK_AVAILABLE = False

# Load environment variables
load_dotenv('.env')

# FastAPI app
app = FastAPI(
    title="Matrix API",
    description="REST API for Matrix messaging operations",
    version="1.0.0"
)

# Pydantic models for request/response
class LoginRequest(BaseModel):
    homeserver: str
    user_id: str
    password: str
    device_name: Optional[str] = "matrix_api"

class LoginResponse(BaseModel):
    success: bool
    access_token: Optional[str] = None
    device_id: Optional[str] = None
    user_id: Optional[str] = None
    message: str

class SendMessageRequest(BaseModel):
    room_id: str
    message: str
    access_token: str
    homeserver: str

class SendMessageResponse(BaseModel):
    success: bool
    event_id: Optional[str] = None
    message: str

class GetMessagesRequest(BaseModel):
    room_id: str
    access_token: str
    homeserver: str
    limit: Optional[int] = 5

class MatrixMessage(BaseModel):
    sender: str
    body: str
    timestamp: int
    formatted_time: str
    event_id: str

class GetMessagesResponse(BaseModel):
    success: bool
    messages: List[MatrixMessage] = []
    message: str

class RoomInfo(BaseModel):
    room_id: str
    room_name: str

class ListRoomsResponse(BaseModel):
    success: bool
    rooms: List[RoomInfo] = []
    message: str

class NewAgentNotification(BaseModel):
    agent_id: str
    timestamp: str

class WebhookResponse(BaseModel):
    success: bool
    message: str
    timestamp: str

class AutoJoinRequest(BaseModel):
    user_id: str
    access_token: str
    homeserver: str

# Global Matrix client for the API
class MatrixAPIClient:
    def __init__(self):
        self.homeserver = os.environ.get("MATRIX_HOMESERVER_URL")
        self.user_id = os.environ.get("MATRIX_USERNAME")
        self.password = os.environ.get("MATRIX_PASSWORD")
        self.access_token = None
        self.device_id = None
        self.authenticated = False

    async def login(self, homeserver: str = None, user_id: str = None, password: str = None, device_name: str = "matrix_api"):
        """Login to Matrix server."""
        try:
            # Use provided credentials or fallback to environment
            homeserver = homeserver or self.homeserver
            user_id = user_id or self.user_id
            password = password or self.password

            if not all([homeserver, user_id, password]):
                return LoginResponse(success=False, message="Missing required credentials")

            url = f"{homeserver}/_matrix/client/v3/login"
            headers = {"Content-Type": "application/json"}
            
            login_data = {
                "type": "m.login.password",
                "identifier": {
                    "type": "m.id.user",
                    "user": user_id
                },
                "password": password,
                "initial_device_display_name": device_name
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=login_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        access_token = result.get('access_token')
                        device_id = result.get('device_id')
                        user_id = result.get('user_id')
                        
                        # Store credentials for this session
                        self.access_token = access_token
                        self.device_id = device_id
                        self.authenticated = True
                        
                        return LoginResponse(
                            success=True,
                            access_token=access_token,
                            device_id=device_id,
                            user_id=user_id,
                            message="Login successful"
                        )
                    else:
                        error_text = await response.text()
                        return LoginResponse(success=False, message=f"Login failed: {error_text}")
                        
        except Exception as e:
            return LoginResponse(success=False, message=f"Error during login: {str(e)}")

    async def send_message(self, homeserver: str, access_token: str, room_id: str, message: str):
        """Send a message to a Matrix room."""
        try:
            # Generate transaction ID
            import time
            txn_id = f"api{int(time.time() * 1000)}"
            
            url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            message_data = {
                "msgtype": "m.text",
                "body": message
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=message_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        event_id = result.get("event_id")
                        return SendMessageResponse(
                            success=True,
                            event_id=event_id,
                            message="Message sent successfully"
                        )
                    else:
                        error_text = await response.text()
                        return SendMessageResponse(success=False, message=f"Failed to send message: {error_text}")
                        
        except Exception as e:
            return SendMessageResponse(success=False, message=f"Error sending message: {str(e)}")

    async def get_messages(self, homeserver: str, access_token: str, room_id: str, limit: int = 5):
        """Get recent messages from a Matrix room."""
        try:
            url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/messages"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            params = {
                "dir": "b",
                "limit": limit,
                "filter": json.dumps({
                    "types": ["m.room.message"]
                })
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        result = await response.json()
                        events = result.get("chunk", [])
                        
                        messages = []
                        for event in reversed(events):  # Show oldest first
                            if event.get("type") == "m.room.message":
                                content = event.get("content", {})
                                body = content.get("body", "")
                                sender = event.get("sender", "unknown")
                                timestamp = event.get("origin_server_ts", 0)
                                event_id = event.get("event_id", "")
                                
                                formatted_time = self.format_timestamp(timestamp)
                                
                                messages.append(MatrixMessage(
                                    sender=sender,
                                    body=body,
                                    timestamp=timestamp,
                                    formatted_time=formatted_time,
                                    event_id=event_id
                                ))
                        
                        return GetMessagesResponse(
                            success=True,
                            messages=messages,
                            message=f"Retrieved {len(messages)} messages"
                        )
                    else:
                        error_text = await response.text()
                        return GetMessagesResponse(success=False, message=f"Failed to get messages: {error_text}")
                        
        except Exception as e:
            return GetMessagesResponse(success=False, message=f"Error getting messages: {str(e)}")

    async def list_rooms(self, homeserver: str, access_token: str):
        """List joined rooms."""
        try:
            url = f"{homeserver}/_matrix/client/v3/joined_rooms"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        room_ids = result.get("joined_rooms", [])
                        
                        rooms = []
                        for room_id in room_ids:
                            room_name = await self.get_room_name(homeserver, access_token, room_id)
                            rooms.append(RoomInfo(room_id=room_id, room_name=room_name))
                        
                        return ListRoomsResponse(
                            success=True,
                            rooms=rooms,
                            message=f"Found {len(rooms)} rooms"
                        )
                    else:
                        error_text = await response.text()
                        return ListRoomsResponse(success=False, message=f"Failed to list rooms: {error_text}")
                        
        except Exception as e:
            return ListRoomsResponse(success=False, message=f"Error listing rooms: {str(e)}")

    async def get_room_name(self, homeserver: str, access_token: str, room_id: str):
        """Get room display name."""
        try:
            url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()
                        return result.get("name", room_id)
                    else:
                        return room_id
                        
        except Exception as e:
            return room_id

    def format_timestamp(self, timestamp_ms):
        """Convert Matrix timestamp to readable format."""
        try:
            timestamp_s = timestamp_ms / 1000
            dt = datetime.fromtimestamp(timestamp_s)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            return "Unknown time"

# Global client instance
matrix_client = MatrixAPIClient()

# API Endpoints
@app.get("/")
async def root():
    """API root endpoint."""
    return {"message": "Matrix API", "version": "1.0.0", "status": "running"}

@app.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """Login to Matrix server."""
    result = await matrix_client.login(
        homeserver=request.homeserver,
        user_id=request.user_id,
        password=request.password,
        device_name=request.device_name
    )
    return result

@app.get("/login/auto", response_model=LoginResponse)
async def auto_login():
    """Auto-login using environment variables."""
    result = await matrix_client.login()
    return result

@app.post("/messages/send", response_model=SendMessageResponse)
async def send_message(request: SendMessageRequest):
    """Send a message to a Matrix room."""
    result = await matrix_client.send_message(
        homeserver=request.homeserver,
        access_token=request.access_token,
        room_id=request.room_id,
        message=request.message
    )
    return result

@app.post("/messages/get", response_model=GetMessagesResponse)
async def get_messages(request: GetMessagesRequest):
    """Get recent messages from a Matrix room."""
    result = await matrix_client.get_messages(
        homeserver=request.homeserver,
        access_token=request.access_token,
        room_id=request.room_id,
        limit=request.limit
    )
    return result

@app.get("/rooms/list")
async def list_rooms(homeserver: str, access_token: str):
    """List joined rooms."""
    result = await matrix_client.list_rooms(homeserver, access_token)
    return result

@app.get("/messages/recent")
async def get_recent_messages(homeserver: str, access_token: str, limit: int = 10):
    """Get the most recent messages across all joined rooms."""
    try:
        # First get all joined rooms
        rooms_result = await matrix_client.list_rooms(homeserver, access_token)
        if not rooms_result.success:
            return {"success": False, "message": f"Failed to get rooms: {rooms_result.message}"}
        
        all_messages = []
        
        # Get recent messages from each room
        for room in rooms_result.rooms:
            messages_result = await matrix_client.get_messages(
                homeserver, access_token, room.room_id, limit=5  # Get 5 from each room
            )
            
            if messages_result.success:
                for msg in messages_result.messages:
                    # Add room info to each message
                    message_with_room = {
                        "room_id": room.room_id,
                        "room_name": room.room_name,
                        "sender": msg.sender,
                        "body": msg.body,
                        "timestamp": msg.timestamp,
                        "formatted_time": msg.formatted_time,
                        "event_id": msg.event_id
                    }
                    all_messages.append(message_with_room)
        
        # Sort all messages by timestamp (most recent first)
        all_messages.sort(key=lambda x: x["timestamp"], reverse=True)
        
        # Return the most recent N messages
        recent_messages = all_messages[:limit]
        
        return {
            "success": True,
            "messages": recent_messages,
            "total_found": len(all_messages),
            "limit": limit,
            "message": f"Retrieved {len(recent_messages)} most recent messages from {len(rooms_result.rooms)} rooms"
        }
        
    except Exception as e:
        return {"success": False, "message": f"Error getting recent messages: {str(e)}"}

@app.post("/webhook/new-agent", response_model=WebhookResponse)
async def new_agent_webhook(notification: NewAgentNotification, background_tasks: BackgroundTasks):
    """Webhook endpoint to receive new agent notifications from Letta webhook receiver."""
    try:
        logger.info(f"Received new agent notification: {notification.agent_id}")
        
        if not AGENT_SYNC_AVAILABLE:
            return WebhookResponse(
                success=False,
                message="Agent sync functionality not available",
                timestamp=datetime.now().isoformat()
            )
        
        # Trigger immediate agent sync in background
        async def trigger_agent_sync():
            try:
                config = Config.from_env()
                await run_agent_sync(config)
                logger.info(f"Successfully synced new agent: {notification.agent_id}")
            except Exception as e:
                logger.error(f"Failed to sync new agent {notification.agent_id}: {e}")
        
        background_tasks.add_task(trigger_agent_sync)
        
        return WebhookResponse(
            success=True,
            message=f"Triggered sync for new agent: {notification.agent_id}",
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"Error processing new agent webhook: {e}")
        return WebhookResponse(
            success=False,
            message=f"Error: {str(e)}",
            timestamp=datetime.now().isoformat()
        )


@app.post("/webhooks/letta/agent-response")
async def letta_agent_response_webhook(request: dict, background_tasks: BackgroundTasks):
    """
    Webhook endpoint for Letta agent.run.completed events.
    
    This receives webhooks from Letta when an agent completes a run,
    and posts the response to the agent's Matrix room as an audit message.
    """
    if not LETTA_WEBHOOK_AVAILABLE:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "Letta webhook functionality not available"}
        )
    
    try:
        payload = LettaWebhookPayload(**request)
        
        if payload.event_type != "agent.run.completed":
            logger.info(f"Ignoring non-run-completed webhook: {payload.event_type}")
            return {"success": True, "message": "Event type ignored", "event_type": payload.event_type}
        
        handler = get_webhook_handler()
        if not handler:
            handler = initialize_webhook_handler()
            bridge = initialize_bridge()
            handler.set_bridge(bridge)
        
        async def process_webhook():
            try:
                result = await handler.handle_run_completed(payload)
                if result.success:
                    logger.info(f"Webhook processed for agent {result.agent_id}: posted={result.response_posted}")
                else:
                    logger.warning(f"Webhook processing failed for agent {result.agent_id}: {result.error}")
            except Exception as e:
                logger.exception(f"Error processing Letta webhook: {e}")
        
        background_tasks.add_task(process_webhook)
        
        return {"success": True, "message": "Webhook received", "agent_id": payload.agent_id}
        
    except Exception as e:
        logger.exception(f"Error parsing Letta webhook: {e}")
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(e)}
        )


@app.get("/agents/mappings")
async def get_agent_mappings():
    """Get all agent-to-room mappings."""
    try:
        from src.core.mapping_service import get_all_mappings
        mappings = get_all_mappings()

        return {
            "success": True,
            "message": f"Retrieved {len(mappings)} agent mappings",
            "mappings": mappings
        }
    except Exception as e:
        logger.error(f"Error reading agent mappings: {e}")
        return {
            "success": False,
            "message": f"Error: {str(e)}",
            "mappings": {}
        }

@app.get("/agents/{agent_id}/room")
async def get_agent_room(agent_id: str):
    """Get the Matrix room ID for a specific agent."""
    try:
        from src.core.mapping_service import get_mapping_by_agent_id
        mapping = get_mapping_by_agent_id(agent_id)

        if not mapping:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found in mappings")

        if not mapping.get("room_created") or not mapping.get("room_id"):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} does not have a room created yet")

        return {
            "success": True,
            "agent_id": agent_id,
            "agent_name": mapping.get("agent_name"),
            "room_id": mapping.get("room_id"),
            "matrix_user_id": mapping.get("matrix_user_id"),
            "room_created": mapping.get("room_created"),
            "invitation_status": mapping.get("invitation_status", {})
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting agent room for {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.post("/rooms/auto-join")
async def auto_join_rooms(request: AutoJoinRequest):
    """Auto-join a user to all agent rooms they're invited to."""
    user_id = request.user_id
    access_token = request.access_token
    homeserver = request.homeserver
    try:
        # Get all agent mappings from database
        from src.core.mapping_service import get_all_mappings
        mappings = get_all_mappings()
        
        if not mappings:
            return {
                "success": False,
                "message": "No agent mappings found",
                "joined_rooms": []
            }

        joined_rooms = []
        failed_rooms = []
        
        # Try to join each room
        for agent_id, mapping in mappings.items():
            room_id = mapping.get("room_id")
            if not room_id:
                continue
            
            # Check if already joined
            check_url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(check_url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        if user_id in data.get("joined", {}):
                            joined_rooms.append({"room_id": room_id, "agent_name": mapping.get("agent_name"), "status": "already_joined"})
                            continue
                
                # Try to join the room
                join_url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/join"
                async with session.post(join_url, headers=headers, json={}) as join_response:
                    if join_response.status == 200:
                        joined_rooms.append({"room_id": room_id, "agent_name": mapping.get("agent_name"), "status": "joined"})
                        logger.info(f"Auto-joined {user_id} to room {room_id} for agent {mapping.get('agent_name')}")
                    else:
                        error_text = await join_response.text()
                        failed_rooms.append({"room_id": room_id, "agent_name": mapping.get("agent_name"), "error": error_text})
                        logger.warning(f"Failed to join {user_id} to room {room_id}: {error_text}")

        return {
            "success": True,
            "message": f"Joined {len(joined_rooms)} rooms, {len(failed_rooms)} failed",
            "joined_rooms": joined_rooms,
            "failed_rooms": failed_rooms
        }
        
    except Exception as e:
        logger.error(f"Error in auto-join: {e}")
        return {
            "success": False,
            "message": f"Error: {str(e)}",
            "joined_rooms": [],
            "failed_rooms": []
        }

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "authenticated": matrix_client.authenticated,
        "timestamp": datetime.now().isoformat(),
        "agent_sync_available": AGENT_SYNC_AVAILABLE
    }


@app.get("/health/agent-provisioning")
async def agent_provisioning_health():
    """
    Check health of agent room provisioning.
    
    Returns status of all Letta agents and their Matrix room mappings.
    Status can be: healthy, degraded, unhealthy, or error.
    """
    try:
        if not AGENT_SYNC_AVAILABLE:
            return {
                "status": "unavailable",
                "message": "Agent sync functionality not available",
                "timestamp": datetime.now().isoformat()
            }
        
        from src.core.agent_user_manager import check_provisioning_health
        config = Config.from_env()
        health = await check_provisioning_health(config)
        health["timestamp"] = datetime.now().isoformat()
        
        return health
        
    except Exception as e:
        logger.error(f"Error checking agent provisioning health: {e}")
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }


if __name__ == "__main__":
    # Configuration
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", 8000))
    
    print("🚀 Starting Matrix API Server...")
    print(f"📡 Server will be available at: http://{host}:{port}")
    print(f"📚 API Documentation: http://{host}:{port}/docs")
    print(f"🔧 Alternative docs: http://{host}:{port}/redoc")
    
    uvicorn.run(
        "src.api.app:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )