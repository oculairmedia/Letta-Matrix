#!/usr/bin/env python3
"""
Test agent-to-agent messaging with proper sender authentication
"""
import asyncio
import json
import time
import aiohttp

# Configuration
MCP_SERVER_URL = "http://localhost:8017/mcp"
FROM_AGENT_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"  # Meridian
TO_AGENT_ID = "agent-f2fdf2aa-5b83-4c2d-a926-2e86e6793551"    # BMO

async def test_agent_message():
    """Test sending a message from Meridian to BMO"""
    async with aiohttp.ClientSession() as session:
        # Send message AS Meridian (via x-agent-id header)
        headers = {
            "Content-Type": "application/json",
            "x-agent-id": FROM_AGENT_ID  # This identifies the sender
        }

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "matrix_agent_message_async",
                "arguments": {
                    "to_agent_id": TO_AGENT_ID,
                    "message": "Hello BMO! This is Meridian testing direct agent messaging. Can you see that this message is from me (Meridian)?",
                    "timeout_seconds": 30
                }
            },
            "id": int(time.time())
        }

        print("=" * 60)
        print("TEST: Agent-to-Agent Direct Message")
        print("=" * 60)
        print(f"From: Meridian ({FROM_AGENT_ID})")
        print(f"To: BMO ({TO_AGENT_ID})")
        print()

        # Send the message
        async with session.post(MCP_SERVER_URL, json=payload, headers=headers) as resp:
            result = await resp.json()

            if "result" in result:
                content = json.loads(result["result"]["content"][0]["text"])
                if content.get("success"):
                    tracking_id = content.get("tracking_id")
                    print(f"✅ Message sent with tracking ID: {tracking_id}")
                    print()

                    # Wait a moment
                    print("Waiting 5 seconds for message delivery...")
                    await asyncio.sleep(5)

                    # Check status
                    status_payload = {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "matrix_agent_message_status",
                            "arguments": {"tracking_id": tracking_id}
                        },
                        "id": int(time.time())
                    }

                    async with session.post(MCP_SERVER_URL, json=status_payload, headers={"Content-Type": "application/json"}) as status_resp:
                        status_result = await status_resp.json()
                        status_content = json.loads(status_result["result"]["content"][0]["text"])
                        print(f"Status: {status_content.get('status')}")
                        print(f"Elapsed: {status_content.get('elapsed_seconds', 0):.1f}s")

                        # Check logs to see who sent the message
                        print("\n📝 To verify sender identity:")
                        print("Check BMO's Matrix room to see if message appears as from Meridian")
                        print("Run: docker logs matrix-synapse-deployment-matrix-client-1 | grep 'Processing message from'")
                else:
                    print(f"❌ Failed to send: {content.get('error')}")
            else:
                print(f"❌ Error: {result}")

if __name__ == "__main__":
    asyncio.run(test_agent_message())