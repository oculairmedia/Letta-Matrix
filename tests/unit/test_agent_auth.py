import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Iterator

import pytest

from src.matrix import agent_auth
from src.matrix.config import Config


def _make_async_cm(response: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.fixture(autouse=True)
def _reset_repair_attempts(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    agent_auth._repair_last_attempt.clear()
    agent_auth._token_cache.clear()
    monkeypatch.setenv("MATRIX_ADMIN_ROOM_ID", "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca")
    from src.core.admin_room import invalidate_cache
    invalidate_cache()
    yield
    agent_auth._repair_last_attempt.clear()
    agent_auth._token_cache.clear()
    invalidate_cache()


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
    return logging.getLogger("test-agent-auth")


@pytest.mark.asyncio
async def test_get_agent_token_returns_none_without_mapping(config: Config, logger: logging.Logger) -> None:
    session = MagicMock()
    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=None),
        patch("src.core.mapping_service.get_portal_link_by_room_id", return_value=None),
    ):
        token = await agent_auth.get_agent_token("!room:test", config, logger, session)

    assert token is None
    session.post.assert_not_called()


@pytest.mark.asyncio
async def test_get_agent_token_returns_cached_token_on_second_call_if_cache_exists(
    config: Config, logger: logging.Logger
) -> None:
    mapping = {
        "agent_id": "agent-1",
        "matrix_user_id": "@agent_1:matrix.test",
        "matrix_password": "pass",
    }
    agent_auth._token_cache.clear()

    login_response = MagicMock(status=200)
    login_response.json = AsyncMock(return_value={"access_token": "token-1"})
    session = MagicMock()
    session.post = MagicMock(return_value=_make_async_cm(login_response))

    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=mapping),
        patch("src.core.mapping_service.get_portal_link_by_room_id", return_value=None),
        patch("src.core.mapping_service.get_mapping_by_agent_id", return_value=None),
    ):
        first = await agent_auth.get_agent_token("!room:test", config, logger, session)
        second = await agent_auth.get_agent_token("!room:test", config, logger, session)

    assert first == "token-1"
    assert second == "token-1"
    assert session.post.call_count == 1


@pytest.mark.asyncio
async def test_get_agent_token_reauthenticates_after_cache_expiry(
    config: Config, logger: logging.Logger
) -> None:
    mapping = {
        "agent_id": "agent-1",
        "matrix_user_id": "@agent_1:matrix.test",
        "matrix_password": "pass",
    }

    first_response = MagicMock(status=200)
    first_response.json = AsyncMock(return_value={"access_token": "token-1"})
    second_response = MagicMock(status=200)
    second_response.json = AsyncMock(return_value={"access_token": "token-2"})

    session = MagicMock()
    session.post = MagicMock(
        side_effect=[
            _make_async_cm(first_response),
            _make_async_cm(second_response),
        ]
    )

    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=mapping),
        patch("src.core.mapping_service.get_portal_link_by_room_id", return_value=None),
        patch("src.core.mapping_service.get_mapping_by_agent_id", return_value=None),
        patch("src.matrix.agent_auth.time.monotonic", side_effect=[100.0, 1901.0, 1901.0]),
    ):
        first = await agent_auth.get_agent_token("!room:test", config, logger, session)
        second = await agent_auth.get_agent_token("!room:test", config, logger, session)

    assert first == "token-1"
    assert second == "token-2"
    assert session.post.call_count == 2


@pytest.mark.asyncio
async def test_get_agent_token_returns_token_on_successful_login(
    config: Config, logger: logging.Logger
) -> None:
    mapping = {
        "agent_id": "agent-1",
        "matrix_user_id": "@agent_1:matrix.test",
        "matrix_password": "pass",
    }
    login_response = MagicMock(status=200)
    login_response.json = AsyncMock(return_value={"access_token": "token-abc"})

    session = MagicMock()
    session.post = MagicMock(return_value=_make_async_cm(login_response))

    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=mapping),
        patch("src.core.mapping_service.get_portal_link_by_room_id", return_value=None),
        patch("src.core.mapping_service.get_mapping_by_agent_id", return_value=None),
    ):
        token = await agent_auth.get_agent_token("!room:test", config, logger, session)

    assert token == "token-abc"


