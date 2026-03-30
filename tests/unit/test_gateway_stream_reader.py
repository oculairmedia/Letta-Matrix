"""Tests for src/letta/gateway_stream_reader.py — WebSocket gateway → StreamEvent."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.matrix.streaming import StreamEvent, StreamEventType
from src.letta.gateway_stream_reader import (
    _parse_gateway_event,
    _parse_stream_event,
    stream_via_gateway,
    collect_via_gateway,
)
from src.letta.ws_gateway_client import GatewaySessionError


# ---------------------------------------------------------------------------
# _parse_gateway_event
# ---------------------------------------------------------------------------


class TestParseGatewayEvent:

    def test_session_init_returns_none(self):
        raw = {"type": "session_init", "session_id": "s1", "conversation_id": "c1"}
        assert _parse_gateway_event(raw, include_reasoning=False) is None

    def test_result_returns_stop(self):
        raw = {"type": "result", "data": "done"}
        event = _parse_gateway_event(raw, include_reasoning=False)
        assert event is not None
        assert event.type == StreamEventType.STOP

    def test_error_returns_error_event(self):
        raw = {"type": "error", "message": "bad request", "code": "ERR_400"}
        event = _parse_gateway_event(raw, include_reasoning=False)
        assert event is not None
        assert event.type == StreamEventType.ERROR
        assert event.content == "bad request"
        assert event.metadata["code"] == "ERR_400"

    def test_unknown_type_returns_none(self):
        raw = {"type": "ping"}
        assert _parse_gateway_event(raw, include_reasoning=False) is None

    def test_stream_type_delegates(self):
        raw = {"type": "stream", "event": "assistant", "content": "hello"}
        event = _parse_gateway_event(raw, include_reasoning=False)
        assert event is not None
        assert event.type == StreamEventType.ASSISTANT
        assert event.content == "hello"


class TestParseStreamEvent:

    def test_assistant_event(self):
        raw = {"type": "stream", "event": "assistant", "content": "Hi", "uuid": "msg-1"}
        event = _parse_stream_event(raw, include_reasoning=False)
        assert event.type == StreamEventType.ASSISTANT
        assert event.content == "Hi"
        assert event.metadata["message_id"] == "msg-1"

    def test_tool_call_event(self):
        raw = {
            "type": "stream",
            "event": "tool_call",
            "content": None,
            "tool_name": "search",
            "tool_call_id": "tc-1",
            "arguments": '{"query": "test"}',
        }
        event = _parse_stream_event(raw, include_reasoning=False)
        assert event.type == StreamEventType.TOOL_CALL
        assert event.metadata["tool_name"] == "search"
        assert event.metadata["tool_call_id"] == "tc-1"

    def test_tool_result_event(self):
        raw = {
            "type": "stream",
            "event": "tool_result",
            "content": "result data",
            "tool_name": "search",
            "is_error": False,
            "tool_call_id": "tc-1",
        }
        event = _parse_stream_event(raw, include_reasoning=False)
        assert event.type == StreamEventType.TOOL_RETURN
        assert event.metadata["status"] == "success"

    def test_tool_result_error(self):
        raw = {"type": "stream", "event": "tool_result", "is_error": True}
        event = _parse_stream_event(raw, include_reasoning=False)
        assert event.metadata["status"] == "error"

    def test_reasoning_filtered_by_default(self):
        raw = {"type": "stream", "event": "reasoning", "content": "thinking..."}
        event = _parse_stream_event(raw, include_reasoning=False)
        assert event is None

    def test_reasoning_included_when_enabled(self):
        raw = {"type": "stream", "event": "reasoning", "content": "thinking..."}
        event = _parse_stream_event(raw, include_reasoning=True)
        assert event is not None
        assert event.type == StreamEventType.REASONING

    def test_unknown_stream_event_returns_none(self):
        raw = {"type": "stream", "event": "custom_unknown"}
        assert _parse_stream_event(raw, include_reasoning=False) is None


# ---------------------------------------------------------------------------
# stream_via_gateway
# ---------------------------------------------------------------------------


class TestStreamViaGateway:

    @pytest.mark.asyncio
    async def test_simple_assistant_message(self):
        """Single assistant message followed by result → one ASSISTANT + STOP."""
        raw_events = [
            {"type": "stream", "event": "assistant", "content": "Hello", "uuid": "m1"},
            {"type": "result"},
        ]

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        events = []
        async for ev in stream_via_gateway(mock_client, "agent-1", "Hi"):
            events.append(ev)

        types = [e.type for e in events]
        assert StreamEventType.ASSISTANT in types
        assert StreamEventType.STOP in types
        assistant_ev = next(e for e in events if e.type == StreamEventType.ASSISTANT)
        assert assistant_ev.content == "Hello"

    @pytest.mark.asyncio
    async def test_tool_call_flushes_assistant(self):
        """Assistant chunks should be flushed before a tool call."""
        raw_events = [
            {"type": "stream", "event": "assistant", "content": "Let me ", "uuid": "m1"},
            {"type": "stream", "event": "assistant", "content": "check.", "uuid": "m1"},
            {"type": "stream", "event": "tool_call", "tool_name": "search", "content": None},
            {"type": "result"},
        ]

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        events = []
        async for ev in stream_via_gateway(mock_client, "agent-1", "search"):
            events.append(ev)

        types = [e.type for e in events]
        # Assistant should be flushed before tool call
        assert types.index(StreamEventType.ASSISTANT) < types.index(StreamEventType.TOOL_CALL)
        assistant_ev = next(e for e in events if e.type == StreamEventType.ASSISTANT)
        assert assistant_ev.content == "Let me check."

    @pytest.mark.asyncio
    async def test_tool_loop_protection(self):
        """Too many tool calls should trigger an error event."""
        raw_events = [
            {"type": "stream", "event": "tool_call", "tool_name": f"tool_{i}", "content": None}
            for i in range(105)
        ]
        raw_events.append({"type": "result"})

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        events = []
        async for ev in stream_via_gateway(
            mock_client, "agent-1", "msg", max_tool_calls=3
        ):
            events.append(ev)

        error_events = [e for e in events if e.type == StreamEventType.ERROR]
        assert len(error_events) == 1
        assert "tool loop" in error_events[0].content.lower()

    @pytest.mark.asyncio
    async def test_uuid_change_splits_assistant_bubbles(self):
        """Different message UUIDs should produce separate assistant events."""
        raw_events = [
            {"type": "stream", "event": "assistant", "content": "First", "uuid": "m1"},
            {"type": "stream", "event": "assistant", "content": "Second", "uuid": "m2"},
            {"type": "result"},
        ]

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        events = []
        async for ev in stream_via_gateway(mock_client, "agent-1", "msg"):
            events.append(ev)

        assistant_events = [e for e in events if e.type == StreamEventType.ASSISTANT]
        assert len(assistant_events) == 2
        assert assistant_events[0].content == "First"
        assert assistant_events[1].content == "Second"

    @pytest.mark.asyncio
    async def test_list_message_serialized_to_json(self):
        """List messages should be JSON-serialized."""
        captured_messages = []

        async def fake_stream(**kwargs):
            captured_messages.append(kwargs.get("message"))
            yield {"type": "result"}

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        events = []
        async for ev in stream_via_gateway(
            mock_client, "agent-1", [{"role": "user", "content": "hi"}]
        ):
            events.append(ev)

        assert captured_messages[0] == '[{"role": "user", "content": "hi"}]'


# ---------------------------------------------------------------------------
# collect_via_gateway
# ---------------------------------------------------------------------------


class TestCollectViaGateway:

    @pytest.mark.asyncio
    async def test_returns_last_assistant_content(self):
        raw_events = [
            {"type": "stream", "event": "assistant", "content": "First", "uuid": "m1"},
            {"type": "stream", "event": "assistant", "content": "Final answer", "uuid": "m2"},
            {"type": "result"},
        ]

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        result = await collect_via_gateway(mock_client, "agent-1", "question")
        assert result == "Final answer"

    @pytest.mark.asyncio
    async def test_raises_on_error_event(self):
        raw_events = [
            {"type": "error", "message": "Something went wrong", "code": "ERR_500"},
        ]

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        with pytest.raises(GatewaySessionError):
            await collect_via_gateway(mock_client, "agent-1", "msg")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_assistant_content(self):
        raw_events = [
            {"type": "stream", "event": "tool_call", "tool_name": "noop", "content": None},
            {"type": "result"},
        ]

        async def fake_stream(**kwargs):
            for e in raw_events:
                yield e

        mock_client = MagicMock()
        mock_client.send_message_streaming = fake_stream

        result = await collect_via_gateway(mock_client, "agent-1", "msg")
        assert result is None
