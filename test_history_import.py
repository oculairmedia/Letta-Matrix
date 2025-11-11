#!/usr/bin/env python3
"""
Tests for Letta conversation history import functionality
"""
import pytest
import asyncio
import json
import os
import tempfile
import sys
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass
from typing import Optional

# Mock the global connector before importing agent_user_manager
with patch('aiohttp.TCPConnector'):
    from agent_user_manager import AgentUserManager, AgentUserMapping

from nio import AsyncClient, LoginResponse, RoomSendResponse


@dataclass
class MockConfig:
    """Mock configuration for testing"""
    homeserver_url: str = "http://localhost:8008"
    username: str = "@letta:matrix.oculair.ca"
    password: str = "letta"
    letta_api_url: str = "http://localhost:8283"
    letta_token: str = "test_token"
    matrix_api_url: str = "http://matrix-api:8000"


class TestHistoryImport:
    """Test suite for conversation history import"""

    @pytest.fixture
    def temp_data_dir(self):
        """Create a temporary directory for test data"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def mock_config(self):
        """Create a mock configuration"""
        return MockConfig()

    @pytest.fixture
    def agent_manager(self, mock_config, temp_data_dir):
        """Create an AgentUserManager with mock config and temp directory"""
        with patch('agent_user_manager.os.makedirs'):
            manager = AgentUserManager(mock_config)
            manager.mappings_file = os.path.join(temp_data_dir, "agent_user_mappings.json")
            return manager

    @pytest.mark.asyncio
    async def test_import_recent_history_success(self, agent_manager):
        """Test successful history import with valid messages"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        # Mock Letta proxy response
        mock_messages = [
            {
                "message_type": "user_message",
                "content": "Hello agent!"
            },
            {
                "message_type": "assistant_message",
                "content": "Hello! How can I help?"
            },
            {
                "message_type": "user_message",
                "content": "What's the weather?"
            }
        ]

        with patch('aiohttp.ClientSession') as mock_session:
            # Mock Letta API response
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": mock_messages})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            # Mock Matrix client
            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.login = AsyncMock(return_value=LoginResponse(
                    access_token="test_token",
                    user_id="@test:matrix.oculair.ca",
                    device_id="TEST_DEVICE"
                ))
                mock_client.room_send = AsyncMock(return_value=RoomSendResponse(
                    event_id="$test_event",
                    room_id="!test_room:matrix.oculair.ca"
                ))
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                # Import history
                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

                # Verify messages were sent
                assert mock_client.room_send.call_count == 3

    @pytest.mark.asyncio
    async def test_import_history_empty_messages(self, agent_manager):
        """Test handling of empty message history"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        with patch('aiohttp.ClientSession') as mock_session:
            # Mock empty response
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": []})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            # Import should complete without error
            await agent_manager.import_recent_history(
                agent_id=agent_id,
                agent_username=agent_username,
                agent_password=agent_password,
                room_id=room_id
            )

    @pytest.mark.asyncio
    async def test_import_history_orphaned_tool_return(self, agent_manager):
        """Test filtering of orphaned tool_return messages"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        # Mock messages starting with orphaned tool_return
        mock_messages = [
            {
                "message_type": "tool_return_message",
                "content": "Tool result"
            },
            {
                "message_type": "user_message",
                "content": "Hello agent!"
            }
        ]

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": mock_messages})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.login = AsyncMock(return_value=LoginResponse(
                    access_token="test_token",
                    user_id="@test:matrix.oculair.ca",
                    device_id="TEST_DEVICE"
                ))
                mock_client.room_send = AsyncMock(return_value=RoomSendResponse(
                    event_id="$test_event",
                    room_id="!test_room:matrix.oculair.ca"
                ))
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

                # Should only send 1 message (tool_return was skipped)
                assert mock_client.room_send.call_count == 1

    @pytest.mark.asyncio
    async def test_import_history_content_array_format(self, agent_manager):
        """Test parsing of content array format from Letta"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        # Mock message with content array
        mock_messages = [
            {
                "message_type": "user_message",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "world"}
                ]
            }
        ]

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": mock_messages})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.login = AsyncMock(return_value=LoginResponse(
                    access_token="test_token",
                    user_id="@test:matrix.oculair.ca",
                    device_id="TEST_DEVICE"
                ))
                mock_client.room_send = AsyncMock(return_value=RoomSendResponse(
                    event_id="$test_event",
                    room_id="!test_room:matrix.oculair.ca"
                ))
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

                # Verify content was joined correctly
                call_args = mock_client.room_send.call_args
                assert "Hello world" in call_args[1]["content"]["body"]

    @pytest.mark.asyncio
    async def test_import_history_api_failure(self, agent_manager):
        """Test handling of Letta API failure"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        with patch('aiohttp.ClientSession') as mock_session:
            # Mock 500 error
            mock_response = AsyncMock()
            mock_response.status = 500

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            # Should complete without raising exception
            await agent_manager.import_recent_history(
                agent_id=agent_id,
                agent_username=agent_username,
                agent_password=agent_password,
                room_id=room_id
            )

    @pytest.mark.asyncio
    async def test_import_history_agent_login_failure(self, agent_manager):
        """Test handling of agent login failure"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "wrong_password"
        room_id = "!test_room:matrix.oculair.ca"

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": [
                {"message_type": "user_message", "content": "Test"}
            ]})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                # Mock failed login
                mock_client.login = AsyncMock(return_value=Mock(spec=[]))  # Not a LoginResponse
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                # Should complete without raising exception
                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

    @pytest.mark.asyncio
    async def test_import_history_15_message_limit(self, agent_manager):
        """Test that only last 15 messages are imported"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        # Create 20 messages
        mock_messages = [
            {"message_type": "user_message", "content": f"Message {i}"}
            for i in range(20)
        ]

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": mock_messages})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.login = AsyncMock(return_value=LoginResponse(
                    access_token="test_token",
                    user_id="@test:matrix.oculair.ca",
                    device_id="TEST_DEVICE"
                ))
                mock_client.room_send = AsyncMock(return_value=RoomSendResponse(
                    event_id="$test_event",
                    room_id="!test_room:matrix.oculair.ca"
                ))
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

                # Should only send 15 messages
                assert mock_client.room_send.call_count == 15

    @pytest.mark.asyncio
    async def test_import_history_historical_flag_set(self, agent_manager):
        """Test that m.letta_historical flag is set on imported messages"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        mock_messages = [
            {"message_type": "user_message", "content": "Test message"}
        ]

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": mock_messages})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.login = AsyncMock(return_value=LoginResponse(
                    access_token="test_token",
                    user_id="@test:matrix.oculair.ca",
                    device_id="TEST_DEVICE"
                ))
                mock_client.room_send = AsyncMock(return_value=RoomSendResponse(
                    event_id="$test_event",
                    room_id="!test_room:matrix.oculair.ca"
                ))
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

                # Verify historical flag is set
                call_args = mock_client.room_send.call_args
                assert call_args[1]["content"]["m.letta_historical"] is True

    @pytest.mark.asyncio
    async def test_import_history_skips_tool_messages(self, agent_manager):
        """Test that tool messages are not imported"""
        agent_id = "agent-test-123"
        agent_username = "@agent_test:matrix.oculair.ca"
        agent_password = "password"
        room_id = "!test_room:matrix.oculair.ca"

        mock_messages = [
            {"message_type": "user_message", "content": "Hello"},
            {"message_type": "tool_call_message", "content": "tool_call"},
            {"message_type": "tool_return_message", "content": "tool_return"},
            {"message_type": "assistant_message", "content": "Response"}
        ]

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": mock_messages})

            # Create async context manager for session.get()
            mock_get_cm = MagicMock()
            mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get_cm.__aexit__ = AsyncMock(return_value=None)

            # Create session instance
            mock_session_instance = MagicMock()
            mock_session_instance.get = MagicMock(return_value=mock_get_cm)

            # Create async context manager for ClientSession()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('nio.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.login = AsyncMock(return_value=LoginResponse(
                    access_token="test_token",
                    user_id="@test:matrix.oculair.ca",
                    device_id="TEST_DEVICE"
                ))
                mock_client.room_send = AsyncMock(return_value=RoomSendResponse(
                    event_id="$test_event",
                    room_id="!test_room:matrix.oculair.ca"
                ))
                mock_client.close = AsyncMock()
                mock_client_class.return_value = mock_client

                await agent_manager.import_recent_history(
                    agent_id=agent_id,
                    agent_username=agent_username,
                    agent_password=agent_password,
                    room_id=room_id
                )

                # Should only send 2 messages (user + assistant, not tools)
                assert mock_client.room_send.call_count == 2


class TestHistoricalMessageFiltering:
    """Test suite for filtering historical messages in receiver"""

    @pytest.mark.asyncio
    async def test_historical_message_ignored(self):
        """Test that messages with m.letta_historical flag are ignored"""
        # This would require mocking the Matrix event handler
        # TODO: Implement when custom_matrix_client.py is refactored for testability
        pass

    @pytest.mark.asyncio
    async def test_non_historical_message_processed(self):
        """Test that messages without historical flag are processed normally"""
        # TODO: Implement when custom_matrix_client.py is refactored for testability
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
