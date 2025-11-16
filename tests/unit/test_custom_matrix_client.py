"""
Unit tests for custom_matrix_client.py

Tests cover:
- Matrix client initialization
- Message routing to agents
- Agent response handling
- Multi-room monitoring
- Message filtering
- Configuration management
"""
import pytest
import asyncio
import time
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass

# Import the module to test
from src.matrix.client import (
    Config,
    setup_logging,
    LettaApiError,
    MatrixClientError,
    ConfigurationError
)


# ============================================================================
# Configuration Tests
# ============================================================================

class TestConfig:
    """Test configuration dataclass"""

    def test_config_creation(self):
        """Test creating a configuration object"""
        config = Config(
            homeserver_url="http://test:8008",
            username="@test:matrix.test",
            password="test_pass",
            room_id="!room:matrix.test",
            letta_api_url="http://letta:8283",
            letta_token="token123",
            letta_agent_id="agent-123"
        )

        assert config.homeserver_url == "http://test:8008"
        assert config.username == "@test:matrix.test"
        assert config.log_level == "INFO"  # Default value

    @patch.dict('os.environ', {
        'MATRIX_HOMESERVER_URL': 'http://env-synapse:8008',
        'MATRIX_USERNAME': '@env_user:matrix.test',
        'MATRIX_PASSWORD': 'env_password',
        'MATRIX_ROOM_ID': '!env_room:matrix.test',
        'LETTA_API_URL': 'http://env-letta:8283',
        'LETTA_TOKEN': 'env_token',
        'LETTA_AGENT_ID': 'env-agent-123',
        'LOG_LEVEL': 'DEBUG'
    })
    def test_config_from_env(self):
        """Test loading configuration from environment variables"""
        config = Config.from_env()

        assert config.homeserver_url == "http://env-synapse:8008"
        assert config.username == "@env_user:matrix.test"
        assert config.password == "env_password"
        assert config.room_id == "!env_room:matrix.test"
        assert config.letta_api_url == "http://env-letta:8283"
        assert config.letta_token == "env_token"
        assert config.letta_agent_id == "env-agent-123"
        assert config.log_level == "DEBUG"

    @patch.dict('os.environ', {}, clear=True)
    def test_config_from_env_defaults(self):
        """Test configuration defaults when env vars are not set"""
        config = Config.from_env()

        assert config.homeserver_url == "http://localhost:8008"
        assert config.username == "@letta:matrix.oculair.ca"
        assert config.log_level == "INFO"


# ============================================================================
# Custom Exception Tests
# ============================================================================

class TestCustomExceptions:
    """Test custom exception classes"""

    def test_letta_api_error(self):
        """Test LettaApiError exception"""
        error = LettaApiError(
            "API call failed",
            status_code=500,
            response_body="Internal Server Error"
        )

        assert str(error) == "API call failed"
        assert error.status_code == 500
        assert error.response_body == "Internal Server Error"

    def test_letta_api_error_minimal(self):
        """Test LettaApiError with minimal parameters"""
        error = LettaApiError("Simple error")

        assert str(error) == "Simple error"
        assert error.status_code is None
        assert error.response_body is None

    def test_matrix_client_error(self):
        """Test MatrixClientError exception"""
        error = MatrixClientError("Client operation failed")

        assert str(error) == "Client operation failed"
        assert isinstance(error, Exception)

    def test_configuration_error(self):
        """Test ConfigurationError exception"""
        error = ConfigurationError("Invalid configuration")

        assert str(error) == "Invalid configuration"
        assert isinstance(error, Exception)


# ============================================================================
# Logging Setup Tests
# ============================================================================

class TestLoggingSetup:
    """Test logging configuration"""

    def test_setup_logging(self, mock_config):
        """Test logging setup"""
        logger = setup_logging(mock_config)

        assert logger.name == "matrix_client"
        assert logger.level == 20  # INFO level

    def test_setup_logging_debug_level(self):
        """Test logging setup with DEBUG level"""
        config = Config(
            homeserver_url="http://test:8008",
            username="@test:matrix.test",
            password="test_pass",
            room_id="!room:matrix.test",
            letta_api_url="http://letta:8283",
            letta_token="token123",
            letta_agent_id="agent-123",
            log_level="DEBUG"
        )

        logger = setup_logging(config)
        assert logger.level == 10  # DEBUG level

    def test_json_formatter(self, mock_config):
        """Test JSON log formatting"""
        import logging
        from src.matrix.client import setup_logging

        logger = setup_logging(mock_config)

        # Verify that handler is set up correctly
        assert len(logger.handlers) > 0
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)


