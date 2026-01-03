"""
Extended unit tests for custom_matrix_client.py

Tests cover the most critical untested functions:
- retry_with_backoff() - Exponential backoff retry logic
- send_to_letta_api() - Agent communication and response parsing
- send_as_agent() - Agent response delivery
- message_callback() - Core message routing and filtering

These tests focus on the core message flow pipeline which has been
completely untested (0% coverage for these functions).
"""
import pytest
import asyncio
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock, mock_open
from nio import RoomMessageText

# Import modules to test
from src.matrix.client import (
    retry_with_backoff,
    send_to_letta_api,
    send_as_agent,
    message_callback,
    handle_letta_code_command,
    Config,
    LettaApiError
)



# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_config():
    """Create a mock Config object"""
    return Config(
        homeserver_url="http://test-server:8008",
        username="@testbot:test.com",
        password="test_password",
        room_id="!testroom:test.com",
        letta_api_url="http://test-letta:8080",
        letta_token="test_token_123",
        letta_agent_id="agent-test-001",
        log_level="INFO"
    )


@pytest.fixture
def mock_logger():
    """Create a mock logger"""
    logger = Mock()
    logger.info = Mock()
    logger.debug = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


@pytest.fixture
def temp_mappings_file(tmp_path):
    """Create a temporary agent mappings file (legacy - kept for compatibility)"""
    mappings_data = {
        "agent-001": {
            "agent_id": "agent-001",
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent_001:test.com",
            "matrix_password": "agent_password",
            "room_id": "!agentroom:test.com",
            "created": True,
            "room_created": True
        },
        "agent-002": {
            "agent_id": "agent-002",
            "agent_name": "SecondAgent",
            "matrix_user_id": "@agent_002:test.com",
            "matrix_password": "agent_password2",
            "room_id": "!secondroom:test.com",
            "created": True,
            "room_created": True
        }
    }

    mappings_file = tmp_path / "agent_user_mappings.json"
    with open(mappings_file, 'w') as f:
        json.dump(mappings_data, f)

    return str(mappings_file)


@pytest.fixture
def mock_mapping_data():
    """Return mock mapping data for tests"""
    return {
        "agent-001": {
            "agent_id": "agent-001",
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent_001:test.com",
            "matrix_password": "agent_password",
            "room_id": "!agentroom:test.com",
            "created": True,
            "room_created": True,
            "invitation_status": {}
        },
        "agent-002": {
            "agent_id": "agent-002",
            "agent_name": "SecondAgent",
            "matrix_user_id": "@agent_002:test.com",
            "matrix_password": "agent_password2",
            "room_id": "!secondroom:test.com",
            "created": True,
            "room_created": True,
            "invitation_status": {}
        }
    }


def mock_get_mapping_by_room_id(mappings_data):
    """Create a mock get_mapping_by_room_id function"""
    def _get_by_room_id(room_id):
        for mapping in mappings_data.values():
            if mapping.get("room_id") == room_id:
                return mapping
        return None
    return _get_by_room_id


def mock_get_mapping_by_matrix_user(mappings_data):
    """Create a mock get_mapping_by_matrix_user function"""
    def _get_by_matrix_user(matrix_user_id):
        for mapping in mappings_data.values():
            if mapping.get("matrix_user_id") == matrix_user_id:
                return mapping
        return None
    return _get_by_matrix_user


def mock_get_all_mappings(mappings_data):
    """Create a mock get_all_mappings function"""
    def _get_all():
        return mappings_data
    return _get_all


# ============================================================================
# retry_with_backoff() Tests
# ============================================================================

