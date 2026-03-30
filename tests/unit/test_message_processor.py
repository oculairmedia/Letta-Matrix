import logging
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.matrix.config import Config, LettaApiError
from src.matrix.message_processor import (
    MessageContext,
    _handle_letta_api_error,
    process_letta_message,
)


def _make_config() -> Config:
    return Config(
        homeserver_url="http://test-server:8008",
        username="@testbot:test.com",
        password="test_password",
        room_id="!testroom:test.com",
        letta_api_url="http://test-letta:8080",
        letta_token="test_token_123",
        letta_agent_id="agent-test-001",
        log_level="INFO",
    )


def _make_logger() -> logging.Logger:
    logger = Mock(spec=logging.Logger)
    logger.info = Mock()
    logger.debug = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


def _make_ctx(
    *,
    body: str = "hello",
    sender: str = "@user:test.com",
    source=None,
    user_reply_to_event_id: str | None = None,
    silent_mode: bool = False,
    streaming: bool = False,
) -> MessageContext:
    cfg = _make_config()
    cfg.letta_streaming_enabled = streaming
    return MessageContext(
        event_body=body,
        event_sender=sender,
        event_sender_display_name="User",
        event_source=source,
        original_event_id="$evt-1",
        room_id="!room:test.com",
        room_display_name="Test Room",
        room_agent_id="agent-room-1",
        config=cfg,
        logger=_make_logger(),
        user_reply_to_event_id=user_reply_to_event_id,
        client=None,
        silent_mode=silent_mode,
        auth_manager=None,
    )


@pytest.mark.asyncio
async def test_process_letta_message_formats_inter_agent_from_metadata():
    ctx = _make_ctx(
        source={
            "origin_server_ts": 123,
            "content": {
                "m.letta.from_agent_id": "agent-42",
                "m.letta.from_agent_name": "Meridian",
            },
        }
    )

    with patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=None), \
         patch("src.matrix.message_processor.matrix_formatter.format_inter_agent_envelope", return_value="INTER_ENVELOPE") as mock_fmt, \
         patch("src.matrix.message_processor.send_to_letta_api", new_callable=AsyncMock, return_value="<no-reply/>") as mock_send:
        await process_letta_message(ctx)

    mock_fmt.assert_called_once()
    assert mock_send.await_args is not None
    assert mock_send.await_args.args[0] == "INTER_ENVELOPE"


@pytest.mark.asyncio
async def test_process_letta_message_silent_mode_suppresses_non_streaming_send():
    ctx = _make_ctx(silent_mode=True, streaming=False)

    with patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=None), \
         patch("src.matrix.message_processor.matrix_formatter.format_message_envelope", return_value="ENV"), \
         patch("src.matrix.message_processor.send_to_letta_api", new_callable=AsyncMock, return_value="response"), \
         patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock) as mock_send_as_agent:
        await process_letta_message(ctx)

    mock_send_as_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_letta_message_silent_mode_suppresses_streaming_send():
    ctx = _make_ctx(silent_mode=True, streaming=True)

    with patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=None), \
         patch("src.matrix.message_processor.matrix_formatter.format_message_envelope", return_value="ENV"), \
         patch("src.matrix.message_processor.send_to_letta_api_streaming", new_callable=AsyncMock, return_value="streamed"), \
         patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock) as mock_send_as_agent:
        await process_letta_message(ctx)

    mock_send_as_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_letta_message_passes_thread_root_to_streaming_bridge():
    ctx = _make_ctx(
        silent_mode=False,
        streaming=True,
        user_reply_to_event_id="$reply-root",
        source={
            "content": {
                "m.relates_to": {
                    "rel_type": "m.thread",
                    "event_id": "$thread-root",
                    "m.in_reply_to": {"event_id": "$reply-root"},
                }
            }
        },
    )

    with patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=None), \
         patch("src.matrix.message_processor.matrix_formatter.format_message_envelope", return_value="ENV"), \
         patch("src.matrix.message_processor.send_to_letta_api_streaming", new_callable=AsyncMock, return_value="streamed") as mock_stream:
        await process_letta_message(ctx)

    stream_await_args = mock_stream.await_args
    assert stream_await_args is not None
    assert stream_await_args.kwargs["thread_root_event_id"] == "$thread-root"
    assert stream_await_args.kwargs["reply_to_event_id"] == "$evt-1"


