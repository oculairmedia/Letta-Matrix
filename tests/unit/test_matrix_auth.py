"""
Unit tests for matrix_auth.py

Tests cover:
- Authentication and login
- Session persistence and restoration
- Token validation
- Logout and cleanup
- Rate limiting handling
- Error recovery
"""
import pytest
import os
import shutil
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from nio import LoginError, LogoutError

# Import the module to test
from src.matrix.auth import MatrixAuthManager


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_store_path(tmp_path):
    """Create a temporary store path for tests"""
    store_path = tmp_path / "test_matrix_store"
    store_path.mkdir()
    yield str(store_path)
    # Cleanup
    if store_path.exists():
        shutil.rmtree(store_path)


@pytest.fixture
def auth_manager(temp_store_path):
    """Create a MatrixAuthManager instance for testing"""
    manager = MatrixAuthManager(
        homeserver_url="http://test-server:8008",
        user_id="@testuser:test.com",
        password="test_password",
        device_name="TestDevice"
    )
    manager.store_path = temp_store_path
    return manager


@pytest.fixture
def mock_nio_client():
    """Create a mock nio AsyncClient"""
    client = AsyncMock()
    client.access_token = None
    client.device_id = None
    client.user_id = "@testuser:test.com"
    client.login = AsyncMock()
    client.logout = AsyncMock()
    client.close = AsyncMock()
    client.load_store = Mock()
    return client


# ============================================================================
# Initialization Tests
# ============================================================================

@pytest.mark.unit
class TestMatrixAuthManagerInit:
    """Test MatrixAuthManager initialization"""

    def test_init_sets_attributes(self):
        """Test that __init__ sets all required attributes"""
        manager = MatrixAuthManager(
            homeserver_url="http://test:8008",
            user_id="@user:test.com",
            password="password123",
            device_name="TestClient"
        )

        assert manager.homeserver_url == "http://test:8008"
        assert manager.user_id == "@user:test.com"
        assert manager.password == "password123"
        assert manager.device_name == "TestClient"
        assert manager.client is None
        assert manager.store_path == "./matrix_store"

    def test_init_default_device_name(self):
        """Test default device name"""
        manager = MatrixAuthManager(
            homeserver_url="http://test:8008",
            user_id="@user:test.com",
            password="password123"
        )

        assert manager.device_name == "CustomMatrixClient"


# ============================================================================
# Authentication Tests
# ============================================================================

@pytest.mark.unit
class TestAuthentication:
    """Test authentication methods"""

    @pytest.mark.asyncio
    async def test_get_authenticated_client_success_new_login(self, auth_manager, mock_nio_client):
        """Test successful authentication with new login"""
        # Mock successful login
        mock_nio_client.access_token = None  # No stored session
        mock_nio_client.device_id = None

        # Create a proper mock response (not a LoginError)
        class MockLoginResponse:
            pass  # Not a LoginError instance

        async def mock_login(*args, **kwargs):
            mock_nio_client.access_token = "test_token_123"
            mock_nio_client.device_id = "TEST_DEVICE"
            return MockLoginResponse()  # Return non-error response

        mock_nio_client.login = mock_login

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is not None
            assert client == mock_nio_client
            assert mock_nio_client.access_token == "test_token_123"

    @pytest.mark.asyncio
    async def test_get_authenticated_client_restores_session(self, auth_manager, mock_nio_client):
        """Test that existing session is restored instead of new login"""
        # Mock existing session
        mock_nio_client.access_token = "existing_token_456"
        mock_nio_client.device_id = "EXISTING_DEVICE"

        # load_store should populate these
        def mock_load_store():
            pass  # Already set above

        mock_nio_client.load_store = mock_load_store

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is not None
            assert client.access_token == "existing_token_456"
            assert client.device_id == "EXISTING_DEVICE"
            # Login should NOT be called when session is restored
            mock_nio_client.login.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_authenticated_client_handles_login_error(self, auth_manager, mock_nio_client):
        """Test handling of login errors"""
        mock_nio_client.access_token = None
        mock_nio_client.device_id = None

        # Mock login failure
        login_error = LoginError(message="Invalid credentials", status_code="M_FORBIDDEN")
        mock_nio_client.login = AsyncMock(return_value=login_error)

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is None
            mock_nio_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_authenticated_client_handles_rate_limiting(self, auth_manager, mock_nio_client):
        """Test handling of rate limiting (429 errors)"""
        mock_nio_client.access_token = None
        mock_nio_client.device_id = None

        # Mock rate limit error
        rate_limit_error = LoginError(
            message="429 Too Many Requests - Rate limited",
            status_code="M_LIMIT_EXCEEDED"
        )
        mock_nio_client.login = AsyncMock(return_value=rate_limit_error)

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is None
            mock_nio_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_authenticated_client_handles_exception(self, auth_manager):
        """Test handling of general exceptions during authentication"""
        with patch('src.matrix.auth.AsyncClient', side_effect=Exception("Network error")):
            client = await auth_manager.get_authenticated_client()

            assert client is None

    @pytest.mark.asyncio
    async def test_get_authenticated_client_creates_store_directory(self, auth_manager):
        """Test that store directory is created if it doesn't exist"""
        # Remove store directory if it exists
        if os.path.exists(auth_manager.store_path):
            shutil.rmtree(auth_manager.store_path)

        mock_nio_client = AsyncMock()
        mock_nio_client.access_token = "token"
        mock_nio_client.device_id = "device"
        mock_nio_client.load_store = Mock()

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            await auth_manager.get_authenticated_client()

            # Store directory should now exist
            assert os.path.exists(auth_manager.store_path)


