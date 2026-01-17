"""
Unit tests for @mention-based agent routing.

These tests verify the mention extraction, forwarding, and routing logic
for inter-agent communication via @mentions.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from typing import List, Tuple, Optional


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def sample_agent_mappings():
    """Sample agent mappings for testing"""
    return {
        "agent-597b5756-2915-4560-ba6b-91005f085166": {
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
            "agent_name": "Meridian",
            "matrix_user_id": "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca",
            "room_id": "!O8cbkBGCMB8Ujlaret:matrix.oculair.ca",
            "room_created": True,
        },
        "agent-870d3dfb-319f-4c52-91f1-72ab46d944a7": {
            "agent_id": "agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
            "agent_name": "Huly - Matrix Synapse Deployment",
            "matrix_user_id": "@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca",
            "room_id": "!MtuqLUCwvKypRU6gll:matrix.oculair.ca",
            "room_created": True,
        },
        "agent-bmo-1234-5678": {
            "agent_id": "agent-bmo-1234-5678",
            "agent_name": "BMO",
            "matrix_user_id": "@agent_bmo_1234_5678:matrix.oculair.ca",
            "room_id": "!BMOroom:matrix.oculair.ca",
            "room_created": True,
        },
    }


@pytest.fixture
def mock_mapping_service(sample_agent_mappings):
    """Mock the mapping service functions"""
    with patch('src.matrix.mention_routing.get_mapping_by_agent_id') as mock_by_id, \
         patch('src.matrix.mention_routing.get_mapping_by_matrix_user') as mock_by_user, \
         patch('src.matrix.mention_routing.get_mapping_by_agent_name') as mock_by_name, \
         patch('src.matrix.mention_routing.get_all_mappings') as mock_all:
        
        def by_id(agent_id):
            return sample_agent_mappings.get(agent_id)
        
        def by_user(mxid):
            for mapping in sample_agent_mappings.values():
                if mapping["matrix_user_id"] == mxid:
                    return mapping
            return None
        
        def by_name(name, fuzzy=True):
            name_lower = name.lower()
            for mapping in sample_agent_mappings.values():
                agent_name = mapping["agent_name"].lower()
                if fuzzy:
                    # Check exact match or if search term is in agent name
                    if name_lower == agent_name or name_lower in agent_name:
                        return mapping
                    # Handle "Huly - " prefix
                    if agent_name.startswith("huly - ") and name_lower in agent_name[7:]:
                        return mapping
                else:
                    if name_lower == agent_name:
                        return mapping
            return None
        
        mock_by_id.side_effect = by_id
        mock_by_user.side_effect = by_user
        mock_by_name.side_effect = by_name
        mock_all.return_value = sample_agent_mappings
        
        yield {
            "by_id": mock_by_id,
            "by_user": mock_by_user,
            "by_name": mock_by_name,
            "all": mock_all,
        }


@pytest.fixture
def mock_config():
    """Mock configuration object"""
    config = Mock()
    config.homeserver_url = "http://matrix.oculair.ca:6167"
    config.letta_api_url = "http://localhost:8283"
    config.letta_streaming_enabled = True
    return config


@pytest.fixture
def mock_logger():
    """Mock logger"""
    return Mock()


# =============================================================================
# Tests for extract_agent_mentions()
# =============================================================================

class TestExtractAgentMentions:
    """Tests for the extract_agent_mentions() function"""
    
    def test_extract_mxid_mention(self, mock_mapping_service):
        """Test extracting a full Matrix user ID mention"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "Hello @agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca how are you?"
        mentions = extract_agent_mentions(body)
        
        assert len(mentions) == 1
        matched_text, agent_id, agent_name = mentions[0]
        assert agent_id == "agent-597b5756-2915-4560-ba6b-91005f085166"
        assert agent_name == "Meridian"
    
    def test_extract_friendly_name_mention(self, mock_mapping_service):
        """Test extracting a friendly name mention like @Meridian"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "Hey @Meridian can you help with this?"
        mentions = extract_agent_mentions(body)
        
        assert len(mentions) == 1
        matched_text, agent_id, agent_name = mentions[0]
        assert agent_id == "agent-597b5756-2915-4560-ba6b-91005f085166"
        assert agent_name == "Meridian"
    
    def test_extract_friendly_name_case_insensitive(self, mock_mapping_service):
        """Test that friendly name matching is case-insensitive"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "Hey @meridian and @MERIDIAN please help"
        mentions = extract_agent_mentions(body)
        
        # Should deduplicate to single mention
        assert len(mentions) == 1
        assert mentions[0][2] == "Meridian"
    
    def test_extract_multiple_mentions(self, mock_mapping_service):
        """Test extracting multiple different agent mentions"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "@Meridian and @BMO please collaborate on this task"
        mentions = extract_agent_mentions(body)
        
        assert len(mentions) == 2
        agent_names = {m[2] for m in mentions}
        assert "Meridian" in agent_names
        assert "BMO" in agent_names
    
    def test_extract_no_mentions(self, mock_mapping_service):
        """Test that empty list is returned when no mentions present"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "Just a regular message without any mentions"
        mentions = extract_agent_mentions(body)
        
        assert len(mentions) == 0
    
    def test_extract_strips_reply_fallback(self, mock_mapping_service):
        """Test that Matrix reply quoted content is stripped before parsing"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        # Matrix reply format - @Meridian in quote should be ignored
        body = """> <@user:matrix.org> @Meridian mentioned in old message
