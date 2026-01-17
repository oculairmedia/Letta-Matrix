"""
Unit tests for ConversationService.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError

from letta_client import APIError, NotFoundError

from src.models.agent_mapping import Base
from src.models.conversation import RoomConversationDB, InterAgentConversationDB
from src.core.conversation_service import (
    ConversationService,
    get_conversation_service,
    reset_conversation_service,
    DEFAULT_ISOLATED_BLOCK_LABELS,
)


def make_api_error(message: str = "API error") -> APIError:
    mock_request = MagicMock()
    mock_request.url = "http://test"
    mock_request.method = "POST"
    return APIError(message, mock_request, body=None)


def make_not_found_error(message: str = "Not found") -> NotFoundError:
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.headers = {}
    return NotFoundError(message, response=mock_response, body=None)


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
def mock_letta_client():
    """Mock Letta client with conversations API."""
    client = MagicMock()
    client.conversations = MagicMock()
    return client


@pytest.fixture
def conversation_service(conversation_engine, mock_letta_client, monkeypatch):
    """Provide ConversationService with mocked dependencies."""
    import src.models.agent_mapping
    monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
    
    reset_conversation_service()
    service = ConversationService(letta_client=mock_letta_client)
    yield service
    reset_conversation_service()


class TestConversationStrategy:
    """Tests for get_conversation_strategy."""

    @pytest.mark.asyncio
    async def test_dm_room_returns_per_user(self, conversation_service):
        """2 members = DM = per-user strategy."""
        strategy = await conversation_service.get_conversation_strategy(
            room_id="!dm:matrix.test",
            room_member_count=2
        )
        assert strategy == "per-user"

    @pytest.mark.asyncio
    async def test_group_room_returns_per_room(self, conversation_service):
        """More than 2 members = per-room strategy."""
        strategy = await conversation_service.get_conversation_strategy(
            room_id="!group:matrix.test",
            room_member_count=5
        )
        assert strategy == "per-room"

    @pytest.mark.asyncio
    async def test_single_member_returns_per_room(self, conversation_service):
        """Edge case: 1 member (e.g., private notes room) = per-room."""
        strategy = await conversation_service.get_conversation_strategy(
            room_id="!notes:matrix.test",
            room_member_count=1
        )
        assert strategy == "per-room"

    @pytest.mark.asyncio
    async def test_three_members_returns_per_room(self, conversation_service):
        """3 members = per-room (not DM)."""
        strategy = await conversation_service.get_conversation_strategy(
            room_id="!small-group:matrix.test",
            room_member_count=3
        )
        assert strategy == "per-room"


class TestCreateLettaConversation:
    """Tests for _create_letta_conversation."""

    def test_creates_conversation_with_isolated_blocks(self, conversation_service, mock_letta_client):
        """Verify Letta API is called with correct parameters."""
        mock_conversation = MagicMock()
        mock_conversation.id = "letta-conv-123"
        mock_letta_client.conversations.create.return_value = mock_conversation

        result = conversation_service._create_letta_conversation(
            agent_id="agent-001",
            summary="Test summary"
        )

        assert result == "letta-conv-123"
        mock_letta_client.conversations.create.assert_called_once_with(
            agent_id="agent-001",
            isolated_block_labels=DEFAULT_ISOLATED_BLOCK_LABELS,
            summary="Test summary",
        )

    def test_raises_on_api_error(self, conversation_service, mock_letta_client):
        """Verify API errors propagate."""
        mock_letta_client.conversations.create.side_effect = make_api_error("Server error")

        with pytest.raises(APIError):
            conversation_service._create_letta_conversation("agent-001")


class TestVerifyLettaConversation:
    """Tests for _verify_letta_conversation."""

    def test_returns_true_when_conversation_exists(self, conversation_service, mock_letta_client):
        """Conversation exists = return True."""
        mock_letta_client.conversations.retrieve.return_value = MagicMock()
        
        result = conversation_service._verify_letta_conversation("conv-123")
        
        assert result is True
        mock_letta_client.conversations.retrieve.assert_called_once_with("conv-123")

    def test_returns_false_when_not_found(self, conversation_service, mock_letta_client):
        """Conversation not found = return False."""
        mock_letta_client.conversations.retrieve.side_effect = make_not_found_error()
        
        result = conversation_service._verify_letta_conversation("conv-deleted")
        
        assert result is False

    def test_raises_on_api_error(self, conversation_service, mock_letta_client):
        """API errors (not NotFoundError) should propagate."""
        mock_letta_client.conversations.retrieve.side_effect = make_api_error("Server error")
        
        with pytest.raises(APIError):
            conversation_service._verify_letta_conversation("conv-123")


class TestGetOrCreateRoomConversation:
    """Tests for get_or_create_room_conversation."""

    @pytest.mark.asyncio
    async def test_creates_new_conversation_per_room(self, conversation_service, mock_letta_client):
        """First call for room+agent creates new conversation."""
        mock_conversation = MagicMock()
        mock_conversation.id = "letta-conv-new"
        mock_letta_client.conversations.create.return_value = mock_conversation

        conv_id, created = await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5,
            room_name="Test Room"
        )

        assert conv_id == "letta-conv-new"
        assert created is True
        mock_letta_client.conversations.create.assert_called_once()
        
        # Verify summary includes room name
        call_kwargs = mock_letta_client.conversations.create.call_args.kwargs
        assert "Test Room" in call_kwargs["summary"]

    @pytest.mark.asyncio
    async def test_returns_existing_conversation(self, conversation_service, mock_letta_client):
        """Second call for same room+agent returns existing."""
        mock_conversation = MagicMock()
        mock_conversation.id = "letta-conv-existing"
        mock_letta_client.conversations.create.return_value = mock_conversation
        mock_letta_client.conversations.retrieve.return_value = MagicMock()

        # First call creates
        conv_id1, created1 = await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5
        )
        
        # Second call returns existing
        conv_id2, created2 = await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5
        )

        assert conv_id1 == conv_id2
        assert created1 is True
        assert created2 is False
        # create() only called once
        assert mock_letta_client.conversations.create.call_count == 1

    @pytest.mark.asyncio
    async def test_per_user_strategy_creates_separate_conversations(
        self, conversation_service, mock_letta_client
    ):
        """Per-user strategy (DM) creates separate conversations per user."""
        call_count = 0
        
        def create_conversation(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_conv = MagicMock()
            mock_conv.id = f"letta-conv-user-{call_count}"
            return mock_conv
        
        mock_letta_client.conversations.create.side_effect = create_conversation
        mock_letta_client.conversations.retrieve.return_value = MagicMock()

        # Alice's conversation
        alice_conv, alice_created = await conversation_service.get_or_create_room_conversation(
            room_id="!dm:matrix.test",
            agent_id="agent-001",
            room_member_count=2,  # DM = per-user
            user_mxid="@alice:matrix.test"
        )
        
        # Bob's conversation (same room, different user)
        bob_conv, bob_created = await conversation_service.get_or_create_room_conversation(
            room_id="!dm:matrix.test",
            agent_id="agent-001",
            room_member_count=2,
            user_mxid="@bob:matrix.test"
        )

        assert alice_conv != bob_conv  # Different conversations
        assert alice_created is True
        assert bob_created is True
        assert mock_letta_client.conversations.create.call_count == 2

    @pytest.mark.asyncio
    async def test_stale_conversation_recovery(self, conversation_service, mock_letta_client):
        """DB record exists but Letta conversation deleted = recreate."""
        # First create returns existing conversation
        mock_conv1 = MagicMock()
        mock_conv1.id = "letta-conv-stale"
        mock_conv2 = MagicMock()
        mock_conv2.id = "letta-conv-new"
        mock_letta_client.conversations.create.side_effect = [mock_conv1, mock_conv2]
        
        # First call: create conversation
        await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5
        )
        
        mock_letta_client.conversations.retrieve.side_effect = make_not_found_error("Deleted")
        
        # Second call should detect stale and recreate
        conv_id, created = await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5
        )

        assert conv_id == "letta-conv-new"
        assert created is True
        assert mock_letta_client.conversations.create.call_count == 2

    @pytest.mark.asyncio
    async def test_race_condition_handling(
        self, conversation_service, mock_letta_client, conversation_engine, monkeypatch
    ):
        """Race condition: DB insert fails, fetches winner's record."""
        mock_conv = MagicMock()
        mock_conv.id = "letta-conv-race"
        mock_letta_client.conversations.create.return_value = mock_conv
        mock_letta_client.conversations.retrieve.return_value = MagicMock()
        
        # First, manually insert a record to simulate race condition
        conversation_service.room_conv_db.create(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            conversation_id="letta-conv-winner"
        )
        
        # Now make the service's create call fail with IntegrityError
        original_create = conversation_service.room_conv_db.create
        
        def create_with_race(*args, **kwargs):
            raise IntegrityError("duplicate", None, Exception("duplicate key"))
        
        monkeypatch.setattr(conversation_service.room_conv_db, 'create', create_with_race)
        
        # Should handle race condition and return existing
        conv_id, created = await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5
        )
        
        assert conv_id == "letta-conv-winner"
        assert created is False

    @pytest.mark.asyncio
    async def test_letta_api_failure_propagates(self, conversation_service, mock_letta_client):
        """Letta API failure should propagate to caller."""
        mock_letta_client.conversations.create.side_effect = make_api_error("Server down")

        with pytest.raises(APIError):
            await conversation_service.get_or_create_room_conversation(
                room_id="!room1:matrix.test",
                agent_id="agent-001",
                room_member_count=5
            )


