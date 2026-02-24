"""
Unit tests for the Letta streaming module.

Tests cover:
- StreamEvent creation and properties
- StreamEventType enum
- StepStreamReader chunk parsing
- StreamingMessageHandler event handling
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from src.matrix.streaming import (
    StreamEventType,
    StreamEvent,
    StepStreamReader,
    StreamingMessageHandler,
)


class TestStreamEventType:
    """Tests for StreamEventType enum"""
    
    def test_all_event_types_defined(self):
        """Verify all expected event types exist"""
        expected_types = [
            'REASONING', 'TOOL_CALL', 'TOOL_RETURN', 
            'ASSISTANT', 'STOP', 'USAGE', 'ERROR', 'PING',
            'APPROVAL_REQUEST'
        ]
        for event_type in expected_types:
            assert hasattr(StreamEventType, event_type)
    
    def test_event_type_values(self):
        """Verify event type string values"""
        assert StreamEventType.REASONING.value == "reasoning"
        assert StreamEventType.TOOL_CALL.value == "tool_call"
        assert StreamEventType.TOOL_RETURN.value == "tool_return"
        assert StreamEventType.ASSISTANT.value == "assistant"
        assert StreamEventType.STOP.value == "stop"
        assert StreamEventType.USAGE.value == "usage"
        assert StreamEventType.ERROR.value == "error"
        assert StreamEventType.PING.value == "ping"
        assert StreamEventType.APPROVAL_REQUEST.value == "approval_request"


class TestStreamEvent:
    """Tests for StreamEvent dataclass"""
    
    def test_create_basic_event(self):
        """Test creating a basic event"""
        event = StreamEvent(type=StreamEventType.ASSISTANT, content="Hello")
        assert event.type == StreamEventType.ASSISTANT
        assert event.content == "Hello"
        assert event.metadata == {}
    
    def test_create_event_with_metadata(self):
        """Test creating event with metadata"""
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "search", "arguments": "{}"}
        )
        assert event.metadata["tool_name"] == "search"
    
    def test_is_progress_tool_call(self):
        """Test is_progress for tool_call events"""
        event = StreamEvent(type=StreamEventType.TOOL_CALL)
        assert event.is_progress is True
    
    def test_is_progress_tool_return(self):
        """Test is_progress for tool_return events"""
        event = StreamEvent(type=StreamEventType.TOOL_RETURN)
        assert event.is_progress is True
    
    def test_is_progress_false_for_other_types(self):
        """Test is_progress is False for non-progress events"""
        for event_type in [StreamEventType.ASSISTANT, StreamEventType.REASONING, 
                          StreamEventType.STOP, StreamEventType.USAGE]:
            event = StreamEvent(type=event_type)
            assert event.is_progress is False
    
    def test_is_final_assistant(self):
        """Test is_final for assistant events"""
        event = StreamEvent(type=StreamEventType.ASSISTANT)
        assert event.is_final is True
    
    def test_is_final_false_for_other_types(self):
        """Test is_final is False for non-assistant events"""
        for event_type in [StreamEventType.TOOL_CALL, StreamEventType.TOOL_RETURN,
                          StreamEventType.REASONING, StreamEventType.STOP]:
            event = StreamEvent(type=event_type)
            assert event.is_final is False
    
    def test_is_error(self):
        """Test is_error property"""
        event = StreamEvent(type=StreamEventType.ERROR, content="Something failed")
        assert event.is_error is True
        
        event2 = StreamEvent(type=StreamEventType.ASSISTANT)
        assert event2.is_error is False
    
    def test_is_approval_request(self):
        """Test is_approval_request property"""
        event = StreamEvent(type=StreamEventType.APPROVAL_REQUEST)
        assert event.is_approval_request is True
        
        event2 = StreamEvent(type=StreamEventType.TOOL_CALL)
        assert event2.is_approval_request is False
    
    def test_format_progress_tool_call(self):
        """Test format_progress for tool call"""
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "archival_memory_search"}
        )
        assert event.format_progress() == "🔧 archival_memory_search..."
    
    def test_format_progress_tool_return_success(self):
        """Test format_progress for successful tool return"""
        event = StreamEvent(
            type=StreamEventType.TOOL_RETURN,
            metadata={"tool_name": "send_message", "status": "success"}
        )
        assert event.format_progress() == "✅ send_message"
    
    def test_format_progress_tool_return_failure(self):
        """Test format_progress for failed tool return"""
        event = StreamEvent(
            type=StreamEventType.TOOL_RETURN,
            metadata={"tool_name": "web_search", "status": "error"}
        )
        assert event.format_progress() == "❌ web_search (failed)"
    
    def test_format_progress_reasoning(self):
        """Test format_progress for reasoning"""
        event = StreamEvent(
            type=StreamEventType.REASONING,
            content="I need to think about this carefully and consider all options"
        )
        progress = event.format_progress()
        assert progress.startswith("💭 I need to think about this carefully")
        assert progress.endswith("...")
        assert len(progress) <= 55  # 50 chars + emoji + ellipsis
    
    def test_format_progress_reasoning_short(self):
        """Test format_progress for short reasoning (no truncation)"""
        event = StreamEvent(
            type=StreamEventType.REASONING,
            content="Quick thought"
        )
        assert event.format_progress() == "💭 Quick thought"
    
    def test_format_progress_assistant(self):
        """Test format_progress for assistant message"""
        event = StreamEvent(
            type=StreamEventType.ASSISTANT,
            content="Hello there!"
        )
        assert event.format_progress() == "Hello there!"
    
    def test_format_progress_approval_request_with_tools(self):
        """Test format_progress for approval request with tool calls"""
        event = StreamEvent(
            type=StreamEventType.APPROVAL_REQUEST,
            metadata={
                "tool_calls": [
                    {"name": "Bash", "tool_call_id": "tc-123"},
                    {"name": "Write", "tool_call_id": "tc-456"}
                ]
            }
        )
        progress = event.format_progress()
        assert "**Approval Required**" in progress
        assert "Bash" in progress
        assert "Write" in progress
    
    def test_format_progress_approval_request_empty(self):
        """Test format_progress for approval request without tool info"""
        event = StreamEvent(
            type=StreamEventType.APPROVAL_REQUEST,
            metadata={"tool_calls": []}
        )
        assert event.format_progress() == "⏳ **Approval Required**"


class TestStepStreamReader:
    """Tests for StepStreamReader"""
    
    def test_init(self):
        """Test StepStreamReader initialization"""
        mock_client = MagicMock()
        reader = StepStreamReader(
            letta_client=mock_client,
            include_reasoning=True,
            include_pings=False,
            timeout=60.0
        )
        assert reader.client == mock_client
        assert reader.include_reasoning is True
        assert reader.include_pings is False
        assert reader.timeout == 60.0
    
    def test_parse_chunk_ping(self):
        """Test parsing ping message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'ping'
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.PING
    
    def test_parse_chunk_reasoning_included(self):
        """Test parsing reasoning message when included"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client, include_reasoning=True)
        
        chunk = MagicMock()
        chunk.message_type = 'reasoning_message'
        chunk.reasoning = 'Thinking about the problem'
        chunk.id = 'msg-123'
        chunk.run_id = 'run-456'
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.REASONING
        assert event.content == 'Thinking about the problem'
        assert event.metadata['id'] == 'msg-123'
    
    def test_parse_chunk_reasoning_excluded(self):
        """Test parsing reasoning message when excluded"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client, include_reasoning=False)
        
        chunk = MagicMock()
        chunk.message_type = 'reasoning_message'
        chunk.reasoning = 'Thinking'
        
        event = reader._parse_chunk(chunk)
        assert event is None
    
    def test_parse_chunk_tool_call(self):
        """Test parsing tool call message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        tool_call = MagicMock()
        tool_call.name = 'archival_memory_search'
        tool_call.arguments = '{"query": "test"}'
        
        chunk = MagicMock()
        chunk.message_type = 'tool_call_message'
        chunk.tool_call = tool_call
        chunk.id = 'msg-789'
        chunk.run_id = 'run-012'
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.TOOL_CALL
        assert event.metadata['tool_name'] == 'archival_memory_search'
        assert event.metadata['arguments'] == '{"query": "test"}'
    
    def test_parse_chunk_tool_return(self):
        """Test parsing tool return message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        # First parse a tool call to set _last_tool_name
        tool_call = MagicMock()
        tool_call.name = 'send_message'
        tool_call.arguments = '{}'
        
        call_chunk = MagicMock()
        call_chunk.message_type = 'tool_call_message'
        call_chunk.tool_call = tool_call
        call_chunk.id = 'msg-1'
        call_chunk.run_id = 'run-1'
        reader._parse_chunk(call_chunk)
        
        # Now parse the return
        return_chunk = MagicMock()
        return_chunk.message_type = 'tool_return_message'
        return_chunk.tool_return = 'Message sent successfully'
        return_chunk.status = 'success'
        return_chunk.id = 'msg-2'
        return_chunk.run_id = 'run-1'
        
        event = reader._parse_chunk(return_chunk)
        assert event.type == StreamEventType.TOOL_RETURN
        assert event.content == 'Message sent successfully'
        assert event.metadata['tool_name'] == 'send_message'
        assert event.metadata['status'] == 'success'
    
    def test_parse_chunk_assistant(self):
        """Test parsing assistant message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'assistant_message'
        chunk.content = 'Hello! How can I help you?'
        chunk.id = 'msg-999'
        chunk.run_id = 'run-888'
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.ASSISTANT
        assert event.content == 'Hello! How can I help you?'
    
    def test_parse_chunk_stop_reason(self):
        """Test parsing stop reason message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'stop_reason'
        chunk.stop_reason = 'end_turn'
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.STOP
        assert event.content == 'end_turn'
    
    def test_parse_chunk_usage_statistics(self):
        """Test parsing usage statistics message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'usage_statistics'
        chunk.completion_tokens = 100
        chunk.prompt_tokens = 50
        chunk.total_tokens = 150
        chunk.step_count = 2
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.USAGE
        assert event.metadata['completion_tokens'] == 100
        assert event.metadata['prompt_tokens'] == 50
        assert event.metadata['total_tokens'] == 150
        assert event.metadata['step_count'] == 2
    
    def test_parse_chunk_error(self):
        """Test parsing error message"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'error_message'
        chunk.message = 'Rate limit exceeded'
        chunk.error_type = 'rate_limit'
        chunk.detail = 'Please wait 60 seconds'
        
        event = reader._parse_chunk(chunk)
        assert event.type == StreamEventType.ERROR
        assert event.content == 'Rate limit exceeded'
        assert event.metadata['error_type'] == 'rate_limit'
        assert event.metadata['detail'] == 'Please wait 60 seconds'
    
    def test_parse_chunk_unknown_type(self):
        """Test parsing unknown message type"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'unknown_type'
        
        event = reader._parse_chunk(chunk)
        assert event is None
    
    def test_parse_chunk_no_message_type(self):
        """Test parsing chunk without message_type"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock(spec=[])  # No attributes
        
        event = reader._parse_chunk(chunk)
        assert event is None
    
    def test_parse_chunk_approval_request_with_tool_calls_list(self):
        """Test parsing approval_request_message with tool_calls list"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        tool_call1 = MagicMock()
        tool_call1.name = 'Bash'
        tool_call1.tool_call_id = 'tc-123'
        tool_call1.arguments = '{"command": "ls -la"}'
        
        tool_call2 = MagicMock()
        tool_call2.name = 'Write'
        tool_call2.tool_call_id = 'tc-456'
        tool_call2.arguments = '{"filePath": "/tmp/test.txt"}'
        
        chunk = MagicMock()
        chunk.message_type = 'approval_request_message'
        chunk.tool_calls = [tool_call1, tool_call2]
        chunk.tool_call = None
        chunk.id = 'msg-approval-1'
        chunk.run_id = 'run-approval-1'
        chunk.step_id = 'step-approval-1'
        
        event = reader._parse_chunk(chunk)
        assert event is not None
        assert event.type == StreamEventType.APPROVAL_REQUEST
        assert event.metadata['id'] == 'msg-approval-1'
        assert event.metadata['run_id'] == 'run-approval-1'
        assert event.metadata['step_id'] == 'step-approval-1'
        assert len(event.metadata['tool_calls']) == 2
        assert event.metadata['tool_calls'][0]['name'] == 'Bash'
        assert event.metadata['tool_calls'][1]['name'] == 'Write'
    
    def test_parse_chunk_approval_request_with_single_tool_call(self):
        """Test parsing approval_request_message with single tool_call"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        tool_call = MagicMock()
        tool_call.name = 'Bash'
        tool_call.tool_call_id = 'tc-789'
        tool_call.arguments = '{"command": "rm -rf /"}'
        
        chunk = MagicMock()
        chunk.message_type = 'approval_request_message'
        chunk.tool_calls = []  # Empty list
        chunk.tool_call = tool_call  # Fallback to single tool_call
        chunk.id = 'msg-approval-2'
        chunk.run_id = 'run-approval-2'
        chunk.step_id = 'step-approval-2'
        
        event = reader._parse_chunk(chunk)
        assert event is not None
        assert event.type == StreamEventType.APPROVAL_REQUEST
        assert len(event.metadata['tool_calls']) == 1
        assert event.metadata['tool_calls'][0]['name'] == 'Bash'
        assert event.metadata['tool_calls'][0]['tool_call_id'] == 'tc-789'
    
    def test_parse_chunk_approval_request_empty(self):
        """Test parsing approval_request_message with no tool info"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        chunk = MagicMock()
        chunk.message_type = 'approval_request_message'
        chunk.tool_calls = None  # No tool_calls attribute
        chunk.tool_call = None
        chunk.id = 'msg-approval-3'
        chunk.run_id = 'run-approval-3'
        chunk.step_id = None
        
        event = reader._parse_chunk(chunk)
        assert event is not None
        assert event.type == StreamEventType.APPROVAL_REQUEST
        assert event.metadata['tool_calls'] == []


class TestStreamingMessageHandler:
    """Tests for StreamingMessageHandler"""
    
    @pytest.fixture
    def handler(self):
        """Create a handler with mock functions"""
        send_mock = AsyncMock(return_value="$event_123")
        delete_mock = AsyncMock()
        return StreamingMessageHandler(
            send_message=send_mock,
            delete_message=delete_mock,
            room_id="!test:matrix.example.com",
            delete_progress=False
        )
    
    @pytest.fixture
    def handler_with_delete(self):
        """Create a handler that deletes progress messages"""
        send_mock = AsyncMock(return_value="$event_123")
        delete_mock = AsyncMock()
        return StreamingMessageHandler(
            send_message=send_mock,
            delete_message=delete_mock,
            room_id="!test:matrix.example.com",
            delete_progress=True
        )
    
    @pytest.mark.asyncio
    async def test_handle_ping_event(self, handler):
        """Test that ping events are ignored"""
        event = StreamEvent(type=StreamEventType.PING)
        result = await handler.handle_event(event)
        assert result is None
        handler.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_progress_event_no_delete(self, handler):
        """Test handling progress event - progress is NOT posted to chat"""
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "search"}
        )
        result = await handler.handle_event(event)
        
        # Progress events are no longer sent to chat (only logged)
        assert result is None
        handler.send_message.assert_not_called()
        handler.delete_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_progress_event_with_delete(self, handler_with_delete):
        """Test handling progress event with deletion enabled - progress not posted"""
        # Send first progress
        event1 = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "search"}
        )
        await handler_with_delete.handle_event(event1)
        
        # Send second progress
        event2 = StreamEvent(
            type=StreamEventType.TOOL_RETURN,
            metadata={"tool_name": "search", "status": "success"}
        )
        await handler_with_delete.handle_event(event2)
        
        # Progress events are silently dropped — no send, no delete
        handler_with_delete.send_message.assert_not_called()
        handler_with_delete.delete_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_final_event(self, handler):
        """Test handling final assistant message"""
        event = StreamEvent(
            type=StreamEventType.ASSISTANT,
            content="Here's my response!"
        )
        result = await handler.handle_event(event)
        
        assert result == "$event_123"
        handler.send_message.assert_called_once_with(
            "!test:matrix.example.com",
            "Here's my response!"
        )
    
    @pytest.mark.asyncio
    async def test_handle_error_event(self, handler):
        """Test handling error event"""
        event = StreamEvent(
            type=StreamEventType.ERROR,
            content="Something went wrong",
            metadata={"detail": "Connection timeout"}
        )
        result = await handler.handle_event(event)
        
        handler.send_message.assert_called_once()
        call_args = handler.send_message.call_args[0]
        assert "Something went wrong" in call_args[1]
        assert "Connection timeout" in call_args[1]
    
    @pytest.mark.asyncio
    async def test_handle_stop_event(self, handler):
        """Test that stop events don't send messages"""
        event = StreamEvent(type=StreamEventType.STOP, content="end_turn")
        result = await handler.handle_event(event)
        
        assert result is None
        handler.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_usage_event(self, handler):
        """Test that usage events don't send messages"""
        event = StreamEvent(
            type=StreamEventType.USAGE,
            metadata={"total_tokens": 100}
        )
        result = await handler.handle_event(event)
        
        assert result is None
        handler.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_reasoning_event(self, handler):
        """Test that reasoning events don't send messages"""
        event = StreamEvent(
            type=StreamEventType.REASONING,
            content="Thinking..."
        )
        result = await handler.handle_event(event)
        
        assert result is None
        handler.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handle_approval_request_event(self, handler):
        """Test handling approval request event"""
        event = StreamEvent(
            type=StreamEventType.APPROVAL_REQUEST,
            metadata={
                'tool_calls': [
                    {'name': 'Bash', 'tool_call_id': 'tc-123', 'arguments': '{"command": "ls"}'},
                    {'name': 'Write', 'tool_call_id': 'tc-456', 'arguments': '{"filePath": "/tmp/x"}'}
                ],
                'id': 'msg-approval-1',
                'run_id': 'run-1',
                'step_id': 'step-1'
            }
        )
        result = await handler.handle_event(event)
        
        assert result == "$event_123"
        handler.send_message.assert_called_once()
        call_args = handler.send_message.call_args[0]
        assert call_args[0] == "!test:matrix.example.com"
        # Verify message content includes approval info
        message_content = call_args[1]
        assert "**Approval Required**" in message_content
        assert "Bash" in message_content
        assert "Write" in message_content
        assert "Tools awaiting approval" in message_content
    
    @pytest.mark.asyncio
    async def test_handle_approval_request_empty(self, handler):
        """Test handling approval request with no tool calls"""
        event = StreamEvent(
            type=StreamEventType.APPROVAL_REQUEST,
            metadata={'tool_calls': [], 'id': 'msg-1', 'run_id': 'run-1'}
        )
        result = await handler.handle_event(event)
        
        assert result == "$event_123"
        handler.send_message.assert_called_once()
        call_args = handler.send_message.call_args[0]
        message_content = call_args[1]
        assert "**Approval Required**" in message_content
        # Should NOT have "Tools awaiting approval" since there are none
        assert "Tools awaiting approval" not in message_content
    
    @pytest.mark.asyncio
    async def test_cleanup_no_delete(self, handler):
        """Test cleanup when delete_progress=False"""
        # Set up a progress event (no longer posts to chat)
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "test"}
        )
        await handler.handle_event(event)
        
        # Cleanup should not delete (no progress event_id stored)
        await handler.cleanup()
        handler.delete_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_cleanup_with_delete(self, handler_with_delete):
        """Test cleanup when delete_progress=True but no progress was sent"""
        # Progress events are no longer sent to chat, so no event_id to delete
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "test"}
        )
        await handler_with_delete.handle_event(event)
        
        # Cleanup — no progress event_id stored since progress isn't posted
        await handler_with_delete.cleanup()
        handler_with_delete.delete_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_cleanup_no_progress_to_delete(self, handler_with_delete):
        """Test cleanup when there's no progress message to delete"""
        await handler_with_delete.cleanup()
        handler_with_delete.delete_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_delete_failure_handled(self, handler_with_delete):
        """Test that delete failures are handled gracefully"""
        handler_with_delete.delete_message.side_effect = Exception("Network error")
        
        # Set up progress
        event1 = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "search"}
        )
        await handler_with_delete.handle_event(event1)
        
        # This should not raise despite delete failure
        event2 = StreamEvent(
            type=StreamEventType.ASSISTANT,
            content="Response"
        )
        result = await handler_with_delete.handle_event(event2)
        assert result is not None  # Should still return event_id