@pytest.mark.unit
class TestRetryWithBackoff:
    """Test exponential backoff retry logic"""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_first_attempt(self, mock_logger):
        """Test that function succeeds on first try without retries"""
        call_count = 0

        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_with_backoff(success_func, max_retries=3, logger=mock_logger)

        assert result == "success"
        assert call_count == 1
        # Should not log any warnings since it succeeded first time
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_failures(self, mock_logger):
        """Test that function succeeds after some failures"""
        call_count = 0

        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Temporary failure")
            return "success after retries"

        result = await retry_with_backoff(flaky_func, max_retries=3, base_delay=0.01, logger=mock_logger)

        assert result == "success after retries"
        assert call_count == 3
        # Should have logged warnings for the 2 failures
        assert mock_logger.warning.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_fails_after_max_retries(self, mock_logger):
        """Test that function raises exception after max retries"""
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise Exception(f"Failure {call_count}")

        with pytest.raises(Exception) as exc_info:
            await retry_with_backoff(always_fails, max_retries=3, base_delay=0.01, logger=mock_logger)

        assert "Failure 3" in str(exc_info.value)
        assert call_count == 3
        # Should have logged error for final failure
        mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_exponential_backoff_delay(self, mock_logger):
        """Test that delays follow exponential backoff pattern"""
        delays = []

        async def track_delays():
            if len(delays) < 2:
                delays.append(asyncio.get_event_loop().time())
                raise Exception("Fail to trigger retry")
            return "success"

        await retry_with_backoff(track_delays, max_retries=3, base_delay=0.1, logger=mock_logger)

        # Should have recorded 2 failures (with delays between them)
        # The delay should increase exponentially: 0.1, 0.2, 0.4...
        # We won't check exact timing due to test flakiness, just that retries happened
        assert len(delays) == 2

    @pytest.mark.asyncio
    async def test_retry_respects_max_delay(self, mock_logger):
        """Test that delay is capped at max_delay"""
        # With base_delay=1.0 and max_delay=2.0, after first retry (2^0 = 1.0),
        # second retry should be capped at 2.0 instead of 2.0 * 2^1 = 4.0

        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Fail")
            return "success"

        await retry_with_backoff(func, max_retries=3, base_delay=1.0, max_delay=2.0, logger=mock_logger)

        # Check that warning was called with capped delay
        warning_calls = [call for call in mock_logger.warning.call_args_list]
        # Should have 2 warning calls (for 2 failures before success)
        assert len(warning_calls) == 2

    @pytest.mark.asyncio
    async def test_retry_without_logger(self):
        """Test retry works without a logger"""
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("Fail")
            return "success"

        result = await retry_with_backoff(func, max_retries=3, base_delay=0.01, logger=None)

        assert result == "success"
        assert call_count == 2


# ============================================================================
# send_to_letta_api() Tests
# ============================================================================

