import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import RoomSendError, RoomSendResponse

from src.matrix.identity_client_pool import IdentityClientPool, get_identity_client_pool


@pytest.fixture
def mock_identity():
    identity = MagicMock()
    identity.id = "letta_agent-123"
    identity.mxid = "@agent:matrix.org"
    identity.access_token = "test_token"
    identity.device_id = "TEST_DEVICE"
    identity.display_name = "Test Agent"
    identity.is_active = True
    return identity


@pytest.fixture
def mock_identity_service(mock_identity):
    with patch("src.matrix.identity_client_pool.get_identity_service") as mock:
        service = MagicMock()
        service.get.return_value = mock_identity
        mock.return_value = service
        yield service


@pytest.fixture
def mock_health_monitor():
    with patch(
        "src.matrix.identity_client_pool.get_identity_token_health_monitor"
    ) as mock:
        monitor = MagicMock()
        monitor.ensure_identity_healthy = AsyncMock(return_value=True)
        mock.return_value = monitor
        yield monitor


@pytest.fixture
def client_pool(mock_identity_service):
    IdentityClientPool._instance = None
    IdentityClientPool._initialized = False
    pool = IdentityClientPool("https://matrix.test.org")
    return pool


class TestIdentityClientPool:
    def test_singleton_pattern(self):
        IdentityClientPool._instance = None
        IdentityClientPool._initialized = False

        pool1 = IdentityClientPool("https://test.org")
        pool2 = IdentityClientPool("https://other.org")

        assert pool1 is pool2

    @pytest.mark.asyncio
    async def test_get_client_caches_result(
        self, client_pool, mock_identity_service, mock_identity
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_async_client.return_value = mock_client

            client1 = await client_pool.get_client("letta_agent-123")
            client2 = await client_pool.get_client("letta_agent-123")

            assert client1 is client2
            mock_async_client.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_client_returns_none_for_unknown_identity(
        self, client_pool, mock_identity_service
    ):
        mock_identity_service.get.return_value = None

        client = await client_pool.get_client("unknown_id")

        assert client is None

    @pytest.mark.asyncio
    async def test_get_client_for_agent(
        self, client_pool, mock_identity_service, mock_identity
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_async_client.return_value = mock_client

            await client_pool.get_client_for_agent("agent-123")

            mock_identity_service.get.assert_called_with("letta_agent-123")

    @pytest.mark.asyncio
    async def test_send_message_success(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_response = MagicMock(spec=RoomSendResponse)
            mock_response.event_id = "$test_event_id"
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_async_client.return_value = mock_client

            event_id = await client_pool.send_message(
                "letta_agent-123", "!room:matrix.org", "Hello world"
            )

            assert event_id == "$test_event_id"

    @pytest.mark.asyncio
    async def test_send_message_failure(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_response = MagicMock(spec=RoomSendError)
            mock_response.message = "Error sending"
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_async_client.return_value = mock_client

            event_id = await client_pool.send_message(
                "letta_agent-123", "!room:matrix.org", "Hello world"
            )

            assert event_id is None

    @pytest.mark.asyncio
    async def test_send_message_recovers_unknown_token_once(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            first_client = MagicMock()
            error_response = MagicMock(spec=RoomSendError)
            error_response.message = "M_UNKNOWN_TOKEN"
            first_client.room_send = AsyncMock(return_value=error_response)
            first_client.close = AsyncMock()

            second_client = MagicMock()
            ok_response = MagicMock(spec=RoomSendResponse)
            ok_response.event_id = "$recovered"
            second_client.room_send = AsyncMock(return_value=ok_response)
            second_client.close = AsyncMock()

            mock_async_client.side_effect = [first_client, second_client]

            event_id = await client_pool.send_message(
                "letta_agent-123", "!room:matrix.org", "Hello world"
            )

            assert event_id == "$recovered"
            mock_health_monitor.ensure_identity_healthy.assert_awaited_once_with(
                "letta_agent-123"
            )
            first_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_health_check_evicts_unhealthy_client(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_client.whoami = AsyncMock(side_effect=RuntimeError("M_UNKNOWN_TOKEN"))
            mock_client.close = AsyncMock()
            mock_async_client.return_value = mock_client

            await client_pool.get_client("letta_agent-123")
            assert client_pool.is_client_active("letta_agent-123")

            await client_pool._run_health_check()

            assert not client_pool.is_client_active("letta_agent-123")
            mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_stop_health_loop(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        wait_event = asyncio.Event()

        async def fake_health_check():
            wait_event.set()

        client_pool._run_health_check = AsyncMock(side_effect=fake_health_check)

        await client_pool.start()
        await asyncio.wait_for(wait_event.wait(), timeout=1)
        assert client_pool._health_check_task is not None

        await client_pool.stop()
        assert client_pool._health_check_task is None

    @pytest.mark.asyncio
    async def test_close_client(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_async_client.return_value = mock_client

            await client_pool.get_client("letta_agent-123")
            assert client_pool.is_client_active("letta_agent-123")

            await client_pool.close_client("letta_agent-123")
            assert not client_pool.is_client_active("letta_agent-123")

    @pytest.mark.asyncio
    async def test_close_all(
        self, client_pool, mock_identity_service, mock_identity, mock_health_monitor
    ):
        with patch("src.matrix.identity_client_pool.AsyncClient") as mock_async_client:
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_async_client.return_value = mock_client

            await client_pool.get_client("letta_agent-123")

            await client_pool.close_all()

            assert client_pool.get_active_count() == 0

    def test_get_active_count(self, client_pool):
        assert client_pool.get_active_count() == 0

    def test_is_client_active_false(self, client_pool):
        assert not client_pool.is_client_active("nonexistent")


class TestModuleFunctions:
    def test_get_identity_client_pool_singleton(self):
        IdentityClientPool._instance = None
        IdentityClientPool._initialized = False

        pool1 = get_identity_client_pool()
        pool2 = get_identity_client_pool()

        assert pool1 is pool2