class TestGetOrCreateInterAgentConversation:
    """Tests for get_or_create_inter_agent_conversation."""

    @pytest.mark.asyncio
    async def test_creates_new_inter_agent_conversation(
        self, conversation_service, mock_letta_client
    ):
        """First @mention creates new inter-agent conversation."""
        mock_conv = MagicMock()
        mock_conv.id = "letta-inter-conv-new"
        mock_letta_client.conversations.create.return_value = mock_conv

        conv_id, created = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test"
        )

        assert conv_id == "letta-inter-conv-new"
        assert created is True
        
        # Verify conversation created for TARGET agent
        call_kwargs = mock_letta_client.conversations.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "agent-002"
        assert "agent-001 -> agent-002" in call_kwargs["summary"]

    @pytest.mark.asyncio
    async def test_returns_existing_inter_agent_conversation(
        self, conversation_service, mock_letta_client
    ):
        """Subsequent @mentions return existing conversation."""
        mock_conv = MagicMock()
        mock_conv.id = "letta-inter-conv-existing"
        mock_letta_client.conversations.create.return_value = mock_conv
        mock_letta_client.conversations.retrieve.return_value = MagicMock()

        # First call
        await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test"
        )
        
        # Second call returns existing
        conv_id, created = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test"
        )

        assert created is False
        assert mock_letta_client.conversations.create.call_count == 1

    @pytest.mark.asyncio
    async def test_direction_matters(self, conversation_service, mock_letta_client):
        """A->B and B->A are different conversations."""
        call_count = 0
        
        def create_conversation(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_conv = MagicMock()
            mock_conv.id = f"letta-inter-conv-{call_count}"
            return mock_conv
        
        mock_letta_client.conversations.create.side_effect = create_conversation
        mock_letta_client.conversations.retrieve.return_value = MagicMock()

        conv_ab, _ = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test"
        )
        
        conv_ba, _ = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-002",
            target_agent_id="agent-001",
            room_id="!room1:matrix.test"
        )

        assert conv_ab != conv_ba
        assert mock_letta_client.conversations.create.call_count == 2

    @pytest.mark.asyncio
    async def test_user_mxid_isolation(self, conversation_service, mock_letta_client):
        """Same agents in same room but different triggering users = different conversations."""
        call_count = 0
        
        def create_conversation(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_conv = MagicMock()
            mock_conv.id = f"letta-inter-conv-user-{call_count}"
            return mock_conv
        
        mock_letta_client.conversations.create.side_effect = create_conversation
        mock_letta_client.conversations.retrieve.return_value = MagicMock()

        # Triggered by Alice
        conv_alice, _ = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            user_mxid="@alice:matrix.test"
        )
        
        # Triggered by Bob
        conv_bob, _ = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test",
            user_mxid="@bob:matrix.test"
        )

        assert conv_alice != conv_bob

    @pytest.mark.asyncio
    async def test_stale_inter_agent_recovery(self, conversation_service, mock_letta_client):
        """Stale inter-agent conversation is recreated."""
        mock_conv1 = MagicMock()
        mock_conv1.id = "letta-inter-stale"
        mock_conv2 = MagicMock()
        mock_conv2.id = "letta-inter-new"
        mock_letta_client.conversations.create.side_effect = [mock_conv1, mock_conv2]

        # First call creates
        await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test"
        )
        
        # Simulate Letta deletion
        mock_letta_client.conversations.retrieve.side_effect = make_not_found_error("Gone")
        
        # Second call recreates
        conv_id, created = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-001",
            target_agent_id="agent-002",
            room_id="!room1:matrix.test"
        )

        assert conv_id == "letta-inter-new"
        assert created is True