> this is quoted content

@BMO this is the new message"""
        
        mentions = extract_agent_mentions(body)
        
        # Should only find @BMO from new message, not @Meridian from quote
        assert len(mentions) == 1
        assert mentions[0][2] == "BMO"
    
    def test_extract_unknown_agent_filtered(self, mock_mapping_service):
        """Test that unknown agent names are filtered out"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "@NonExistentAgent please help @Meridian"
        mentions = extract_agent_mentions(body)
        
        # Only Meridian should be found
        assert len(mentions) == 1
        assert mentions[0][2] == "Meridian"
    
    def test_extract_huly_prefix_handling(self, mock_mapping_service):
        """Test matching agents with 'Huly - ' prefix by partial name"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "@Matrix Synapse Deployment please check this"
        mentions = extract_agent_mentions(body)
        
        # Should match "Huly - Matrix Synapse Deployment"
        assert len(mentions) == 1
        assert mentions[0][2] == "Huly - Matrix Synapse Deployment"
    
    def test_extract_mixed_mxid_and_name(self, mock_mapping_service):
        """Test extracting both MXID and friendly name mentions"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca and @BMO"
        mentions = extract_agent_mentions(body)
        
        assert len(mentions) == 2
    
    def test_extract_email_not_matched(self, mock_mapping_service):
        """Test that email addresses are not matched as mentions"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "Contact me at agent@example.com for more info"
        mentions = extract_agent_mentions(body)
        
        assert len(mentions) == 0
    
    def test_extract_partial_mxid_not_matched(self, mock_mapping_service):
        """Test that incomplete MXIDs without domain are not matched"""
        from src.matrix.mention_routing import extract_agent_mentions
        
        body = "@agent_partial without domain"
        mentions = extract_agent_mentions(body)
        
        # Should not match - no domain part
        assert len(mentions) == 0


# =============================================================================
# Tests for strip_reply_fallback()
# =============================================================================

class TestStripReplyFallback:
    """Tests for the strip_reply_fallback() helper function"""
    
    def test_strip_simple_reply(self):
        """Test stripping a simple reply fallback"""
        from src.matrix.mention_routing import strip_reply_fallback
        
        body = """> <@user:matrix.org> original message

New message here"""
        
        result = strip_reply_fallback(body)
        assert result == "New message here"
    
    def test_strip_multiline_quote(self):
        """Test stripping multi-line quoted content"""
        from src.matrix.mention_routing import strip_reply_fallback
        
        body = """> <@user:matrix.org> first line of quote
> second line of quote
> third line

Actual new message"""
        
        result = strip_reply_fallback(body)
        assert result == "Actual new message"
    
    def test_no_quote_unchanged(self):
        """Test that messages without quotes are unchanged"""
        from src.matrix.mention_routing import strip_reply_fallback
        
        body = "Just a normal message without any quotes"
        result = strip_reply_fallback(body)
        
        assert result == body
    
    def test_preserve_newlines_in_new_message(self):
        """Test that newlines in new message are preserved"""
        from src.matrix.mention_routing import strip_reply_fallback
        
        body = """> <@user:matrix.org> quoted

