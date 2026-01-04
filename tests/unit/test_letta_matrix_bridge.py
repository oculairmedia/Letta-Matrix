import pytest
from unittest.mock import AsyncMock, Mock, MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestLettaMatrixBridge:
    
    @pytest.mark.asyncio
    async def test_post_as_agent_uses_identity(self):
        with patch('src.bridges.letta_matrix_bridge.send_as_agent', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "$event123"
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            
            event_id = await bridge.post_as_agent(
                agent_id="agent-123",
                room_id="!room:matrix.test",
                content="Hello from agent"
            )
            
            assert event_id == "$event123"
            mock_send.assert_called_once_with("agent-123", "!room:matrix.test", "Hello from agent")
    
    @pytest.mark.asyncio
    async def test_post_as_agent_falls_back_when_identity_fails(self):
        with patch('src.bridges.letta_matrix_bridge.send_as_agent', new_callable=AsyncMock) as mock_send:
            mock_send.return_value = None
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            bridge._send_matrix_message = AsyncMock()
            
            event_id = await bridge.post_as_agent(
                agent_id="agent-456",
                room_id="!room:matrix.test",
                content="Fallback message"
            )
            
            assert event_id is None
            bridge._send_matrix_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_post_as_agent_skips_identity_when_disabled(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        bridge.config.use_identity_posting = False
        bridge._send_matrix_message = AsyncMock()
        
        event_id = await bridge.post_as_agent(
            agent_id="agent-789",
            room_id="!room:matrix.test",
            content="Direct message"
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
    async def test_post_webhook_response_posts_messages(self):
        with patch('src.bridges.letta_matrix_bridge.send_as_agent', new_callable=AsyncMock) as mock_agent, \
             patch('src.bridges.letta_matrix_bridge.send_as_user', new_callable=AsyncMock) as mock_user, \
             patch('src.letta.webhook_handler.get_opencode_sender', return_value="@admin:matrix.test"):
            
            mock_agent.return_value = "$agent_event"
            mock_user.return_value = "$user_event"
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            bridge.find_matrix_room_for_agent = AsyncMock(return_value="!room:matrix.test")
            
            result = await bridge.post_webhook_response(
                agent_id="agent-123",
                user_content="Hello?",
                assistant_content="Hi there!",
                run_id="run-abc"
            )
            
            assert result.success is True
            assert result.response_posted is True
            assert result.room_id == "!room:matrix.test"
            
            mock_user.assert_called_once()
            mock_agent.assert_called_once()
    
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
    async def test_post_webhook_response_without_user_content(self):
        with patch('src.bridges.letta_matrix_bridge.send_as_agent', new_callable=AsyncMock) as mock_agent, \
             patch('src.bridges.letta_matrix_bridge.send_as_user', new_callable=AsyncMock) as mock_user, \
             patch('src.letta.webhook_handler.get_opencode_sender', return_value=None):
            
            mock_agent.return_value = "$agent_event"
            
            from src.bridges.letta_matrix_bridge import LettaMatrixBridge
            bridge = LettaMatrixBridge()
            bridge.find_matrix_room_for_agent = AsyncMock(return_value="!room:matrix.test")
            
            result = await bridge.post_webhook_response(
                agent_id="agent-123",
                user_content=None,
                assistant_content="Just responding"
            )
            
            assert result.success is True
            mock_user.assert_not_called()
            mock_agent.assert_called_once()


class TestBridgeOriginatedMarker:
    
    @pytest.mark.asyncio
    async def test_send_matrix_message_includes_bridge_marker(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        bridge._access_token = "test_token"
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$test123"})
            
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            
            mock_session_instance = MagicMock()
            mock_session_instance.put = MagicMock(return_value=mock_ctx)
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock(return_value=None)
            
            mock_session.return_value = mock_session_instance
            
            await bridge._send_matrix_message(
                room_id="!room:matrix.test",
                body="Test message",
                msgtype="m.text"
            )
            
            mock_session_instance.put.assert_called_once()
            call_args = mock_session_instance.put.call_args
            json_data = call_args.kwargs.get('json', {})
            assert json_data.get('m.bridge_originated') is True


class TestFindMatrixRoom:
    
    @pytest.mark.asyncio
    async def test_find_room_from_api(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "room_id": "!found_room:matrix.test"
            })
            
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_ctx)
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock(return_value=None)
            
            mock_session.return_value = mock_session_instance
            
            room_id = await bridge.find_matrix_room_for_agent("agent-123")
            
            assert room_id == "!found_room:matrix.test"
    
    @pytest.mark.asyncio
    async def test_find_room_returns_none_on_404(self):
        from src.bridges.letta_matrix_bridge import LettaMatrixBridge
        bridge = LettaMatrixBridge()
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 404
            
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_ctx)
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock(return_value=None)
            
            mock_session.return_value = mock_session_instance
            
            room_id = await bridge.find_matrix_room_for_agent("unknown-agent")
            
            assert room_id is None
