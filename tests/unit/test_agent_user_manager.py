"""
Unit tests for agent_user_manager.py

Tests cover:
- Agent discovery from Letta API
- Matrix user creation for agents
- Room creation and management
- Mapping persistence
- Admin token management
- Name update handling
"""
import pytest
import json
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import asdict

# Import the module to test
from src.core.agent_user_manager import (
    AgentUserManager,
    AgentUserMapping,
    get_global_session
)


# ============================================================================
# AgentUserMapping Tests
# ============================================================================

class TestAgentUserMapping:
    """Test the AgentUserMapping dataclass"""

    def test_mapping_creation(self):
        """Test creating an agent mapping"""
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="TestAgent",
            matrix_user_id="@agent_123:matrix.test",
            matrix_password="test_pass",
            created=True,
            room_id="!room123:matrix.test",
            room_created=True,
            invitation_status={"@admin:matrix.test": "joined"}
        )

        assert mapping.agent_id == "agent-123"
        assert mapping.agent_name == "TestAgent"
        assert mapping.matrix_user_id == "@agent_123:matrix.test"
        assert mapping.created is True
        assert mapping.room_created is True
        assert mapping.invitation_status["@admin:matrix.test"] == "joined"

    def test_mapping_defaults(self):
        """Test default values in mapping"""
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="TestAgent",
            matrix_user_id="@agent_123:matrix.test",
            matrix_password="test_pass"
        )

        assert mapping.created is False
        assert mapping.room_id is None
        assert mapping.room_created is False
        assert mapping.invitation_status is None


# ============================================================================
# AgentUserManager Initialization Tests
# ============================================================================

class TestAgentUserManagerInit:
    """Test AgentUserManager initialization"""

    def test_init_with_config(self, mock_config):
        """Test initialization with configuration"""
        manager = AgentUserManager(mock_config)

        assert manager.config == mock_config
        assert manager.homeserver_url == mock_config.homeserver_url
        assert manager.letta_token == mock_config.letta_token
        assert manager.letta_api_url == mock_config.letta_api_url
        assert manager.mappings == {}
        assert manager.admin_token is None

    @patch.dict(os.environ, {"MATRIX_ADMIN_USERNAME": "@custom_admin:test", "MATRIX_ADMIN_PASSWORD": "custom_pass"})
    def test_init_with_custom_admin(self, mock_config):
        """Test initialization with custom admin credentials"""
        manager = AgentUserManager(mock_config)

        assert manager.admin_username == "@custom_admin:test"
        assert manager.admin_password == "custom_pass"

    def test_init_creates_data_directory(self, mock_config):
        """Test that initialization creates data directory"""
        with patch('os.makedirs') as mock_makedirs:
            manager = AgentUserManager(mock_config)
            mock_makedirs.assert_called_once_with("/app/data", exist_ok=True)


# ============================================================================
# Mapping Persistence Tests
# ============================================================================

class TestMappingPersistence:
    """Test loading and saving agent mappings"""

    @pytest.mark.asyncio
    async def test_load_existing_mappings_success(self, mock_config, tmp_path):
        """Test successfully loading mappings from file"""
        # Create test mappings file
        mappings_file = tmp_path / "agent_user_mappings.json"
        test_data = {
            "agent-001": {
                "agent_id": "agent-001",
                "agent_name": "TestAgent1",
                "matrix_user_id": "@agent_001:matrix.test",
                "matrix_password": "pass1",
                "created": True,
                "room_id": "!room001:matrix.test",
                "room_created": True,
                "invitation_status": {}
            }
        }

        with open(mappings_file, 'w') as f:
            json.dump(test_data, f)

        # Patch the mappings file path
        manager = AgentUserManager(mock_config)
        manager.mappings_file = str(mappings_file)

        await manager.load_existing_mappings()

        assert len(manager.mappings) == 1
        assert "agent-001" in manager.mappings
        assert manager.mappings["agent-001"].agent_name == "TestAgent1"
        assert manager.mappings["agent-001"].created is True

    @pytest.mark.asyncio
    async def test_load_mappings_backward_compatibility(self, mock_config, tmp_path):
        """Test loading old mappings without invitation_status field"""
        mappings_file = tmp_path / "agent_user_mappings.json"
        test_data = {
            "agent-001": {
                "agent_id": "agent-001",
                "agent_name": "OldAgent",
                "matrix_user_id": "@agent_001:matrix.test",
                "matrix_password": "pass1",
                "created": True,
                "room_id": "!room001:matrix.test",
                "room_created": True
                # No invitation_status field
            }
        }

        with open(mappings_file, 'w') as f:
            json.dump(test_data, f)

        manager = AgentUserManager(mock_config)
        manager.mappings_file = str(mappings_file)

        await manager.load_existing_mappings()

        assert manager.mappings["agent-001"].invitation_status is None

    @pytest.mark.asyncio
    async def test_load_mappings_file_not_found(self, mock_config, tmp_path):
        """Test loading when mappings file doesn't exist"""
        manager = AgentUserManager(mock_config)
        manager.mappings_file = str(tmp_path / "nonexistent.json")

        await manager.load_existing_mappings()

        assert len(manager.mappings) == 0

    @pytest.mark.asyncio
    async def test_save_mappings_success(self, mock_config, tmp_path):
        """Test successfully saving mappings to file"""
        mappings_file = tmp_path / "agent_user_mappings.json"

        manager = AgentUserManager(mock_config)
        manager.mappings_file = str(mappings_file)

        # Add a mapping
        manager.mappings["agent-001"] = AgentUserMapping(
            agent_id="agent-001",
            agent_name="SaveTest",
            matrix_user_id="@agent_001:matrix.test",
            matrix_password="pass1",
            created=True,
            room_id="!room001:matrix.test",
            room_created=True
        )

        await manager.save_mappings()

        # Verify file was created and contains correct data
        assert mappings_file.exists()

        with open(mappings_file, 'r') as f:
            saved_data = json.load(f)

        assert "agent-001" in saved_data
        assert saved_data["agent-001"]["agent_name"] == "SaveTest"


