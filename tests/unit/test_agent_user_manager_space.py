"""
Unit tests for agent_user_manager.py space management functions.

Tests cover:
- Letta Agents space creation and management
- Room-to-space hierarchy management
- Space configuration persistence
- Room existence checking
- Room name updates
"""

import pytest
import json
import os
import tempfile
from unittest.mock import Mock, AsyncMock, patch, mock_open, MagicMock
from src.core.agent_user_manager import AgentUserManager, AgentUserMapping


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


@pytest.fixture
def temp_space_config_file():
    """Create a temporary space config file"""
    fd, path = tempfile.mkstemp(suffix='.json')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def manager_with_space(mock_config):
    """Create an AgentUserManager with mocked dependencies and space ID"""
    with patch('src.core.agent_user_manager.logging.getLogger'):
        with patch('src.core.space_manager.logging.getLogger'):
            with patch.dict(os.environ, {
                "MATRIX_ADMIN_USERNAME": "@matrixadmin:matrix.oculair.ca",
                "MATRIX_ADMIN_PASSWORD": "admin123"
            }):
                with patch('src.core.agent_user_manager.os.makedirs'):  # Prevent creating /app/data
                    with patch('src.core.space_manager.os.makedirs'):  # Prevent creating /app/data for space_manager
                        manager = AgentUserManager(config=mock_config)
                        manager.space_manager.space_id = "!test_space_123:matrix.oculair.ca"
                        return manager


