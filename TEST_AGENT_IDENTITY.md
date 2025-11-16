# Agent Response Identity Test Suite

## Overview
This test suite ensures that agents respond with their own Matrix user identity, not as the @letta bot. This maintains proper agent attribution and user experience in Matrix clients.

## The Problem Being Prevented

### What Could Go Wrong
Without these tests, all agents might respond as `@letta:matrix.oculair.ca` instead of their individual agent accounts:
- Meridian should respond as `@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca`
- Personal Site should respond as `@agent_7659b796_d1ee_4c2d_9915_f676ee94667f:matrix.oculair.ca`
- etc.

### Why It Matters
- **User Confusion**: Users can't tell which agent is responding
- **Lost Context**: Message history doesn't show agent attribution
- **Poor UX**: All responses appear to come from the same bot
- **Breaks Agent Personality**: Each agent should have their own identity

## Test Suite Coverage

### File: `test_agent_response_identity.py`

#### 1. **test_send_as_agent_uses_correct_user** ✅
**Purpose**: Verify messages are sent as the agent user, not @letta

**What it tests**:
- Login uses agent's Matrix credentials
- Authorization token is agent-specific
- Message is sent with agent's identity

**Example**:
```python
# Meridian's room → Meridian's user
room_id = "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
user = "agent_597b5756_2915_4560_ba6b_91005f085166"  # NOT "letta"
```

#### 2. **test_different_agents_use_different_identities** ✅
**Purpose**: Ensure each agent uses their own unique Matrix account

**What it tests**:
- Meridian uses `@agent_597b5756...`
- Personal Site uses `@agent_7659b796...`
- No cross-contamination

**Prevents**: All agents responding as the same user

#### 3. **test_send_as_agent_uses_put_with_transaction_id** ✅
**Purpose**: Verify correct Matrix API usage for sending messages

**What it tests**:
- Uses PUT (not POST) for sending messages
- Includes unique transaction ID in URL
- Proper endpoint: `/_matrix/client/r0/rooms/{room}/send/m.room.message/{txn_id}`

**Prevents**: 404 errors from incorrect API endpoints

#### 4. **test_send_as_agent_handles_login_failure** ✅
**Purpose**: Graceful degradation when agent login fails

**What it tests**:
- Returns `False` on authentication failure
- Logs error appropriately
- Allows fallback to @letta account

**Prevents**: Crashes or unhandled exceptions

#### 5. **test_send_as_agent_handles_missing_room_mapping** ✅
**Purpose**: Handle rooms without agent mappings

**What it tests**:
- Returns `False` when room has no agent
- Logs warning
- Falls back to @letta gracefully

**Prevents**: Crashes on unmapped rooms

#### 6. **test_send_as_agent_message_content** ✅
**Purpose**: Verify message content is sent correctly

**What it tests**:
- Message body preserved exactly (including special chars/emojis)
- Correct msgtype ("m.text")
- No truncation or corruption

**Prevents**: Message content issues

#### 7. **test_agent_mapping_structure** ✅
**Purpose**: Validate agent mapping data structure

**What it tests**:
- Required fields present: `agent_id`, `agent_name`, `matrix_user_id`, `matrix_password`, `room_id`
- Correct format for `matrix_user_id` (starts with @, contains domain)
- Correct format for `room_id` (starts with !)

**Prevents**: Malformed agent mappings

## Running the Tests

### Quick Run
```bash
cd /opt/stacks/matrix-synapse-deployment
python3 -m pytest test_agent_response_identity.py -v
```

**Expected Output**:
```
test_agent_response_identity.py::test_send_as_agent_uses_correct_user PASSED [ 14%]
test_agent_response_identity.py::test_different_agents_use_different_identities PASSED [ 28%]
test_agent_response_identity.py::test_send_as_agent_uses_put_with_transaction_id PASSED [ 42%]
test_agent_response_identity.py::test_send_as_agent_handles_login_failure PASSED [ 57%]
test_agent_response_identity.py::test_send_as_agent_handles_missing_room_mapping PASSED [ 71%]
test_agent_response_identity.py::test_send_as_agent_message_content PASSED [ 85%]
test_agent_response_identity.py::test_agent_mapping_structure PASSED [100%]

============================== 7 passed in 0.58s ===============================
```

### Run All Agent Tests
```bash
# Both routing and identity tests
pytest test_agent_routing.py test_agent_response_identity.py -v
```

**Expected**: 13 tests passing (6 routing + 7 identity)

