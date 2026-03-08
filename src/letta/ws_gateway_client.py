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
    in_use: bool = False  # True while send_message_streaming is iterating recv

class GatewayClient:
    """Async WebSocket client that pools connections per agent_id."""

    def __init__(
        self,
        gateway_url: str,
        idle_timeout: float = 3600.0,  # 1 hour — avoid cold-start subprocess spawns
        max_connections: int = 20,
        connect_timeout: float = 10.0,
        event_timeout: float = 300.0,  # 5 min per-event timeout (Opus thinking can be slow)
        api_key: Optional[str] = None,
    ):
        self._gateway_url = gateway_url
        self._idle_timeout = idle_timeout
        self._max_connections = max_connections
        self._connect_timeout = connect_timeout
        self._api_key = api_key
        self._event_timeout = event_timeout
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

        Automatically retries once on connection failure (dead WS, gateway restart).

        Raises GatewayUnavailableError if connection cannot be established after retry.
        Raises GatewaySessionError on protocol-level errors from the gateway.
        """
        last_error: Optional[Exception] = None
        for attempt in range(2):  # attempt 0 = normal, attempt 1 = retry after reconnect
            if attempt > 0:
                logger.info(f"[WS-GATEWAY] Retrying message for agent {agent_id} (attempt {attempt + 1})")
                # Evict the dead connection so _get_or_create makes a fresh one
                await self._evict(agent_id)

            entry = await self._get_or_create(agent_id, conversation_id)
            request_id = str(uuid.uuid4())

            try:
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

                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                entry.ws.recv(),
                                timeout=self._event_timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.error(
                                f"[WS-GATEWAY] Stream timeout ({self._event_timeout}s) for agent {agent_id}, "
                                f"aborting stuck stream"
                            )
                            # Try to abort the stuck run on the server side
                            try:
                                await entry.ws.send(json.dumps({"type": "abort"}))
                            except Exception:
                                pass
                            await self._evict(agent_id)
                            last_error = GatewayUnavailableError(
                                f"Stream timed out after {self._event_timeout}s with no events"
                            )
                            if attempt == 0:
                                break  # Will retry
                            raise last_error

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
                            return  # Success — exit both the stream loop and the retry loop

                        if event_type in ("stream", "session_init"):
                            yield event
                finally:
                    entry.in_use = False

                # Stream ended without result — treat as connection issue on first attempt
                if attempt == 0:
                    logger.warning(f"[WS-GATEWAY] Stream ended without result for agent {agent_id}, will retry")
                    last_error = GatewayUnavailableError("Stream ended without result event")
                    continue
                raise GatewayUnavailableError("Stream ended without result event after retry")

            except websockets.ConnectionClosed as exc:
                logger.warning(f"[WS-GATEWAY] Connection closed for agent {agent_id}: {exc}")
                await self._evict(agent_id)
                last_error = GatewayUnavailableError(f"WS connection closed: {exc}")
                if attempt == 0:
                    continue  # Retry with fresh connection
                raise last_error from exc
            except GatewaySessionError as exc:
                # Stale session: gateway expired the session but TCP stayed alive.
                # Evict and retry once — fresh _connect_and_init will send session_start.
                if attempt == 0 and "session_start" in str(exc).lower():
                    logger.warning(f"[WS-GATEWAY] Stale session for agent {agent_id}: {exc}, will reconnect")
                    await self._evict(agent_id)
                    last_error = exc
                    continue
                raise
            except Exception as exc:
                logger.error(f"[WS-GATEWAY] Unexpected error for agent {agent_id}: {exc}", exc_info=True)
                await self._evict(agent_id)
                last_error = GatewayUnavailableError(f"Gateway error: {exc}")
                if attempt == 0:
                    continue  # Retry with fresh connection
                raise last_error from exc

        # Should not reach here, but just in case
        if last_error:
            raise last_error

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
        Send an abort frame to the gateway for the given agent,
        then evict the connection from the pool.

        After abort, the WS recv() buffer may still contain buffered
        reasoning events from the cancelled stream.  Reusing the same
        connection would leak those stale events into the next request.
        Evicting forces a fresh connection on the next message.

        Returns True if the abort was sent, False if no active connection.
        """
        async with self._pool_lock:
            entry = self._pool.get(agent_id)
            if not entry or not entry.healthy:
                return False
            try:
                await entry.ws.send(json.dumps({"type": "abort"}))
                logger.info(f"[WS-GATEWAY] Sent abort for agent {agent_id}, evicting connection")
            except Exception as exc:
                logger.warning(f"[WS-GATEWAY] Failed to send abort for {agent_id}: {exc}")
            # Always evict after abort — stale events may be buffered
            self._pool.pop(agent_id, None)
            await self._close_entry(entry)
            return True
    # ── pool internals ────────────────────────────────────────────

    async def _get_or_create(
        self, agent_id: str, conversation_id: Optional[str] = None
    ) -> _PoolEntry:
        wait_deadline = time.monotonic() + self._connect_timeout

        while True:
            async with self._pool_lock:
                entry = self._pool.get(agent_id)
                if entry and entry.healthy:
                    if entry.in_use:
                        if time.monotonic() >= wait_deadline:
                            raise GatewayUnavailableError(
                                f"Timed out waiting for available session for agent {agent_id}"
                            )
                    else:
                        try:
                            await entry.ws.ping()
                            entry.last_used = time.monotonic()
                            entry.in_use = True
                            return entry
                        except Exception:
                            logger.info(f"[WS-GATEWAY] Stale connection for {agent_id}, reconnecting")
                            await self._close_entry(entry)
                            del self._pool[agent_id]

                if len(self._pool) >= self._max_connections:
                    evicted = await self._evict_oldest_unlocked()
                    if not evicted:
                        # All connections busy and at capacity — wait for one to free up
                        pass  # Falls through to sleep/continue or deadline check below
            # Wait if entry is busy OR pool is at capacity with no room
            at_capacity = len(self._pool) >= self._max_connections
            if (entry and entry.healthy and entry.in_use) or (at_capacity and agent_id not in self._pool):
                if time.monotonic() >= wait_deadline:
                    raise GatewayUnavailableError(
                        f"Timed out waiting for available session for agent {agent_id}"
                    )
                await asyncio.sleep(0.01)
                continue

            new_entry = await self._connect_and_init(agent_id, conversation_id)

            async with self._pool_lock:
                current = self._pool.get(agent_id)
                if current and current.healthy:
                    if current.in_use:
                        # Race: another caller reserved entry while we were connecting
                        await self._close_entry(new_entry)
                    else:
                        await self._close_entry(new_entry)
                        current.last_used = time.monotonic()
                        current.in_use = True
                        return current
                else:
                    old = self._pool.get(agent_id)
                    if old:
                        await self._close_entry(old)
                    new_entry.last_used = time.monotonic()
                    new_entry.in_use = True
                    self._pool[agent_id] = new_entry
                    return new_entry

            if time.monotonic() >= wait_deadline:
                raise GatewayUnavailableError(
                    f"Timed out waiting for available session for agent {agent_id}"
                )
            await asyncio.sleep(0.01)

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
                    ping_interval=None,  # Disable built-in pings; we run our own health check loop
                    ping_timeout=None,
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

    async def _evict_oldest_unlocked(self) -> bool:
        """Evict the oldest idle (not in_use) connection to make room.

        Returns True if a connection was evicted, False otherwise.
        """
        if not self._pool:
            return False
        # Only consider entries that are not actively streaming
        candidates = {k: v for k, v in self._pool.items() if not v.in_use}
        if not candidates:
            return False  # All connections are in use, can't evict any
        oldest_key = min(candidates, key=lambda k: candidates[k].last_used)
        entry = self._pool.pop(oldest_key)
        logger.info(f"[WS-GATEWAY] Evicting idle connection for agent {oldest_key}")
        await self._close_entry(entry)
        return True
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
            to_ping = []

            async with self._pool_lock:
                for agent_id, entry in list(self._pool.items()):
                    if now - entry.last_used > self._idle_timeout:
                        to_evict.append(agent_id)
                    else:
                        to_ping.append((agent_id, entry))

                for agent_id in to_evict:
                    entry = self._pool.pop(agent_id, None)
                    if entry:
                        logger.info(f"[WS-GATEWAY] Closing idle session for agent {agent_id}")
                        await self._close_entry(entry)

            # Proactive health check — ping live connections outside the lock.
            # Skip entries with recent activity (within 120s) to avoid killing
            # connections that are actively streaming (e.g. during model escalation
            # where Opus thinking time can exceed 60s with no data frames).
            for agent_id, entry in to_ping:
                if entry.in_use:
                    continue  # Actively streaming — never health-check
                if now - entry.last_used < 120:
                    continue  # Recently active — no health check needed
                try:
                    await asyncio.wait_for(entry.ws.ping(), timeout=30.0)
                except Exception:
                    logger.warning(f"[WS-GATEWAY] Health ping failed for agent {agent_id}, evicting stale connection")
                    await self._evict(agent_id)

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