@pytest.mark.asyncio
async def test_get_agent_token_does_not_cache_failed_login(
    config: Config, logger: logging.Logger
) -> None:
    mapping = {
        "agent_id": "agent-1",
        "matrix_user_id": "@agent_1:matrix.test",
        "matrix_password": "pass",
    }
    login_response = MagicMock(status=403)
    login_response.text = AsyncMock(return_value='{"errcode":"M_FORBIDDEN"}')

    session = MagicMock()
    session.post = MagicMock(return_value=_make_async_cm(login_response))

    with (
        patch("src.core.mapping_service.get_mapping_by_room_id", return_value=mapping),
        patch("src.core.mapping_service.get_portal_link_by_room_id", return_value=None),
        patch("src.core.mapping_service.get_mapping_by_agent_id", return_value=None),
        patch("src.matrix.agent_auth.repair_agent_password", new=AsyncMock(return_value=None)),
    ):
        first = await agent_auth.get_agent_token("!room:test", config, logger, session)
        second = await agent_auth.get_agent_token("!room:test", config, logger, session)

    assert first is None
    assert second is None
    assert session.post.call_count == 2
    assert agent_auth._token_cache == {}


@pytest.mark.asyncio
async def test_repair_agent_password_sends_admin_room_command(
    config: Config, logger: logging.Logger
) -> None:
    mapping = {
        "agent_id": "agent-cmd",
        "agent_name": "Agent Cmd",
        "matrix_user_id": "@agent_cmd:matrix.test",
        "matrix_password": "old-pass",
    }

    admin_login_response = MagicMock(status=200)
    admin_login_response.json = AsyncMock(return_value={"access_token": "admin-token"})

    cmd_response = MagicMock(status=200)
    cmd_response.text = AsyncMock(return_value="")

    messages_response = MagicMock(status=200)
    messages_response.json = AsyncMock(
        return_value={
            "chunk": [{"content": {"body": "Successfully reset password"}}],
        }
    )

    http_session = MagicMock()
    http_session.post = AsyncMock(return_value=admin_login_response)
    http_session.put = MagicMock(return_value=_make_async_cm(cmd_response))
    http_session.get = MagicMock(return_value=_make_async_cm(messages_response))
    http_session.__aenter__ = AsyncMock(return_value=http_session)
    http_session.__aexit__ = AsyncMock(return_value=None)

    db_record = SimpleNamespace(
        agent_id="agent-cmd",
        agent_name="Agent Cmd",
        matrix_user_id="@agent_cmd:matrix.test",
        room_id="!room:test",
    )
    db_instance = MagicMock()
    db_instance.get_by_agent_id.return_value = db_record

    with patch("src.matrix.agent_auth.asyncio.sleep", new=AsyncMock(return_value=None)):
        sync_password = AsyncMock(return_value=True)
        new_password = await agent_auth.repair_agent_password(
            mapping, config, logger, _session_factory=lambda: http_session,
            _db_factory=lambda: db_instance,
            _invalidate_fn=lambda: None,
            _sync_password_fn=sync_password,
            _cooldown_override=0,
        )

    assert isinstance(new_password, str)
    assert new_password.startswith("AgentRepair_")
    assert http_session.put.call_count == 1

    put_call = http_session.put.call_args
    assert "rooms/!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca/send/m.room.message/" in put_call.args[0]
    assert put_call.kwargs["json"]["body"].startswith("!admin users reset-password agent_cmd ")


