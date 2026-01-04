"""
Matrix Client Pool for identity-based messaging.

Manages per-identity AsyncClient instances with connection pooling and caching.
"""
import asyncio
import os
from typing import Optional, Dict
from nio import AsyncClient, RoomSendResponse, RoomSendError
from src.models.identity import Identity
from src.core.identity_storage import get_identity_service
import logging

logger = logging.getLogger(__name__)


class IdentityClientPool:
    _instance: Optional['IdentityClientPool'] = None
    _initialized: bool = False
    
    def __new__(cls, *args, **kwargs) -> 'IdentityClientPool':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, homeserver_url: Optional[str] = None, store_dir: str = "./identity_store"):
        if self._initialized:
            return
        self._homeserver = homeserver_url or os.environ.get("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
        self._store_dir = store_dir
        self._clients: Dict[str, AsyncClient] = {}
        self._lock = asyncio.Lock()
        self._initialized = True
        logger.info(f"IdentityClientPool initialized with homeserver: {self._homeserver}")
    
    async def initialize(self) -> None:
        os.makedirs(self._store_dir, exist_ok=True)
        logger.info("IdentityClientPool storage initialized")
    
    async def get_client(self, identity_id: str) -> Optional[AsyncClient]:
        async with self._lock:
            if identity_id in self._clients:
                return self._clients[identity_id]
            
            identity_service = get_identity_service()
            identity = identity_service.get(identity_id)
            if not identity:
                logger.warning(f"Identity not found: {identity_id}")
                return None
            
            client = await self._create_client(identity)
            if client:
                self._clients[identity_id] = client
            return client
    
    async def get_client_for_agent(self, agent_id: str) -> Optional[AsyncClient]:
        identity_id = f"letta_{agent_id}"
        return await self.get_client(identity_id)
    
    async def _create_client(self, identity: Identity) -> Optional[AsyncClient]:
        try:
            identity_id = str(identity.id)
            mxid = str(identity.mxid)
            access_token = str(identity.access_token)
            device_id = str(identity.device_id) if identity.device_id is not None else None
            
            store_path = os.path.join(self._store_dir, identity_id)
            os.makedirs(store_path, exist_ok=True)
            
            client = AsyncClient(
                homeserver=self._homeserver,
                user=mxid,
                store_path=store_path
            )
            
            client.access_token = access_token
            client.user_id = mxid
            
            if device_id:
                client.device_id = device_id
            
            logger.info(f"Created client for identity: {identity_id} -> {mxid}")
            return client
            
        except Exception as e:
            logger.error(f"Failed to create client for {identity.id}: {e}")
            return None
    
    async def send_message(
        self,
        identity_id: str,
        room_id: str,
        message: str,
        msgtype: str = "m.text"
    ) -> Optional[str]:
        client = await self.get_client(identity_id)
        if not client:
            logger.error(f"Cannot send message: no client for {identity_id}")
            return None
        
        try:
            content = {
                "msgtype": msgtype,
                "body": message
            }
            
            response = await client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content
            )
            
            if isinstance(response, RoomSendResponse):
                logger.info(f"Message sent by {identity_id} to {room_id}: {response.event_id}")
                return response.event_id
            elif isinstance(response, RoomSendError):
                logger.error(f"Failed to send message: {response.message}")
                return None
            else:
                logger.error(f"Unexpected response type: {type(response)}")
                return None
                
        except Exception as e:
            logger.error(f"Error sending message from {identity_id}: {e}")
            return None
    
    async def send_as_agent(
        self,
        agent_id: str,
        room_id: str,
        message: str,
        msgtype: str = "m.text"
    ) -> Optional[str]:
        identity_id = f"letta_{agent_id}"
        return await self.send_message(identity_id, room_id, message, msgtype)
    
    async def close_client(self, identity_id: str) -> None:
        async with self._lock:
            client = self._clients.pop(identity_id, None)
            if client:
                try:
                    await client.close()
                    logger.info(f"Closed client for: {identity_id}")
                except Exception as e:
                    logger.error(f"Error closing client for {identity_id}: {e}")
    
    async def restart_client(self, identity_id: str) -> Optional[AsyncClient]:
        await self.close_client(identity_id)
        return await self.get_client(identity_id)
    
    async def close_all(self) -> None:
        async with self._lock:
            for identity_id, client in list(self._clients.items()):
                try:
                    await client.close()
                except Exception as e:
                    logger.error(f"Error closing client {identity_id}: {e}")
            self._clients.clear()
            logger.info("All identity clients closed")
    
    def get_active_count(self) -> int:
        return len(self._clients)
    
    def is_client_active(self, identity_id: str) -> bool:
        return identity_id in self._clients


_pool: Optional[IdentityClientPool] = None


def get_identity_client_pool(homeserver_url: Optional[str] = None) -> IdentityClientPool:
    global _pool
    if _pool is None:
        _pool = IdentityClientPool(homeserver_url)
    return _pool


async def send_as_identity(identity_id: str, room_id: str, message: str) -> Optional[str]:
    pool = get_identity_client_pool()
    return await pool.send_message(identity_id, room_id, message)


async def send_as_agent(agent_id: str, room_id: str, message: str) -> Optional[str]:
    pool = get_identity_client_pool()
    return await pool.send_as_agent(agent_id, room_id, message)


async def send_as_user(user_mxid: str, room_id: str, message: str) -> Optional[str]:
    identity_service = get_identity_service()
    identity = identity_service.get_by_mxid(user_mxid)
    if identity is None:
        logger.debug(f"No identity found for user {user_mxid}")
        return None
    if not bool(identity.is_active):
        logger.debug(f"Identity for {user_mxid} is not active")
        return None
    identity_id = str(identity.id)
    pool = get_identity_client_pool()
    event_id = await pool.send_message(identity_id, room_id, message)
    if event_id:
        identity_service.mark_used(identity_id)
    return event_id
