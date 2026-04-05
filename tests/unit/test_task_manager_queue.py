"""Tests for per-room message queue when agent is busy (GitHub #28)."""

import asyncio
import collections
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.matrix.task_manager import (
    _active_letta_tasks,
    _dispatch_letta_task,
    _handle_stop_command,
    _on_letta_task_done,
    _pending_queues,
    _QueuedMessage,
    _MAX_QUEUE_SIZE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeRoom:
    room_id: str = "!room:test"
    display_name: str = "Test Room"

    def user_name(self, sender):
        return sender


@dataclass
class _FakeEvent:
    sender: str = "@user:test"
    event_id: str = "$evt1"
    source: dict = None


@pytest.fixture(autouse=True)
def _clean_state():
    """Ensure module-level state is clean between tests."""
    _active_letta_tasks.clear()
    _pending_queues.clear()
    yield
    _active_letta_tasks.clear()
    _pending_queues.clear()


@pytest.fixture
def mock_send():
    with patch("src.matrix.task_manager.send_as_agent", new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def mock_process():
    with patch("src.matrix.task_manager.process_letta_message", new_callable=AsyncMock) as m:
        yield m


# ---------------------------------------------------------------------------
# Tests: message queuing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_queued_when_busy(mock_send, mock_process):
    """When an agent is busy, the incoming message should be queued."""
    room = _FakeRoom()
    config = MagicMock()
    task_key = (room.room_id, "agent-1")

    # Simulate an active task
    active = asyncio.Future()
    _active_letta_tasks[task_key] = asyncio.ensure_future(active)

    result = await _dispatch_letta_task(
        room=room,
        event=_FakeEvent(),
        config=config,
        logger=MagicMock(),
        client=MagicMock(),
        room_agent_id="agent-1",
        gating_result=None,
        message_text="hello",
        user_reply_to_event_id=None,
    )

    assert result is True
    assert task_key in _pending_queues
    assert len(_pending_queues[task_key]) == 1
    assert _pending_queues[task_key][0].message_text == "hello"

    # Notice should mention position
    mock_send.assert_called_once()
    notice_text = mock_send.call_args[0][1]
    assert "Queued" in notice_text
    assert "position 1" in notice_text

    # Cleanup
    active.cancel()
    _active_letta_tasks[task_key].cancel()


@pytest.mark.asyncio
async def test_queue_respects_max_size(mock_send, mock_process):
    """Queue should reject messages once full."""
    room = _FakeRoom()
    config = MagicMock()
    task_key = (room.room_id, "agent-1")

    active = asyncio.Future()
    _active_letta_tasks[task_key] = asyncio.ensure_future(active)

    # Fill the queue
    _pending_queues[task_key] = collections.deque(
        [_QueuedMessage(
            room=room, event=_FakeEvent(), config=config,
            logger=MagicMock(), client=MagicMock(),
            room_agent_id="agent-1", gating_result=None,
            message_text=f"msg-{i}", user_reply_to_event_id=None,
        ) for i in range(_MAX_QUEUE_SIZE)],
        maxlen=_MAX_QUEUE_SIZE,
    )

    result = await _dispatch_letta_task(
        room=room,
        event=_FakeEvent(),
        config=config,
        logger=MagicMock(),
        client=MagicMock(),
        room_agent_id="agent-1",
        gating_result=None,
        message_text="overflow",
        user_reply_to_event_id=None,
    )

    assert result is True
    # Should still have max size (overflow message dropped)
    assert len(_pending_queues[task_key]) == _MAX_QUEUE_SIZE
    # Notice should mention "Queue full"
    notice_text = mock_send.call_args[0][1]
    assert "Queue full" in notice_text

    active.cancel()
    _active_letta_tasks[task_key].cancel()


@pytest.mark.asyncio
async def test_queued_messages_processed_in_order(mock_send, mock_process):
    """After task completion, queued messages should be drained in FIFO order."""
    room = _FakeRoom()
    config = MagicMock()
    task_key = (room.room_id, "agent-1")

    # Queue 2 messages
    _pending_queues[task_key] = collections.deque([
        _QueuedMessage(
            room=room, event=_FakeEvent(), config=config,
            logger=MagicMock(), client=MagicMock(),
            room_agent_id="agent-1", gating_result=None,
            message_text="first", user_reply_to_event_id=None,
        ),
        _QueuedMessage(
            room=room, event=_FakeEvent(), config=config,
            logger=MagicMock(), client=MagicMock(),
            room_agent_id="agent-1", gating_result=None,
            message_text="second", user_reply_to_event_id=None,
        ),
    ], maxlen=_MAX_QUEUE_SIZE)

    # Simulate task completion
    completed_task = asyncio.Future()
    completed_task.set_result(None)

    # Patch _dispatch_letta_task for the drain call to avoid full processing
    with patch("src.matrix.task_manager._dispatch_letta_task", new_callable=AsyncMock) as mock_dispatch:
        _on_letta_task_done(task_key, completed_task)
        # Give the loop a tick to schedule the drain task
        await asyncio.sleep(0.05)

    # First message should be dispatched
    assert mock_dispatch.called
    first_call_text = mock_dispatch.call_args[1]["message_text"]
    assert first_call_text == "first"


@pytest.mark.asyncio
async def test_queue_is_per_room(mock_send, mock_process):
    """Different rooms should have independent queues."""
    config = MagicMock()
    key_a = ("!roomA:test", "agent-1")
    key_b = ("!roomB:test", "agent-1")

    active_a = asyncio.Future()
    active_b = asyncio.Future()
    _active_letta_tasks[key_a] = asyncio.ensure_future(active_a)
    _active_letta_tasks[key_b] = asyncio.ensure_future(active_b)

    await _dispatch_letta_task(
        room=_FakeRoom(room_id="!roomA:test"),
        event=_FakeEvent(), config=config,
        logger=MagicMock(), client=MagicMock(),
        room_agent_id="agent-1", gating_result=None,
        message_text="for room A", user_reply_to_event_id=None,
    )

    await _dispatch_letta_task(
        room=_FakeRoom(room_id="!roomB:test"),
        event=_FakeEvent(), config=config,
        logger=MagicMock(), client=MagicMock(),
        room_agent_id="agent-1", gating_result=None,
        message_text="for room B", user_reply_to_event_id=None,
    )

    assert len(_pending_queues[key_a]) == 1
    assert len(_pending_queues[key_b]) == 1
    assert _pending_queues[key_a][0].message_text == "for room A"
    assert _pending_queues[key_b][0].message_text == "for room B"

    active_a.cancel()
    active_b.cancel()
    _active_letta_tasks[key_a].cancel()
    _active_letta_tasks[key_b].cancel()


@pytest.mark.asyncio
async def test_stop_clears_queue(mock_send):
    """The /stop command should clear the queue for the room."""
    room = _FakeRoom()
    config = MagicMock()
    config.letta_gateway_url = None
    task_key = (room.room_id, "agent-1")

    # Active task + queued messages
    active = asyncio.Future()
    _active_letta_tasks[task_key] = asyncio.ensure_future(active)
    _pending_queues[task_key] = collections.deque([
        _QueuedMessage(
            room=room, event=_FakeEvent(), config=config,
            logger=MagicMock(), client=MagicMock(),
            room_agent_id="agent-1", gating_result=None,
            message_text="queued msg", user_reply_to_event_id=None,
        ),
    ], maxlen=_MAX_QUEUE_SIZE)

    result = await _handle_stop_command(
        room=room,
        config=config,
        logger=MagicMock(),
        room_agent_id="agent-1",
        room_agent_name="TestAgent",
        message_text="/stop",
    )

    assert result is True
    # Queue should be cleared
    assert task_key not in _pending_queues
    # Stop message should mention the cleared queue
    stop_msg = mock_send.call_args[0][1]
    assert "Stopped" in stop_msg
    assert "1 queued" in stop_msg

    active.cancel()


@pytest.mark.asyncio
async def test_task_cancellation_clears_queue():
    """When a task is cancelled, its queue should be cleared too."""
    task_key = ("!room:test", "agent-1")
    _pending_queues[task_key] = collections.deque([MagicMock()], maxlen=_MAX_QUEUE_SIZE)

    cancelled_task = asyncio.Future()
    cancelled_task.cancel()

    try:
        _on_letta_task_done(task_key, cancelled_task)
    except asyncio.CancelledError:
        pass

    assert task_key not in _pending_queues


@pytest.mark.asyncio
async def test_no_queue_when_not_busy(mock_send, mock_process):
    """When no active task exists, message should dispatch normally (no queue)."""
    room = _FakeRoom()
    config = MagicMock()

    result = await _dispatch_letta_task(
        room=room,
        event=_FakeEvent(),
        config=config,
        logger=MagicMock(),
        client=MagicMock(),
        room_agent_id="agent-1",
        gating_result=None,
        message_text="hello",
        user_reply_to_event_id=None,
    )

    assert result is True
    # No queue should be created
    assert not _pending_queues
    # process_letta_message should be called (via asyncio.create_task)
    mock_send.assert_not_called()  # No notice sent