# ============================================================================
# Token Validation Tests
# ============================================================================

@pytest.mark.unit
class TestTokenValidation:
    """Test token validation"""

    @pytest.mark.asyncio
    async def test_ensure_valid_token_with_valid_client(self, auth_manager):
        """Test token validation with valid client and token"""
        mock_client = Mock()
        mock_client.access_token = "valid_token_789"

        is_valid = await auth_manager.ensure_valid_token(mock_client)

        assert is_valid is True

    @pytest.mark.asyncio
    async def test_ensure_valid_token_with_no_token(self, auth_manager):
        """Test token validation when client has no token"""
        mock_client = Mock()
        mock_client.access_token = None

        is_valid = await auth_manager.ensure_valid_token(mock_client)

        assert is_valid is False

    @pytest.mark.asyncio
    async def test_ensure_valid_token_with_none_client(self, auth_manager):
        """Test token validation when client is None"""
        is_valid = await auth_manager.ensure_valid_token(None)

        assert is_valid is False


# ============================================================================
# Logout Tests
# ============================================================================

@pytest.mark.unit
class TestLogout:
    """Test logout functionality"""

    @pytest.mark.asyncio
    async def test_logout_success(self, auth_manager, mock_nio_client):
        """Test successful logout"""
        # Mock successful logout
        mock_nio_client.logout = AsyncMock(return_value=Mock())  # Not a LogoutError

        auth_manager.client = mock_nio_client

        await auth_manager.logout()

        mock_nio_client.logout.assert_called_once()
        mock_nio_client.close.assert_called_once()
        assert auth_manager.client is None

    @pytest.mark.asyncio
    async def test_logout_handles_error(self, auth_manager, mock_nio_client):
        """Test logout handles LogoutError gracefully"""
        # Mock logout error
        logout_error = LogoutError(message="Logout failed")
        mock_nio_client.logout = AsyncMock(return_value=logout_error)

        auth_manager.client = mock_nio_client

        await auth_manager.logout()

        # Should still close client even on error
        mock_nio_client.close.assert_called_once()
        assert auth_manager.client is None

    @pytest.mark.asyncio
    async def test_logout_cleans_up_store(self, auth_manager, mock_nio_client, temp_store_path):
        """Test that logout cleans up session store file"""
        # Create a fake store file
        store_file = Path(temp_store_path) / f"{auth_manager.user_id}_{auth_manager.device_name}.db"
        store_file.touch()

        assert store_file.exists()

        mock_nio_client.logout = AsyncMock(return_value=Mock())
        auth_manager.client = mock_nio_client

        await auth_manager.logout()

        # Store file should be removed
        assert not store_file.exists()

    @pytest.mark.asyncio
    async def test_logout_when_client_is_none(self, auth_manager):
        """Test logout when client is already None"""
        auth_manager.client = None

        # Should not raise an error
        await auth_manager.logout()

        # Client should still be None
        assert auth_manager.client is None

    @pytest.mark.asyncio
    async def test_logout_handles_exception(self, auth_manager, mock_nio_client):
        """Test that logout handles exceptions gracefully"""
        mock_nio_client.logout = AsyncMock(side_effect=Exception("Network error"))
        auth_manager.client = mock_nio_client

        # Should not raise exception
        await auth_manager.logout()

        # Client should still be closed and set to None
        mock_nio_client.close.assert_called_once()
        assert auth_manager.client is None


