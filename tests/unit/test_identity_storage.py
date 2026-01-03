import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from src.core.identity_storage import IdentityStorageService, DMRoomStorageService


@pytest.fixture
def mock_identity_db():
    with patch('src.core.identity_storage.IdentityDB') as mock:
        yield mock


@pytest.fixture
def mock_dm_room_db():
    with patch('src.core.identity_storage.DMRoomDB') as mock:
        yield mock


@pytest.fixture
def identity_service(mock_identity_db):
    IdentityStorageService._instance = None
    IdentityStorageService._initialized = False
    service = IdentityStorageService()
    return service


@pytest.fixture
def dm_room_service(mock_dm_room_db):
    DMRoomStorageService._instance = None
    DMRoomStorageService._initialized = False
    service = DMRoomStorageService()
    return service


class TestIdentityStorageService:
    
    def test_singleton_pattern(self, mock_identity_db):
        IdentityStorageService._instance = None
        IdentityStorageService._initialized = False
        
        service1 = IdentityStorageService()
        service2 = IdentityStorageService()
        
        assert service1 is service2
    
    def test_get_caches_result(self, identity_service, mock_identity_db):
        mock_identity = MagicMock()
        mock_identity.id = "test_id"
        mock_identity.mxid = "@test:matrix.org"
        identity_service._db.get_by_id.return_value = mock_identity
        
        result1 = identity_service.get("test_id")
        result2 = identity_service.get("test_id")
        
        assert result1 == mock_identity
        assert result2 == mock_identity
        identity_service._db.get_by_id.assert_called_once_with("test_id")
    
    def test_get_by_mxid_caches_result(self, identity_service, mock_identity_db):
        mock_identity = MagicMock()
        mock_identity.id = "test_id"
        mock_identity.mxid = "@test:matrix.org"
        identity_service._db.get_by_mxid.return_value = mock_identity
        
        result = identity_service.get_by_mxid("@test:matrix.org")
        
        assert result == mock_identity
        assert "@test:matrix.org" in identity_service._mxid_cache
    
    def test_get_by_agent_id(self, identity_service, mock_identity_db):
        mock_identity = MagicMock()
        identity_service._db.get_by_id.return_value = mock_identity
        
        result = identity_service.get_by_agent_id("agent-123")
        
        identity_service._db.get_by_id.assert_called_with("letta_agent-123")
    
    def test_create_updates_cache(self, identity_service, mock_identity_db):
        mock_identity = MagicMock()
        mock_identity.id = "new_id"
        mock_identity.mxid = "@new:matrix.org"
        identity_service._db.create.return_value = mock_identity
        
        result = identity_service.create(
            identity_id="new_id",
            identity_type="letta",
            mxid="@new:matrix.org",
            access_token="token123"
        )
        
        assert result == mock_identity
        assert "new_id" in identity_service._cache
        assert "@new:matrix.org" in identity_service._mxid_cache
    
    def test_delete_clears_cache(self, identity_service, mock_identity_db):
        mock_identity = MagicMock()
        mock_identity.id = "del_id"
        mock_identity.mxid = "@del:matrix.org"
        identity_service._cache["del_id"] = mock_identity
        identity_service._mxid_cache["@del:matrix.org"] = "del_id"
        identity_service._db.get_by_id.return_value = mock_identity
        identity_service._db.delete.return_value = True
        
        result = identity_service.delete("del_id")
        
        assert result is True
        assert "del_id" not in identity_service._cache
        assert "@del:matrix.org" not in identity_service._mxid_cache
    
    def test_clear_cache(self, identity_service):
        identity_service._cache["test"] = MagicMock()
        identity_service._mxid_cache["@test:matrix.org"] = "test"
        
        identity_service.clear_cache()
        
        assert len(identity_service._cache) == 0
        assert len(identity_service._mxid_cache) == 0


class TestDMRoomStorageService:
    
    def test_singleton_pattern(self, mock_dm_room_db):
        DMRoomStorageService._instance = None
        DMRoomStorageService._initialized = False
        
        service1 = DMRoomStorageService()
        service2 = DMRoomStorageService()
        
        assert service1 is service2
    
    def test_get_caches_result(self, dm_room_service, mock_dm_room_db):
        mock_dm = MagicMock()
        mock_dm.participant_1 = "@a:matrix.org"
        mock_dm.participant_2 = "@b:matrix.org"
        dm_room_service._db.get_by_participants.return_value = mock_dm
        
        result1 = dm_room_service.get("@a:matrix.org", "@b:matrix.org")
        result2 = dm_room_service.get("@a:matrix.org", "@b:matrix.org")
        
        assert result1 == mock_dm
        dm_room_service._db.get_by_participants.assert_called_once()
    
    def test_get_or_create_returns_existing(self, dm_room_service, mock_dm_room_db):
        mock_dm = MagicMock()
        dm_room_service._db.get_by_participants.return_value = mock_dm
        
        result = dm_room_service.get_or_create(
            "!room:matrix.org",
            "@a:matrix.org",
            "@b:matrix.org"
        )
        
        assert result == mock_dm
        dm_room_service._db.create.assert_not_called()
    
    def test_get_or_create_creates_new(self, dm_room_service, mock_dm_room_db):
        mock_dm = MagicMock()
        dm_room_service._db.get_by_participants.return_value = None
        dm_room_service._db.create.return_value = mock_dm
        
        result = dm_room_service.get_or_create(
            "!room:matrix.org",
            "@a:matrix.org",
            "@b:matrix.org"
        )
        
        assert result == mock_dm
        dm_room_service._db.create.assert_called_once()
    
    def test_delete_clears_cache(self, dm_room_service, mock_dm_room_db):
        key = "@a:matrix.org<->@b:matrix.org"
        dm_room_service._cache[key] = MagicMock()
        dm_room_service._db.delete.return_value = True
        
        result = dm_room_service.delete("@a:matrix.org", "@b:matrix.org")
        
        assert result is True
        assert key not in dm_room_service._cache
