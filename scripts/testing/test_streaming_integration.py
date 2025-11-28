#!/usr/bin/env python3
"""
Test script for Letta step streaming integration with Matrix.

This script tests the streaming adapter without requiring the full Matrix client.
It validates:
1. StepStreamReader parses events correctly
2. StreamingMessageHandler manages message lifecycle
3. Progress messages are formatted properly

Usage:
    python scripts/testing/test_streaming_integration.py
"""

import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from letta_client import Letta

# Test configuration
LETTA_API_URL = os.getenv("LETTA_API_URL", "http://192.168.50.90:8283")
LETTA_TOKEN = os.getenv("LETTA_TOKEN", "lettaSecurePass123")
# Use Meridian agent for testing (Claude Sonnet 4)
TEST_AGENT_ID = os.getenv("TEST_AGENT_ID", "agent-597b5756-2915-4560-ba6b-91005f085166")


class MockMatrixHandler:
    """Mock handler to track messages that would be sent to Matrix"""
    
    def __init__(self):
        self.messages = []
        self.deleted = []
        self.event_counter = 0
    
    async def send_message(self, room_id: str, content: str) -> str:
        """Mock send - returns fake event_id"""
        self.event_counter += 1
        event_id = f"$mock_event_{self.event_counter}"
        self.messages.append({
            "event_id": event_id,
            "room_id": room_id,
            "content": content,
            "deleted": False
        })
        print(f"  [SEND] {event_id}: {content[:60]}...")
        return event_id
    
    async def delete_message(self, room_id: str, event_id: str) -> None:
        """Mock delete"""
        self.deleted.append(event_id)
        for msg in self.messages:
            if msg["event_id"] == event_id:
                msg["deleted"] = True
        print(f"  [DELETE] {event_id}")
    
    def get_visible_messages(self):
        """Return messages that weren't deleted"""
        return [m for m in self.messages if not m["deleted"]]


async def test_step_streaming():
    """Test step streaming with a real Letta agent"""
    from src.matrix.streaming import StepStreamReader, StreamingMessageHandler, StreamEventType
    
    print("\n" + "="*60)
    print("Testing Step Streaming Integration")
    print("="*60)
    
    # Create Letta client
    print(f"\nConnecting to Letta at {LETTA_API_URL}")
    from src.letta.client import get_letta_client, LettaConfig
    sdk_config = LettaConfig(
        base_url=LETTA_API_URL,
        api_key=LETTA_TOKEN,
        timeout=120.0,
        max_retries=3
    )
    client = get_letta_client(sdk_config)
    
    # Verify agent exists
    try:
        agent = client.agents.retrieve(TEST_AGENT_ID)
        print(f"Using agent: {agent.name} ({agent.id})")
    except Exception as e:
        print(f"ERROR: Could not retrieve agent {TEST_AGENT_ID}: {e}")
        return False
    
    # Create stream reader
    stream_reader = StepStreamReader(
        letta_client=client,
        include_reasoning=True,  # Include reasoning for debugging
        include_pings=False,     # Skip pings for cleaner output
        timeout=120.0
    )
    
    # Create mock Matrix handler
    mock_handler = MockMatrixHandler()
    handler = StreamingMessageHandler(
        send_message=mock_handler.send_message,
        delete_message=mock_handler.delete_message,
        room_id="!test:matrix.oculair.ca"
    )
    
    # Test message - should trigger tool use
    test_message = "What time is it? Please check and tell me."
    print(f"\nSending test message: '{test_message}'")
    print("-" * 40)
    
    event_count = 0
    final_response = ""
    
    try:
        async for event in stream_reader.stream_message(TEST_AGENT_ID, test_message):
            event_count += 1
            
            # Log event details
            print(f"\n[Event {event_count}] Type: {event.type.value}")
            if event.content:
                preview = event.content[:100] + "..." if len(event.content) > 100 else event.content
                print(f"  Content: {preview}")
            if event.metadata:
                print(f"  Metadata: {event.metadata}")
            
            # Handle with Matrix handler
            await handler.handle_event(event)
            
            # Track final response
            if event.type == StreamEventType.ASSISTANT and event.content:
                final_response = event.content
        
        # Cleanup
        await handler.cleanup()
        
    except Exception as e:
        print(f"\nERROR during streaming: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Results
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    
    print(f"\nTotal events received: {event_count}")
    print(f"Messages sent to Matrix: {len(mock_handler.messages)}")
    print(f"Messages deleted: {len(mock_handler.deleted)}")
    
    visible = mock_handler.get_visible_messages()
    print(f"\nVisible messages (not deleted): {len(visible)}")
    for msg in visible:
        print(f"  - {msg['content'][:80]}...")
    
    print(f"\nFinal response: {final_response[:200] if final_response else 'None'}...")
    
    # Validation
    success = True
    
    if event_count == 0:
        print("\nFAIL: No events received!")
        success = False
    
    if not final_response:
        print("\nFAIL: No final assistant response!")
        success = False
    
    if len(visible) != 1:
        print(f"\nWARN: Expected exactly 1 visible message (final response), got {len(visible)}")
        # Not a hard failure - might have multiple assistant messages
    
    if success:
        print("\nSUCCESS: Step streaming integration test passed!")
    
    return success


async def test_event_parsing():
    """Test that event types are parsed correctly"""
    from src.matrix.streaming import StreamEventType, StreamEvent
    
    print("\n" + "="*60)
    print("Testing Event Parsing")
    print("="*60)
    
    # Test StreamEvent properties
    events = [
        StreamEvent(type=StreamEventType.TOOL_CALL, metadata={"tool_name": "send_message"}),
        StreamEvent(type=StreamEventType.TOOL_RETURN, metadata={"tool_name": "send_message", "status": "success"}),
        StreamEvent(type=StreamEventType.ASSISTANT, content="Hello!"),
        StreamEvent(type=StreamEventType.ERROR, content="Something went wrong"),
        StreamEvent(type=StreamEventType.REASONING, content="Thinking about the request..."),
    ]
    
    for event in events:
        print(f"\n{event.type.value}:")
        print(f"  is_progress: {event.is_progress}")
        print(f"  is_final: {event.is_final}")
        print(f"  is_error: {event.is_error}")
        print(f"  format_progress: {event.format_progress()}")
    
    # Validate
    assert events[0].is_progress == True, "TOOL_CALL should be progress"
    assert events[1].is_progress == True, "TOOL_RETURN should be progress"
    assert events[2].is_final == True, "ASSISTANT should be final"
    assert events[3].is_error == True, "ERROR should be error"
    assert events[4].is_progress == False, "REASONING should not be progress"
    
    print("\nSUCCESS: Event parsing test passed!")
    return True


async def main():
    """Run all tests"""
    print("="*60)
    print("Letta Streaming Integration Tests")
    print("="*60)
    
    results = []
    
    # Test 1: Event parsing
    try:
        results.append(("Event Parsing", await test_event_parsing()))
    except Exception as e:
        print(f"Event parsing test failed: {e}")
        results.append(("Event Parsing", False))
    
    # Test 2: Step streaming (requires live Letta server)
    try:
        results.append(("Step Streaming", await test_step_streaming()))
    except Exception as e:
        print(f"Step streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Step Streaming", False))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    print("\n" + ("All tests passed!" if all_passed else "Some tests failed!"))
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
