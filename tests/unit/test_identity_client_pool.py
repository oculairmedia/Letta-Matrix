import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from src.matrix.identity_client_pool import IdentityClientPool, get_identity_client_pool


@pytest.fixture
def mock_identity():
    identity = MagicMock()
    identity.id = "letta_agent-123"
    identity.mxid = "@agent:matrix.org"
    identity.access_token = "test_token"
    identity.device_id = "TEST_DEVICE"
    identity.display_name = "Test Agent"
    return identity


@pytest.fixture
def mock_identity_service(mock_identity):
    with patch('src.matrix.identity_client_pool.get_identity_service') as mock:
        service = MagicMock()
        service.get.return_value = mock_identity
        mock.return_value = service
        yield service


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
    async def test_get_client_caches_result(self, client_pool, mock_identity_service, mock_identity):
        with patch('src.matrix.identity_client_pool.AsyncClient') as mock_async_client:
            mock_client = MagicMock()
            mock_async_client.return_value = mock_client
            
            client1 = await client_pool.get_client("letta_agent-123")
            client2 = await client_pool.get_client("letta_agent-123")
            
            assert client1 is client2
            mock_async_client.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_client_returns_none_for_unknown_identity(self, client_pool, mock_identity_service):
        mock_identity_service.get.return_value = None
        
        client = await client_pool.get_client("unknown_id")
        
        assert client is None
    
    @pytest.mark.asyncio
    async def test_get_client_for_agent(self, client_pool, mock_identity_service, mock_identity):
        with patch('src.matrix.identity_client_pool.AsyncClient') as mock_async_client:
            mock_client = MagicMock()
            mock_async_client.return_value = mock_client
            
            client = await client_pool.get_client_for_agent("agent-123")
            
            mock_identity_service.get.assert_called_with("letta_agent-123")
    
    @pytest.mark.asyncio
    async def test_send_message_success(self, client_pool, mock_identity_service, mock_identity):
        with patch('src.matrix.identity_client_pool.AsyncClient') as mock_async_client:
            from nio import RoomSendResponse
            
            mock_client = MagicMock()
            mock_response = MagicMock(spec=RoomSendResponse)
            mock_response.event_id = "$test_event_id"
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_async_client.return_value = mock_client
            
            event_id = await client_pool.send_message(
                "letta_agent-123",
                "!room:matrix.org",
                "Hello world"
            )
            
            assert event_id == "$test_event_id"
    
    @pytest.mark.asyncio
    async def test_send_message_failure(self, client_pool, mock_identity_service, mock_identity):
        with patch('src.matrix.identity_client_pool.AsyncClient') as mock_async_client:
            from nio import RoomSendError
            
            mock_client = MagicMock()
            mock_response = MagicMock(spec=RoomSendError)
            mock_response.message = "Error sending"
            mock_client.room_send = AsyncMock(return_value=mock_response)
            mock_async_client.return_value = mock_client
            
            event_id = await client_pool.send_message(
                "letta_agent-123",
                "!room:matrix.org",
                "Hello world"
            )
            
            assert event_id is None
    
    @pytest.mark.asyncio
    async def test_close_client(self, client_pool, mock_identity_service, mock_identity):
        with patch('src.matrix.identity_client_pool.AsyncClient') as mock_async_client:
            mock_client = MagicMock()
            mock_client.close = AsyncMock()
            mock_async_client.return_value = mock_client
            
            await client_pool.get_client("letta_agent-123")
            assert client_pool.is_client_active("letta_agent-123")
            
            await client_pool.close_client("letta_agent-123")
            assert not client_pool.is_client_active("letta_agent-123")
    
    @pytest.mark.asyncio
    async def test_close_all(self, client_pool, mock_identity_service, mock_identity):
        with patch('src.matrix.identity_client_pool.AsyncClient') as mock_async_client:
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
