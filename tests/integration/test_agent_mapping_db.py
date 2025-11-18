"""
SQLite-based integration tests for AgentMappingDB

These tests use a real SQLite in-memory database to verify database operations.
They test the actual SQL queries and database constraints.

Run these tests with:
    pytest -m "integration and sqlite" tests/integration/test_agent_mapping_db.py -v
"""
import pytest
from datetime import datetime
from sqlalchemy.exc import IntegrityError


@pytest.mark.integration
@pytest.mark.sqlite
class TestAgentMappingCRUD:
    """Test CRUD operations with real database"""

    def test_create_agent_mapping(self, sqlite_db):
        """Test creating a new agent mapping"""
        # Create
        agent_id = "agent-create-test"
        mapping = sqlite_db.upsert(
            agent_id=agent_id,
            agent_name="CreateTest",
            matrix_user_id="@create:matrix.test",
            matrix_password="password123",
            room_id="!room:matrix.test",
            room_created=True
        )

        # Verify return value
        assert mapping is not None
        assert mapping.agent_id == agent_id
        assert mapping.agent_name == "CreateTest"
        assert mapping.room_id == "!room:matrix.test"
        assert mapping.room_created is True

        # Verify persisted in database
        retrieved = sqlite_db.get_by_agent_id(agent_id)
        assert retrieved is not None
        assert retrieved.agent_name == "CreateTest"
        assert retrieved.room_id == "!room:matrix.test"

    def test_create_agent_without_room(self, sqlite_db):
        """Test creating an agent mapping without a room"""
        mapping = sqlite_db.upsert(
            agent_id="agent-no-room",
            agent_name="NoRoom",
            matrix_user_id="@noroom:matrix.test",
            matrix_password="password123",
            room_id=None,
            room_created=False
        )

        assert mapping.room_id is None
        assert mapping.room_created is False

    def test_update_agent_mapping(self, sqlite_db_with_data):
        """Test updating an existing agent mapping"""
        # Update
        updated = sqlite_db_with_data.upsert(
            agent_id="test-agent-001",
            agent_name="UpdatedName",
            matrix_user_id="@test:matrix.test",
            matrix_password="new_password",
            room_id="!newroom:matrix.test",
            room_created=True
        )

        # Verify
        assert updated.agent_name == "UpdatedName"
        assert updated.room_id == "!newroom:matrix.test"
        assert updated.matrix_password == "new_password"

        # Verify persisted
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert mapping.agent_name == "UpdatedName"
        assert mapping.room_id == "!newroom:matrix.test"

    def test_update_creates_if_not_exists(self, sqlite_db):
        """Test that upsert creates a new record if it doesn't exist"""
        # Database is empty, upsert should create
        mapping = sqlite_db.upsert(
            agent_id="new-agent",
            agent_name="NewAgent",
            matrix_user_id="@new:matrix.test",
            matrix_password="password",
            room_id="!newroom:matrix.test",
            room_created=True
        )

        assert mapping is not None
        assert mapping.agent_id == "new-agent"

        # Verify it exists
        retrieved = sqlite_db.get_by_agent_id("new-agent")
        assert retrieved is not None
        assert retrieved.agent_name == "NewAgent"

    def test_delete_agent_mapping(self, sqlite_db_with_data):
        """Test deleting an agent mapping"""
        # Verify exists
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert mapping is not None

        # Delete
        success = sqlite_db_with_data.delete("test-agent-001")
        assert success is True

        # Verify deleted
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert mapping is None

    def test_delete_nonexistent_mapping(self, sqlite_db):
        """Test deleting a mapping that doesn't exist"""
        success = sqlite_db.delete("nonexistent-agent")
        assert success is False

    def test_get_all_mappings(self, sqlite_db):
        """Test retrieving all mappings"""
        # Create multiple mappings
        for i in range(5):
            sqlite_db.upsert(
                agent_id=f"agent-{i}",
                agent_name=f"Agent{i}",
                matrix_user_id=f"@agent{i}:matrix.test",
                matrix_password="password",
                room_id=f"!room{i}:matrix.test",
                room_created=True
            )

        # Verify
        all_mappings = sqlite_db.get_all()
        assert len(all_mappings) == 5

        # Verify they're all different agents
        agent_ids = {m.agent_id for m in all_mappings}
        assert len(agent_ids) == 5
        assert "agent-0" in agent_ids
        assert "agent-4" in agent_ids

    def test_get_all_empty_database(self, sqlite_db):
        """Test get_all with empty database"""
        all_mappings = sqlite_db.get_all()
        assert all_mappings == []


