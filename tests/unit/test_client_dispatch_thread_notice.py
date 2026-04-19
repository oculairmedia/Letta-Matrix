import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.matrix.task_manager import _dispatch_letta_task
from src.matrix.fs_mode_handler import _maybe_handle_fs_mode


@pytest.mark.asyncio
async def test_dispatch_busy_notice_preserves_thread_relation():
    room = SimpleNamespace(room_id="!room:test.com")
    event = SimpleNamespace(
        event_id="$evt-123",
        source={
            "content": {
                "m.relates_to": {
                    "rel_type": "m.thread",
                    "event_id": "$thread-root",
                    "m.in_reply_to": {"event_id": "$thread-latest"},
                }
            }
        },
    )
    logger = Mock()
    active_task = asyncio.create_task(asyncio.sleep(60))

    try:
        with patch("src.matrix.task_manager._active_letta_tasks", {("!room:test.com", "agent-1"): active_task}), patch(
            "src.matrix.task_manager.send_as_agent", new_callable=AsyncMock
        ) as mock_send:
            handled = await _dispatch_letta_task(
                room=room,
                event=event,
                config=Mock(),
                logger=logger,
                client=None,
                room_agent_id="agent-1",
                gating_result=None,
                message_text="hello",
                user_reply_to_event_id="$thread-latest",
            )

        assert handled is True
        assert mock_send.await_count == 1
        await_args = mock_send.await_args
        assert await_args is not None
        kwargs = await_args.kwargs
        assert kwargs["thread_event_id"] == "$thread-root"
        assert kwargs["thread_latest_event_id"] == "$evt-123"
        assert kwargs["reply_to_event_id"] is None
    finally:
        active_task.cancel()
        try:
            await active_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_dispatch_busy_notice_queues_and_sends_notice():
    """When a task is active, new messages are queued and a notice is sent."""
    room = SimpleNamespace(room_id="!room:test.com")
    event = SimpleNamespace(event_id="$evt-124", source={"content": {}})
    logger = Mock()
    active_task = asyncio.create_task(asyncio.sleep(60))

    try:
        with patch("src.matrix.task_manager._active_letta_tasks", {("!room:test.com", "agent-1"): active_task}), patch(
            "src.matrix.task_manager.send_as_agent", new_callable=AsyncMock
        ) as mock_send:
            handled = await _dispatch_letta_task(
                room=room,
                event=event,
                config=Mock(),
                logger=logger,
                client=None,
                room_agent_id="agent-1",
                gating_result=None,
                message_text="hello",
                user_reply_to_event_id=None,
            )

        assert handled is True
        assert mock_send.await_count == 1
        call_args = mock_send.await_args
        assert "Queued" in call_args.args[1]
        assert call_args.kwargs["msgtype"] == "m.notice"
    finally:
        active_task.cancel()
        try:
            await active_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_fs_mode_returns_false_when_not_enabled():
    room = SimpleNamespace(room_id="!room:test.com", display_name="Test Room")
    event = SimpleNamespace(sender="@user:test.com", body="hello", event_id="$evt")

    with patch("src.matrix.fs_mode_handler.get_letta_code_room_state", return_value={"enabled": False}):
        handled = await _maybe_handle_fs_mode(
            room,
            event,
            config=Mock(letta_code_enabled=True),
            logger=Mock(),
            room_agent_id="agent-1",
            room_agent_name="Meridian",
        )

    assert handled is False


@pytest.mark.asyncio
async def test_fs_mode_without_agent_id_notifies_and_handles():
    room = SimpleNamespace(room_id="!room:test.com", display_name="Test Room")
    event = SimpleNamespace(sender="@user:test.com", body="hello", event_id="$evt", source={})

    with patch("src.matrix.fs_mode_handler.get_letta_code_room_state", return_value={"enabled": True}), patch(
        "src.matrix.fs_mode_handler.send_as_agent", new_callable=AsyncMock
    ) as mock_send, patch("src.models.agent_mapping.AgentMappingDB") as mock_db:
        mock_db.return_value.get_by_room_id.return_value = None
        handled = await _maybe_handle_fs_mode(
            room,
            event,
            config=Mock(letta_code_enabled=True),
            logger=Mock(),
            room_agent_id=None,
            room_agent_name="Meridian",
        )

    assert handled is True
    mock_send.assert_awaited_once()
    assert "No agent configured" in mock_send.await_args_list[0].args[1]


@pytest.mark.asyncio
async def test_fs_mode_opencode_sender_runs_letta_code_task_with_wrapped_prompt():
    room = SimpleNamespace(room_id="!room:test.com", display_name="Test Room")
    event = SimpleNamespace(
        sender="@oc_bot:test.com",
        body="do task",
        event_id="$evt",
        source={"origin_server_ts": 12345},
    )
    config = Mock(letta_code_enabled=True)

    with patch("src.matrix.fs_mode_handler.get_letta_code_room_state", return_value={"enabled": True, "projectDir": "/tmp/proj"}), patch(
        "src.matrix.fs_mode_handler.matrix_formatter.format_opencode_envelope", return_value="WRAPPED"
    ) as mock_wrap, patch("src.matrix.fs_mode_handler.run_letta_code_task", new_callable=AsyncMock) as mock_run:
        handled = await _maybe_handle_fs_mode(
            room,
            event,
            config=config,
            logger=Mock(),
            room_agent_id="agent-1",
            room_agent_name="Meridian",
        )

    assert handled is True
    mock_wrap.assert_called_once()
    assert mock_run.await_args_list[0].kwargs["prompt"] == "WRAPPED"


@pytest.mark.asyncio
async def test_fs_mode_missing_project_dir_notifies_and_handles():
    room = SimpleNamespace(room_id="!room:test.com", display_name="Test Room")
    event = SimpleNamespace(sender="@user:test.com", body="hello", event_id="$evt", source={})

    with patch("src.matrix.fs_mode_handler.get_letta_code_room_state", return_value={"enabled": True}), patch(
        "src.matrix.fs_mode_handler.resolve_letta_project_dir", new_callable=AsyncMock, return_value=None
    ), patch("src.matrix.fs_mode_handler.send_as_agent", new_callable=AsyncMock) as mock_send:
        handled = await _maybe_handle_fs_mode(
            room,
            event,
            config=Mock(letta_code_enabled=True),
            logger=Mock(),
            room_agent_id="agent-1",
            room_agent_name="Meridian",
        )

    assert handled is True
    assert "no project linked" in mock_send.await_args_list[0].args[1].lower()
