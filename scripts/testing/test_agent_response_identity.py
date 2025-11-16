"""
Test suite for agent response identity.

This test suite ensures that when agents respond to messages, they respond
as their own Matrix user identity, not as the @letta bot user.

This prevents the regression where all agents would respond as @letta instead
of using their individual agent accounts (e.g., @agent_597b5756... for Meridian).
"""

import pytest
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Optional

# Import the functions we want to test
import sys
sys.path.insert(0, os.path.dirname(__file__))

# Mock the aiohttp connector to avoid event loop issues during import
with patch('aiohttp.TCPConnector'):
    from custom_matrix_client import send_as_agent, Config


def create_mock_config():
    """Create a Config instance for testing"""
    return Config(
        homeserver_url="http://localhost:8008",
        username="@letta:matrix.oculair.ca",
        password="test_password",
        room_id="!TestRoom:matrix.oculair.ca",
        letta_api_url="http://localhost:8289",
        letta_token="test_token",
        letta_agent_id="agent-default-fallback-id",
        log_level="INFO"
    )


@pytest.fixture
def mock_agent_mappings():
    """Create mock agent mappings"""
    return {
        "agent-597b5756-2915-4560-ba6b-91005f085166": {
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
            "agent_name": "Meridian",
            "matrix_user_id": "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!8I9YBvbr4KpXNedbph:matrix.oculair.ca",
            "created": True,
            "room_created": True
        },
        "agent-7659b796-d1ee-4c2d-9915-f676ee94667f": {
            "agent_id": "agent-7659b796-d1ee-4c2d-9915-f676ee94667f",
            "agent_name": "Personal Site",
            "matrix_user_id": "@agent_7659b796_d1ee_4c2d_9915_f676ee94667f:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!DifferentRoomID:matrix.oculair.ca",
            "created": True,
            "room_created": True
        }
    }


@pytest.mark.asyncio
async def test_send_as_agent_uses_correct_user():
    """
    CRITICAL TEST: Ensure messages are sent as the agent user, not @letta.
    
    When Meridian responds in her room, the message should come from
    @agent_597b5756... not from @letta:matrix.oculair.ca
    """
    config = create_mock_config()
    logger = Mock()
    
    mappings = {
        "agent-597b5756-2915-4560-ba6b-91005f085166": {
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
            "agent_name": "Meridian",
            "matrix_user_id": "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
        }
    }
    
    meridian_room = "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
    message = "I am Meridian responding"
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            # Mock aiohttp session
            mock_login_response = AsyncMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={
                "access_token": "test_meridian_token"
            })
            
            mock_send_response = AsyncMock()
            mock_send_response.status = 200
            
            # Create context managers
            mock_login_context = AsyncMock()
            mock_login_context.__aenter__.return_value = mock_login_response
            mock_login_context.__aexit__.return_value = None
            
            mock_send_context = AsyncMock()
            mock_send_context.__aenter__.return_value = mock_send_response
            mock_send_context.__aexit__.return_value = None
            
            mock_session = AsyncMock()
            mock_session.post = Mock(return_value=mock_login_context)
            mock_session.put = Mock(return_value=mock_send_context)
            
            mock_session_context = AsyncMock()
            mock_session_context.__aenter__.return_value = mock_session
            mock_session_context.__aexit__.return_value = None
            
            with patch('aiohttp.ClientSession', return_value=mock_session_context):
                result = await send_as_agent(meridian_room, message, config, logger)
                
                # Verify success
                assert result == True, "send_as_agent should return True"
                
                # Verify login was called with Meridian's credentials
                login_call = mock_session.post.call_args
                assert login_call is not None, "Login should be called"
                
                login_data = login_call[1]['json']
                assert login_data['user'] == "agent_597b5756_2915_4560_ba6b_91005f085166", \
                    "Should login as Meridian's user, not letta"
                assert login_data['password'] == "password"
                
                # Verify message was sent with Meridian's token
                send_call = mock_session.put.call_args
                assert send_call is not None, "Message send should be called"
                
                headers = send_call[1]['headers']
                assert headers['Authorization'] == "Bearer test_meridian_token", \
                    "Should use Meridian's token, not letta's token"


