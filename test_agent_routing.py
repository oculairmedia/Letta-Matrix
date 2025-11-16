"""
Test suite for agent routing functionality.

This test suite specifically prevents the bug where messages were routed to the wrong
agent due to Letta SDK pagination issues. It ensures that:

1. Messages are always routed to the correct agent based on room_id
2. The agent ID from the room mapping is used (not fallback to first agent)
3. Direct HTTP API calls work correctly without SDK dependencies
4. Agent responses are sent back to the correct room
"""

import pytest
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass
from typing import Optional

# Import the functions we want to test
import sys
sys.path.insert(0, os.path.dirname(__file__))

# Mock the aiohttp connector to avoid event loop issues during import
with patch('aiohttp.TCPConnector'):
    from custom_matrix_client import send_to_letta_api, Config


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


def create_mock_aiohttp_session(response_data):
    """
    Create a properly mocked aiohttp session for testing.
    
    Args:
        response_data: Dictionary to return as JSON response
    
    Returns:
        Tuple of (mock_session_class, mock_session, mock_response)
    """
    # Create mock response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value=response_data)
    
    # Create async context manager for post()
    mock_post_context = AsyncMock()
    mock_post_context.__aenter__.return_value = mock_response
    mock_post_context.__aexit__.return_value = None
    
    # Create mock session
    mock_session = AsyncMock()
    mock_session.post = Mock(return_value=mock_post_context)
    
    # Create mock session class
    mock_session_class = Mock()
    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_context.__aexit__.return_value = None
    mock_session_class.return_value = mock_session_context
    
    return mock_session_class, mock_session, mock_response


@pytest.fixture
def mock_agent_mappings():
    """Create mock agent mappings similar to production"""
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


