#!/usr/bin/env python3
from datetime import datetime
import asyncio
import json
import logging
import os
from typing import Optional

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, Header
import uvicorn
from pydantic import BaseModel

from src.api.auth import verify_internal_key
from src.core.identity_health_monitor import get_identity_token_health_monitor
from src.matrix.identity_client_pool import get_identity_client_pool

from src.api.routes.agent_sync import (
    NewAgentNotification,
    WebhookResponse,
    router as agent_sync_router,
)
from src.api.routes.messaging import (
    GetMessagesResponse,
    ListRoomsResponse,
    LoginResponse,
    MatrixMessage,
    RoomInfo,
    SendMessageResponse,
    router as messaging_router,
)
from src.api.routes.messaging import GetMessagesRequest, LoginRequest, SendMessageRequest
from src.api.routes.portal_links import PortalLinkRequest, router as portal_links_router


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

try:
    from ..core.agent_user_manager import run_agent_sync
    from ..matrix.client import Config

    AGENT_SYNC_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Agent sync not available: {e}")
    AGENT_SYNC_AVAILABLE = False

LETTA_WEBHOOK_AVAILABLE = False

try:
    from src.api.routes.identity import dm_router, internal_router, router as identity_router

    IDENTITY_API_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Identity API not available: {e}")
    IDENTITY_API_AVAILABLE = False
    identity_router = None
    dm_router = None
    internal_router = None

load_dotenv(".env")

app = FastAPI(title="Matrix API", description="REST API for Matrix messaging operations", version="1.0.0")

if IDENTITY_API_AVAILABLE and identity_router and dm_router and internal_router:
    app.include_router(identity_router)
    app.include_router(dm_router)
    app.include_router(internal_router)
    logger.info("Identity API routes registered")

app.include_router(messaging_router)
app.include_router(agent_sync_router)
app.include_router(portal_links_router)


class AutoJoinRequest(BaseModel):
    user_id: str
    access_token: str
    homeserver: str


