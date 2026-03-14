"""
Unit tests for portal-links API endpoints authentication guards.

Tests cover:
- All 5 portal-links endpoints return 422 when x-internal-key header is missing
- All 5 portal-links endpoints return 403 when wrong key is provided
- All 5 portal-links endpoints succeed (200) with correct key
- Proper mocking of mapping_service functions
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from urllib.parse import quote

# Import the FastAPI app
from src.api.app import app

# Import the auth function and key
from src.api.auth import INTERNAL_API_KEY


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def client():
    """Create FastAPI test client"""
    return TestClient(app)


@pytest.fixture
def default_internal_key():
    """Get the default internal API key"""
    return "matrix-identity-internal-key"


@pytest.fixture
def correct_headers(default_internal_key):
    """Headers with correct internal key"""
    return {"x-internal-key": default_internal_key}


@pytest.fixture
def wrong_headers():
    """Headers with wrong internal key"""
    return {"x-internal-key": "wrong-key-12345"}


# ============================================================================
# Test Class: Portal Links Auth Guards
# ============================================================================

class TestPortalLinksAuth:
    """Test authentication guards on all 5 portal-links endpoints"""

    # ========================================================================
    # GET /agents/portal-links (List all portal links)
    # ========================================================================

    def test_list_all_portal_links_returns_422_when_header_missing(self, client):
        """Test GET /agents/portal-links returns 422 when x-internal-key header is missing"""
        response = client.get("/agents/portal-links")
        
        # FastAPI returns 422 for missing required headers
        assert response.status_code == 422

    def test_list_all_portal_links_returns_403_when_wrong_key(self, client, wrong_headers):
        """Test GET /agents/portal-links returns 403 when wrong key is provided"""
        response = client.get("/agents/portal-links", headers=wrong_headers)
        
        assert response.status_code == 403
        assert "Invalid internal API key" in response.json()["detail"]

    def test_list_all_portal_links_succeeds_with_correct_key(self, client, correct_headers):
        """Test GET /agents/portal-links succeeds (200) with correct key"""
        with patch("src.core.mapping_service.get_all_portal_links", return_value=[]):
            response = client.get("/agents/portal-links", headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["links"] == []
        assert data["count"] == 0

    # ========================================================================
    # GET /agents/{agent_id}/portal-links (Get portal links for agent)
    # ========================================================================

    def test_get_agent_portal_links_returns_422_when_header_missing(self, client):
        """Test GET /agents/{agent_id}/portal-links returns 422 when x-internal-key header is missing"""
        response = client.get("/agents/agent-123/portal-links")
        
        assert response.status_code == 422

    def test_get_agent_portal_links_returns_403_when_wrong_key(self, client, wrong_headers):
        """Test GET /agents/{agent_id}/portal-links returns 403 when wrong key is provided"""
        response = client.get("/agents/agent-123/portal-links", headers=wrong_headers)
        
        assert response.status_code == 403
        assert "Invalid internal API key" in response.json()["detail"]

    def test_get_agent_portal_links_succeeds_with_correct_key(self, client, correct_headers):
        """Test GET /agents/{agent_id}/portal-links succeeds (200) with correct key"""
        agent_id = "agent-123"
        
        with patch("src.core.mapping_service.get_portal_links_by_agent", return_value=[]):
            response = client.get(f"/agents/{agent_id}/portal-links", headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["agent_id"] == agent_id
        assert data["links"] == []
        assert data["count"] == 0

    # ========================================================================
    # POST /agents/{agent_id}/portal-links (Create portal link)
    # ========================================================================

    def test_create_portal_link_returns_422_when_header_missing(self, client):
        """Test POST /agents/{agent_id}/portal-links returns 422 when x-internal-key header is missing"""
        request_body = {"room_id": "!room:test"}
        
        response = client.post("/agents/agent-123/portal-links", json=request_body)
        
        assert response.status_code == 422

    def test_create_portal_link_returns_403_when_wrong_key(self, client, wrong_headers):
        """Test POST /agents/{agent_id}/portal-links returns 403 when wrong key is provided"""
        request_body = {"room_id": "!room:test"}
        
        response = client.post("/agents/agent-123/portal-links", json=request_body, headers=wrong_headers)
        
        assert response.status_code == 403
        assert "Invalid internal API key" in response.json()["detail"]

    def test_create_portal_link_succeeds_with_correct_key(self, client, correct_headers):
        """Test POST /agents/{agent_id}/portal-links succeeds (200) with correct key"""
        agent_id = "agent-1"
        room_id = "!room:test"
        request_body = {"room_id": room_id}
        
        mock_link = {"agent_id": agent_id, "room_id": room_id}
        
        with patch("src.core.mapping_service.get_mapping_by_agent_id", return_value={"agent_id": agent_id}):
            with patch("src.core.mapping_service.create_portal_link", return_value=mock_link):
                response = client.post(f"/agents/{agent_id}/portal-links", json=request_body, headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["link"]["agent_id"] == agent_id
        assert data["link"]["room_id"] == room_id

    # ========================================================================
    # DELETE /agents/{agent_id}/portal-links/{room_id} (Delete portal link)
    # ========================================================================

    def test_delete_portal_link_returns_422_when_header_missing(self, client):
        """Test DELETE /agents/{agent_id}/portal-links/{room_id} returns 422 when x-internal-key header is missing"""
        agent_id = "agent-123"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        
        response = client.delete(f"/agents/{agent_id}/portal-links/{encoded_room_id}")
        
        assert response.status_code == 422

    def test_delete_portal_link_returns_403_when_wrong_key(self, client, wrong_headers):
        """Test DELETE /agents/{agent_id}/portal-links/{room_id} returns 403 when wrong key is provided"""
        agent_id = "agent-123"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        
        response = client.delete(f"/agents/{agent_id}/portal-links/{encoded_room_id}", headers=wrong_headers)
        
        assert response.status_code == 403
        assert "Invalid internal API key" in response.json()["detail"]

    def test_delete_portal_link_succeeds_with_correct_key(self, client, correct_headers):
        """Test DELETE /agents/{agent_id}/portal-links/{room_id} succeeds (200) with correct key"""
        agent_id = "agent-123"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        
        with patch("src.core.mapping_service.delete_portal_link", return_value=True):
            response = client.delete(f"/agents/{agent_id}/portal-links/{encoded_room_id}", headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Deleted portal link" in data["message"]

    # ========================================================================
    # PATCH /agents/{agent_id}/portal-links/{room_id} (Update portal link)
    # ========================================================================

    def test_update_portal_link_returns_422_when_header_missing(self, client):
        """Test PATCH /agents/{agent_id}/portal-links/{room_id} returns 422 when x-internal-key header is missing"""
        agent_id = "agent-123"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        request_body = {"enabled": False}
        
        response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body)
        
        assert response.status_code == 422

    def test_update_portal_link_returns_403_when_wrong_key(self, client, wrong_headers):
        """Test PATCH /agents/{agent_id}/portal-links/{room_id} returns 403 when wrong key is provided"""
        agent_id = "agent-123"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        request_body = {"enabled": False}
        
        response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body, headers=wrong_headers)
        
        assert response.status_code == 403
        assert "Invalid internal API key" in response.json()["detail"]

    def test_update_portal_link_succeeds_with_correct_key(self, client, correct_headers):
        """Test PATCH /agents/{agent_id}/portal-links/{room_id} succeeds (200) with correct key"""
        agent_id = "agent-1"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        request_body = {"enabled": False}
        
        mock_link = {"agent_id": agent_id, "room_id": room_id, "enabled": False}
        
        with patch("src.core.mapping_service.update_portal_link", return_value=mock_link):
            response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body, headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["link"]["agent_id"] == agent_id
        assert data["link"]["room_id"] == room_id
        assert data["link"]["enabled"] is False

    # ========================================================================
    # Additional Tests: Edge Cases and Variations
    # ========================================================================

    def test_all_endpoints_with_empty_agent_id(self, client, correct_headers):
        """Test that endpoints handle empty agent_id gracefully (auth passes, business logic may fail)"""
        # Auth should pass, but business logic may return 404 or 500
        with patch("src.core.mapping_service.get_portal_links_by_agent", return_value=[]):
            response = client.get("/agents//portal-links", headers=correct_headers)
        
        # Should not be 403 (auth passed)
        assert response.status_code != 403

    def test_delete_with_complex_room_id(self, client, correct_headers):
        """Test DELETE with complex room ID containing special characters"""
        agent_id = "agent-123"
        room_id = "!abc123:matrix.example.com"
        encoded_room_id = quote(room_id, safe="")
        
        with patch("src.core.mapping_service.delete_portal_link", return_value=True):
            response = client.delete(f"/agents/{agent_id}/portal-links/{encoded_room_id}", headers=correct_headers)
        
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_create_with_all_optional_fields(self, client, correct_headers):
        """Test POST with all optional fields in request body"""
        agent_id = "agent-1"
        room_id = "!room:test"
        request_body = {
            "room_id": room_id,
            "enabled": True,
            "relay_mode": False,
            "mention_enabled": True
        }
        
        mock_link = {
            "agent_id": agent_id,
            "room_id": room_id,
            "enabled": True,
            "relay_mode": False,
            "mention_enabled": True
        }
        
        with patch("src.core.mapping_service.get_mapping_by_agent_id", return_value={"agent_id": agent_id}):
            with patch("src.core.mapping_service.create_portal_link", return_value=mock_link):
                response = client.post(f"/agents/{agent_id}/portal-links", json=request_body, headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_update_with_multiple_fields(self, client, correct_headers):
        """Test PATCH with multiple fields to update"""
        agent_id = "agent-1"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        request_body = {
            "enabled": False,
            "relay_mode": True,
            "mention_enabled": False
        }
        
        mock_link = {
            "agent_id": agent_id,
            "room_id": room_id,
            "enabled": False,
            "relay_mode": True,
            "mention_enabled": False
        }
        
        with patch("src.core.mapping_service.update_portal_link", return_value=mock_link):
            response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body, headers=correct_headers)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_header_case_insensitivity(self, client):
        """Test that FastAPI normalizes header names (case-insensitive)"""
        # FastAPI normalizes header names to lowercase
        headers = {"X-Internal-Key": "matrix-identity-internal-key"}
        
        with patch("src.core.mapping_service.get_all_portal_links", return_value=[]):
            response = client.get("/agents/portal-links", headers=headers)
        
        # Should succeed because FastAPI normalizes header names
        assert response.status_code == 200
        assert response.json()["success"] is True

    # ========================================================================
    # Triage Agent ID Tests
    # ========================================================================

    def test_create_with_triage_agent_id(self, client, correct_headers):
        """POST with triage_agent_id passes it through to create_portal_link"""
        agent_id = "agent-1"
        triage_id = "agent-triage-999"
        request_body = {
            "room_id": "!room:test",
            "triage_agent_id": triage_id,
        }
        mock_link = {
            "agent_id": agent_id,
            "room_id": "!room:test",
            "enabled": True,
            "relay_mode": True,
            "mention_enabled": False,
            "triage_agent_id": triage_id,
        }

        with patch("src.core.mapping_service.get_mapping_by_agent_id", return_value={"agent_id": agent_id}):
            with patch("src.core.mapping_service.create_portal_link", return_value=mock_link) as mock_create:
                response = client.post(f"/agents/{agent_id}/portal-links", json=request_body, headers=correct_headers)

        assert response.status_code == 200
        assert response.json()["link"]["triage_agent_id"] == triage_id
        mock_create.assert_called_once_with(agent_id, "!room:test", True, True, False, triage_id)

    def test_patch_set_triage_agent_id(self, client, correct_headers):
        """PATCH with triage_agent_id sets it on the portal link"""
        agent_id = "agent-1"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        triage_id = "agent-triage-999"
        request_body = {"triage_agent_id": triage_id}
        mock_link = {
            "agent_id": agent_id,
            "room_id": room_id,
            "triage_agent_id": triage_id,
        }

        with patch("src.core.mapping_service.update_portal_link", return_value=mock_link) as mock_update:
            response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body, headers=correct_headers)

        assert response.status_code == 200
        assert response.json()["link"]["triage_agent_id"] == triage_id
        mock_update.assert_called_once_with(agent_id, room_id, triage_agent_id=triage_id)

    def test_patch_clear_triage_agent_id_to_null(self, client, correct_headers):
        """PATCH with triage_agent_id=null clears it (exclude_unset=True behavior)"""
        agent_id = "agent-1"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        request_body = {"triage_agent_id": None}
        mock_link = {
            "agent_id": agent_id,
            "room_id": room_id,
            "triage_agent_id": None,
        }

        with patch("src.core.mapping_service.update_portal_link", return_value=mock_link) as mock_update:
            response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body, headers=correct_headers)

        assert response.status_code == 200
        assert response.json()["link"]["triage_agent_id"] is None
        mock_update.assert_called_once_with(agent_id, room_id, triage_agent_id=None)

    def test_patch_without_triage_does_not_send_it(self, client, correct_headers):
        """PATCH without triage_agent_id should not include it in kwargs"""
        agent_id = "agent-1"
        room_id = "!room:test"
        encoded_room_id = quote(room_id, safe="")
        request_body = {"enabled": False}
        mock_link = {"agent_id": agent_id, "room_id": room_id, "enabled": False}

        with patch("src.core.mapping_service.update_portal_link", return_value=mock_link) as mock_update:
            response = client.patch(f"/agents/{agent_id}/portal-links/{encoded_room_id}", json=request_body, headers=correct_headers)

        assert response.status_code == 200
        mock_update.assert_called_once_with(agent_id, room_id, enabled=False)
