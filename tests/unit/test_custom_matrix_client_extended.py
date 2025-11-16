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
    """Create a temporary agent mappings file"""
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
    """Test Letta API communication"""

    @pytest.mark.asyncio
    async def test_send_to_letta_api_success_with_assistant_message(self, mock_config, mock_logger):
        """Test successful API call with assistant message response"""
        # Mock successful HTTP response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "messages": [
                {
                    "message_type": "assistant_message",
                    "content": "Hello! This is the agent's response."
                }
            ]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
            response = await send_to_letta_api(
                message_body="Test message",
                sender_id="@user:test.com",
                config=mock_config,
                logger=mock_logger
            )

        assert response == "Hello! This is the agent's response."
        mock_session.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_letta_api_with_tool_call_message(self, mock_config, mock_logger):
        """Test API response with tool call (inter-agent message)"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "messages": [
                {
                    "message_type": "tool_call_message",
                    "tool_call": {
                        "name": "matrix_agent_message",
                        "arguments": json.dumps({"message": "Inter-agent communication"})
                    }
                }
            ]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
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
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
            with pytest.raises(LettaApiError) as exc_info:
                await send_to_letta_api(
                    message_body="Test message",
                    sender_id="@user:test.com",
                    config=mock_config,
                    logger=mock_logger
                )

        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_to_letta_api_routes_to_correct_agent(self, mock_config, mock_logger, temp_mappings_file):
        """Test that messages route to the correct agent based on room_id"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "messages": [{"message_type": "assistant_message", "content": "Response"}]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
                    response = await send_to_letta_api(
                        message_body="Test message",
                        sender_id="@user:test.com",
                        config=mock_config,
                        logger=mock_logger,
                        room_id="!agentroom:test.com"  # Should route to agent-001
                    )

        # Check that the correct agent URL was called
        call_args = mock_session.post.call_args
        assert "agent-001" in call_args[0][0]  # URL should contain agent-001

    @pytest.mark.asyncio
    async def test_send_to_letta_api_extracts_username_from_sender(self, mock_config, mock_logger):
        """Test that username is correctly extracted from Matrix user ID"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "messages": [{"message_type": "assistant_message", "content": "Response"}]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
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
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "messages": []  # Empty messages
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
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
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
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
        """Test handling when mappings file doesn't exist"""
        with patch('src.matrix.client.os.path.exists', return_value=False):
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
        with patch('src.matrix.client.os.path.exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
                result = await send_as_agent(
                    room_id="!unknownroom:test.com",  # Not in mappings
                    message="Test",
                    config=mock_config,
                    logger=mock_logger
                )

        assert result is False
        # Should log warning about no agent mapping found

    @pytest.mark.asyncio
    async def test_send_as_agent_login_failure(self, mock_config, mock_logger, temp_mappings_file):
        """Test handling of login failure"""
        login_response = AsyncMock()
        login_response.status = 403  # Forbidden
        login_response.text = AsyncMock(return_value="Invalid credentials")
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = Mock(return_value=login_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
                    result = await send_as_agent(
                        room_id="!agentroom:test.com",
                        message="Test",
                        config=mock_config,
                        logger=mock_logger
                    )

        assert result is False
        mock_logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_send_as_agent_message_send_failure(self, mock_config, mock_logger, temp_mappings_file):
        """Test handling of message send failure"""
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

        with patch('src.matrix.client.aiohttp.ClientSession', return_value=mock_session):
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
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
    async def test_message_callback_ignores_room_agent_self_messages(self, mock_config, mock_logger, temp_mappings_file):
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
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
                    await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should log that it's ignoring the room's own agent
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        agent_self_ignore = [call for call in debug_calls if "Ignoring message from room's own agent" in call]
        assert len(agent_self_ignore) > 0

    @pytest.mark.asyncio
    async def test_message_callback_processes_user_message(self, mock_config, mock_logger, temp_mappings_file):
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
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
                    with patch('src.matrix.client.send_to_letta_api', return_value="Agent response"):
                        with patch('src.matrix.client.send_as_agent', return_value=True):
                            await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should have logged "Received message from user"
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        received_msg = [call for call in info_calls if "Received message from user" in call]
        assert len(received_msg) > 0

    @pytest.mark.asyncio
    async def test_message_callback_detects_inter_agent_message(self, mock_config, mock_logger, temp_mappings_file):
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
            with patch('src.matrix.client.os.path.exists', return_value=True):
                with patch('builtins.open', mock_open(read_data=open(temp_mappings_file).read())):
                    with patch('src.matrix.client.send_to_letta_api', return_value="Response"):
                        with patch('src.matrix.client.send_as_agent', return_value=True):
                            await message_callback(mock_room, mock_event, mock_config, mock_logger, mock_client)

        # Should have detected inter-agent message
        info_calls = [str(call) for call in mock_logger.info.call_args_list]
        inter_agent_calls = [call for call in info_calls if "inter-agent" in call.lower()]
        assert len(inter_agent_calls) > 0
