import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, Mock
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
