# Quick Test Reference Card

## Run All Tests
```bash
cd /opt/stacks/matrix-synapse-deployment

# Run all agent tests (routing + identity)
python3 -m pytest test_agent_routing.py test_agent_response_identity.py -v

# Or run individually
pytest test_agent_routing.py -v              # 6 routing tests
pytest test_agent_response_identity.py -v    # 7 identity tests
```
**Expected**: 13 passed in ~1.1s total

## Run Specific Critical Tests
```bash
# Agent routing tests
pytest test_agent_routing.py::test_correct_agent_routing_for_meridian_room -v
pytest test_agent_routing.py::test_no_letta_sdk_imports -v

# Agent identity tests  
pytest test_agent_response_identity.py::test_send_as_agent_uses_correct_user -v
pytest test_agent_response_identity.py::test_different_agents_use_different_identities -v
```

## Verify Production Routing & Identity
```bash
# Check logs for correct routing
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | \
  grep "AGENT ROUTING" | tail -5

# Should show: Room !8I9YBvbr4KpXNedbph -> Agent agent-597b5756...
# Should NOT show: agent-7659b796 (Personal Site)

# Check agent identity in responses
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | \
  grep "SEND_AS_AGENT" | tail -10

# Should show: Successfully sent message as Meridian (agent_597b5756...)
# Should show: sent_as_agent: true
```

## Quick Health Check
```bash
# Container status
docker ps | grep matrix-client

# Recent logs
docker logs matrix-synapse-deployment-matrix-client-1 --tail 20

# Agent mappings
cat matrix_client_data/agent_user_mappings.json | \
  jq '.[] | select(.agent_name == "Meridian")'
```

## Test Failures?
```bash
# Run with detailed output
pytest test_agent_routing.py -vv --tb=long

# Check for SDK imports (should find nothing)
grep -r "from letta import\|import letta" custom_matrix_client.py

# Verify Python version
python3 --version  # Should be 3.11+
```

## Success Indicators
✅ All 13 tests pass (6 routing + 7 identity)  
✅ No SDK imports found  
✅ Container shows "healthy" status  
✅ Logs show correct agent routing  
✅ Logs show `sent_as_agent: true`  
✅ Meridian responds as `@agent_597b5756...` (not @letta)  

## Failure Indicators  
❌ Any test failures  
❌ SDK imports detected  
❌ Container unhealthy/restarting  
❌ Wrong agent ID in routing logs  
❌ `sent_as_agent: false` in logs  
❌ All agents responding as @letta  
❌ Personal Site responding in Meridian's room  