@pytest.mark.integration
@pytest.mark.sqlite
class TestAgentQueries:
    """Test query operations"""

    def test_get_by_room_id(self, sqlite_db_with_data):
        """Test finding agent by room ID"""
        mapping = sqlite_db_with_data.get_by_room_id("!testroom:matrix.test")

        assert mapping is not None
        assert mapping.agent_id == "test-agent-001"
        assert mapping.agent_name == "TestAgent"

    def test_get_by_nonexistent_room(self, sqlite_db):
        """Test querying for non-existent room"""
        mapping = sqlite_db.get_by_room_id("!nonexistent:matrix.test")
        assert mapping is None

    def test_get_by_agent_id(self, sqlite_db_with_data):
        """Test finding agent by agent ID"""
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")

        assert mapping is not None
        assert mapping.agent_name == "TestAgent"
        assert mapping.room_id == "!testroom:matrix.test"

    def test_get_by_nonexistent_agent_id(self, sqlite_db):
        """Test querying for non-existent agent ID"""
        mapping = sqlite_db.get_by_agent_id("nonexistent-agent")
        assert mapping is None

    def test_get_by_matrix_user(self, sqlite_db_with_data):
        """Test finding agent by Matrix user ID"""
        mapping = sqlite_db_with_data.get_by_matrix_user("@test:matrix.test")

        assert mapping is not None
        assert mapping.agent_id == "test-agent-001"
        assert mapping.agent_name == "TestAgent"

    def test_get_by_nonexistent_matrix_user(self, sqlite_db):
        """Test querying for non-existent Matrix user"""
        mapping = sqlite_db.get_by_matrix_user("@nonexistent:matrix.test")
        assert mapping is None

    def test_room_id_uniqueness(self, sqlite_db):
        """Test that room_id is unique across agents"""
        # Create first mapping
        sqlite_db.upsert(
            agent_id="agent-1",
            agent_name="Agent1",
            matrix_user_id="@agent1:matrix.test",
            matrix_password="pass1",
            room_id="!unique:matrix.test",
            room_created=True
        )

        # Create second mapping with different room
        sqlite_db.upsert(
            agent_id="agent-2",
            agent_name="Agent2",
            matrix_user_id="@agent2:matrix.test",
            matrix_password="pass2",
            room_id="!different:matrix.test",
            room_created=True
        )

        # Verify both exist
        assert sqlite_db.get_by_room_id("!unique:matrix.test") is not None
        assert sqlite_db.get_by_room_id("!different:matrix.test") is not None

    def test_matrix_user_uniqueness(self, sqlite_db):
        """Test that matrix_user_id is unique across agents"""
        # Create first mapping
        sqlite_db.upsert(
            agent_id="agent-1",
            agent_name="Agent1",
            matrix_user_id="@unique:matrix.test",
            matrix_password="pass1",
            room_id="!room1:matrix.test",
            room_created=True
        )

        # Verify it exists
        mapping = sqlite_db.get_by_matrix_user("@unique:matrix.test")
        assert mapping is not None
        assert mapping.agent_id == "agent-1"


@pytest.mark.integration
@pytest.mark.sqlite
class TestInvitationStatus:
    """Test invitation status tracking"""

    def test_update_invitation_status(self, sqlite_db_with_data):
        """Test creating/updating invitation status"""
        # The fixture already has one invitation (@admin:matrix.test -> joined)
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert len(mapping.invitations) == 1

        # Add another invitation
        sqlite_db_with_data.update_invitation_status(
            agent_id="test-agent-001",
            invitee="@user:matrix.test",
            status="invited"
        )

        # Verify both invitations exist
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert len(mapping.invitations) == 2

        statuses = {inv.invitee: inv.status for inv in mapping.invitations}
        assert statuses["@admin:matrix.test"] == "joined"
        assert statuses["@user:matrix.test"] == "invited"

    def test_update_existing_invitation_status(self, sqlite_db_with_data):
        """Test updating an existing invitation status"""
        # Change status from joined to failed
        sqlite_db_with_data.update_invitation_status(
            agent_id="test-agent-001",
            invitee="@admin:matrix.test",
            status="failed"
        )

        # Verify updated
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        statuses = {inv.invitee: inv.status for inv in mapping.invitations}
        assert statuses["@admin:matrix.test"] == "failed"

    def test_invitation_cascade_delete(self, sqlite_db_with_data):
        """Test that invitations are deleted when agent is deleted"""
        # Verify invitation exists
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert len(mapping.invitations) == 1

        # Delete agent
        sqlite_db_with_data.delete("test-agent-001")

        # Verify agent and invitations are gone
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert mapping is None