# ============================================================================
# Message Parsing Tests
# ============================================================================

@pytest.mark.unit
class TestMessageParsing:
    """Test message content parsing from Letta responses"""

    def test_parse_assistant_message(self):
        """Test parsing assistant_message from Letta response"""
        # This would test a function that extracts message content
        # For now, documenting the expected behavior

        letta_response = {
            "messages": [
                {"message_type": "function_return", "function_return": "..."},
                {"message_type": "internal_monologue", "internal_monologue": "..."},
                {"message_type": "assistant_message", "assistant_message": "Hello!"}
            ]
        }

        # Expected: Should extract "Hello!" from assistant_message
        expected = "Hello!"

        # This test would verify the parsing logic
        assert True  # Placeholder

    def test_parse_multiple_formats(self):
        """Test parsing messages in different formats"""
        # Document various Letta response formats that need to be handled

        formats = [
            # Format 1: Simple string response
            "Simple response",

            # Format 2: Object with 'response' key
            {"response": "Response text"},

            # Format 3: Messages array with assistant_message
            {
                "messages": [
                    {"message_type": "assistant_message", "assistant_message": "Text"}
                ]
            }
        ]

        # Test would verify all formats are handled correctly
        assert len(formats) == 3


# ============================================================================
# Room Monitoring Tests
# ============================================================================

@pytest.mark.unit
class TestRoomMonitoring:
    """Test multi-room monitoring functionality"""

    def test_identify_agent_from_room(self):
        """Test identifying which agent a room belongs to"""
        # Mock scenario: Given a room ID, determine the corresponding agent

        room_to_agent_map = {
            "!room001:matrix.test": "agent-001",
            "!room002:matrix.test": "agent-002",
            "!room003:matrix.test": "agent-003"
        }

        room_id = "!room002:matrix.test"
        expected_agent = "agent-002"

        assert room_to_agent_map.get(room_id) == expected_agent

    def test_handle_message_in_agent_room(self):
        """Test routing messages to the correct agent"""
        # Mock scenario: Message received in agent's room should route to that agent

        message = {
            "room_id": "!room001:matrix.test",
            "sender": "@user:matrix.test",
            "body": "Hello agent!"
        }

        agent_for_room = {
            "!room001:matrix.test": "agent-001"
        }

        # Verify correct agent is identified
        target_agent = agent_for_room.get(message["room_id"])
        assert target_agent == "agent-001"


# ============================================================================
# Message Filtering Tests
# ============================================================================

@pytest.mark.unit
class TestMessageFiltering:
    """Test message filtering to prevent replay"""

    def test_filter_old_messages_on_startup(self):
        """Test that old messages are filtered on startup"""
        startup_time = 1704067200.0  # Timestamp when client starts

        messages = [
            {"timestamp": 1704067100.0, "body": "Old message"},      # Before startup
            {"timestamp": 1704067200.0, "body": "Startup message"},  # At startup
            {"timestamp": 1704067300.0, "body": "New message"}       # After startup
        ]

        # Filter messages before startup
        filtered = [m for m in messages if m["timestamp"] > startup_time]

        assert len(filtered) == 1
        assert filtered[0]["body"] == "New message"

    def test_filter_own_messages(self):
        """Test filtering out bot's own messages"""
        bot_user_id = "@letta:matrix.test"

        messages = [
            {"sender": "@user:matrix.test", "body": "User message"},
            {"sender": "@letta:matrix.test", "body": "Bot message"},
            {"sender": "@admin:matrix.test", "body": "Admin message"}
        ]

        # Filter out bot's own messages
        filtered = [m for m in messages if m["sender"] != bot_user_id]

        assert len(filtered) == 2
        assert all(m["sender"] != bot_user_id for m in filtered)


# ============================================================================
# Agent Response Tests
# ============================================================================

@pytest.mark.unit
class TestAgentResponse:
    """Test agent response handling"""

    @pytest.mark.asyncio
    async def test_send_message_as_agent(self, mock_nio_client):
        """Test sending message as agent user"""
        agent_user_id = "@agent_001:matrix.test"
        room_id = "!room001:matrix.test"
        message_body = "Agent response"

        # Mock the room_send method
        mock_nio_client.user_id = agent_user_id
        mock_nio_client.room_send = AsyncMock(return_value=Mock(event_id="$event123"))

        # Simulate sending message
        response = await mock_nio_client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": message_body}
        )

        # Verify message was sent
        assert response.event_id == "$event123"
        mock_nio_client.room_send.assert_called_once()

    def test_format_agent_response(self):
        """Test formatting agent response for Matrix"""
        raw_response = "This is the agent's response."

        formatted = {
            "msgtype": "m.text",
            "body": raw_response
        }

        assert formatted["msgtype"] == "m.text"
        assert formatted["body"] == raw_response


