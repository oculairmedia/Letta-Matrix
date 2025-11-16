# Test Quick Reference Card

## Running Tests

```bash
# Quick smoke tests (5s)
./run_tests.sh smoke

# Unit tests (30s)
./run_tests.sh unit

# Integration tests (2min)
./run_tests.sh integration

# All tests with coverage
./run_tests.sh coverage

# Everything
./run_tests.sh all
```

## Common Pytest Commands

```bash
# Specific test file
pytest tests/unit/test_agent_user_manager.py

# Specific test function
pytest tests/unit/test_agent_user_manager.py::TestAgentUserMapping::test_mapping_creation

# Tests matching pattern
pytest -k "agent_creation"

# Tests with marker
pytest -m unit
pytest -m "smoke or unit"

# Stop on first failure
pytest -x

# Show prints
pytest -s

# Verbose
pytest -v

# Debug on failure
pytest --pdb
```

## Test Markers

- `@pytest.mark.smoke` - Quick validation
- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.slow` - Long-running tests
- `@pytest.mark.asyncio` - Async tests

## Coverage

```bash
# Generate HTML coverage report
./run_tests.sh coverage
open htmlcov/index.html

# Terminal coverage report
pytest --cov=. --cov-report=term-missing

# Coverage for specific module
pytest --cov=agent_user_manager tests/unit/test_agent_user_manager.py
```

## Writing Tests

### Basic test structure

```python
@pytest.mark.unit
class TestMyFeature:
    """Test my feature"""

    def test_specific_behavior(self, mock_config):
        # Arrange
        manager = AgentUserManager(mock_config)

        # Act
        result = manager.do_something()

        # Assert
        assert result.success is True
```

### Async test

```python
@pytest.mark.asyncio
async def test_async_operation(mock_config):
    manager = AgentUserManager(mock_config)
    result = await manager.async_operation()
    assert result is not None
```

### Using fixtures

```python
def test_with_fixtures(mock_config, mock_aiohttp_session, tmp_path):
    # mock_config - Test configuration
    # mock_aiohttp_session - Mocked HTTP client
    # tmp_path - Temporary directory
    pass
```

## Pre-Refactor Checklist

- [ ] All tests passing: `./run_tests.sh all`
- [ ] Coverage baseline: `./run_tests.sh coverage`
- [ ] Document current coverage %
- [ ] Commit test suite
- [ ] Ready to refactor!

## During Refactoring

1. Run smoke tests frequently: `./run_tests.sh smoke`
2. Run unit tests after changes: `./run_tests.sh unit`
3. Keep tests green (passing)
4. Update tests only if behavior changes intentionally

## Post-Refactor Checklist

- [ ] All tests still passing: `./run_tests.sh all`
- [ ] Coverage maintained: `./run_tests.sh coverage`
- [ ] No new failures introduced
- [ ] Added tests for new functionality
- [ ] Cleaned up obsolete tests

## Files

- `pytest.ini` - Configuration
- `tests/conftest.py` - Fixtures
- `tests/test_smoke.py` - Smoke tests
- `tests/unit/` - Unit tests
- `tests/integration/` - Integration tests
- `TESTING.md` - Full documentation
- `run_tests.sh` - Test runner

## Getting Help

1. Check `TESTING.md` for detailed guide
2. Look at existing tests for examples
3. Check pytest docs: https://docs.pytest.org/