class TestGetConversationId:
    """Tests for get_conversation_id (read-only lookup)."""

    @pytest.mark.asyncio
    async def test_returns_existing_conversation_id(
        self, conversation_service, mock_letta_client
    ):
        """Returns conversation ID when mapping exists."""
        mock_conv = MagicMock()
        mock_conv.id = "letta-conv-lookup"
        mock_letta_client.conversations.create.return_value = mock_conv

        await conversation_service.get_or_create_room_conversation(
            room_id="!room1:matrix.test",
            agent_id="agent-001",
            room_member_count=5
        )

        result = conversation_service.get_conversation_id(
            room_id="!room1:matrix.test",
            agent_id="agent-001"
        )

        assert result == "letta-conv-lookup"

    def test_returns_none_when_not_found(self, conversation_service):
        """Returns None when no mapping exists."""
        result = conversation_service.get_conversation_id(
            room_id="!nonexistent:matrix.test",
            agent_id="agent-999"
        )

        assert result is None


class TestCleanupStaleConversations:
    """Tests for cleanup_stale_conversations."""

    def test_delegates_to_db_helpers(self, conversation_service, monkeypatch):
        """Cleanup calls both DB helper delete_stale methods."""
        monkeypatch.setattr(conversation_service.room_conv_db, 'delete_stale', lambda days: 5)
        monkeypatch.setattr(conversation_service.inter_agent_db, 'delete_stale', lambda days: 3)

        room_deleted, inter_deleted = conversation_service.cleanup_stale_conversations(days=30)

        assert room_deleted == 5
        assert inter_deleted == 3


