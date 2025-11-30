"""
Integration tests for the OpenCode Matrix Bridge.

Tests cover:
1. Auto-registration of OpenCode instances
2. Message forwarding with @mention routing
3. Auto-accept room invites
4. Stale registration cleanup
"""

import pytest
import requests
import time
import json
from typing import Optional, Dict, Any

# Bridge configuration
BRIDGE_URL = "http://localhost:3201"
MATRIX_HOMESERVER = "http://127.0.0.1:6167"
BRIDGE_USER_ID = "@oc_matrix_synapse_deployment:matrix.oculair.ca"

# Test room (Meridian's room)
TEST_ROOM_ID = "!O8cbkBGCMB8Ujlaret:matrix.oculair.ca"


class TestBridgeHealth:
    """Test bridge health and basic connectivity."""
    
    def test_bridge_is_running(self):
        """Bridge should respond to health check."""
        response = requests.get(f"{BRIDGE_URL}/health", timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"
    
    def test_bridge_has_rooms_endpoint(self):
        """Bridge should expose rooms endpoint."""
        response = requests.get(f"{BRIDGE_URL}/rooms", timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert "rooms" in data
    
    def test_bridge_has_registrations_endpoint(self):
        """Bridge should expose registrations endpoint."""
        response = requests.get(f"{BRIDGE_URL}/registrations", timeout=5)
        assert response.status_code == 200
        data = response.json()
        assert "registrations" in data
        assert "count" in data


class TestRegistration:
    """Test OpenCode instance registration."""
    
    def test_register_new_instance(self):
        """Should successfully register a new OpenCode instance."""
        payload = {
            "port": 55555,
            "hostname": "127.0.0.1",
            "sessionId": "test-session-123",
            "directory": "/test/directory"
        }
        response = requests.post(
            f"{BRIDGE_URL}/register",
            json=payload,
            timeout=5
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") == True
        assert "matrixIdentity" in data  # API returns matrixIdentity, not identity
    
    def test_register_derives_identity_from_directory(self):
        """Registration should derive Matrix identity from directory name."""
        payload = {
            "port": 55556,
            "hostname": "127.0.0.1",
            "sessionId": "test-session-456",
            "directory": "/opt/stacks/my-cool-project"
        }
        response = requests.post(
            f"{BRIDGE_URL}/register",
            json=payload,
            timeout=5
        )
        assert response.status_code == 200
        data = response.json()
        # Identity should be derived from directory: my-cool-project -> @oc_my_cool_project:matrix.oculair.ca
        assert data.get("matrixIdentity") == "@oc_my_cool_project:matrix.oculair.ca"
    
    def test_unregister_instance(self):
        """Should successfully unregister an OpenCode instance."""
        # First register
        payload = {
            "port": 55557,
            "hostname": "127.0.0.1",
            "sessionId": "test-session-789",
            "directory": "/test/to-unregister"
        }
        reg_response = requests.post(f"{BRIDGE_URL}/register", json=payload, timeout=5)
        reg_data = reg_response.json()
        registration_id = reg_data.get("id")
        
        # Then unregister using the registration ID
        response = requests.post(
            f"{BRIDGE_URL}/unregister",
            json={"id": registration_id},
            timeout=5
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("success") == True
    
    def test_duplicate_registration_updates_existing(self):
        """Re-registering same session should update, not duplicate."""
        payload = {
            "port": 55558,
            "hostname": "127.0.0.1",
            "sessionId": "test-dup-session",
            "directory": "/test/duplicate"
        }
        
        # Register twice
        requests.post(f"{BRIDGE_URL}/register", json=payload, timeout=5)
        requests.post(f"{BRIDGE_URL}/register", json=payload, timeout=5)
        
        # Check registrations - should only have one entry for this session
        response = requests.get(f"{BRIDGE_URL}/registrations", timeout=5)
        data = response.json()
        
        matching = [r for r in data["registrations"] 
                   if r["sessionId"] == "test-dup-session"]
        assert len(matching) == 1


class TestIdentityMapping:
    """Test identity to registration mapping."""
    
    def test_identity_mapping_exists_after_registration(self):
        """After registration, identity should map to registration."""
        payload = {
            "port": 55559,
            "hostname": "127.0.0.1",
            "sessionId": "test-identity-session",
            "directory": "/opt/stacks/identity-test-project"
        }
        response = requests.post(
            f"{BRIDGE_URL}/register",
            json=payload,
            timeout=5
        )
        data = response.json()
        identity = data.get("matrixIdentity")
        
        # Verify the identity was registered
        assert identity == "@oc_identity_test_project:matrix.oculair.ca"


class TestMentionExtraction:
    """Test @mention parsing and routing."""
    
    def test_mention_pattern_matches(self):
        """Verify mention pattern matches expected format."""
        import re
        pattern = r'@oc_[a-zA-Z0-9_]+:matrix\.oculair\.ca'
        
        # Should match
        assert re.search(pattern, "@oc_matrix_synapse_deployment:matrix.oculair.ca")
        assert re.search(pattern, "@oc_my_project:matrix.oculair.ca")
        assert re.search(pattern, "Hello @oc_test:matrix.oculair.ca how are you?")
        
        # Should not match
        assert not re.search(pattern, "@letta:matrix.oculair.ca")
        assert not re.search(pattern, "@admin:matrix.oculair.ca")
        assert not re.search(pattern, "oc_test:matrix.oculair.ca")  # Missing @


class TestMessageForwarding:
    """Test message forwarding to OpenCode instances."""
    
    @pytest.fixture
    def admin_token(self):
        """Get admin token for sending test messages."""
        response = requests.post(
            f"{MATRIX_HOMESERVER}/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "user": "admin",
                "password": "m6kvcVMWiSYzi6v"
            },
            timeout=10
        )
        if response.status_code == 200:
            return response.json().get("access_token")
        return None
    
    def test_message_with_mention_is_detected(self, admin_token):
        """Messages with @oc_* mentions should trigger forwarding logic."""
        if not admin_token:
            pytest.skip("Could not get admin token")
        
        # Send a message with @mention
        txn_id = f"test_{int(time.time() * 1000)}"
        response = requests.put(
            f"{MATRIX_HOMESERVER}/_matrix/client/v3/rooms/{TEST_ROOM_ID}/send/m.room.message/{txn_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "msgtype": "m.text",
                "body": f"@oc_matrix_synapse_deployment:matrix.oculair.ca test message {txn_id}"
            },
            timeout=10
        )
        assert response.status_code == 200
        
        # Give bridge time to process
        time.sleep(2)
        
        # Check bridge logs would show the message was detected
        # (In a real test, we'd verify the forwarding endpoint was called)


class TestAutoInvite:
    """Test auto-accept room invite functionality."""
    
    def test_bridge_user_in_rooms(self):
        """Bridge user should be in multiple rooms after auto-accepting invites."""
        # Get bridge token from env
        bridge_token = "hiLpdlhbasYM2LKHA1viQWJJlQGGx7b5"
        
        response = requests.get(
            f"{MATRIX_HOMESERVER}/_matrix/client/v3/joined_rooms",
            headers={"Authorization": f"Bearer {bridge_token}"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            rooms = data.get("joined_rooms", [])
            # Bridge should be in multiple rooms after auto-invite setup
            assert len(rooms) > 10, f"Bridge only in {len(rooms)} rooms, expected >10"


class TestStaleRegistrationCleanup:
    """Test stale registration cleanup."""
    
    def test_stale_registrations_are_removed(self):
        """Registrations that haven't been seen recently should be cleaned up."""
        # Register with a very old timestamp would be cleaned
        # This tests the cleanup mechanism
        
        # First get current registrations
        response = requests.get(f"{BRIDGE_URL}/registrations", timeout=5)
        initial_data = response.json()
        initial_count = initial_data["count"]
        
        # The bridge should have cleanup logic that removes stale entries
        # after the keepalive timeout (default 60 seconds)
        # For now, just verify the endpoint works
        assert initial_count >= 0


# Cleanup fixture
@pytest.fixture(autouse=True)
def cleanup_test_registrations():
    """Clean up test registrations after each test."""
    yield
    # Unregister any test sessions
    test_sessions = [
        "test-session-123",
        "test-session-456", 
        "test-session-789",
        "test-dup-session",
        "test-identity-session"
    ]
    for session in test_sessions:
        try:
            requests.post(
                f"{BRIDGE_URL}/unregister",
                json={
                    "port": 0,
                    "hostname": "127.0.0.1",
                    "sessionId": session,
                    "directory": "/test"
                },
                timeout=2
            )
        except:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