### Run Specific Test
```bash
# Test the main identity check
pytest test_agent_response_identity.py::test_send_as_agent_uses_correct_user -v

# Test fallback behavior
pytest test_agent_response_identity.py::test_send_as_agent_handles_login_failure -v
```

## Integration with Agent Routing Tests

These tests complement the agent routing tests:

| Test Suite | What It Guards |
|------------|----------------|
| `test_agent_routing.py` | Messages go to the **correct agent** |
| `test_agent_response_identity.py` | Responses come from the **correct user** |

Both are essential for proper agent functionality.

## Production Validation

### Manual Testing Checklist

After deploying changes that affect agent responses:

1. **Send message to Meridian's room**
   ```
   Room: !8I9YBvbr4KpXNedbph:matrix.oculair.ca
   Expected sender in response: @agent_597b5756_2915_4560_ba6b_91005f085166
   NOT: @letta:matrix.oculair.ca
   ```

2. **Check Matrix client UI**
   - Response should show Meridian's display name
   - Avatar should be Meridian's (if set)
   - Username should be the agent user

3. **Check server logs**
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "SEND_AS_AGENT"
   ```
   Should show:
   ```
   [SEND_AS_AGENT] Attempting to send as agent: Meridian
   [SEND_AS_AGENT] Successfully logged in as agent_597b5756...
   [SEND_AS_AGENT] ✅ Successfully sent message as Meridian
   ```

4. **Verify sent_as_agent flag**
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "sent_as_agent"
   ```
   Should show: `"sent_as_agent": true`

### Automated Checks

Add to monitoring:
```bash
# Check for agent send failures
docker logs matrix-synapse-deployment-matrix-client-1 --since 1h | \
  grep "Failed to send message as agent" | wc -l
# Should be 0 or very low
```

## Common Issues & Solutions

### Issue: All responses come from @letta

**Symptom**: `sent_as_agent: false` in logs

**Possible Causes**:
1. Agent login failing (check `[SEND_AS_AGENT]` logs)
2. Wrong password in agent mappings
3. Matrix API endpoint incorrect

**Debug**:
```bash
# Check for login errors
docker logs matrix-synapse-deployment-matrix-client-1 | \
  grep "\[SEND_AS_AGENT\].*Failed to login"

# Verify agent mapping has password
cat matrix_client_data/agent_user_mappings.json | \
  jq '.["agent-597b5756-2915-4560-ba6b-91005f085166"]'
```

### Issue: 404 error when sending

**Symptom**: `Failed to send message as agent: 404`

**Cause**: Missing transaction ID in URL

**Solution**: Verify `send_as_agent` uses PUT with UUID transaction ID

**Check**:
```bash
# Should see transaction IDs in logs
docker logs matrix-synapse-deployment-matrix-client-1 | \
  grep "Sending to.*m.room.message"
```

### Issue: Login fails with 403

**Symptom**: `Failed to login as agent: 403`

**Causes**:
1. Wrong password
2. User doesn't exist
3. Account disabled

**Debug**:
```bash
# Check if agent user exists on Matrix server
# (requires admin access to Synapse)
```

## CI/CD Integration

### GitHub Actions

Tests run automatically on:
- Push to `main` or `develop`
- Pull requests changing:
  - `custom_matrix_client.py`
  - `test_agent_response_identity.py`
  - `requirements.txt`

### Pre-commit Hook

Add to `.git/hooks/pre-commit`:
```bash
#!/bin/bash
echo "Running agent identity tests..."
pytest test_agent_response_identity.py -v --tb=short

if [ $? -ne 0 ]; then
    echo "❌ Agent identity tests failed!"
    exit 1
fi
```

## Test Maintenance

### When to Update

1. **Adding new agents**: Tests should work automatically (they use dynamic mappings)
2. **Changing Matrix API version**: Update endpoint URLs if Matrix upgrades
3. **Modifying `send_as_agent` function**: Add corresponding test cases
4. **New authentication methods**: Add tests for new auth flows

### Quarterly Review

- Verify all tests still pass
- Check coverage remains high
- Review production logs for agent send failures
- Update test data if agent count changes significantly

## Success Criteria

All tests must pass before merging changes that affect:
- `send_as_agent()` function
- Agent mapping structure
- Matrix authentication
- Message sending logic

Zero tolerance for:
- All agents responding as @letta
- Missing agent attribution
- Authentication bypasses

## Related Documentation

- **Agent Routing**: `TEST_AGENT_ROUTING.md`
- **Test Results**: `TESTING_SUMMARY.md`
- **Quick Reference**: `QUICK_TEST_REFERENCE.md`
- **CI/CD**: `.github/workflows/agent-routing-tests.yml`
