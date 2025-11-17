"""
Unit tests for Matrix room and space creation
Tests the automatic room and space creation functionality
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.core.room_manager import MatrixRoomManager, AgentUserMapping
from src.core.space_manager import MatrixSpaceManager


class TestSpaceCreation:
    """Test Matrix Space creation and management"""

    @pytest.fixture
    def space_manager(self):
        """Create a MatrixSpaceManager instance for testing"""
        return MatrixSpaceManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass",
            main_bot_username="@letta:test.com",
            space_config_file="/tmp/test_space_config.json"
        )

    @pytest.mark.asyncio
    async def test_ensure_letta_space_creates_new_space(self, space_manager):
        """Test that create_letta_agents_space creates a new space if none exists"""
        # Mock login response
        login_response = MagicMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "admin_token_123"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)
        
        # Mock space creation response
        create_response = MagicMock()
        create_response.status = 200
        create_response.json = AsyncMock(return_value={"room_id": "!newspace:test.com"})
        create_response.__aenter__ = AsyncMock(return_value=create_response)
        create_response.__aexit__ = AsyncMock(return_value=None)
        
        # Mock session
        mock_post = MagicMock(side_effect=[login_response, create_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Mock save_space_config
            with patch.object(space_manager, 'save_space_config', new_callable=AsyncMock):
                space_id = await space_manager.create_letta_agents_space()
                
                assert space_id == "!newspace:test.com"
                assert space_manager.space_id == "!newspace:test.com"

    @pytest.mark.asyncio
    async def test_ensure_letta_space_uses_existing_space(self, space_manager):
        """Test that create_letta_agents_space uses existing space if available"""
        space_manager.space_id = "!existingspace:test.com"
        
        # Mock check_room_exists to return True
        with patch.object(space_manager, 'check_room_exists', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True
            
            space_id = await space_manager.create_letta_agents_space()
            
            assert space_id == "!existingspace:test.com"
            # Should not have tried to create a new space
            mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_room_to_space_success(self, space_manager):
        """Test successfully adding a room to the space"""
        space_manager.space_id = "!space:test.com"
        
        # Mock get_admin_token
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"
            
            # Mock HTTP responses
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$event123"})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)
            
            mock_put = MagicMock(return_value=mock_response)
            mock_session = MagicMock()
            mock_session.put = mock_put
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            
            with patch('aiohttp.ClientSession', return_value=mock_session):
                success = await space_manager.add_room_to_space("!room:test.com", "Agent Room")
                
                assert success is True

    @pytest.mark.asyncio
    async def test_add_room_to_space_no_space(self, space_manager):
        """Test that add_room_to_space fails gracefully when no space exists"""
        space_manager.space_id = None
        
        success = await space_manager.add_room_to_space("!room:test.com", "Agent Room")
        
        assert success is False

    def test_get_space_id(self, space_manager):
        """Test get_space_id returns the current space ID"""
        space_manager.space_id = "!myspace:test.com"

        assert space_manager.get_space_id() == "!myspace:test.com"

    @pytest.mark.asyncio
    async def test_check_room_exists_room_found(self, space_manager):
        """Test check_room_exists returns True when room exists"""
        # Mock get_admin_token
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"

            # Mock successful state response
            state_response = MagicMock()
            state_response.status = 200
            state_response.__aenter__ = AsyncMock(return_value=state_response)
            state_response.__aexit__ = AsyncMock(return_value=None)

            # Mock successful create event response
            create_response = MagicMock()
            create_response.status = 200
            create_response.json = AsyncMock(return_value={"room_version": "10"})
            create_response.__aenter__ = AsyncMock(return_value=create_response)
            create_response.__aexit__ = AsyncMock(return_value=None)

            mock_get = MagicMock(side_effect=[state_response, create_response])
            mock_session = MagicMock()
            mock_session.get = mock_get
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            with patch('aiohttp.ClientSession', return_value=mock_session):
                exists = await space_manager.check_room_exists("!room:test.com")
                assert exists is True

    @pytest.mark.asyncio
    async def test_check_room_exists_room_not_found(self, space_manager):
        """Test check_room_exists returns False when room doesn't exist (404)"""
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"

            # Mock 404 response
            mock_response = MagicMock()
            mock_response.status = 404
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_get = MagicMock(return_value=mock_response)
            mock_session = MagicMock()
            mock_session.get = mock_get
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            with patch('aiohttp.ClientSession', return_value=mock_session):
                exists = await space_manager.check_room_exists("!room:test.com")
                assert exists is False

    @pytest.mark.asyncio
    async def test_check_room_exists_room_forbidden(self, space_manager):
        """Test check_room_exists returns False when access is forbidden"""
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"

            # Mock 403 response
            mock_response = MagicMock()
            mock_response.status = 403
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)

            mock_get = MagicMock(return_value=mock_response)
            mock_session = MagicMock()
            mock_session.get = mock_get
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            with patch('aiohttp.ClientSession', return_value=mock_session):
                exists = await space_manager.check_room_exists("!room:test.com")
                assert exists is False

    @pytest.mark.asyncio
    async def test_check_room_exists_no_admin_token(self, space_manager):
        """Test check_room_exists returns False when admin token unavailable"""
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = None

            exists = await space_manager.check_room_exists("!room:test.com")
            assert exists is False

    @pytest.mark.asyncio
    async def test_check_room_exists_missing_room_version(self, space_manager):
        """Test check_room_exists returns False when room has no room_version"""
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"

            # Mock successful state response
            state_response = MagicMock()
            state_response.status = 200
            state_response.__aenter__ = AsyncMock(return_value=state_response)
            state_response.__aexit__ = AsyncMock(return_value=None)

            # Mock create event response without room_version
            create_response = MagicMock()
            create_response.status = 200
            create_response.json = AsyncMock(return_value={})  # No room_version
            create_response.__aenter__ = AsyncMock(return_value=create_response)
            create_response.__aexit__ = AsyncMock(return_value=None)

            mock_get = MagicMock(side_effect=[state_response, create_response])
            mock_session = MagicMock()
            mock_session.get = mock_get
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            with patch('aiohttp.ClientSession', return_value=mock_session):
                exists = await space_manager.check_room_exists("!room:test.com")
                assert exists is False

    @pytest.mark.asyncio
    async def test_check_room_exists_exception_handling(self, space_manager):
        """Test check_room_exists handles exceptions gracefully"""
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.side_effect = Exception("Network error")

            exists = await space_manager.check_room_exists("!room:test.com")
            assert exists is False

    @pytest.mark.asyncio
    async def test_get_admin_token_caching(self, space_manager):
        """Test that get_admin_token caches the token"""
        # Set a cached token
        space_manager._admin_token = "cached_token_123"

        # Should return cached token without making HTTP request
        token = await space_manager.get_admin_token()
        assert token == "cached_token_123"

    @pytest.mark.asyncio
    async def test_get_admin_token_failure(self, space_manager):
        """Test get_admin_token handles login failure"""
        # Mock failed login response
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value="Invalid credentials")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            token = await space_manager.get_admin_token()
            assert token is None

    @pytest.mark.asyncio
    async def test_migrate_existing_rooms_to_space_success(self, space_manager):
        """Test migrate_existing_rooms_to_space successfully migrates rooms"""
        space_manager.space_id = "!space:test.com"

        # Create mock agent mappings
        mock_mapping1 = Mock()
        mock_mapping1.room_id = "!room1:test.com"
        mock_mapping1.room_created = True
        mock_mapping1.agent_name = "Agent 1"

        mock_mapping2 = Mock()
        mock_mapping2.room_id = "!room2:test.com"
        mock_mapping2.room_created = True
        mock_mapping2.agent_name = "Agent 2"

        agent_mappings = {
            "agent-1": mock_mapping1,
            "agent-2": mock_mapping2
        }

        # Mock add_room_to_space to return success
        with patch.object(space_manager, 'add_room_to_space', new_callable=AsyncMock) as mock_add:
            mock_add.return_value = True

            count = await space_manager.migrate_existing_rooms_to_space(agent_mappings)

            assert count == 2
            assert mock_add.call_count == 2

    @pytest.mark.asyncio
    async def test_migrate_existing_rooms_no_space(self, space_manager):
        """Test migrate_existing_rooms_to_space returns 0 when no space exists"""
        space_manager.space_id = None

        agent_mappings = {"agent-1": Mock()}

        count = await space_manager.migrate_existing_rooms_to_space(agent_mappings)
        assert count == 0

    @pytest.mark.asyncio
    async def test_load_space_config_file_not_found(self, space_manager):
        """Test load_space_config handles missing config file"""
        import tempfile
        import os

        # Use a non-existent file path
        with tempfile.TemporaryDirectory() as tmpdir:
            space_manager.space_config_file = os.path.join(tmpdir, "nonexistent.json")

            # Should not raise exception
            await space_manager.load_space_config()

            # space_id should remain None
            assert space_manager.space_id is None

    @pytest.mark.asyncio
    async def test_save_space_config_success(self, space_manager):
        """Test save_space_config successfully saves configuration"""
        import tempfile
        import json
        import os

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            config_file = f.name

        try:
            space_manager.space_config_file = config_file
            space_manager.space_id = "!savedspace:test.com"

            await space_manager.save_space_config()

            # Verify file was created with correct content
            assert os.path.exists(config_file)
            with open(config_file, 'r') as f:
                data = json.load(f)
                assert data["space_id"] == "!savedspace:test.com"
                assert "created_at" in data
                assert data["name"] == "Letta Agents"
        finally:
            if os.path.exists(config_file):
                os.unlink(config_file)


