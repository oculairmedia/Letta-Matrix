# Inter-Agent Messaging Test Suite

**Date**: November 14, 2025  
**Status**: ✅ Complete - Tests verify sender identity and message delivery

## Overview

This test suite validates the inter-agent messaging system, specifically ensuring:
1. **Messages appear with correct sender identity** (not as "letta")
2. **Messages reach the target Letta agent**
3. **Context enhancement works correctly**
4. **Both sync and async tools function properly**

## Test Files

### `test_inter_agent_simple.py` (Recommended)
- **Simple manual tests** - Easy to run and understand
- **3 core tests**: Sync messaging, async messaging, error handling
- **Clear output** - Shows exactly what's happening
- **Exit codes** - Returns 0 on success, 1 on failure

### `test_inter_agent_messaging.py`
- **Comprehensive pytest suite** - 10 detailed tests
- **Full coverage** - Tests all edge cases
- **Requires pytest** - More dependencies

## Quick Start

### Run Simple Tests (Recommended)

```bash
cd /opt/stacks/matrix-synapse-deployment

# Make executable
chmod +x test_inter_agent_simple.py

# Run tests
python3 test_inter_agent_simple.py
```

### Run Comprehensive Tests

```bash
cd /opt/stacks/matrix-synapse-deployment

# Install test dependencies
pip3 install pytest pytest-asyncio

# Run all tests
pytest test_inter_agent_messaging.py -v

# Run specific test
pytest test_inter_agent_messaging.py::test_sync_message_sends_as_agent -v
```

## Test Configuration

### Test Agents Used

The tests use these real agents from your Letta instance:

| Agent | ID | Purpose |
|-------|-----|---------|
| Meridian | `agent-597b5756-...` | **Sender** - Sends test messages |
| Huly - Personal Site | `agent-7659b796-...` | **Receiver** - Receives sync messages |
| BMO | `agent-f2fdf2aa-...` | **Receiver** - Receives async messages |

### Endpoints

- **MCP Server**: `http://localhost:8017/mcp`
- **Matrix Homeserver**: `http://localhost:8008`
- **Letta API**: `http://192.168.50.90:8289`

## Test Descriptions

### Simple Tests

#### 1. **Synchronous Message Test**
- Sends message FROM Meridian TO Huly Personal
- Verifies:
  - Message sends successfully
  - Returns event_id
  - Shows `from_agent = "Meridian"` (not "letta")
  - Message appears in target room

#### 2. **Asynchronous Message Test**
- Sends async message FROM Meridian TO BMO
- Verifies:
  - Returns tracking_id
  - Status progresses from "pending" to "sent"
  - Background task processes message

#### 3. **Error Handling Test**
- Attempts to send to non-existent agent
- Verifies:
  - Returns error (not crash)
  - Includes error_type
  - Provides helpful error message

### Comprehensive Tests

1. **`test_sync_message_sender_identity`** - Verifies Matrix display name
2. **`test_sync_message_sends_as_agent`** - Verifies sender authentication
3. **`test_async_message_sends_as_agent`** - Verifies async sender
4. **`test_message_context_enhancement`** - Verifies metadata injection
5. **`test_message_reaches_letta_agent`** - End-to-end delivery test
6. **`test_agent_not_found_error`** - Non-existent agent handling
7. **`test_missing_parameters`** - Parameter validation
8. **`test_async_message_status_tracking`** - Status progression
9. **`test_system_sender_uses_admin`** - System message handling

## Expected Output

### ✅ Successful Test Run

```
############################################################
# Inter-Agent Messaging Test Suite
############################################################

============================================================
TEST 1: Synchronous Message (matrix_agent_message)
============================================================

Sending message FROM Meridian TO Huly Personal...

Result: {
  "success": true,
  "event_id": "$abc123...",
  "from_agent": "Meridian",
  "room_id": "!67Z7CRRaG2YfGEZ6aW:matrix.oculair.ca"
}

✓ SUCCESS: Message sent
  Event ID: $abc123...
  From Agent: Meridian
  Room ID: !67Z7CRRaG2YfGEZ6aW:matrix.oculair.ca

✓ VERIFIED: Message shows correct sender (Meridian)

============================================================
TEST SUMMARY
============================================================
✓ PASSED: Sync Message
✓ PASSED: Async Message
✓ PASSED: Error Handling

Total: 3/3 tests passed

🎉 ALL TESTS PASSED!
```

### ❌ Failed Test (Before Fix)