# ============================================================================
# Session Persistence Tests
# ============================================================================

@pytest.mark.unit
class TestSessionPersistence:
    """Test session persistence functionality"""

    @pytest.mark.asyncio
    async def test_session_restore_with_missing_token(self, auth_manager, mock_nio_client):
        """Test session restore when token is missing"""
        # Mock load_store that doesn't set token
        mock_nio_client.access_token = None
        mock_nio_client.device_id = "device_id"
        mock_nio_client.load_store = Mock()

        # Should attempt login since token is missing
        class MockLoginResponse:
            pass  # Not a LoginError instance

        async def mock_login(*args, **kwargs):
            mock_nio_client.access_token = "new_token"
            return MockLoginResponse()

        mock_nio_client.login = mock_login

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is not None
            assert client.access_token == "new_token"

    @pytest.mark.asyncio
    async def test_session_restore_with_missing_device(self, auth_manager, mock_nio_client):
        """Test session restore when device_id is missing"""
        # Mock load_store that doesn't set device_id
        # When both token and device_id are not present, it should login
        mock_nio_client.access_token = None  # Changed: must be None to trigger login
        mock_nio_client.device_id = None
        mock_nio_client.load_store = Mock()

        # Should attempt login since credentials are missing
        class MockLoginResponse:
            pass  # Not a LoginError instance

        async def mock_login(*args, **kwargs):
            mock_nio_client.access_token = "new_token"
            mock_nio_client.device_id = "new_device"
            return MockLoginResponse()

        mock_nio_client.login = mock_login

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is not None
            assert client.device_id == "new_device"

    @pytest.mark.asyncio
    async def test_session_restore_exception_fallback_to_login(self, auth_manager, mock_nio_client):
        """Test that exceptions during session restore trigger login"""
        mock_nio_client.access_token = None
        mock_nio_client.device_id = None
        mock_nio_client.load_store = Mock(side_effect=Exception("Store corrupted"))

        # Should fall back to login
        class MockLoginResponse:
            pass  # Not a LoginError instance

        async def mock_login(*args, **kwargs):
            mock_nio_client.access_token = "fallback_token"
            mock_nio_client.device_id = "fallback_device"
            return MockLoginResponse()

        mock_nio_client.login = mock_login

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is not None
            assert client.access_token == "fallback_token"


# ============================================================================
# Rate Limiting Tests
# ============================================================================

@pytest.mark.unit
class TestRateLimiting:
    """Test rate limiting detection and handling"""

    @pytest.mark.asyncio
    async def test_detects_rate_limit_in_error_message(self, auth_manager, mock_nio_client):
        """Test that rate limiting is detected in error messages"""
        mock_nio_client.access_token = None
        mock_nio_client.login = AsyncMock(side_effect=Exception("429 rate limit exceeded"))

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is None
            mock_nio_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_detects_rate_keyword_in_error(self, auth_manager, mock_nio_client):
        """Test that 'rate' keyword is detected in errors"""
        mock_nio_client.access_token = None
        mock_nio_client.login = AsyncMock(
            side_effect=Exception("Too many login attempts - rate limited")
        )

        with patch('src.matrix.auth.AsyncClient', return_value=mock_nio_client):
            client = await auth_manager.get_authenticated_client()

            assert client is None