Line 1
Line 2
Line 3"""
        
        result = strip_reply_fallback(body)
        assert result == "Line 1\nLine 2\nLine 3"


# =============================================================================
# Tests for forward_to_agent_room()
# =============================================================================

class TestForwardToAgentRoom:
    """Tests for the forward_to_agent_room() function"""
    
    @pytest.mark.asyncio
    async def test_forward_success(self, mock_config, mock_logger, mock_mapping_service):
        """Test successful message forwarding"""
        from src.matrix.mention_routing import forward_to_agent_room
        
        with patch('src.matrix.mention_routing.get_client_for_identity') as mock_get_client:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=Mock(event_id="$forwarded123"))
            mock_get_client.return_value = mock_client
            
            event_id = await forward_to_agent_room(
                source_room_id="!source:matrix.oculair.ca",
                target_room_id="!O8cbkBGCMB8Ujlaret:matrix.oculair.ca",
                message="Hello @Meridian",
                sender_mxid="@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                target_agent_name="Meridian",
                original_event_id="$original456",
                config=mock_config,
                logger=mock_logger,
            )
            
            assert event_id == "$forwarded123"
            mock_client.room_send.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_forward_sets_forwarded_flag(self, mock_config, mock_logger, mock_mapping_service):
        """Test that forwarded messages have m.forwarded flag"""
        from src.matrix.mention_routing import forward_to_agent_room
        
        with patch('src.matrix.mention_routing.get_client_for_identity') as mock_get_client:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=Mock(event_id="$fwd"))
            mock_get_client.return_value = mock_client
            
            await forward_to_agent_room(
                source_room_id="!source:test",
                target_room_id="!target:test",
                message="Test message",
                sender_mxid="@sender:test",
                sender_agent_name="Sender",
                target_agent_name="Target",
                original_event_id="$orig",
                config=mock_config,
                logger=mock_logger,
            )
            
            # Check the content passed to room_send
            call_args = mock_client.room_send.call_args
            content = call_args[1]["content"] if "content" in call_args[1] else call_args[0][2]
            
            assert content.get("m.forwarded") is True
            assert "m.forward_source" in content
    
    @pytest.mark.asyncio
    async def test_forward_includes_source_reference(self, mock_config, mock_logger, mock_mapping_service):
        """Test that forwarded messages include source room and event reference"""
        from src.matrix.mention_routing import forward_to_agent_room
        
        with patch('src.matrix.mention_routing.get_client_for_identity') as mock_get_client:
            mock_client = AsyncMock()
            mock_client.room_send = AsyncMock(return_value=Mock(event_id="$fwd"))
            mock_get_client.return_value = mock_client
            
            await forward_to_agent_room(
                source_room_id="!source:test",
                target_room_id="!target:test",
                message="Test message",
                sender_mxid="@sender:test",
                sender_agent_name="Sender",
                target_agent_name="Target",
                original_event_id="$original123",
                config=mock_config,
                logger=mock_logger,
            )
            
            call_args = mock_client.room_send.call_args
            content = call_args[1]["content"] if "content" in call_args[1] else call_args[0][2]
            
            forward_source = content.get("m.forward_source", {})
            assert forward_source.get("room_id") == "!source:test"
            assert forward_source.get("event_id") == "$original123"
            assert forward_source.get("sender") == "@sender:test"
    
    @pytest.mark.asyncio
    async def test_forward_returns_none_on_error(self, mock_config, mock_logger, mock_mapping_service):
        """Test that None is returned when forwarding fails"""
        from src.matrix.mention_routing import forward_to_agent_room
        
        with patch('src.matrix.mention_routing.get_client_for_identity') as mock_get_client:
            mock_get_client.side_effect = Exception("Connection failed")
            
            event_id = await forward_to_agent_room(
                source_room_id="!source:test",
                target_room_id="!target:test",
                message="Test message",
                sender_mxid="@sender:test",
                sender_agent_name="Sender",
                target_agent_name="Target",
                original_event_id="$orig",
                config=mock_config,
                logger=mock_logger,
            )
            
            assert event_id is None
            mock_logger.error.assert_called()


# =============================================================================
# Tests for handle_agent_mention_routing()
# =============================================================================

class TestHandleAgentMentionRouting:
    """Tests for the handle_agent_mention_routing() integration function"""
    
    @pytest.fixture
    def mock_room(self):
        """Create a mock Matrix room"""
        room = Mock()
        room.room_id = "!MtuqLUCwvKypRU6gll:matrix.oculair.ca"
        room.display_name = "Huly - Matrix Synapse Deployment"
        return room
    
    @pytest.fixture
    def mock_event(self):
        """Create a mock Matrix event"""
        event = Mock()
        event.body = "@Meridian please help with this task"
        event.sender = "@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca"
        event.event_id = "$original123"
        event.source = {"content": {}}
        return event
    
    @pytest.mark.asyncio
    async def test_routes_mention_to_target_room(
        self, mock_room, mock_event, mock_config, mock_logger, mock_mapping_service
    ):
        """Test that @mention triggers forwarding to target agent room"""
        from src.matrix.mention_routing import handle_agent_mention_routing
        
        with patch('src.matrix.mention_routing.forward_to_agent_room') as mock_forward:
            mock_forward.return_value = "$forwarded123"
            
            await handle_agent_mention_routing(
                room=mock_room,
                event=mock_event,
                sender_mxid=mock_event.sender,
                sender_agent_id="agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                config=mock_config,
                logger=mock_logger,
            )
            
            mock_forward.assert_called_once()
            call_kwargs = mock_forward.call_args[1]
            assert call_kwargs["target_room_id"] == "!O8cbkBGCMB8Ujlaret:matrix.oculair.ca"
            assert call_kwargs["target_agent_name"] == "Meridian"
    
    @pytest.mark.asyncio
    async def test_skips_self_mention(
        self, mock_room, mock_config, mock_logger, mock_mapping_service
    ):
        """Test that agent mentioning itself doesn't trigger forward"""
        from src.matrix.mention_routing import handle_agent_mention_routing
        
        # Agent mentions itself
        mock_event = Mock()
        mock_event.body = "@Huly - Matrix Synapse Deployment check status"
        mock_event.sender = "@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca"
        mock_event.event_id = "$self123"
        mock_event.source = {"content": {}}
        
        with patch('src.matrix.mention_routing.forward_to_agent_room') as mock_forward:
            await handle_agent_mention_routing(
                room=mock_room,
                event=mock_event,
                sender_mxid=mock_event.sender,
                sender_agent_id="agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                config=mock_config,
                logger=mock_logger,
            )
            
            # Should not forward to self
            mock_forward.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_skips_forwarded_message(
        self, mock_room, mock_config, mock_logger, mock_mapping_service
    ):
        """Test that already-forwarded messages are not re-forwarded (loop prevention)"""
        from src.matrix.mention_routing import handle_agent_mention_routing
        
        # Message already has m.forwarded flag
        mock_event = Mock()
        mock_event.body = "@Meridian this is a forwarded message"
        mock_event.sender = "@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca"
        mock_event.event_id = "$fwd123"
        mock_event.source = {"content": {"m.forwarded": True}}
        
        with patch('src.matrix.mention_routing.forward_to_agent_room') as mock_forward:
            await handle_agent_mention_routing(
                room=mock_room,
                event=mock_event,
                sender_mxid=mock_event.sender,
                sender_agent_id="agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                config=mock_config,
                logger=mock_logger,
            )
            
            # Should not forward already-forwarded messages
            mock_forward.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_forwards_to_multiple_agents(
        self, mock_room, mock_config, mock_logger, mock_mapping_service
    ):
        """Test that multiple @mentions forward to all target rooms"""
        from src.matrix.mention_routing import handle_agent_mention_routing
        
        mock_event = Mock()
        mock_event.body = "@Meridian and @BMO please collaborate"
        mock_event.sender = "@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca"
        mock_event.event_id = "$multi123"
        mock_event.source = {"content": {}}
        
        with patch('src.matrix.mention_routing.forward_to_agent_room') as mock_forward:
            mock_forward.return_value = "$fwd"
            
            await handle_agent_mention_routing(
                room=mock_room,
                event=mock_event,
                sender_mxid=mock_event.sender,
                sender_agent_id="agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                config=mock_config,
                logger=mock_logger,
            )
            
            # Should forward to both Meridian and BMO
            assert mock_forward.call_count == 2
    
    @pytest.mark.asyncio
    async def test_no_forward_when_no_mentions(
        self, mock_room, mock_config, mock_logger, mock_mapping_service
    ):
        """Test that no forwarding happens when there are no @mentions"""
        from src.matrix.mention_routing import handle_agent_mention_routing
        
        mock_event = Mock()
        mock_event.body = "Just a regular message without mentions"
        mock_event.sender = "@agent_870d3dfb_319f_4c52_91f1_72ab46d944a7:matrix.oculair.ca"
        mock_event.event_id = "$none123"
        mock_event.source = {"content": {}}
        
        with patch('src.matrix.mention_routing.forward_to_agent_room') as mock_forward:
            await handle_agent_mention_routing(
                room=mock_room,
                event=mock_event,
                sender_mxid=mock_event.sender,
                sender_agent_id="agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                config=mock_config,
                logger=mock_logger,
            )
            
            mock_forward.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_logs_routing_activity(
        self, mock_room, mock_event, mock_config, mock_logger, mock_mapping_service
    ):
        """Test that routing activity is logged"""
        from src.matrix.mention_routing import handle_agent_mention_routing
        
        with patch('src.matrix.mention_routing.forward_to_agent_room') as mock_forward:
            mock_forward.return_value = "$fwd"
            
            await handle_agent_mention_routing(
                room=mock_room,
                event=mock_event,
                sender_mxid=mock_event.sender,
                sender_agent_id="agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
                sender_agent_name="Huly - Matrix Synapse Deployment",
                config=mock_config,
                logger=mock_logger,
            )
            
            # Should log mention detection and forwarding
            assert mock_logger.info.called


