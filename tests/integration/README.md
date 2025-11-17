# Integration Tests

This directory contains integration tests for the Letta-Matrix project. Unlike unit tests that test individual components in isolation, integration tests verify that multiple components work correctly together.

## Test Types

### Mocked Integration Tests

**File:** `test_space_integration_mocked.py`

These tests simulate the full workflow of Matrix space management without requiring live services. They use mocked HTTP responses to simulate Matrix homeserver and Letta API interactions.

**Advantages:**
- Fast execution (no network calls)
- No external dependencies (no live Matrix/Letta servers needed)
- Deterministic results (consistent mock data)
- Safe for CI/CD pipelines
- Can be run in any environment

**What it tests:**
1. **Space Creation** - Creating the "Letta Agents" space
2. **Space Persistence** - Saving/loading space configuration
3. **Agent Discovery** - Fetching agent list from Letta API
4. **Room to Space Relationship** - Adding agent rooms to the space
5. **Room Migration** - Migrating existing rooms to the space
6. **Full Agent Sync** - Complete agent synchronization workflow

**Usage:**

```bash
# Run with pytest
pytest tests/integration/test_space_integration_mocked.py -v

# Run as standalone script
python tests/integration/test_space_integration_mocked.py

# Run all integration tests
pytest tests/integration/ -v

# Run with coverage
pytest tests/integration/test_space_integration_mocked.py --cov=src --cov-report=html
```

### Live Integration Tests

**File:** `../../scripts/integration_test_space.py`

These tests run against actual live services (Matrix homeserver and Letta API). They require proper configuration and running services.

**Advantages:**
- Tests real service interactions
- Validates actual API compatibility
- Catches real-world issues

**Disadvantages:**
- Requires live Matrix homeserver
- Requires live Letta API
- Slower execution
- May have side effects
- Requires network access

**Usage:**

```bash
# Set environment variables
export MATRIX_HOMESERVER_URL="http://localhost:8008"
export MATRIX_USERNAME="@letta:matrix.test"
export MATRIX_PASSWORD="password"
export LETTA_API_URL="http://localhost:8283"
export LETTA_TOKEN="your_token"

# Run the live integration test
python scripts/integration_test_space.py
```

## Mock Architecture

The mocked integration tests use a comprehensive mocking strategy:

### HTTP Session Mocking

All HTTP calls are intercepted via mocked `aiohttp.ClientSession`:
- Matrix login → Returns mock access token
- Space creation → Returns mock space ID
- Room creation → Returns mock room ID
- Letta agents API → Returns mock agent list
- Other operations → Returns success responses

### File System Mocking

All file operations use temporary directories:
- Agent mappings stored in temp directory
- Space configuration stored in temp directory
- Cleanup automatic after test completion

### Configuration Mocking

Test configuration uses mock values:
- Mock homeserver URL
- Mock Letta API URL
- Mock credentials
- All safe for testing

## Shared Fixtures

The `conftest.py` file provides reusable fixtures for integration tests:

- `integration_config` - Mock configuration object
- `integration_temp_dir` - Temporary directory for test data
- `mock_http_session` - Fully configured mock HTTP session
- `patched_http_session` - Auto-patches all modules to use mock session
- `patched_logging` - Patches logging to avoid initialization issues
- `integration_manager` - Ready-to-use mocked AgentUserManager
- `sample_agent_mappings` - Sample data for testing

## Adding New Integration Tests

To add new integration tests:

1. Create a new test file in `tests/integration/`
2. Use the shared fixtures from `conftest.py`
3. Mark tests with `@pytest.mark.integration`
4. For async tests, use `@pytest.mark.asyncio`

Example:

```python
import pytest
from src.core.agent_user_manager import AgentUserManager

@pytest.mark.integration
@pytest.mark.asyncio
async def test_my_feature(integration_manager):
    """Test my new feature"""
    result = await integration_manager.my_new_method()
    assert result is not None
```

## Best Practices

1. **Use mocked tests for CI/CD** - Fast, reliable, no external dependencies
2. **Use live tests for validation** - Before releases, validate against real services
3. **Keep mocks realistic** - Mock responses should match real API behavior
4. **Test error cases** - Mock both success and failure scenarios
5. **Clean up resources** - Use fixtures with proper cleanup
6. **Document assumptions** - Comment any non-obvious mock behavior

## Continuous Integration

Mocked integration tests are suitable for CI/CD:

```yaml
# Example GitHub Actions workflow
name: Integration Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run integration tests
        run: pytest tests/integration/ -v
```

## Troubleshooting

### Tests fail with "event loop" errors
- Ensure `pytest-asyncio` is installed
- Check that async fixtures use `@pytest.mark.asyncio`

### Tests fail with "module not found"
- Ensure you're running from project root
- Check that `src/` is in Python path

### Mocks not working
- Verify patchers are started before test execution
- Check that patch targets match actual module paths
- Ensure fixtures are properly chained

### File permission errors
- Check that temp directory is writable
- Verify `MATRIX_DATA_DIR` environment variable is set correctly

## Related Files

- `tests/conftest.py` - Main test configuration
- `tests/unit/` - Unit tests for individual components
- `scripts/integration_test_space.py` - Live integration tests
- `src/core/agent_user_manager.py` - Main component under test
