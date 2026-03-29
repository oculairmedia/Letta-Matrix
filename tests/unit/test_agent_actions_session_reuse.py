import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.matrix.agent_actions import (
    _build_edit_content,
    _build_message_content,
    delete_message_as_agent,
    edit_message_as_agent,
    send_as_agent,
    send_as_agent_with_event_id,
    send_reaction_as_agent,
    send_read_receipt_as_agent,
)
from src.matrix.config import Config


def _make_async_cm(response: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.fixture
def config() -> Config:
    return Config(
        homeserver_url="http://matrix.test",
        username="@user:matrix.test",
        password="pass",
        room_id="!room:test",
        letta_api_url="http://letta.test",
        letta_token="token",
        letta_agent_id="agent-1",
    )


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test-agent-actions-session-reuse")


@pytest.mark.asyncio
async def test_send_as_agent_with_event_id_uses_provided_session(
    config: Config, logger: logging.Logger
) -> None:
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"event_id": "$event"})

    session = MagicMock()
    session.put = MagicMock(return_value=_make_async_cm(response))

    mapping = {
        "agent_name": "TestAgent",
        "matrix_user_id": "@agent:matrix.test",
    }

    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=mapping),
        patch("src.matrix.agent_actions.get_agent_token", new=AsyncMock(return_value="token")) as mock_token,
        patch("src.matrix.agent_actions.aiohttp.ClientSession") as mock_client_session,
    ):
        event_id = await send_as_agent_with_event_id(
            "!room:test",
            "hello",
            config,
            logger,
            session=session,
        )

    assert event_id == "$event"
    token_await_args = mock_token.await_args
    assert token_await_args is not None
    assert token_await_args.args[3] is session
    session.put.assert_called_once()
    mock_client_session.assert_not_called()


@pytest.mark.asyncio
async def test_send_as_agent_passes_provided_session_through(
    config: Config, logger: logging.Logger
) -> None:
    session = MagicMock()

    with patch(
        "src.matrix.agent_actions.send_as_agent_with_event_id",
        new=AsyncMock(return_value="$event"),
    ) as mock_send:
        sent = await send_as_agent(
            "!room:test",
            "hello",
            config,
            logger,
            session=session,
        )

    assert sent is True
    send_await_args = mock_send.await_args
    assert send_await_args is not None
    assert send_await_args.kwargs["session"] is session


@pytest.mark.asyncio
async def test_delete_message_as_agent_uses_provided_session(
    config: Config, logger: logging.Logger
) -> None:
    response = MagicMock(status=200)
    session = MagicMock()
    session.put = MagicMock(return_value=_make_async_cm(response))

    mapping = {
        "agent_name": "TestAgent",
        "matrix_user_id": "@agent:matrix.test",
    }

    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=mapping),
        patch("src.matrix.agent_actions.get_agent_token", new=AsyncMock(return_value="token")) as mock_token,
        patch("src.matrix.agent_actions.aiohttp.ClientSession") as mock_client_session,
    ):
        deleted = await delete_message_as_agent(
            "!room:test",
            "$event",
            config,
            logger,
            session=session,
        )

    assert deleted is True
    token_await_args = mock_token.await_args
    assert token_await_args is not None
    assert token_await_args.args[3] is session
    session.put.assert_called_once()
    mock_client_session.assert_not_called()


@pytest.mark.asyncio
async def test_edit_message_as_agent_uses_provided_session(
    config: Config, logger: logging.Logger
) -> None:
    response = MagicMock(status=200)
    session = MagicMock()
    session.put = MagicMock(return_value=_make_async_cm(response))

    with (
        patch("src.matrix.agent_actions.get_agent_token", new=AsyncMock(return_value="token")) as mock_token,
        patch("src.matrix.agent_actions.aiohttp.ClientSession") as mock_client_session,
    ):
        edited = await edit_message_as_agent(
            "!room:test",
            "$event",
            "updated",
            config,
            logger,
            session=session,
        )

    assert edited is True
    token_await_args = mock_token.await_args
    assert token_await_args is not None
    assert token_await_args.args[3] is session
    session.put.assert_called_once()
    mock_client_session.assert_not_called()


@pytest.mark.asyncio
async def test_send_reaction_as_agent_uses_provided_session(
    config: Config, logger: logging.Logger
) -> None:
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"event_id": "$reaction"})
    session = MagicMock()
    session.put = MagicMock(return_value=_make_async_cm(response))

    with (
        patch("src.matrix.agent_actions.get_agent_token", new=AsyncMock(return_value="token")) as mock_token,
        patch("src.matrix.agent_actions.aiohttp.ClientSession") as mock_client_session,
    ):
        reaction_event_id = await send_reaction_as_agent(
            "!room:test",
            "$event",
            "✅",
            config,
            logger,
            session=session,
        )

    assert reaction_event_id == "$reaction"
    token_await_args = mock_token.await_args
    assert token_await_args is not None
    assert token_await_args.args[3] is session
    session.put.assert_called_once()
    mock_client_session.assert_not_called()


@pytest.mark.asyncio
async def test_send_read_receipt_as_agent_uses_provided_session(
    config: Config, logger: logging.Logger
) -> None:
    response = MagicMock(status=200)
    session = MagicMock()
    session.post = MagicMock(return_value=_make_async_cm(response))

    with (
        patch("src.matrix.agent_actions.get_agent_token", new=AsyncMock(return_value="token")) as mock_token,
        patch("src.matrix.agent_actions.aiohttp.ClientSession") as mock_client_session,
    ):
        sent = await send_read_receipt_as_agent(
            "!room:test",
            "$event",
            config,
            logger,
            session=session,
        )

    assert sent is True
    token_await_args = mock_token.await_args
    assert token_await_args is not None
    assert token_await_args.args[3] is session
    session.post.assert_called_once()
    mock_client_session.assert_not_called()


def test_build_message_content_does_not_swallow_keyboard_interrupt() -> None:
    with patch(
        "src.matrix.agent_actions.extract_and_convert_pills",
        side_effect=KeyboardInterrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            _build_message_content("hi @Meridian")


def test_build_edit_content_does_not_swallow_keyboard_interrupt() -> None:
    with patch(
        "src.matrix.agent_actions.extract_and_convert_pills",
        side_effect=KeyboardInterrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            _build_edit_content("$evt", "hi @Meridian")