@pytest.mark.asyncio
async def test_repair_agent_password_respects_cooldown(
    config: Config, logger: logging.Logger
) -> None:
    mapping = {
        "agent_id": "agent-cooldown",
        "agent_name": "Agent Cooldown",
        "matrix_user_id": "@agent_cooldown:matrix.test",
        "matrix_password": "old-pass",
    }

    admin_login_response = MagicMock(status=200)
    admin_login_response.json = AsyncMock(return_value={"access_token": "admin-token"})
    cmd_response = MagicMock(status=500)
    cmd_response.text = AsyncMock(return_value="error")

    http_session = MagicMock()
    http_session.post = AsyncMock(return_value=admin_login_response)
    http_session.put = MagicMock(return_value=_make_async_cm(cmd_response))
    http_session.get = MagicMock(return_value=_make_async_cm(MagicMock(status=500)))
    http_session.__aenter__ = AsyncMock(return_value=http_session)
    http_session.__aexit__ = AsyncMock(return_value=None)

    mock_time = MagicMock(side_effect=[1000.0] * 20)
    with patch("src.matrix.agent_auth.time.monotonic", mock_time):
        first = await agent_auth.repair_agent_password(
            mapping, config, logger, _session_factory=lambda: http_session,
        )
        second = await agent_auth.repair_agent_password(
            mapping, config, logger, _session_factory=lambda: http_session,
        )

    assert first is None
    assert second is None
    assert http_session.post.call_count == 1


@pytest.mark.asyncio
async def test_repair_confirmation_correlates_to_agent_username(
    config: Config, logger: logging.Logger
) -> None:
    """Test that password repair correlates confirmation to the specific agent username"""
    mapping = {
        "agent_id": "agent-correlate",
        "agent_name": "Agent Correlate",
        "matrix_user_id": "@agent_correlate:matrix.test",
        "matrix_password": "old-pass",
    }

    admin_login_response = MagicMock(status=200)
    admin_login_response.json = AsyncMock(return_value={"access_token": "admin-token"})

    cmd_response = MagicMock(status=200)
    cmd_response.text = AsyncMock(return_value="")

    # Mock messages response with success message for agent_1
    messages_response = MagicMock(status=200)
    messages_response.json = AsyncMock(
        return_value={
            "chunk": [
                {"content": {"body": "Successfully reset the password for user agent_correlate"}}
            ],
        }
    )

    http_session = MagicMock()
    http_session.post = AsyncMock(return_value=admin_login_response)
    http_session.put = MagicMock(return_value=_make_async_cm(cmd_response))
    http_session.get = MagicMock(return_value=_make_async_cm(messages_response))
    http_session.__aenter__ = AsyncMock(return_value=http_session)
    http_session.__aexit__ = AsyncMock(return_value=None)

    db_record = SimpleNamespace(
        agent_id="agent-correlate",
        agent_name="Agent Correlate",
        matrix_user_id="@agent_correlate:matrix.test",
        room_id="!room:test",
    )
    db_instance = MagicMock()
    db_instance.get_by_agent_id.return_value = db_record

    with patch("src.matrix.agent_auth.asyncio.sleep", new=AsyncMock(return_value=None)):
        sync_password = AsyncMock(return_value=True)
        new_password = await agent_auth.repair_agent_password(
            mapping, config, logger, _session_factory=lambda: http_session,
            _db_factory=lambda: db_instance,
            _invalidate_fn=lambda: None,
            _sync_password_fn=sync_password,
            _cooldown_override=0,
        )

    assert isinstance(new_password, str)
    assert new_password.startswith("AgentRepair_")


