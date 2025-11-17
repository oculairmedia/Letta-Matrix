"""
Functional tests for Letta MCP module
Tests agent messaging, tool execution, and MCP integration
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime


class TestMatrixAgentMessageToolParams:
    """Test MatrixAgentMessageTool parameter handling"""

    def test_tool_parameters_definition(self):
        """Test tool defines correct parameters"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test-api:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="admin_pass"
        )

        # Tool should have parameters attribute
        assert hasattr(tool, 'parameters')

    def test_tool_initialization_values(self):
        """Test tool initializes with correct values"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://api:8000",
            matrix_homeserver="http://hs:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass123"
        )

        assert tool.matrix_api_url == "http://api:8000"
        assert tool.matrix_homeserver == "http://hs:8008"
        assert tool.letta_api_url == "http://letta:8080"
        assert tool.admin_username == "@admin:test.com"
        assert tool.admin_password == "pass123"
        assert tool.admin_token is None
        assert tool.mappings_cache == {}
        assert tool.cache_ttl == 60


class TestLettaMCPSession:
    """Test Letta MCP session management"""

    def test_session_initialization(self):
        """Test Letta MCP Session initialization"""
        from src.mcp.letta_mcp import Session

        session = Session(
            id="letta-session-1",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        assert session.id == "letta-session-1"
        assert isinstance(session.created_at, datetime)
        assert isinstance(session.last_activity, datetime)
        assert session.metadata == {}
        assert session.event_counter == 0

    def test_session_event_generation(self):
        """Test event ID generation in Letta MCP session"""
        from src.mcp.letta_mcp import Session

        session = Session(
            id="letta-test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Generate multiple events
        events = [session.generate_event_id() for _ in range(5)]

        # All events should be unique
        assert len(set(events)) == 5

        # Events should follow pattern
        assert all(e.startswith("letta-test-") for e in events)

        # Counter should be at 5
        assert session.event_counter == 5

    def test_session_metadata_storage(self):
        """Test session can store metadata"""
        from src.mcp.letta_mcp import Session

        session = Session(
            id="test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Store agent information
        session.metadata["agent_id"] = "agent-123"
        session.metadata["user_id"] = "@user:test.com"
        session.metadata["capabilities"] = ["send", "receive"]

        assert session.metadata["agent_id"] == "agent-123"
        assert "send" in session.metadata["capabilities"]


class TestCustomExceptions:
    """Test custom exception classes in Letta MCP"""

    def test_agent_not_found_error(self):
        """Test AgentNotFoundError exception"""
        from src.mcp.letta_mcp import AgentNotFoundError

        error_msg = "Agent agent-123 not found"

        with pytest.raises(AgentNotFoundError) as exc_info:
            raise AgentNotFoundError(error_msg)

        assert str(exc_info.value) == error_msg

    def test_matrix_auth_error(self):
        """Test MatrixAuthError exception"""
        from src.mcp.letta_mcp import MatrixAuthError

        error_msg = "Authentication failed for @user:test.com"

        with pytest.raises(MatrixAuthError) as exc_info:
            raise MatrixAuthError(error_msg)

        assert str(exc_info.value) == error_msg

    def test_room_not_found_error(self):
        """Test RoomNotFoundError exception"""
        from src.mcp.letta_mcp import RoomNotFoundError

        error_msg = "Room !abc:test.com not found"

        with pytest.raises(RoomNotFoundError) as exc_info:
            raise RoomNotFoundError(error_msg)

        assert str(exc_info.value) == error_msg

    def test_exceptions_are_subclass_of_exception(self):
        """Test that custom exceptions are proper Exception subclasses"""
        from src.mcp.letta_mcp import AgentNotFoundError, MatrixAuthError, RoomNotFoundError

        assert issubclass(AgentNotFoundError, Exception)
        assert issubclass(MatrixAuthError, Exception)
        assert issubclass(RoomNotFoundError, Exception)


class TestMCPToolBase:
    """Test MCPTool base class in Letta MCP"""

    def test_tool_base_initialization(self):
        """Test MCPTool base class initializes correctly"""
        from src.mcp.letta_mcp import MCPTool

        tool = MCPTool(
            name="test_tool",
            description="A test tool for validation"
        )

        assert tool.name == "test_tool"
        assert tool.description == "A test tool for validation"
        assert tool.parameters == {}

    @pytest.mark.asyncio
    async def test_tool_base_execute_not_implemented(self):
        """Test MCPTool base execute method raises NotImplementedError"""
        from src.mcp.letta_mcp import MCPTool

        tool = MCPTool(name="test", description="test")

        with pytest.raises(NotImplementedError):
            await tool.execute({}, context=None)

    @pytest.mark.asyncio
    async def test_tool_execute_with_context(self):
        """Test tool execute can accept context parameter"""
        from src.mcp.letta_mcp import MCPTool

        tool = MCPTool(name="test", description="test")

        # Should raise NotImplementedError but accept context param
        with pytest.raises(NotImplementedError):
            await tool.execute({"param": "value"}, context={"session": "123"})


class TestMatrixAgentMessageToolCaching:
    """Test caching behavior in MatrixAgentMessageTool"""

    def test_mappings_cache_initialization(self):
        """Test mappings cache initializes empty"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        assert tool.mappings_cache == {}
        assert isinstance(tool.mappings_cache, dict)

    def test_cache_ttl_default_value(self):
        """Test cache TTL has correct default"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        assert tool.cache_ttl == 60

    def test_mappings_cache_can_store_data(self):
        """Test mappings cache can store and retrieve data"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        # Store mappings
        tool.mappings_cache["agent-1"] = {
            "matrix_user_id": "@agent1:test.com",
            "room_id": "!room1:test.com"
        }

        tool.mappings_cache["agent-2"] = {
            "matrix_user_id": "@agent2:test.com",
            "room_id": "!room2:test.com"
        }

        # Retrieve mappings
        assert tool.mappings_cache["agent-1"]["matrix_user_id"] == "@agent1:test.com"
        assert tool.mappings_cache["agent-2"]["room_id"] == "!room2:test.com"
        assert len(tool.mappings_cache) == 2