class TestServiceSingleton:
    """Tests for service singleton management."""

    def test_get_conversation_service_returns_singleton(
        self, conversation_engine, mock_letta_client, monkeypatch
    ):
        """get_conversation_service returns same instance."""
        import src.models.agent_mapping
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
        
        reset_conversation_service()
        
        # Patch get_letta_client to return our mock
        with patch('src.core.conversation_service.get_letta_client', return_value=mock_letta_client):
            service1 = get_conversation_service()
            service2 = get_conversation_service()
        
        assert service1 is service2
        reset_conversation_service()

    def test_reset_clears_singleton(
        self, conversation_engine, mock_letta_client, monkeypatch
    ):
        """reset_conversation_service clears the singleton."""
        import src.models.agent_mapping
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
        
        with patch('src.core.conversation_service.get_letta_client', return_value=mock_letta_client):
            service1 = get_conversation_service()
            reset_conversation_service()
            service2 = get_conversation_service()
        
        assert service1 is not service2
        reset_conversation_service()

    def test_explicit_client_overrides_singleton(
        self, conversation_engine, mock_letta_client, monkeypatch
    ):
        """Passing explicit client creates new instance."""
        import src.models.agent_mapping
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
        
        reset_conversation_service()
        
        with patch('src.core.conversation_service.get_letta_client', return_value=mock_letta_client):
            service1 = get_conversation_service()
        
        another_client = MagicMock()
        service2 = get_conversation_service(letta_client=another_client)
        
        assert service1 is not service2
        assert service2.letta is another_client
        reset_conversation_service()


class TestCustomIsolatedBlocks:
    """Tests for custom isolated_block_labels."""

    def test_uses_default_isolated_blocks(self, conversation_service, mock_letta_client):
        """Default isolated blocks are used."""
        mock_conv = MagicMock()
        mock_conv.id = "conv-123"
        mock_letta_client.conversations.create.return_value = mock_conv

        conversation_service._create_letta_conversation("agent-001")

        call_kwargs = mock_letta_client.conversations.create.call_args.kwargs
        assert call_kwargs["isolated_block_labels"] == DEFAULT_ISOLATED_BLOCK_LABELS

    def test_custom_isolated_blocks(self, conversation_engine, mock_letta_client, monkeypatch):
        """Custom isolated blocks can be specified."""
        import src.models.agent_mapping
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
        
        custom_blocks = ["custom_block_1", "custom_block_2"]
        service = ConversationService(
            letta_client=mock_letta_client,
            isolated_block_labels=custom_blocks
        )
        
        mock_conv = MagicMock()
        mock_conv.id = "conv-custom"
        mock_letta_client.conversations.create.return_value = mock_conv

        service._create_letta_conversation("agent-001")

        call_kwargs = mock_letta_client.conversations.create.call_args.kwargs
        assert call_kwargs["isolated_block_labels"] == custom_blocks