@pytest.fixture
def temp_mappings_file(mock_agent_mappings):
    """Create a temporary agent mappings file"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(mock_agent_mappings, f)
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_correct_agent_routing_for_meridian_room(temp_mappings_file, mock_agent_mappings):
    """
    CRITICAL TEST: Ensure messages to Meridian's room are routed to Meridian agent.
    
    This test prevents the bug where messages were incorrectly routed to
    "Personal Site" agent instead of "Meridian" due to SDK pagination.
    """
    config = create_mock_config()
    logger = Mock()
    
    meridian_room_id = "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
    expected_agent_id = "agent-597b5756-2915-4560-ba6b-91005f085166"
    
    # Mock the agent mappings file location
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(mock_agent_mappings)
            
            # Mock aiohttp session
            response_data = {
                "messages": [{"role": "assistant", "text": "I am Meridian"}]
            }
            mock_session_class, mock_session, mock_response = create_mock_aiohttp_session(response_data)
            
            with patch('aiohttp.ClientSession', mock_session_class):
                
                # Patch the file reading
                with patch('custom_matrix_client.open', create=True) as mock_file:
                    mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mock_agent_mappings)
                    
                    # Send message
                    response = await send_to_letta_api(
                        message_body="Hello",
                        sender_id="@admin:matrix.oculair.ca",
                        config=config,
                        logger=logger,
                        room_id=meridian_room_id
                    )
                    
                    # Verify the correct agent ID was used in the API call
                    call_args = mock_session.post.call_args
                    assert call_args is not None, "API call was not made"
                    
                    # Check URL contains correct agent ID
                    url = call_args[0][0]
                    assert expected_agent_id in url, f"Expected {expected_agent_id} in URL {url}"
                    
                    # Ensure wrong agent ID is NOT in the URL
                    wrong_agent_id = "agent-7659b796-d1ee-4c2d-9915-f676ee94667f"
                    assert wrong_agent_id not in url, f"Wrong agent {wrong_agent_id} used in URL {url}"


@pytest.mark.asyncio
async def test_no_fallback_to_first_agent():
    """
    CRITICAL TEST: Ensure we don't fallback to first agent when room mapping exists.
    
    This was the core bug - when agent lookup failed due to pagination,
    it fell back to agents[0].
    """
    config = create_mock_config()
    logger = Mock()
    
    # Create mappings where Meridian is NOT the first agent
    mappings = {
        "agent-first-agent": {
            "agent_id": "agent-first-agent",
            "agent_name": "First Agent",
            "room_id": "!FirstRoom:matrix.oculair.ca"
        },
        "agent-597b5756-2915-4560-ba6b-91005f085166": {
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
            "agent_name": "Meridian",
            "room_id": "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
        }
    }
    
    meridian_room_id = "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            response_data = {"messages": [{"role": "assistant", "text": "Response"}]}
            mock_session_class, mock_session, _ = create_mock_aiohttp_session(response_data)
            
            with patch('aiohttp.ClientSession', mock_session_class):
                await send_to_letta_api(
                    message_body="Test",
                    sender_id="@user:matrix.oculair.ca",
                    config=config,
                    logger=logger,
                    room_id=meridian_room_id
                )
                
                # Verify correct agent was called, NOT the first agent
                call_args = mock_session.post.call_args[0][0]
                assert "agent-597b5756-2915-4560-ba6b-91005f085166" in call_args
                assert "agent-first-agent" not in call_args


@pytest.mark.asyncio
async def test_agent_routing_with_51_agents():
    """
    TEST: Simulate the exact production scenario with 50+ agents.
    
    The bug occurred because SDK's agents.list() only returned first 50 agents,
    and Meridian was agent #51-56. This test ensures routing works regardless
    of agent position.
    """
    config = create_mock_config()
    logger = Mock()
    
    # Create 56 agents (Meridian at position 51)
    mappings = {}
    for i in range(50):
        agent_id = f"agent-{i:04d}"
        mappings[agent_id] = {
            "agent_id": agent_id,
            "agent_name": f"Agent {i}",
            "room_id": f"!Room{i}:matrix.oculair.ca"
        }
    
    # Add Meridian as agent 51
    meridian_id = "agent-597b5756-2915-4560-ba6b-91005f085166"
    meridian_room = "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
    mappings[meridian_id] = {
        "agent_id": meridian_id,
        "agent_name": "Meridian",
        "room_id": meridian_room
    }
    
    # Add 5 more agents after Meridian
    for i in range(51, 56):
        agent_id = f"agent-{i:04d}"
        mappings[agent_id] = {
            "agent_id": agent_id,
            "agent_name": f"Agent {i}",
            "room_id": f"!Room{i}:matrix.oculair.ca"
        }
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            response_data = {"messages": [{"role": "assistant", "text": "Meridian response"}]}
            mock_session_class, mock_session, _ = create_mock_aiohttp_session(response_data)
            
            with patch('aiohttp.ClientSession', mock_session_class):
                await send_to_letta_api(
                    message_body="Test with 56 agents",
                    sender_id="@user:matrix.oculair.ca",
                    config=config,
                    logger=logger,
                    room_id=meridian_room
                )
                
                # Verify Meridian (agent 51) was used, not agent-0000 (first)
                call_url = mock_session.post.call_args[0][0]
                assert meridian_id in call_url
                assert "agent-0000" not in call_url


@pytest.mark.asyncio
async def test_direct_http_api_call():
    """
    TEST: Verify we're using direct HTTP API calls, not SDK.
    
    The fix removed the Letta SDK and uses direct HTTP POST.
    This test ensures that remains the case.
    """
    config = create_mock_config()
    logger = Mock()
    
    mappings = {
        "agent-test": {
            "agent_id": "agent-test",
            "agent_name": "Test Agent",
            "room_id": "!TestRoom:matrix.oculair.ca"
        }
    }
    
    with patch('custom_matrix_client.os.path.exists', return_value=True):
        with patch('builtins.open', create=True) as mock_file:
            mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
            
            response_data = {"messages": [{"role": "assistant", "text": "Response"}]}
            mock_session_class, mock_session, _ = create_mock_aiohttp_session(response_data)
            
            with patch('aiohttp.ClientSession', mock_session_class):
                await send_to_letta_api(
                    message_body="Test",
                    sender_id="@user:matrix.oculair.ca",
                    config=config,
                    logger=logger,
                    room_id="!TestRoom:matrix.oculair.ca"
                )
                
                # Verify aiohttp was used (HTTP POST)
                assert mock_session.post.called
                
                # Verify correct API endpoint format
                call_args = mock_session.post.call_args
                url = call_args[0][0]
                assert "/v1/agents/" in url
                assert "/messages" in url
                
                # Verify authorization header
                headers = call_args[1]['headers']
                assert 'Authorization' in headers
                assert headers['Authorization'] == f"Bearer {config.letta_token}"


@pytest.mark.asyncio
async def test_room_mapping_integrity():
    """
    TEST: Verify room-to-agent mapping is always used correctly.
    
    Ensures the mapping file lookup and agent selection logic is robust.
    """
    config = create_mock_config()
    logger = Mock()
    
    # Multiple agents with different rooms
    mappings = {
        "agent-a": {"agent_id": "agent-a", "room_id": "!RoomA:matrix.oculair.ca"},
        "agent-b": {"agent_id": "agent-b", "room_id": "!RoomB:matrix.oculair.ca"},
        "agent-c": {"agent_id": "agent-c", "room_id": "!RoomC:matrix.oculair.ca"}
    }
    
    test_cases = [
        ("!RoomA:matrix.oculair.ca", "agent-a"),
        ("!RoomB:matrix.oculair.ca", "agent-b"),
        ("!RoomC:matrix.oculair.ca", "agent-c")
    ]
    
    for room_id, expected_agent_id in test_cases:
        with patch('custom_matrix_client.os.path.exists', return_value=True):
            with patch('builtins.open', create=True) as mock_file:
                mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mappings)
                
                response_data = {"messages": [{"role": "assistant", "text": "Response"}]}
                mock_session_class, mock_session, _ = create_mock_aiohttp_session(response_data)
                
                with patch('aiohttp.ClientSession', mock_session_class):
                    await send_to_letta_api(
                        message_body="Test",
                        sender_id="@user:matrix.oculair.ca",
                        config=config,
                        logger=logger,
                        room_id=room_id
                    )
                    
                    call_url = mock_session.post.call_args[0][0]
                    assert expected_agent_id in call_url, \
                        f"Expected {expected_agent_id} for room {room_id}, got {call_url}"


def test_no_letta_sdk_imports():
    """
    TEST: Ensure Letta SDK is not imported in the codebase.
    
    The bug was caused by SDK pagination. This test ensures we don't
    accidentally re-introduce the SDK.
    """
    import custom_matrix_client
    import inspect
    
    source = inspect.getsource(custom_matrix_client)
    
    # Check for SDK imports that should NOT exist
    forbidden_imports = [
        "from letta import",
        "from letta_client import",
        "import letta",
        "import letta_client"
    ]
    
    for forbidden in forbidden_imports:
        assert forbidden not in source, \
            f"Found forbidden import: {forbidden}. SDK should not be used!"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
