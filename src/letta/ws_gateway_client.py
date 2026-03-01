"""
WebSocket gateway client for lettabot agent-gateway.

Maintains a pool of WebSocket connections keyed by agent_id.
Each connection maps 1:1 to a letta-code-sdk Session on the gateway side.
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, Optional

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger("matrix_client.ws_gateway")


class GatewayUnavailableError(Exception):
    pass


class GatewaySessionError(Exception):
    def __init__(self, code: str, message: str, request_id: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass
class _PoolEntry:
    ws: ClientConnection
    agent_id: str
    session_id: Optional[str] = None
    conversation_id: Optional[str] = None
    last_used: float = field(default_factory=time.monotonic)
    healthy: bool = True


class GatewayClient:
    """Async WebSocket client that pools connections per agent_id."""

    def __init__(
        self,
        gateway_url: str,
        idle_timeout: float = 300.0,
        max_connections: int = 20,
        connect_timeout: float = 10.0,
        api_key: Optional[str] = None,
    ):
        self._gateway_url = gateway_url
        self._idle_timeout = idle_timeout
        self._max_connections = max_connections
        self._connect_timeout = connect_timeout
        self._api_key = api_key
        self._pool: Dict[str, _PoolEntry] = {}
        self._pool_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._idle_cleanup_loop())

    async def close(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        async with self._pool_lock:
            for entry in self._pool.values():
                await self._close_entry(entry)
            self._pool.clear()

    async def send_message_streaming(
        self,
        agent_id: str,
        message: str,
        conversation_id: Optional[str] = None,
        source: Optional[Dict[str, str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send a message through the gateway and yield raw WS events as dicts.

        Raises GatewayUnavailableError if connection cannot be established.
        Raises GatewaySessionError on protocol-level errors from the gateway.
        """
        entry = await self._get_or_create(agent_id, conversation_id)
        request_id = str(uuid.uuid4())

        try:
            payload: Dict[str, Any] = {
                "type": "message",
                "content": message,
                "request_id": request_id,
            }
            if source:
                payload["source"] = source
            msg_payload = json.dumps(payload)
            await entry.ws.send(msg_payload)
            entry.last_used = time.monotonic()

            async for raw in entry.ws:
                entry.last_used = time.monotonic()
                try:
                    event = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"[WS-GATEWAY] Non-JSON frame from gateway: {raw!r:.200}")
                    continue

                event_type = event.get("type")

                if event_type == "error":
                    code = event.get("code", "UNKNOWN")
                    err_msg = event.get("message", "Unknown gateway error")
                    err_rid = event.get("request_id")
                    if err_rid and err_rid != request_id:
                        continue
                    raise GatewaySessionError(code=code, message=err_msg, request_id=err_rid)

                if event_type == "result":
                    yield event
                    break

                if event_type in ("stream", "session_init"):
                    yield event

        except websockets.ConnectionClosed as exc:
            logger.warning(f"[WS-GATEWAY] Connection closed for agent {agent_id}: {exc}")
            await self._evict(agent_id)
            raise GatewayUnavailableError(f"WS connection closed: {exc}") from exc
        except GatewaySessionError:
            raise
        except Exception as exc:
            logger.error(f"[WS-GATEWAY] Unexpected error for agent {agent_id}: {exc}", exc_info=True)
            await self._evict(agent_id)
            raise GatewayUnavailableError(f"Gateway error: {exc}") from exc

    async def send_message_blocking(
        self,
        agent_id: str,
        message: str,
        conversation_id: Optional[str] = None,
        source: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Send a message and collect all stream events until the result event.
        Returns the full result dict with an extra 'events' key containing all stream events.
        """
        events = []
        result = None

        async for event in self.send_message_streaming(
            agent_id=agent_id,
            message=message,
            conversation_id=conversation_id,
            source=source,
        ):
            if event.get("type") == "result":
                result = event
            else:
                events.append(event)

        if result is None:
            raise GatewaySessionError(
                code="NO_RESULT",
                message="Stream ended without a result event",
            )

        result["events"] = events
        return result

    async def abort(self, agent_id: str) -> bool:
        """
        Send an abort frame to the gateway for the given agent.
        Returns True if the abort was sent, False if no active connection.
        """
        async with self._pool_lock:
            entry = self._pool.get(agent_id)
            if not entry or not entry.healthy:
                return False
            try:
                await entry.ws.send(json.dumps({"type": "abort"}))
                logger.info(f"[WS-GATEWAY] Sent abort for agent {agent_id} (keeping connection alive)")
                return True
            except Exception as exc:
                logger.warning(f"[WS-GATEWAY] Failed to send abort for {agent_id}: {exc}")
                return False
    # ── pool internals ────────────────────────────────────────────

    async def _get_or_create(
        self, agent_id: str, conversation_id: Optional[str] = None
    ) -> _PoolEntry:
        async with self._pool_lock:
            entry = self._pool.get(agent_id)
            if entry and entry.healthy:
                try:
                    await entry.ws.ping()
                    entry.last_used = time.monotonic()
                    return entry
                except Exception:
                    logger.info(f"[WS-GATEWAY] Stale connection for {agent_id}, reconnecting")
                    await self._close_entry(entry)
                    del self._pool[agent_id]

            if len(self._pool) >= self._max_connections:
                await self._evict_oldest_unlocked()

        new_entry = await self._connect_and_init(agent_id, conversation_id)

        async with self._pool_lock:
            old = self._pool.get(agent_id)
            if old:
                await self._close_entry(old)
            self._pool[agent_id] = new_entry

        return new_entry

    async def _connect_and_init(
        self, agent_id: str, conversation_id: Optional[str] = None
    ) -> _PoolEntry:
        extra_headers = {}
        if self._api_key:
            extra_headers["X-Api-Key"] = self._api_key

        try:
            ws = await asyncio.wait_for(
                websockets.connect(
                    self._gateway_url,
                    additional_headers=extra_headers,
                    max_size=2**22,  # 4 MB frames
                    close_timeout=5,
                ),
                timeout=self._connect_timeout,
            )
        except Exception as exc:
            raise GatewayUnavailableError(
                f"Cannot connect to gateway at {self._gateway_url}: {exc}"
            ) from exc

        session_start = {
            "type": "session_start",
            "agent_id": agent_id,
        }
        if conversation_id:
            session_start["conversation_id"] = conversation_id

        await ws.send(json.dumps(session_start))

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self._connect_timeout)
            init_event = json.loads(raw)
        except Exception as exc:
            await ws.close()
            raise GatewayUnavailableError(
                f"No session_init from gateway for agent {agent_id}: {exc}"
            ) from exc

        if init_event.get("type") == "error":
            await ws.close()
            raise GatewaySessionError(
                code=init_event.get("code", "INIT_ERROR"),
                message=init_event.get("message", "Session init failed"),
            )

        if init_event.get("type") != "session_init":
            await ws.close()
            raise GatewayUnavailableError(
                f"Expected session_init, got {init_event.get('type')}"
            )

        entry = _PoolEntry(
            ws=ws,
            agent_id=agent_id,
            session_id=init_event.get("session_id"),
            conversation_id=init_event.get("conversation_id", conversation_id),
        )
        logger.info(
            f"[WS-GATEWAY] Session established: agent={agent_id} "
            f"session={entry.session_id}"
        )
        return entry

    async def _evict(self, agent_id: str) -> None:
        async with self._pool_lock:
            entry = self._pool.pop(agent_id, None)
            if entry:
                await self._close_entry(entry)

    async def _evict_oldest_unlocked(self) -> None:
        if not self._pool:
            return
        oldest_key = min(self._pool, key=lambda k: self._pool[k].last_used)
        entry = self._pool.pop(oldest_key)
        logger.info(f"[WS-GATEWAY] Evicting idle connection for agent {oldest_key}")
        await self._close_entry(entry)

    async def _close_entry(self, entry: _PoolEntry) -> None:
        entry.healthy = False
        try:
            close_msg = json.dumps({"type": "session_close"})
            await entry.ws.send(close_msg)
        except Exception:
            pass
        try:
            await entry.ws.close()
        except Exception:
            pass

    async def _idle_cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            to_evict = []

            async with self._pool_lock:
                for agent_id, entry in list(self._pool.items()):
                    if now - entry.last_used > self._idle_timeout:
                        to_evict.append(agent_id)

                for agent_id in to_evict:
                    entry = self._pool.pop(agent_id, None)
                    if entry:
                        logger.info(f"[WS-GATEWAY] Closing idle session for agent {agent_id}")
                        await self._close_entry(entry)


_global_client: Optional[GatewayClient] = None
_global_lock = asyncio.Lock()


async def get_gateway_client(
    gateway_url: str,
    idle_timeout: float = 300.0,
    max_connections: int = 20,
    api_key: Optional[str] = None,
) -> GatewayClient:
    global _global_client
    async with _global_lock:
        if _global_client is None:
            _global_client = GatewayClient(
                gateway_url=gateway_url,
                idle_timeout=idle_timeout,
                max_connections=max_connections,
                api_key=api_key,
            )
            await _global_client.start()
        return _global_client
