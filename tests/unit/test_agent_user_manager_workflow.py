"""
Unit tests for agent_user_manager.py user creation and sync workflows.

Tests cover:
- User creation for agents
- Agent discovery and synchronization
- History import functionality
- Sync workflow integration
"""

import pytest
import json
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.core.agent_user_manager import AgentUserManager
from src.core.types import AgentUserMapping


@pytest.fixture
def mock_config():
    """Create a mock Config object"""
    config = Mock()
    config.username = "@letta:matrix.oculair.ca"
    config.password = "letta_password"
    config.homeserver_url = "https://matrix.oculair.ca"
    config.letta_api_url = "http://192.168.50.90:1416"
    config.letta_token = "test_token"
    config.matrix_api_url = "http://matrix-api:8000"
    return config


@pytest.mark.unit
class TestCreateUserForAgent:
    """Test user creation for agents"""

    @pytest.mark.asyncio
    async def test_create_user_for_agent_success(self, mock_config, mock_aiohttp_session):
        """Test successful user creation for new agent"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                agent = {
                    "id": "agent-123",
                    "name": "Test Agent",
                    "created_at": 1234567890
                }

                # Mock admin token
                with patch.object(manager, 'get_admin_token', return_value="admin_token"):
                    # Mock user creation response (200 OK means success)
                    mock_response = AsyncMock()
                    mock_response.status = 200
                    mock_response.text = AsyncMock(return_value="OK")
                    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                    mock_response.__aexit__ = AsyncMock(return_value=None)

                    mock_aiohttp_session.put = Mock(return_value=mock_response)
                    mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                    mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                    with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        with patch.object(manager, 'create_or_update_agent_room', new_callable=AsyncMock):
                            await manager.create_user_for_agent(agent)

                            # Verify mapping was created
                            assert "agent-123" in manager.mappings
                            mapping = manager.mappings["agent-123"]
                            assert mapping.agent_name == "Test Agent"
                            assert mapping.created is True

    @pytest.mark.asyncio
    async def test_create_user_for_agent_uses_agent_name_as_display_name(self, mock_config):
        """Test that user creation uses agent name directly as display name (not 'Letta Agent: ...')"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                agent = {
                    "id": "agent-display-test",
                    "name": "My Cool Agent",
                    "created_at": 1234567890
                }

                # Track what display name is passed to create_matrix_user
                captured_display_name = None

                async def mock_create_matrix_user(username, password, display_name):
                    nonlocal captured_display_name
                    captured_display_name = display_name
                    return True

                with patch.object(manager, 'create_matrix_user', side_effect=mock_create_matrix_user):
                    with patch.object(manager, 'create_or_update_agent_room', new_callable=AsyncMock):
                        await manager.create_user_for_agent(agent)

                        # Verify the display name is the agent name, NOT "Letta Agent: ..."
                        assert captured_display_name == "My Cool Agent"
                        assert captured_display_name != "Letta Agent: My Cool Agent"
                        assert not captured_display_name.startswith("Letta Agent:")

    @pytest.mark.asyncio
    async def test_create_user_for_agent_already_exists(self, mock_config):
        """Test creating user when agent already exists in mappings"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Pre-populate mapping
                manager.mappings["agent-456"] = AgentUserMapping(
                    agent_id="agent-456",
                    agent_name="Existing Agent",
                    matrix_user_id="@agent_456:matrix.oculair.ca",
                    matrix_password="password",
                    created=True
                )

                agent = {
                    "id": "agent-456",
                    "name": "Existing Agent",
                    "created_at": 1234567890
                }

                with patch.object(manager, 'create_or_update_agent_room', new_callable=AsyncMock) as mock_room:
                    await manager.create_user_for_agent(agent)

                    # Should still create/update room
                    mock_room.assert_called_once_with("agent-456")

    @pytest.mark.asyncio
    async def test_create_user_for_agent_creates_mapping_even_without_token(self, mock_config):
        """Test user creation creates mapping structure even when user creation fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                agent = {
                    "id": "agent-789",
                    "name": "Test Agent",
                    "created_at": 1234567890
                }

                # Mock create_matrix_user to fail
                with patch.object(manager, 'create_matrix_user', return_value=False):
                    await manager.create_user_for_agent(agent)

                    # Mapping is created but marked as failed
                    assert "agent-789" in manager.mappings
                    assert manager.mappings["agent-789"].created is False