@pytest.mark.asyncio
async def test_process_letta_message_passes_thread_context_to_non_streaming_send():
    ctx = _make_ctx(
        silent_mode=False,
        streaming=False,
        source={
            "content": {
                "m.relates_to": {
                    "rel_type": "m.thread",
                    "event_id": "$thread-root",
                    "m.in_reply_to": {"event_id": "$reply-root"},
                }
            }
        },
    )

    with patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=None), \
         patch("src.matrix.message_processor.matrix_formatter.format_message_envelope", return_value="ENV"), \
         patch("src.matrix.message_processor.send_to_letta_api", new_callable=AsyncMock, return_value="response"), \
         patch("src.matrix.message_processor.process_agent_response", new_callable=AsyncMock, return_value=(False, "response", None)) as mock_process_agent_response, \
         patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock, return_value=True) as mock_send_as_agent:
        await process_letta_message(ctx)

    process_await_args = mock_process_agent_response.await_args
    assert process_await_args is not None
    assert process_await_args.kwargs["reply_to_event_id"] == "$evt-1"

    send_await_args = mock_send_as_agent.await_args
    assert send_await_args is not None
    assert send_await_args.kwargs["reply_to_event_id"] == "$evt-1"
    assert send_await_args.kwargs["thread_event_id"] == "$thread-root"
    assert send_await_args.kwargs["thread_latest_event_id"] == "$evt-1"


@pytest.mark.asyncio
async def test_process_letta_message_detects_inter_agent_by_sender_mapping():
    ctx = _make_ctx(source={"content": {}})
    sender_mapping = {"agent_id": "agent-abc", "agent_name": "BridgeAgent"}

    with patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=sender_mapping), \
         patch("src.matrix.message_processor.matrix_formatter.format_inter_agent_envelope", return_value="INTER_BY_SENDER") as mock_fmt, \
         patch("src.matrix.message_processor.send_to_letta_api", new_callable=AsyncMock, return_value="<no-reply/>") as mock_send:
        await process_letta_message(ctx)

    mock_fmt.assert_called_once()
    assert mock_send.await_args is not None
    assert mock_send.await_args.args[0] == "INTER_BY_SENDER"


@pytest.mark.asyncio
async def test_handle_letta_api_error_gateway_down_stashes_and_notifies():
    cfg = _make_config()
    logger = _make_logger()
    error = LettaApiError("Gateway unavailable while connecting")

    mock_buffer = Mock()
    mock_buffer.stash = AsyncMock(return_value=3)

    with patch("src.letta.message_retry_buffer.get_retry_buffer", return_value=mock_buffer), \
         patch("src.letta.message_retry_buffer.PendingMessage", side_effect=lambda **kwargs: kwargs), \
         patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock) as mock_send:
        await _handle_letta_api_error(
            error,
            room_id="!room:test.com",
            room_agent_id="agent-room-1",
            event_sender="@user:test.com",
            message_to_send="hello",
            config=cfg,
            logger=logger,
            client=None,
        )

    mock_buffer.stash.assert_awaited_once()
    assert mock_send.await_count >= 1
    queued_notice = "queued and I'll process it automatically"
    assert any(queued_notice in str(call) for call in mock_send.await_args_list)


@pytest.mark.asyncio
async def test_handle_letta_api_error_timeout_triggers_timeout_alert():
    cfg = _make_config()
    logger = _make_logger()
    error = LettaApiError("Timeout while waiting for response")

    with patch("src.matrix.alerting.alert_streaming_timeout", new_callable=AsyncMock) as mock_timeout_alert, \
         patch("src.matrix.alerting.alert_letta_error", new_callable=AsyncMock) as mock_error_alert, \
         patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock, return_value=True):
        await _handle_letta_api_error(
            error,
            room_id="!room:test.com",
            room_agent_id="agent-room-1",
            event_sender="@user:test.com",
            message_to_send="hello",
            config=cfg,
            logger=logger,
            client=None,
        )

    mock_timeout_alert.assert_awaited_once()
    mock_error_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_letta_api_error_non_timeout_triggers_generic_alert():
    cfg = _make_config()
    logger = _make_logger()
    error = LettaApiError("Invalid request payload")

    with patch("src.matrix.alerting.alert_streaming_timeout", new_callable=AsyncMock) as mock_timeout_alert, \
         patch("src.matrix.alerting.alert_letta_error", new_callable=AsyncMock) as mock_error_alert, \
         patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock, return_value=True):
        await _handle_letta_api_error(
            error,
            room_id="!room:test.com",
            room_agent_id="agent-room-1",
            event_sender="@user:test.com",
            message_to_send="hello",
            config=cfg,
            logger=logger,
            client=None,
        )

    mock_timeout_alert.assert_not_awaited()
    mock_error_alert.assert_awaited_once()
