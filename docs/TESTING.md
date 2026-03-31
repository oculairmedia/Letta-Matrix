# Testing Guide

## Quick Reference

```bash
# Smoke tests (~5s)
./run_tests.sh smoke

# Unit tests (~30s)
./run_tests.sh unit

# Integration tests (~2min)
./run_tests.sh integration

# All tests with coverage
./run_tests.sh coverage

# Everything
./run_tests.sh all
```

### Pytest Shortcuts

```bash
pytest tests/unit/test_agent_user_manager.py          # Specific file
pytest -k "agent_creation"                              # Pattern match
pytest -m unit                                          # By marker
pytest -x                                               # Stop on first failure
pytest -vsl --pdb                                       # Debug mode
```

## Test Structure

```
tests/
├── conftest.py                           # Shared fixtures
├── test_smoke.py                         # Quick smoke tests
├── unit/                                 # Isolated unit tests
│   ├── test_agent_user_manager.py
│   ├── test_custom_matrix_client.py
│   ├── test_mention_routing.py
│   ├── test_pill_formatter.py
│   └── ...
└── integration/                          # Component interaction tests
    ├── test_multi_agent_workflow.py
    ├── test_opencode_bridge.py
    └── ...
```

## Markers

| Marker | Purpose | When to Run |
|--------|---------|-------------|
| `@pytest.mark.smoke` | Quick validation, imports work | Before every commit |
| `@pytest.mark.unit` | Isolated function tests | During development |
| `@pytest.mark.integration` | Component interaction | Before merging, CI |
| `@pytest.mark.slow` | Tests >10s | CI only |
| `@pytest.mark.asyncio` | Async functions | Automatic |

## Writing Tests

Follow Arrange-Act-Assert:

```python
@pytest.mark.unit
class TestFeature:
    @pytest.mark.asyncio
    async def test_specific_behavior(self, mock_config):
        # Arrange
        manager = AgentUserManager(mock_config)
        agent = {"id": "test-001", "name": "Test Agent"}

        # Act
        result = await manager.process_agent(agent)

        # Assert
        assert result.success is True
```

### Fixtures (defined in `tests/conftest.py`)

- `mock_config` — Mock configuration object
- `mock_aiohttp_session` — Mock HTTP session
- `tmp_path` — Temporary directory for file operations

### Mocking External Dependencies

```python
from unittest.mock import Mock, AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mocks(mock_aiohttp_session):
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"success": True})
    mock_aiohttp_session.get = Mock(return_value=mock_response)
```

## Coverage

```bash
# HTML report
./run_tests.sh coverage && open htmlcov/index.html

# Terminal report
pytest --cov=src --cov-report=term-missing
```

**Targets**: 80%+ overall, 95%+ for critical paths (auth, routing, message sending).

## Critical Test Suites

### Agent Routing Tests

Prevent the bug where messages to one agent's room get routed to a different agent (originally caused by Letta SDK pagination returning only 50 agents).

Key tests:
- Correct agent receives messages for their room
- No fallback to first agent when mapping exists
- Works with 50+ agents
- Direct HTTP API calls (no Letta SDK)

### Agent Identity Tests

Ensure agents respond with their own Matrix user identity, not as `@letta`.

Key tests:
- Messages sent using agent-specific credentials
- Different agents use different Matrix accounts
- Graceful fallback when agent login fails
- Proper PUT with transaction ID for Matrix API

### Space Management Tests

Verify the Matrix Space organization feature:
- Space created on first startup and persisted
- Rooms auto-added to space
- Existing rooms migrated
- Bidirectional m.space.child/m.space.parent relationships

## Database Testing Strategy

### Three Tiers

| Tier | Backend | Speed | Realism | Use Case |
|------|---------|-------|---------|----------|
| Mock | unittest.mock | ⚡⚡⚡ | Low | Unit logic |
| SQLite | In-memory SQLite | ⚡⚡ | Medium | DB operations, CI |
| Full | Production-like | ⚡ | High | Integration |

Mock-based tests are the default. SQLite fixtures available for tests that need real SQL operations — see `tests/conftest.py` for `sqlite_db` and `sqlite_db_with_data` fixtures.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError` | Run from project root: `cd /opt/stacks/matrix-tuwunel-deploy && pytest` |
| `coroutine was never awaited` | Add `@pytest.mark.asyncio` decorator |
| `fixture not found` | Check `tests/conftest.py` — fixtures defined there |
| Tests hang | Add `@pytest.mark.timeout(10)` or check for infinite async loops |
| Coverage at 0% | Install pytest-cov: `pip install pytest-cov` |

## Best Practices

- Write tests before refactoring
- Test one thing per test
- Mock external dependencies (Matrix API, Letta API)
- Keep unit tests fast (<100ms each)
- Don't test implementation details — test behavior
- Never commit failing tests
- Don't use `sleep()` — use proper async handling

## Files

- `pytest.ini` — Pytest configuration and markers
- `tests/conftest.py` — Shared fixtures
- `run_tests.sh` — Test runner script
- `requirements.txt` / `test_requirements.txt` — Dependencies
