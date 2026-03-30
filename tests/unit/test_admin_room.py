import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.admin_room import (
    AdminRoomResolutionError,
    invalidate_cache,
    resolve_admin_room_id,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


def _mock_response(status: int, body: dict):
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.text = AsyncMock(return_value=str(body))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(resp):
    session = AsyncMock()
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestResolveAdminRoomId:
    @pytest.mark.asyncio
    async def test_resolves_via_alias_api(self):
        resp = _mock_response(200, {"room_id": "!resolved:server"})
        session = _mock_session(resp)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session):
            room_id = await resolve_admin_room_id(
                access_token="tok", homeserver_url="http://localhost:8008"
            )

        assert room_id == "!resolved:server"
        session.get.assert_called_once()
        call_url = session.get.call_args[0][0]
        assert "%23admins%3A" in call_url

    @pytest.mark.asyncio
    async def test_caches_result(self):
        resp = _mock_response(200, {"room_id": "!cached:server"})
        session = _mock_session(resp)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session):
            first = await resolve_admin_room_id(access_token="tok", homeserver_url="http://localhost")
            second = await resolve_admin_room_id(access_token="tok", homeserver_url="http://localhost")

        assert first == second == "!cached:server"
        assert session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_env(self):
        resp = _mock_response(404, {"errcode": "M_NOT_FOUND"})
        session = _mock_session(resp)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session), \
             patch.dict("os.environ", {"MATRIX_ADMIN_ROOM_ID": "!env-room:server"}):
            room_id = await resolve_admin_room_id(
                access_token="tok", homeserver_url="http://localhost"
            )

        assert room_id == "!env-room:server"

    @pytest.mark.asyncio
    async def test_raises_when_no_fallback(self):
        resp = _mock_response(404, {"errcode": "M_NOT_FOUND"})
        session = _mock_session(resp)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("MATRIX_ADMIN_ROOM_ID", None)
            with pytest.raises(AdminRoomResolutionError):
                await resolve_admin_room_id(
                    access_token="tok", homeserver_url="http://localhost"
                )

    @pytest.mark.asyncio
    async def test_no_token_uses_env(self):
        with patch.dict("os.environ", {"MATRIX_ADMIN_ROOM_ID": "!env-only:server"}):
            room_id = await resolve_admin_room_id(homeserver_url="http://localhost")

        assert room_id == "!env-only:server"

    @pytest.mark.asyncio
    async def test_no_token_no_env_raises(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("MATRIX_ADMIN_ROOM_ID", None)
            with pytest.raises(AdminRoomResolutionError):
                await resolve_admin_room_id(homeserver_url="http://localhost")

    @pytest.mark.asyncio
    async def test_network_error_falls_back_to_env(self):
        import aiohttp
        with patch(
            "src.core.admin_room.aiohttp.ClientSession",
            side_effect=aiohttp.ClientError("refused"),
        ), patch.dict("os.environ", {"MATRIX_ADMIN_ROOM_ID": "!fallback:server"}):
            room_id = await resolve_admin_room_id(
                access_token="tok", homeserver_url="http://localhost"
            )

        assert room_id == "!fallback:server"

    @pytest.mark.asyncio
    async def test_invalidate_cache_forces_re_resolve(self):
        resp = _mock_response(200, {"room_id": "!first:server"})
        session = _mock_session(resp)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session):
            first = await resolve_admin_room_id(access_token="tok", homeserver_url="http://localhost")

        invalidate_cache()

        resp2 = _mock_response(200, {"room_id": "!second:server"})
        session2 = _mock_session(resp2)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session2):
            second = await resolve_admin_room_id(access_token="tok", homeserver_url="http://localhost")

        assert first == "!first:server"
        assert second == "!second:server"

    @pytest.mark.asyncio
    async def test_rejects_invalid_room_id(self):
        resp = _mock_response(200, {"room_id": "not-a-room-id"})
        session = _mock_session(resp)

        with patch("src.core.admin_room.aiohttp.ClientSession", return_value=session), \
             patch.dict("os.environ", {"MATRIX_ADMIN_ROOM_ID": "!env:server"}):
            room_id = await resolve_admin_room_id(
                access_token="tok", homeserver_url="http://localhost"
            )

        assert room_id == "!env:server"
