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

from .common import MatrixAPIError, NotifyError

MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
LETTA_GATEWAY_URL = os.getenv(
    "LETTA_GATEWAY_URL", "ws://192.168.50.90:8407/api/v1/agent-gateway"
)
LETTA_GATEWAY_API_KEY = os.getenv("LETTA_GATEWAY_API_KEY", "")


@dataclass
class NotifyAgentInput:
    agent_id: str
    message: str
    room_id: str = ""
    conversation_id: str = ""
    wait_for_result: bool = True


@dataclass
class NotifyAgentResult:
    success: bool
    duration_ms: int = 0
    error: Optional[str] = None
    response_text: Optional[str] = None


@dataclass
class MatrixStatusInput:
    room_id: str
    message: str
    agent_id: str
    event_id: Optional[str] = None
    msgtype: str = "m.notice"


@dataclass
class MatrixStatusResult:
    event_id: Optional[str] = None
    success: bool = True
    duration_ms: int = 0


@activity.defn
async def notify_letta_agent(input: NotifyAgentInput) -> NotifyAgentResult:
    start = time.monotonic()
    mode = "fire-and-forget" if not input.wait_for_result else "wait-for-result"
    activity.logger.info(f"Notifying agent {input.agent_id} via WS gateway ({mode})")

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

        session_payload = {
            "type": "session_start",
            "agent_id": input.agent_id,
        }
        if input.conversation_id:
            session_payload["conversation_id"] = input.conversation_id

        session_start = json.dumps(session_payload)
        await ws.send(session_start)

        raw_init = await asyncio.wait_for(ws.recv(), timeout=15.0)
        init_event = json.loads(raw_init)

        if init_event.get("type") == "error":
            raise NotifyError(
                f"Gateway session error for agent {input.agent_id}: "
                f"{init_event.get('message', 'Unknown error')}"
            )
        if init_event.get("type") != "session_init":
            raise NotifyError(
                f"Expected session_init from gateway, got {init_event.get('type')}"
            )

        activity.logger.info(
            f"Gateway session established for agent {input.agent_id}, "
            f"session={init_event.get('session_id')}"
        )

        msg_payload_body: dict[str, Any] = {
            "type": "message",
            "content": input.message,
            "request_id": f"temporal-notify-{uuid.uuid4()}",
        }
        if input.room_id:
            msg_payload_body["source"] = {
                "channel": "matrix",
                "chatId": input.room_id,
            }

        msg_payload = json.dumps(msg_payload_body)
        await ws.send(msg_payload)

        if not input.wait_for_result:
            elapsed = int((time.monotonic() - start) * 1000)
            activity.logger.info(
                f"Fire-and-forget: sent message to agent {input.agent_id}, {elapsed}ms"
            )
            return NotifyAgentResult(
                success=True,
                duration_ms=elapsed,
                response_text=None,
            )

        _RECEIVE_TIMEOUT = 60  # seconds per event
        _MAX_EVENTS = 500  # safety cap
        response_chunks: list[str] = []
        event_count = 0
        while True:
            event_count += 1
            if event_count > _MAX_EVENTS:
                raise NotifyError(
                    f"Exceeded {_MAX_EVENTS} events without result for agent {input.agent_id}"
                )
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECEIVE_TIMEOUT)
            except asyncio.TimeoutError:
                raise NotifyError(
                    f"Receive timeout ({_RECEIVE_TIMEOUT}s) waiting for agent {input.agent_id} response"
                )
            except websockets.ConnectionClosed:
                break  # falls through to 'stream ended without result' error
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = event.get("type")

            if event_type == "error":
                raise NotifyError(
                    f"Gateway error for agent {input.agent_id}: "
                    f"{event.get('message', 'Unknown error')}"
                )

            if event_type == "stream" and event.get("event") == "assistant":
                chunk = event.get("content")
                if chunk:
                    response_chunks.append(chunk)

            if event_type == "result":
                elapsed = int((time.monotonic() - start) * 1000)
                response_text = "".join(response_chunks).strip() or None
                activity.logger.info(
                    f"Notified agent {input.agent_id} via WS gateway, {elapsed}ms, "
                    f"response_len={len(response_text) if response_text else 0}"
                )
                return NotifyAgentResult(
                    success=True,
                    duration_ms=elapsed,
                    response_text=response_text,
                )

        raise NotifyError(
            f"Gateway stream ended without result for agent {input.agent_id}"
        )

    except NotifyError:
        raise
    except asyncio.TimeoutError as e:
        raise NotifyError(
            f"Timeout connecting to gateway for agent {input.agent_id}: {e}"
        ) from e
    except websockets.ConnectionClosed as e:
        raise NotifyError(
            f"Gateway connection closed for agent {input.agent_id}: {e}"
        ) from e
    except Exception as e:
        raise NotifyError(f"Error notifying agent {input.agent_id}: {e}") from e
    finally:
        if ws:
            try:
                await ws.close()
            except Exception:
                pass


@activity.defn
async def update_matrix_status(input: MatrixStatusInput) -> MatrixStatusResult:
    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if input.event_id:
                activity.logger.info(
                    f"Editing status message {input.event_id} in {input.room_id}"
                )
                response = await client.post(
                    f"{MATRIX_API_URL}/api/v1/messages/edit-as-agent",
                    json={
                        "agent_id": input.agent_id,
                        "room_id": input.room_id,
                        "event_id": input.event_id,
                        "message": input.message,
                        "msgtype": input.msgtype,
                    },
                )
            else:
                activity.logger.info(
                    f"Sending status message to {input.room_id} as agent {input.agent_id}"
                )
                response = await client.post(
                    f"{MATRIX_API_URL}/api/v1/messages/send-as-agent",
                    json={
                        "agent_id": input.agent_id,
                        "room_id": input.room_id,
                        "message": input.message,
                        "msgtype": input.msgtype,
                    },
                )

            if response.status_code >= 400:
                raise MatrixAPIError(
                    f"Matrix API {response.status_code}: {response.text[:500]}"
                )

            result = response.json()
            if result.get("success") is False:
                raise MatrixAPIError(
                    f"Matrix API logical failure: {result.get('error', 'unknown error')}"
                )

            event_id = result.get("event_id") or input.event_id
            elapsed = int((time.monotonic() - start) * 1000)

            activity.logger.info(
                f"Matrix status updated in {input.room_id}, event={event_id}, {elapsed}ms"
            )
            return MatrixStatusResult(event_id=event_id, success=True, duration_ms=elapsed)

    except MatrixAPIError:
        raise
    except httpx.TimeoutException as e:
        raise MatrixAPIError(f"Timeout updating Matrix status in {input.room_id}: {e}") from e
    except Exception as e:
        raise MatrixAPIError(f"Error updating Matrix status in {input.room_id}: {e}") from e