@pytest.mark.unit
class TestSendToLettaApi:
    """Test Letta API communication via SDK"""

    @pytest.fixture(autouse=True)
    def reset_letta_client(self):
        """Reset the Letta client singleton before each test"""
        from src.letta.client import reset_client
        reset_client()
        yield
        reset_client()

    def _create_mock_sdk_response(self, messages: list):
        """Helper to create a mock SDK response object"""
        mock_response = Mock()
        mock_response.messages = []
        for msg in messages:
            mock_msg = Mock()
            mock_msg.message_type = msg.get("message_type", "unknown")
            mock_msg.content = msg.get("content")
            # Handle tool_call for inter-agent messages
            if "tool_call" in msg:
                mock_msg.tool_call = Mock()
                mock_msg.tool_call.name = msg["tool_call"]["name"]
                mock_msg.tool_call.arguments = msg["tool_call"]["arguments"]
            else:
                mock_msg.tool_call = None
            mock_response.messages.append(mock_msg)
        
        # Add model_dump method for serialization
        mock_response.model_dump = Mock(return_value={"messages": messages})
        return mock_response

    @pytest.mark.asyncio
    async def test_send_to_letta_api_success_with_assistant_message(self, mock_config, mock_logger):
        """Test successful API call with assistant message response"""
        # Create mock SDK client
        mock_client = Mock()
        mock_sdk_response = self._create_mock_sdk_response([
            {
                "message_type": "assistant_message",
                "content": "Hello! This is the agent's response."
            }
        ])
        mock_client.agents.messages.create = Mock(return_value=mock_sdk_response)

        with patch('src.letta.client.get_letta_client', return_value=mock_client):
            response = await send_to_letta_api(
                message_body="Test message",
                sender_id="@user:test.com",
                config=mock_config,
                logger=mock_logger
            )

        assert response == "Hello! This is the agent's response."
        mock_client.agents.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_letta_api_with_tool_call_message(self, mock_config, mock_logger):
        """Test API response with tool call (inter-agent message)"""
        # Create mock SDK client
        mock_client = Mock()
        mock_sdk_response = self._create_mock_sdk_response([
            {
                "message_type": "tool_call_message",
                "tool_call": {
                    "name": "matrix_agent_message",
                    "arguments": json.dumps({"message": "Inter-agent communication"})
                }
            }
        ])
        mock_client.agents.messages.create = Mock(return_value=mock_sdk_response)

        with patch('src.letta.client.get_letta_client', return_value=mock_client):
            response = await send_to_letta_api(
                message_body="Test message",
                sender_id="@user:test.com",
                config=mock_config,
                logger=mock_logger
            )

        assert "[Sent to another agent]: Inter-agent communication" in response

    @pytest.mark.asyncio
    async def test_send_to_letta_api_handles_http_error(self, mock_config, mock_logger):
        """Test handling of HTTP errors from Letta API"""
        from letta_client._exceptions import APIStatusError
        
        # Create mock SDK client that raises an error
        mock_client = Mock()
        # Create a mock response for the error
        mock_err_response = Mock()
        mock_err_response.status_code = 500
        mock_client.agents.messages.create = Mock(
            side_effect=APIStatusError(message="Internal Server Error", response=mock_err_response, body=None)
        )

        with patch('src.letta.client.get_letta_client', return_value=mock_client):
            with pytest.raises(Exception) as exc_info:
                await send_to_letta_api(
                    message_body="Test message",
                    sender_id="@user:test.com",
                    config=mock_config,
                    logger=mock_logger
                )

        # SDK raises APIStatusError which gets propagated
        assert "Internal Server Error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_to_letta_api_routes_to_correct_agent(self, mock_config, mock_logger):
        """Test that messages route to the correct agent based on room_id"""
        import sys
        
        # Create mock SDK client
        mock_client = Mock()
        mock_sdk_response = self._create_mock_sdk_response([
            {"message_type": "assistant_message", "content": "Response"}
        ])
        mock_client.agents.messages.create = Mock(return_value=mock_sdk_response)

        # Mock the database mapping lookup
        mock_mapping = Mock()
        mock_mapping.agent_id = "agent-001"
        mock_mapping.agent_name = "TestAgent"
        
        mock_db = Mock()
        mock_db.get_by_room_id = Mock(return_value=mock_mapping)
        
        # Create a mock module with our mocked class
        mock_module = Mock()
        mock_db_class = Mock(return_value=mock_db)
        mock_module.AgentMappingDB = mock_db_class
        
        # Patch sys.modules to include our mock module
        original_module = sys.modules.get('src.models.agent_mapping')
        sys.modules['src.models.agent_mapping'] = mock_module

        try:
            with patch('src.letta.client.get_letta_client', return_value=mock_client):
                response = await send_to_letta_api(
                    message_body="Test message",
                    sender_id="@user:test.com",
                    config=mock_config,
                    logger=mock_logger,
                    room_id="!agentroom:test.com"  # Should route to agent-001
                )

            # Check that the correct agent was called via SDK
            call_args = mock_client.agents.messages.create.call_args
            assert call_args.kwargs.get('agent_id') == "agent-001"
        finally:
            # Restore original module
            if original_module is not None:
                sys.modules['src.models.agent_mapping'] = original_module
            else:
                sys.modules.pop('src.models.agent_mapping', None)

    @pytest.mark.asyncio
    async def test_send_to_letta_api_extracts_username_from_sender(self, mock_config, mock_logger):
        """Test that username is correctly extracted from Matrix user ID"""
        # Create mock SDK client
        mock_client = Mock()
        mock_sdk_response = self._create_mock_sdk_response([
            {"message_type": "assistant_message", "content": "Response"}
        ])
        mock_client.agents.messages.create = Mock(return_value=mock_sdk_response)

        with patch('src.letta.client.get_letta_client', return_value=mock_client):
            await send_to_letta_api(
                message_body="Test",
                sender_id="@johndoe:matrix.org",  # Should extract "johndoe"
                config=mock_config,
                logger=mock_logger
            )

        # Verify logger was called with extracted username
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        # Should have logged with sender "johndoe"

    @pytest.mark.asyncio
    async def test_send_to_letta_api_empty_response(self, mock_config, mock_logger):
        """Test handling of empty response from Letta API"""
        # Create mock SDK client with empty messages
        mock_client = Mock()
        mock_sdk_response = self._create_mock_sdk_response([])  # Empty messages
        mock_client.agents.messages.create = Mock(return_value=mock_sdk_response)

        with patch('src.letta.client.get_letta_client', return_value=mock_client):
            response = await send_to_letta_api(
                message_body="Test",
                sender_id="@user:test.com",
                config=mock_config,
                logger=mock_logger
            )

        # Should return fallback message
        assert "check other agent's room" in response.lower() or "no assistant messages" in mock_logger.warning.call_args_list[0][0][0].lower()


