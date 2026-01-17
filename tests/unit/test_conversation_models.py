"""
Unit tests for conversation models (RoomConversation, InterAgentConversation).
"""
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from src.models.agent_mapping import Base
from src.models.conversation import (
    RoomConversation,
    InterAgentConversation,
    RoomConversationDB,
    InterAgentConversationDB,
)


@pytest.fixture
def conversation_engine():
    """Create in-memory SQLite engine with conversation tables."""
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def room_conv_db(conversation_engine, monkeypatch):
    """Provide RoomConversationDB using test engine."""
    import src.models.agent_mapping
    monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
    return RoomConversationDB()


@pytest.fixture
def inter_agent_db(conversation_engine, monkeypatch):
    """Provide InterAgentConversationDB using test engine."""
    import src.models.agent_mapping
    monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
    return InterAgentConversationDB()


class TestRoomConversation:

    def test_create_room_conversation(self, room_conv_db):
        conv = room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-001",
            strategy="per-room"
        )
        
        assert conv.room_id == "!room1:matrix.test"
        assert conv.agent_id == "agent-001"
        assert conv.conversation_id == "conv-001"
        assert conv.strategy == "per-room"
        assert conv.user_mxid is None
        assert conv.created_at is not None

    def test_create_per_user_conversation(self, room_conv_db):
        conv = room_conv_db.create(
            room_id="!dm:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-user-001",
            strategy="per-user",
            user_mxid="@alice:matrix.test"
        )
        
        assert conv.strategy == "per-user"
        assert conv.user_mxid == "@alice:matrix.test"

    def test_get_by_room_and_agent(self, room_conv_db):
        room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-001"
        )
        
        found = room_conv_db.get_by_room_and_agent("!room1:matrix.test", "agent-001")
        assert found is not None
        assert found.conversation_id == "conv-001"
        
        not_found = room_conv_db.get_by_room_and_agent("!room1:matrix.test", "agent-999")
        assert not_found is None

    def test_get_by_conversation_id(self, room_conv_db):
        room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-unique-id"
        )
        
        found = room_conv_db.get_by_conversation_id("conv-unique-id")
        assert found is not None
        assert found.room_id == "!room1:matrix.test"

    def test_get_or_create_existing(self, room_conv_db):
        room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-001"
        )
        
        conv, created = room_conv_db.get_or_create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-new"  # Should be ignored
        )
        
        assert created is False
        assert conv.conversation_id == "conv-001"  # Original

    def test_get_or_create_new(self, room_conv_db):
        conv, created = room_conv_db.get_or_create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-001"
        )
        
        assert created is True
        assert conv.conversation_id == "conv-001"

    def test_get_all_for_agent(self, room_conv_db):
        room_conv_db.create("!room1:matrix.test", "agent-001", "conv-001")
        room_conv_db.create("!room2:matrix.test", "agent-001", "conv-002")
        room_conv_db.create("!room3:matrix.test", "agent-002", "conv-003")
        
        convs = room_conv_db.get_all_for_agent("agent-001")
        assert len(convs) == 2

    def test_get_all_for_room(self, room_conv_db):
        room_conv_db.create("!room1:matrix.test", "agent-001", "conv-001")
        room_conv_db.create("!room1:matrix.test", "agent-002", "conv-002")
        room_conv_db.create("!room2:matrix.test", "agent-001", "conv-003")
        
        convs = room_conv_db.get_all_for_room("!room1:matrix.test")
        assert len(convs) == 2

    def test_update_last_message(self, room_conv_db):
        conv = room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-001"
        )
        original_time = conv.last_message_at
        
        import time
        time.sleep(0.01)  # Ensure time difference
        
        updated = room_conv_db.update_last_message("!room1:matrix.test", "agent-001")
        assert updated is not None
        assert updated.last_message_at > original_time

    def test_delete(self, room_conv_db):
        room_conv_db.create("!room1:matrix.test", "agent-001", "conv-001")
        
        result = room_conv_db.delete("!room1:matrix.test", "agent-001")
        assert result is True
        
        found = room_conv_db.get_by_room_and_agent("!room1:matrix.test", "agent-001")
        assert found is None
        
        result = room_conv_db.delete("!room1:matrix.test", "agent-001")
        assert result is False

    def test_unique_constraint_room_agent_with_user(self, room_conv_db):
        """Test unique constraint with explicit user_mxid (SQLite treats NULL as distinct)."""
        room_conv_db.create(
            "!room1:matrix.test", "agent-001", "conv-001",
            strategy="per-user", user_mxid="@user:test"
        )
        
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            room_conv_db.create(
                "!room1:matrix.test", "agent-001", "conv-002",
                strategy="per-user", user_mxid="@user:test"
            )

    def test_per_user_isolation(self, room_conv_db):
        room_conv_db.create(
            room_id="!dm:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-alice",
            strategy="per-user",
            user_mxid="@alice:matrix.test"
        )
        room_conv_db.create(
            room_id="!dm:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-bob",
            strategy="per-user",
            user_mxid="@bob:matrix.test"
        )
        
        alice_conv = room_conv_db.get_by_room_and_agent(
            "!dm:matrix.test", "agent-001", "@alice:matrix.test"
        )
        bob_conv = room_conv_db.get_by_room_and_agent(
            "!dm:matrix.test", "agent-001", "@bob:matrix.test"
        )
        
        assert alice_conv.conversation_id == "conv-alice"
        assert bob_conv.conversation_id == "conv-bob"

    def test_to_dict(self, room_conv_db):
        conv = room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="conv-001",
            strategy="per-room"
        )
        
        d = conv.to_dict()
        assert d["room_id"] == "!room1:matrix.test"
        assert d["agent_id"] == "agent-001"
        assert d["conversation_id"] == "conv-001"
        assert d["strategy"] == "per-room"
        assert "created_at" in d


