import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.matrix.presence_manager import (
    PresenceManager,
    PresenceState,
    _url_encode,
    get_presence_manager,
    notify_agent_busy,
    notify_agent_ready,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    PresenceManager.reset()
    import src.matrix.presence_manager as pm
    pm._manager = None
    yield
    PresenceManager.reset()
    pm._manager = None


def _mock_response(status: int = 200, body: str = "{}"):
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(resp):
    session = AsyncMock()
    session.put = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestPresenceState:
    def test_enum_values(self):
        assert PresenceState.ONLINE.value == "online"
        assert PresenceState.UNAVAILABLE.value == "unavailable"
        assert PresenceState.OFFLINE.value == "offline"

    def test_string_enum(self):
        assert str(PresenceState.ONLINE) == "PresenceState.ONLINE"
        assert PresenceState.ONLINE == "online"


class TestUrlEncode:
    def test_encodes_at_and_colon(self):
        assert _url_encode("@user:server.com") == "%40user%3Aserver.com"

    def test_no_special_chars(self):
        assert _url_encode("plaintext") == "plaintext"


class TestPresenceManagerSingleton:
    def test_singleton(self):
        a = PresenceManager("http://localhost")
        b = PresenceManager("http://other")
        assert a is b

    def test_reset(self):
        a = PresenceManager("http://localhost")
        PresenceManager.reset()
        b = PresenceManager("http://other")
        assert a is not b

    def test_get_presence_manager(self):
        mgr = get_presence_manager("http://localhost")
        assert mgr is get_presence_manager()


class TestSetPresence:
    @pytest.mark.asyncio
    async def test_set_presence_success(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            ok = await mgr.set_presence(
                "@bot:server", "token123", PresenceState.ONLINE, "Ready"
            )

        assert ok is True
        assert mgr.get_current_state("@bot:server") == PresenceState.ONLINE
        session.put.assert_called_once()
        call_kwargs = session.put.call_args
        assert "%40bot%3Aserver" in call_kwargs[0][0]
        assert call_kwargs[1]["json"] == {"presence": "online", "status_msg": "Ready"}

    @pytest.mark.asyncio
    async def test_set_presence_failure(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(403, '{"errcode":"M_FORBIDDEN"}')
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            ok = await mgr.set_presence(
                "@bot:server", "token123", PresenceState.ONLINE
            )

        assert ok is False
        assert mgr.get_current_state("@bot:server") is None

    @pytest.mark.asyncio
    async def test_rate_limiting_skips_duplicate(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            await mgr.set_presence("@bot:server", "tok", PresenceState.ONLINE)
            ok = await mgr.set_presence("@bot:server", "tok", PresenceState.ONLINE)

        assert ok is True
        assert session.put.call_count == 1

    @pytest.mark.asyncio
    async def test_state_change_bypasses_rate_limit(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            await mgr.set_presence("@bot:server", "tok", PresenceState.ONLINE)
            await mgr.set_presence("@bot:server", "tok", PresenceState.UNAVAILABLE)

        assert session.put.call_count == 2
        assert mgr.get_current_state("@bot:server") == PresenceState.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_force_bypasses_rate_limit(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            await mgr.set_presence("@bot:server", "tok", PresenceState.ONLINE)
            await mgr.set_presence("@bot:server", "tok", PresenceState.ONLINE, force=True)

        assert session.put.call_count == 2

    @pytest.mark.asyncio
    async def test_no_status_msg(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            await mgr.set_presence("@bot:server", "tok", PresenceState.OFFLINE)

        call_kwargs = session.put.call_args
        assert call_kwargs[1]["json"] == {"presence": "offline"}

    @pytest.mark.asyncio
    async def test_network_error(self):
        mgr = PresenceManager("http://localhost:8008")

        import aiohttp
        with patch(
            "src.matrix.presence_manager.aiohttp.ClientSession",
            side_effect=aiohttp.ClientError("connection refused"),
        ):
            ok = await mgr.set_presence("@bot:server", "tok", PresenceState.ONLINE)

        assert ok is False


class TestAgentHelpers:
    @pytest.mark.asyncio
    async def test_set_agent_busy(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            ok = await mgr.set_agent_busy("@bot:s", "tok")

        assert ok is True
        assert mgr.get_current_state("@bot:s") == PresenceState.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_set_agent_ready(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            ok = await mgr.set_agent_ready("@bot:s", "tok")

        assert ok is True
        assert mgr.get_current_state("@bot:s") == PresenceState.ONLINE


class TestBulkOperations:
    @pytest.mark.asyncio
    async def test_set_all_online(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        identities = {"@a:s": "tok_a", "@b:s": "tok_b", "@c:s": "tok_c"}
        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            count = await mgr.set_all_online(identities)

        assert count == 3

    @pytest.mark.asyncio
    async def test_set_all_offline(self):
        mgr = PresenceManager("http://localhost:8008")
        resp = _mock_response(200)
        session = _mock_session(resp)

        identities = {"@a:s": "tok_a", "@b:s": "tok_b"}
        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session):
            count = await mgr.set_all_offline(identities)

        assert count == 2

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        mgr = PresenceManager("http://localhost:8008")
        ok_resp = _mock_response(200)
        fail_resp = _mock_response(500, "error")

        call_count = 0

        def _alternating_session():
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                return _mock_session(fail_resp)
            return _mock_session(ok_resp)

        identities = {"@a:s": "tok_a", "@b:s": "tok_b"}
        with patch("src.matrix.presence_manager.aiohttp.ClientSession", side_effect=_alternating_session):
            count = await mgr.set_all_online(identities, "Ready")

        assert count < len(identities)


class TestNotifyHelpers:
    @pytest.mark.asyncio
    async def test_notify_agent_busy_resolves_identity(self):
        mock_identity = MagicMock()
        mock_identity.mxid = "@agent_test:server"
        mock_identity.access_token = "secret_token"

        mock_svc = MagicMock()
        mock_svc.get.return_value = mock_identity

        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session), \
             patch("src.core.identity_storage.get_identity_service", return_value=mock_svc):
            await notify_agent_busy("test-agent-id")

        mock_svc.get.assert_called_once_with("letta_test-agent-id")
        mgr = get_presence_manager()
        assert mgr.get_current_state("@agent_test:server") == PresenceState.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_notify_agent_ready_resolves_identity(self):
        mock_identity = MagicMock()
        mock_identity.mxid = "@agent_test:server"
        mock_identity.access_token = "secret_token"

        mock_svc = MagicMock()
        mock_svc.get.return_value = mock_identity

        resp = _mock_response(200)
        session = _mock_session(resp)

        with patch("src.matrix.presence_manager.aiohttp.ClientSession", return_value=session), \
             patch("src.core.identity_storage.get_identity_service", return_value=mock_svc):
            await notify_agent_ready("test-agent-id")

        mgr = get_presence_manager()
        assert mgr.get_current_state("@agent_test:server") == PresenceState.ONLINE

    @pytest.mark.asyncio
    async def test_notify_agent_busy_missing_identity(self):
        mock_svc = MagicMock()
        mock_svc.get.return_value = None

        with patch("src.core.identity_storage.get_identity_service", return_value=mock_svc):
            await notify_agent_busy("nonexistent-agent")

    @pytest.mark.asyncio
    async def test_notify_agent_busy_import_error(self):
        with patch(
            "src.core.identity_storage.get_identity_service",
            side_effect=ImportError("no module"),
        ):
            await notify_agent_busy("any-agent")


class TestMessageProcessorIntegration:
    @pytest.mark.asyncio
    async def test_process_letta_message_sets_presence(self):
        from src.matrix.message_processor import process_letta_message, MessageContext

        busy_calls = []
        ready_calls = []

        async def mock_busy(agent_id, status_msg="Thinking..."):
            busy_calls.append(agent_id)

        async def mock_ready(agent_id, status_msg="Ready"):
            ready_calls.append(agent_id)

        ctx = MessageContext(
            event_body="hello",
            event_sender="@user:server",
            event_sender_display_name="User",
            event_source={"content": {"body": "hello"}, "origin_server_ts": 1234},
            original_event_id="$evt1",
            room_id="!room:server",
            room_display_name="Test Room",
            room_agent_id="agent-123",
            config=MagicMock(
                letta_agent_id="default-agent",
                letta_api_url="http://localhost:8283",
                letta_token="tok",
                letta_streaming_enabled=False,
                homeserver_url="http://localhost:8008",
                temporal_message_delivery=False,
            ),
            logger=MagicMock(),
            client=None,
            silent_mode=False,
            auth_manager=None,
        )

        with patch("src.matrix.message_processor.notify_agent_busy", mock_busy), \
             patch("src.matrix.message_processor.notify_agent_ready", mock_ready), \
             patch("src.matrix.message_processor.send_read_receipt_as_agent", new_callable=AsyncMock), \
             patch("src.matrix.message_processor.send_to_letta_api", new_callable=AsyncMock, return_value="Hi!"), \
             patch("src.matrix.message_processor.send_as_agent", new_callable=AsyncMock, return_value=True), \
             patch("src.core.mapping_service.get_mapping_by_matrix_user", return_value=None):
            await process_letta_message(ctx)
            await asyncio.sleep(0.05)

        assert "agent-123" in busy_calls
        assert "agent-123" in ready_calls