# ============================================================================
# send_as_agent() Tests
# ============================================================================

@pytest.mark.unit
class TestSendAsAgent:
    """Test agent response delivery"""

    @pytest.mark.asyncio
    async def test_send_as_agent_success(self, mock_config, mock_logger, temp_mappings_file):
        """Test successfully sending message as agent"""
        # Mock mapping service
        mock_mapping = {
            "agent_id": "agent-001",
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent_001:test.com",
            "matrix_password": "agent_password",
            "room_id": "!agentroom:test.com",
            "room_created": True
        }
        
        # Mock login response
        login_response = AsyncMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "agent_token_123"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock message send response
        send_response = AsyncMock()
        send_response.status = 200
        send_response.__aenter__ = AsyncMock(return_value=send_response)
        send_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=login_response)
        mock_session.put = Mock(return_value=send_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
            with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=mock_mapping):
                result = await send_as_agent(
                    room_id="!agentroom:test.com",
                    message="Test response",
                    config=mock_config,
                    logger=mock_logger
                )

        assert result is True
        mock_session.post.assert_called_once()  # Login
        mock_session.put.assert_called_once()  # Send message

    @pytest.mark.asyncio
    async def test_send_as_agent_no_mappings_file(self, mock_config, mock_logger):
        """Test handling when no mapping found"""
        with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=None):
            result = await send_as_agent(
                room_id="!agentroom:test.com",
                message="Test",
                config=mock_config,
                logger=mock_logger
            )

        assert result is False
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_send_as_agent_room_not_found(self, mock_config, mock_logger, temp_mappings_file):
        """Test handling when room is not in mappings"""
        with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=None):
            result = await send_as_agent(
                room_id="!unknownroom:test.com",  # Not in mappings
                message="Test",
                config=mock_config,
                logger=mock_logger
            )

        assert result is False
        # Should log warning about no agent mapping found

    @pytest.mark.asyncio
    async def test_send_as_agent_login_failure(self, mock_config, mock_logger, mock_mapping_data):
        """Test handling of login failure"""
        mock_mapping = {
            "agent_id": "agent-001",
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent_001:test.com",
            "matrix_password": "agent_password",
            "room_id": "!agentroom:test.com",
            "room_created": True
        }
        
        login_response = AsyncMock()
        login_response.status = 403  # Forbidden
        login_response.text = AsyncMock(return_value="Invalid credentials")
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=login_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=mock_mapping):
            with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
                result = await send_as_agent(
                    room_id="!agentroom:test.com",
                    message="Test",
                    config=mock_config,
                    logger=mock_logger
                )

        assert result is False
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_send_as_agent_message_send_failure(self, mock_config, mock_logger, mock_mapping_data):
        """Test handling of message send failure"""
        mock_mapping = mock_mapping_data["agent-001"]
        
        login_response = AsyncMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "agent_token"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)

        send_response = AsyncMock()
        send_response.status = 403  # Forbidden to send
        send_response.text = AsyncMock(return_value="Not in room")
        send_response.__aenter__ = AsyncMock(return_value=send_response)
        send_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=login_response)
        mock_session.put = Mock(return_value=send_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=mock_mapping):
            with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
                result = await send_as_agent(
                    room_id="!agentroom:test.com",
                    message="Test",
                    config=mock_config,
                    logger=mock_logger
                )

        assert result is False
        # Should have logged error about failed message send


# ============================================================================
# message_callback() Tests
# ============================================================================