# =============================================================================
# Tests for get_mapping_by_agent_name() - to be added to mapping_service
# =============================================================================

class TestGetMappingByAgentName:
    """Tests for the get_mapping_by_agent_name() function in mapping_service"""
    
    @pytest.fixture
    def mock_db_with_agents(self):
        """Mock database with sample agents"""
        with patch('src.core.mapping_service._get_db') as mock:
            db = Mock()
            
            # Sample agent data
            agents = [
                Mock(agent_id="agent-1", agent_name="Meridian"),
                Mock(agent_id="agent-2", agent_name="BMO"),
                Mock(agent_id="agent-3", agent_name="Huly - Matrix Synapse Deployment"),
            ]
            
            def get_by_name(name, fuzzy=True):
                name_lower = name.lower()
                for agent in agents:
                    agent_name_lower = agent.agent_name.lower()
                    if fuzzy:
                        if name_lower == agent_name_lower or name_lower in agent_name_lower:
                            return agent
                        if agent_name_lower.startswith("huly - ") and name_lower in agent_name_lower[7:]:
                            return agent
                    else:
                        if name_lower == agent_name_lower:
                            return agent
                return None
            
            db.get_by_agent_name = get_by_name
            mock.return_value = db
            yield db
    
    def test_exact_name_match(self, mock_db_with_agents):
        """Test exact name matching"""
        from src.core.mapping_service import get_mapping_by_agent_name
        
        result = get_mapping_by_agent_name("Meridian")
        
        assert result is not None
        assert result["agent_name"] == "Meridian"
    
    def test_case_insensitive_match(self, mock_db_with_agents):
        """Test case-insensitive matching"""
        from src.core.mapping_service import get_mapping_by_agent_name
        
        result = get_mapping_by_agent_name("meridian")
        
        assert result is not None
        assert result["agent_name"] == "Meridian"
    
    def test_huly_prefix_stripping(self, mock_db_with_agents):
        """Test matching Huly agents by name without prefix"""
        from src.core.mapping_service import get_mapping_by_agent_name
        
        result = get_mapping_by_agent_name("Matrix Synapse Deployment")
        
        assert result is not None
        assert result["agent_name"] == "Huly - Matrix Synapse Deployment"
    
    def test_returns_none_for_unknown(self, mock_db_with_agents):
        """Test that None is returned for unknown agent names"""
        from src.core.mapping_service import get_mapping_by_agent_name
        
        result = get_mapping_by_agent_name("NonExistentAgent")
        
        assert result is None
    
    def test_fuzzy_disabled(self, mock_db_with_agents):
        """Test with fuzzy matching disabled"""
        from src.core.mapping_service import get_mapping_by_agent_name
        
        # Exact match should work
        result = get_mapping_by_agent_name("BMO", fuzzy=False)
        assert result is not None
        
        # Partial match should not work with fuzzy=False
        result = get_mapping_by_agent_name("Matrix Synapse", fuzzy=False)
        assert result is None
