"""
Additional tests for agent_user_manager.py to reach 70% coverage threshold.

Focuses on:
- Display name updates
- Get space ID
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from src.core.agent_user_manager import AgentUserManager


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
class TestUpdateDisplayName:
    """Test user display name updates"""

    @pytest.mark.asyncio
    async def test_update_display_name_success(self, mock_config, mock_aiohttp_session):
        """Test successfully updating display name by logging in as user"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock successful login response
                mock_login_response = AsyncMock()
                mock_login_response.status = 200
                mock_login_response.json = AsyncMock(return_value={"access_token": "user_token_123"})
                mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
                mock_login_response.__aexit__ = AsyncMock(return_value=None)

                # Mock successful profile update response
                mock_profile_response = AsyncMock()
                mock_profile_response.status = 200
                mock_profile_response.__aenter__ = AsyncMock(return_value=mock_profile_response)
                mock_profile_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_login_response)
                mock_aiohttp_session.put = Mock(return_value=mock_profile_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    success = await manager.update_display_name(
                        "@agent_123:matrix.oculair.ca",
                        "New Agent Name",
                        "agent_password"
                    )

                    assert success is True

    @pytest.mark.asyncio
    async def test_update_display_name_no_password(self, mock_config):
        """Test display name update fails when no password provided"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                success = await manager.update_display_name(
                    "@agent_123:matrix.oculair.ca",
                    "New Name"
                )

                assert success is False


@pytest.mark.unit
class TestGetSpaceId:
    """Test getting space ID"""

    def test_get_space_id_returns_current_id(self, mock_config):
        """Test that get_space_id returns the current space ID"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_id = "!test_space:matrix.oculair.ca"

                space_id = manager.space_manager.get_space_id()

                assert space_id == "!test_space:matrix.oculair.ca"

    def test_get_space_id_returns_none_when_not_set(self, mock_config):
        """Test that get_space_id returns None when space not created"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                space_id = manager.space_manager.get_space_id()

                assert space_id is None


@pytest.mark.unit
class TestUpdateDisplayNameFailure:
    """Test display name update error handling"""

    @pytest.mark.asyncio
    async def test_update_display_name_login_failure(self, mock_config, mock_aiohttp_session):
        """Test display name update when login fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock failed login response
                mock_login_response = AsyncMock()
                mock_login_response.status = 403
                mock_login_response.text = AsyncMock(return_value="Wrong password")
                mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
                mock_login_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.post = Mock(return_value=mock_login_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    success = await manager.update_display_name(
                        "@agent_123:matrix.oculair.ca",
                        "New Name",
                        "wrong_password"
                    )

                    assert success is False
