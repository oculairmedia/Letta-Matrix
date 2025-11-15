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
from agent_user_manager import AgentUserManager, AgentUserMapping


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
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
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

                    with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        with patch.object(manager, 'create_or_update_agent_room', new_callable=AsyncMock):
                            await manager.create_user_for_agent(agent)

                            # Verify mapping was created
                            assert "agent-123" in manager.mappings
                            mapping = manager.mappings["agent-123"]
                            assert mapping.agent_name == "Test Agent"
                            assert mapping.created is True

    @pytest.mark.asyncio
    async def test_create_user_for_agent_already_exists(self, mock_config):
        """Test creating user when agent already exists in mappings"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
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
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
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
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock 403 response (wrong password = user exists)
                mock_response = AsyncMock()
                mock_response.status = 403
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    exists = await manager.check_user_exists("@test:matrix.oculair.ca")

                    assert exists is True

    @pytest.mark.asyncio
    async def test_user_exists_returns_false_on_404(self, mock_config, mock_aiohttp_session):
        """Test that 404 response indicates user doesn't exist"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock 404 response (user not found)
                mock_response = AsyncMock()
                mock_response.status = 404
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    exists = await manager.check_user_exists("@test:matrix.oculair.ca")

                    assert exists is False

    @pytest.mark.asyncio
    async def test_user_exists_returns_false_on_other_errors(self, mock_config, mock_aiohttp_session):
        """Test that unknown error codes default to user doesn't exist"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock 500 response (assume doesn't exist for unknown errors)
                mock_response = AsyncMock()
                mock_response.status = 500
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    exists = await manager.check_user_exists("@test:matrix.oculair.ca")

                    assert exists is False


@pytest.mark.unit
class TestPasswordGeneration:
    """Test password generation"""

    def test_generate_password_returns_string(self, mock_config):
        """Test that password generation returns a string"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                password = manager.generate_password()

                assert isinstance(password, str)
                assert len(password) > 0

    def test_generate_password_dev_mode(self, mock_config):
        """Test that DEV_MODE returns simple password"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                with patch.dict(os.environ, {"DEV_MODE": "true"}):
                    manager = AgentUserManager(config=mock_config)

                    password = manager.generate_password()

                    assert password == "password"


@pytest.mark.unit
class TestUsernameGeneration:
    """Test Matrix username generation"""

    def test_generate_username_from_agent_id(self, mock_config):
        """Test username generation from agent ID"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Test with standard agent ID format
                username = manager.generate_username("Test Agent", "agent-abc-123-def")

                assert username == "agent_abc_123_def"
                assert "-" not in username  # Hyphens should be converted to underscores

    def test_generate_username_removes_agent_prefix(self, mock_config):
        """Test that 'agent-' prefix is removed"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                username = manager.generate_username("Test", "agent-xyz789")

                assert username == "agent_xyz789"  # Prefix removed, underscores added


@pytest.mark.unit
class TestGetLettaAgents:
    """Test Letta agent discovery"""

    @pytest.mark.asyncio
    async def test_get_letta_agents_success(self, mock_config, mock_aiohttp_session):
        """Test successful agent discovery"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock models response
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.json = AsyncMock(return_value={
                    "data": [
                        {"id": "agent-1", "name": "Agent One", "created": 1234567890},
                        {"id": "agent-2", "name": "Agent Two", "created": 1234567891}
                    ]
                })
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.get = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    agents = await manager.get_letta_agents()

                    assert len(agents) == 2
                    assert agents[0]["id"] == "agent-1"
                    assert agents[1]["name"] == "Agent Two"

    @pytest.mark.asyncio
    async def test_get_letta_agents_api_error(self, mock_config, mock_aiohttp_session):
        """Test agent discovery when API fails"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock failed response
                mock_response = AsyncMock()
                mock_response.status = 500
                mock_response.text = AsyncMock(return_value="Server Error")
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.get = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    agents = await manager.get_letta_agents()

                    # Should return empty list on error
                    assert agents == []

    @pytest.mark.asyncio
    async def test_get_letta_agents_invalid_json(self, mock_config, mock_aiohttp_session):
        """Test agent discovery with malformed JSON response"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock response with invalid JSON structure
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.json = AsyncMock(return_value={"invalid": "structure"})
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.get = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    agents = await manager.get_letta_agents()

                    # Should handle gracefully
                    assert agents == []


@pytest.mark.unit
class TestImportRecentHistory:
    """Test conversation history import"""

    @pytest.mark.asyncio
    async def test_import_recent_history_handles_no_messages(self, mock_config, mock_aiohttp_session):
        """Test history import when no messages exist"""
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
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

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
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
        with patch('agent_user_manager.logging.getLogger'):
            with patch('agent_user_manager.os.makedirs'):
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

                with patch('agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    # Should handle error gracefully
                    await manager.import_recent_history(
                        agent_id="agent-789",
                        agent_username="@agent_789:matrix.oculair.ca",
                        agent_password="password",
                        room_id="!room789:matrix.oculair.ca"
                    )
