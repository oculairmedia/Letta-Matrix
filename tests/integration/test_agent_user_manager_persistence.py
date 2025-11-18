"""
SQLite-based integration tests for AgentUserManager persistence

These tests verify the database persistence layer of AgentUserManager
using a real SQLite in-memory database instead of mocks.

Run these tests with:
    pytest -m "integration and sqlite" tests/integration/test_agent_user_manager_persistence.py -v
"""
import pytest
import os
from unittest.mock import patch
from src.core.agent_user_manager import AgentUserManager, AgentUserMapping


@pytest.mark.integration
@pytest.mark.sqlite
class TestAgentUserManagerPersistence:
    """Test AgentUserManager database persistence with SQLite"""

    @pytest.mark.asyncio
    async def test_load_existing_mappings_from_database(self, mock_config, sqlite_db, monkeypatch):
        """Test loading mappings from database"""
        # Pre-populate database with test data
        sqlite_db.upsert(
            agent_id="agent-001",
            agent_name="TestAgent1",
            matrix_user_id="@agent_001:matrix.test",
            matrix_password="pass1",
            room_id="!room001:matrix.test",
            room_created=True
        )

        sqlite_db.upsert(
            agent_id="agent-002",
            agent_name="TestAgent2",
            matrix_user_id="@agent_002:matrix.test",
            matrix_password="pass2",
            room_id="!room002:matrix.test",
            room_created=True
        )

        # Add invitation status
        sqlite_db.update_invitation_status(
            agent_id="agent-001",
            invitee="@admin:matrix.test",
            status="joined"
        )

        # Patch get_engine in src.models.agent_mapping to use our test database
        import src.models.agent_mapping

        # Get the test engine from sqlite_db
        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        # Create manager and load mappings
        manager = AgentUserManager(mock_config)
        await manager.load_existing_mappings()

        # Verify mappings were loaded
        assert len(manager.mappings) == 2
        assert "agent-001" in manager.mappings
        assert "agent-002" in manager.mappings

        # Verify agent-001 details
        agent1 = manager.mappings["agent-001"]
        assert agent1.agent_name == "TestAgent1"
        assert agent1.matrix_user_id == "@agent_001:matrix.test"
        assert agent1.room_id == "!room001:matrix.test"
        assert agent1.room_created is True
        assert agent1.created is True

        # Verify invitation status
        assert agent1.invitation_status is not None
        assert agent1.invitation_status["@admin:matrix.test"] == "joined"

        # Verify agent-002 details
        agent2 = manager.mappings["agent-002"]
        assert agent2.agent_name == "TestAgent2"
        assert agent2.room_created is True

    @pytest.mark.asyncio
    async def test_load_mappings_empty_database(self, mock_config, sqlite_db, monkeypatch):
        """Test loading from empty database"""
        # Database is already empty from fixture

        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        manager = AgentUserManager(mock_config)
        await manager.load_existing_mappings()

        # Should have no mappings
        assert len(manager.mappings) == 0

    @pytest.mark.asyncio
    async def test_save_mappings_to_database(self, mock_config, sqlite_db, monkeypatch):
        """Test saving mappings to database"""
        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        # Create manager with mappings
        manager = AgentUserManager(mock_config)

        # Add mappings
        manager.mappings["agent-001"] = AgentUserMapping(
            agent_id="agent-001",
            agent_name="SaveTest1",
            matrix_user_id="@agent_001:matrix.test",
            matrix_password="pass1",
            created=True,
            room_id="!room001:matrix.test",
            room_created=True,
            invitation_status={"@admin:matrix.test": "joined"}
        )

        manager.mappings["agent-002"] = AgentUserMapping(
            agent_id="agent-002",
            agent_name="SaveTest2",
            matrix_user_id="@agent_002:matrix.test",
            matrix_password="pass2",
            created=True,
            room_id="!room002:matrix.test",
            room_created=False
        )

        # Save to database
        await manager.save_mappings()

        # Verify data was saved by querying database directly
        saved_mapping1 = sqlite_db.get_by_agent_id("agent-001")
        assert saved_mapping1 is not None
        assert saved_mapping1.agent_name == "SaveTest1"
        assert saved_mapping1.room_id == "!room001:matrix.test"
        assert saved_mapping1.room_created is True

        saved_mapping2 = sqlite_db.get_by_agent_id("agent-002")
        assert saved_mapping2 is not None
        assert saved_mapping2.agent_name == "SaveTest2"
        assert saved_mapping2.room_created is False

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip(self, mock_config, sqlite_db, monkeypatch):
        """Test saving then loading mappings (round trip)"""
        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        # Create first manager and save data
        manager1 = AgentUserManager(mock_config)
        manager1.mappings["agent-round-trip"] = AgentUserMapping(
            agent_id="agent-round-trip",
            agent_name="RoundTripTest",
            matrix_user_id="@roundtrip:matrix.test",
            matrix_password="secret123",
            created=True,
            room_id="!roundtrip:matrix.test",
            room_created=True,
            invitation_status={"@user1:matrix.test": "invited", "@user2:matrix.test": "joined"}
        )

        await manager1.save_mappings()

        # Create second manager and load data
        manager2 = AgentUserManager(mock_config)
        await manager2.load_existing_mappings()

        # Verify loaded data matches saved data
        assert "agent-round-trip" in manager2.mappings
        loaded = manager2.mappings["agent-round-trip"]
        assert loaded.agent_name == "RoundTripTest"
        assert loaded.matrix_user_id == "@roundtrip:matrix.test"
        assert loaded.matrix_password == "secret123"
        assert loaded.room_id == "!roundtrip:matrix.test"
        assert loaded.room_created is True
        assert loaded.created is True
        assert loaded.invitation_status["@user1:matrix.test"] == "invited"
        assert loaded.invitation_status["@user2:matrix.test"] == "joined"

    @pytest.mark.asyncio
    async def test_update_existing_mapping(self, mock_config, sqlite_db, monkeypatch):
        """Test updating an existing mapping in database"""
        # Pre-populate database
        sqlite_db.upsert(
            agent_id="agent-update",
            agent_name="OriginalName",
            matrix_user_id="@update:matrix.test",
            matrix_password="original_pass",
            room_id="!original:matrix.test",
            room_created=False
        )

        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        # Load, modify, and save
        manager = AgentUserManager(mock_config)
        await manager.load_existing_mappings()

        # Modify the mapping
        manager.mappings["agent-update"].agent_name = "UpdatedName"
        manager.mappings["agent-update"].room_id = "!updated:matrix.test"
        manager.mappings["agent-update"].room_created = True

        await manager.save_mappings()

        # Verify update in database
        updated = sqlite_db.get_by_agent_id("agent-update")
        assert updated.agent_name == "UpdatedName"
        assert updated.room_id == "!updated:matrix.test"
        assert updated.room_created is True

    @pytest.mark.asyncio
    async def test_load_mappings_without_invitation_status(self, mock_config, sqlite_db, monkeypatch):
        """Test backward compatibility - loading mappings without invitation status"""
        # Create mapping without any invitations
        sqlite_db.upsert(
            agent_id="agent-old-format",
            agent_name="OldFormatAgent",
            matrix_user_id="@oldformat:matrix.test",
            matrix_password="pass",
            room_id="!old:matrix.test",
            room_created=True
        )

        # Don't add any invitation status - simulates old data

        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        manager = AgentUserManager(mock_config)
        await manager.load_existing_mappings()

        # Verify mapping loaded correctly
        assert "agent-old-format" in manager.mappings
        mapping = manager.mappings["agent-old-format"]
        assert mapping.agent_name == "OldFormatAgent"

        # invitation_status should be empty dict (no invitations)
        assert mapping.invitation_status == {}

    @pytest.mark.asyncio
    async def test_save_mapping_with_special_characters(self, mock_config, sqlite_db, monkeypatch):
        """Test saving/loading mappings with special characters"""
        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        manager = AgentUserManager(mock_config)

        # Create mapping with special characters
        manager.mappings["agent-special"] = AgentUserMapping(
            agent_id="agent-special",
            agent_name="Test Agent™ with 日本語",
            matrix_user_id="@special_chars:matrix.test",
            matrix_password="p@ssw0rd!#$%",
            created=True,
            room_id="!special:matrix.test",
            room_created=True
        )

        await manager.save_mappings()

        # Verify saved correctly
        saved = sqlite_db.get_by_agent_id("agent-special")
        assert saved.agent_name == "Test Agent™ with 日本語"
        assert saved.matrix_password == "p@ssw0rd!#$%"

    @pytest.mark.asyncio
    async def test_concurrent_manager_operations(self, mock_config, sqlite_db, monkeypatch):
        """Test multiple manager instances accessing same database"""
        # Patch get_engine
        import src.models.agent_mapping

        test_engine = sqlite_db.Session().get_bind()
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: test_engine)

        # Manager 1 saves data
        manager1 = AgentUserManager(mock_config)
        manager1.mappings["agent-concurrent-1"] = AgentUserMapping(
            agent_id="agent-concurrent-1",
            agent_name="ConcurrentAgent1",
            matrix_user_id="@concurrent1:matrix.test",
            matrix_password="pass1",
            created=True
        )
        await manager1.save_mappings()

        # Manager 2 should be able to load data from manager 1
        manager2 = AgentUserManager(mock_config)
        await manager2.load_existing_mappings()

        assert "agent-concurrent-1" in manager2.mappings
        assert manager2.mappings["agent-concurrent-1"].agent_name == "ConcurrentAgent1"

        # Manager 2 adds more data
        manager2.mappings["agent-concurrent-2"] = AgentUserMapping(
            agent_id="agent-concurrent-2",
            agent_name="ConcurrentAgent2",
            matrix_user_id="@concurrent2:matrix.test",
            matrix_password="pass2",
            created=True
        )
        await manager2.save_mappings()

        # Manager 3 should see data from both managers
        manager3 = AgentUserManager(mock_config)
        await manager3.load_existing_mappings()

        assert len(manager3.mappings) == 2
        assert "agent-concurrent-1" in manager3.mappings
        assert "agent-concurrent-2" in manager3.mappings