# ============================================================================
# Admin Token Tests
# ============================================================================

class TestAdminToken:
    """Test admin token retrieval"""

    @pytest.mark.asyncio
    async def test_get_admin_token_success(self, mock_config, mock_aiohttp_session):
        """Test successfully getting admin token"""
        # Mock the session and response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"access_token": "admin_token_123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.post = Mock(return_value=mock_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

        manager = AgentUserManager(mock_config)

        # Patch aiohttp.ClientSession which is used by get_admin_token
        with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
            token = await manager.get_admin_token()

        assert token == "admin_token_123"
        assert manager.admin_token == "admin_token_123"

    @pytest.mark.asyncio
    async def test_get_admin_token_cached(self, mock_config):
        """Test that admin token is cached and not re-fetched"""
        manager = AgentUserManager(mock_config)
        manager.admin_token = "cached_token"

        # Should return cached token without making any requests
        token = await manager.get_admin_token()

        assert token == "cached_token"

    @pytest.mark.asyncio
    async def test_get_admin_token_failure(self, mock_config, mock_aiohttp_session):
        """Test handling of failed admin token retrieval"""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.text = AsyncMock(return_value="Unauthorized")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.post = Mock(return_value=mock_response)

        manager = AgentUserManager(mock_config)

        with patch('src.core.agent_user_manager.get_global_session', return_value=mock_aiohttp_session):
            token = await manager.get_admin_token()

        assert token is None


# ============================================================================
# Agent Discovery Tests
# ============================================================================

