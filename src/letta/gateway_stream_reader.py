"""
Converts raw WebSocket gateway events into the canonical StreamEvent format
consumed by StreamingMessageHandler / LiveEditStreamingHandler.
"""

import logging
from typing import Any, AsyncGenerator, Dict, Optional

from src.matrix.streaming import StreamEvent, StreamEventType
from src.letta.ws_gateway_client import (
    GatewayClient,
    GatewaySessionError,
    GatewayUnavailableError,
)

logger = logging.getLogger("matrix_client.gateway_stream_reader")

_EVENT_MAP: Dict[str, StreamEventType] = {
    "assistant": StreamEventType.ASSISTANT,
    "tool_call": StreamEventType.TOOL_CALL,
    "tool_result": StreamEventType.TOOL_RETURN,
    "reasoning": StreamEventType.REASONING,
}


async def stream_via_gateway(
    client: GatewayClient,
    agent_id: str,
    message: str,
    conversation_id: Optional[str] = None,
    include_reasoning: bool = False,
    max_tool_calls: int = 100,
) -> AsyncGenerator[StreamEvent, None]:
    """
    Yield StreamEvents from the WS gateway, matching the same interface as
    StepStreamReader.stream_message() so handlers can consume either source.
    """
    tool_call_count = 0
    assistant_chunks: list[str] = []
    assistant_metadata: Dict[str, Any] = {}
    current_assistant_uuid: Optional[str] = None

    def _flush_assistant():
        """Build a flushed StreamEvent from accumulated chunks (non-yielding helper)."""
        if not assistant_chunks:
            return None
        ev = StreamEvent(
            type=StreamEventType.ASSISTANT,
            content="".join(assistant_chunks),
            metadata=dict(assistant_metadata),
        )
        assistant_chunks.clear()
        assistant_metadata.clear()
        return ev

    async for raw_event in client.send_message_streaming(
        agent_id=agent_id,
        message=message,
        conversation_id=conversation_id,
    ):
        event = _parse_gateway_event(raw_event, include_reasoning)
        if event is None:
            continue

        if event.type == StreamEventType.TOOL_CALL:
            # Flush any pending assistant content before tool activity
            # (mirrors bot.ts type-change finalize, line 580)
            flushed = _flush_assistant()
            if flushed:
                current_assistant_uuid = None
                yield flushed
            tool_call_count += 1
            if tool_call_count > max_tool_calls:
                logger.error(
                    f"[GW-STREAM] Tool loop: {tool_call_count} calls "
                    f"(limit={max_tool_calls}), aborting"
                )
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    content=f"Agent stuck in tool loop ({tool_call_count} calls). Stopped.",
                    metadata={"error_type": "tool_loop"},
                )
                return
            yield event
            continue

        if event.type == StreamEventType.ASSISTANT:
            # Detect UUID change — different send_message call = separate bubble
            # (mirrors bot.ts UUID-based splitting, line 612)
            msg_uuid = event.metadata.get("message_id")
            if (
                msg_uuid
                and current_assistant_uuid
                and msg_uuid != current_assistant_uuid
                and assistant_chunks
            ):
                flushed = _flush_assistant()
                if flushed:
                    yield flushed
            current_assistant_uuid = msg_uuid or current_assistant_uuid
            if event.content:
                assistant_chunks.append(event.content)
            assistant_metadata.update(event.metadata)
            continue

        if event.type == StreamEventType.STOP:
            flushed = _flush_assistant()
            if flushed:
                yield flushed
            current_assistant_uuid = None
            yield event
            continue

        yield event


async def collect_via_gateway(
    client: GatewayClient,
    agent_id: str,
    message: str,
    conversation_id: Optional[str] = None,
) -> Optional[str]:
    """
    Send through the gateway, collect everything, and return the final assistant content.
    Used by the non-streaming path (send_to_letta_api).
    """
    assistant_content: Optional[str] = None

    async for event in stream_via_gateway(
        client=client,
        agent_id=agent_id,
        message=message,
        conversation_id=conversation_id,
        include_reasoning=False,
    ):
        if event.type == StreamEventType.ASSISTANT and event.content:
            assistant_content = event.content
        elif event.type == StreamEventType.ERROR:
            raise GatewaySessionError(
                code="STREAM_ERROR",
                message=event.content or "Unknown streaming error",
            )

    return assistant_content


def _parse_gateway_event(
    raw: Dict[str, Any],
    include_reasoning: bool,
) -> Optional[StreamEvent]:
    event_type = raw.get("type")

    if event_type == "stream":
        return _parse_stream_event(raw, include_reasoning)

    if event_type == "result":
        return StreamEvent(type=StreamEventType.STOP, metadata=raw)

    if event_type == "session_init":
        logger.debug(
            f"[GW-STREAM] Session init: session={raw.get('session_id')} "
            f"conversation={raw.get('conversation_id')}"
        )
        return None

    if event_type == "error":
        return StreamEvent(
            type=StreamEventType.ERROR,
            content=raw.get("message", "Unknown gateway error"),
            metadata={"error_type": "gateway", "code": raw.get("code")},
        )

    logger.debug(f"[GW-STREAM] Unknown event type: {event_type}")
    return None


def _parse_stream_event(
    raw: Dict[str, Any],
    include_reasoning: bool,
) -> Optional[StreamEvent]:
    sub_event = raw.get("event", "")
    mapped_type = _EVENT_MAP.get(sub_event)

    if mapped_type is None:
        logger.debug(f"[GW-STREAM] Unmapped stream event: {sub_event}")
        return None

    if mapped_type == StreamEventType.REASONING and not include_reasoning:
        return None

    metadata: Dict[str, Any] = {}

    if mapped_type == StreamEventType.TOOL_CALL:
        metadata["tool_name"] = raw.get("tool_name", "unknown")
        if raw.get("tool_call_id"):
            metadata["tool_call_id"] = raw["tool_call_id"]

    elif mapped_type == StreamEventType.TOOL_RETURN:
        metadata["tool_name"] = raw.get("tool_name", "unknown")
        metadata["status"] = "success"
        if raw.get("tool_call_id"):
            metadata["tool_call_id"] = raw["tool_call_id"]

    elif mapped_type == StreamEventType.ASSISTANT:
        if raw.get("uuid"):
            metadata["message_id"] = raw["uuid"]

    return StreamEvent(
        type=mapped_type,
        content=raw.get("content"),
        metadata=metadata,
    )