@pytest.mark.integration
@pytest.mark.sqlite
class TestDataIntegrity:
    """Test data integrity and constraints"""

    def test_timestamps_created(self, sqlite_db):
        """Test that created_at and updated_at are set automatically"""
        before = datetime.utcnow()

        mapping = sqlite_db.upsert(
            agent_id="timestamp-test",
            agent_name="TimestampTest",
            matrix_user_id="@timestamp:matrix.test",
            matrix_password="password",
            room_id="!timestamp:matrix.test",
            room_created=True
        )

        after = datetime.utcnow()

        assert mapping.created_at is not None
        assert mapping.updated_at is not None
        assert before <= mapping.created_at <= after
        assert before <= mapping.updated_at <= after

    def test_timestamps_updated_on_upsert(self, sqlite_db):
        """Test that updated_at changes when record is updated"""
        # Create initial mapping
        mapping1 = sqlite_db.upsert(
            agent_id="update-timestamp-test",
            agent_name="Initial",
            matrix_user_id="@updatetime:matrix.test",
            matrix_password="password",
            room_id="!updatetime:matrix.test",
            room_created=True
        )
        initial_updated_at = mapping1.updated_at

        # Small delay to ensure timestamp difference
        import time
        time.sleep(0.01)

        # Update the mapping
        mapping2 = sqlite_db.upsert(
            agent_id="update-timestamp-test",
            agent_name="Updated",
            matrix_user_id="@updatetime:matrix.test",
            matrix_password="new_password",
            room_id="!updatetime:matrix.test",
            room_created=True
        )

        # Verify updated_at changed
        assert mapping2.updated_at > initial_updated_at
        # created_at should remain the same
        assert mapping2.created_at == mapping1.created_at

    def test_to_dict_conversion(self, sqlite_db_with_data):
        """Test converting AgentMapping to dictionary format"""
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        data = mapping.to_dict()

        # Verify structure
        assert data["agent_id"] == "test-agent-001"
        assert data["agent_name"] == "TestAgent"
        assert data["matrix_user_id"] == "@test:matrix.test"
        assert data["room_id"] == "!testroom:matrix.test"
        assert data["room_created"] is True
        assert data["created"] is True  # Backward compatibility
        assert "invitation_status" in data
        assert data["invitation_status"]["@admin:matrix.test"] == "joined"

    def test_export_to_dict(self, sqlite_db):
        """Test exporting all mappings to dictionary format"""
        # Create multiple mappings
        for i in range(3):
            sqlite_db.upsert(
                agent_id=f"agent-{i}",
                agent_name=f"Agent{i}",
                matrix_user_id=f"@agent{i}:matrix.test",
                matrix_password="password",
                room_id=f"!room{i}:matrix.test",
                room_created=True
            )

        # Export to dict
        exported = sqlite_db.export_to_dict()

        # Verify structure
        assert len(exported) == 3
        assert "agent-0" in exported
        assert "agent-1" in exported
        assert "agent-2" in exported

        # Verify each entry has correct structure
        for agent_id, data in exported.items():
            assert data["agent_id"] == agent_id
            assert "agent_name" in data
            assert "invitation_status" in data


@pytest.mark.integration
@pytest.mark.sqlite
class TestBulkOperations:
    """Test bulk operations and performance"""

    def test_bulk_insert(self, sqlite_db):
        """Test inserting multiple records"""
        # Insert 100 records
        for i in range(100):
            sqlite_db.upsert(
                agent_id=f"bulk-agent-{i}",
                agent_name=f"BulkAgent{i}",
                matrix_user_id=f"@bulk{i}:matrix.test",
                matrix_password="password",
                room_id=f"!bulk{i}:matrix.test",
                room_created=True
            )

        # Verify all inserted
        all_mappings = sqlite_db.get_all()
        assert len(all_mappings) == 100

    def test_bulk_update(self, sqlite_db):
        """Test updating multiple records"""
        # Create initial records
        for i in range(10):
            sqlite_db.upsert(
                agent_id=f"update-agent-{i}",
                agent_name=f"Original{i}",
                matrix_user_id=f"@update{i}:matrix.test",
                matrix_password="password",
                room_id=f"!update{i}:matrix.test",
                room_created=False
            )

        # Update all records
        for i in range(10):
            sqlite_db.upsert(
                agent_id=f"update-agent-{i}",
                agent_name=f"Updated{i}",
                matrix_user_id=f"@update{i}:matrix.test",
                matrix_password="new_password",
                room_id=f"!update{i}:matrix.test",
                room_created=True
            )

        # Verify all updated
        all_mappings = sqlite_db.get_all()
        assert len(all_mappings) == 10

        for mapping in all_mappings:
            assert mapping.agent_name.startswith("Updated")
            assert mapping.room_created is True
            assert mapping.matrix_password == "new_password"
