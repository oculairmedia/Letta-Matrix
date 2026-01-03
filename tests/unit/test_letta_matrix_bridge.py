import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.fixture
def mock_identity():
    identity = Mock()
    identity.id = "letta_agent-123"
    identity.mxid = "@agent_123:matrix.test"
    identity.is_active = True
    identity.access_token = "test_token"
    return identity


@pytest.fixture
def mock_identity_service(mock_identity):
    service = Mock()
    service.get_by_agent_id = Mock(return_value=mock_identity)
    service.mark_used = Mock()
    return service


@pytest.fixture
def mock_client_pool():
    pool = Mock()
    pool.send_as_agent = AsyncMock(return_value="$event123")
    return pool


class TestLettaMatrixBridge:
    
    @pytest.mark.asyncio
    async def test_post_agent_response_uses_identity(self, mock_identity_service, mock_client_pool):
        with patch('src.bridges.letta_matrix_bridge.get_identity_service', return_value=mock_identity_service), \
             patch('src.bridges.letta_matrix_bridge.get_identity_client_pool', return_value=mock_client_pool):
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            
            event_id = await bridge.post_agent_response_as_identity(
                agent_id="agent-123",
                room_id="!room:matrix.test",
                content="Hello from agent"
            )
            
            assert event_id == "$event123"
            mock_client_pool.send_as_agent.assert_called_once_with(
                "agent-123", "!room:matrix.test", "Hello from agent"
            )
            mock_identity_service.mark_used.assert_called_once_with("letta_agent-123")
    
    @pytest.mark.asyncio
    async def test_post_agent_response_falls_back_when_no_identity(self, mock_client_pool):
        mock_service = Mock()
        mock_service.get_by_agent_id = Mock(return_value=None)
        
        with patch('src.bridges.letta_matrix_bridge.get_identity_service', return_value=mock_service), \
             patch('src.bridges.letta_matrix_bridge.get_identity_client_pool', return_value=mock_client_pool):
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            bridge._send_matrix_message = AsyncMock(return_value="$admin_event")
            
            event_id = await bridge.post_agent_response_as_identity(
                agent_id="agent-456",
                room_id="!room:matrix.test",
                content="Fallback message"
            )
            
            assert event_id is None
            bridge._send_matrix_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_post_user_message_as_admin(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        bridge._send_matrix_message = AsyncMock()
        
        await bridge.post_user_message_as_admin(
            room_id="!room:matrix.test",
            user_message="What is the weather?",
            source="external"
        )
        
        bridge._send_matrix_message.assert_called_once()
        call_kwargs = bridge._send_matrix_message.call_args.kwargs
        assert call_kwargs["msgtype"] == "m.text"
        assert call_kwargs["body"] == "What is the weather?"
    
    @pytest.mark.asyncio
    async def test_post_webhook_response_posts_both_messages(self, mock_identity_service, mock_client_pool):
        with patch('src.bridges.letta_matrix_bridge.get_identity_service', return_value=mock_identity_service), \
             patch('src.bridges.letta_matrix_bridge.get_identity_client_pool', return_value=mock_client_pool):
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            bridge.find_matrix_room_for_agent = AsyncMock(return_value="!room:matrix.test")
            bridge._send_matrix_message = AsyncMock()
            
            result = await bridge.post_webhook_response(
                agent_id="agent-123",
                user_content="Hello?",
                assistant_content="Hi there!",
                run_id="run-abc"
            )
            
            assert result.success is True
            assert result.response_posted is True
            assert result.room_id == "!room:matrix.test"
            
            bridge._send_matrix_message.assert_called_once()
            mock_client_pool.send_as_agent.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_post_webhook_response_no_room_found(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        bridge.find_matrix_room_for_agent = AsyncMock(return_value=None)
        
        result = await bridge.post_webhook_response(
            agent_id="agent-unknown",
            user_content="Hello?",
            assistant_content="Hi!"
        )
        
        assert result.success is False
        assert result.error == "no_matrix_room"
    
    @pytest.mark.asyncio
    async def test_post_webhook_response_without_user_content(self, mock_identity_service, mock_client_pool):
        with patch('src.bridges.letta_matrix_bridge.get_identity_service', return_value=mock_identity_service), \
             patch('src.bridges.letta_matrix_bridge.get_identity_client_pool', return_value=mock_client_pool):
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            bridge.find_matrix_room_for_agent = AsyncMock(return_value="!room:matrix.test")
            bridge._send_matrix_message = AsyncMock()
            
            result = await bridge.post_webhook_response(
                agent_id="agent-123",
                user_content=None,
                assistant_content="Just responding"
            )
            
            assert result.success is True
            bridge._send_matrix_message.assert_not_called()
            mock_client_pool.send_as_agent.assert_called_once()


class TestBridgeOriginatedMarker:
    
    @pytest.mark.asyncio
    async def test_send_matrix_message_includes_bridge_marker(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        bridge._access_token = "test_token"
        
        captured_content = {}
        
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"event_id": "$test123"})
        
        mock_put_context = AsyncMock()
        mock_put_context.__aenter__.return_value = mock_resp
        mock_put_context.__aexit__.return_value = None
        
        def capture_put(*args, **kwargs):
            captured_content.update(kwargs.get("json", {}))
            return mock_put_context
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session.put = capture_put
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session
            
            await bridge._send_matrix_message(
                room_id="!room:matrix.test",
                body="Test message"
            )
            
            assert captured_content.get("m.bridge_originated") is True
    
    @pytest.mark.asyncio
    async def test_bridge_marker_prevents_loop(self):
        from unittest.mock import MagicMock
        
        event = MagicMock()
        event.sender = "@user:matrix.test"
        event.body = "Test message"
        event.source = {
            "content": {
                "msgtype": "m.text",
                "body": "Test message",
                "m.bridge_originated": True
            }
        }
        
        content = event.source.get("content", {})
        assert content.get("m.bridge_originated") is True