class TestRoomCreation:
    """Test Matrix room creation and management"""

    @pytest.fixture
    def room_manager(self):
        """Create a MatrixRoomManager instance for testing"""
        mock_space_manager = Mock()
        mock_space_manager.get_space_id.return_value = "!space:test.com"
        mock_space_manager.add_room_to_space = AsyncMock(return_value=True)
        mock_space_manager.check_room_exists = AsyncMock(return_value=True)  # Assume rooms exist by default
        
        mock_user_manager = Mock()
        mock_user_manager.get_admin_token = AsyncMock(return_value="admin_token")
        
        mock_config = Mock()
        mock_config.username = "@letta:test.com"
        mock_config.homeserver_url = "http://test-homeserver:8008"
        
        return MatrixRoomManager(
            homeserver_url="http://test-homeserver:8008",
            space_manager=mock_space_manager,
            user_manager=mock_user_manager,
            config=mock_config,
            admin_username="@admin:test.com",
            get_admin_token_callback=AsyncMock(return_value="admin_token"),
            save_mappings_callback=AsyncMock()
        )

    @pytest.mark.asyncio
    async def test_create_or_update_agent_room_creates_room(self, room_manager):
        """Test create_or_update_agent_room creates a room"""
        agent_id = "agent-123"
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            matrix_password="password",
            created=True,
            room_id=None,
            invitation_status={}
        )
        
        # Mock the HTTP client responses for room creation
        login_response = MagicMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "token123"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)
        
        create_room_response = MagicMock()
        create_room_response.status = 200
        create_room_response.json = AsyncMock(return_value={"room_id": "!newroom:test.com"})
        create_room_response.__aenter__ = AsyncMock(return_value=create_room_response)
        create_room_response.__aexit__ = AsyncMock(return_value=None)
        
        join_response = MagicMock()
        join_response.status = 200
        join_response.json = AsyncMock(return_value={})
        join_response.__aenter__ = AsyncMock(return_value=join_response)
        join_response.__aexit__ = AsyncMock(return_value=None)
        
        # Create mock session
        mock_post = MagicMock(side_effect=[login_response, create_room_response, login_response, login_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            await room_manager.create_or_update_agent_room(agent_id, mapping)
            
            # Verify room was assigned to mapping
            assert mapping.room_id == "!newroom:test.com"

    @pytest.mark.asyncio
    async def test_update_room_name_success(self, room_manager):
        """Test successfully updating a room name"""
        # Mock get_admin_token
        room_manager.get_admin_token = AsyncMock(return_value="admin_token")
        
        # Mock HTTP response
        put_response = MagicMock()
        put_response.status = 200
        put_response.json = AsyncMock(return_value={"event_id": "$event123"})
        put_response.__aenter__ = AsyncMock(return_value=put_response)
        put_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_put = MagicMock(return_value=put_response)
        mock_session = MagicMock()
        mock_session.put = mock_put
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await room_manager.update_room_name("!room:test.com", "New Room Name")
            
            assert result is True
            mock_put.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_existing_agent_room_returns_none(self, room_manager):
        """Test find_existing_agent_room (currently returns None to force new room creation)"""
        result = await room_manager.find_existing_agent_room("Test Agent")
        
        # Currently this always returns None to force creation of new rooms
        assert result is None

    @pytest.mark.asyncio
    async def test_create_or_update_agent_room_user_not_created(self, room_manager):
        """Test create_or_update_agent_room fails gracefully when user not created"""
        agent_id = "agent-123"
        
        # Mapping with created=False
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            matrix_password="password",
            created=False,  # User not created
            room_id=None,
            invitation_status=None
        )
        
        # Should return early without creating room
        await room_manager.create_or_update_agent_room(agent_id, mapping)
        
        # Room should still be None
        assert mapping.room_id is None

    @pytest.mark.asyncio
    async def test_create_or_update_agent_room_skips_when_room_exists(self, room_manager):
        """Test create_or_update_agent_room skips creation when room already exists"""
        agent_id = "agent-123"
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            matrix_password="password",
            created=True,
            room_id="!existing:test.com",  # Room already exists
            room_created=True,  # Mark room as created
            invitation_status={}
        )
        
        # Explicitly set check_room_exists to return True for the existing room
        room_manager.space_manager.check_room_exists = AsyncMock(return_value=True)
        
        # Should skip room creation since room_id is already set and room exists
        await room_manager.create_or_update_agent_room(agent_id, mapping)
        
        # Room should remain the same
        assert mapping.room_id == "!existing:test.com"
        assert mapping.room_created is True

    @pytest.mark.asyncio
    async def test_update_room_name_no_admin_token(self, room_manager):
        """Test update_room_name fails when admin token unavailable"""
        room_manager.get_admin_token = AsyncMock(return_value=None)

        result = await room_manager.update_room_name("!room:test.com", "New Name")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_room_name_http_failure(self, room_manager):
        """Test update_room_name handles HTTP failures"""
        room_manager.get_admin_token = AsyncMock(return_value="admin_token")

        # Mock failed HTTP response
        put_response = MagicMock()
        put_response.status = 403
        put_response.text = AsyncMock(return_value="Forbidden")
        put_response.__aenter__ = AsyncMock(return_value=put_response)
        put_response.__aexit__ = AsyncMock(return_value=None)

        mock_put = MagicMock(return_value=put_response)
        mock_session = MagicMock()
        mock_session.put = mock_put
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await room_manager.update_room_name("!room:test.com", "New Name")
            assert result is False

    @pytest.mark.asyncio
    async def test_update_room_name_exception(self, room_manager):
        """Test update_room_name handles exceptions"""
        room_manager.get_admin_token = AsyncMock(side_effect=Exception("Network error"))

        result = await room_manager.update_room_name("!room:test.com", "New Name")
        assert result is False

    @pytest.mark.asyncio
    async def test_create_or_update_agent_room_invalid_room_recreates(self, room_manager):
        """Test create_or_update_agent_room recreates room when invalid room is detected"""
        agent_id = "agent-123"

        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            matrix_password="password",
            created=True,
            room_id="!invalid:test.com",  # Invalid room
            room_created=True,
            invitation_status={}
        )

        # Mock check_room_exists to return False (room doesn't exist)
        room_manager.space_manager.check_room_exists = AsyncMock(return_value=False)

        # Mock HTTP responses for room creation
        login_response = MagicMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "token123"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)

        create_room_response = MagicMock()
        create_room_response.status = 200
        create_room_response.json = AsyncMock(return_value={"room_id": "!newroom:test.com"})
        create_room_response.__aenter__ = AsyncMock(return_value=create_room_response)
        create_room_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(side_effect=[login_response, create_room_response, login_response, login_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            await room_manager.create_or_update_agent_room(agent_id, mapping)

            # Room ID should be updated to the new room
            assert mapping.room_id == "!newroom:test.com"
            # Invalid room info should have been cleared and recreated
            assert mapping.room_created is True

    @pytest.mark.asyncio
    async def test_auto_accept_invitations_tracking(self, room_manager):
        """Test auto_accept_invitations_with_tracking updates invitation status when dict is pre-populated"""
        # Initialize mapping with a non-empty dict for invitation_status
        # (empty dict evaluates to False in the code's `if mapping.invitation_status:` check)
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            matrix_password="password",
            created=True,
            room_id="!room:test.com",
            invitation_status={"placeholder": "invited"}  # Non-empty dict
        )
        
        # Mock successful HTTP responses for login and join
        login_response = MagicMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "token123"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)
        
        join_response = MagicMock()
        join_response.status = 200
        join_response.json = AsyncMock(return_value={"room_id": "!room:test.com"})
        join_response.__aenter__ = AsyncMock(return_value=join_response)
        join_response.__aexit__ = AsyncMock(return_value=None)
        
        # Need: login1, join1, login2, join2 for both users
        mock_post = MagicMock(side_effect=[login_response, join_response, login_response, join_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Need to patch the config and user_manager password attributes
            room_manager.config.password = "letta_password"
            room_manager.user_manager.admin_password = "admin_password"
            
            await room_manager.auto_accept_invitations_with_tracking("!room:test.com", mapping)
            
            # Verify invitation status was updated
            assert mapping.invitation_status is not None
            # Both admin and letta should have status = "joined"
            assert len(mapping.invitation_status) >= 2
            assert mapping.invitation_status[room_manager.admin_username] == "joined"
            assert mapping.invitation_status[room_manager.config.username] == "joined"


class TestRoomSpaceIntegration:
    """Integration tests for room and space creation workflow"""

    @pytest.mark.asyncio
    async def test_complete_space_and_room_creation_workflow(self):
        """Test complete workflow: create space, create room, add room to space"""
        # Setup
        space_manager = MatrixSpaceManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass",
            main_bot_username="@letta:test.com",
            space_config_file="/tmp/test_space.json"
        )
        
        # Mock login response
        login_response = MagicMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "admin_token_123"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)
        
        # Mock space creation response
        space_response = MagicMock()
        space_response.status = 200
        space_response.json = AsyncMock(return_value={"room_id": "!space:test.com"})
        space_response.__aenter__ = AsyncMock(return_value=space_response)
        space_response.__aexit__ = AsyncMock(return_value=None)
        
        # Mock add room to space response
        room_response = MagicMock()
        room_response.status = 200
        room_response.json = AsyncMock(return_value={"event_id": "$event"})
        room_response.__aenter__ = AsyncMock(return_value=room_response)
        room_response.__aexit__ = AsyncMock(return_value=None)
        
        # Second login for add_room_to_space
        login_response2 = MagicMock()
        login_response2.status = 200
        login_response2.json = AsyncMock(return_value={"access_token": "admin_token_456"})
        login_response2.__aenter__ = AsyncMock(return_value=login_response2)
        login_response2.__aexit__ = AsyncMock(return_value=None)
        
        mock_post = MagicMock(side_effect=[login_response, space_response, login_response2])
        mock_put = MagicMock(return_value=room_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.put = mock_put
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            with patch.object(space_manager, 'save_space_config', new_callable=AsyncMock):
                # Create space
                space_id = await space_manager.create_letta_agents_space()
                assert space_id == "!space:test.com"
                
                # Add room to space
                success = await space_manager.add_room_to_space("!room:test.com", "Test Room")
                assert success is True

    @pytest.mark.asyncio
    async def test_multiple_rooms_added_to_space(self):
        """Test multiple agent rooms can be added to the same space"""
        space_manager = MatrixSpaceManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass",
            main_bot_username="@letta:test.com",
            space_config_file="/tmp/test_space.json"
        )
        
        space_manager.space_id = "!space:test.com"
        
        # Mock login response (will be called for each room addition)
        login_response = MagicMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "admin_token"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)
        
        # Mock put responses (for adding room to space and space as parent)
        put_response = MagicMock()
        put_response.status = 200
        put_response.json = AsyncMock(return_value={"event_id": "$event"})
        put_response.__aenter__ = AsyncMock(return_value=put_response)
        put_response.__aexit__ = AsyncMock(return_value=None)
        
        # For 3 rooms, need:
        # - 3 logins (one per room via get_admin_token)
        # - 6 puts (2 per room: child + parent)
        mock_post = MagicMock(side_effect=[login_response, login_response, login_response])
        mock_put = MagicMock(side_effect=[put_response, put_response, put_response, put_response, put_response, put_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.put = mock_put
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Add multiple rooms
            rooms = [
                ("!room1:test.com", "Agent 1"),
                ("!room2:test.com", "Agent 2"),
                ("!room3:test.com", "Agent 3")
            ]
            
            for room_id, room_name in rooms:
                success = await space_manager.add_room_to_space(room_id, room_name)
                assert success is True

    @pytest.mark.asyncio
    async def test_space_persists_across_restarts(self):
        """Test that space configuration persists and can be loaded"""
        import tempfile
        import json
        
        # Create temp file for space config
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            config_file = f.name
            json.dump({"space_id": "!existing:test.com", "created_at": "2024-01-01"}, f)
        
        try:
            space_manager = MatrixSpaceManager(
                homeserver_url="http://test-homeserver:8008",
                admin_username="@admin:test.com",
                admin_password="admin_pass",
                main_bot_username="@letta:test.com",
                space_config_file=config_file
            )
            
            # Load existing config
            await space_manager.load_space_config()
            
            assert space_manager.space_id == "!existing:test.com"
        finally:
            import os
            os.unlink(config_file)
