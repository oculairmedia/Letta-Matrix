import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes._identity_helpers import (
    _provision_login,
    _send_admin_password_reset_command,
    _reset_password_and_verify_login,
)


@pytest.fixture(autouse=True)
def _admin_room_env(monkeypatch):
    monkeypatch.setenv("MATRIX_ADMIN_ROOM_ID", "!test-admin-room:matrix.test")
    from src.core.admin_room import invalidate_cache
    invalidate_cache()
    yield
    invalidate_cache()


def _make_async_cm(response: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provision_login_retries_then_succeeds():
    fail_response = MagicMock(status=403)
    fail_response.__aenter__ = AsyncMock(return_value=fail_response)
    fail_response.__aexit__ = AsyncMock(return_value=None)

    ok_response = MagicMock(status=200)
    ok_response.json = AsyncMock(return_value={"access_token": "tok_123"})
    ok_response.__aenter__ = AsyncMock(return_value=ok_response)
    ok_response.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.post = MagicMock(side_effect=[_make_async_cm(fail_response), _make_async_cm(ok_response)])
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch("src.api.routes._identity_helpers.aiohttp.ClientSession", return_value=session):
        token = await _provision_login("https://matrix.test", "agent_test", "pass", retries=2)

    assert token == "tok_123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_send_admin_reset_clears_token_cache_on_401_then_recovers():
    user_manager = MagicMock()
    user_manager.get_admin_token = AsyncMock(side_effect=["stale", "fresh"])

    unauthorized = MagicMock(status=401)
    unauthorized.__aenter__ = AsyncMock(return_value=unauthorized)
    unauthorized.__aexit__ = AsyncMock(return_value=None)

    authorized = MagicMock(status=200)
    authorized.__aenter__ = AsyncMock(return_value=authorized)
    authorized.__aexit__ = AsyncMock(return_value=None)

    session = MagicMock()
    session.put = MagicMock(side_effect=[_make_async_cm(unauthorized), _make_async_cm(authorized)])
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    with patch("src.api.routes._identity_helpers.aiohttp.ClientSession", return_value=session):
        ok = await _send_admin_password_reset_command(
            user_manager,
            "https://matrix.test",
            "agent_test",
            "pass",
        )

    assert ok is True
    assert user_manager.clear_admin_token_cache.call_count >= 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reset_password_and_verify_login_retries_three_times():
    user_manager = MagicMock()

    with (
        patch("src.api.routes._identity_helpers._send_admin_password_reset_command", new=AsyncMock(return_value=True)) as reset_cmd,
        patch("src.api.routes._identity_helpers._provision_login", new=AsyncMock(side_effect=[None, None, "token_final"])) as login_fn,
        patch("src.api.routes._identity_helpers.asyncio.sleep", new=AsyncMock(return_value=None)),
    ):
        token = await _reset_password_and_verify_login(
            user_manager,
            "https://matrix.test",
            "agent_test",
            "pass",
            max_attempts=3,
        )

    assert token == "token_final"
    assert reset_cmd.await_count == 3
    assert login_fn.await_count == 3