@pytest.mark.asyncio
async def test_different_agents_use_different_identities():
    """
    TEST: Verify that different agent rooms use different Matrix identities.
    
    Meridian should send as @agent_597b5756..., Personal Site as @agent_7659b796...
    """
    config = create_mock_config()
    logger = Mock()
    
    test_cases = [
        {
            "room_id": "!8I9YBvbr4KpXNedbph:matrix.oculair.ca",
            "agent_name": "Meridian",
            "expected_user": "agent_597b5756_2915_4560_ba6b_91005f085166",
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166"
        },
        {
            "room_id": "!DifferentRoomID:matrix.oculair.ca",
            "agent_name": "Personal Site",
            "expected_user": "agent_7659b796_d1ee_4c2d_9915_f676ee94667f",
            "agent_id": "agent-7659b796-d1ee-4c2d-9915-f676ee94667f"
        }
    ]
    
    for test_case in test_cases:
        mappings = {
            test_case["agent_id"]: {
                "agent_id": test_case["agent_id"],
                "agent_name": test_case["agent_name"],
                "matrix_user_id": f"@{test_case['expected_user']}:matrix.oculair.ca",
                "matrix_password": "password",
                "room_id": test_case["room_id"]
            }
        }
        
        with patch('custom_matrix_client.os.path.exists', return_value=True):
            with patch('builtins.open', create=True) as mock_file:
                mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
                
                # Mock responses
                mock_login_response = AsyncMock()
                mock_login_response.status = 200
                mock_login_response.json = AsyncMock(return_value={"access_token": "test_token"})
                
                mock_send_response = AsyncMock()
                mock_send_response.status = 200
                
                # Create context managers
                mock_login_context = AsyncMock()
                mock_login_context.__aenter__.return_value = mock_login_response
                mock_login_context.__aexit__.return_value = None
                
                mock_send_context = AsyncMock()
                mock_send_context.__aenter__.return_value = mock_send_response
                mock_send_context.__aexit__.return_value = None
                
                mock_session = AsyncMock()
                mock_session.post = Mock(return_value=mock_login_context)
                mock_session.put = Mock(return_value=mock_send_context)
                
                mock_session_context = AsyncMock()
                mock_session_context.__aenter__.return_value = mock_session
                mock_session_context.__aexit__.return_value = None
                
                with patch('aiohttp.ClientSession', return_value=mock_session_context):
                    result = await send_as_agent(test_case["room_id"], "Test message", config, logger)
                    
                    assert result == True
                    
                    # Verify correct user was used
                    login_call = mock_session.post.call_args
                    login_data = login_call[1]['json']
                    assert login_data['user'] == test_case['expected_user'], \
                        f"{test_case['agent_name']} should use their own user ID"


@pytest.mark.asyncio
async def test_send_as_agent_uses_put_with_transaction_id():
    """
    TEST: Verify Matrix message sending uses PUT with transaction ID.
    
    Matrix requires PUT to /_matrix/client/r0/rooms/{room}/send/m.room.message/{txn_id}
    """
    config = create_mock_config()
    logger = Mock()
    
    mappings = {
        "agent-test": {
            "agent_id": "agent-test",
            "agent_name": "Test Agent",
            "matrix_user_id": "@agent_test:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!TestRoom:matrix.oculair.ca"
        }
    }
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            # Mock responses
            mock_login_response = AsyncMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={"access_token": "test_token"})
            
            mock_send_response = AsyncMock()
            mock_send_response.status = 200
            
            # Create context managers
            mock_login_context = AsyncMock()
            mock_login_context.__aenter__.return_value = mock_login_response
            mock_login_context.__aexit__.return_value = None
            
            mock_send_context = AsyncMock()
            mock_send_context.__aenter__.return_value = mock_send_response
            mock_send_context.__aexit__.return_value = None
            
            mock_session = AsyncMock()
            mock_session.post = Mock(return_value=mock_login_context)
            mock_session.put = Mock(return_value=mock_send_context)
            
            mock_session_context = AsyncMock()
            mock_session_context.__aenter__.return_value = mock_session
            mock_session_context.__aexit__.return_value = None
            
            with patch('aiohttp.ClientSession', return_value=mock_session_context):
                result = await send_as_agent("!TestRoom:matrix.oculair.ca", "Test", config, logger)
                
                assert result == True
                
                # Verify PUT was used (not POST)
                assert mock_session.put.called, "Should use PUT for sending messages"
                assert not mock_session.post.called or mock_session.post.call_count == 1, \
                    "POST should only be used for login"
                
                # Verify URL includes transaction ID
                put_call = mock_session.put.call_args
                url = put_call[0][0]
                assert "/send/m.room.message/" in url, "URL should include message type"
                # Check that there's a UUID after the message type
                assert url.split("/send/m.room.message/")[1], "URL should include transaction ID"


