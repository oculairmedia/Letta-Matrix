#!/usr/bin/env python3
"""
Test suite for async inter-agent communication via Matrix
Tests the full workflow: send -> monitor -> retrieve
"""
import asyncio
import json
import time
from typing import Optional
import aiohttp

# Configuration
MCP_SERVER_URL = "http://localhost:8017/mcp"
TEST_FROM_AGENT = "agent-597b5756-2915-4560-ba6b-91005f085166"  # Meridian
TEST_TO_AGENT = "agent-f2fdf2aa-5b83-4c2d-a926-2e86e6793551"    # BMO


class AsyncCommunicationTester:
    """Test harness for async agent communication"""

    def __init__(self):
        self.session = None
        self.test_results = []

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def call_mcp_tool(self, tool_name: str, arguments: dict, agent_id: Optional[str] = None) -> dict:
        """Call an MCP tool"""
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

        async with self.session.post(MCP_SERVER_URL, json=payload, headers=headers) as resp:
            result = await resp.json()
            return result

    def record_test(self, test_name: str, passed: bool, details: str):
        """Record test result"""
        self.test_results.append({
            "test": test_name,
            "passed": passed,
            "details": details,
            "timestamp": time.time()
        })
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} - {test_name}")
        if details:
            print(f"   {details}")

    async def test_send_async_message(self) -> Optional[str]:
        """Test 1: Send async message"""
        print("\n" + "="*60)
        print("TEST 1: Send Async Message")
        print("="*60)

        try:
            result = await self.call_mcp_tool(
                "matrix_agent_message_async",
                {
                    "to_agent_id": TEST_TO_AGENT,
                    "message": "Test async message - please respond with 'ACK' to confirm receipt",
                    "timeout_seconds": 30
                },
                agent_id=TEST_FROM_AGENT
            )

            # Check if successful
            if "result" not in result:
                self.record_test(
                    "Send Async Message",
                    False,
                    f"No result in response: {result}"
                )
                return None

            content = json.loads(result["result"]["content"][0]["text"])

            if content.get("success"):
                tracking_id = content.get("tracking_id")
                self.record_test(
                    "Send Async Message",
                    True,
                    f"Tracking ID: {tracking_id}"
                )
                return tracking_id
            else:
                self.record_test(
                    "Send Async Message",
                    False,
                    f"Error: {content.get('error')}"
                )
                return None

        except Exception as e:
            self.record_test(
                "Send Async Message",
                False,
                f"Exception: {str(e)}"
            )
            return None

    async def test_check_status(self, tracking_id: str, expected_status: str = None) -> dict:
        """Test 2: Check message status"""
        print("\n" + "="*60)
        print(f"TEST 2: Check Status (expecting: {expected_status or 'any'})")
        print("="*60)

        try:
            result = await self.call_mcp_tool(
                "matrix_agent_message_status",
                {"tracking_id": tracking_id}
            )

            if "result" not in result:
                self.record_test(
                    "Check Status",
                    False,
                    f"No result in response"
                )
                return {}

            content = json.loads(result["result"]["content"][0]["text"])

            status = content.get("status")
            elapsed = content.get("elapsed_seconds", 0)

            details = f"Status: {status}, Elapsed: {elapsed:.1f}s"
            if expected_status:
                passed = status == expected_status
                details += f" (expected: {expected_status})"
            else:
                passed = True

            self.record_test(
                "Check Status",
                passed,
                details
            )

            return content

        except Exception as e:
            self.record_test(
                "Check Status",
                False,
                f"Exception: {str(e)}"
            )
            return {}

    async def test_retrieve_result(self, tracking_id: str, delete_after: bool = False) -> dict:
        """Test 3: Retrieve result"""
        print("\n" + "="*60)
        print("TEST 3: Retrieve Result")
        print("="*60)

        try:
            result = await self.call_mcp_tool(
                "matrix_agent_message_result",
                {
                    "tracking_id": tracking_id,
                    "delete_after_read": delete_after
                }
            )

            if "result" not in result:
                self.record_test(
                    "Retrieve Result",
                    False,
                    f"No result in response"
                )
                return {}

            content = json.loads(result["result"]["content"][0]["text"])

            status = content.get("status")
            has_response = content.get("response") is not None
            has_error = content.get("error") is not None

            details = f"Status: {status}, Response: {has_response}, Error: {has_error}"
            if delete_after:
                details += f", Deleted: {content.get('deleted', False)}"

            self.record_test(
                "Retrieve Result",
                True,
                details
            )

            if has_response:
                print(f"\n   Response Preview: {content['response'][:100]}...")

            return content

        except Exception as e:
            self.record_test(
                "Retrieve Result",
                False,
                f"Exception: {str(e)}"
            )
            return {}

    async def test_timeout_behavior(self):
        """Test 4: Timeout behavior"""
        print("\n" + "="*60)
        print("TEST 4: Timeout Behavior (5 second timeout)")
        print("="*60)

        try:
            # Send message with very short timeout
            result = await self.call_mcp_tool(
                "matrix_agent_message_async",
                {
                    "to_agent_id": TEST_TO_AGENT,
                    "message": "This should timeout - do not respond",
                    "timeout_seconds": 5
                },
                agent_id=TEST_FROM_AGENT
            )

            content = json.loads(result["result"]["content"][0]["text"])
            tracking_id = content.get("tracking_id")

            # Wait for timeout
            await asyncio.sleep(7)

            # Check status - should be timeout
            status_result = await self.call_mcp_tool(
                "matrix_agent_message_status",
                {"tracking_id": tracking_id}
            )

            status_content = json.loads(status_result["result"]["content"][0]["text"])
            actual_status = status_content.get("status")

            self.record_test(
                "Timeout Behavior",
                actual_status == "timeout",
                f"Status after timeout: {actual_status}"
            )

        except Exception as e:
            self.record_test(
                "Timeout Behavior",
                False,
                f"Exception: {str(e)}"
            )

    async def test_invalid_agent(self):
        """Test 5: Invalid agent handling"""
        print("\n" + "="*60)
        print("TEST 5: Invalid Agent Handling")
        print("="*60)

        try:
            result = await self.call_mcp_tool(
                "matrix_agent_message_async",
                {
                    "to_agent_id": "agent-nonexistent-invalid",
                    "message": "Test message to invalid agent",
                    "timeout_seconds": 10
                },
                agent_id=TEST_FROM_AGENT
            )

            content = json.loads(result["result"]["content"][0]["text"])

            # Should get tracking ID but eventually fail
            if content.get("success"):
                tracking_id = content.get("tracking_id")

                # Wait a bit for background task to fail
                await asyncio.sleep(3)

                # Check status
                status_result = await self.call_mcp_tool(
                    "matrix_agent_message_status",
                    {"tracking_id": tracking_id}
                )

                status_content = json.loads(status_result["result"]["content"][0]["text"])
                actual_status = status_content.get("status")

                self.record_test(
                    "Invalid Agent Handling",
                    actual_status == "failed",
                    f"Status for invalid agent: {actual_status}"
                )
            else:
                # Immediate error is also acceptable
                self.record_test(
                    "Invalid Agent Handling",
                    True,
                    f"Immediate error: {content.get('error')}"
                )

        except Exception as e:
            self.record_test(
                "Invalid Agent Handling",
                False,
                f"Exception: {str(e)}"
            )

    async def test_status_tracking_id_not_found(self):
        """Test 6: Status check with invalid tracking ID"""
        print("\n" + "="*60)
        print("TEST 6: Invalid Tracking ID")
        print("="*60)

        try:
            result = await self.call_mcp_tool(
                "matrix_agent_message_status",
                {"tracking_id": "invalid-tracking-id-12345"}
            )

            content = json.loads(result["result"]["content"][0]["text"])

            # Should return not_found status
            self.record_test(
                "Invalid Tracking ID",
                content.get("status") == "not_found",
                f"Status: {content.get('status')}"
            )

        except Exception as e:
            self.record_test(
                "Invalid Tracking ID",
                False,
                f"Exception: {str(e)}"
            )

    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)

        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r["passed"])
        failed = total - passed

        print(f"Total Tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        print(f"Success Rate: {(passed/total*100):.1f}%")

        if failed > 0:
            print("\nFailed Tests:")
            for result in self.test_results:
                if not result["passed"]:
                    print(f"  - {result['test']}: {result['details']}")


async def main():
    """Main test runner"""
    print("="*60)
    print("ASYNC INTER-AGENT COMMUNICATION TEST SUITE")
    print("="*60)
    print(f"MCP Server: {MCP_SERVER_URL}")
    print(f"From Agent: {TEST_FROM_AGENT}")
    print(f"To Agent: {TEST_TO_AGENT}")

    async with AsyncCommunicationTester() as tester:
        # Test 1: Send async message
        tracking_id = await tester.test_send_async_message()

        if tracking_id:
            # Test 2: Check status immediately
            await tester.test_check_status(tracking_id, expected_status="pending")

            # Wait a bit for processing
            print("\nWaiting 8 seconds for message processing...")
            await asyncio.sleep(8)

            # Test 2b: Check status after processing
            await tester.test_check_status(tracking_id)

            # Test 3: Retrieve result
            await tester.test_retrieve_result(tracking_id, delete_after=True)

        # Test 4: Timeout behavior
        await tester.test_timeout_behavior()

        # Test 5: Invalid agent
        await tester.test_invalid_agent()

        # Test 6: Invalid tracking ID
        await tester.test_status_tracking_id_not_found()

        # Print summary
        tester.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