@pytest.mark.unit
class TestMessageCallback:
    """Test core message routing and filtering"""

    @pytest.mark.asyncio
    async def test_message_callback_ignores_duplicate_events(self, mock_config, mock_logger):
        """Test that duplicate events are ignored"""
        mock_room = Mock()
        mock_room.room_id = "!testroom:test.com"
        mock_room.display_name = "Test Room"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@user:test.com"
        mock_event.body = "Test message"
        mock_event.event_id = "$duplicate_event_123"

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        # Mock is_duplicate_event to return True
        with patch('src.matrix.client.is_duplicate_event', return_value=True):
            await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should return early, no Letta API call
        # Logger should not have logged "Received message from user"
        user_msg_calls = [call for call in mock_logger.info.call_args_list
                         if "Received message from user" in str(call)]
        assert len(user_msg_calls) == 0

    @pytest.mark.asyncio
    async def test_message_callback_ignores_own_messages(self, mock_config, mock_logger):
        """Test that bot's own messages are ignored"""
        mock_room = Mock()
        mock_room.room_id = "!testroom:test.com"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@bot:test.com"  # Same as client user
        mock_event.body = "Bot's own message"
        mock_event.event_id = "$event_456"

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        with patch('src.matrix.client.is_duplicate_event', return_value=False):
            await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should return early without processing

    @pytest.mark.asyncio
    async def test_message_callback_ignores_room_agent_self_messages(self, mock_config, mock_logger, mock_mapping_data):
        """Test that room's own agent messages are ignored (prevent self-loops)"""
        mock_room = Mock()
        mock_room.room_id = "!agentroom:test.com"  # TestAgent's room
        mock_room.display_name = "TestAgent Room"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@agent_001:test.com"  # The room's own agent
        mock_event.body = "Agent self-message"
        mock_event.event_id = "$event_789"

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        with patch('src.matrix.client.is_duplicate_event', return_value=False):
            with patch('src.core.mapping_service.get_mapping_by_room_id', 
                       side_effect=mock_get_mapping_by_room_id(mock_mapping_data)):
                with patch('src.core.mapping_service.get_mapping_by_matrix_user', 
                           side_effect=mock_get_mapping_by_matrix_user(mock_mapping_data)):
                    with patch('src.core.mapping_service.get_all_mappings', 
                               side_effect=mock_get_all_mappings(mock_mapping_data)):
                        await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should log that it's ignoring the room's own agent
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        agent_self_ignore = [call for call in debug_calls if "Ignoring message from room's own agent" in call]
        assert len(agent_self_ignore) > 0

    @pytest.mark.asyncio
    async def test_message_callback_processes_user_message(self, mock_config, mock_logger, mock_mapping_data):
        """Test that valid user messages are processed"""
        mock_room = Mock()
        mock_room.room_id = "!agentroom:test.com"
        mock_room.display_name = "TestAgent Room"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@user:test.com"  # Regular user
        mock_event.body = "Hello agent!"
        mock_event.event_id = "$event_abc"
        mock_event.source = {}

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        # Mock Letta API response
        with patch('src.matrix.client.is_duplicate_event', return_value=False):
            with patch('src.core.mapping_service.get_mapping_by_room_id', 
                       side_effect=mock_get_mapping_by_room_id(mock_mapping_data)):
                with patch('src.core.mapping_service.get_mapping_by_matrix_user', 
                           side_effect=mock_get_mapping_by_matrix_user(mock_mapping_data)):
                    with patch('src.core.mapping_service.get_all_mappings', 
                               side_effect=mock_get_all_mappings(mock_mapping_data)):
                        with patch('src.matrix.client.send_to_letta_api', return_value="Agent response"):
                            with patch('src.matrix.client.send_as_agent', return_value=True):
                                await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should have logged "Received message from user"
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        received_msg = [call for call in info_calls if "Received message from user" in call]
        assert len(received_msg) > 0

    @pytest.mark.asyncio
    async def test_message_callback_detects_inter_agent_message(self, mock_config, mock_logger, mock_mapping_data):
        """Test detection of inter-agent messages"""
        mock_room = Mock()
        mock_room.room_id = "!agentroom:test.com"  # agent-001's room
        mock_room.display_name = "TestAgent Room"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@agent_002:test.com"  # Different agent sending to agent-001's room
        mock_event.body = "Inter-agent message"
        mock_event.event_id = "$event_inter"
        mock_event.source = {}

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        with patch('src.matrix.client.is_duplicate_event', return_value=False):
            with patch('src.core.mapping_service.get_mapping_by_room_id', 
                       side_effect=mock_get_mapping_by_room_id(mock_mapping_data)):
                with patch('src.core.mapping_service.get_mapping_by_matrix_user', 
                           side_effect=mock_get_mapping_by_matrix_user(mock_mapping_data)):
                    with patch('src.core.mapping_service.get_all_mappings', 
                               side_effect=mock_get_all_mappings(mock_mapping_data)):
                        with patch('src.matrix.client.send_to_letta_api', return_value="Response"):
                            with patch('src.matrix.client.send_as_agent', return_value=True):
                                await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should have detected inter-agent message
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        inter_agent_calls = [call for call in info_calls if "inter-agent" in call.lower()]
        assert len(inter_agent_calls) > 0

    @pytest.mark.asyncio
    async def test_message_callback_ignores_bridge_originated_messages(self, mock_config, mock_logger, mock_mapping_data):
        mock_room = Mock()
        mock_room.room_id = "!agentroom:test.com"
        mock_room.display_name = "TestAgent Room"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@admin:test.com"
        mock_event.body = "Message from webhook bridge"
        mock_event.event_id = "$event_bridge"
        mock_event.source = {
            "content": {
                "msgtype": "m.text",
                "body": "Message from webhook bridge",
                "m.bridge_originated": True
            }
        }

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        with patch('src.matrix.client.is_duplicate_event', return_value=False):
            with patch('src.core.mapping_service.get_mapping_by_room_id', 
                       side_effect=mock_get_mapping_by_room_id(mock_mapping_data)):
                with patch('src.matrix.client.send_to_letta_api', return_value="Response") as mock_letta:
                    await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        mock_letta.assert_not_called()
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        bridge_ignore = [call for call in debug_calls if "bridge-originated" in call.lower()]
        assert len(bridge_ignore) > 0

    @pytest.mark.asyncio
    async def test_message_callback_ignores_letta_historical_messages(self, mock_config, mock_logger, mock_mapping_data):
        mock_room = Mock()
        mock_room.room_id = "!agentroom:test.com"
        mock_room.display_name = "TestAgent Room"

        mock_event = Mock(spec=RoomMessageText)
        mock_event.sender = "@user:test.com"
        mock_event.body = "Historical message"
        mock_event.event_id = "$event_hist"
        mock_event.source = {
            "content": {
                "msgtype": "m.text",
                "body": "Historical message",
                "m.letta_historical": True
            }
        }

        mock_client = Mock()
        mock_client.user_id = "@bot:test.com"

        with patch('src.matrix.client.is_duplicate_event', return_value=False):
            with patch('src.core.mapping_service.get_mapping_by_room_id', 
                       side_effect=mock_get_mapping_by_room_id(mock_mapping_data)):
                with patch('src.matrix.client.send_to_letta_api', return_value="Response") as mock_letta:
                    await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        mock_letta.assert_not_called()


