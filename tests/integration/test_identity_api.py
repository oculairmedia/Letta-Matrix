import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, Mock
from types import SimpleNamespace
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_test_engine = None
_test_session_maker = None


@pytest.fixture(scope="module")
def identity_sqlite_engine():
    global _test_engine, _test_session_maker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from src.models.agent_mapping import Base
    from src.models.identity import Identity, DMRoom

    _test_engine = create_engine(
        'sqlite:///:memory:',
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    _test_session_maker = sessionmaker(bind=_test_engine)
    Base.metadata.create_all(_test_engine)
    
    yield _test_engine
    
    Base.metadata.drop_all(_test_engine)
    _test_engine.dispose()
    _test_engine = None
    _test_session_maker = None


@pytest.fixture(autouse=True)
def patch_database(identity_sqlite_engine, monkeypatch):
    import src.models.agent_mapping as agent_mapping_module
    import src.core.identity_storage as identity_storage_module
    from src.core.identity_storage import IdentityStorageService, DMRoomStorageService
    from src.models.identity import Identity, DMRoom
    
    monkeypatch.setattr(agent_mapping_module, 'get_engine', lambda: _test_engine)
    monkeypatch.setattr(agent_mapping_module, 'get_session_maker', lambda: _test_session_maker)
    
    IdentityStorageService._instance = None
    IdentityStorageService._initialized = False
    DMRoomStorageService._instance = None
    DMRoomStorageService._initialized = False
    identity_storage_module._identity_service = None
    identity_storage_module._dm_room_service = None
    
    yield
    
    if _test_session_maker:
        session = _test_session_maker()
        try:
            session.query(Identity).delete()
            session.query(DMRoom).delete()
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
    
    IdentityStorageService._instance = None
    IdentityStorageService._initialized = False
    DMRoomStorageService._instance = None
    DMRoomStorageService._initialized = False
    identity_storage_module._identity_service = None
    identity_storage_module._dm_room_service = None


@pytest.fixture
def identity_db():
    from src.core.identity_storage import get_identity_service, get_dm_room_service
    return {
        'identity_service': get_identity_service(),
        'dm_room_service': get_dm_room_service()
    }


@pytest.fixture
def client(identity_db):
    from src.api.app import app
    return TestClient(app)


@pytest.fixture
def sample_identity():
    return {
        "id": "test_identity_001",
        "identity_type": "custom",
        "mxid": "@testuser:matrix.test",
        "access_token": "test_access_token_12345",
        "display_name": "Test User",
        "avatar_url": None,
        "password_hash": None,
        "device_id": "TESTDEVICE"
    }


@pytest.fixture
def sample_letta_identity():
    return {
        "id": "letta_agent-12345",
        "identity_type": "letta",
        "mxid": "@agent_12345:matrix.test",
        "access_token": "agent_access_token",
        "display_name": "Agent 12345",
        "avatar_url": None,
        "password_hash": None,
        "device_id": None
    }


class TestIdentityEndpoints:
    
    def test_create_identity(self, client, sample_identity):
        response = client.post("/api/v1/identities", json=sample_identity)
        
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == sample_identity["id"]
        assert data["mxid"] == sample_identity["mxid"]
        assert data["identity_type"] == sample_identity["identity_type"]
        assert data["is_active"] is True
    
    def test_create_identity_duplicate_id(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        response = client.post("/api/v1/identities", json=sample_identity)
        
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]
    
    def test_create_identity_duplicate_mxid(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        sample_identity["id"] = "different_id"
        response = client.post("/api/v1/identities", json=sample_identity)
        
        assert response.status_code == 409
        assert "already registered" in response.json()["detail"]
    
    def test_list_identities_empty(self, client):
        response = client.get("/api/v1/identities")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 0
        assert data["identities"] == []
    
    def test_list_identities(self, client, sample_identity, sample_letta_identity):
        client.post("/api/v1/identities", json=sample_identity)
        client.post("/api/v1/identities", json=sample_letta_identity)
        
        response = client.get("/api/v1/identities")
        
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["identities"]) == 2
    
    def test_list_identities_by_type(self, client, sample_identity, sample_letta_identity):
        client.post("/api/v1/identities", json=sample_identity)
        client.post("/api/v1/identities", json=sample_letta_identity)
        
        response = client.get("/api/v1/identities", params={"identity_type": "letta"})
        
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["identities"][0]["identity_type"] == "letta"
    
    def test_get_identity(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        response = client.get(f"/api/v1/identities/{sample_identity['id']}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_identity["id"]
        assert data["mxid"] == sample_identity["mxid"]
    
    def test_get_identity_not_found(self, client):
        response = client.get("/api/v1/identities/nonexistent")
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
    
    def test_get_identity_by_mxid(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        encoded_mxid = sample_identity["mxid"].replace("@", "%40").replace(":", "%3A")
        response = client.get(f"/api/v1/identities/by-mxid/{encoded_mxid}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["mxid"] == sample_identity["mxid"]
    
    def test_get_identity_by_agent(self, client, sample_letta_identity):
        client.post("/api/v1/identities", json=sample_letta_identity)
        
        response = client.get("/api/v1/identities/by-agent/agent-12345")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "letta_agent-12345"
        assert data["identity_type"] == "letta"
    
    def test_update_identity(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        update_data = {"display_name": "Updated Name"}
        with patch('src.api.routes.identity._sync_identity_profile', new_callable=AsyncMock):
            response = client.put(f"/api/v1/identities/{sample_identity['id']}", json=update_data)
        
        assert response.status_code == 200
        data = response.json()
        assert data["display_name"] == "Updated Name"
    
    def test_update_identity_not_found(self, client):
        response = client.put("/api/v1/identities/nonexistent", json={"display_name": "Test"})
        
        assert response.status_code == 404
    
    def test_update_identity_empty_request(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        response = client.put(f"/api/v1/identities/{sample_identity['id']}", json={})
        
        assert response.status_code == 400
        assert "No fields to update" in response.json()["detail"]
    
    def test_delete_identity_soft(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool.return_value.close_client = AsyncMock()
            response = client.delete(f"/api/v1/identities/{sample_identity['id']}")
        
        assert response.status_code == 204
        
        list_response = client.get("/api/v1/identities", params={"active_only": True})
        assert list_response.json()["count"] == 0
        
        list_all_response = client.get("/api/v1/identities", params={"active_only": False})
        assert list_all_response.json()["count"] == 1
        assert list_all_response.json()["identities"][0]["is_active"] is False
    
    def test_delete_identity_hard(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool.return_value.close_client = AsyncMock()
            response = client.delete(f"/api/v1/identities/{sample_identity['id']}", params={"hard_delete": True})
        
        assert response.status_code == 204
        
        list_response = client.get("/api/v1/identities", params={"active_only": False})
        assert list_response.json()["count"] == 0
    
    def test_delete_identity_not_found(self, client):
        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool.return_value.close_client = AsyncMock()
            response = client.delete("/api/v1/identities/nonexistent")
        
        assert response.status_code == 404


class TestSendAsIdentityEndpoints:
    
    def test_send_as_identity_not_found(self, client):
        request = {
            "identity_id": "nonexistent",
            "room_id": "!room:matrix.test",
            "message": "Hello"
        }
        response = client.post("/api/v1/messages/send-as-identity", json=request)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"]
    
    def test_send_as_identity_inactive(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool.return_value.close_client = AsyncMock()
            client.delete(f"/api/v1/identities/{sample_identity['id']}")
        
        request = {
            "identity_id": sample_identity["id"],
            "room_id": "!room:matrix.test",
            "message": "Hello"
        }
        response = client.post("/api/v1/messages/send-as-identity", json=request)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "inactive" in data["error"]
    
    def test_send_as_identity_success(self, client, sample_identity):
        client.post("/api/v1/identities", json=sample_identity)
        
        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool_instance = AsyncMock()
            mock_pool_instance.send_message = AsyncMock(return_value="$event123")
            mock_pool.return_value = mock_pool_instance
            
            request = {
                "identity_id": sample_identity["id"],
                "room_id": "!room:matrix.test",
                "message": "Hello"
            }
            response = client.post("/api/v1/messages/send-as-identity", json=request)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["event_id"] == "$event123"
    
    def test_send_as_agent_success(self, client, sample_letta_identity):
        client.post("/api/v1/identities", json=sample_letta_identity)
        
        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool_instance = AsyncMock()
            mock_pool_instance.send_as_agent = AsyncMock(return_value="$event456")
            mock_pool.return_value = mock_pool_instance
            
            request = {
                "agent_id": "agent-12345",
                "room_id": "!room:matrix.test",
                "message": "Agent message"
            }
            response = client.post("/api/v1/messages/send-as-agent", json=request)
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["event_id"] == "$event456"

    def test_edit_as_agent_success(self, client, sample_letta_identity):
        client.post("/api/v1/identities", json=sample_letta_identity)

        with patch('src.api.routes.identity.get_identity_client_pool') as mock_pool:
            mock_pool_instance = AsyncMock()
            mock_pool_instance.edit_as_agent = AsyncMock(return_value="$event789")
            mock_pool.return_value = mock_pool_instance

            request = {
                "agent_id": "agent-12345",
                "room_id": "!room:matrix.test",
                "event_id": "$original",
                "message": "Updated status",
                "msgtype": "m.notice",
            }
            response = client.post("/api/v1/messages/edit-as-agent", json=request)

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["event_id"] == "$event789"


class TestDMRoomEndpoints:
    
    def test_create_dm_room(self, client, identity_db):
        request = {
            "room_id": "!dm123:matrix.test",
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        }
        response = client.post("/api/v1/dm-rooms", json=request)
        
        assert response.status_code == 201
        data = response.json()
        assert data["room_id"] == "!dm123:matrix.test"
        assert "@user1:matrix.test" in [data["participant_1"], data["participant_2"]]
        assert "@user2:matrix.test" in [data["participant_1"], data["participant_2"]]
    
    def test_get_or_create_dm_room_idempotent(self, client, identity_db):
        request = {
            "room_id": "!dm123:matrix.test",
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        }
        
        response1 = client.post("/api/v1/dm-rooms", json=request)
        response2 = client.post("/api/v1/dm-rooms", json=request)
        
        assert response1.json()["room_id"] == response2.json()["room_id"]
    
    def test_list_dm_rooms_empty(self, client, identity_db):
        response = client.get("/api/v1/dm-rooms")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 0
    
    def test_list_dm_rooms(self, client, identity_db):
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm1:matrix.test",
            "mxid1": "@a:matrix.test",
            "mxid2": "@b:matrix.test"
        })
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm2:matrix.test",
            "mxid1": "@c:matrix.test",
            "mxid2": "@d:matrix.test"
        })
        
        response = client.get("/api/v1/dm-rooms")
        
        assert response.status_code == 200
        assert response.json()["count"] == 2
    
    def test_list_dm_rooms_for_user(self, client, identity_db):
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm1:matrix.test",
            "mxid1": "@a:matrix.test",
            "mxid2": "@b:matrix.test"
        })
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm2:matrix.test",
            "mxid1": "@a:matrix.test",
            "mxid2": "@c:matrix.test"
        })
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm3:matrix.test",
            "mxid1": "@x:matrix.test",
            "mxid2": "@y:matrix.test"
        })
        
        response = client.get("/api/v1/dm-rooms", params={"user_mxid": "@a:matrix.test"})
        
        assert response.status_code == 200
        assert response.json()["count"] == 2
    
    def test_lookup_dm_room(self, client, identity_db):
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm123:matrix.test",
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        })
        
        response = client.get("/api/v1/dm-rooms/lookup", params={
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        })
        
        assert response.status_code == 200
        assert response.json()["room_id"] == "!dm123:matrix.test"
    
    def test_lookup_dm_room_order_independent(self, client, identity_db):
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm123:matrix.test",
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        })
        
        response = client.get("/api/v1/dm-rooms/lookup", params={
            "mxid1": "@user2:matrix.test",
            "mxid2": "@user1:matrix.test"
        })
        
        assert response.status_code == 200
        assert response.json()["room_id"] == "!dm123:matrix.test"
    
    def test_lookup_dm_room_not_found(self, client, identity_db):
        response = client.get("/api/v1/dm-rooms/lookup", params={
            "mxid1": "@nobody:matrix.test",
            "mxid2": "@another:matrix.test"
        })
        
        assert response.status_code == 404
    
    def test_get_dm_room_by_id(self, client, identity_db):
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm123:matrix.test",
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        })
        
        response = client.get("/api/v1/dm-rooms/by-room-id/!dm123:matrix.test")
        
        assert response.status_code == 200
        assert response.json()["room_id"] == "!dm123:matrix.test"
    
    def test_delete_dm_room(self, client, identity_db):
        client.post("/api/v1/dm-rooms", json={
            "room_id": "!dm123:matrix.test",
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        })
        
        response = client.delete("/api/v1/dm-rooms", params={
            "mxid1": "@user1:matrix.test",
            "mxid2": "@user2:matrix.test"
        })
        
        assert response.status_code == 204
        
        list_response = client.get("/api/v1/dm-rooms")
        assert list_response.json()["count"] == 0
    
    def test_delete_dm_room_not_found(self, client, identity_db):
        response = client.delete("/api/v1/dm-rooms", params={
            "mxid1": "@nobody:matrix.test",
            "mxid2": "@another:matrix.test"
        })
        
        assert response.status_code == 404


