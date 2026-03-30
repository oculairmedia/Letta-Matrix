"""
Unit tests for letta/message_retry_buffer.py

Tests cover:
- PendingMessage creation and defaults
- MessageRetryBuffer.stash() — happy path, overflow/drop, starts retry loop
- MessageRetryBuffer.pending_count property
- _expire_stale() — TTL expiry with error callback
- _notify_error() — callback invocation and silent failure
- get_retry_buffer() singleton
- Buffer overflow drops oldest and calls error callback
"""

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.letta.message_retry_buffer import (
    MAX_BUFFER_SIZE,
    MESSAGE_TTL_SECONDS,
    MessageRetryBuffer,
    PendingMessage,
    get_retry_buffer,
    _global_buffer,
)


class TestPendingMessage:
    def test_defaults(self):
        msg = PendingMessage(
            room_id="!room:test",
            agent_id="agent-1",
            message_body="hello",
            conversation_id=None,
            sender="@user:test",
            config=Mock(),
            is_streaming=False,
        )
        assert msg.room_id == "!room:test"
        assert msg.attempt_count == 0
        assert msg.reply_callback is None
        assert msg.error_callback is None
        assert msg.context == {}
        assert isinstance(msg.created_at, float)

    def test_custom_fields(self):
        cb = AsyncMock()
        msg = PendingMessage(
            room_id="!r:t",
            agent_id="a-2",
            message_body=["multipart"],
            conversation_id="conv-1",
            sender="@s:t",
            config=Mock(),
            is_streaming=True,
            reply_callback=cb,
            error_callback=cb,
            context={"key": "val"},
        )
        assert msg.conversation_id == "conv-1"
        assert msg.is_streaming is True
        assert msg.context == {"key": "val"}


class TestMessageRetryBuffer:
    @pytest.mark.asyncio
    async def test_stash_adds_message_and_returns_count(self):
        buf = MessageRetryBuffer()
        msg = PendingMessage(
            room_id="!r:t", agent_id="a", message_body="hi",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
        )
        count = await buf.stash(msg)
        assert count == 1
        assert buf.pending_count == 1

    @pytest.mark.asyncio
    async def test_stash_multiple_increments(self):
        buf = MessageRetryBuffer()
        for i in range(3):
            count = await buf.stash(PendingMessage(
                room_id=f"!r{i}:t", agent_id="a", message_body="hi",
                conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
            ))
        assert count == 3
        assert buf.pending_count == 3

    @pytest.mark.asyncio
    async def test_stash_overflow_drops_oldest(self):
        buf = MessageRetryBuffer()
        error_cb = AsyncMock()

        # Fill buffer
        for i in range(MAX_BUFFER_SIZE):
            await buf.stash(PendingMessage(
                room_id=f"!r{i}:t", agent_id="a", message_body=f"msg-{i}",
                conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
                error_callback=error_cb if i == 0 else None,
            ))

        assert buf.pending_count == MAX_BUFFER_SIZE

        # One more triggers overflow — oldest (msg-0) gets dropped
        count = await buf.stash(PendingMessage(
            room_id="!overflow:t", agent_id="a", message_body="overflow",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
        ))
        assert count == MAX_BUFFER_SIZE
        # Error callback should have been called for the dropped message
        error_cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stash_starts_retry_loop(self):
        buf = MessageRetryBuffer()
        msg = PendingMessage(
            room_id="!r:t", agent_id="a", message_body="hi",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
        )

        with patch.object(buf, '_retry_loop', new_callable=AsyncMock) as mock_loop:
            await buf.stash(msg)
            # Give the event loop a tick to create the task
            await asyncio.sleep(0)
            assert buf._retry_task is not None

    @pytest.mark.asyncio
    async def test_pending_count_empty(self):
        buf = MessageRetryBuffer()
        assert buf.pending_count == 0

    @pytest.mark.asyncio
    async def test_expire_stale_removes_old_messages(self):
        buf = MessageRetryBuffer()
        error_cb = AsyncMock()

        # Manually add an expired message
        old_msg = PendingMessage(
            room_id="!old:t", agent_id="a", message_body="old",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
            created_at=time.monotonic() - MESSAGE_TTL_SECONDS - 10,
            error_callback=error_cb,
        )
        buf._buffer.append(old_msg)
        assert buf.pending_count == 1

        await buf._expire_stale()
        assert buf.pending_count == 0
        error_cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expire_stale_keeps_fresh_messages(self):
        buf = MessageRetryBuffer()
        fresh = PendingMessage(
            room_id="!fresh:t", agent_id="a", message_body="fresh",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
        )
        buf._buffer.append(fresh)

        await buf._expire_stale()
        assert buf.pending_count == 1

    @pytest.mark.asyncio
    async def test_notify_error_calls_callback(self):
        buf = MessageRetryBuffer()
        error_cb = AsyncMock()
        msg = PendingMessage(
            room_id="!r:t", agent_id="a", message_body="hi",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
            error_callback=error_cb,
        )
        await buf._notify_error(msg, "something went wrong")
        error_cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_error_no_callback_does_not_raise(self):
        buf = MessageRetryBuffer()
        msg = PendingMessage(
            room_id="!r:t", agent_id="a", message_body="hi",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
        )
        # Should not raise
        await buf._notify_error(msg, "something went wrong")

    @pytest.mark.asyncio
    async def test_notify_error_swallows_callback_exception(self):
        buf = MessageRetryBuffer()
        error_cb = AsyncMock(side_effect=RuntimeError("boom"))
        msg = PendingMessage(
            room_id="!r:t", agent_id="a", message_body="hi",
            conversation_id=None, sender="@u:t", config=Mock(), is_streaming=False,
            error_callback=error_cb,
        )
        # Should not raise despite callback failure
        await buf._notify_error(msg, "test")


class TestGetRetryBuffer:
    def test_returns_singleton(self):
        import src.letta.message_retry_buffer as mod
        mod._global_buffer = None
        buf1 = get_retry_buffer()
        buf2 = get_retry_buffer()
        assert buf1 is buf2
        mod._global_buffer = None  # cleanup

    def test_creates_instance_if_none(self):
        import src.letta.message_retry_buffer as mod
        mod._global_buffer = None
        buf = get_retry_buffer()
        assert isinstance(buf, MessageRetryBuffer)
        mod._global_buffer = None
