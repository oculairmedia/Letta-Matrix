"""
Message retry buffer for gateway-down scenarios.

When the WS gateway is unreachable, messages are stashed in an in-memory
buffer and replayed automatically once the gateway recovers.

Constraints:
  - Max 50 pending messages (oldest dropped on overflow)
  - 5-minute TTL per message (expired messages notify the user)
  - Retry probe every 5 seconds while buffer is non-empty
  - No retries when buffer is empty (zero overhead in normal operation)
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Deque, Dict, Optional

logger = logging.getLogger("matrix_client.retry_buffer")

MAX_BUFFER_SIZE = 50
MESSAGE_TTL_SECONDS = 300.0
RETRY_INTERVAL_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 5.0


@dataclass
class PendingMessage:
    """A message that failed due to gateway unavailability."""
    room_id: str
    agent_id: str
    message_body: Any
    conversation_id: Optional[str]
    sender: str
    config: Any
    is_streaming: bool
    created_at: float = field(default_factory=time.monotonic)
    attempt_count: int = 0
    reply_callback: Optional[Callable[..., Coroutine]] = None
    error_callback: Optional[Callable[..., Coroutine]] = None
    context: Dict[str, Any] = field(default_factory=dict)


class MessageRetryBuffer:
    """In-memory buffer that retries messages when the gateway recovers."""

    def __init__(self):
        self._buffer: Deque[PendingMessage] = deque(maxlen=MAX_BUFFER_SIZE)
        self._retry_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def pending_count(self) -> int:
        return len(self._buffer)

    async def stash(self, msg: PendingMessage) -> int:
        dropped: Optional[PendingMessage] = None

        async with self._lock:
            if len(self._buffer) >= MAX_BUFFER_SIZE:
                dropped = self._buffer.popleft()
                logger.warning(
                    f"[RETRY-BUFFER] Buffer full ({MAX_BUFFER_SIZE}), "
                    f"dropping oldest message for room {dropped.room_id}"
                )

            self._buffer.append(msg)
            size = len(self._buffer)

        if dropped and dropped.error_callback:
            try:
                await dropped.error_callback(
                    dropped.room_id,
                    "Your earlier message was dropped because the system was "
                    "recovering from a connection issue. Please resend it.",
                    dropped.config,
                    logger,
                )
            except Exception:
                pass

        logger.info(
            f"[RETRY-BUFFER] Stashed message for room {msg.room_id} "
            f"(agent={msg.agent_id[:12]}..., buffer={size})"
        )

        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._retry_loop())

        return size

    async def _retry_loop(self):
        """Retry stashed messages periodically until buffer is empty."""
        logger.info("[RETRY-BUFFER] Retry loop started")

        # Uses buffer/TTL/probe-driven replay semantics, not fixed-attempt retries.
        while True:
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)

            if not self._buffer:
                logger.info("[RETRY-BUFFER] Buffer empty, stopping retry loop")
                return

            await self._expire_stale()

            if not self._buffer:
                logger.info("[RETRY-BUFFER] All messages expired, stopping retry loop")
                return

            msg = self._buffer[0]

            if not await self._probe_gateway(msg.config):
                continue

            logger.info("[RETRY-BUFFER] Gateway reachable, replaying buffer")
            await self._replay_all()

    async def _probe_gateway(self, config) -> bool:
        try:
            import websockets
            import websockets

            extra_headers = {}
            api_key = config.letta_gateway_api_key or config.letta_token
            if api_key:
                extra_headers["X-Api-Key"] = api_key

            ws = await asyncio.wait_for(
                websockets.connect(
                    config.letta_gateway_url,
                    additional_headers=extra_headers,
                    close_timeout=2,
                ),
                timeout=PROBE_TIMEOUT_SECONDS,
            )
            await ws.close()
            return True
        except Exception as e:
            logger.debug(f"[RETRY-BUFFER] Gateway still down: {e}")
            return False

    async def _replay_all(self):
        async with self._lock:
            messages = list(self._buffer)
            self._buffer.clear()

        # Group messages by agent_id and replay SEQUENTIALLY within each agent
        # to prevent WS connection races (two concurrent _get_or_create for the
        # same agent evict each other's sessions, causing infinite retry loops).
        # Different agents can still replay concurrently.
        from collections import defaultdict
        agent_groups: Dict[str, list] = defaultdict(list)
        for msg in messages:
            agent_groups[msg.agent_id].append(msg)

        results = {"success": 0, "fail": 0, "restashed": []}
        results_lock = asyncio.Lock()

        async def _replay_one(msg: PendingMessage):
            msg.attempt_count += 1
            elapsed = time.monotonic() - msg.created_at

            if elapsed > MESSAGE_TTL_SECONDS:
                logger.warning(
                    f"[RETRY-BUFFER] Message for room {msg.room_id} expired "
                    f"({elapsed:.0f}s > {MESSAGE_TTL_SECONDS:.0f}s TTL)"
                )
                await self._notify_error(
                    msg, "I couldn't process your message in time due to a "
                    "temporary connection issue. Please resend it."
                )
                async with results_lock:
                    results["fail"] += 1
                return

            try:
                from src.letta.gateway_stream_reader import collect_via_gateway
                from src.letta.ws_gateway_client import get_gateway_client
                gw_client = await get_gateway_client(
                    gateway_url=msg.config.letta_gateway_url,
                    idle_timeout=msg.config.letta_gateway_idle_timeout,
                    max_connections=msg.config.letta_gateway_max_connections,
                    api_key=msg.config.letta_gateway_api_key or msg.config.letta_token,
                )
                result = await collect_via_gateway(
                    client=gw_client,
                    agent_id=msg.agent_id,
                    message=msg.message_body,
                    conversation_id=msg.conversation_id,
                    source={"channel": "matrix", "chatId": msg.room_id},
                )

                if result and msg.reply_callback:
                    await msg.reply_callback(
                        msg.room_id, result, msg.config, logger
                    )
                async with results_lock:
                    results["success"] += 1
                logger.info(
                    f"[RETRY-BUFFER] Replayed message for room {msg.room_id} "
                    f"(attempt {msg.attempt_count}, {elapsed:.0f}s old)"
                )

            except Exception as e:
                logger.error(
                    f"[RETRY-BUFFER] Replay failed for room {msg.room_id}: {e}"
                )
                if elapsed < MESSAGE_TTL_SECONDS:
                    async with results_lock:
                        results["restashed"].append(msg)
                        results["fail"] += 1
                else:
                    await self._notify_error(
                        msg, "I couldn't process your message after multiple "
                        "attempts. Please try again."
                    )
                    async with results_lock:
                        results["fail"] += 1

        async def _replay_agent_group(agent_msgs: list):
            """Replay all messages for a single agent sequentially."""
            for msg in agent_msgs:
                await _replay_one(msg)

        # Concurrent across agents, sequential within each agent
        await asyncio.gather(*[
            _replay_agent_group(group) for group in agent_groups.values()
        ])

        if results["restashed"]:
            async with self._lock:
                for msg in results["restashed"]:
                    self._buffer.append(msg)

        logger.info(
            f"[RETRY-BUFFER] Replay complete: {results['success']} succeeded, "
            f"{results['fail']} failed/expired, {len(results['restashed'])} re-stashed"
        )

    async def _notify_error(self, msg: PendingMessage, text: str) -> None:
        if msg.error_callback:
            try:
                await msg.error_callback(msg.room_id, text, msg.config, logger)
            except Exception:
                pass

    async def _expire_stale(self):
        """Remove and notify for any messages past their TTL."""
        now = time.monotonic()
        expired = []

        async with self._lock:
            while self._buffer and (now - self._buffer[0].created_at) > MESSAGE_TTL_SECONDS:
                expired.append(self._buffer.popleft())

        for msg in expired:
            logger.warning(
                f"[RETRY-BUFFER] Message expired for room {msg.room_id} "
                f"(age={now - msg.created_at:.0f}s)"
            )
            if msg.error_callback:
                try:
                    await msg.error_callback(
                        msg.room_id,
                        "I couldn't process your message in time due to a "
                        "temporary connection issue. Please resend it.",
                        msg.config,
                        logger,
                    )
                except Exception:
                    pass


_global_buffer: Optional[MessageRetryBuffer] = None


def get_retry_buffer() -> MessageRetryBuffer:
    global _global_buffer
    if _global_buffer is None:
        _global_buffer = MessageRetryBuffer()
    return _global_buffer