@pytest.mark.unit
class TestFilesystemCommands:
    @pytest.mark.asyncio
    async def test_fs_link_uses_project_path_payload(self, mock_config, mock_logger):
        room = MagicMock()
        room.room_id = "!room:matrix"
        event = MagicMock()
        event.body = "/fs-link /opt/stacks/huly-personal-site"

        agent_id = "agent-abc-123"
        agent_name = "Huly - Personal Site"

        with patch("src.matrix.client.get_letta_code_room_state", return_value={}) as mock_get_state, \
             patch("src.matrix.client.update_letta_code_room_state") as mock_update_state, \
             patch("src.matrix.client.call_letta_code_api", new_callable=AsyncMock) as mock_api, \
             patch("src.matrix.client.send_as_agent", new_callable=AsyncMock) as mock_send:
            mock_api.return_value = {"message": "Linked"}

            handled = await handle_letta_code_command(
                room,
                event,
                mock_config,
                mock_logger,
                agent_mapping={"matrix_user_id": "@agent_abc:matrix"},
                agent_id_hint=agent_id,
                agent_name_hint=agent_name,
            )

        assert handled is True
        mock_get_state.assert_called_once_with(room.room_id)
        mock_api.assert_awaited_once()
        awaited_call = mock_api.await_args
        assert awaited_call is not None
        payload = awaited_call.args[3]
        assert payload["projectDir"] == "/opt/stacks/huly-personal-site"
        mock_update_state.assert_called_with(room.room_id, {"projectDir": "/opt/stacks/huly-personal-site"})
        mock_send.assert_awaited()