class TestIdentitySyncNamesEndpoint:

    def test_sync_names_dry_run_reports_diff(self, client, sample_letta_identity):
        sample_letta_identity["id"] = "letta_agent-1"
        sample_letta_identity["display_name"] = "Old Name"
        sample_letta_identity["mxid"] = "@agent_1:matrix.test"
        client.post("/api/v1/identities", json=sample_letta_identity)

        with (
            patch("src.api.routes.identity.LettaService") as mock_letta_service,
            patch("src.api.routes.identity._get_matrix_display_name", new_callable=AsyncMock, return_value="Old Name"),
            patch("src.api.routes.identity._sync_identity_profile", new_callable=AsyncMock) as mock_sync_profile,
            patch("src.models.agent_mapping.AgentMappingDB") as mock_mapping_db,
        ):
            mock_letta_service.return_value.list_agents.return_value = [
                SimpleNamespace(id="agent-1", name="Huly - Meridian")
            ]
            mapping_obj = Mock()
            mapping_obj.agent_name = "Old Name"
            mock_mapping_db.return_value.get_by_agent_id.return_value = mapping_obj

            response = client.post("/api/v1/identities/sync-names", json={"dry_run": True})

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert data["checked"] == 1
        assert data["missing_identity"] == 0
        assert data["mismatched"] == 1
        assert data["updated_identity"] == 0
        assert data["updated_matrix"] == 0
        assert data["updated_mapping"] == 0
        assert data["failed"] == 0
        assert len(data["changes"]) == 1
        assert data["changes"][0]["desired_name"] == "Meridian"
        assert data["changes"][0]["needs_identity_update"] is True
        assert data["changes"][0]["needs_matrix_update"] is True
        assert data["changes"][0]["needs_mapping_update"] is True
        mock_sync_profile.assert_not_called()

    def test_sync_names_apply_updates_all_layers(self, client, sample_letta_identity):
        sample_letta_identity["id"] = "letta_agent-2"
        sample_letta_identity["display_name"] = "Legacy Name"
        sample_letta_identity["mxid"] = "@agent_2:matrix.test"
        client.post("/api/v1/identities", json=sample_letta_identity)

        with (
            patch("src.api.routes.identity.LettaService") as mock_letta_service,
            patch("src.api.routes.identity._get_matrix_display_name", new_callable=AsyncMock, return_value="Legacy Name"),
            patch("src.api.routes.identity._sync_identity_profile", new_callable=AsyncMock) as mock_sync_profile,
            patch("src.models.agent_mapping.AgentMappingDB") as mock_mapping_db,
        ):
            mock_letta_service.return_value.list_agents.return_value = [
                SimpleNamespace(id="agent-2", name="Huly - Nova")
            ]
            mapping_obj = Mock()
            mapping_obj.agent_name = "Legacy Name"
            mock_mapping_db.return_value.get_by_agent_id.return_value = mapping_obj
            mock_mapping_db.return_value.update.return_value = mapping_obj

            response = client.post("/api/v1/identities/sync-names", json={"dry_run": False})

        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert data["mismatched"] == 1
        assert data["updated_identity"] == 1
        assert data["updated_matrix"] == 1
        assert data["updated_mapping"] == 1
        assert data["failed"] == 0
        assert data["changes"][0]["applied_identity_update"] is True
        assert data["changes"][0]["applied_matrix_update"] is True
        assert data["changes"][0]["applied_mapping_update"] is True
        mock_sync_profile.assert_called_once_with("letta_agent-2", "Nova")

        identity_response = client.get("/api/v1/identities/letta_agent-2")
        assert identity_response.status_code == 200
        assert identity_response.json()["display_name"] == "Nova"

    def test_sync_names_counts_missing_identity(self, client):
        with (
            patch("src.api.routes.identity.LettaService") as mock_letta_service,
            patch("src.api.routes.identity._get_matrix_display_name", new_callable=AsyncMock, return_value=None),
            patch("src.models.agent_mapping.AgentMappingDB") as mock_mapping_db,
        ):
            mock_letta_service.return_value.list_agents.return_value = [
                SimpleNamespace(id="agent-missing", name="Huly - Missing")
            ]
            mock_mapping_db.return_value.get_by_agent_id.return_value = None

            response = client.post("/api/v1/identities/sync-names", json={"dry_run": True})

        assert response.status_code == 200
        data = response.json()
        assert data["checked"] == 1
        assert data["missing_identity"] == 1
        assert data["mismatched"] == 0
        assert data["changes"] == []