@pytest.mark.unit
class TestSpaceConfigPersistence:
    """Test space configuration save/load operations"""

    @pytest.mark.asyncio
    async def test_save_space_config_success(self, mock_config, temp_space_config_file):
        """Test successful space config save"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_config_file = temp_space_config_file
                manager.space_manager.space_id = "!test_space_456:matrix.oculair.ca"

                await manager.space_manager.save_space_config()

                # Verify file was written with correct data
                assert os.path.exists(temp_space_config_file)
                with open(temp_space_config_file, 'r') as f:
                    data = json.load(f)
                    assert data["space_id"] == "!test_space_456:matrix.oculair.ca"
                    assert data["name"] == "Letta Agents"
                    assert "created_at" in data

    @pytest.mark.asyncio
    async def test_load_space_config_success(self, mock_config, temp_space_config_file):
        """Test successful space config load"""
        # Write test data to file
        test_data = {
            "space_id": "!loaded_space_789:matrix.oculair.ca",
            "created_at": 1234567890.0,
            "name": "Letta Agents"
        }
        with open(temp_space_config_file, 'w') as f:
            json.dump(test_data, f)

        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_config_file = temp_space_config_file

                await manager.space_manager.load_space_config()

                assert manager.space_manager.space_id == "!loaded_space_789:matrix.oculair.ca"

    @pytest.mark.asyncio
    async def test_load_space_config_file_not_found(self, mock_config):
        """Test loading when space config file doesn't exist"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_config_file = "/nonexistent/path/space_config.json"

                await manager.space_manager.load_space_config()

                # Should not crash, space_id should remain None
                assert manager.space_manager.space_id is None

    @pytest.mark.asyncio
    async def test_save_space_config_handles_exception(self, mock_config):
        """Test that save_space_config handles exceptions gracefully"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_config_file = "/invalid/path/space_config.json"
                manager.space_manager.space_id = "!test_space:matrix.oculair.ca"

                # Should not raise exception
                await manager.space_manager.save_space_config()


@pytest.mark.unit
class TestCreateLettaAgentsSpace:
    """Test Letta Agents space creation"""

    @pytest.mark.asyncio
    async def test_create_space_success(self, mock_config, mock_aiohttp_session):
        """Test successful space creation"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Mock login response
                mock_login_response = AsyncMock()
                mock_login_response.status = 200
                mock_login_response.json = AsyncMock(return_value={"access_token": "test_admin_token_123"})
                mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
                mock_login_response.__aexit__ = AsyncMock(return_value=None)

                # Mock space creation response
                mock_create_response = AsyncMock()
                mock_create_response.status = 200
                mock_create_response.json = AsyncMock(return_value={"room_id": "!new_space_123:matrix.oculair.ca"})
                mock_create_response.__aenter__ = AsyncMock(return_value=mock_create_response)
                mock_create_response.__aexit__ = AsyncMock(return_value=None)

                # Set up session to return different responses for each post call
                mock_aiohttp_session.post = Mock(side_effect=[mock_login_response, mock_create_response])
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        with patch.object(manager.space_manager, 'save_space_config', new_callable=AsyncMock) as mock_save:
                            space_id = await manager.space_manager.create_letta_agents_space()

                            assert space_id == "!new_space_123:matrix.oculair.ca"
                            assert manager.space_manager.space_id == "!new_space_123:matrix.oculair.ca"
                            mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_space_already_exists(self, mock_config):
        """Test when space already exists"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
            manager.space_manager.space_id = "!existing_space:matrix.oculair.ca"

            with patch.object(manager.space_manager, 'check_room_exists', return_value=True) as mock_check:
                space_id = await manager.space_manager.create_letta_agents_space()

                assert space_id == "!existing_space:matrix.oculair.ca"
                mock_check.assert_called_once_with("!existing_space:matrix.oculair.ca")

    @pytest.mark.asyncio
    async def test_create_space_login_failure(self, mock_config):
        """Test space creation when admin login fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

            # Mock failed login
            mock_session = AsyncMock()
            mock_login_response = AsyncMock()
            mock_login_response.status = 401
            mock_login_response.text = AsyncMock(return_value="Unauthorized")

            mock_session.post = AsyncMock(return_value=mock_login_response.__aenter__.return_value)
            mock_login_response.__aenter__.return_value = mock_login_response
            mock_login_response.__aexit__.return_value = AsyncMock()

            with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_session):
                with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_session):
                    mock_session.__aenter__.return_value = mock_session
                    mock_session.__aexit__.return_value = AsyncMock()

                    space_id = await manager.space_manager.create_letta_agents_space()

                    assert space_id is None

    @pytest.mark.asyncio
    async def test_create_space_creation_failure(self, mock_config):
        """Test when Matrix API fails to create space"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

            # Mock successful login but failed space creation
            mock_session = AsyncMock()
            mock_login_response = AsyncMock()
            mock_login_response.status = 200
            mock_login_response.json = AsyncMock(return_value={
                "access_token": "test_admin_token"
            })

            mock_create_response = AsyncMock()
            mock_create_response.status = 500
            mock_create_response.text = AsyncMock(return_value="Internal Server Error")

            mock_session.post = AsyncMock(side_effect=[
                mock_login_response.__aenter__.return_value,
                mock_create_response.__aenter__.return_value
            ])

            mock_login_response.__aenter__.return_value = mock_login_response
            mock_login_response.__aexit__.return_value = AsyncMock()
            mock_create_response.__aenter__.return_value = mock_create_response
            mock_create_response.__aexit__.return_value = AsyncMock()

            with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_session):
                with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_session):
                    mock_session.__aenter__.return_value = mock_session
                    mock_session.__aexit__.return_value = AsyncMock()

                    space_id = await manager.space_manager.create_letta_agents_space()

                    assert space_id is None


@pytest.mark.unit
class TestAddRoomToSpace:
    """Test adding rooms to space hierarchy"""

    @pytest.mark.asyncio
    async def test_add_room_to_space_success(self, manager_with_space, mock_aiohttp_session):
        """Test successfully adding a room to space"""
        manager = manager_with_space

        # Mock get_admin_token
        with patch.object(manager.space_manager, 'get_admin_token', return_value="admin_token_123"):
            # Mock child response
            mock_child_response = AsyncMock()
            mock_child_response.status = 200
            mock_child_response.__aenter__ = AsyncMock(return_value=mock_child_response)
            mock_child_response.__aexit__ = AsyncMock(return_value=None)

            # Mock parent response
            mock_parent_response = AsyncMock()
            mock_parent_response.status = 200
            mock_parent_response.__aenter__ = AsyncMock(return_value=mock_parent_response)
            mock_parent_response.__aexit__ = AsyncMock(return_value=None)

            # Set up session to return different responses for each put call
            mock_aiohttp_session.put = Mock(side_effect=[mock_child_response, mock_parent_response])
            mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
            mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

            with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    success = await manager.space_manager.add_room_to_space(
                        "!room123:matrix.oculair.ca",
                        "Test Agent"
                    )

                    assert success is True
                    assert mock_aiohttp_session.put.call_count == 2

    @pytest.mark.asyncio
    async def test_add_room_to_space_no_space_id(self, mock_config):
        """Test adding room when no space ID is set"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
            # Don't set space_id

            success = await manager.space_manager.add_room_to_space(
                "!room123:matrix.oculair.ca",
                "Test Agent"
            )

            assert success is False

    @pytest.mark.asyncio
    async def test_add_room_to_space_no_admin_token(self, manager_with_space):
        """Test adding room when admin token is unavailable"""
        manager = manager_with_space

        with patch.object(manager.space_manager, 'get_admin_token', return_value=None):
            success = await manager.space_manager.add_room_to_space(
                "!room123:matrix.oculair.ca",
                "Test Agent"
            )

            assert success is False

    @pytest.mark.asyncio
    async def test_add_room_to_space_child_api_failure(self, manager_with_space, mock_aiohttp_session):
        """Test when child relationship API call fails"""
        manager = manager_with_space

        with patch.object(manager.space_manager, 'get_admin_token', return_value="admin_token"):
            mock_response = AsyncMock()
            mock_response.status = 403
            mock_response.text = AsyncMock(return_value="Forbidden")
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_aiohttp_session.put = Mock(return_value=mock_response)
            mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
            mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

            with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    success = await manager.space_manager.add_room_to_space(
                        "!room123:matrix.oculair.ca",
                        "Test Agent"
                    )

                    assert success is False

    @pytest.mark.asyncio
    async def test_add_room_to_space_parent_api_warning(self, manager_with_space, mock_aiohttp_session):
        """Test when parent relationship API succeeds but with warning"""
        manager = manager_with_space

        with patch.object(manager.space_manager, 'get_admin_token', return_value="admin_token"):
            mock_child_response = AsyncMock()
            mock_child_response.status = 200
            mock_child_response.__aenter__ = AsyncMock(return_value=mock_child_response)
            mock_child_response.__aexit__ = AsyncMock(return_value=None)

            mock_parent_response = AsyncMock()
            mock_parent_response.status = 500  # Parent call fails, but we still return True
            mock_parent_response.__aenter__ = AsyncMock(return_value=mock_parent_response)
            mock_parent_response.__aexit__ = AsyncMock(return_value=None)

            mock_aiohttp_session.put = Mock(side_effect=[mock_child_response, mock_parent_response])
            mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
            mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

            with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    success = await manager.space_manager.add_room_to_space(
                        "!room123:matrix.oculair.ca",
                        "Test Agent"
                    )

                    # Still returns True because child relationship succeeded
                    assert success is True


