"""
Unit tests for Matrix user creation and core user bootstrapping
Tests the automatic user creation functionality added in Sprint 4
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from src.core.user_manager import MatrixUserManager


class TestUserCreation:
    """Test user creation functionality"""

    @pytest.fixture
    def user_manager(self):
        """Create a MatrixUserManager instance for testing"""
        return MatrixUserManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass"
        )

    @pytest.mark.asyncio
    async def test_check_user_exists_user_found(self, user_manager):
        """Test check_user_exists returns True when user exists (403 Forbidden)"""
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.json = AsyncMock(return_value={"errcode": "M_FORBIDDEN"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            exists = await user_manager.check_user_exists("testuser")
            assert exists is True

    @pytest.mark.asyncio
    async def test_check_user_exists_user_not_found(self, user_manager):
        """Test check_user_exists returns False when user doesn't exist (404)"""
        mock_response = AsyncMock()
        mock_response.status = 404
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            exists = await user_manager.check_user_exists("testuser")
            assert exists is False

    @pytest.mark.asyncio
    async def test_check_user_exists_m_unknown(self, user_manager):
        """Test check_user_exists returns False for M_UNKNOWN error"""
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.json = AsyncMock(return_value={"errcode": "M_UNKNOWN"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            exists = await user_manager.check_user_exists("testuser")
            assert exists is False

    @pytest.mark.asyncio
    async def test_create_matrix_user_success(self, user_manager):
        """Test successful user creation"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "test_token",
            "user_id": "@testuser:test.com"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Mock set_user_display_name
            with patch.object(user_manager, 'set_user_display_name', new_callable=AsyncMock) as mock_set_display:
                mock_set_display.return_value = True
                
                result = await user_manager.create_matrix_user("testuser", "password123", "Test User")
                assert result is True

    @pytest.mark.asyncio
    async def test_create_matrix_user_already_exists(self, user_manager):
        """Test creating user that already exists returns True"""
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={"errcode": "M_USER_IN_USE"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.create_matrix_user("testuser", "password123", "Test User")
            assert result is True

    @pytest.mark.asyncio
    async def test_create_matrix_user_failure(self, user_manager):
        """Test failed user creation returns False"""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal server error")
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            result = await user_manager.create_matrix_user("testuser", "password123", "Test User")
            assert result is False

    @pytest.mark.asyncio
    async def test_ensure_core_users_exist_creates_missing_users(self, user_manager):
        """Test ensure_core_users_exist creates missing users"""
        core_users = [
            ("@admin:test.com", "admin_pass", "Admin"),
            ("@letta:test.com", "letta_pass", "Letta Bot")
        ]
        
        # Mock check_user_exists to return False (users don't exist)
        with patch.object(user_manager, 'check_user_exists', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = False
            
            # Mock create_matrix_user to return True (successful creation)
            with patch.object(user_manager, 'create_matrix_user', new_callable=AsyncMock) as mock_create:
                mock_create.return_value = True
                
                await user_manager.ensure_core_users_exist(core_users)
                
                # Verify both users were checked and created
                assert mock_check.call_count == 2
                assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_ensure_core_users_exist_skips_existing_users(self, user_manager):
        """Test ensure_core_users_exist skips users that already exist"""
        core_users = [
            ("@admin:test.com", "admin_pass", "Admin"),
            ("@letta:test.com", "letta_pass", "Letta Bot")
        ]
        
        # Mock check_user_exists to return True (users exist)
        with patch.object(user_manager, 'check_user_exists', new_callable=AsyncMock) as mock_check:
            mock_check.return_value = True
            
            # Mock create_matrix_user (should not be called)
            with patch.object(user_manager, 'create_matrix_user', new_callable=AsyncMock) as mock_create:
                await user_manager.ensure_core_users_exist(core_users)
                
                # Verify users were checked but not created
                assert mock_check.call_count == 2
                assert mock_create.call_count == 0

    @pytest.mark.asyncio
    async def test_ensure_core_users_exist_mixed_scenario(self, user_manager):
        """Test ensure_core_users_exist with some existing and some new users"""
        core_users = [
            ("@admin:test.com", "admin_pass", "Admin"),
            ("@letta:test.com", "letta_pass", "Letta Bot")
        ]
        
        # Mock check_user_exists: first exists, second doesn't
        with patch.object(user_manager, 'check_user_exists', new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [True, False]
            
            # Mock create_matrix_user
            with patch.object(user_manager, 'create_matrix_user', new_callable=AsyncMock) as mock_create:
                mock_create.return_value = True
                
                await user_manager.ensure_core_users_exist(core_users)
                
                # Verify: 2 checks, 1 creation
                assert mock_check.call_count == 2
                assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_ensure_core_users_exist_handles_exceptions(self, user_manager):
        """Test ensure_core_users_exist handles exceptions gracefully"""
        core_users = [
            ("@admin:test.com", "admin_pass", "Admin"),
            ("@letta:test.com", "letta_pass", "Letta Bot")
        ]
        
        # Mock check_user_exists to raise an exception for first user
        with patch.object(user_manager, 'check_user_exists', new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [Exception("Network error"), False]
            
            # Mock create_matrix_user
            with patch.object(user_manager, 'create_matrix_user', new_callable=AsyncMock) as mock_create:
                mock_create.return_value = True
                
                # Should not raise, should continue to next user
                await user_manager.ensure_core_users_exist(core_users)
                
                # Second user should still be processed
                assert mock_check.call_count == 2
                assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_get_admin_token_success(self, user_manager):
        """Test successful admin token retrieval"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"access_token": "admin_token_123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_post = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            token = await user_manager.get_admin_token()
            assert token == "admin_token_123"
            assert user_manager.admin_token == "admin_token_123"

    @pytest.mark.asyncio
    async def test_get_admin_token_uses_cache(self, user_manager):
        """Test that get_admin_token uses cached token"""
        user_manager.admin_token = "cached_token"
        
        # Should not make any HTTP requests
        token = await user_manager.get_admin_token()
        assert token == "cached_token"

    @pytest.mark.asyncio
    async def test_get_admin_token_failure(self, user_manager):
        """Test failed admin token retrieval"""
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.text = AsyncMock(return_value="Forbidden")
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            token = await user_manager.get_admin_token()
            assert token is None

    def test_generate_username(self, user_manager):
        """Test username generation from agent ID"""
        # Test with agent- prefix
        username = user_manager.generate_username("Test Agent", "agent-abc-123-def")
        assert username == "agent_abc_123_def"
        
        # Test without agent- prefix
        username = user_manager.generate_username("Test Agent", "xyz-456-uvw")
        assert username == "agent_xyz_456_uvw"
        
        # Test with special characters
        username = user_manager.generate_username("Test Agent", "agent-test@123#456")
        assert username == "agent_test123456"

    def test_generate_password_dev_mode(self, user_manager):
        """Test password generation in dev mode"""
        with patch.dict('os.environ', {'DEV_MODE': 'true'}):
            password = user_manager.generate_password()
            assert password == "password"

    def test_generate_password_production(self, user_manager):
        """Test password generation in production"""
        with patch.dict('os.environ', {'DEV_MODE': 'false'}):
            password = user_manager.generate_password()
            assert len(password) == 16
            assert password.isalnum()


class TestUserCreationIntegration:
    """Integration tests for user creation workflow"""

    @pytest.mark.asyncio
    async def test_complete_user_creation_workflow(self):
        """Test complete workflow: check user doesn't exist, create, verify"""
        user_manager = MatrixUserManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass"
        )
        
        # Simulate user doesn't exist (check returns 404)
        check_response = MagicMock()
        check_response.status = 404
        check_response.__aenter__ = AsyncMock(return_value=check_response)
        check_response.__aexit__ = AsyncMock(return_value=None)
        
        # Simulate successful creation
        create_response = MagicMock()
        create_response.status = 200
        create_response.json = AsyncMock(return_value={
            "access_token": "new_user_token",
            "user_id": "@newuser:test.com"
        })
        create_response.__aenter__ = AsyncMock(return_value=create_response)
        create_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_post = MagicMock(side_effect=[check_response, create_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Check user doesn't exist
            exists = await user_manager.check_user_exists("newuser")
            
            # Create user
            with patch.object(user_manager, 'set_user_display_name', new_callable=AsyncMock) as mock_set_display:
                mock_set_display.return_value = True
                created = await user_manager.create_matrix_user("newuser", "pass123", "New User")
                
            assert created is True

    @pytest.mark.asyncio
    async def test_idempotent_user_creation(self):
        """Test that creating a user multiple times is idempotent"""
        user_manager = MatrixUserManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass"
        )
        
        # First call: user doesn't exist, gets created
        # Second call: user already exists
        with patch.object(user_manager, 'check_user_exists', new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = [False, True]
            
            with patch.object(user_manager, 'create_matrix_user', new_callable=AsyncMock) as mock_create:
                mock_create.return_value = True
                
                core_users = [("@test:test.com", "pass", "Test User")]
                
                # First run - creates user
                await user_manager.ensure_core_users_exist(core_users)
                
                # Second run - skips creation
                await user_manager.ensure_core_users_exist(core_users)
                
                # User should only be created once
                assert mock_create.call_count == 1