class TestIntegration:
    """Integration tests for streaming components"""
    
    @pytest.mark.asyncio
    async def test_full_streaming_flow(self):
        """Test a complete streaming flow with multiple events"""
        sent_messages = []
        deleted_messages = []
        event_counter = [0]
        
        async def mock_send(room_id, content):
            event_counter[0] += 1
            event_id = f"$event_{event_counter[0]}"
            sent_messages.append({"room_id": room_id, "content": content, "event_id": event_id})
            return event_id
        
        async def mock_delete(room_id, event_id):
            deleted_messages.append({"room_id": room_id, "event_id": event_id})
        
        handler = StreamingMessageHandler(
            send_message=mock_send,
            delete_message=mock_delete,
            room_id="!test:matrix.example.com",
            delete_progress=False  # Keep progress visible
        )
        
        # Simulate streaming flow
        events = [
            StreamEvent(type=StreamEventType.TOOL_CALL, metadata={"tool_name": "archival_memory_search"}),
            StreamEvent(type=StreamEventType.TOOL_RETURN, metadata={"tool_name": "archival_memory_search", "status": "success"}),
            StreamEvent(type=StreamEventType.ASSISTANT, content="I found the information you requested."),
            StreamEvent(type=StreamEventType.STOP, content="end_turn"),
            StreamEvent(type=StreamEventType.USAGE, metadata={"total_tokens": 150}),
        ]
        
        for event in events:
            await handler.handle_event(event)
        
        await handler.cleanup()
        
        # Verify: 1 message sent (assistant only — progress is silently dropped)
        assert len(sent_messages) == 1
        assert "I found the information" in sent_messages[0]["content"]
        
        # Verify: No messages deleted (delete_progress=False)
        assert len(deleted_messages) == 0
    
    @pytest.mark.asyncio
    async def test_separate_final_message_handler(self):
        """Test that send_final_message is used for final responses (e.g., for rich replies)"""
        regular_messages = []
        final_messages = []
        event_counter = [0]
        
        async def mock_send(room_id, content):
            event_counter[0] += 1
            event_id = f"$event_{event_counter[0]}"
            regular_messages.append({"room_id": room_id, "content": content, "event_id": event_id})
            return event_id
        
        async def mock_send_final(room_id, content):
            event_counter[0] += 1
            event_id = f"$final_{event_counter[0]}"
            final_messages.append({"room_id": room_id, "content": content, "event_id": event_id})
            return event_id
        
        async def mock_delete(room_id, event_id):
            pass
        
        handler = StreamingMessageHandler(
            send_message=mock_send,
            delete_message=mock_delete,
            room_id="!test:matrix.example.com",
            delete_progress=False,
            send_final_message=mock_send_final  # Separate handler for final messages
        )
        
        # Simulate streaming flow: progress then final
        events = [
            StreamEvent(type=StreamEventType.TOOL_CALL, metadata={"tool_name": "search"}),
            StreamEvent(type=StreamEventType.ASSISTANT, content="Here is your answer."),
        ]
        
        for event in events:
            await handler.handle_event(event)
        
        # Verify: No progress went through regular send_message (progress is silently dropped)
        assert len(regular_messages) == 0
        
        # Verify: Final message went through send_final_message (for rich reply support)
        assert len(final_messages) == 1
        assert "Here is your answer." in final_messages[0]["content"]