@pytest.mark.unit
class TestMigrateExistingRoomsToSpace:
    """Test migration of existing agent rooms to space"""

    @pytest.mark.asyncio
    async def test_migrate_rooms_success(self, manager_with_space):
        """Test successful migration of multiple rooms"""
        manager = manager_with_space

        # Create test mappings with rooms
        manager.mappings = {
            "agent-1": AgentUserMapping(
                agent_id="agent-1",
                agent_name="Agent One",
                matrix_user_id="@agent_1:matrix.oculair.ca",
                matrix_password="pass1",
                room_id="!room1:matrix.oculair.ca",
                room_created=True
            ),
            "agent-2": AgentUserMapping(
                agent_id="agent-2",
                agent_name="Agent Two",
                matrix_user_id="@agent_2:matrix.oculair.ca",
                matrix_password="pass2",
                room_id="!room2:matrix.oculair.ca",
                room_created=True
            ),
            "agent-3": AgentUserMapping(
                agent_id="agent-3",
                agent_name="Agent Three",
                matrix_user_id="@agent_3:matrix.oculair.ca",
                matrix_password="pass3",
                room_id=None,  # No room, should be skipped
                room_created=False
            )
        }

        # Mock add_room_to_space to succeed for both rooms
        with patch.object(manager.space_manager, 'add_room_to_space', return_value=True) as mock_add:
            migrated_count = await manager.space_manager.migrate_existing_rooms_to_space(manager.mappings)

            assert migrated_count == 2
            assert mock_add.call_count == 2

    @pytest.mark.asyncio
    async def test_migrate_rooms_no_space_id(self, mock_config):
        """Test migration when no space ID is set"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
            # Don't set space_id

            migrated_count = await manager.space_manager.migrate_existing_rooms_to_space(manager.mappings)

            assert migrated_count == 0

    @pytest.mark.asyncio
    async def test_migrate_rooms_partial_success(self, manager_with_space):
        """Test migration with some failures"""
        manager = manager_with_space

        manager.mappings = {
            "agent-1": AgentUserMapping(
                agent_id="agent-1",
                agent_name="Agent One",
                matrix_user_id="@agent_1:matrix.oculair.ca",
                matrix_password="pass1",
                room_id="!room1:matrix.oculair.ca",
                room_created=True
            ),
            "agent-2": AgentUserMapping(
                agent_id="agent-2",
                agent_name="Agent Two",
                matrix_user_id="@agent_2:matrix.oculair.ca",
                matrix_password="pass2",
                room_id="!room2:matrix.oculair.ca",
                room_created=True
            )
        }

        # Mock add_room_to_space to succeed for first, fail for second
        with patch.object(manager.space_manager, 'add_room_to_space', side_effect=[True, False]) as mock_add:
            migrated_count = await manager.space_manager.migrate_existing_rooms_to_space(manager.mappings)

            assert migrated_count == 1
            assert mock_add.call_count == 2


@pytest.mark.unit
class TestCheckRoomExists:
    """Test room existence verification"""

    @pytest.mark.asyncio
    async def test_check_room_exists_true(self, mock_config, mock_aiohttp_session):
        """Test checking a room that exists"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                with patch.object(manager.space_manager, 'get_admin_token', return_value="admin_token"):
                    mock_response = AsyncMock()
                    mock_response.status = 200
                    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                    mock_response.__aexit__ = AsyncMock(return_value=None)

                    mock_aiohttp_session.get = Mock(return_value=mock_response)
                    mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                    mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                    with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                            exists = await manager.check_room_exists("!room123:matrix.oculair.ca")

                            assert exists is True

    @pytest.mark.asyncio
    async def test_check_room_exists_false(self, mock_config, mock_aiohttp_session):
        """Test checking a room that doesn't exist"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                with patch.object(manager, 'get_admin_token', return_value="admin_token"):
                    mock_response = AsyncMock()
                    mock_response.status = 404
                    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                    mock_response.__aexit__ = AsyncMock(return_value=None)

                    mock_aiohttp_session.get = Mock(return_value=mock_response)
                    mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                    mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                    with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        exists = await manager.check_room_exists("!room123:matrix.oculair.ca")

                        assert exists is False

    @pytest.mark.asyncio
    async def test_check_room_exists_forbidden_still_exists(self, mock_config, mock_aiohttp_session):
        """Test that 403 response indicates room is invalid (access denied)"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                with patch.object(manager.space_manager, 'get_admin_token', return_value="admin_token"):
                    mock_response = AsyncMock()
                    mock_response.status = 403
                    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                    mock_response.__aexit__ = AsyncMock(return_value=None)

                    mock_aiohttp_session.get = Mock(return_value=mock_response)
                    mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                    mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                    with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        with patch('src.core.space_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                            exists = await manager.check_room_exists("!room123:matrix.oculair.ca")

                            # Changed: 403 now treated as invalid (returns False) to trigger recreation
                            assert exists is False


    @pytest.mark.asyncio
    async def test_check_room_exists_no_admin_token(self, mock_config):
        """Test checking room when admin token is unavailable"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

            with patch.object(manager.space_manager, 'get_admin_token', return_value=None):
                exists = await manager.check_room_exists("!room123:matrix.oculair.ca")

                assert exists is False


@pytest.mark.unit
class TestUpdateRoomName:
    """Test room name update functionality"""

    @pytest.mark.asyncio
    async def test_update_room_name_success(self, mock_config, mock_aiohttp_session):
        """Test successfully updating room name"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Patch the callback in room_manager since it was captured during init
                manager.room_manager.get_admin_token = AsyncMock(return_value="admin_token")

                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.put = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    with patch('src.core.room_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        success = await manager.update_room_name(
                            "!room123:matrix.oculair.ca",
                            "New Agent Name"
                        )

                        assert success is True

    @pytest.mark.asyncio
    async def test_update_room_name_no_admin_token(self, mock_config, mock_aiohttp_session):
        """Test updating room name when admin token is unavailable"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

            # Patch the callback in room_manager since it was captured during init
            async def no_token():
                return None
            
            with patch.object(manager.room_manager, 'get_admin_token', side_effect=no_token):
                success = await manager.update_room_name(
                    "!room123:matrix.oculair.ca",
                    "New Agent Name"
                )

                assert success is False

    @pytest.mark.asyncio
    async def test_update_room_name_api_failure(self, mock_config, mock_aiohttp_session):
        """Test room name update when API call fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

                # Patch the callback in room_manager since it was captured during init
                manager.room_manager.get_admin_token = AsyncMock(return_value="admin_token")

                mock_response = AsyncMock()
                mock_response.status = 500
                mock_response.text = AsyncMock(return_value="Internal Server Error")
                mock_response.__aenter__ = AsyncMock(return_value=mock_response)
                mock_response.__aexit__ = AsyncMock(return_value=None)

                mock_aiohttp_session.put = Mock(return_value=mock_response)
                mock_aiohttp_session.__aenter__ = AsyncMock(return_value=mock_aiohttp_session)
                mock_aiohttp_session.__aexit__ = AsyncMock(return_value=None)

                with patch('src.core.agent_user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    with patch('src.core.room_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                        success = await manager.update_room_name(
                            "!room123:matrix.oculair.ca",
                            "New Agent Name"
                        )

                assert success is False


@pytest.mark.unit
class TestSpaceValidationAndRecreation:
    """Test automatic space validation and recreation during sync"""

    @pytest.mark.asyncio
    async def test_sync_validates_existing_space(self, mock_config):
        """Test that sync validates existing space"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_id = "!existing_space:matrix.oculair.ca"
                
                # Mock dependencies
                with patch.object(manager, 'get_letta_agents', return_value=[]):
                    with patch.object(manager.space_manager, 'check_room_exists', return_value=True) as mock_check:
                        with patch.object(manager.space_manager, 'load_space_config', return_value=None):
                            with patch.object(manager, 'save_mappings', return_value=None):
                                await manager.sync_agents_to_users()
                                
                                # Verify check_room_exists was called for the space
                                mock_check.assert_called_once_with("!existing_space:matrix.oculair.ca")

    @pytest.mark.asyncio
    async def test_sync_recreates_invalid_space(self, mock_config):
        """Test that sync recreates space when validation fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_id = "!invalid_space:matrix.oculair.ca"
                
                # Mock dependencies
                new_space_id = "!new_space:matrix.oculair.ca"
                
                async def mock_create_space():
                    manager.space_manager.space_id = new_space_id
                    return new_space_id
                
                with patch.object(manager, 'get_letta_agents', return_value=[]):
                    with patch.object(manager, 'ensure_core_users_exist', return_value=None):
                        # First call validates old space (False), second call validates new space (True)
                        with patch.object(manager.space_manager, 'check_room_exists', side_effect=[False, True]):
                            with patch.object(manager.space_manager, 'save_space_config', return_value=None):
                                with patch.object(manager.space_manager, 'create_letta_agents_space', side_effect=mock_create_space) as mock_create:
                                    with patch.object(manager.space_manager, 'load_space_config', return_value=None):
                                        with patch.object(manager, 'save_mappings', return_value=None):
                                            with patch.object(manager.space_manager, 'migrate_existing_rooms_to_space', return_value=0):
                                                await manager.sync_agents_to_users()
                                                
                                                # Verify new space was created
                                                mock_create.assert_called_once()
                                                # Verify space ID was cleared then updated
                                                assert manager.space_manager.space_id == new_space_id

    @pytest.mark.asyncio
    async def test_sync_migrates_rooms_after_space_recreation(self, mock_config):
        """Test that rooms are migrated after space recreation"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_id = "!invalid_space:matrix.oculair.ca"
                
                # Create test mappings with rooms
                test_mappings = {
                    "agent-1": AgentUserMapping(
                        agent_id="agent-1",
                        agent_name="Test Agent 1",
                        matrix_user_id="@agent1:matrix.test",
                        matrix_password="pass",
                        created=True,
                        room_id="!room1:matrix.test",
                        room_created=True
                    ),
                    "agent-2": AgentUserMapping(
                        agent_id="agent-2",
                        agent_name="Test Agent 2",
                        matrix_user_id="@agent2:matrix.test",
                        matrix_password="pass",
                        created=True,
                        room_id="!room2:matrix.test",
                        room_created=True
                    )
                }
                manager.mappings = test_mappings
                
                new_space_id = "!new_space:matrix.oculair.ca"
                
                async def mock_create_space():
                    manager.space_manager.space_id = new_space_id
                    return new_space_id
                
                with patch.object(manager, 'get_letta_agents', return_value=[]):
                    with patch.object(manager, 'ensure_core_users_exist', return_value=None):
                        # First call validates old space (False), second call validates new space (True)
                        with patch.object(manager.space_manager, 'check_room_exists', side_effect=[False, True]):
                            with patch.object(manager.space_manager, 'save_space_config', return_value=None):
                                with patch.object(manager.space_manager, 'create_letta_agents_space', side_effect=mock_create_space):
                                    with patch.object(manager.space_manager, 'load_space_config', return_value=None):
                                        with patch.object(manager, 'save_mappings', return_value=None):
                                            with patch.object(manager.space_manager, 'migrate_existing_rooms_to_space', return_value=2) as mock_migrate:
                                                await manager.sync_agents_to_users()
                                                
                                                # Verify migration was called with the mappings
                                                mock_migrate.assert_called_once_with(test_mappings)

    @pytest.mark.asyncio
    async def test_sync_handles_space_recreation_failure(self, mock_config):
        """Test sync handles gracefully when space recreation fails"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                old_space_id = "!invalid_space:matrix.oculair.ca"
                manager.space_manager.space_id = old_space_id
                
                # Mock dependencies
                with patch.object(manager, 'get_letta_agents', return_value=[]):
                    # Only one check_room_exists call since create_letta_agents_space returns None
                    with patch.object(manager.space_manager, 'check_room_exists', return_value=False):
                        with patch.object(manager.space_manager, 'save_space_config', return_value=None):
                            with patch.object(manager.space_manager, 'create_letta_agents_space', return_value=None):
                                with patch.object(manager.space_manager, 'load_space_config', return_value=None):
                                    with patch.object(manager, 'save_mappings', return_value=None):
                                        # Should not raise exception
                                        await manager.sync_agents_to_users()
                                        
                                        # Space ID should be restored to old value to prevent recreation loops
                                        assert manager.space_manager.space_id == old_space_id

    @pytest.mark.asyncio
    async def test_sync_skips_validation_when_no_space(self, mock_config):
        """Test that sync creates space when none exists (no validation needed)"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                manager.space_manager.space_id = None
                
                new_space_id = "!new_space:matrix.oculair.ca"
                
                async def mock_create_space():
                    manager.space_manager.space_id = new_space_id
                    return new_space_id
                
                with patch.object(manager, 'get_letta_agents', return_value=[]):
                    with patch.object(manager, 'ensure_core_users_exist', return_value=None):
                        with patch.object(manager.space_manager, 'check_room_exists') as mock_check:
                            with patch.object(manager.space_manager, 'create_letta_agents_space', side_effect=mock_create_space):
                                with patch.object(manager.space_manager, 'load_space_config', return_value=None):
                                    with patch.object(manager, 'save_mappings', return_value=None):
                                        with patch.object(manager.space_manager, 'migrate_existing_rooms_to_space', return_value=0):
                                            await manager.sync_agents_to_users()
                                            
                                            # Verify check_room_exists was NOT called
                                            mock_check.assert_not_called()
                                            # Verify new space was created
                                            assert manager.space_manager.space_id == new_space_id

    @pytest.mark.asyncio
    async def test_sync_preserves_valid_space(self, mock_config):
        """Test that sync preserves space when validation passes"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)
                original_space_id = "!valid_space:matrix.oculair.ca"
                manager.space_manager.space_id = original_space_id
                
                with patch.object(manager, 'get_letta_agents', return_value=[]):
                    with patch.object(manager.space_manager, 'check_room_exists', return_value=True):
                        with patch.object(manager.space_manager, 'create_letta_agents_space') as mock_create:
                            with patch.object(manager.space_manager, 'load_space_config', return_value=None):
                                with patch.object(manager, 'save_mappings', return_value=None):
                                    await manager.sync_agents_to_users()
                                    
                                    # Verify create was NOT called
                                    mock_create.assert_not_called()
                                    # Verify space ID is unchanged
                                    assert manager.space_manager.space_id == original_space_id

    @pytest.mark.asyncio
    async def test_update_room_name_exception_handling(self, mock_config):
        """Test that exceptions are handled gracefully"""
        with patch('src.core.agent_user_manager.logging.getLogger'):
            with patch('src.core.agent_user_manager.os.makedirs'):
                manager = AgentUserManager(config=mock_config)

            # Patch the callback in room_manager since it was captured during init
            async def failing_get_token():
                raise Exception("Network error")
            
            with patch.object(manager.room_manager, 'get_admin_token', side_effect=failing_get_token):
                success = await manager.update_room_name(
                    "!room123:matrix.oculair.ca",
                    "New Agent Name"
                )

                assert success is False