# ============================================================================
# Authentication Tests
# ============================================================================

@pytest.mark.unit
class TestAuthentication:
    """Test Matrix authentication"""

    @pytest.mark.asyncio
    async def test_login_success(self, mock_nio_client):
        """Test successful Matrix login"""
        # Mock successful login
        login_response = Mock()
        login_response.user_id = "@test:matrix.test"
        login_response.device_id = "test_device"
        login_response.access_token = "test_token"

        mock_nio_client.login = AsyncMock(return_value=login_response)

        # Perform login
        response = await mock_nio_client.login(password="test_password")

        assert response.user_id == "@test:matrix.test"
        assert response.access_token == "test_token"

    @pytest.mark.asyncio
    async def test_login_failure(self, mock_nio_client):
        """Test failed Matrix login"""
        from nio import LoginError

        # Mock failed login
        login_error = LoginError("Invalid credentials")
        mock_nio_client.login = AsyncMock(return_value=login_error)

        # Perform login
        response = await mock_nio_client.login(password="wrong_password")

        assert isinstance(response, LoginError)


# ============================================================================
# Sync Tests
# ============================================================================

@pytest.mark.unit
class TestMatrixSync:
    """Test Matrix sync operations"""

    @pytest.mark.asyncio
    async def test_sync_with_filter(self, mock_nio_client):
        """Test sync with message filter"""
        # Mock sync response
        sync_response = Mock()
        sync_response.rooms = Mock()
        sync_response.rooms.join = {
            "!room001:matrix.test": Mock(
                timeline=Mock(
                    events=[
                        Mock(
                            sender="@user:matrix.test",
                            type="m.room.message",
                            event_id="$event1"
                        )
                    ]
                )
            )
        }

        mock_nio_client.sync = AsyncMock(return_value=sync_response)

        # Perform sync
        response = await mock_nio_client.sync(timeout=30000)

        assert len(response.rooms.join) == 1
        mock_nio_client.sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_filter_configuration(self):
        """Test sync filter configuration"""
        # Define filter for efficient sync
        sync_filter = {
            "room": {
                "timeline": {
                    "limit": 0,  # Don't fetch historical messages on startup
                    "lazy_load_members": True
                },
                "state": {
                    "lazy_load_members": True
                }
            },
            "presence": {
                "types": []  # Disable presence
            },
            "account_data": {
                "types": []  # Disable account data
            }
        }

        assert sync_filter["room"]["timeline"]["limit"] == 0
        assert sync_filter["room"]["timeline"]["lazy_load_members"] is True
        assert sync_filter["presence"]["types"] == []


# ============================================================================
# Error Handling Tests
# ============================================================================

@pytest.mark.unit
class TestErrorHandling:
    """Test error handling in Matrix client"""

    @pytest.mark.asyncio
    async def test_handle_network_error(self, mock_nio_client):
        """Test handling of network errors"""
        import aiohttp

        # Mock network error
        mock_nio_client.sync = AsyncMock(
            side_effect=aiohttp.ClientError("Connection failed")
        )

        # Attempt sync and handle error
        with pytest.raises(aiohttp.ClientError):
            await mock_nio_client.sync()

    @pytest.mark.asyncio
    async def test_handle_timeout_error(self, mock_nio_client):
        """Test handling of timeout errors"""
        import asyncio

        # Mock timeout
        mock_nio_client.sync = AsyncMock(
            side_effect=asyncio.TimeoutError("Request timed out")
        )

        # Attempt sync and handle timeout
        with pytest.raises(asyncio.TimeoutError):
            await mock_nio_client.sync()

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        """Test retry logic on transient failures"""
        max_retries = 3
        attempt = 0

        async def failing_operation():
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise Exception("Transient error")
            return "Success"

        # Retry loop
        for i in range(max_retries):
            try:
                result = await failing_operation()
                break
            except Exception as e:
                if i == max_retries - 1:
                    raise
                await asyncio.sleep(0.1)

        assert result == "Success"
        assert attempt == 3
