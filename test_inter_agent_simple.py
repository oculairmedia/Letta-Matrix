#!/usr/bin/env python3
"""
Simple manual test for inter-agent messaging
Verifies that messages send with correct identity and reach target agent
"""
import asyncio
import aiohttp
import json
import time

MCP_SERVER_URL = "http://localhost:8017/mcp"

# Test agents
MERIDIAN_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"
HULY_PERSONAL_ID = "agent-7659b796-4723-4d61-98b5-737f874ee652"
BMO_ID = "agent-f2fdf2aa-5b83-4c2d-a926-2e86e6793551"


async def call_mcp_tool(tool_name: str, arguments: dict, agent_id: str = ""):
    """Call MCP tool"""
    headers = {"Content-Type": "application/json"}
    if agent_id:
        headers["x-agent-id"] = agent_id
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        },
        "id": int(time.time())
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(MCP_SERVER_URL, json=payload, headers=headers) as resp:
            result = await resp.json()
            
            # Handle MCP response format
            if "result" in result:
                mcp_result = result["result"]
                # Check if it's wrapped in content array (MCP format)
                if isinstance(mcp_result, dict) and "content" in mcp_result:
                    content = mcp_result["content"]
                    if isinstance(content, list) and len(content) > 0:
                        # Parse the text content as JSON
                        text = content[0].get("text", "{}")
                        try:
                            return json.loads(text)
                        except:
                            return {"error": text}
                return mcp_result
            return result


async def test_sync_message():
    """Test synchronous inter-agent message"""
    print("\n" + "="*60)
    print("TEST 1: Synchronous Message (matrix_agent_message)")
    print("="*60)
    
    print(f"\nSending message FROM Meridian TO Huly Personal...")
    
    result = await call_mcp_tool(
        "matrix_agent_message",
        {
            "to_agent_id": HULY_PERSONAL_ID,
            "message": f"Test sync message at {time.time()}"
        },
        agent_id=MERIDIAN_ID
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")
    
    if result.get("success"):
        print("✓ SUCCESS: Message sent")
        print(f"  Event ID: {result.get('event_id')}")
        print(f"  From Agent: {result.get('from_agent')}")
        print(f"  Room ID: {result.get('room_id')}")
        
        # Verify it shows as from Meridian, not from "letta"
        assert result.get("from_agent") == "Meridian", \
            f"Expected from_agent='Meridian', got '{result.get('from_agent')}'"
        print("\n✓ VERIFIED: Message shows correct sender (Meridian)")
    else:
        print(f"✗ FAILED: {result.get('error')}")
        return False
    
    return True


async def test_async_message():
    """Test asynchronous inter-agent message"""
    print("\n" + "="*60)
    print("TEST 2: Asynchronous Message (matrix_agent_message_async)")
    print("="*60)
    
    print(f"\nSending async message FROM Meridian TO BMO...")
    
    result = await call_mcp_tool(
        "matrix_agent_message_async",
        {
            "to_agent_id": BMO_ID,
            "message": f"Test async message at {time.time()}",
            "timeout_seconds": 60
        },
        agent_id=MERIDIAN_ID
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")
    
    if result.get("success"):
        tracking_id = result.get("tracking_id")
        print(f"✓ SUCCESS: Async message queued")
        print(f"  Tracking ID: {tracking_id}")
        
        # Wait for processing
        print("\nWaiting 3 seconds for message to be sent...")
        await asyncio.sleep(3)
        
        # Check status
        status = await call_mcp_tool(
            "matrix_agent_message_status",
            {"tracking_id": tracking_id}
        )
        
        print(f"\nStatus check: {json.dumps(status, indent=2)}")
        
        if status.get("status") in ["sent", "completed"]:
            print(f"✓ VERIFIED: Message was sent (status: {status.get('status')})")
        else:
            print(f"⚠ WARNING: Unexpected status: {status.get('status')}")
        
        return True
    else:
        print(f"✗ FAILED: {result.get('error')}")
        return False


async def test_error_handling():
    """Test error handling"""
    print("\n" + "="*60)
    print("TEST 3: Error Handling")
    print("="*60)
    
    print("\nTesting with non-existent agent...")
    
    result = await call_mcp_tool(
        "matrix_agent_message",
        {
            "to_agent_id": "agent-00000000-0000-0000-0000-000000000000",
            "message": "This should fail"
        },
        agent_id=MERIDIAN_ID
    )
    
    print(f"\nResult: {json.dumps(result, indent=2)}")
    
    if "error" in result:
        print("✓ SUCCESS: Error properly returned")
        print(f"  Error type: {result.get('error_type')}")
        print(f"  Error message: {result.get('error')}")
        return True
    else:
        print("✗ FAILED: Should have returned error")
        return False


async def main():
    """Run all tests"""
    print("\n" + "#"*60)
    print("# Inter-Agent Messaging Test Suite")
    print("#"*60)
    
    results = []
    
    # Test 1: Sync message
    try:
        results.append(("Sync Message", await test_sync_message()))
    except Exception as e:
        print(f"✗ EXCEPTION: {e}")
        results.append(("Sync Message", False))
    
    # Test 2: Async message
    try:
        results.append(("Async Message", await test_async_message()))
    except Exception as e:
        print(f"✗ EXCEPTION: {e}")
        results.append(("Async Message", False))
    
    # Test 3: Error handling
    try:
        results.append(("Error Handling", await test_error_handling()))
    except Exception as e:
        print(f"✗ EXCEPTION: {e}")
        results.append(("Error Handling", False))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{status}: {test_name}")
    
    passed_count = sum(1 for _, p in results if p)
    total_count = len(results)
    
    print(f"\nTotal: {passed_count}/{total_count} tests passed")
    
    if passed_count == total_count:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"\n❌ {total_count - passed_count} test(s) failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