@pytest.mark.asyncio
async def test_send_as_agent_handles_login_failure():
    """
    TEST: Verify graceful handling when agent login fails.
    
    Should return False and log error, allowing fallback to @letta.
    """
    config = create_mock_config()
    logger = Mock()
    
    mappings = {
        "agent-test": {
            "agent_id": "agent-test",
            "agent_name": "Test Agent",
            "matrix_user_id": "@agent_test:matrix.oculair.ca",
            "matrix_password": "wrong_password",
            "room_id": "!TestRoom:matrix.oculair.ca"
        }
    }
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            # Mock failed login
            mock_login_response = AsyncMock()
            mock_login_response.status = 403  # Forbidden - wrong password
            mock_login_response.text = AsyncMock(return_value='{"error": "Invalid credentials"}')
            
            mock_login_context = AsyncMock()
            mock_login_context.__aenter__.return_value = mock_login_response
            mock_login_context.__aexit__.return_value = None
            
            mock_session = AsyncMock()
            mock_session.post = Mock(return_value=mock_login_context)
            
            mock_session_context = AsyncMock()
            mock_session_context.__aenter__.return_value = mock_session
            mock_session_context.__aexit__.return_value = None
            
            with patch('aiohttp.ClientSession', return_value=mock_session_context):
                result = await send_as_agent("!TestRoom:matrix.oculair.ca", "Test", config, logger)
                
                # Should return False on login failure
                assert result == False, "Should return False when login fails"
                
                # Should log the error
                assert logger.error.called, "Should log error on login failure"


@pytest.mark.asyncio
async def test_send_as_agent_handles_missing_room_mapping():
    """
    TEST: Verify behavior when room has no agent mapping.
    
    Should return False, allowing fallback to @letta.
    """
    config = create_mock_config()
    logger = Mock()
    
    mappings = {
        "agent-test": {
            "agent_id": "agent-test",
            "agent_name": "Test Agent",
            "matrix_user_id": "@agent_test:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!DifferentRoom:matrix.oculair.ca"  # Different room
        }
    }
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            # Try to send in a room with no mapping
            result = await send_as_agent("!UnmappedRoom:matrix.oculair.ca", "Test", config, logger)
            
            # Should return False
            assert result == False, "Should return False when no mapping exists"
            
            # Should log warning
            assert logger.warning.called, "Should log warning when mapping not found"


@pytest.mark.asyncio
async def test_send_as_agent_message_content():
    """
    TEST: Verify the message content is sent correctly.
    
    The message body should be sent as-is with proper msgtype.
    """
    config = create_mock_config()
    logger = Mock()
    
    test_message = "This is Meridian's response with special chars: émojis 🎉"
    
    mappings = {
        "agent-test": {
            "agent_id": "agent-test",
            "agent_name": "Test Agent",
            "matrix_user_id": "@agent_test:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!TestRoom:matrix.oculair.ca"
        }
    }
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            # Mock responses
            mock_login_response = AsyncMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={"access_token": "test_token"})
            
            mock_send_response = AsyncMock()
            mock_send_response.status = 200
            
            # Create context managers
            mock_login_context = AsyncMock()
            mock_login_context.__aenter__.return_value = mock_login_response
            mock_login_context.__aexit__.return_value = None
            
            mock_send_context = AsyncMock()
            mock_send_context.__aenter__.return_value = mock_send_response
            mock_send_context.__aexit__.return_value = None
            
            mock_session = AsyncMock()
            mock_session.post = Mock(return_value=mock_login_context)
            mock_session.put = Mock(return_value=mock_send_context)
            
            mock_session_context = AsyncMock()
            mock_session_context.__aenter__.return_value = mock_session
            mock_session_context.__aexit__.return_value = None
            
            with patch('aiohttp.ClientSession', return_value=mock_session_context):
                result = await send_as_agent("!TestRoom:matrix.oculair.ca", test_message, config, logger)
                
                assert result == True
                
                # Verify message content
                put_call = mock_session.put.call_args
                message_data = put_call[1]['json']
                
                assert message_data['msgtype'] == "m.text", "Should use m.text msgtype"
                assert message_data['body'] == test_message, "Message body should match exactly"


def test_agent_mapping_structure():
    """
    TEST: Validate agent mapping contains required fields for send_as_agent.
    
    Ensures mappings have all necessary data for authentication and sending.
    """
    required_fields = ["agent_id", "agent_name", "matrix_user_id", "matrix_password", "room_id"]
    
    sample_mapping = {
        "agent-test": {
            "agent_id": "agent-test",
            "agent_name": "Test Agent",
            "matrix_user_id": "@agent_test:matrix.oculair.ca",
            "matrix_password": "password",
            "room_id": "!TestRoom:matrix.oculair.ca"
        }
    }
    
    for agent_id, mapping in sample_mapping.items():
        for field in required_fields:
            assert field in mapping, f"Mapping must contain {field}"
        
        # Validate matrix_user_id format
        assert mapping['matrix_user_id'].startswith('@'), "matrix_user_id should start with @"
        assert ':' in mapping['matrix_user_id'], "matrix_user_id should contain domain"
        
        # Validate room_id format
        assert mapping['room_id'].startswith('!'), "room_id should start with !"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
