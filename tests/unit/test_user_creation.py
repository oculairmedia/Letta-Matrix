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
            assert exists == "exists_auth_failed"

    @pytest.mark.asyncio
    async def test_check_user_exists_user_not_found(self, user_manager):
        """Test check_user_exists returns False when user doesn't exist (404)"""
        mock_response = AsyncMock()
        mock_response.status = 404
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            exists = await user_manager.check_user_exists("testuser")
            assert exists == "not_found"

    @pytest.mark.asyncio
    async def test_check_user_exists_m_unknown(self, user_manager):
        """Test check_user_exists returns False for M_UNKNOWN error"""
        mock_response = AsyncMock()
        mock_response.status = 403
        mock_response.json = AsyncMock(return_value={"errcode": "M_UNKNOWN"})
        
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
            
            exists = await user_manager.check_user_exists("testuser")
            assert exists == "not_found"

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
            mock_check.return_value = "not_found"
            
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
            mock_check.return_value = "exists_auth_failed"
            
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
            mock_check.side_effect = ["exists_auth_failed", "not_found"]
            
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
            mock_check.side_effect = [Exception("Network error"), "not_found"]
            
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

    @pytest.mark.asyncio
    async def test_update_display_name_success(self, user_manager):
        """Test successfully updating display name by logging in as user"""
        # Mock successful login response
        mock_login_response = MagicMock()
        mock_login_response.status = 200
        mock_login_response.json = AsyncMock(return_value={"access_token": "user_token_123"})
        mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
        mock_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock successful profile update response
        mock_profile_response = MagicMock()
        mock_profile_response.status = 200
        mock_profile_response.__aenter__ = AsyncMock(return_value=mock_profile_response)
        mock_profile_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_login_response)
        mock_session.put = MagicMock(return_value=mock_profile_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.update_display_name(
                "@testuser:test.com",
                "New Display Name",
                "user_password"
            )

            assert result is True
            mock_session.post.assert_called_once()  # Login call
            mock_session.put.assert_called_once()   # Profile update call
            # Verify the correct profile URL was called
            call_args = mock_session.put.call_args
            assert "/_matrix/client/v3/profile/" in call_args[0][0]
            assert "@testuser:test.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_update_display_name_no_password(self, user_manager):
        """Test update_display_name fails when no password provided"""
        result = await user_manager.update_display_name(
            "@testuser:test.com",
            "New Name"
        )

        assert result is False

        # Also test with None explicitly
        result = await user_manager.update_display_name(
            "@testuser:test.com",
            "New Name",
            None
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_update_display_name_login_failure(self, user_manager):
        """Test update_display_name handles login failures"""
        # Mock failed login response
        mock_login_response = MagicMock()
        mock_login_response.status = 403
        mock_login_response.text = AsyncMock(return_value="Forbidden")
        mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
        mock_login_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_login_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.update_display_name(
                "@testuser:test.com",
                "New Name",
                "wrong_password"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_update_display_name_profile_update_failure(self, user_manager):
        """Test update_display_name handles profile update failures"""
        # Mock successful login response
        mock_login_response = MagicMock()
        mock_login_response.status = 200
        mock_login_response.json = AsyncMock(return_value={"access_token": "user_token_123"})
        mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
        mock_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock failed profile update response
        mock_profile_response = MagicMock()
        mock_profile_response.status = 403
        mock_profile_response.text = AsyncMock(return_value="Forbidden")
        mock_profile_response.__aenter__ = AsyncMock(return_value=mock_profile_response)
        mock_profile_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_login_response)
        mock_session.put = MagicMock(return_value=mock_profile_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.update_display_name(
                "@testuser:test.com",
                "New Name",
                "password"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_update_display_name_exception_handling(self, user_manager):
        """Test update_display_name handles exceptions gracefully"""
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session_class.side_effect = Exception("Network error")

            result = await user_manager.update_display_name(
                "@testuser:test.com",
                "New Name",
                "password"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_set_user_display_name_success(self, user_manager):
        """Test successfully setting display name with user's own token"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_put = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.put = mock_put
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.set_user_display_name(
                "@user:test.com",
                "Display Name",
                "user_access_token"
            )

            assert result is True
            mock_put.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_user_display_name_failure(self, user_manager):
        """Test set_user_display_name handles failures"""
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_put = MagicMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.put = mock_put
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.set_user_display_name(
                "@user:test.com",
                "Display Name",
                "user_token"
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_set_user_display_name_exception(self, user_manager):
        """Test set_user_display_name handles exceptions"""
        with patch('aiohttp.ClientSession') as mock_session:
            mock_session.side_effect = Exception("Connection error")

            result = await user_manager.set_user_display_name(
                "@user:test.com",
                "Display Name",
                "user_token"
            )

            assert result is False


class TestRegistrationTokenFlow:
    """Test the two-step registration with m.login.registration_token (Tuwunel)"""

    @pytest.fixture
    def user_manager(self):
        """Create a MatrixUserManager instance for testing"""
        return MatrixUserManager(
            homeserver_url="http://test-homeserver:8008",
            admin_username="@admin:test.com",
            admin_password="admin_pass"
        )

    @pytest.mark.asyncio
    async def test_create_user_with_registration_token(self, user_manager):
        """Test user creation with m.login.registration_token (Tuwunel flow)"""
        # Step 1: Initial request returns 401 with session and flows
        initial_response = MagicMock()
        initial_response.status = 401
        initial_response.json = AsyncMock(return_value={
            "session": "test_session_id",
            "flows": [{"stages": ["m.login.registration_token"]}]
        })
        initial_response.__aenter__ = AsyncMock(return_value=initial_response)
        initial_response.__aexit__ = AsyncMock(return_value=None)

        # Step 2: Complete registration returns 200 with access token
        complete_response = MagicMock()
        complete_response.status = 200
        complete_response.json = AsyncMock(return_value={
            "access_token": "new_user_token",
            "user_id": "@testuser:matrix.oculair.ca"
        })
        complete_response.__aenter__ = AsyncMock(return_value=complete_response)
        complete_response.__aexit__ = AsyncMock(return_value=None)

        # Mock session that returns different responses for each POST call
        mock_post = MagicMock(side_effect=[initial_response, complete_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            with patch.dict('os.environ', {'MATRIX_REGISTRATION_TOKEN': 'test_token_123'}):
                with patch.object(user_manager, 'set_user_display_name', new_callable=AsyncMock) as mock_set_display:
                    mock_set_display.return_value = True

                    result = await user_manager.create_matrix_user("testuser", "password", "Test User")

                    assert result is True
                    # Verify two POST calls were made (initial + complete)
                    assert mock_post.call_count == 2

                    # Verify the second call used registration_token auth
                    second_call_args = mock_post.call_args_list[1]
                    call_json = second_call_args[1].get('json', {})
                    assert call_json.get('auth', {}).get('type') == 'm.login.registration_token'
                    assert call_json.get('auth', {}).get('token') == 'test_token_123'
                    assert call_json.get('auth', {}).get('session') == 'test_session_id'

    @pytest.mark.asyncio
    async def test_create_user_with_dummy_auth_fallback(self, user_manager):
        """Test user creation falls back to m.login.dummy when token not required"""
        # Step 1: Initial request returns 401 with session and dummy flow
        initial_response = MagicMock()
        initial_response.status = 401
        initial_response.json = AsyncMock(return_value={
            "session": "test_session_id",
            "flows": [{"stages": ["m.login.dummy"]}]
        })
        initial_response.__aenter__ = AsyncMock(return_value=initial_response)
        initial_response.__aexit__ = AsyncMock(return_value=None)

        # Step 2: Complete registration returns 200
        complete_response = MagicMock()
        complete_response.status = 200
        complete_response.json = AsyncMock(return_value={
            "access_token": "new_user_token",
            "user_id": "@testuser:test.com"
        })
        complete_response.__aenter__ = AsyncMock(return_value=complete_response)
        complete_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(side_effect=[initial_response, complete_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            with patch.object(user_manager, 'set_user_display_name', new_callable=AsyncMock) as mock_set_display:
                mock_set_display.return_value = True

                result = await user_manager.create_matrix_user("testuser", "password", "Test User")

                assert result is True
                # Verify the second call used dummy auth
                second_call_args = mock_post.call_args_list[1]
                call_json = second_call_args[1].get('json', {})
                assert call_json.get('auth', {}).get('type') == 'm.login.dummy'

    @pytest.mark.asyncio
    async def test_create_user_fails_without_registration_token(self, user_manager):
        """Test user creation fails when token required but not configured"""
        # Initial request returns 401 requiring registration_token
        initial_response = MagicMock()
        initial_response.status = 401
        initial_response.json = AsyncMock(return_value={
            "session": "test_session_id",
            "flows": [{"stages": ["m.login.registration_token"]}]
        })
        initial_response.__aenter__ = AsyncMock(return_value=initial_response)
        initial_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=initial_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            # Clear the registration token env var
            with patch.dict('os.environ', {'MATRIX_REGISTRATION_TOKEN': ''}, clear=False):
                # Remove the key entirely if it exists
                import os
                if 'MATRIX_REGISTRATION_TOKEN' in os.environ:
                    del os.environ['MATRIX_REGISTRATION_TOKEN']

                result = await user_manager.create_matrix_user("testuser", "password", "Test User")

                # Should fail because no token is available
                assert result is False

    @pytest.mark.asyncio
    async def test_create_user_no_session_returned(self, user_manager):
        """Test user creation fails when no session is returned"""
        initial_response = MagicMock()
        initial_response.status = 401
        initial_response.json = AsyncMock(return_value={
            # No session field
            "flows": [{"stages": ["m.login.registration_token"]}]
        })
        initial_response.__aenter__ = AsyncMock(return_value=initial_response)
        initial_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=initial_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            result = await user_manager.create_matrix_user("testuser", "password", "Test User")
            assert result is False

    @pytest.mark.asyncio
    async def test_create_user_registration_completes_on_first_request(self, user_manager):
        """Test user creation when registration succeeds without auth (rare case)"""
        # Some servers might accept registration without auth
        immediate_response = MagicMock()
        immediate_response.status = 200
        immediate_response.json = AsyncMock(return_value={
            "access_token": "immediate_token",
            "user_id": "@testuser:test.com"
        })
        immediate_response.__aenter__ = AsyncMock(return_value=immediate_response)
        immediate_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(return_value=immediate_response)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            with patch.object(user_manager, 'set_user_display_name', new_callable=AsyncMock) as mock_set_display:
                mock_set_display.return_value = True

                result = await user_manager.create_matrix_user("testuser", "password", "Test User")

                assert result is True
                # Only one POST call needed
                assert mock_post.call_count == 1

    @pytest.mark.asyncio
    async def test_create_user_second_step_fails(self, user_manager):
        """Test user creation when second step (complete registration) fails"""
        initial_response = MagicMock()
        initial_response.status = 401
        initial_response.json = AsyncMock(return_value={
            "session": "test_session",
            "flows": [{"stages": ["m.login.registration_token"]}]
        })
        initial_response.__aenter__ = AsyncMock(return_value=initial_response)
        initial_response.__aexit__ = AsyncMock(return_value=None)

        # Second step fails with 403
        complete_response = MagicMock()
        complete_response.status = 403
        complete_response.text = AsyncMock(return_value="Invalid registration token")
        complete_response.__aenter__ = AsyncMock(return_value=complete_response)
        complete_response.__aexit__ = AsyncMock(return_value=None)

        mock_post = MagicMock(side_effect=[initial_response, complete_response])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            with patch.dict('os.environ', {'MATRIX_REGISTRATION_TOKEN': 'wrong_token'}):
                result = await user_manager.create_matrix_user("testuser", "password", "Test User")
                assert result is False


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
            mock_check.side_effect = ["not_found", "exists_auth_failed"]
            
            with patch.object(user_manager, 'create_matrix_user', new_callable=AsyncMock) as mock_create:
                mock_create.return_value = True
                
                core_users = [("@test:test.com", "pass", "Test User")]
                
                # First run - creates user
                await user_manager.ensure_core_users_exist(core_users)
                
                # Second run - skips creation
                await user_manager.ensure_core_users_exist(core_users)
                
                # User should only be created once
                assert mock_create.call_count == 1
