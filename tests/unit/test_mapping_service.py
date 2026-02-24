"""
Unit tests for the centralized mapping service.

These tests verify that the mapping service correctly interfaces with the database
and provides a consistent API for all consumers.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from src.core.mapping_service import (
    get_all_mappings,
    get_mapping_by_agent_id,
    get_mapping_by_room_id,
    get_mapping_by_matrix_user,
    upsert_mapping,
    update_invitation_status,
    delete_mapping,
    get_agents_without_rooms,
    get_agents_with_rooms,
    invalidate_cache,
)


@pytest.fixture
def mock_db():
    """Create a mock database instance"""
    with patch('src.core.mapping_service._get_db') as mock:
        db = Mock()
        mock.return_value = db
        yield db


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before each test"""
    invalidate_cache()
    yield
    invalidate_cache()


class TestGetAllMappings:
    """Tests for get_all_mappings()"""
    
    def test_returns_all_mappings(self, mock_db):
        """Test that all mappings are returned"""
        mock_db.export_to_dict.return_value = {
            "agent-1": {"agent_id": "agent-1", "room_id": "!room1:test"},
            "agent-2": {"agent_id": "agent-2", "room_id": "!room2:test"},
        }
        
        result = get_all_mappings()
        
        assert len(result) == 2
        assert "agent-1" in result
        assert "agent-2" in result
        mock_db.export_to_dict.assert_called_once()
    
    def test_caches_result(self, mock_db):
        """Test that results are cached"""
        mock_db.export_to_dict.return_value = {"agent-1": {"agent_id": "agent-1"}}
        
        # First call
        result1 = get_all_mappings()
        # Second call should use cache
        result2 = get_all_mappings()
        
        assert result1 == result2
        # Database should only be called once
        assert mock_db.export_to_dict.call_count == 1
    
    def test_returns_empty_on_error(self, mock_db):
        """Test that empty dict is returned on database error"""
        mock_db.export_to_dict.side_effect = Exception("Database error")
        
        result = get_all_mappings()
        
        assert result == {}


class TestGetMappingByAgentId:
    """Tests for get_mapping_by_agent_id()"""
    
    def test_returns_mapping_when_found(self, mock_db):
        """Test that mapping is returned when found"""
        mock_mapping = Mock()
        mock_mapping.removed_at = None
        mock_mapping.to_dict.return_value = {
            "agent_id": "agent-123",
            "agent_name": "TestAgent",
            "room_id": "!room:test"
        }
        mock_db.get_by_agent_id.return_value = mock_mapping
        
        result = get_mapping_by_agent_id("agent-123")
        
        assert result is not None
        assert result["agent_id"] == "agent-123"
        mock_db.get_by_agent_id.assert_called_once_with("agent-123")
    
    def test_returns_none_when_not_found(self, mock_db):
        """Test that None is returned when not found"""
        mock_db.get_by_agent_id.return_value = None
        
        result = get_mapping_by_agent_id("nonexistent")
        
        assert result is None
    
    def test_returns_none_on_error(self, mock_db):
        """Test that None is returned on database error"""
        mock_db.get_by_agent_id.side_effect = Exception("Database error")
        
        result = get_mapping_by_agent_id("agent-123")
        
        assert result is None


class TestGetMappingByRoomId:
    """Tests for get_mapping_by_room_id()"""
    
    def test_returns_mapping_when_found(self, mock_db):
        """Test that mapping is returned when found"""
        mock_mapping = Mock()
        mock_mapping.removed_at = None
        mock_mapping.to_dict.return_value = {
            "agent_id": "agent-123",
            "room_id": "!room:test"
        }
        mock_db.get_by_room_id.return_value = mock_mapping
        
        result = get_mapping_by_room_id("!room:test")
        
        assert result is not None
        assert result["room_id"] == "!room:test"
    
    def test_returns_none_when_not_found(self, mock_db):
        """Test that None is returned when not found"""
        mock_db.get_by_room_id.return_value = None
        
        result = get_mapping_by_room_id("!nonexistent:test")
        
        assert result is None


class TestGetMappingByMatrixUser:
    """Tests for get_mapping_by_matrix_user()"""
    
    def test_returns_mapping_when_found(self, mock_db):
        """Test that mapping is returned when found"""
        mock_mapping = Mock()
        mock_mapping.removed_at = None
        mock_mapping.to_dict.return_value = {
            "agent_id": "agent-123",
            "matrix_user_id": "@agent:test"
        }
        mock_db.get_by_matrix_user.return_value = mock_mapping
        
        result = get_mapping_by_matrix_user("@agent:test")
        
        assert result is not None
        assert result["matrix_user_id"] == "@agent:test"
    
    def test_returns_none_when_not_found(self, mock_db):
        """Test that None is returned when not found"""
        mock_db.get_by_matrix_user.return_value = None
        
        result = get_mapping_by_matrix_user("@nonexistent:test")
        
        assert result is None


