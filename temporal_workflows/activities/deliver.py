"""
Message delivery activities — send messages to Letta agents via WS gateway,
acknowledge delivery in Matrix, and dead-letter failed messages.
"""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import httpx
import websockets
from temporalio import activity

from .common import NotifyError

# Re-use the same env vars as notify.py
LETTA_GATEWAY_URL = os.getenv(
    "LETTA_GATEWAY_URL", "ws://192.168.50.90:8407/api/v1/agent-gateway"
)
LETTA_GATEWAY_API_KEY = os.getenv("LETTA_GATEWAY_API_KEY", "")
MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
NTFY_URL = os.getenv("NTFY_URL", "http://192.168.50.90:8409")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "matrix-alerts")

# PostgreSQL for dead letter table
DB_URL = os.getenv(
    "DEAD_LETTER_DB_URL",
    os.getenv("DATABASE_URL", "postgresql://letta:letta@192.168.50.90:5432/matrix_letta")
)


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------

class ClientError(Exception):
    """Non-retryable client error (4xx equivalent)."""
    pass

class AgentNotFoundError(Exception):
    """Non-retryable: agent doesn't exist."""
    pass


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DeliverToLettaInput:
    agent_id: str
    message_body: str
    conversation_id: str = ""
    room_id: str = ""
    source_channel: str = "matrix"


@dataclass
class DeliverToLettaResult:
    success: bool
    response_text: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 1
    duration_ms: int = 0


@dataclass
class DeadLetterInput:
    event_id: str
    room_id: str
    agent_id: str
    message_body: str
    sender: str
    error: str = ""
    attempts: int = 0


@dataclass
class DeadLetterResult:
    success: bool
    error: Optional[str] = None


@dataclass
class DeliveryAckInput:
    room_id: str
    event_id: str
    agent_id: str


@dataclass
class DeliveryAckResult:
    success: bool


# ---------------------------------------------------------------------------
# Activity: deliver_to_letta
# ---------------------------------------------------------------------------

@activity.defn
async def deliver_to_letta(input: DeliverToLettaInput) -> DeliverToLettaResult:
    """Send a message to a Letta agent via WS gateway and collect the response."""
    start = time.monotonic()
    activity.logger.info(
        f"Delivering message to agent {input.agent_id} via WS gateway"
    )

    extra_headers = {}
    if LETTA_GATEWAY_API_KEY:
        extra_headers["X-Api-Key"] = LETTA_GATEWAY_API_KEY

    ws = None
    try:
        ws = await websockets.connect(
            LETTA_GATEWAY_URL,
            additional_headers=extra_headers,
            max_size=2**22,
            close_timeout=5,
            open_timeout=10,
        )

        # Session start
        session_payload = {"type": "session_start", "agent_id": input.agent_id}
        if input.conversation_id:
            session_payload["conversation_id"] = input.conversation_id

        await ws.send(json.dumps(session_payload))

        raw_init = await asyncio.wait_for(ws.recv(), timeout=15.0)
        init_event = json.loads(raw_init)

        if init_event.get("type") == "error":
            error_msg = init_event.get("message", "Unknown error")
            # Check if it's a client error (agent not found, etc.)
            if "not found" in error_msg.lower() or "does not exist" in error_msg.lower():
                raise AgentNotFoundError(f"Agent {input.agent_id}: {error_msg}")
            raise NotifyError(f"Gateway session error: {error_msg}")

        if init_event.get("type") != "session_init":
            raise NotifyError(
                f"Expected session_init, got {init_event.get('type')}"
            )

        activity.logger.info(
            f"Gateway session established: agent={input.agent_id}, "
            f"session={init_event.get('session_id')}"
        )

        # Send message
        msg_payload: dict[str, Any] = {
            "type": "message",
            "content": input.message_body,
            "request_id": f"temporal-deliver-{uuid.uuid4()}",
        }
        if input.room_id:
            msg_payload["source"] = {
                "channel": input.source_channel,
                "chatId": input.room_id,
            }

        await ws.send(json.dumps(msg_payload))

        # Collect response
        _RECEIVE_TIMEOUT = 300  # 5 minutes per event
        _MAX_EVENTS = 1000
        response_chunks: list[str] = []
        event_count = 0

        while True:
            event_count += 1
            if event_count > _MAX_EVENTS:
                raise NotifyError(f"Exceeded {_MAX_EVENTS} events without result")

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECEIVE_TIMEOUT)
            except asyncio.TimeoutError:
                raise NotifyError(
                    f"Receive timeout ({_RECEIVE_TIMEOUT}s) waiting for response"
                )
            except websockets.ConnectionClosed:
                break

            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = event.get("type")

            if event_type == "error":
                raise NotifyError(
                    f"Gateway error: {event.get('message', 'Unknown error')}"
                )

            if event_type == "stream" and event.get("event") == "assistant":
                chunk = event.get("content")
                if chunk:
                    response_chunks.append(chunk)

            if event_type == "result":
                elapsed = int((time.monotonic() - start) * 1000)
                response_text = "".join(response_chunks).strip() or None
                activity.logger.info(
                    f"Delivered to agent {input.agent_id}, {elapsed}ms, "
                    f"response_len={len(response_text) if response_text else 0}"
                )
                return DeliverToLettaResult(
                    success=True,
                    response_text=response_text,
                    duration_ms=elapsed,
                )

        raise NotifyError("Gateway stream ended without result")

    except (ClientError, AgentNotFoundError):
        raise  # Non-retryable, let Temporal propagate
    except NotifyError:
        raise  # Retryable via Temporal
    except asyncio.TimeoutError as e:
        raise NotifyError(f"Timeout connecting to gateway: {e}") from e
    except websockets.ConnectionClosed as e:
        raise NotifyError(f"Gateway connection closed: {e}") from e
    except Exception as e:
        raise NotifyError(f"Delivery error: {e}") from e
    finally:
        if ws:
            try:
                await ws.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Activity: dead_letter_message
