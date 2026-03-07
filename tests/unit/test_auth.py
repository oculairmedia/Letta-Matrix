"""
Unit tests for src/api/auth.py and /rooms/auto-join endpoint

Tests cover:
- verify_internal_key function with correct/wrong/missing keys
- /rooms/auto-join endpoint authentication guard
- FastAPI Header validation
- HTTPException 403 on invalid key
"""
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from unittest.mock import Mock, patch
import os

# Import the FastAPI app
from src.api.app import app, AutoJoinRequest

# Import the auth function
from src.api.auth import verify_internal_key, INTERNAL_API_KEY


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


# ============================================================================
# Unit Tests for verify_internal_key Function
# ============================================================================

class TestVerifyInternalKey:
    """Test the verify_internal_key function directly"""

    def test_verify_internal_key_accepts_correct_key(self, default_internal_key):
        """Test that verify_internal_key accepts the correct key"""
        # Should not raise any exception
        result = verify_internal_key(default_internal_key)
        # Function returns None on success
        assert result is None

    def test_verify_internal_key_rejects_wrong_key(self):
        """Test that verify_internal_key rejects wrong key with HTTPException 403"""
        with pytest.raises(HTTPException) as exc_info:
            verify_internal_key("wrong-key")
        
        assert exc_info.value.status_code == 403
        assert "Invalid internal API key" in exc_info.value.detail

    def test_verify_internal_key_rejects_empty_key(self):
        """Test that verify_internal_key rejects empty key"""
        with pytest.raises(HTTPException) as exc_info:
            verify_internal_key("")
        
        assert exc_info.value.status_code == 403
        assert "Invalid internal API key" in exc_info.value.detail

    def test_verify_internal_key_rejects_none_key(self):
        """Test that verify_internal_key rejects None key"""
        with pytest.raises(HTTPException) as exc_info:
            verify_internal_key(None)
        
        assert exc_info.value.status_code == 403

    def test_verify_internal_key_case_sensitive(self):
        """Test that key verification is case-sensitive"""
        with pytest.raises(HTTPException) as exc_info:
            verify_internal_key("MATRIX-IDENTITY-INTERNAL-KEY")
        
        assert exc_info.value.status_code == 403


# ============================================================================
# Integration Tests for /rooms/auto-join Endpoint
# ============================================================================

class TestAutoJoinRoomsEndpoint:
    """Test the /rooms/auto-join endpoint with auth guard"""

    def test_auto_join_returns_422_when_header_missing(self, client):
        """Test that /rooms/auto-join returns 422 when x-internal-key header is missing"""
        request_data = {
            "user_id": "@test:matrix.test",
            "access_token": "test_token",
            "homeserver": "http://test:8008"
        }
        
        response = client.post("/rooms/auto-join", json=request_data)
        
        # FastAPI returns 422 for missing required headers
        assert response.status_code == 422

    def test_auto_join_returns_403_when_wrong_key_provided(self, client):
        """Test that /rooms/auto-join returns 403 when wrong key is provided"""
        request_data = {
            "user_id": "@test:matrix.test",
            "access_token": "test_token",
            "homeserver": "http://test:8008"
        }
        
        headers = {"x-internal-key": "wrong-key"}
        response = client.post("/rooms/auto-join", json=request_data, headers=headers)
        
        # Should return 403 Forbidden
        assert response.status_code == 403
        assert "Invalid internal API key" in response.json()["detail"]

    def test_auto_join_succeeds_with_correct_key(self, client):
        """Test that /rooms/auto-join succeeds with correct key"""
        request_data = {
            "user_id": "@test:matrix.test",
            "access_token": "test_token",
            "homeserver": "http://test:8008"
        }
        
        headers = {"x-internal-key": "matrix-identity-internal-key"}
        
        # Mock get_all_mappings to return empty dict
        with patch("src.core.mapping_service.get_all_mappings", return_value={}):
            response = client.post("/rooms/auto-join", json=request_data, headers=headers)
        
        # Should succeed (200 or 400 depending on business logic)
        # The important part is that it passes the auth guard (not 403)
        assert response.status_code in [200, 400]
        assert response.status_code != 403

    def test_auto_join_with_custom_internal_key_env(self, client):
        """Test that /rooms/auto-join respects custom INTERNAL_API_KEY from env"""
        custom_key = "custom-secret-key-12345"
        
        request_data = {
            "user_id": "@test:matrix.test",
            "access_token": "test_token",
            "homeserver": "http://test:8008"
        }
        
        # Patch the INTERNAL_API_KEY in the auth module
        with patch("src.api.auth.INTERNAL_API_KEY", custom_key):
            headers = {"x-internal-key": custom_key}
            
            with patch("src.core.mapping_service.get_all_mappings", return_value={}):
                response = client.post("/rooms/auto-join", json=request_data, headers=headers)
            
            # Should succeed with custom key
            assert response.status_code in [200, 400]
            assert response.status_code != 403

    def test_auto_join_request_validation(self, client):
        """Test that AutoJoinRequest validates required fields"""
        # Missing required fields
        request_data = {
            "user_id": "@test:matrix.test"
            # Missing access_token and homeserver
        }
        
        headers = {"x-internal-key": "matrix-identity-internal-key"}
        response = client.post("/rooms/auto-join", json=request_data, headers=headers)
        
        # Should return 422 for validation error (before auth is checked)
        assert response.status_code == 422

    def test_auto_join_with_empty_mappings(self, client):
        """Test /rooms/auto-join with empty mappings returns appropriate response"""
        request_data = {
            "user_id": "@test:matrix.test",
            "access_token": "test_token",
            "homeserver": "http://test:8008"
        }
        
        headers = {"x-internal-key": "matrix-identity-internal-key"}
        
        # Mock get_all_mappings to return empty dict
        with patch("src.core.mapping_service.get_all_mappings", return_value={}):
            response = client.post("/rooms/auto-join", json=request_data, headers=headers)
        
        # Should return 200 with empty joined_rooms
        assert response.status_code == 200
        data = response.json()
        assert "joined_rooms" in data
        assert data["joined_rooms"] == []

    def test_auto_join_header_case_insensitive_in_fastapi(self, client):
        """Test that FastAPI header matching is case-insensitive"""
        request_data = {
            "user_id": "@test:matrix.test",
            "access_token": "test_token",
            "homeserver": "http://test:8008"
        }
        
        # Try with different case
        headers = {"X-Internal-Key": "matrix-identity-internal-key"}
        
        with patch("src.core.mapping_service.get_all_mappings", return_value={}):
            response = client.post("/rooms/auto-join", json=request_data, headers=headers)
        
        # FastAPI normalizes header names, so this should work
        assert response.status_code in [200, 400]
        assert response.status_code != 403