class TestIdentityProvisionHardening:

    def test_provision_uses_create_then_login_retry_path(self, client):
        request_payload = {
            "identity_type": "opencode",
            "directory": "/opt/stacks/sample-project",
            "display_name": "OpenCode: Sample Project",
        }

        mock_manager = Mock()
        mock_manager.check_user_exists = AsyncMock(return_value="not_found")
        mock_manager.create_matrix_user = AsyncMock(return_value=True)

        with (
            patch("src.core.user_manager.MatrixUserManager", return_value=mock_manager),
            patch("src.api.routes.identity._provision_login", new=AsyncMock(side_effect=[None, "token_created"])),
            patch("src.api.routes.identity._reset_password_and_verify_login", new=AsyncMock(return_value=None)) as reset_login,
        ):
            response = client.post(
                "/api/v1/internal/identities/provision",
                json=request_payload,
                headers={"x-internal-key": "matrix-identity-internal-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["access_token"] == "token_created"
        mock_manager.create_matrix_user.assert_awaited_once()
        reset_login.assert_not_awaited()

    def test_provision_existing_user_auth_failed_triggers_reset_login(self, client):
        request_payload = {
            "identity_type": "opencode",
            "directory": "/opt/stacks/existing-project",
            "display_name": "OpenCode: Existing Project",
        }

        mock_manager = Mock()
        mock_manager.check_user_exists = AsyncMock(return_value="exists_auth_failed")
        mock_manager.create_matrix_user = AsyncMock(return_value=False)

        with (
            patch("src.core.user_manager.MatrixUserManager", return_value=mock_manager),
            patch("src.api.routes.identity._provision_login", new=AsyncMock(side_effect=[None, None])),
            patch(
                "src.api.routes.identity._reset_password_and_verify_login",
                new=AsyncMock(return_value="token_after_reset"),
            ) as reset_login,
        ):
            response = client.post(
                "/api/v1/internal/identities/provision",
                json=request_payload,
                headers={"x-internal-key": "matrix-identity-internal-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["access_token"] == "token_after_reset"
        mock_manager.create_matrix_user.assert_not_awaited()
        reset_login.assert_awaited_once()

    def test_provision_fails_after_all_retries(self, client):
        request_payload = {
            "identity_type": "opencode",
            "directory": "/opt/stacks/failing-project",
            "display_name": "OpenCode: Failing Project",
        }

        mock_manager = Mock()
        mock_manager.check_user_exists = AsyncMock(return_value="exists_auth_failed")
        mock_manager.create_matrix_user = AsyncMock(return_value=False)

        with (
            patch("src.core.user_manager.MatrixUserManager", return_value=mock_manager),
            patch("src.api.routes.identity._provision_login", new=AsyncMock(return_value=None)),
            patch("src.api.routes.identity._reset_password_and_verify_login", new=AsyncMock(return_value=None)),
        ):
            response = client.post(
                "/api/v1/internal/identities/provision",
                json=request_payload,
                headers={"x-internal-key": "matrix-identity-internal-key"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "after retries" in data["error"]


class TestIdentityHealthEndpoint:

    def test_identity_health_reports_healthy_record(self, client, sample_letta_identity):
        sample_letta_identity["id"] = "letta_agent-1"
        sample_letta_identity["identity_type"] = "letta"
        sample_letta_identity["mxid"] = "@agent_1:matrix.test"
        sample_letta_identity["display_name"] = "Meridian"
        sample_letta_identity["password_hash"] = "pw-1"
        client.post("/api/v1/identities", json=sample_letta_identity)

        mock_monitor = Mock()
        mock_monitor._validate_identity_token = AsyncMock(return_value=True)

        with (
            patch("src.api.routes.identity.get_identity_token_health_monitor", return_value=mock_monitor),
            patch("src.api.routes.identity.get_all_mappings", return_value={
                "agent-1": {
                    "agent_name": "Meridian",
                    "matrix_password": "pw-1",
                    "matrix_user_id": "@agent_1:matrix.test",
                    "room_id": "!room:matrix.test",
                }
            }),
            patch("src.api.routes.identity.LettaService") as mock_letta_service,
            patch("src.api.routes.identity._get_matrix_display_name", new=AsyncMock(return_value="Meridian")),
        ):
            mock_letta_service.return_value.list_agents.return_value = [
                SimpleNamespace(id="agent-1", name="Meridian")
            ]
            response = client.get("/api/v1/identities/health")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["checked"] == 1
        assert data["healthy"] == 1
        assert data["degraded"] == 0
        assert data["critical"] == 0
        assert data["coverage_percentage"] == 100.0
        assert data["stale_token_count"] == 0
        assert data["name_mismatch_count"] == 0
        assert isinstance(data["last_reconciliation_at"], int)
        assert data["actionable_agents"] == []
        assert data["coverage"]["missing_letta_identities"] == []
        record = data["records"][0]
        assert record["identity_id"] == "letta_agent-1"
        assert record["token_valid"] is True
        assert record["identity_matrix_name_match"] is True
        assert record["identity_letta_name_match"] is True
        assert record["identity_mapping_name_match"] is True
        assert record["password_consistent"] is True
        assert record["dm_rooms_valid"] is True

    def test_identity_health_detects_mismatches_and_invalid_dm(self, client, sample_letta_identity, sample_identity):
        sample_letta_identity["id"] = "letta_agent-2"
        sample_letta_identity["identity_type"] = "letta"
        sample_letta_identity["mxid"] = "@agent_2:matrix.test"
        sample_letta_identity["display_name"] = "Identity Name"
        sample_letta_identity["password_hash"] = "pw-identity"
        client.post("/api/v1/identities", json=sample_letta_identity)

        sample_identity["id"] = "custom_2"
        sample_identity["mxid"] = "@custom_2:matrix.test"
        client.post("/api/v1/identities", json=sample_identity)

        mock_monitor = Mock()
        mock_monitor._validate_identity_token = AsyncMock(return_value=False)
        mock_dm_service = Mock()
        mock_dm_service.get_all.return_value = [
            SimpleNamespace(
                room_id="!dm:matrix.test",
                participant_1="@agent_2:matrix.test",
                participant_2="@missing:matrix.test",
            )
        ]

        with (
            patch("src.api.routes.identity.get_identity_token_health_monitor", return_value=mock_monitor),
            patch("src.api.routes.identity.get_dm_room_service", return_value=mock_dm_service),
            patch("src.api.routes.identity.get_all_mappings", return_value={
                "agent-2": {
                    "agent_name": "Mapping Name",
                    "matrix_password": "pw-mapping",
                    "matrix_user_id": "@agent_2:matrix.test",
                    "room_id": "!room:matrix.test",
                }
            }),
            patch("src.api.routes.identity.LettaService") as mock_letta_service,
            patch("src.api.routes.identity._get_matrix_display_name", new=AsyncMock(return_value="Matrix Name")),
        ):
            mock_letta_service.return_value.list_agents.return_value = [
                SimpleNamespace(id="agent-2", name="Letta Name")
            ]
            response = client.get("/api/v1/identities/health")

        assert response.status_code == 200
        data = response.json()
        assert data["token_invalid"] >= 1
        assert data["stale_token_count"] >= 1
        assert data["name_mismatches"] >= 3
        assert data["name_mismatch_count"] >= 3
        assert data["password_mismatches"] >= 1
        assert data["invalid_dm_rooms"] >= 1
        assert len(data["actionable_agents"]) >= 1
        assert any(a["identity_id"] == "letta_agent-2" for a in data["actionable_agents"])

        letta_record = next(r for r in data["records"] if r["identity_id"] == "letta_agent-2")
        assert letta_record["token_valid"] is False
        assert letta_record["identity_matrix_name_match"] is False
        assert letta_record["identity_letta_name_match"] is False
        assert letta_record["identity_mapping_name_match"] is False
        assert letta_record["password_consistent"] is False
        assert letta_record["dm_rooms_valid"] is False

    def test_identity_health_reports_missing_letta_identity_coverage(self, client):
        mock_monitor = Mock()
        mock_monitor._validate_identity_token = AsyncMock(return_value=True)

        with (
            patch("src.api.routes.identity.get_identity_token_health_monitor", return_value=mock_monitor),
            patch("src.api.routes.identity.get_all_mappings", return_value={}),
            patch("src.api.routes.identity.LettaService") as mock_letta_service,
            patch("src.api.routes.identity._get_matrix_display_name", new=AsyncMock(return_value=None)),
        ):
            mock_letta_service.return_value.list_agents.return_value = [
                SimpleNamespace(id="agent-3", name="Orphan")
            ]
            response = client.get("/api/v1/identities/health")

        assert response.status_code == 200
        data = response.json()
        assert data["coverage"]["letta_agents_total"] == 1
        assert data["coverage"]["letta_identities_total"] == 0
        assert data["coverage"]["missing_letta_identities"] == ["letta_agent-3"]
        assert data["coverage_percentage"] == 100.0


class TestDMRoomNameReconciliation:

    def test_dm_reconcile_dry_run_reports_profile_and_room_name_mismatch(self, client, sample_letta_identity, sample_identity):
        sample_letta_identity["id"] = "letta_agent-11"
        sample_letta_identity["identity_type"] = "letta"
        sample_letta_identity["mxid"] = "@agent_11:matrix.test"
        sample_letta_identity["display_name"] = "Agent Eleven"
        sample_letta_identity["access_token"] = "token-agent"
        client.post("/api/v1/identities", json=sample_letta_identity)

        sample_identity["id"] = "custom_user_11"
        sample_identity["mxid"] = "@user_11:matrix.test"
        sample_identity["display_name"] = "User Eleven"
        sample_identity["access_token"] = "token-user"
        client.post("/api/v1/identities", json=sample_identity)

        client.post(
            "/api/v1/dm-rooms",
            json={
                "room_id": "!dm11:matrix.test",
                "mxid1": "@agent_11:matrix.test",
                "mxid2": "@user_11:matrix.test",
            },
        )

        with (
            patch("src.api.routes.identity._get_room_name", new=AsyncMock(return_value="Wrong Name")),
            patch("src.api.routes.identity._get_matrix_display_name", new=AsyncMock(return_value="Not Synced")),
            patch("src.api.routes.identity._sync_identity_profile", new=AsyncMock()) as sync_profile,
        ):
            response = client.post("/api/v1/dm-rooms/reconcile-names", json={"dry_run": True})

        assert response.status_code == 200
        data = response.json()
        assert data["checked"] == 1
        assert data["agent_dm_rooms"] == 1
        assert data["mismatched_rooms"] == 1
        assert data["profile_mismatches"] == 2
        assert data["profiles_synced"] == 0
        assert data["failed"] == 0
        assert len(data["changes"]) == 1
        change = data["changes"][0]
        assert change["room_id"] == "!dm11:matrix.test"
        assert change["room_name_mismatch"] is True
        assert set(change["profile_mismatches"]) == {"letta_agent-11", "custom_user_11"}
        sync_profile.assert_not_awaited()

    def test_dm_reconcile_apply_syncs_profile_mismatches(self, client, sample_letta_identity, sample_identity):
        sample_letta_identity["id"] = "letta_agent-12"
        sample_letta_identity["identity_type"] = "letta"
        sample_letta_identity["mxid"] = "@agent_12:matrix.test"
        sample_letta_identity["display_name"] = "Agent Twelve"
        sample_letta_identity["access_token"] = "token-agent"
        client.post("/api/v1/identities", json=sample_letta_identity)

        sample_identity["id"] = "custom_user_12"
        sample_identity["mxid"] = "@user_12:matrix.test"
        sample_identity["display_name"] = "User Twelve"
        sample_identity["access_token"] = "token-user"
        client.post("/api/v1/identities", json=sample_identity)

        client.post(
            "/api/v1/dm-rooms",
            json={
                "room_id": "!dm12:matrix.test",
                "mxid1": "@agent_12:matrix.test",
                "mxid2": "@user_12:matrix.test",
            },
        )

        with (
            patch("src.api.routes.identity._get_room_name", new=AsyncMock(return_value="Agent Twelve ↔ User Twelve")),
            patch("src.api.routes.identity._get_matrix_display_name", new=AsyncMock(return_value="Outdated")),
            patch("src.api.routes.identity._sync_identity_profile", new=AsyncMock()) as sync_profile,
        ):
            response = client.post(
                "/api/v1/dm-rooms/reconcile-names",
                json={"dry_run": False, "sync_profiles": True},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["mismatched_rooms"] == 1
        assert data["profile_mismatches"] == 2
        assert data["profiles_synced"] == 2
        assert data["failed"] == 0
        change = data["changes"][0]
        assert set(change["profiles_synced"]) == {"letta_agent-12", "custom_user_12"}
        assert sync_profile.await_count == 2

    def test_dm_reconcile_skips_non_agent_dm_rooms(self, client, sample_identity):
        sample_identity["id"] = "custom_user_21"
        sample_identity["mxid"] = "@user_21:matrix.test"
        client.post("/api/v1/identities", json=sample_identity)

        sample_identity2 = {
            "id": "custom_user_22",
            "identity_type": "custom",
            "mxid": "@user_22:matrix.test",
            "access_token": "token22",
            "display_name": "User 22",
            "avatar_url": None,
            "password_hash": None,
            "device_id": "DEV22",
        }
        client.post("/api/v1/identities", json=sample_identity2)

        client.post(
            "/api/v1/dm-rooms",
            json={
                "room_id": "!dm22:matrix.test",
                "mxid1": "@user_21:matrix.test",
                "mxid2": "@user_22:matrix.test",
            },
        )

        with (
            patch("src.api.routes.identity._get_room_name", new=AsyncMock(return_value=None)),
            patch("src.api.routes.identity._get_matrix_display_name", new=AsyncMock(return_value=None)),
        ):
            response = client.post("/api/v1/dm-rooms/reconcile-names", json={"dry_run": True})

        assert response.status_code == 200
        data = response.json()
        assert data["checked"] == 1
        assert data["agent_dm_rooms"] == 0
        assert data["mismatched_rooms"] == 0
        assert data["changes"] == []
