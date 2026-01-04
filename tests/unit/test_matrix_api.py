"""
Unit tests for matrix_api.py

Tests cover:
- FastAPI endpoints
- Request/response models
- Authentication
- Message operations
- Room management
- Error handling
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, patch
import json

# Import the FastAPI app
from src.api.app import (
    app,
    LoginRequest,
    LoginResponse,
    SendMessageRequest,
    SendMessageResponse,
    GetMessagesRequest,
    MatrixMessage,
    GetMessagesResponse,
    RoomInfo,
    ListRoomsResponse,
    NewAgentNotification,
    WebhookResponse
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def client():
    """Create FastAPI test client"""
    return TestClient(app)


# ============================================================================
# Pydantic Model Tests
# ============================================================================

class TestPydanticModels:
    """Test Pydantic request/response models"""

    def test_login_request_model(self):
        """Test LoginRequest model validation"""
        request = LoginRequest(
            homeserver="http://test:8008",
            user_id="@test:matrix.test",
            password="test_pass",
            device_name="test_device"
        )

        assert request.homeserver == "http://test:8008"
        assert request.user_id == "@test:matrix.test"
        assert request.device_name == "test_device"

    def test_login_request_default_device_name(self):
        """Test LoginRequest with default device name"""
        request = LoginRequest(
            homeserver="http://test:8008",
            user_id="@test:matrix.test",
            password="test_pass"
        )

        assert request.device_name == "matrix_api"

    def test_login_response_model(self):
        """Test LoginResponse model"""
        response = LoginResponse(
            success=True,
            access_token="token123",
            device_id="device123",
            user_id="@test:matrix.test",
            message="Login successful"
        )

        assert response.success is True
        assert response.access_token == "token123"
        assert response.message == "Login successful"

    def test_send_message_request_model(self):
        """Test SendMessageRequest model"""
        request = SendMessageRequest(
            room_id="!room:matrix.test",
            message="Hello world",
            access_token="token123",
            homeserver="http://test:8008"
        )

        assert request.room_id == "!room:matrix.test"
        assert request.message == "Hello world"

    def test_send_message_response_model(self):
        """Test SendMessageResponse model"""
        response = SendMessageResponse(
            success=True,
            event_id="$event123",
            message="Message sent successfully"
        )

        assert response.success is True
        assert response.event_id == "$event123"

    def test_matrix_message_model(self):
        """Test MatrixMessage model"""
        message = MatrixMessage(
            sender="@user:matrix.test",
            body="Test message",
            timestamp=1704067200000,
            formatted_time="2025-01-01 00:00:00",
            event_id="$event123"
        )

        assert message.sender == "@user:matrix.test"
        assert message.body == "Test message"
        assert message.timestamp == 1704067200000

    def test_get_messages_request_model(self):
        """Test GetMessagesRequest model"""
        request = GetMessagesRequest(
            room_id="!room:matrix.test",
            access_token="token123",
            homeserver="http://test:8008",
            limit=10
        )

        assert request.room_id == "!room:matrix.test"
        assert request.limit == 10

    def test_get_messages_request_default_limit(self):
        """Test GetMessagesRequest with default limit"""
        request = GetMessagesRequest(
            room_id="!room:matrix.test",
            access_token="token123",
            homeserver="http://test:8008"
        )

        assert request.limit == 5  # Default value

    def test_room_info_model(self):
        """Test RoomInfo model"""
        room = RoomInfo(
            room_id="!room:matrix.test",
            room_name="Test Room"
        )

        assert room.room_id == "!room:matrix.test"
        assert room.room_name == "Test Room"

    def test_new_agent_notification_model(self):
        """Test NewAgentNotification model"""
        notification = NewAgentNotification(
            agent_id="agent-123",
            timestamp="2025-01-01T00:00:00Z"
        )

        assert notification.agent_id == "agent-123"
        assert notification.timestamp == "2025-01-01T00:00:00Z"


# ============================================================================
# Health Check Tests
# ============================================================================

@pytest.mark.unit
class TestHealthCheck:
    """Test health check endpoint"""

    def test_health_check_endpoint(self, client):
        """Test /health endpoint returns 200"""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data


# ============================================================================
# Login Endpoint Tests
# ============================================================================

@pytest.mark.unit
class TestLoginEndpoint:
    """Test login endpoint"""

    @patch('src.api.app.aiohttp.ClientSession')
    def test_login_success(self, mock_session, client):
        """Test successful login"""
        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "user_id": "@test:matrix.test",
            "access_token": "token123",
            "device_id": "device123"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(return_value=mock_response)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_session.return_value = mock_session_instance

        # Make request
        response = client.post("/login", json={
            "homeserver": "http://test:8008",
            "user_id": "@test:matrix.test",
            "password": "test_pass"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["access_token"] == "token123"

    def test_login_missing_fields(self, client):
        """Test login with missing required fields"""
        response = client.post("/login", json={
            "homeserver": "http://test:8008",
            # Missing user_id and password
        })

        assert response.status_code == 422  # Validation error


# ============================================================================
# Send Message Endpoint Tests
# ============================================================================

@pytest.mark.unit
class TestSendMessageEndpoint:
    """Test send message endpoint"""

    @patch('src.api.app.aiohttp.ClientSession')
    def test_send_message_success(self, mock_session, client):
        """Test successfully sending a message"""
        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"event_id": "$event123"})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = AsyncMock()
        # send_message uses PUT not POST
        mock_session_instance.put = Mock(return_value=mock_response)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_session.return_value = mock_session_instance

        # Make request
        response = client.post("/messages/send", json={
            "room_id": "!room:matrix.test",
            "message": "Test message",
            "access_token": "token123",
            "homeserver": "http://test:8008"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["event_id"] == "$event123"

    def test_send_message_validation(self, client):
        """Test send message with invalid data"""
        response = client.post("/messages/send", json={
            "room_id": "!room:matrix.test",
            # Missing required fields
        })

        assert response.status_code == 422


# ============================================================================
# Get Messages Endpoint Tests
# ============================================================================

@pytest.mark.unit
class TestGetMessagesEndpoint:
    """Test get messages endpoint"""

    @patch('src.api.app.aiohttp.ClientSession')
    def test_get_messages_success(self, mock_session, client):
        """Test successfully getting messages"""
        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "chunk": [
                {
                    "type": "m.room.message",  # Required field for filtering
                    "sender": "@user:matrix.test",
                    "content": {"body": "Test message"},
                    "origin_server_ts": 1704067200000,
                    "event_id": "$event123"
                }
            ]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = AsyncMock()
        mock_session_instance.get = Mock(return_value=mock_response)
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_session.return_value = mock_session_instance

        # Make request
        response = client.post("/messages/get", json={
            "room_id": "!room:matrix.test",
            "access_token": "token123",
            "homeserver": "http://test:8008",
            "limit": 5
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["messages"]) > 0


# ============================================================================
# List Rooms Endpoint Tests
# ============================================================================

@pytest.mark.unit
class TestListRoomsEndpoint:
    """Test list rooms endpoint"""

    @patch('src.api.app.aiohttp.ClientSession')
    def test_list_rooms_success(self, mock_session, client):
        """Test successfully listing rooms"""
        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "joined_rooms": [
                "!room1:matrix.test",
                "!room2:matrix.test"
            ]
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        # Mock room state responses
        mock_state_response = AsyncMock()
        mock_state_response.status = 200
        mock_state_response.json = AsyncMock(return_value=[
            {
                "type": "m.room.name",
                "content": {"name": "Test Room"}
            }
        ])
        mock_state_response.__aenter__ = AsyncMock(return_value=mock_state_response)
        mock_state_response.__aexit__ = AsyncMock(return_value=None)

        mock_session_instance = AsyncMock()
        mock_session_instance.get = Mock(side_effect=[mock_response, mock_state_response, mock_state_response])
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_session.return_value = mock_session_instance

        # Make request - /rooms/list is a GET endpoint
        response = client.get("/rooms/list?access_token=token123&homeserver=http://test:8008")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


# ============================================================================
# Webhook Endpoint Tests
# ============================================================================

@pytest.mark.unit
class TestWebhookEndpoint:
    """Test webhook endpoint for new agent notifications"""

    @patch('src.api.app.AGENT_SYNC_AVAILABLE', False)
    def test_webhook_new_agent(self, client):
        """Test webhook receives new agent notification"""
        response = client.post("/webhook/new-agent", json={
            "agent_id": "agent-123",
            "timestamp": "2025-01-01T00:00:00Z"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not available" in data["message"]

    @patch('src.api.app.AGENT_SYNC_AVAILABLE', True)
    @patch('src.api.app.run_agent_sync', new_callable=AsyncMock)
    def test_webhook_new_agent_triggers_sync(self, mock_sync, client):
        """Test webhook triggers agent sync when available"""
        response = client.post("/webhook/new-agent", json={
            "agent_id": "agent-123",
            "timestamp": "2025-01-01T00:00:00Z"
        })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "agent-123" in data["message"]

    def test_webhook_validation(self, client):
        """Test webhook validation"""
        response = client.post("/webhook/new-agent", json={})

        assert response.status_code == 422


# ============================================================================
# Error Handling Tests
# ============================================================================

@pytest.mark.unit
class TestErrorHandling:
    """Test error handling in API endpoints"""

    @patch('src.api.app.aiohttp.ClientSession')
    def test_network_error_handling(self, mock_session, client):
        """Test handling of network errors"""
        import aiohttp

        # Mock network error
        mock_session_instance = AsyncMock()
        mock_session_instance.post = Mock(side_effect=aiohttp.ClientError("Connection failed"))
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)

        mock_session.return_value = mock_session_instance

        # Make request
        response = client.post("/login", json={
            "homeserver": "http://test:8008",
            "user_id": "@test:matrix.test",
            "password": "test_pass"
        })

        # Should handle error gracefully with 200 but success=False
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "Error" in data["message"] or "error" in data["message"].lower()

    def test_invalid_json_handling(self, client):
        """Test handling of invalid JSON"""
        response = client.post(
            "/login",
            data="invalid json",
            headers={"Content-Type": "application/json"}
        )

        assert response.status_code == 422


# ============================================================================
# Agent Room Mapping Endpoint Tests
# ============================================================================

@pytest.mark.unit
class TestAgentRoomMappingEndpoints:
    """Test endpoints for exposing agent-to-room mappings"""

    def test_get_agent_room_mappings_endpoint(self, client):
        """Test endpoint for getting all agent-room mappings"""
        # This would test the endpoint if it exists
        # Documenting expected behavior

        # Expected endpoint: GET /agent_rooms
        # Expected response: List of {agent_id, agent_name, room_id}
        assert True  # Placeholder

    def test_get_agent_room_by_id_endpoint(self, client):
        """Test endpoint for getting specific agent's room"""
        # Expected endpoint: GET /agent_rooms/{agent_id}
        # Expected response: {agent_id, agent_name, room_id}
        assert True  # Placeholder


# ============================================================================
# Rate Limiting Tests
# ============================================================================

@pytest.mark.unit
class TestRateLimiting:
    """Test rate limiting (if implemented)"""

    def test_rate_limit_enforcement(self, client):
        """Test that rate limiting is enforced"""
        # This would test rate limiting if implemented
        # For now, documenting expected behavior

        # Expected: After N requests in time window, return 429
        assert True  # Placeholder

    def test_rate_limit_headers(self, client):
        """Test that rate limit headers are included"""
        # Expected headers:
        # X-RateLimit-Limit: Maximum requests
        # X-RateLimit-Remaining: Remaining requests
        # X-RateLimit-Reset: Reset timestamp
        assert True  # Placeholder
