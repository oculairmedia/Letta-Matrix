import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Dict, Optional
import aiohttp

from nio import (
    AsyncClient,
    RoomReadMarkersError,
    RoomReadMarkersResponse,
    RoomRedactError,
    RoomRedactResponse,
    RoomSendError,
    RoomSendResponse,
)

from src.core.identity_health_monitor import get_identity_token_health_monitor
from src.core.identity_storage import get_identity_service
from src.models.identity import Identity


logger = logging.getLogger(__name__)


@dataclass
class _PooledClient:
    identity_id: str
    client: AsyncClient


class IdentityClientPool:
    _instance: Optional["IdentityClientPool"] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs) -> "IdentityClientPool":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        homeserver_url: Optional[str] = None,
        store_dir: str = "./identity_store",
    ):
        if self._initialized:
            return
        self._homeserver = homeserver_url or os.environ.get(
            "MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca"
        )
        self._store_dir = store_dir
        self._clients: Dict[str, _PooledClient] = {}
        self._lock = asyncio.Lock()
        self._health_check_task: Optional[asyncio.Task] = None
        self._health_check_stop = asyncio.Event()
        self._health_check_interval_seconds = int(
            os.environ.get("IDENTITY_CLIENT_POOL_HEALTH_INTERVAL_SECONDS", "600")
        )
        self._initialized = True
        logger.info(
            "IdentityClientPool initialized with homeserver: %s", self._homeserver
        )

    async def initialize(self) -> None:
        os.makedirs(self._store_dir, exist_ok=True)
        logger.info("IdentityClientPool storage initialized")

    async def start(self) -> None:
        await self.initialize()
        if self._health_check_task and not self._health_check_task.done():
            return
        self._health_check_stop.clear()
        self._health_check_task = asyncio.create_task(self._health_check_loop())
        logger.info(
            "IdentityClientPool health loop started (interval=%ss)",
            self._health_check_interval_seconds,
        )

    async def stop(self) -> None:
        self._health_check_stop.set()
        if self._health_check_task:
            await self._health_check_task
            self._health_check_task = None
        await self.close_all()

    async def get_client(self, identity_id: str) -> Optional[AsyncClient]:
        pooled = self._clients.get(identity_id)
        if pooled is not None:
            return pooled.client

        async with self._lock:
            pooled = self._clients.get(identity_id)
            if pooled is not None:
                return pooled.client

            identity_service = get_identity_service()
            identity = identity_service.get(identity_id)
            if not identity:
                logger.warning("Identity not found: %s", identity_id)
                return None

            client = await self._create_client(identity)
            if client:
                self._clients[identity_id] = _PooledClient(
                    identity_id=identity_id,
                    client=client,
                )
            return client

    async def get_client_for_agent(self, agent_id: str) -> Optional[AsyncClient]:
        identity_id = f"letta_{agent_id}"
        return await self.get_client(identity_id)

    async def _create_client(self, identity: Identity) -> Optional[AsyncClient]:
        try:
            identity_id = str(identity.id)
            mxid = str(identity.mxid)
            access_token = str(identity.access_token)
            device_id = (
                str(identity.device_id) if identity.device_id is not None else None
            )

            store_path = os.path.join(self._store_dir, identity_id)
            os.makedirs(store_path, exist_ok=True)

            client = AsyncClient(
                homeserver=self._homeserver,
                user=mxid,
                store_path=store_path,
            )
            client.access_token = access_token
            client.user_id = mxid

            if device_id:
                client.device_id = device_id

            logger.info("Created client for identity: %s -> %s", identity_id, mxid)
            return client
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            logger.error("Failed to create client for %s: %s", identity.id, exc)
            return None

    async def _health_check_loop(self) -> None:
        while not self._health_check_stop.is_set():
            try:
                await self._run_health_check()
            except (RuntimeError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.error("Identity client pool health check failed: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(
                    self._health_check_stop.wait(),
                    timeout=self._health_check_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _run_health_check(self) -> None:
        async with self._lock:
            pooled_clients = list(self._clients.items())

        for identity_id, pooled in pooled_clients:
            is_healthy = await self._check_client_health(pooled.client, identity_id)
            if not is_healthy:
                logger.warning("Evicting unhealthy client for %s", identity_id)
                await self.close_client(identity_id)

    async def _check_client_health(self, client: AsyncClient, identity_id: str) -> bool:
        try:
            response = await client.whoami()
        except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
            logger.warning("whoami failed for %s: %s", identity_id, exc)
            return False

        user_id = getattr(response, "user_id", None)
        if not user_id:
            logger.warning("whoami missing user_id for %s", identity_id)
            return False
        return True

    async def _recover_identity(self, identity_id: str) -> bool:
        monitor = get_identity_token_health_monitor()
        is_healthy = await monitor.ensure_identity_healthy(identity_id)
        if not is_healthy:
            return False
        await self.close_client(identity_id)
        recreated = await self.get_client(identity_id)
        return recreated is not None

    def _is_unknown_token_error(self, response_or_error: object) -> bool:
        message = getattr(response_or_error, "message", "")
        if isinstance(message, str) and "M_UNKNOWN_TOKEN" in message:
            return True
        return "M_UNKNOWN_TOKEN" in str(response_or_error)

    async def _send_with_recovery(
        self,
        identity_id: str,
        room_id: str,
        content: Dict[str, object],
    ) -> Optional[str]:
        client = await self.get_client(identity_id)
        if not client:
            logger.error("Cannot send message: no client for %s", identity_id)
            return None

        for attempt in range(2):
            try:
                response = await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                )

                if isinstance(response, RoomSendResponse):
                    logger.info(
                        "Message sent by %s to %s: %s",
                        identity_id,
                        room_id,
                        response.event_id,
                    )
                    return response.event_id
                if isinstance(response, RoomSendError):
                    logger.error("Failed to send message: %s", response.message)
                    if attempt == 0 and self._is_unknown_token_error(response):
                        recovered = await self._recover_identity(identity_id)
                        if recovered:
                            client = await self.get_client(identity_id)
                            if client:
                                continue
                    return None
                logger.error("Unexpected response type: %s", type(response))
                return None
            except (RuntimeError, ValueError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.error("Error sending message from %s: %s", identity_id, exc)
                if attempt == 0 and self._is_unknown_token_error(exc):
                    recovered = await self._recover_identity(identity_id)
                    if recovered:
                        client = await self.get_client(identity_id)
                        if client:
                            continue
                return None
        return None

    async def send_message(
        self,
        identity_id: str,
        room_id: str,
        message: str,
        msgtype: str = "m.text",
        thread_event_id: Optional[str] = None,
        thread_latest_event_id: Optional[str] = None,
    ) -> Optional[str]:
        content: Dict[str, object] = {"msgtype": msgtype, "body": message}
        if thread_event_id:
            fallback_event_id = thread_latest_event_id or thread_event_id
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_event_id,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": fallback_event_id},
            }
        return await self._send_with_recovery(identity_id, room_id, content)

    async def send_as_agent(
        self,
        agent_id: str,
        room_id: str,
        message: str,
        msgtype: str = "m.text",
        thread_event_id: Optional[str] = None,
        thread_latest_event_id: Optional[str] = None,
    ) -> Optional[str]:
        identity_id = f"letta_{agent_id}"
        return await self.send_message(
            identity_id,
            room_id,
            message,
            msgtype,
            thread_event_id=thread_event_id,
            thread_latest_event_id=thread_latest_event_id,
        )

    async def edit_message(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
        message: str,
        msgtype: str = "m.text",
    ) -> Optional[str]:
        content = {
            "msgtype": msgtype,
            "body": f"* {message}",
            "m.new_content": {
                "msgtype": msgtype,
                "body": message,
            },
            "m.relates_to": {
                "rel_type": "m.replace",
                "event_id": event_id,
            },
        }
        return await self._send_with_recovery(identity_id, room_id, content)

    async def edit_as_agent(
        self,
        agent_id: str,
        room_id: str,
        event_id: str,
        message: str,
        msgtype: str = "m.text",
    ) -> Optional[str]:
        identity_id = f"letta_{agent_id}"
        return await self.edit_message(identity_id, room_id, event_id, message, msgtype)

    async def _redact_with_recovery(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """Redact an event with automatic token recovery."""
        client = await self.get_client(identity_id)
        if not client:
            logger.error("Cannot redact message: no client for %s", identity_id)
            return False

        for attempt in range(2):
            try:
                response = await client.room_redact(
                    room_id=room_id,
                    event_id=event_id,
                    reason=reason,
                )

                if isinstance(response, RoomRedactResponse):
                    logger.info(
                        "Message redacted by %s in %s: %s",
                        identity_id,
                        room_id,
                        response.event_id,
                    )
                    return True
                if isinstance(response, RoomRedactError):
                    logger.error("Failed to redact message: %s", response.message)
                    if attempt == 0 and self._is_unknown_token_error(response):
                        recovered = await self._recover_identity(identity_id)
                        if recovered:
                            client = await self.get_client(identity_id)
                            if client:
                                continue
                    return False
                logger.error("Unexpected response type: %s", type(response))
                return False
            except (RuntimeError, ValueError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.error("Error redacting message from %s: %s", identity_id, exc)
                if attempt == 0 and self._is_unknown_token_error(exc):
                    recovered = await self._recover_identity(identity_id)
                    if recovered:
                        client = await self.get_client(identity_id)
                        if client:
                            continue
                return False
        return False

    async def redact_message(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """Redact (delete) a message."""
        return await self._redact_with_recovery(identity_id, room_id, event_id, reason)

    async def redact_as_agent(
        self,
        agent_id: str,
        room_id: str,
        event_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """Redact (delete) a message as an agent."""
        identity_id = f"letta_{agent_id}"
        return await self.redact_message(identity_id, room_id, event_id, reason)

    async def _react_with_recovery(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
        emoji: str,
    ) -> Optional[str]:
        """Send a reaction with automatic token recovery."""
        client = await self.get_client(identity_id)
        if not client:
            logger.error("Cannot send reaction: no client for %s", identity_id)
            return None

        content: Dict[str, object] = {
            "m.relates_to": {
                "rel_type": "m.annotation",
                "event_id": event_id,
                "key": emoji,
            }
        }

        for attempt in range(2):
            try:
                response = await client.room_send(
                    room_id=room_id,
                    message_type="m.reaction",
                    content=content,
                )

                if isinstance(response, RoomSendResponse):
                    logger.info(
                        "Reaction sent by %s to %s: %s",
                        identity_id,
                        event_id,
                        response.event_id,
                    )
                    return response.event_id
                if isinstance(response, RoomSendError):
                    logger.error("Failed to send reaction: %s", response.message)
                    if attempt == 0 and self._is_unknown_token_error(response):
                        recovered = await self._recover_identity(identity_id)
                        if recovered:
                            client = await self.get_client(identity_id)
                            if client:
                                continue
                    return None
                logger.error("Unexpected response type: %s", type(response))
                return None
            except (RuntimeError, ValueError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.error("Error sending reaction from %s: %s", identity_id, exc)
                if attempt == 0 and self._is_unknown_token_error(exc):
                    recovered = await self._recover_identity(identity_id)
                    if recovered:
                        client = await self.get_client(identity_id)
                        if client:
                            continue
                return None
        return None

    async def react_to_message(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
        emoji: str,
    ) -> Optional[str]:
        """Send a reaction (emoji) to a message."""
        return await self._react_with_recovery(identity_id, room_id, event_id, emoji)

    async def react_as_agent(
        self,
        agent_id: str,
        room_id: str,
        event_id: str,
        emoji: str,
    ) -> Optional[str]:
        """Send a reaction (emoji) to a message as an agent."""
        identity_id = f"letta_{agent_id}"
        return await self.react_to_message(identity_id, room_id, event_id, emoji)

    async def _read_receipt_with_recovery(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
    ) -> bool:
        """Send a read receipt with automatic token recovery."""
        client = await self.get_client(identity_id)
        if not client:
            logger.error("Cannot send read receipt: no client for %s", identity_id)
            return False

        for attempt in range(2):
            try:
                response = await client.room_read_markers(
                    room_id=room_id,
                    fully_read_event=event_id,
                    read_event=event_id,
                )

                if isinstance(response, RoomReadMarkersResponse):
                    logger.debug(
                        "Read receipt sent by %s for %s in %s",
                        identity_id,
                        event_id,
                        room_id,
                    )
                    return True
                if isinstance(response, RoomReadMarkersError):
                    logger.debug("Failed to send read receipt: %s", response.message)
                    if attempt == 0 and self._is_unknown_token_error(response):
                        recovered = await self._recover_identity(identity_id)
                        if recovered:
                            client = await self.get_client(identity_id)
                            if client:
                                continue
                    return False
                logger.error("Unexpected response type: %s", type(response))
                return False
            except (RuntimeError, ValueError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.debug("Error sending read receipt from %s: %s", identity_id, exc)
                if attempt == 0 and self._is_unknown_token_error(exc):
                    recovered = await self._recover_identity(identity_id)
                    if recovered:
                        client = await self.get_client(identity_id)
                        if client:
                            continue
                return False
        return False

    async def send_read_receipt(
        self,
        identity_id: str,
        room_id: str,
        event_id: str,
    ) -> bool:
        """Send a read receipt for a message."""
        return await self._read_receipt_with_recovery(identity_id, room_id, event_id)

    async def read_receipt_as_agent(
        self,
        agent_id: str,
        room_id: str,
        event_id: str,
    ) -> bool:
        """Send a read receipt for a message as an agent."""
        identity_id = f"letta_{agent_id}"
        return await self.send_read_receipt(identity_id, room_id, event_id)

    async def close_client(self, identity_id: str) -> None:
        async with self._lock:
            pooled = self._clients.pop(identity_id, None)
        if pooled:
            try:
                await pooled.client.close()
                logger.info("Closed client for: %s", identity_id)
            except (RuntimeError, ValueError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.error("Error closing client for %s: %s", identity_id, exc)

    async def restart_client(self, identity_id: str) -> Optional[AsyncClient]:
        await self.close_client(identity_id)
        return await self.get_client(identity_id)

    async def close_all(self) -> None:
        async with self._lock:
            pooled_clients = list(self._clients.values())
            self._clients.clear()
        for pooled in pooled_clients:
            try:
                await pooled.client.close()
            except (RuntimeError, ValueError, asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.error("Error closing client %s: %s", pooled.identity_id, exc)
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
        logger.debug("No identity found for user %s", user_mxid)
        return None
    if not bool(identity.is_active):
        logger.debug("Identity for %s is not active", user_mxid)
        return None
    identity_id = str(identity.id)
    pool = get_identity_client_pool()
    event_id = await pool.send_message(identity_id, room_id, message)
    if event_id:
        identity_service.mark_used(identity_id)
    return event_id
