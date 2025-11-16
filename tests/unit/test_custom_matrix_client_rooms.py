"""
Unit tests for custom_matrix_client.py room management functions.

Tests cover:
- Room creation
- Room joining with error handling
- RemoteProtocolError handling
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from nio import JoinError, RemoteProtocolError
from src.matrix.client import create_room_if_needed, join_room_if_needed


@pytest.fixture
def mock_logger():
    """Create a mock logger"""
    return Mock()


@pytest.fixture
def mock_client():
    """Create a mock Matrix client"""
    return AsyncMock()


@pytest.mark.unit
class TestCreateRoomIfNeeded:
    """Test room creation functionality"""

    @pytest.mark.asyncio
    async def test_create_room_success(self, mock_client, mock_logger):
        """Test successful room creation"""
        # Mock successful room creation
        mock_response = Mock()
        mock_response.room_id = "!new_room_123:matrix.oculair.ca"
        mock_client.room_create = AsyncMock(return_value=mock_response)

        room_id = await create_room_if_needed(mock_client, mock_logger, "Test Room")

        assert room_id == "!new_room_123:matrix.oculair.ca"
        mock_client.room_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_room_no_room_id_in_response(self, mock_client, mock_logger):
        """Test room creation when response doesn't have room_id"""
        # Mock response without room_id attribute
        mock_response = Mock(spec=[])  # No attributes
        mock_client.room_create = AsyncMock(return_value=mock_response)

        room_id = await create_room_if_needed(mock_client, mock_logger)

        assert room_id is None

    @pytest.mark.asyncio
    async def test_create_room_exception(self, mock_client, mock_logger):
        """Test room creation when exception occurs"""
        # Mock exception during room creation
        mock_client.room_create = AsyncMock(side_effect=Exception("Network error"))

        room_id = await create_room_if_needed(mock_client, mock_logger)

        assert room_id is None


@pytest.mark.unit
class TestJoinRoomIfNeeded:
    """Test room joining functionality"""

    @pytest.mark.asyncio
    async def test_join_room_success(self, mock_client, mock_logger):
        """Test successful room join"""
        # Mock successful join
        mock_response = Mock()
        mock_response.room_id = "!room123:matrix.oculair.ca"
        mock_client.join = AsyncMock(return_value=mock_response)

        result = await join_room_if_needed(mock_client, "!room123:matrix.oculair.ca", mock_logger)

        # Function returns room_id on success
        assert result == "!room123:matrix.oculair.ca"
        mock_client.join.assert_called_once_with("!room123:matrix.oculair.ca")

    @pytest.mark.asyncio
    async def test_join_room_unknown_error(self, mock_client, mock_logger):
        """Test join room when room doesn't exist (M_UNKNOWN)"""
        # Mock JoinError with M_UNKNOWN status
        mock_error = JoinError("Room not found", "M_UNKNOWN")
        mock_client.join = AsyncMock(return_value=mock_error)

        result = await join_room_if_needed(mock_client, "#nonexistent:matrix.oculair.ca", mock_logger)

        assert result is None  # Function logs but doesn't return anything

    @pytest.mark.asyncio
    async def test_join_room_unrecognized_error(self, mock_client, mock_logger):
        """Test join room with M_UNRECOGNIZED status"""
        # Mock JoinError with M_UNRECOGNIZED status
        mock_error = JoinError("Unrecognized request", "M_UNRECOGNIZED")
        mock_client.join = AsyncMock(return_value=mock_error)

        result = await join_room_if_needed(mock_client, "#invalid:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_forbidden_error(self, mock_client, mock_logger):
        """Test join room when bot is not allowed (M_FORBIDDEN)"""
        # Mock JoinError with M_FORBIDDEN status
        mock_error = JoinError("Forbidden", "M_FORBIDDEN")
        mock_client.join = AsyncMock(return_value=mock_error)

        result = await join_room_if_needed(mock_client, "!private:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_cant_join_remote(self, mock_client, mock_logger):
        """Test join room with 'Can't join remote room' error"""
        # Mock JoinError with remote room error message
        mock_error = JoinError("Can't join remote room without a server name", "M_UNKNOWN")
        mock_client.join = AsyncMock(return_value=mock_error)

        result = await join_room_if_needed(mock_client, "#room:remote.server", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_generic_error(self, mock_client, mock_logger):
        """Test join room with generic error status"""
        # Mock JoinError with generic error
        mock_error = JoinError("Some other error", "M_OTHER_ERROR")
        mock_client.join = AsyncMock(return_value=mock_error)

        result = await join_room_if_needed(mock_client, "!room:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_exception(self, mock_client, mock_logger):
        """Test join room when exception occurs"""
        # Mock exception during join
        mock_client.join = AsyncMock(side_effect=Exception("Connection timeout"))

        result = await join_room_if_needed(mock_client, "!room:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_unexpected_response(self, mock_client, mock_logger):
        """Test join room with unexpected response type (no room_id attribute)"""
        # Mock response that's neither JoinError nor has room_id
        mock_response = Mock(spec=[])  # Empty spec = no attributes
        mock_client.join = AsyncMock(return_value=mock_response)

        result = await join_room_if_needed(mock_client, "!room:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_remote_protocol_unknown_token(self, mock_client, mock_logger):
        """Test join room with RemoteProtocolError containing M_UNKNOWN_TOKEN"""
        # Mock RemoteProtocolError with M_UNKNOWN_TOKEN
        mock_error = RemoteProtocolError("M_UNKNOWN_TOKEN: Invalid access token")
        mock_client.join = AsyncMock(side_effect=mock_error)

        result = await join_room_if_needed(mock_client, "!room:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_remote_protocol_forbidden(self, mock_client, mock_logger):
        """Test join room with RemoteProtocolError containing M_FORBIDDEN"""
        # Mock RemoteProtocolError with M_FORBIDDEN
        mock_error = RemoteProtocolError("M_FORBIDDEN: You are not invited")
        mock_client.join = AsyncMock(side_effect=mock_error)

        result = await join_room_if_needed(mock_client, "!room:matrix.oculair.ca", mock_logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_join_room_remote_protocol_other(self, mock_client, mock_logger):
        """Test join room with RemoteProtocolError containing other error"""
        # Mock RemoteProtocolError with other error
        mock_error = RemoteProtocolError("M_LIMIT_EXCEEDED: Too many requests")
        mock_client.join = AsyncMock(side_effect=mock_error)

        result = await join_room_if_needed(mock_client, "!room:matrix.oculair.ca", mock_logger)

        assert result is None