@pytest.mark.asyncio
async def test_repair_ignores_other_agent_confirmation(
    config: Config, logger: logging.Logger
) -> None:
    """Test that repair ignores success messages for other agents but persists password optimistically"""
    mapping = {
        "agent_id": "agent-ignore",
        "agent_name": "Agent Ignore",
        "matrix_user_id": "@agent_ignore:matrix.test",
        "matrix_password": "old-pass",
    }

    admin_login_response = MagicMock(status=200)
    admin_login_response.json = AsyncMock(return_value={"access_token": "admin-token"})

    cmd_response = MagicMock(status=200)
    cmd_response.text = AsyncMock(return_value="")

    # Mock messages response with success message for agent_2 (different agent)
    messages_response = MagicMock(status=200)
    messages_response.json = AsyncMock(
        return_value={
            "chunk": [
                {"content": {"body": "Successfully reset the password for user agent_2"}}
            ],
        }
    )

    http_session = MagicMock()
    http_session.post = AsyncMock(return_value=admin_login_response)
    http_session.put = MagicMock(return_value=_make_async_cm(cmd_response))
    http_session.get = MagicMock(return_value=_make_async_cm(messages_response))
    http_session.__aenter__ = AsyncMock(return_value=http_session)
    http_session.__aexit__ = AsyncMock(return_value=None)

    db_record = SimpleNamespace(
        agent_id="agent-ignore",
        agent_name="Agent Ignore",
        matrix_user_id="@agent_ignore:matrix.test",
        room_id="!room:test",
    )
    db_instance = MagicMock()
    db_instance.get_by_agent_id.return_value = db_record

    with patch("src.matrix.agent_auth.asyncio.sleep", new=AsyncMock(return_value=None)):
        sync_password = AsyncMock(return_value=True)
        new_password = await agent_auth.repair_agent_password(
            mapping, config, logger, _session_factory=lambda: http_session,
            _db_factory=lambda: db_instance,
            _invalidate_fn=lambda: None,
            _sync_password_fn=sync_password,
            _cooldown_override=0,
        )

    # Should still return password (persisted optimistically) even without correlated confirmation
    assert isinstance(new_password, str)
    assert new_password.startswith("AgentRepair_")


@pytest.mark.asyncio
async def test_repair_expanded_polling_window(
    config: Config, logger: logging.Logger
) -> None:
    """Test that repair uses expanded polling window (limit=10 instead of limit=2)"""
    mapping = {
        "agent_id": "agent-poll",
        "agent_name": "Agent Poll",
        "matrix_user_id": "@agent_poll:matrix.test",
        "matrix_password": "old-pass",
    }

    admin_login_response = MagicMock(status=200)
    admin_login_response.json = AsyncMock(return_value={"access_token": "admin-token"})

    cmd_response = MagicMock(status=200)
    cmd_response.text = AsyncMock(return_value="")

    messages_response = MagicMock(status=200)
    messages_response.json = AsyncMock(
        return_value={
            "chunk": [
                {"content": {"body": "Successfully reset the password for user agent_poll"}}
            ],
        }
    )

    http_session = MagicMock()
    http_session.post = AsyncMock(return_value=admin_login_response)
    http_session.put = MagicMock(return_value=_make_async_cm(cmd_response))
    http_session.get = MagicMock(return_value=_make_async_cm(messages_response))
    http_session.__aenter__ = AsyncMock(return_value=http_session)
    http_session.__aexit__ = AsyncMock(return_value=None)

    db_record = SimpleNamespace(
        agent_id="agent-poll",
        agent_name="Agent Poll",
        matrix_user_id="@agent_poll:matrix.test",
        room_id="!room:test",
    )
    db_instance = MagicMock()
    db_instance.get_by_agent_id.return_value = db_record

    with patch("src.matrix.agent_auth.asyncio.sleep", new=AsyncMock(return_value=None)):
        sync_password = AsyncMock(return_value=True)
        new_password = await agent_auth.repair_agent_password(
            mapping, config, logger, _session_factory=lambda: http_session,
            _db_factory=lambda: db_instance,
            _invalidate_fn=lambda: None,
            _sync_password_fn=sync_password,
            _cooldown_override=0,
        )

    # Verify the messages URL contains limit=10
    get_call = http_session.get.call_args
    assert get_call is not None
    messages_url = get_call.args[0]
    assert "limit=10" in messages_url
    assert isinstance(new_password, str)