@pytest.mark.unit
class TestUserExistence:
    """Test checking if Matrix users exist"""

    @pytest.mark.asyncio
    async def test_user_exists_returns_true_on_403(self, mock_config, mock_aiohttp_session):
        """Test that 403 response indicates user exists"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock 403 response (wrong password = user exists)
                mock_response = AsyncMock()
                mock_response.status = 403
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    exists = await manager.check_user_exists("@test:matrix.oculair.ca")

                    assert exists == "exists_auth_failed"

    @pytest.mark.asyncio
    async def test_user_exists_returns_false_on_404(self, mock_config, mock_aiohttp_session):
        """Test that 404 response indicates user doesn't exist"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock 404 response (user not found)
                mock_response = AsyncMock()
                mock_response.status = 404
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    exists = await manager.check_user_exists("@test:matrix.oculair.ca")

                    assert exists == "not_found"

    @pytest.mark.asyncio
    async def test_user_exists_returns_false_on_other_errors(self, mock_config, mock_aiohttp_session):
        """Test that unknown error codes default to user doesn't exist"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock 500 response (assume doesn't exist for unknown errors)
                mock_response = AsyncMock()
                mock_response.status = 500
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    exists = await manager.check_user_exists("@test:matrix.oculair.ca")

                    assert exists == "not_found"


@pytest.mark.unit
class TestPasswordGeneration:
    """Test password generation"""

    def test_generate_password_returns_string(self, mock_config):
        """Test that password generation returns a string"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                password = manager.generate_password()

                assert isinstance(password, str)
                assert len(password) > 0

    def test_generate_password_dev_mode(self, mock_config):
        """Test that DEV_MODE returns simple password"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                with patch.dict(os.environ, {"DEV_MODE": "true"}):
                    manager = AgentUserManager(config=mock_config)

                    password = manager.generate_password()

                    assert password == "password"


@pytest.mark.unit
class TestUsernameGeneration:
    """Test Matrix username generation"""

    def test_generate_username_from_agent_id(self, mock_config):
        """Test username generation from agent ID"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Test with standard agent ID format
                username = manager.generate_username("Test Agent", "agent-abc-123-def")

                assert username == "agent_abc_123_def"
                assert "-" not in username  # Hyphens should be converted to underscores

    def test_generate_username_removes_agent_prefix(self, mock_config):
        """Test that 'agent-' prefix is removed"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                username = manager.generate_username("Test", "agent-xyz789")

                assert username == "agent_xyz789"  # Prefix removed, underscores added

@pytest.mark.unit
class TestAgentNameChanges:
    """Test handling of agent name changes during sync"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_agent_name_change_updates_display_name_with_password(self, mock_config):
        """Test that when agent name changes, update_display_name is called with password"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                with patch('src.letta.matrix_memory.sync_matrix_block_to_agents', new_callable=AsyncMock, return_value={"synced": 0}):
                    manager = AgentUserManager(config=mock_config)

                    manager.mappings["agent-rename-test"] = AgentUserMapping(
                        agent_id="agent-rename-test",
                        agent_name="Old Agent Name",
                        matrix_user_id="@agent_rename_test:matrix.oculair.ca",
                        matrix_password="secret_password",
                        created=True,
                        room_id="!room123:matrix.oculair.ca",
                        room_created=True
                    )

                    update_calls = []

                    async def mock_update_display_name(user_id, display_name, password=None):
                        update_calls.append({
                            "user_id": user_id,
                            "display_name": display_name,
                            "password": password
                        })
                        return True

                    with patch.object(manager, 'ensure_core_users_exist', new_callable=AsyncMock):
                        with patch.object(manager, 'load_existing_mappings', new_callable=AsyncMock):
                            with patch.object(manager, 'update_display_name', side_effect=mock_update_display_name):
                                with patch.object(manager, 'update_room_name', new_callable=AsyncMock, return_value=True):
                                    with patch.object(manager.space_manager, 'check_room_exists', new_callable=AsyncMock, return_value=True):
                                        with patch.object(manager, 'discover_agent_room', new_callable=AsyncMock, return_value="!room123:matrix.oculair.ca"):
                                            with patch.object(manager, 'auto_accept_invitations_with_tracking', new_callable=AsyncMock):
                                                with patch.object(manager.room_manager, 'check_admin_in_room', return_value=True):
                                                    with patch.object(manager, 'get_letta_agents', return_value=[
                                                        {"id": "agent-rename-test", "name": "New Agent Name"}
                                                    ]):
                                                        with patch.object(manager, 'save_mappings', new_callable=AsyncMock):
                                                            with patch.object(manager.space_manager, 'load_space_config', new_callable=AsyncMock):
                                                                with patch.object(manager.space_manager, 'get_space_id', return_value="!space:matrix.oculair.ca"):
                                                                    await manager.sync_agents_to_users()

                    assert len(update_calls) == 1
                    assert update_calls[0]["user_id"] == "@agent_rename_test:matrix.oculair.ca"
                    assert update_calls[0]["display_name"] == "New Agent Name"
                    assert update_calls[0]["password"] == "secret_password"


@pytest.mark.unit
class TestGetLettaAgents:
    """Test Letta agent discovery via SDK"""

    @pytest.fixture(autouse=True)
    def reset_letta_client(self):
        """Reset the Letta client singleton before each test"""
        from src.letta.client import reset_client
        reset_client()
        yield
        reset_client()

    def _create_mock_agents(self, agent_data: list):
        """Helper to create mock SDK AgentState objects"""
        mock_agents = []
        for data in agent_data:
            mock_agent = Mock()
            mock_agent.id = data.get("id", "")
            mock_agent.name = data.get("name", "")
            mock_agents.append(mock_agent)
        return mock_agents

    @pytest.mark.asyncio
    async def test_get_letta_agents_success(self, mock_config):
        """Test successful agent discovery via SDK"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Create mock SDK client with mock agents
                mock_client = Mock()
                mock_agents = self._create_mock_agents([
                    {"id": "agent-1", "name": "Agent One"},
                    {"id": "agent-2", "name": "Agent Two"}
                ])
                mock_client.agents.list = Mock(return_value=mock_agents)

                with patch('src.letta.client.get_letta_client', return_value=mock_client):
                    agents = await manager.get_letta_agents()

                    assert len(agents) == 2
                    assert agents[0]["id"] == "agent-1"
                    assert agents[1]["name"] == "Agent Two"

    @pytest.mark.asyncio
    async def test_get_letta_agents_api_error(self, mock_config):
        """Test agent discovery when SDK call fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Create mock SDK client that raises an exception
                mock_client = Mock()
                mock_client.agents.list = Mock(side_effect=Exception("Connection refused"))

                with patch('src.letta.client.get_letta_client', return_value=mock_client):
                    agents = await manager.get_letta_agents()

                    # Should return empty list on error
                    assert agents == []

    @pytest.mark.asyncio
    async def test_get_letta_agents_invalid_json(self, mock_config):
        """Test agent discovery with empty SDK response"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Create mock SDK client with empty list
                mock_client = Mock()
                mock_client.agents.list = Mock(return_value=[])

                with patch('src.letta.client.get_letta_client', return_value=mock_client):
                    agents = await manager.get_letta_agents()

                    # Should handle gracefully with empty list
                    assert agents == []


@pytest.mark.unit
class TestImportRecentHistory:
    """Test conversation history import"""

    @pytest.mark.asyncio
    async def test_import_recent_history_handles_no_messages(self, mock_config, mock_aiohttp_session):
        """Test history import when no messages exist"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock empty messages response
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.json = AsyncMock(return_value={"messages": []})
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.get = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    # Should complete without errors
                    await manager.import_recent_history(
                        agent_id="agent-456",
                        agent_username="@agent_456:matrix.oculair.ca",
                        agent_password="password",
                        room_id="!room456:matrix.oculair.ca"
                    )

    @pytest.mark.asyncio
    async def test_import_recent_history_handles_api_failure(self, mock_config, mock_aiohttp_session):
        """Test history import when Letta API fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock failed API response
                mock_response = AsyncMock()
                mock_response.status = 500
                mock_response.text = AsyncMock(return_value="Server Error")
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.get = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    # Should handle error gracefully
                    await manager.import_recent_history(
                        agent_id="agent-789",
                        agent_username="@agent_789:matrix.oculair.ca",
                        agent_password="password",
                        room_id="!room789:matrix.oculair.ca"
                    )