# ---------------------------------------------------------------------------

@activity.defn
async def dead_letter_message(input: DeadLetterInput) -> DeadLetterResult:
    """Write a failed message to the dead_letter_messages table and send ntfy alert."""
    activity.logger.info(
        f"Dead-lettering message: event={input.event_id} "
        f"room={input.room_id} agent={input.agent_id}"
    )

    try:
        # Write to PostgreSQL
        import asyncpg
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute(
                """
                INSERT INTO dead_letter_messages
                    (event_id, room_id, agent_id, message_body, sender, error, attempts)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (event_id) DO UPDATE SET
                    error = EXCLUDED.error,
                    attempts = EXCLUDED.attempts,
                    failed_at = NOW()
                """,
                input.event_id,
                input.room_id,
                input.agent_id,
                input.message_body[:10000],  # Truncate very long messages
                input.sender,
                input.error[:2000] if input.error else "",
                input.attempts,
            )
        finally:
            await conn.close()

        activity.logger.info(f"Dead-lettered to DB: event={input.event_id}")

    except Exception as db_err:
        activity.logger.error(f"Failed to write dead letter to DB: {db_err}")
        # Don't fail the activity — still send the ntfy alert

    # Send ntfy alert
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{NTFY_URL}/{NTFY_TOPIC}",
                headers={
                    "Title": "Dead Letter: Message delivery failed",
                    "Priority": "high",
                    "Tags": "warning,skull",
                },
                content=(
                    f"Message to agent {input.agent_id} failed after {input.attempts} attempts.\n"
                    f"Room: {input.room_id}\n"
                    f"Event: {input.event_id}\n"
                    f"Sender: {input.sender}\n"
                    f"Error: {input.error[:200] if input.error else 'Unknown'}"
                ),
            )
    except Exception as ntfy_err:
        activity.logger.warning(f"Failed to send ntfy alert: {ntfy_err}")

    return DeadLetterResult(success=True)


# ---------------------------------------------------------------------------
# Activity: send_delivery_ack
# ---------------------------------------------------------------------------

@activity.defn
async def send_delivery_ack(input: DeliveryAckInput) -> DeliveryAckResult:
    """Send a read receipt for the original Matrix event (best-effort)."""
    activity.logger.info(
        f"Sending delivery ack: event={input.event_id} room={input.room_id}"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{MATRIX_API_URL}/api/v1/messages/read-receipt",
                json={
                    "agent_id": input.agent_id,
                    "room_id": input.room_id,
                    "event_id": input.event_id,
                },
            )
            # Don't fail on 404 — endpoint might not exist yet
            if response.status_code >= 500:
                raise Exception(f"Matrix API {response.status_code}: {response.text[:200]}")

        return DeliveryAckResult(success=True)

    except Exception as e:
        activity.logger.warning(f"Delivery ack failed: {e}")
        return DeliveryAckResult(success=False)