class TestConversationsAPISupport:
    """Tests for Conversations API support in StepStreamReader"""

    def test_stream_reader_accepts_conversation_id(self):
        """StepStreamReader.stream_message accepts conversation_id parameter"""
        mock_client = MagicMock()
        reader = StepStreamReader(letta_client=mock_client)
        
        import inspect
        sig = inspect.signature(reader.stream_message)
        params = list(sig.parameters.keys())
        
        assert "conversation_id" in params

    @pytest.mark.asyncio
    async def test_stream_message_uses_conversations_api_when_conversation_id_provided(self):
        """When conversation_id is provided, uses conversations.messages.create"""
        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_client.conversations.messages.create.return_value = mock_stream
        
        reader = StepStreamReader(letta_client=mock_client, timeout=1.0)
        
        events = []
        async for event in reader.stream_message(
            agent_id="agent-123",
            message="Hello",
            conversation_id="conv-456",
        ):
            events.append(event)
        
        mock_client.conversations.messages.create.assert_called_once()
        call_kwargs = mock_client.conversations.messages.create.call_args.kwargs
        assert call_kwargs["conversation_id"] == "conv-456"
        assert call_kwargs["input"] == "Hello"
        assert call_kwargs["streaming"] is True
        
        mock_client.agents.messages.stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_message_uses_agents_api_when_no_conversation_id(self):
        """When conversation_id is None, uses agents.messages.stream"""
        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        mock_client.agents.messages.stream.return_value = mock_stream
        
        reader = StepStreamReader(letta_client=mock_client, timeout=1.0)
        
        events = []
        async for event in reader.stream_message(
            agent_id="agent-123",
            message="Hello",
            conversation_id=None,
        ):
            events.append(event)
        
        mock_client.agents.messages.stream.assert_called_once()
        call_kwargs = mock_client.agents.messages.stream.call_args.kwargs
        assert call_kwargs["agent_id"] == "agent-123"
        assert call_kwargs["input"] == "Hello"
        
        mock_client.conversations.messages.create.assert_not_called()
