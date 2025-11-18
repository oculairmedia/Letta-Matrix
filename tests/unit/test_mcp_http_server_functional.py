"""
Functional tests for MCP HTTP Server
Tests HTTP endpoints, session management, and tool execution
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop


class TestMatrixSendMessageToolExecution:
    """Test MatrixSendMessageTool execution paths"""

    @pytest.mark.asyncio
    async def test_execute_with_valid_params_needs_login(self):
        """Test execute when access token needs to be obtained"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_username="@letta:test.com",
            letta_password="password"
        )

        # Mock _login to return success
        mock_login = AsyncMock(return_value={"access_token": "test_token_123"})
        tool._login = mock_login

        # Mock HTTP request for sending message
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"event_id": "$event123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await tool.execute({
                "room_id": "!test:room.com",
                "message": "Hello, world!"
            })

            # Should have called login
            mock_login.assert_called_once()
            # Should have set access token
            assert tool.access_token == "test_token_123"
            # Should have attempted to send message
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_with_existing_token(self):
        """Test execute when access token already exists"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_username="@letta:test.com",
            letta_password="password"
        )

        # Set existing token
        tool.access_token = "existing_token"

        # Mock HTTP request for sending message
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"event_id": "$event123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await tool.execute({
                "room_id": "!test:room.com",
                "message": "Test message"
            })

            # Should NOT have called login since token exists
            # Should have attempted to send message
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_login_failure(self):
        """Test execute when login fails"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_username="@letta:test.com",
            letta_password="password"
        )

        # Mock _login to return error
        mock_login = AsyncMock(return_value={"error": "Login failed"})
        tool._login = mock_login

        result = await tool.execute({
            "room_id": "!test:room.com",
            "message": "Test message"
        })

        # Should return the error from login
        assert "error" in result
        assert result["error"] == "Login failed"

    @pytest.mark.asyncio
    async def test_execute_with_empty_string_params(self):
        """Test execute with empty string parameters"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_username="@letta:test.com",
            letta_password="password"
        )

        # Test with empty room_id
        result = await tool.execute({
            "room_id": "",
            "message": "Test"
        })
        assert "error" in result

        # Test with empty message
        result = await tool.execute({
            "room_id": "!room:test.com",
            "message": ""
        })
        assert "error" in result


class TestSessionManagement:
    """Test session management functionality"""

    def test_session_last_activity_updates(self):
        """Test that session last_activity can be updated"""
        from src.mcp.http_server import Session

        session = Session(
            id="test-session",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        original_activity = session.last_activity

        # Simulate updating last activity
        import time
        time.sleep(0.01)  # Small delay
        session.last_activity = datetime.now()

        assert session.last_activity > original_activity

    def test_session_metadata_operations(self):
        """Test session metadata can store and retrieve data"""
        from src.mcp.http_server import Session

        session = Session(
            id="test-session",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Add various metadata
        session.metadata["user_id"] = "@user:test.com"
        session.metadata["agent_id"] = "agent-123"
        session.metadata["preferences"] = {"theme": "dark"}

        assert session.metadata["user_id"] == "@user:test.com"
        assert session.metadata["agent_id"] == "agent-123"
        assert session.metadata["preferences"]["theme"] == "dark"

        # Update metadata
        session.metadata["preferences"]["theme"] = "light"
        assert session.metadata["preferences"]["theme"] == "light"

    def test_session_event_counter_increment(self):
        """Test event counter increments correctly"""
        from src.mcp.http_server import Session

        session = Session(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        assert session.event_counter == 0

        # Generate events
        for i in range(10):
            event_id = session.generate_event_id()
            assert session.event_counter == i + 1
            assert event_id == f"test-{i + 1}"

    def test_session_pending_responses_add_remove(self):
        """Test adding and removing pending responses"""
        from src.mcp.http_server import Session

        session = Session(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Add pending responses
        response1 = Mock()
        response2 = Mock()

        session.pending_responses["req1"] = response1
        session.pending_responses["req2"] = response2

        assert len(session.pending_responses) == 2
        assert session.pending_responses["req1"] == response1

        # Remove a response
        del session.pending_responses["req1"]
        assert len(session.pending_responses) == 1
        assert "req1" not in session.pending_responses


class TestMCPToolParameterValidation:
    """Test tool parameter validation"""

    def test_matrix_send_message_tool_parameters_structure(self):
        """Test MatrixSendMessageTool has correct parameter structure"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_username="@test:test.com",
            letta_password="pass"
        )

        # Verify all required parameters exist
        assert "room_id" in tool.parameters
        assert "message" in tool.parameters

        # Verify parameter metadata
        room_id_param = tool.parameters["room_id"]
        assert "type" in room_id_param
        assert "description" in room_id_param
        assert room_id_param["type"] == "string"

        message_param = tool.parameters["message"]
        assert "type" in message_param
        assert "description" in message_param
        assert message_param["type"] == "string"

    def test_tool_name_and_description(self):
        """Test tool has proper name and description"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_username="@test:test.com",
            letta_password="pass"
        )

        assert tool.name == "matrix_send_message"
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0


class TestMCPToolErrorHandling:
    """Test error handling in MCP tools"""

    @pytest.mark.asyncio
    async def test_execute_with_partial_params(self):
        """Test execute handles partial parameters gracefully"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_username="@test:test.com",
            letta_password="pass"
        )

        # Only room_id provided
        result = await tool.execute({"room_id": "!room:test.com"})
        assert "error" in result

        # Only message provided
        result = await tool.execute({"message": "Hello"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_execute_with_extra_params(self):
        """Test execute ignores extra parameters"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_username="@test:test.com",
            letta_password="pass"
        )

        tool.access_token = "token123"

        # Mock HTTP request
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"event_id": "$event"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Extra params should be ignored
            result = await tool.execute({
                "room_id": "!room:test.com",
                "message": "Test",
                "extra_param": "ignored",
                "another": 123
            })

            # Should still succeed
            mock_post.assert_called_once()


class TestSessionState:
    """Test session state management"""

    def test_session_initialization_defaults(self):
        """Test session initializes with correct defaults"""
        from src.mcp.http_server import Session

        now = datetime.now()
        session = Session(
            id="test-123",
            created_at=now,
            last_activity=now
        )

        assert session.id == "test-123"
        assert session.created_at == now
        assert session.last_activity == now
        assert session.metadata == {}
        assert session.pending_responses == {}
        assert session.event_counter == 0

    def test_session_with_custom_metadata(self):
        """Test session with pre-populated metadata"""
        from src.mcp.http_server import Session

        metadata = {
            "user": "test_user",
            "role": "admin",
            "permissions": ["read", "write"]
        }

        session = Session(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now(),
            metadata=metadata
        )

        assert session.metadata == metadata
        assert session.metadata["user"] == "test_user"
        assert "write" in session.metadata["permissions"]

    def test_session_event_id_format(self):
        """Test event ID follows expected format"""
        from src.mcp.http_server import Session

        session = Session(
            id="session-abc-123",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        event_id = session.generate_event_id()

        # Should be in format: {session_id}-{counter}
        assert event_id.startswith("session-abc-123-")
        assert event_id == "session-abc-123-1"

    def test_multiple_sessions_independent(self):
        """Test multiple sessions maintain independent state"""
        from src.mcp.http_server import Session

        session1 = Session(id="s1", created_at=datetime.now(), last_activity=datetime.now())
        session2 = Session(id="s2", created_at=datetime.now(), last_activity=datetime.now())

        # Generate events on session1
        session1.generate_event_id()
        session1.generate_event_id()

        # Generate events on session2
        session2.generate_event_id()

        # Counters should be independent
        assert session1.event_counter == 2
        assert session2.event_counter == 1

        # Add metadata to session1
        session1.metadata["key"] = "value1"

        # Session2 metadata should be empty
        assert session2.metadata == {}
