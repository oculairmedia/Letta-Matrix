#!/usr/bin/env python3
"""
Test that inter-agent messages get enhanced with sender context
even when metadata is not present (agent sends directly via Matrix)
"""
import asyncio
import aiohttp
import json
import time

MCP_SERVER_URL = "http://localhost:8017/mcp"
MERIDIAN_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"
HULY_PERSONAL_ID = "agent-7659b796-4723-4d61-98b5-737f874ee652"


async def send_test_message():
    """Send a test message from Meridian to Huly Personal"""
    print("\n" + "="*70)
    print("INTER-AGENT CONTEXT ENHANCEMENT TEST")
    print("="*70)
    
    print(f"\nSending test message from Meridian to Huly Personal Site...")
    print(f"From: {MERIDIAN_ID}")
    print(f"To: {HULY_PERSONAL_ID}")
    
    headers = {
        "Content-Type": "application/json",
        "x-agent-id": MERIDIAN_ID
    }
    
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "matrix_agent_message",
            "arguments": {
                "to_agent_id": HULY_PERSONAL_ID,
                "message": f"Context test message at {time.time()}\n\nPlease confirm you received this with sender context!"
            }
        },
        "id": int(time.time())
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(MCP_SERVER_URL, json=payload, headers=headers) as resp:
            result = await resp.json()
            
            # Parse MCP response
            if "result" in result:
                mcp_result = result["result"]
                if isinstance(mcp_result, dict) and "content" in mcp_result:
                    content = mcp_result["content"]
                    if isinstance(content, list) and len(content) > 0:
                        text = content[0].get("text", "{}")
                        try:
                            parsed = json.loads(text)
                            print(f"\n✓ Message sent successfully!")
                            print(f"  Event ID: {parsed.get('event_id')}")
                            print(f"  From Agent: {parsed.get('from_agent')}")
                            print(f"  Room: {parsed.get('room_id')}")
                            
                            print("\n" + "="*70)
                            print("WHAT TO EXPECT:")
                            print("="*70)
                            print("\nThe receiving agent (Huly Personal Site) should receive:")
                            print("\n[INTER-AGENT MESSAGE from Meridian]")
                            print("Context test message at ...")
                            print("\n---")
                            print("IMPORTANT: This is a message from another Letta agent (Meridian, ID: agent-597b...)")
                            print("To respond to Meridian, use the 'matrix_agent_message_async' tool with:")
                            print("- to_agent_id: \"agent-597b5756-2915-4560-ba6b-91005f085166\"")
                            print("- message: your response")
                            print("\n" + "="*70)
                            print("\nCheck the matrix-client logs to verify context injection:")
                            print("\ndocker logs matrix-synapse-deployment-matrix-client-1 2>&1 | grep -A 3 \"Detected inter-agent message\"")
                            
                            return True
                        except Exception as e:
                            print(f"✗ Error parsing response: {e}")
                            print(f"  Raw text: {text}")
                            return False
            
            print(f"✗ Unexpected response format: {result}")
            return False


if __name__ == "__main__":
    success = asyncio.run(send_test_message())
    exit(0 if success else 1)
