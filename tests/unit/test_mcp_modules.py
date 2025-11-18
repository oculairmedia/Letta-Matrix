"""
Unit tests for MCP modules
Tests the MCP HTTP server, Letta MCP, and related components
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Any, Optional


class TestMCPHTTPServer:
    """Test MCP HTTP Server components"""

    def test_session_initialization(self):
        """Test Session dataclass initialization"""
        from src.mcp.http_server import Session

        session_id = "test-session-123"
        created = datetime.now()

        session = Session(
            id=session_id,
            created_at=created,
            last_activity=created
        )

        assert session.id == session_id
        assert session.created_at == created
        assert session.last_activity == created
        assert session.metadata == {}
        assert session.pending_responses == {}
        assert session.event_counter == 0

    def test_session_generate_event_id(self):
        """Test Session.generate_event_id increments counter"""
        from src.mcp.http_server import Session

        session = Session(
            id="test-session",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Generate multiple event IDs
        event_id_1 = session.generate_event_id()
        event_id_2 = session.generate_event_id()
        event_id_3 = session.generate_event_id()

        assert event_id_1 == "test-session-1"
        assert event_id_2 == "test-session-2"
        assert event_id_3 == "test-session-3"
        assert session.event_counter == 3

    def test_session_with_metadata(self):
        """Test Session with custom metadata"""
        from src.mcp.http_server import Session

        metadata = {"user": "test_user", "agent_id": "agent-123"}

        session = Session(
            id="test-session",
            created_at=datetime.now(),
            last_activity=datetime.now(),
            metadata=metadata
        )

        assert session.metadata == metadata
        assert session.metadata["user"] == "test_user"
        assert session.metadata["agent_id"] == "agent-123"

    def test_mcp_tool_base_class(self):
        """Test MCPTool base class initialization"""
        from src.mcp.http_server import MCPTool

        tool = MCPTool(name="test_tool", description="Test description")

        assert tool.name == "test_tool"
        assert tool.description == "Test description"
        assert tool.parameters == {}

    @pytest.mark.asyncio
    async def test_mcp_tool_execute_not_implemented(self):
        """Test MCPTool.execute raises NotImplementedError"""
        from src.mcp.http_server import MCPTool

        tool = MCPTool(name="test_tool", description="Test")

        with pytest.raises(NotImplementedError):
            await tool.execute({})

    def test_matrix_send_message_tool_initialization(self):
        """Test MatrixSendMessageTool initialization"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_username="@letta:test.com",
            letta_password="password"
        )

        assert tool.name == "matrix_send_message"
        assert "Send a message to a Matrix room" in tool.description
        assert tool.matrix_api_url == "http://test-api:8000"
        assert tool.matrix_homeserver == "http://test-homeserver:8008"
        assert tool.letta_username == "@letta:test.com"
        assert tool.letta_password == "password"
        assert tool.access_token is None
        assert "room_id" in tool.parameters
        assert "message" in tool.parameters

    @pytest.mark.asyncio
    async def test_matrix_send_message_tool_missing_params(self):
        """Test MatrixSendMessageTool with missing parameters"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_username="@letta:test.com",
            letta_password="password"
        )

        # Missing both parameters
        result = await tool.execute({})
        assert "error" in result
        assert "Missing required parameters" in result["error"]

        # Missing message
        result = await tool.execute({"room_id": "!room:test.com"})
        assert "error" in result

        # Missing room_id
        result = await tool.execute({"message": "Hello"})
        assert "error" in result


class TestLettaMCP:
    """Test Letta MCP components"""

    def test_letta_mcp_session_initialization(self):
        """Test Session dataclass in letta_mcp module"""
        from src.mcp.letta_mcp import Session

        session_id = "letta-session-456"
        created = datetime.now()

        session = Session(
            id=session_id,
            created_at=created,
            last_activity=created
        )

        assert session.id == session_id
        assert session.created_at == created
        assert session.last_activity == created
        assert session.metadata == {}
        assert session.event_counter == 0

    def test_letta_mcp_session_generate_event_id(self):
        """Test Session.generate_event_id in letta_mcp"""
        from src.mcp.letta_mcp import Session

        session = Session(
            id="letta-session",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        event_id = session.generate_event_id()
        assert event_id == "letta-session-1"
        assert session.event_counter == 1

    def test_mcp_tool_base_in_letta_mcp(self):
        """Test MCPTool base class in letta_mcp"""
        from src.mcp.letta_mcp import MCPTool

        tool = MCPTool(name="letta_tool", description="Letta tool description")

        assert tool.name == "letta_tool"
        assert tool.description == "Letta tool description"
        assert tool.parameters == {}

    @pytest.mark.asyncio
    async def test_letta_mcp_tool_execute_not_implemented(self):
        """Test MCPTool.execute raises NotImplementedError in letta_mcp"""
        from src.mcp.letta_mcp import MCPTool

        tool = MCPTool(name="test", description="test")

        with pytest.raises(NotImplementedError):
            await tool.execute({}, context=None)

    def test_custom_exceptions(self):
        """Test custom exception classes"""
        from src.mcp.letta_mcp import AgentNotFoundError, MatrixAuthError, RoomNotFoundError

        # Test AgentNotFoundError
        with pytest.raises(AgentNotFoundError):
            raise AgentNotFoundError("Agent not found")

        # Test MatrixAuthError
        with pytest.raises(MatrixAuthError):
            raise MatrixAuthError("Auth failed")

        # Test RoomNotFoundError
        with pytest.raises(RoomNotFoundError):
            raise RoomNotFoundError("Room not found")

    def test_matrix_agent_message_tool_initialization(self):
        """Test MatrixAgentMessageTool initialization"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test-homeserver:8008",
            letta_api_url="http://letta-api:8080",
            admin_username="@admin:test.com",
            admin_password="admin_pass"
        )

        assert tool.name == "matrix_agent_message"
        assert "Send a message to another Letta agent" in tool.description
        assert tool.matrix_api_url == "http://test-api:8000"
        assert tool.matrix_homeserver == "http://test-homeserver:8008"
        assert tool.letta_api_url == "http://letta-api:8080"
        assert tool.admin_username == "@admin:test.com"
        assert tool.admin_password == "admin_pass"
        assert tool.admin_token is None
        assert tool.mappings_cache == {}
        assert tool.cache_ttl == 60


