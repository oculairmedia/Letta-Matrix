# Agent Routing Test Suite

## Overview
This test suite prevents a critical bug where messages to specific agent rooms were being routed to the wrong Letta agent.

## The Bug That Was Fixed

### Problem
- **Date**: January 2025
- **Symptom**: Messages sent to Meridian's Matrix room were being processed by "Personal Site" agent
- **Root Cause**: Letta SDK's `agents.list()` had pagination issues:
  - Production Letta API returned only first 50 agents by default
  - Meridian was agent #51-56 in the list
  - When agent lookup failed, code fell back to `agents[0].id` (Personal Site agent)

### Solution
1. **Removed Letta SDK entirely** - No more pagination issues
2. **Direct HTTP API calls** - Uses agent ID directly from room mapping
3. **No fallback to first agent** - Explicit routing based on room_id

## Running the Tests

### Prerequisites
```bash
cd /opt/stacks/matrix-synapse-deployment
pip install -r test_requirements.txt
```

### Run All Agent Routing Tests
```bash
pytest test_agent_routing.py -v
```

### Run Specific Tests
```bash
# Test the exact bug scenario (Meridian routing)
pytest test_agent_routing.py::test_correct_agent_routing_for_meridian_room -v

# Test with 50+ agents (pagination scenario)
pytest test_agent_routing.py::test_agent_routing_with_51_agents -v

# Ensure no SDK imports
pytest test_agent_routing.py::test_no_letta_sdk_imports -v
```

### Run with Coverage
```bash
pytest test_agent_routing.py --cov=custom_matrix_client --cov-report=html
```

## Test Coverage

### Critical Tests

1. **`test_correct_agent_routing_for_meridian_room`**
   - Ensures messages to Meridian's room go to Meridian agent
   - Verifies agent-597b5756... is used, NOT agent-7659b796... (Personal Site)

2. **`test_no_fallback_to_first_agent`**
   - Prevents fallback to `agents[0]` when room mapping exists
   - Tests that agent position in list doesn't matter

3. **`test_agent_routing_with_51_agents`**
   - Simulates exact production scenario with 56 agents
   - Ensures agent #51 (Meridian) is correctly routed

4. **`test_direct_http_api_call`**
   - Verifies we're using aiohttp POST, not Letta SDK
   - Checks correct API endpoint format: `/v1/agents/{id}/messages`

5. **`test_room_mapping_integrity`**
   - Tests multiple agents with different rooms
   - Ensures room-to-agent mapping is always respected

6. **`test_no_letta_sdk_imports`**
   - Static analysis to prevent SDK re-introduction
   - Fails if `from letta import` or `from letta_client import` found

## Integration with CI/CD

### GitHub Actions Workflow
Add to `.github/workflows/test.yml`:

```yaml
name: Agent Routing Tests

on:
  push:
    branches: [ main, develop ]
  pull_request:
    paths:
      - 'custom_matrix_client.py'
      - 'test_agent_routing.py'
      - 'requirements.txt'

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r test_requirements.txt
      
      - name: Run agent routing tests
        run: |
          pytest test_agent_routing.py -v --tb=short
      
      - name: Check for Letta SDK imports
        run: |
          pytest test_agent_routing.py::test_no_letta_sdk_imports -v
```

### Pre-commit Hook
Add to `.git/hooks/pre-commit`:

```bash
#!/bin/bash
echo "Running agent routing tests..."
pytest test_agent_routing.py -v --tb=short

if [ $? -ne 0 ]; then
    echo "❌ Agent routing tests failed! Fix before committing."
    exit 1
fi

echo "✅ All agent routing tests passed!"
```

## Manual Testing Checklist

### Production Validation
After deploying changes that affect agent routing:

1. **Send test message to Meridian's room**
   ```
   Room: !8I9YBvbr4KpXNedbph:matrix.oculair.ca
   Message: "What's your name?"
   Expected: Response from Meridian agent, not Personal Site
   ```

2. **Check logs for correct routing**
   ```bash
   docker logs matrix-synapse-deployment-matrix-client-1 | grep "AGENT ROUTING"
   ```
   Should show: `Room !8I9YBvbr4KpXNedbph:matrix.oculair.ca -> Agent agent-597b5756...`

3. **Verify agent mapping file**
   ```bash
   cat matrix_client_data/agent_user_mappings.json | jq '.[] | select(.agent_name == "Meridian")'
   ```

4. **Test multiple agent rooms**
   - Send messages to at least 3 different agent rooms
   - Verify each gets response from the correct agent
   - Check no cross-contamination

## Debugging Failed Tests

### If `test_correct_agent_routing_for_meridian_room` fails:
```python
# Add debug output in custom_matrix_client.py around line 156:
logger.warning(f"[TEST DEBUG] Room: {room_id}, Agent: {agent_id_to_use}")
```

### If `test_no_letta_sdk_imports` fails:
- Check `custom_matrix_client.py` for any `from letta` imports
- Search for `AsyncLetta` or `ApiError` references
- Ensure SDK was completely removed

### If HTTP tests fail:
- Verify aiohttp is installed: `pip list | grep aiohttp`
- Check API URL format in code matches tests
- Ensure `/v1/agents/{id}/messages` endpoint is correct

## Maintenance

### When to Update Tests
1. **Adding new agents**: Update `test_agent_routing_with_51_agents` agent count
2. **Changing room mappings**: Update test fixtures with new room IDs
3. **Modifying API endpoints**: Update URL assertions in `test_direct_http_api_call`
4. **Adding new routing logic**: Add corresponding test case

### Quarterly Review
- Run full test suite with coverage analysis
- Verify all critical paths are tested
- Update test data to match production agent count
- Review logs for any routing anomalies in production

## Success Criteria

All tests must pass before merging changes to:
- `custom_matrix_client.py`
- `agent_user_manager.py`
- `requirements.txt`

Zero tolerance for:
- Fallback to first agent when room mapping exists
- Re-introduction of Letta SDK for agent listing
- Pagination-dependent agent lookups

## References
- **Bug Report**: `CONVERSATION_SUMMARY.md`
- **Production Logs**: `docker logs matrix-synapse-deployment-matrix-client-1`
- **Agent Mappings**: `/app/data/agent_user_mappings.json`
- **Letta API Docs**: https://docs.letta.com/api
