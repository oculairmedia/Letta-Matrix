#!/usr/bin/env python3
"""
Tests for Matrix Space management functionality
"""
import pytest
import asyncio
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass
from typing import Optional

# Import the modules we're testing
from agent_user_manager import AgentUserManager, AgentUserMapping


@dataclass
class MockConfig:
    """Mock configuration for testing"""
    homeserver_url: str = "http://localhost:8008"
    username: str = "@letta:matrix.oculair.ca"
    password: str = "letta"
    letta_api_url: str = "http://localhost:8283"
    letta_token: str = "test_token"
    matrix_api_url: str = "http://matrix-api:8000"


class TestSpaceManagement:
    """Test suite for Matrix Space management"""

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
            manager.space_config_file = os.path.join(temp_data_dir, "letta_space_config.json")
            return manager

    @pytest.mark.asyncio
    async def test_space_creation(self, agent_manager):
        """Test that a space is created correctly"""
        space_id = "!test_space:matrix.oculair.ca"

        # Mock the HTTP requests
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"room_id": space_id})

            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response

            # Mock the admin login
            agent_manager.get_admin_token = AsyncMock(return_value="test_admin_token")

            # Create the space
            result = await agent_manager.create_letta_agents_space()

            assert result == space_id
            assert agent_manager.space_id == space_id

    @pytest.mark.asyncio
    async def test_space_persistence(self, agent_manager):
        """Test that space configuration is saved and loaded correctly"""
        space_id = "!test_space:matrix.oculair.ca"
        agent_manager.space_id = space_id

        # Save space config
        await agent_manager.save_space_config()

        # Verify file was created
        assert os.path.exists(agent_manager.space_config_file)

        # Load space config in a new manager instance
        new_manager = AgentUserManager(agent_manager.config)
        new_manager.space_config_file = agent_manager.space_config_file
        await new_manager.load_space_config()

        assert new_manager.space_id == space_id

    @pytest.mark.asyncio
    async def test_add_room_to_space(self, agent_manager):
        """Test adding a room to the space"""
        space_id = "!test_space:matrix.oculair.ca"
        room_id = "!test_room:matrix.oculair.ca"
        room_name = "Test Agent"

        agent_manager.space_id = space_id
        agent_manager.get_admin_token = AsyncMock(return_value="test_admin_token")

        # Mock the HTTP requests for adding child and parent relationships
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200

            mock_session.return_value.__aenter__.return_value.put.return_value.__aenter__.return_value = mock_response

            # Add room to space
            result = await agent_manager.add_room_to_space(room_id, room_name)

            assert result is True

    @pytest.mark.asyncio
    async def test_add_room_without_space(self, agent_manager):
        """Test that adding a room fails gracefully when no space exists"""
        room_id = "!test_room:matrix.oculair.ca"
        room_name = "Test Agent"

        # Don't set a space_id
        agent_manager.space_id = None

        # Attempt to add room to space
        result = await agent_manager.add_room_to_space(room_id, room_name)

        assert result is False

    @pytest.mark.asyncio
    async def test_migrate_existing_rooms(self, agent_manager):
        """Test migration of existing rooms to the space"""
        space_id = "!test_space:matrix.oculair.ca"
        agent_manager.space_id = space_id

        # Create mock agent mappings with rooms
        agent_manager.mappings = {
            "agent-1": AgentUserMapping(
                agent_id="agent-1",
                agent_name="Agent One",
                matrix_user_id="@agent_1:matrix.oculair.ca",
                matrix_password="password",
                created=True,
                room_id="!room1:matrix.oculair.ca",
                room_created=True
            ),
            "agent-2": AgentUserMapping(
                agent_id="agent-2",
                agent_name="Agent Two",
                matrix_user_id="@agent_2:matrix.oculair.ca",
                matrix_password="password",
                created=True,
                room_id="!room2:matrix.oculair.ca",
                room_created=True
            ),
            "agent-3": AgentUserMapping(
                agent_id="agent-3",
                agent_name="Agent Three",
                matrix_user_id="@agent_3:matrix.oculair.ca",
                matrix_password="password",
                created=True,
                room_id=None,  # No room created yet
                room_created=False
            )
        }

        # Mock add_room_to_space to succeed
        agent_manager.add_room_to_space = AsyncMock(return_value=True)

        # Migrate rooms
        migrated_count = await agent_manager.migrate_existing_rooms_to_space()

        # Should migrate 2 rooms (agent-1 and agent-2), skip agent-3
        assert migrated_count == 2
        assert agent_manager.add_room_to_space.call_count == 2

    @pytest.mark.asyncio
    async def test_space_reuse_on_restart(self, agent_manager):
        """Test that existing space is reused on restart"""
        space_id = "!existing_space:matrix.oculair.ca"

        # Save existing space config
        agent_manager.space_id = space_id
        await agent_manager.save_space_config()

        # Mock check_room_exists to return True (space exists)
        agent_manager.check_room_exists = AsyncMock(return_value=True)

        # Create space (should reuse existing)
        result = await agent_manager.create_letta_agents_space()

        assert result == space_id
        assert agent_manager.space_id == space_id

    @pytest.mark.asyncio
    async def test_space_recreation_if_deleted(self, agent_manager):
        """Test that space is recreated if the stored one was deleted"""
        old_space_id = "!deleted_space:matrix.oculair.ca"
        new_space_id = "!new_space:matrix.oculair.ca"

        # Save old space config
        agent_manager.space_id = old_space_id
        await agent_manager.save_space_config()

        # Mock check_room_exists to return False (space was deleted)
        agent_manager.check_room_exists = AsyncMock(return_value=False)

        # Mock space creation
        with patch('aiohttp.ClientSession') as mock_session:
            # Mock login response
            login_response = AsyncMock()
            login_response.status = 200
            login_response.json = AsyncMock(return_value={"access_token": "test_token"})

            # Mock create room response
            create_response = AsyncMock()
            create_response.status = 200
            create_response.json = AsyncMock(return_value={"room_id": new_space_id})

            mock_session_instance = mock_session.return_value.__aenter__.return_value
            mock_session_instance.post.return_value.__aenter__.return_value = login_response

            # First call is login, second is create room
            mock_session_instance.post.return_value.__aenter__.side_effect = [
                login_response,
                create_response
            ]

            # Create space (should create new one)
            result = await agent_manager.create_letta_agents_space()

            # Should have created a new space
            assert agent_manager.space_id == new_space_id

    @pytest.mark.asyncio
    async def test_get_space_id(self, agent_manager):
        """Test getting the space ID"""
        space_id = "!test_space:matrix.oculair.ca"
        agent_manager.space_id = space_id

        result = agent_manager.get_space_id()

        assert result == space_id

    @pytest.mark.asyncio
    async def test_space_data_format(self, agent_manager):
        """Test that space configuration has correct data format"""
        space_id = "!test_space:matrix.oculair.ca"
        agent_manager.space_id = space_id

        await agent_manager.save_space_config()

        # Load and verify the saved data
        with open(agent_manager.space_config_file, 'r') as f:
            data = json.load(f)

        assert data["space_id"] == space_id
        assert data["name"] == "Letta Agents"
        assert "created_at" in data
        assert isinstance(data["created_at"], (int, float))

    @pytest.mark.asyncio
    async def test_room_to_space_bidirectional_relationship(self, agent_manager):
        """Test that bidirectional parent-child relationships are set"""
        space_id = "!test_space:matrix.oculair.ca"
        room_id = "!test_room:matrix.oculair.ca"
        room_name = "Test Agent"

        agent_manager.space_id = space_id
        agent_manager.get_admin_token = AsyncMock(return_value="test_admin_token")

        put_calls = []

        # Mock the HTTP requests to capture the calls
        with patch('aiohttp.ClientSession') as mock_session:
            async def mock_put(url, headers, json, timeout):
                mock_response = AsyncMock()
                mock_response.status = 200
                put_calls.append((url, json))
                return mock_response

            mock_session_instance = mock_session.return_value.__aenter__.return_value
            mock_session_instance.put = mock_put

            # Add room to space
            await agent_manager.add_room_to_space(room_id, room_name)

            # Verify both m.space.child and m.space.parent were set
            assert len(put_calls) == 2

            # First call should be m.space.child
            child_url, child_data = put_calls[0]
            assert f"m.space.child/{room_id}" in child_url
            assert child_data["via"] == ["matrix.oculair.ca"]
            assert child_data["suggested"] is True

            # Second call should be m.space.parent
            parent_url, parent_data = put_calls[1]
            assert f"m.space.parent/{space_id}" in parent_url
            assert parent_data["canonical"] is True

    @pytest.mark.asyncio
    async def test_sync_creates_space(self, agent_manager):
        """Test that sync_agents_to_users creates the space if it doesn't exist"""
        space_id = "!new_space:matrix.oculair.ca"

        # Mock required methods
        agent_manager.load_existing_mappings = AsyncMock()
        agent_manager.load_space_config = AsyncMock()
        agent_manager.get_letta_agents = AsyncMock(return_value=[])
        agent_manager.save_mappings = AsyncMock()
        agent_manager.create_letta_agents_space = AsyncMock(return_value=space_id)

        # Run sync
        await agent_manager.sync_agents_to_users()

        # Verify space was created
        agent_manager.create_letta_agents_space.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_room_added_to_space(self, agent_manager):
        """Test that newly created agent rooms are automatically added to space"""
        space_id = "!test_space:matrix.oculair.ca"
        room_id = "!new_room:matrix.oculair.ca"
        agent_id = "agent-test"

        agent_manager.space_id = space_id

        # Create a mapping for the agent
        mapping = AgentUserMapping(
            agent_id=agent_id,
            agent_name="Test Agent",
            matrix_user_id="@agent_test:matrix.oculair.ca",
            matrix_password="password",
            created=True,
            room_id=None,
            room_created=False
        )
        agent_manager.mappings[agent_id] = mapping

        # Mock methods
        agent_manager.save_mappings = AsyncMock()
        agent_manager.check_room_exists = AsyncMock(return_value=False)
        agent_manager.find_existing_agent_room = AsyncMock(return_value=None)
        agent_manager.auto_accept_invitations_with_tracking = AsyncMock()
        agent_manager.add_room_to_space = AsyncMock(return_value=True)

        # Mock room creation
        with patch('aiohttp.ClientSession') as mock_session:
            # Mock login response
            login_response = AsyncMock()
            login_response.status = 200
            login_response.json = AsyncMock(return_value={"access_token": "agent_token"})

            # Mock create room response
            create_response = AsyncMock()
            create_response.status = 200
            create_response.json = AsyncMock(return_value={"room_id": room_id})

            mock_session_instance = mock_session.return_value.__aenter__.return_value
            mock_session_instance.post.return_value.__aenter__.side_effect = [
                login_response,
                create_response
            ]

            # Create room
            await agent_manager.create_or_update_agent_room(agent_id)

            # Verify room was added to space
            agent_manager.add_room_to_space.assert_called_once_with(room_id, "Test Agent")


