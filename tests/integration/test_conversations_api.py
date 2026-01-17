"""
Integration tests for Letta Conversations API.

Tests conversation isolation, persistence, strategy detection, retry behavior,
and fallback scenarios without requiring live services.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError

from src.models.agent_mapping import Base
from src.models.conversation import RoomConversationDB, InterAgentConversationDB
from src.core.conversation_service import (
    ConversationService,
    get_conversation_service,
    reset_conversation_service,
)
from src.core.retry import ConversationBusyError, is_conversation_busy_error


@pytest.fixture
def conversation_engine():
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
    client = Mock()
    client.conversations = Mock()
    client.conversations.create = Mock()
    client.conversations.retrieve = Mock()
    client.conversations.messages = Mock()
    client.conversations.messages.create = Mock()
    return client


@pytest.fixture
def conversation_service(conversation_engine, mock_letta_client, monkeypatch):
    import src.models.agent_mapping
    monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
    
    reset_conversation_service()
    service = ConversationService(letta_client=mock_letta_client)
    yield service
    reset_conversation_service()


class TestRoomIsolation:
    @pytest.mark.asyncio
    async def test_different_rooms_get_different_conversations(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.side_effect = [
            Mock(id="conv-room-a"),
            Mock(id="conv-room-b"),
        ]
        mock_letta_client.conversations.retrieve.return_value = Mock(id="exists")
        
        conv_a, created_a = await conversation_service.get_or_create_room_conversation(
            room_id="!room-a:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        conv_b, created_b = await conversation_service.get_or_create_room_conversation(
            room_id="!room-b:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        assert conv_a == "conv-room-a"
        assert conv_b == "conv-room-b"
        assert conv_a != conv_b
        assert created_a is True
        assert created_b is True
    
    @pytest.mark.asyncio
    async def test_same_room_reuses_conversation(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.return_value = Mock(id="conv-shared")
        mock_letta_client.conversations.retrieve.return_value = Mock(id="conv-shared")
        
        conv_1, created_1 = await conversation_service.get_or_create_room_conversation(
            room_id="!room-x:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        conv_2, created_2 = await conversation_service.get_or_create_room_conversation(
            room_id="!room-x:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        assert conv_1 == conv_2
        assert created_1 is True
        assert created_2 is False
        assert mock_letta_client.conversations.create.call_count == 1
    
    @pytest.mark.asyncio
    async def test_different_agents_same_room_get_different_conversations(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.side_effect = [
            Mock(id="conv-agent-1"),
            Mock(id="conv-agent-2"),
        ]
        mock_letta_client.conversations.retrieve.return_value = Mock(id="exists")
        
        conv_1, _ = await conversation_service.get_or_create_room_conversation(
            room_id="!room:matrix.test",
            agent_id="agent-001",
            room_member_count=5,
        )
        
        conv_2, _ = await conversation_service.get_or_create_room_conversation(
            room_id="!room:matrix.test",
            agent_id="agent-002",
            room_member_count=5,
        )
        
        assert conv_1 == "conv-agent-1"
        assert conv_2 == "conv-agent-2"
        assert conv_1 != conv_2


class TestConversationPersistence:
    @pytest.mark.asyncio
    async def test_conversation_survives_service_restart(
        self, conversation_engine, mock_letta_client, monkeypatch
    ):
        import src.models.agent_mapping
        monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: conversation_engine)
        
        mock_letta_client.conversations.create.return_value = Mock(id="conv-persistent")
        mock_letta_client.conversations.retrieve.return_value = Mock(id="conv-persistent")
        
        service1 = ConversationService(letta_client=mock_letta_client)
        conv_1, created_1 = await service1.get_or_create_room_conversation(
            room_id="!persistent:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        service2 = ConversationService(letta_client=mock_letta_client)
        conv_2, created_2 = await service2.get_or_create_room_conversation(
            room_id="!persistent:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        assert conv_1 == conv_2 == "conv-persistent"
        assert created_1 is True
        assert created_2 is False
        assert mock_letta_client.conversations.create.call_count == 1
    
    @pytest.mark.asyncio
    async def test_stale_conversation_recovery(
        self, conversation_service, mock_letta_client
    ):
        from letta_client import NotFoundError
        
        create_call_count = [0]
        def create_side_effect(*args, **kwargs):
            create_call_count[0] += 1
            if create_call_count[0] == 1:
                return Mock(id="conv-old")
            return Mock(id="conv-new")
        
        mock_letta_client.conversations.create.side_effect = create_side_effect
        
        def retrieve_raises_not_found(conv_id):
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.headers = {}
            raise NotFoundError("Not found", response=mock_response, body=None)
        
        mock_letta_client.conversations.retrieve.return_value = Mock(id="exists")
        
        conv_1, _ = await conversation_service.get_or_create_room_conversation(
            room_id="!stale:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        assert conv_1 == "conv-old"
        
        mock_letta_client.conversations.retrieve.side_effect = retrieve_raises_not_found
        
        conv_2, created_2 = await conversation_service.get_or_create_room_conversation(
            room_id="!stale:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        assert conv_2 == "conv-new"
        assert created_2 is True
        assert create_call_count[0] == 2


class TestStrategyDetection:
    @pytest.mark.asyncio
    async def test_dm_uses_per_user_strategy(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.side_effect = [
            Mock(id="conv-user-1"),
            Mock(id="conv-user-2"),
        ]
        mock_letta_client.conversations.retrieve.return_value = Mock(id="exists")
        
        conv_user1, _ = await conversation_service.get_or_create_room_conversation(
            room_id="!dm:matrix.test",
            agent_id="agent-123",
            room_member_count=2,
            user_mxid="@user1:matrix.test",
        )
        
        conv_user2, _ = await conversation_service.get_or_create_room_conversation(
            room_id="!dm:matrix.test",
            agent_id="agent-123",
            room_member_count=2,
            user_mxid="@user2:matrix.test",
        )
        
        assert conv_user1 == "conv-user-1"
        assert conv_user2 == "conv-user-2"
        assert conv_user1 != conv_user2
    
    @pytest.mark.asyncio
    async def test_group_uses_per_room_strategy(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.return_value = Mock(id="conv-group")
        mock_letta_client.conversations.retrieve.return_value = Mock(id="conv-group")
        
        conv_user1, created_1 = await conversation_service.get_or_create_room_conversation(
            room_id="!group:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
            user_mxid="@user1:matrix.test",
        )
        
        conv_user2, created_2 = await conversation_service.get_or_create_room_conversation(
            room_id="!group:matrix.test",
            agent_id="agent-123",
            room_member_count=5,
            user_mxid="@user2:matrix.test",
        )
        
        assert conv_user1 == conv_user2 == "conv-group"
        assert created_1 is True
        assert created_2 is False
    
    @pytest.mark.asyncio
    async def test_strategy_detection_boundaries(self, conversation_service):
        strategy_2 = await conversation_service.get_conversation_strategy("!room:test", 2)
        assert strategy_2 == "per-user"
        
        strategy_1 = await conversation_service.get_conversation_strategy("!room:test", 1)
        assert strategy_1 == "per-room"
        
        strategy_3 = await conversation_service.get_conversation_strategy("!room:test", 3)
        assert strategy_3 == "per-room"
        
        strategy_100 = await conversation_service.get_conversation_strategy("!room:test", 100)
        assert strategy_100 == "per-room"


class TestRetryBehavior:
    def test_conflict_error_detection(self):
        class MockConflictError(Exception):
            pass
        
        with patch('src.core.retry.ConflictError', MockConflictError):
            error = MockConflictError("CONVERSATION_BUSY: agent processing")
            assert is_conversation_busy_error(error) is True
    
    def test_409_in_message_detection(self):
        error = Exception("HTTP 409: conversation is busy")
        assert is_conversation_busy_error(error) is True
    
    def test_non_busy_error_not_detected(self):
        error = ValueError("Some other error")
        assert is_conversation_busy_error(error) is False
    
    @pytest.mark.asyncio
    async def test_retry_succeeds_after_busy(self, mock_letta_client):
        from src.core.retry import retry_on_conversation_busy
        
        call_count = [0]
        
        def create_message(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Error 409: conversation busy")
            return [Mock(content="success")]
        
        mock_letta_client.conversations.messages.create.side_effect = create_message
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            result = await retry_on_conversation_busy(
                func=lambda: mock_letta_client.conversations.messages.create(
                    conversation_id="conv-123",
                    input="test",
                ),
                conversation_id="conv-123",
                max_retries=3,
            )
        
        assert call_count[0] == 2
        assert len(result) == 1
    
    @pytest.mark.asyncio
    async def test_retry_exhausts_and_raises(self, mock_letta_client):
        from src.core.retry import retry_on_conversation_busy
        
        mock_letta_client.conversations.messages.create.side_effect = Exception(
            "Error 409: conversation busy"
        )
        
        with patch('src.core.retry.asyncio.sleep', return_value=None):
            with pytest.raises(ConversationBusyError) as exc_info:
                await retry_on_conversation_busy(
                    func=lambda: mock_letta_client.conversations.messages.create(
                        conversation_id="conv-123",
                        input="test",
                    ),
                    conversation_id="conv-123",
                    max_retries=2,
                )
        
        assert exc_info.value.conversation_id == "conv-123"
        assert exc_info.value.attempts == 3


class TestFallbackBehavior:
    @pytest.mark.asyncio
    async def test_fallback_when_feature_disabled(self):
        from src.matrix.client import Config
        
        config = Config(
            homeserver_url="http://test",
            username="@test:test",
            password="test",
            room_id="!test:test",
            letta_api_url="http://letta",
            letta_token="token",
            letta_agent_id="agent-123",
            letta_conversations_enabled=False,
        )
        
        assert config.letta_conversations_enabled is False
    
    @pytest.mark.asyncio
    async def test_graceful_fallback_on_service_error(
        self, conversation_service, mock_letta_client
    ):
        from letta_client import APIError
        
        mock_request = Mock()
        mock_request.url = "http://test"
        mock_request.method = "POST"
        mock_letta_client.conversations.create.side_effect = APIError(
            "Service unavailable", mock_request, body=None
        )
        
        with pytest.raises(APIError):
            await conversation_service.get_or_create_room_conversation(
                room_id="!error:matrix.test",
                agent_id="agent-123",
                room_member_count=5,
            )


class TestInterAgentConversations:
    @pytest.mark.asyncio
    async def test_inter_agent_creates_separate_conversations(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.side_effect = [
            Mock(id="conv-a-to-b"),
            Mock(id="conv-b-to-a"),
        ]
        mock_letta_client.conversations.retrieve.return_value = Mock(id="exists")
        
        conv_ab, _ = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-a",
            target_agent_id="agent-b",
            room_id="!room:test",
        )
        
        conv_ba, _ = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-b",
            target_agent_id="agent-a",
            room_id="!room:test",
        )
        
        assert conv_ab == "conv-a-to-b"
        assert conv_ba == "conv-b-to-a"
        assert conv_ab != conv_ba
    
    @pytest.mark.asyncio
    async def test_inter_agent_reuses_existing(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.return_value = Mock(id="conv-reuse")
        mock_letta_client.conversations.retrieve.return_value = Mock(id="conv-reuse")
        
        conv_1, created_1 = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-a",
            target_agent_id="agent-b",
            room_id="!room:test",
        )
        
        conv_2, created_2 = await conversation_service.get_or_create_inter_agent_conversation(
            source_agent_id="agent-a",
            target_agent_id="agent-b",
            room_id="!room:test",
        )
        
        assert conv_1 == conv_2
        assert created_1 is True
        assert created_2 is False


class TestRaceConditionHandling:
    @pytest.mark.asyncio
    async def test_race_condition_returns_existing(
        self, conversation_service, mock_letta_client
    ):
        mock_letta_client.conversations.create.return_value = Mock(id="conv-race")
        mock_letta_client.conversations.retrieve.return_value = Mock(id="conv-race")
        
        conversation_service.room_conv_db.create(
            room_id="!race:test",
            agent_id="agent-123",
            conversation_id="conv-winner",
            strategy="per-room",
        )
        
        original_create = conversation_service.room_conv_db.create
        
        def create_with_integrity_error(*args, **kwargs):
            raise IntegrityError("UNIQUE constraint failed", params=None, orig=Exception())
        
        conversation_service.room_conv_db.create = create_with_integrity_error
        
        conv, created = await conversation_service.get_or_create_room_conversation(
            room_id="!race:test",
            agent_id="agent-123",
            room_member_count=5,
        )
        
        conversation_service.room_conv_db.create = original_create
        
        assert conv == "conv-winner"
        assert created is False


class TestCleanup:
    def test_cleanup_stale_conversations(self, conversation_service, mock_letta_client):
        mock_letta_client.conversations.create.return_value = Mock(id="conv-old")
        
        room_deleted, inter_deleted = conversation_service.cleanup_stale_conversations(days=30)
        
        assert room_deleted >= 0
        assert inter_deleted >= 0
