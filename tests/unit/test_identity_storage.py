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

    def test_get_expires_stale_cache_entry_and_refetches(self, identity_service):
        stale_identity = MagicMock()
        stale_identity.id = "test_id"
        stale_identity.mxid = "@test:matrix.org"
        refreshed_identity = MagicMock()
        refreshed_identity.id = "test_id"
        refreshed_identity.mxid = "@test:matrix.org"

        identity_service._cache_ttl_seconds = 60
        identity_service._cache["test_id"] = stale_identity
        identity_service._cache_timestamps["test_id"] = 100.0
        identity_service._mxid_cache["@test:matrix.org"] = "test_id"
        identity_service._mxid_cache_timestamps["@test:matrix.org"] = 100.0
        identity_service._db.get_by_id.return_value = refreshed_identity

        with patch.object(identity_service, "_now", return_value=200.0):
            result = identity_service.get("test_id")

        assert result == refreshed_identity
        identity_service._db.get_by_id.assert_called_once_with("test_id")
        assert identity_service._cache["test_id"] == refreshed_identity
    
    def test_get_by_mxid_caches_result(self, identity_service, mock_identity_db):
        mock_identity = MagicMock()
        mock_identity.id = "test_id"
        mock_identity.mxid = "@test:matrix.org"
        identity_service._db.get_by_mxid.return_value = mock_identity
        
        result = identity_service.get_by_mxid("@test:matrix.org")
        
        assert result == mock_identity
        assert "@test:matrix.org" in identity_service._mxid_cache

    def test_get_by_mxid_expires_stale_cache_entry_and_refetches(self, identity_service):
        stale_identity = MagicMock()
        stale_identity.id = "test_id"
        stale_identity.mxid = "@test:matrix.org"
        refreshed_identity = MagicMock()
        refreshed_identity.id = "test_id"
        refreshed_identity.mxid = "@test:matrix.org"

        identity_service._cache_ttl_seconds = 60
        identity_service._cache["test_id"] = stale_identity
        identity_service._cache_timestamps["test_id"] = 100.0
        identity_service._mxid_cache["@test:matrix.org"] = "test_id"
        identity_service._mxid_cache_timestamps["@test:matrix.org"] = 100.0
        identity_service._db.get_by_mxid.return_value = refreshed_identity

        with patch.object(identity_service, "_now", return_value=200.0):
            result = identity_service.get_by_mxid("@test:matrix.org")

        assert result == refreshed_identity
        identity_service._db.get_by_mxid.assert_called_once_with("@test:matrix.org")
        assert identity_service._cache["test_id"] == refreshed_identity
    
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
        identity_service._cache_timestamps["test"] = 123.0
        identity_service._mxid_cache_timestamps["@test:matrix.org"] = 123.0
        
        identity_service.clear_cache()
        
        assert len(identity_service._cache) == 0
        assert len(identity_service._mxid_cache) == 0
        assert len(identity_service._cache_timestamps) == 0
        assert len(identity_service._mxid_cache_timestamps) == 0

    def test_set_cache_entry_prunes_oldest_when_over_max_entries(self, identity_service):
        identity_service._cache_ttl_seconds = 10_000
        identity_service._max_cache_entries = 1

        first_identity = MagicMock()
        first_identity.id = "first"
        first_identity.mxid = "@first:matrix.org"
        second_identity = MagicMock()
        second_identity.id = "second"
        second_identity.mxid = "@second:matrix.org"

        with patch.object(identity_service, "_now", side_effect=[100.0, 100.0, 101.0, 101.0]):
            identity_service._set_cache_entry(first_identity)
            identity_service._set_cache_entry(second_identity)

        assert "first" not in identity_service._cache
        assert "second" in identity_service._cache
        assert "@first:matrix.org" not in identity_service._mxid_cache
        assert identity_service._mxid_cache.get("@second:matrix.org") == "second"


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