class TestInterAgentConversation:

    def test_create_inter_agent_conversation(self, inter_agent_db):
        conv = inter_agent_db.create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-001"
        )
        
        assert conv.source_agent_id == "agent-001"
        assert conv.target_agent_id == "agent-002"
        assert conv.room_id == "!room1:matrix.test"
        assert conv.conversation_id == "inter-conv-001"

    def test_create_with_user(self, inter_agent_db):
        conv = inter_agent_db.create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-001",
            user_mxid="@alice:matrix.test"
        )
        
        assert conv.user_mxid == "@alice:matrix.test"

    def test_get_inter_agent_conversation(self, inter_agent_db):
        inter_agent_db.create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-001"
        )
        
        found = inter_agent_db.get("agent-001", "agent-002", "!room1:matrix.test")
        assert found is not None
        assert found.conversation_id == "inter-conv-001"
        
        not_found = inter_agent_db.get("agent-002", "agent-001", "!room1:matrix.test")
        assert not_found is None  # Direction matters

    def test_get_or_create_existing(self, inter_agent_db):
        inter_agent_db.create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-001"
        )
        
        conv, created = inter_agent_db.get_or_create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-new"
        )
        
        assert created is False
        assert conv.conversation_id == "inter-conv-001"

    def test_get_all_for_agent(self, inter_agent_db):
        inter_agent_db.create("agent-001", "agent-002", "!room1:matrix.test", "conv-1")
        inter_agent_db.create("agent-003", "agent-001", "!room2:matrix.test", "conv-2")
        inter_agent_db.create("agent-002", "agent-003", "!room3:matrix.test", "conv-3")
        
        convs = inter_agent_db.get_all_for_agent("agent-001")
        assert len(convs) == 2  # As source and as target

    def test_update_last_message(self, inter_agent_db):
        conv = inter_agent_db.create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-001"
        )
        original_time = conv.last_message_at
        
        import time
        time.sleep(0.01)
        
        updated = inter_agent_db.update_last_message(
            "agent-001", "agent-002", "!room1:matrix.test"
        )
        assert updated is not None
        assert updated.last_message_at > original_time

    def test_delete(self, inter_agent_db):
        inter_agent_db.create("agent-001", "agent-002", "!room1:matrix.test", "conv-1")
        
        result = inter_agent_db.delete("agent-001", "agent-002", "!room1:matrix.test")
        assert result is True
        
        found = inter_agent_db.get("agent-001", "agent-002", "!room1:matrix.test")
        assert found is None

    def test_unique_constraint_with_user(self, inter_agent_db):
        """Test unique constraint with explicit user_mxid (SQLite treats NULL as distinct)."""
        inter_agent_db.create(
            "agent-001", "agent-002", "!room1:matrix.test", "conv-1",
            user_mxid="@user:test"
        )
        
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            inter_agent_db.create(
                "agent-001", "agent-002", "!room1:matrix.test", "conv-2",
                user_mxid="@user:test"
            )

    def test_to_dict(self, inter_agent_db):
        conv = inter_agent_db.create(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            conversation_id="inter-conv-001"
        )
        
        d = conv.to_dict()
        assert d["source_agent_id"] == "agent-001"
        assert d["target_agent_id"] == "agent-002"
        assert d["room_id"] == "!room1:matrix.test"
        assert d["conversation_id"] == "inter-conv-001"
        assert "created_at" in d


class TestStaleConversationCleanup:

    def test_delete_stale_room_conversations(self, room_conv_db, conversation_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=conversation_engine)
        
        room_conv_db.create("!room1:matrix.test", "agent-001", "conv-fresh")
        room_conv_db.create("!room2:matrix.test", "agent-001", "conv-stale")
        
        session = Session()
        stale_conv = session.query(RoomConversation).filter_by(
            conversation_id="conv-stale"
        ).first()
        stale_conv.last_message_at = datetime.utcnow() - timedelta(days=60)
        session.commit()
        session.close()
        
        deleted_count = room_conv_db.delete_stale(days=30)
        assert deleted_count == 1
        
        fresh = room_conv_db.get_by_conversation_id("conv-fresh")
        stale = room_conv_db.get_by_conversation_id("conv-stale")
        assert fresh is not None
        assert stale is None

    def test_delete_stale_inter_agent_conversations(self, inter_agent_db, conversation_engine):
        from sqlalchemy.orm import sessionmaker
        Session = sessionmaker(bind=conversation_engine)
        
        inter_agent_db.create("agent-001", "agent-002", "!room1:matrix.test", "conv-fresh")
        inter_agent_db.create("agent-003", "agent-004", "!room2:matrix.test", "conv-stale")
        
        session = Session()
        stale_conv = session.query(InterAgentConversation).filter_by(
            conversation_id="conv-stale"
        ).first()
        stale_conv.last_message_at = datetime.utcnow() - timedelta(days=60)
        session.commit()
        session.close()
        
        deleted_count = inter_agent_db.delete_stale(days=30)
        assert deleted_count == 1
        
        fresh = inter_agent_db.get_by_conversation_id("conv-fresh")
        stale = inter_agent_db.get_by_conversation_id("conv-stale")
        assert fresh is not None
        assert stale is None