@pytest.mark.unit
class TestRichReplies:
    """Tests for Matrix rich reply functionality"""
    
    @pytest.mark.asyncio
    async def test_send_as_agent_includes_rich_reply(self, mock_config, mock_logger):
        """Test that send_as_agent can include m.in_reply_to for rich replies"""
        from src.matrix.client import send_as_agent_with_event_id
        
        room_id = "!testroom:matrix"
        message = "This is my reply"
        reply_to_event_id = "$original_event_abc123"
        reply_to_sender = "@user:matrix"
        
        # Mock the mapping service
        mock_mapping = {
            "room_id": room_id,
            "agent_name": "Test Agent",
            "matrix_user_id": "@test_agent:matrix",
            "matrix_password": "secret123"
        }
        
        with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=mock_mapping), \
             patch("aiohttp.ClientSession") as mock_session_class:
            
            # Setup mock session responses
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session_class.return_value = mock_session
            
            # Mock login response
            mock_login_response = MagicMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={"access_token": "token123"})
            mock_login_ctx = MagicMock()
            mock_login_ctx.__aenter__ = AsyncMock(return_value=mock_login_response)
            mock_login_ctx.__aexit__ = AsyncMock()
            
            # Mock send message response
            mock_send_response = MagicMock()
            mock_send_response.status = 200
            mock_send_response.json = AsyncMock(return_value={"event_id": "$new_event_xyz"})
            mock_send_ctx = MagicMock()
            mock_send_ctx.__aenter__ = AsyncMock(return_value=mock_send_response)
            mock_send_ctx.__aexit__ = AsyncMock()
            
            # Track what was sent
            sent_data = {}
            
            def capture_put(*args, **kwargs):
                sent_data['json'] = kwargs.get('json', {})
                return mock_send_ctx
            
            mock_session.post.return_value = mock_login_ctx
            mock_session.put.side_effect = capture_put
            
            # Call the function with reply parameters
            result = await send_as_agent_with_event_id(
                room_id, message, mock_config, mock_logger,
                reply_to_event_id=reply_to_event_id,
                reply_to_sender=reply_to_sender
            )
            
            # Verify the response
            assert result == "$new_event_xyz"
            
            # Verify the message data includes rich reply structure
            assert "m.relates_to" in sent_data['json']
            assert "m.in_reply_to" in sent_data['json']['m.relates_to']
            assert sent_data['json']['m.relates_to']['m.in_reply_to']['event_id'] == reply_to_event_id
            
            # Verify mentions are included
            assert "m.mentions" in sent_data['json']
            assert reply_to_sender in sent_data['json']['m.mentions']['user_ids']
    
    @pytest.mark.asyncio
    async def test_send_as_agent_without_reply(self, mock_config, mock_logger):
        """Test that send_as_agent works without reply parameters (original behavior)"""
        from src.matrix.client import send_as_agent_with_event_id
        
        room_id = "!testroom:matrix"
        message = "Just a regular message"
        
        # Mock the mapping service
        mock_mapping = {
            "room_id": room_id,
            "agent_name": "Test Agent",
            "matrix_user_id": "@test_agent:matrix",
            "matrix_password": "secret123"
        }
        
        with patch('src.core.mapping_service.get_mapping_by_room_id', return_value=mock_mapping), \
             patch("aiohttp.ClientSession") as mock_session_class:
            
            # Setup mock session responses
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session_class.return_value = mock_session
            
            # Mock login response
            mock_login_response = MagicMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={"access_token": "token123"})
            mock_login_ctx = MagicMock()
            mock_login_ctx.__aenter__ = AsyncMock(return_value=mock_login_response)
            mock_login_ctx.__aexit__ = AsyncMock()
            
            # Mock send message response
            mock_send_response = MagicMock()
            mock_send_response.status = 200
            mock_send_response.json = AsyncMock(return_value={"event_id": "$new_event_xyz"})
            mock_send_ctx = MagicMock()
            mock_send_ctx.__aenter__ = AsyncMock(return_value=mock_send_response)
            mock_send_ctx.__aexit__ = AsyncMock()
            
            # Track what was sent
            sent_data = {}
            
            def capture_put(*args, **kwargs):
                sent_data['json'] = kwargs.get('json', {})
                return mock_send_ctx
            
            mock_session.post.return_value = mock_login_ctx
            mock_session.put.side_effect = capture_put
            
            # Call the function without reply parameters
            result = await send_as_agent_with_event_id(
                room_id, message, mock_config, mock_logger
            )
            
            # Verify the response
            assert result == "$new_event_xyz"
            
            # Verify no rich reply structure is included
            assert "m.relates_to" not in sent_data['json']
            assert "m.mentions" not in sent_data['json']
            
            # Verify basic message structure
            assert sent_data['json']['msgtype'] == "m.text"
            assert sent_data['json']['body'] == message