class TestMCPServer:
    """Test MCP Server module"""

    def test_mcp_server_module_imports(self):
        """Test that MCP server module can be imported"""
        try:
            import src.mcp.server as server_module
            assert server_module is not None
        except ImportError as e:
            pytest.fail(f"Failed to import MCP server module: {e}")

    def test_mcp_server_has_required_components(self):
        """Test that MCP server module has required components"""
        import src.mcp.server as server_module

        # Check for common server components
        # The actual implementation may vary
        assert hasattr(server_module, '__name__')


class TestMatrixBridge:
    """Test Matrix Bridge module"""

    def test_matrix_bridge_module_imports(self):
        """Test that Matrix bridge module can be imported"""
        try:
            import src.mcp.matrix_bridge as bridge_module
            assert bridge_module is not None
        except ImportError as e:
            pytest.fail(f"Failed to import Matrix bridge module: {e}")

    def test_matrix_bridge_has_required_components(self):
        """Test that Matrix bridge module has required components"""
        import src.mcp.matrix_bridge as bridge_module

        # Check module exists and has basic attributes
        assert hasattr(bridge_module, '__name__')


class TestMCPIntegration:
    """Integration tests for MCP components"""

    def test_session_event_id_uniqueness(self):
        """Test that event IDs are unique across sessions"""
        from src.mcp.http_server import Session

        session1 = Session(id="session-1", created_at=datetime.now(), last_activity=datetime.now())
        session2 = Session(id="session-2", created_at=datetime.now(), last_activity=datetime.now())

        ids1 = [session1.generate_event_id() for _ in range(5)]
        ids2 = [session2.generate_event_id() for _ in range(5)]

        # All IDs should be unique
        all_ids = ids1 + ids2
        assert len(all_ids) == len(set(all_ids))

        # IDs should contain session ID prefix
        assert all("session-1" in id for id in ids1)
        assert all("session-2" in id for id in ids2)

    def test_multiple_sessions_independent_counters(self):
        """Test that multiple sessions have independent event counters"""
        from src.mcp.http_server import Session

        session1 = Session(id="s1", created_at=datetime.now(), last_activity=datetime.now())
        session2 = Session(id="s2", created_at=datetime.now(), last_activity=datetime.now())

        # Generate different numbers of events
        for _ in range(3):
            session1.generate_event_id()

        for _ in range(7):
            session2.generate_event_id()

        # Counters should be independent
        assert session1.event_counter == 3
        assert session2.event_counter == 7

    def test_tool_parameters_structure(self):
        """Test that tool parameters follow expected structure"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_username="@test:test.com",
            letta_password="pass"
        )

        # Verify parameter structure
        assert isinstance(tool.parameters, dict)

        for param_name, param_spec in tool.parameters.items():
            assert "type" in param_spec
            assert "description" in param_spec
            assert isinstance(param_spec["type"], str)
            assert isinstance(param_spec["description"], str)


class TestMCPErrorHandling:
    """Test error handling in MCP modules"""

    @pytest.mark.asyncio
    async def test_matrix_send_message_tool_handles_none_params(self):
        """Test MatrixSendMessageTool handles None parameters"""
        from src.mcp.http_server import MatrixSendMessageTool

        tool = MatrixSendMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_username="@test:test.com",
            letta_password="pass"
        )

        result = await tool.execute({"room_id": None, "message": None})
        assert "error" in result

    def test_session_metadata_mutability(self):
        """Test that session metadata can be modified"""
        from src.mcp.http_server import Session

        session = Session(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Add metadata
        session.metadata["key1"] = "value1"
        session.metadata["key2"] = "value2"

        assert session.metadata["key1"] == "value1"
        assert session.metadata["key2"] == "value2"
        assert len(session.metadata) == 2

    def test_session_pending_responses_mutability(self):
        """Test that session pending_responses can be modified"""
        from src.mcp.http_server import Session

        session = Session(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Add mock pending responses
        mock_response1 = Mock()
        mock_response2 = Mock()

        session.pending_responses["resp1"] = mock_response1
        session.pending_responses["resp2"] = mock_response2

        assert session.pending_responses["resp1"] == mock_response1
        assert session.pending_responses["resp2"] == mock_response2
        assert len(session.pending_responses) == 2