class TestUpsertMapping:
    """Tests for upsert_mapping()"""
    
    def test_creates_new_mapping(self, mock_db):
        """Test creating a new mapping"""
        mock_mapping = Mock()
        mock_mapping.to_dict.return_value = {
            "agent_id": "agent-new",
            "agent_name": "NewAgent",
            "matrix_user_id": "@new:test",
            "room_id": "!new:test",
            "room_created": True
        }
        mock_db.upsert.return_value = mock_mapping
        
        result = upsert_mapping(
            agent_id="agent-new",
            agent_name="NewAgent",
            matrix_user_id="@new:test",
            matrix_password="password",
            room_id="!new:test",
            room_created=True
        )
        
        assert result is not None
        assert result["agent_id"] == "agent-new"
        mock_db.upsert.assert_called_once()
    
    def test_invalidates_cache(self, mock_db):
        """Test that cache is invalidated after upsert"""
        # Prime the cache
        mock_db.export_to_dict.return_value = {"agent-1": {"agent_id": "agent-1"}}
        get_all_mappings()
        assert mock_db.export_to_dict.call_count == 1
        
        # Upsert should invalidate cache
        mock_mapping = Mock()
        mock_mapping.to_dict.return_value = {"agent_id": "agent-new"}
        mock_db.upsert.return_value = mock_mapping
        upsert_mapping("agent-new", "New", "@new:test", "pass")
        
        # Next get_all should hit DB again
        get_all_mappings()
        assert mock_db.export_to_dict.call_count == 2
    
    def test_returns_none_on_error(self, mock_db):
        """Test that None is returned on database error"""
        mock_db.upsert.side_effect = Exception("Database error")
        
        result = upsert_mapping("agent", "Agent", "@agent:test", "pass")
        
        assert result is None


class TestUpdateInvitationStatus:
    """Tests for update_invitation_status()"""
    
    def test_updates_status(self, mock_db):
        """Test updating invitation status"""
        result = update_invitation_status("agent-123", "@user:test", "joined")
        
        assert result is True
        mock_db.update_invitation_status.assert_called_once_with(
            "agent-123", "@user:test", "joined"
        )
    
    def test_returns_false_on_error(self, mock_db):
        """Test that False is returned on database error"""
        mock_db.update_invitation_status.side_effect = Exception("Database error")
        
        result = update_invitation_status("agent-123", "@user:test", "joined")
        
        assert result is False


class TestDeleteMapping:
    """Tests for delete_mapping()"""
    
    def test_deletes_mapping(self, mock_db):
        """Test deleting a mapping"""
        mock_db.delete.return_value = True
        
        result = delete_mapping("agent-123")
        
        assert result is True
        mock_db.delete.assert_called_once_with("agent-123")
    
    def test_returns_false_when_not_found(self, mock_db):
        """Test that False is returned when not found"""
        mock_db.delete.return_value = False
        
        result = delete_mapping("nonexistent")
        
        assert result is False


class TestFilterFunctions:
    """Tests for filter helper functions"""
    
    def test_get_agents_without_rooms(self, mock_db):
        """Test getting agents without rooms"""
        mock_db.export_to_dict.return_value = {
            "agent-1": {"agent_id": "agent-1", "room_id": "!room:test", "room_created": True},
            "agent-2": {"agent_id": "agent-2", "room_id": None, "room_created": False},
            "agent-3": {"agent_id": "agent-3", "room_id": "!room3:test", "room_created": False},
        }
        
        result = get_agents_without_rooms()
        
        assert len(result) == 2
        agent_ids = [m["agent_id"] for m in result]
        assert "agent-2" in agent_ids
        assert "agent-3" in agent_ids
        assert "agent-1" not in agent_ids
    
    def test_get_agents_with_rooms(self, mock_db):
        """Test getting agents with rooms"""
        mock_db.export_to_dict.return_value = {
            "agent-1": {"agent_id": "agent-1", "room_id": "!room:test", "room_created": True},
            "agent-2": {"agent_id": "agent-2", "room_id": None, "room_created": False},
            "agent-3": {"agent_id": "agent-3", "room_id": "!room3:test", "room_created": True},
        }
        
        result = get_agents_with_rooms()
        
        assert len(result) == 2
        agent_ids = [m["agent_id"] for m in result]
        assert "agent-1" in agent_ids
        assert "agent-3" in agent_ids
        assert "agent-2" not in agent_ids


class TestCacheInvalidation:
    """Tests for cache invalidation"""
    
    def test_invalidate_cache_forces_reload(self, mock_db):
        """Test that invalidate_cache forces a database reload"""
        mock_db.export_to_dict.return_value = {"agent-1": {"agent_id": "agent-1"}}
        
        # First call - hits DB
        get_all_mappings()
        assert mock_db.export_to_dict.call_count == 1
        
        # Second call - uses cache
        get_all_mappings()
        assert mock_db.export_to_dict.call_count == 1
        
        # Invalidate cache
        invalidate_cache()
        
        # Third call - hits DB again
        get_all_mappings()
        assert mock_db.export_to_dict.call_count == 2