class TestSpaceEdgeCases:
    """Test edge cases and error handling"""

    @pytest.fixture
    def mock_config(self):
        return MockConfig()

    @pytest.fixture
    def agent_manager(self, mock_config):
        with patch('agent_user_manager.os.makedirs'):
            return AgentUserManager(mock_config)

    @pytest.mark.asyncio
    async def test_space_creation_failure(self, agent_manager):
        """Test handling of space creation failure"""
        # Mock login failure
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 403
            mock_response.text = AsyncMock(return_value="Forbidden")

            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response

            result = await agent_manager.create_letta_agents_space()

            assert result is None
            assert agent_manager.space_id is None

    @pytest.mark.asyncio
    async def test_add_room_to_space_failure(self, agent_manager):
        """Test handling of room addition failure"""
        space_id = "!test_space:matrix.oculair.ca"
        room_id = "!test_room:matrix.oculair.ca"

        agent_manager.space_id = space_id
        agent_manager.get_admin_token = AsyncMock(return_value="test_admin_token")

        # Mock HTTP failure
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 500
            mock_response.text = AsyncMock(return_value="Internal Server Error")

            mock_session.return_value.__aenter__.return_value.put.return_value.__aenter__.return_value = mock_response

            result = await agent_manager.add_room_to_space(room_id, "Test Agent")

            assert result is False

    @pytest.mark.asyncio
    async def test_migrate_with_no_space(self, agent_manager):
        """Test migration when no space exists"""
        agent_manager.space_id = None
        agent_manager.mappings = {
            "agent-1": AgentUserMapping(
                agent_id="agent-1",
                agent_name="Agent One",
                matrix_user_id="@agent_1:matrix.oculair.ca",
                matrix_password="password",
                created=True,
                room_id="!room1:matrix.oculair.ca",
                room_created=True
            )
        }

        migrated_count = await agent_manager.migrate_existing_rooms_to_space()

        assert migrated_count == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