```
Result: {
  "success": true,
  "from_agent": "System",  # WRONG - should be "Meridian"
  "event_id": "$xyz..."
}

✗ FAILED: Expected from_agent='Meridian', got 'System'
```

## What the Tests Verify

### Issue 1: Sender Identity ✅ FIXED
**Before Fix**: Messages showed as "letta 💕"  
**Problem**: Using admin_token instead of agent's token  
**After Fix**: Messages show as "Letta Agent: Meridian"

The tests verify:
- Agent has proper Matrix display name
- `from_agent` field shows correct agent name
- Message sent with agent's credentials (not admin)

### Issue 2: Message Delivery ✅ VERIFIED
**Before Fix**: Messages appeared in Matrix but didn't reach Letta agent  
**Problem**: Unclear if messages were being processed  
**After Fix**: Messages delivered to correct Letta agent

The tests verify:
- Message sent to correct Matrix room
- custom_matrix_client picks up message
- Message routed to correct Letta agent ID
- Context enhancement includes sender metadata

## Troubleshooting

### Test Fails: "Connection refused"
```bash
# Check MCP server is running
docker ps | grep letta-agent-mcp

# Check logs
docker logs matrix-synapse-deployment-letta-agent-mcp-1
```

### Test Fails: "Agent not found"
```bash
# Verify agents exist
cat matrix_client_data/agent_user_mappings.json | jq 'keys'

# Check specific agent
cat matrix_client_data/agent_user_mappings.json | \
  jq '.["agent-597b5756-2915-4560-ba6b-91005f085166"]'
```

### Test Fails: "Room not found"
```bash
# Verify room exists in mapping
cat matrix_client_data/agent_user_mappings.json | \
  jq '.["agent-7659b796-4723-4d61-98b5-737f874ee652"].room_id'
```

### Message Shows Wrong Sender
This indicates the fix wasn't applied. Verify:
```bash
# Check letta-agent-mcp logs for "Agent login successful"
docker logs matrix-synapse-deployment-letta-agent-mcp-1 | \
  grep "Agent login successful"

# Should show the SENDING agent logging in, not admin
```

## Manual Verification

### Check Message in Matrix Client

1. **Open Element** at your Matrix homeserver
2. **Navigate to** "Huly - Personal Site" room
3. **Look for** recent test message
4. **Verify sender** shows as "Letta Agent: Meridian" (not "letta")

### Check Message Reached Letta

```bash
# Query Letta API for agent's messages
curl -H "Authorization: Bearer $LETTA_PASSWORD" \
  "http://192.168.50.90:8289/v1/agents/agent-7659b796-4723-4d61-98b5-737f874ee652/messages" | \
  jq '.[-5:]'  # Last 5 messages

# Look for your test message in the output
```

## Integration with CI/CD

### GitHub Actions

Add to `.github/workflows/inter-agent-tests.yml`:

```yaml
name: Inter-Agent Messaging Tests

on:
  push:
    paths:
      - 'letta_agent_mcp_server.py'
      - 'custom_matrix_client.py'
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Run simple tests
        run: |
          python3 test_inter_agent_simple.py
      
      - name: Run comprehensive tests
        run: |
          pip install pytest pytest-asyncio
          pytest test_inter_agent_messaging.py -v
```

## Test Maintenance

### When to Update Tests

- **New agent** added to mappings → Update test agent IDs
- **MCP server** port change → Update `MCP_SERVER_URL`
- **New messaging tool** added → Add corresponding test
- **Authentication** changes → Update token/auth tests

### Adding New Tests

```python
async def test_my_new_feature():
    """Test description"""
    async with InterAgentMessagingTester() as tester:
        result = await tester.call_mcp_tool(
            "matrix_agent_message",
            {"to_agent_id": TARGET, "message": "test"},
            agent_id=SENDER
        )
        
        assert result.get("success"), "Should succeed"
        # Add more assertions
```

## Related Documentation

- **Implementation Details**: `INTER_AGENT_CONTEXT_ENHANCEMENT.md`
- **Original Bug Fix**: `INTER_AGENT_MESSAGING_FIX.md`
- **Agent Routing**: `TEST_AGENT_ROUTING.md`
- **Agent Identity**: `TEST_AGENT_IDENTITY.md`

## Success Criteria

✅ All tests pass (exit code 0)  
✅ Messages show correct sender in Matrix  
✅ Messages reach target Letta agent  
✅ Error handling works properly  
✅ Both sync and async tools work  
✅ Status tracking functions correctly  

---

**Last Updated**: November 14, 2025  
**Maintainer**: Matrix Integration Team