class MatrixAPIClient:
    def __init__(self):
        self.homeserver = os.environ.get("MATRIX_HOMESERVER_URL")
        self.user_id = os.environ.get("MATRIX_USERNAME")
        self.password = os.environ.get("MATRIX_PASSWORD")
        self.access_token = None
        self.device_id = None
        self.authenticated = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session

        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                return self._session
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=50,
                ttl_dns_cache=300,
                keepalive_timeout=30,
            )
            self._session = aiohttp.ClientSession(connector=connector)
            return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def login(
        self,
        homeserver: Optional[str] = None,
        user_id: Optional[str] = None,
        password: Optional[str] = None,
        device_name: str = "matrix_api",
    ):
        try:
            homeserver = homeserver or self.homeserver
            user_id = user_id or self.user_id
            password = password or self.password

            if not all([homeserver, user_id, password]):
                return LoginResponse(success=False, message="Missing required credentials")

            url = f"{homeserver}/_matrix/client/v3/login"
            headers = {"Content-Type": "application/json"}
            login_data = {
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": user_id},
                "password": password,
                "initial_device_display_name": device_name,
            }

            session = await self._get_session()
            async with session.post(url, headers=headers, json=login_data) as response:
                if response.status == 200:
                    result = await response.json()
                    access_token = result.get("access_token")
                    device_id = result.get("device_id")
                    response_user_id = result.get("user_id")
                    self.access_token = access_token
                    self.device_id = device_id
                    self.authenticated = True
                    return LoginResponse(
                        success=True,
                        access_token=access_token,
                        device_id=device_id,
                        user_id=response_user_id,
                        message="Login successful",
                    )
                error_text = await response.text()
                return LoginResponse(success=False, message=f"Login failed: {error_text}")
        except Exception as e:
            return LoginResponse(success=False, message=f"Error during login: {str(e)}")

    async def send_message(self, homeserver: str, access_token: str, room_id: str, message: str):
        try:
            import time

            txn_id = f"api{int(time.time() * 1000)}"
            url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            message_data = {"msgtype": "m.text", "body": message}

            session = await self._get_session()
            async with session.put(url, headers=headers, json=message_data) as response:
                if response.status == 200:
                    result = await response.json()
                    return SendMessageResponse(success=True, event_id=result.get("event_id"), message="Message sent successfully")
                error_text = await response.text()
                return SendMessageResponse(success=False, message=f"Failed to send message: {error_text}")
        except Exception as e:
            return SendMessageResponse(success=False, message=f"Error sending message: {str(e)}")

    async def get_messages(self, homeserver: str, access_token: str, room_id: str, limit: int = 5):
        try:
            url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/messages"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            params = {"dir": "b", "limit": limit, "filter": json.dumps({"types": ["m.room.message"]})}

            session = await self._get_session()
            async with session.get(url, headers=headers, params=params) as response:
                if response.status == 200:
                    result = await response.json()
                    events = result.get("chunk", [])
                    messages = []
                    for event in reversed(events):
                        if event.get("type") == "m.room.message":
                            content = event.get("content", {})
                            timestamp = event.get("origin_server_ts", 0)
                            messages.append(
                                MatrixMessage(
                                    sender=event.get("sender", "unknown"),
                                    body=content.get("body", ""),
                                    timestamp=timestamp,
                                    formatted_time=self.format_timestamp(timestamp),
                                    event_id=event.get("event_id", ""),
                                )
                            )
                    return GetMessagesResponse(success=True, messages=messages, message=f"Retrieved {len(messages)} messages")
                error_text = await response.text()
                return GetMessagesResponse(success=False, message=f"Failed to get messages: {error_text}")
        except Exception as e:
            return GetMessagesResponse(success=False, message=f"Error getting messages: {str(e)}")

    async def list_rooms(self, homeserver: str, access_token: str):
        try:
            url = f"{homeserver}/_matrix/client/v3/joined_rooms"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

            session = await self._get_session()
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    room_ids = result.get("joined_rooms", [])
                    rooms = []
                    for room_id in room_ids:
                        room_name = await self.get_room_name(homeserver, access_token, room_id, session=session)
                        rooms.append(RoomInfo(room_id=room_id, room_name=room_name))
                    return ListRoomsResponse(success=True, rooms=rooms, message=f"Found {len(rooms)} rooms")
                error_text = await response.text()
                return ListRoomsResponse(success=False, message=f"Failed to list rooms: {error_text}")
        except Exception as e:
            return ListRoomsResponse(success=False, message=f"Error listing rooms: {str(e)}")

    async def get_room_name(
        self,
        homeserver: str,
        access_token: str,
        room_id: str,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        try:
            url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

            active_session = session or await self._get_session()
            async with active_session.get(url, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("name", room_id)
                return room_id
        except Exception:
            return room_id

    def format_timestamp(self, timestamp_ms):
        try:
            return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "Unknown time"


matrix_client = MatrixAPIClient()
app.state.matrix_client = matrix_client


@app.on_event("startup")
async def start_identity_token_monitor():
    if not IDENTITY_API_AVAILABLE:
        return
    monitor = get_identity_token_health_monitor()
    await monitor.start()
    app.state.identity_token_monitor = monitor
    client_pool = get_identity_client_pool()
    await client_pool.start()
    app.state.identity_client_pool = client_pool


@app.on_event("shutdown")
async def stop_identity_token_monitor():
    matrix_api_client = getattr(app.state, "matrix_client", None)
    if matrix_api_client:
        await matrix_api_client.close()
    client_pool = getattr(app.state, "identity_client_pool", None)
    if client_pool:
        await client_pool.stop()
    monitor = getattr(app.state, "identity_token_monitor", None)
    if monitor:
        await monitor.stop()


@app.get("/")
async def root():
    return {"message": "Matrix API", "version": "1.0.0", "status": "running"}


@app.post("/rooms/auto-join")
async def auto_join_rooms(request: AutoJoinRequest, x_internal_key: str = Header(...)):
    verify_internal_key(x_internal_key)
    user_id = request.user_id
    access_token = request.access_token
    homeserver = request.homeserver
    try:
        from src.core.mapping_service import get_all_mappings

        mappings = get_all_mappings()
        if not mappings:
            return {"success": False, "message": "No agent mappings found", "joined_rooms": []}

        joined_rooms = []
        failed_rooms = []
        for _, mapping in mappings.items():
            room_id = mapping.get("room_id")
            if not room_id:
                continue

            check_url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/joined_members"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
            session = await matrix_client._get_session()
            async with session.get(check_url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if user_id in data.get("joined", {}):
                        joined_rooms.append({"room_id": room_id, "agent_name": mapping.get("agent_name"), "status": "already_joined"})
                        continue

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
            "failed_rooms": failed_rooms,
        }
    except Exception as e:
        logger.error(f"Error in auto-join: {e}")
        return {"success": False, "message": f"Error: {str(e)}", "joined_rooms": [], "failed_rooms": []}


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "authenticated": matrix_client.authenticated,
        "timestamp": datetime.now().isoformat(),
        "agent_sync_available": AGENT_SYNC_AVAILABLE,
    }


@app.get("/health/agent-provisioning")
async def agent_provisioning_health():
    try:
        if not AGENT_SYNC_AVAILABLE:
            return {
                "status": "unavailable",
                "message": "Agent sync functionality not available",
                "timestamp": datetime.now().isoformat(),
            }

        from src.core.agent_user_manager import check_provisioning_health

        from src.matrix.client import Config as MatrixConfig

        config = MatrixConfig.from_env()
        health = await check_provisioning_health(config)
        health["timestamp"] = datetime.now().isoformat()
        return health
    except Exception as e:
        logger.error(f"Error checking agent provisioning health: {e}")
        return {"status": "error", "message": str(e), "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", 8000))
    print("Starting Matrix API Server")
    print(f"Server will be available at: http://{host}:{port}")
    print(f"API Documentation: http://{host}:{port}/docs")
    print(f"Alternative docs: http://{host}:{port}/redoc")
    uvicorn.run("src.api.app:app", host=host, port=port, reload=True, log_level="info")
