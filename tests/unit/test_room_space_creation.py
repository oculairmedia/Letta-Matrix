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
        """Test that ensure_letta_space creates a new space if none exists"""
        # Mock get_admin_token
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"
            
            # Mock HTTP response for space creation
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"room_id": "!newspace:test.com"})
            
            with patch('aiohttp.ClientSession') as mock_session:
                mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
                
                # Mock save_space_config
                with patch.object(space_manager, 'save_space_config', new_callable=AsyncMock):
                    space_id = await space_manager.ensure_letta_space()
                    
                    assert space_id == "!newspace:test.com"
                    assert space_manager.space_id == "!newspace:test.com"

    @pytest.mark.asyncio
    async def test_ensure_letta_space_uses_existing_space(self, space_manager):
        """Test that ensure_letta_space uses existing space if available"""
        space_manager.space_id = "!existingspace:test.com"
        
        # Mock check_room_exists to return True
        with patch.object(space_manager, 'check_room_exists', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True
            
            space_id = await space_manager.ensure_letta_space()
            
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
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$event123"})
            
            with patch('aiohttp.ClientSession') as mock_session:
                mock_session.return_value.__aenter__.return_value.put.return_value.__aenter__.return_value = mock_response
                
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


class TestRoomCreation:
    """Test Matrix room creation and management"""

    @pytest.fixture
    def room_manager(self):
        """Create a MatrixRoomManager instance for testing"""
        mock_space_manager = Mock()
        mock_space_manager.get_space_id.return_value = "!space:test.com"
        mock_space_manager.add_room_to_space = AsyncMock(return_value=True)
        
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
    async def test_create_agent_room_success(self, room_manager):
        """Test successful room creation for an agent"""
        agent = {
            "id": "agent-123",
            "name": "Test Agent"
        }
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            password="password",
            room_id=None,
            invitation_status=None
        )
        
        # Mock HTTP response for room creation
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"room_id": "!newroom:test.com"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            # Mock send_invite
            with patch.object(room_manager, 'send_invite', new_callable=AsyncMock) as mock_invite:
                mock_invite.return_value = True
                
                room_id = await room_manager.create_agent_room(agent, mapping)
                
                assert room_id == "!newroom:test.com"
                assert mapping.room_id == "!newroom:test.com"

    @pytest.mark.asyncio
    async def test_create_agent_room_adds_to_space(self, room_manager):
        """Test that created room is added to the Letta Agents space"""
        agent = {
            "id": "agent-123",
            "name": "Test Agent"
        }
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            password="password",
            room_id=None,
            invitation_status=None
        )
        
        # Mock HTTP response for room creation
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"room_id": "!newroom:test.com"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            with patch.object(room_manager, 'send_invite', new_callable=AsyncMock) as mock_invite:
                mock_invite.return_value = True
                
                room_id = await room_manager.create_agent_room(agent, mapping)
                
                # Verify space_manager.add_room_to_space was called
                room_manager.space_manager.add_room_to_space.assert_called_once_with(
                    "!newroom:test.com",
                    "Agent: Test Agent"
                )

    @pytest.mark.asyncio
    async def test_create_agent_room_sends_invitations(self, room_manager):
        """Test that room creation sends invitations to admin and letta user"""
        agent = {
            "id": "agent-123",
            "name": "Test Agent"
        }
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            password="password",
            room_id=None,
            invitation_status=None
        )
        
        # Mock HTTP response for room creation
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"room_id": "!newroom:test.com"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            with patch.object(room_manager, 'send_invite', new_callable=AsyncMock) as mock_invite:
                mock_invite.return_value = True
                
                room_id = await room_manager.create_agent_room(agent, mapping)
                
                # Verify invitations were sent
                # Should invite the agent user
                assert mock_invite.call_count >= 1

    @pytest.mark.asyncio
    async def test_create_or_update_agent_room_creates_new(self, room_manager):
        """Test create_or_update_agent_room creates room when none exists"""
        agent = {
            "id": "agent-123",
            "name": "Test Agent"
        }
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            password="password",
            room_id=None,
            invitation_status=None
        )
        
        with patch.object(room_manager, 'create_agent_room', new_callable=AsyncMock) as mock_create:
            mock_create.return_value = "!newroom:test.com"
            
            result = await room_manager.create_or_update_agent_room(agent, mapping)
            
            assert result == "!newroom:test.com"
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_or_update_agent_room_uses_existing(self, room_manager):
        """Test create_or_update_agent_room uses existing room"""
        agent = {
            "id": "agent-123",
            "name": "Test Agent"
        }
        
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            password="password",
            room_id="!existing:test.com",
            invitation_status=None
        )
        
        with patch.object(room_manager, 'check_room_exists', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True
            
            result = await room_manager.create_or_update_agent_room(agent, mapping)
            
            assert result == "!existing:test.com"

    @pytest.mark.asyncio
    async def test_auto_accept_invitations_success(self, room_manager):
        """Test successful invitation acceptance"""
        mapping = AgentUserMapping(
            agent_id="agent-123",
            agent_name="Test Agent",
            matrix_user_id="@agent_123:test.com",
            password="password",
            room_id="!room:test.com",
            invitation_status=None
        )
        
        users_to_invite = [
            ("@admin:test.com", "admin_pass"),
            ("@letta:test.com", "letta_pass")
        ]
        
        # Mock successful login and room join
        with patch.object(room_manager, 'login_and_join_room', new_callable=AsyncMock) as mock_join:
            mock_join.return_value = True
            
            await room_manager.auto_accept_invitations_with_tracking(
                "!room:test.com",
                users_to_invite,
                mapping
            )
            
            # Verify login_and_join_room was called for each user
            assert mock_join.call_count == 2


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
        
        # Mock space creation
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"
            
            space_response = AsyncMock()
            space_response.status = 200
            space_response.json = AsyncMock(return_value={"room_id": "!space:test.com"})
            
            room_response = AsyncMock()
            room_response.status = 200
            room_response.json = AsyncMock(return_value={"event_id": "$event"})
            
            with patch('aiohttp.ClientSession') as mock_session:
                mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = space_response
                mock_session.return_value.__aenter__.return_value.put.return_value.__aenter__.return_value = room_response
                
                with patch.object(space_manager, 'save_space_config', new_callable=AsyncMock):
                    # Create space
                    space_id = await space_manager.ensure_letta_space()
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
        
        with patch.object(space_manager, 'get_admin_token', new_callable=AsyncMock) as mock_token:
            mock_token.return_value = "admin_token"
            
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$event"})
            
            with patch('aiohttp.ClientSession') as mock_session:
                mock_session.return_value.__aenter__.return_value.put.return_value.__aenter__.return_value = mock_response
                
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