class TestMatrixAgentMessageToolTokenManagement:
    """Test token management in MatrixAgentMessageTool"""

    def test_admin_token_initialization(self):
        """Test admin token initializes as None"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        assert tool.admin_token is None

    def test_admin_token_can_be_set(self):
        """Test admin token can be set and retrieved"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        # Set token
        tool.admin_token = "test_admin_token_123"

        # Verify it was set
        assert tool.admin_token == "test_admin_token_123"


class TestMatrixAgentMessageToolConfiguration:
    """Test configuration handling in MatrixAgentMessageTool"""

    def test_tool_stores_configuration(self):
        """Test tool stores all configuration values"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        api_url = "http://api.example.com:8000"
        hs_url = "http://matrix.example.com:8008"
        letta_url = "http://letta.example.com:8080"
        admin_user = "@admin:example.com"
        admin_pwd = "secure_password_123"

        tool = MatrixAgentMessageTool(
            matrix_api_url=api_url,
            matrix_homeserver=hs_url,
            letta_api_url=letta_url,
            admin_username=admin_user,
            admin_password=admin_pwd
        )

        assert tool.matrix_api_url == api_url
        assert tool.matrix_homeserver == hs_url
        assert tool.letta_api_url == letta_url
        assert tool.admin_username == admin_user
        assert tool.admin_password == admin_pwd

    def test_tool_name_and_description(self):
        """Test tool has correct name and description"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool

        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        assert tool.name == "matrix_agent_message"
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0
        assert "Letta agent" in tool.description or "agent" in tool.description.lower()


class TestLettaMCPSessionsMultiple:
    """Test multiple session management"""

    def test_multiple_sessions_with_different_ids(self):
        """Test creating multiple sessions with different IDs"""
        from src.mcp.letta_mcp import Session

        session1 = Session(id="s1", created_at=datetime.now(), last_activity=datetime.now())
        session2 = Session(id="s2", created_at=datetime.now(), last_activity=datetime.now())
        session3 = Session(id="s3", created_at=datetime.now(), last_activity=datetime.now())

        assert session1.id != session2.id
        assert session2.id != session3.id
        assert session1.id != session3.id

    def test_sessions_have_independent_event_counters(self):
        """Test sessions maintain independent event counters"""
        from src.mcp.letta_mcp import Session

        sessions = [
            Session(id=f"session-{i}", created_at=datetime.now(), last_activity=datetime.now())
            for i in range(5)
        ]

        # Generate different numbers of events for each
        for i, session in enumerate(sessions):
            for _ in range(i + 1):
                session.generate_event_id()

        # Verify independent counters
        for i, session in enumerate(sessions):
            assert session.event_counter == i + 1

    def test_sessions_have_independent_metadata(self):
        """Test sessions maintain independent metadata"""
        from src.mcp.letta_mcp import Session

        session1 = Session(id="s1", created_at=datetime.now(), last_activity=datetime.now())
        session2 = Session(id="s2", created_at=datetime.now(), last_activity=datetime.now())

        session1.metadata["agent"] = "agent-1"
        session2.metadata["agent"] = "agent-2"

        assert session1.metadata["agent"] == "agent-1"
        assert session2.metadata["agent"] == "agent-2"
        assert session1.metadata != session2.metadata


class TestLettaMCPIntegration:
    """Integration tests for Letta MCP components"""

    def test_tool_and_session_integration(self):
        """Test tool and session work together"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool, Session

        # Create tool
        tool = MatrixAgentMessageTool(
            matrix_api_url="http://test:8000",
            matrix_homeserver="http://test:8008",
            letta_api_url="http://letta:8080",
            admin_username="@admin:test.com",
            admin_password="pass"
        )

        # Create session
        session = Session(
            id="integration-test",
            created_at=datetime.now(),
            last_activity=datetime.now()
        )

        # Store tool reference in session metadata
        session.metadata["tool_name"] = tool.name

        # Verify integration
        assert session.metadata["tool_name"] == "matrix_agent_message"

    def test_multiple_tools_and_sessions(self):
        """Test multiple tools and sessions can coexist"""
        from src.mcp.letta_mcp import MatrixAgentMessageTool, Session, MCPTool

        # Create multiple tools
        tool1 = MatrixAgentMessageTool(
            matrix_api_url="http://api1:8000",
            matrix_homeserver="http://hs1:8008",
            letta_api_url="http://letta1:8080",
            admin_username="@admin1:test.com",
            admin_password="pass1"
        )

        tool2 = MCPTool(name="custom_tool", description="Custom tool")

        # Create multiple sessions
        session1 = Session(id="s1", created_at=datetime.now(), last_activity=datetime.now())
        session2 = Session(id="s2", created_at=datetime.now(), last_activity=datetime.now())

        # Associate sessions with tools
        session1.metadata["tool"] = tool1.name
        session2.metadata["tool"] = tool2.name

        # Verify independence
        assert session1.metadata["tool"] != session2.metadata["tool"]
        assert tool1.name == "matrix_agent_message"
        assert tool2.name == "custom_tool"