class TestAgentDiscovery:
    """Test Letta agent discovery"""

    @pytest.mark.asyncio
    async def test_get_letta_agents_success(self, mock_config, mock_aiohttp_session):
        """Test successfully retrieving Letta agents"""
        # Mock the /v1/agents endpoint response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "data": [
                {"id": "agent-001", "name": "Agent 1"},
                {"id": "agent-002", "name": "Agent 2"},
                {"id": "agent-003", "name": "Agent 3"}
            ]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.get = Mock(return_value=mock_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

        manager = AgentUserManager(mock_config)

        # Patch aiohttp.ClientSession to return our mock session
        with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
            agents = await manager.get_letta_agents()

        assert len(agents) == 3
        assert agents[0]["id"] == "agent-001"
        assert agents[2]["id"] == "agent-003"

    @pytest.mark.asyncio
    async def test_get_letta_agents_with_details(self, mock_config, mock_aiohttp_session):
        """Test retrieving agents with detailed information"""
        # Mock agent list response with name included
        list_response = AsyncMock()
        list_response.status = 200
        list_response.json = AsyncMock(return_value={
            "data": [{"id": "agent-001", "name": "DetailedAgent"}]
        })
        list_response.__aenter__ = AsyncMock(return_value=list_response)
        list_response.__aexit__ = AsyncMock(return_value=None)

        # Setup mock session
        mock_aiohttp_session.get = Mock(return_value=list_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

        manager = AgentUserManager(mock_config)

        with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
            agents = await manager.get_letta_agents()

        assert len(agents) == 1
        assert agents[0]["name"] == "DetailedAgent"

    @pytest.mark.asyncio
    async def test_get_letta_agents_empty_list(self, mock_config, mock_aiohttp_session):
        """Test when no agents are available"""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"data": []})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.get = Mock(return_value=mock_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

        manager = AgentUserManager(mock_config)

        with patch('aiohttp.ClientSession', return_value=mock_aiohttp_session):
            agents = await manager.get_letta_agents()

        assert len(agents) == 0

    @pytest.mark.asyncio
    async def test_get_letta_agents_api_error(self, mock_config, mock_aiohttp_session):
        """Test handling of Letta API errors"""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.get = Mock(return_value=mock_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

        manager = AgentUserManager(mock_config)

        with patch('aiohttp.ClientSession', return_value=mock_aiohttp_session):
            agents = await manager.get_letta_agents()

        assert agents == []


# ============================================================================
# Username Generation Tests
# ============================================================================

class TestUsernameGeneration:
    """Test Matrix username generation for agents"""

    def test_generate_matrix_username(self, mock_config):
        """Test username generation from agent ID"""
        manager = AgentUserManager(mock_config)

        # Test with standard UUID format
        agent_name = "TestAgent"
        agent_id = "agent-12345678-1234-1234-1234-123456789abc"
        username = manager.generate_username(agent_name, agent_id)

        # Should convert hyphens to underscores and use agent ID
        assert username.startswith("agent_")
        assert "12345678_1234" in username
        # Note: generate_username returns just the localpart without @, domain

    def test_generate_matrix_username_stability(self, mock_config):
        """Test that username generation is stable across renames"""
        manager = AgentUserManager(mock_config)

        agent_id = "agent-001"
        agent_name_v1 = "OriginalName"
        agent_name_v2 = "NewName"

        username1 = manager.generate_username(agent_name_v1, agent_id)
        username2 = manager.generate_username(agent_name_v2, agent_id)

        # Username should be the same despite name change
        assert username1 == username2


# ============================================================================
# User Creation Tests
# ============================================================================

@pytest.mark.unit
class TestUserCreation:
    """Test Matrix user creation for agents"""

    @pytest.mark.asyncio
    async def test_create_user_for_agent_success(self, mock_config, mock_aiohttp_session):
        """Test successfully creating a Matrix user for an agent"""
        # Mock user creation response
        create_response = AsyncMock()
        create_response.status = 200
        create_response.json = AsyncMock(return_value={
            "access_token": "new_user_token",
            "user_id": "@agent_123:matrix.oculair.ca"
        })
        create_response.__aenter__ = AsyncMock(return_value=create_response)
        create_response.__aexit__ = AsyncMock(return_value=None)

        # Mock display name update response
        display_name_response = AsyncMock()
        display_name_response.status = 200
        display_name_response.__aenter__ = AsyncMock(return_value=display_name_response)
        display_name_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.post = Mock(return_value=create_response)
        mock_aiohttp_session.put = Mock(return_value=display_name_response)
        mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
        mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

        manager = AgentUserManager(mock_config)
        agent = {"id": "agent-123", "name": "NewAgent"}

        # Patch aiohttp.ClientSession to return our mock session
        with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
            await manager.create_user_for_agent(agent)

        # Verify mapping was created
        assert "agent-123" in manager.mappings
        assert manager.mappings["agent-123"].agent_name == "NewAgent"
        assert manager.mappings["agent-123"].created is True

    @pytest.mark.asyncio
    async def test_create_user_already_exists(self, mock_config):
        """Test that existing users are not recreated"""
        manager = AgentUserManager(mock_config)

        # Pre-populate mapping
        manager.mappings["agent-123"] = AgentUserMapping(
            agent_id="agent-123",
            agent_name="ExistingAgent",
            matrix_user_id="@agent_123:matrix.test",
            matrix_password="existing_pass",
            created=True
        )

        agent = {"id": "agent-123", "name": "ExistingAgent"}

        # Should not make any API calls
        with patch('src.core.agent_user_manager.get_global_session') as mock_session:
            await manager.create_user_for_agent(agent)
            # Verify no session was created
            mock_session.assert_not_called()


# ============================================================================
# Mark tests with appropriate markers
# ============================================================================

pytest.mark.unit(TestAgentUserMapping)
pytest.mark.unit(TestAgentUserManagerInit)
pytest.mark.unit(TestMappingPersistence)
pytest.mark.unit(TestAdminToken)
pytest.mark.unit(TestAgentDiscovery)
pytest.mark.unit(TestUsernameGeneration)
