# Mock Testing Quick Start Guide

**Goal**: Run all integration tests in CI without external services  
**Status**: Phase 1 Complete (6/6 space tests passing)  
**Next**: Extend to all 14 integration tests

---

## Current State ✅

### What's Working
- **6 Mocked Space Integration Tests** (100% passing)
- **Zero External Dependencies** (no Matrix/Letta needed)
- **Fast Execution** (0.07s vs 17s)
- **CI-Ready** (works in GitHub Actions)

### What Needs Work
- **4 Multi-Agent Workflow Tests** (need mocking)
- **4 Live Integration Tests** (require real services)

---

## Quick Reference

### Run Mocked Tests
```bash
# All mocked integration tests
pytest tests/integration/test_space_integration_mocked.py -v

# All integration tests (mocked + live)
pytest tests/integration/ -v

# Specific test
pytest tests/integration/test_space_integration_mocked.py::test_mocked_space_integration -v
```

### Check What's Mocked
```bash
# View mock fixtures
cat tests/integration/conftest.py

# View mock implementation plan
cat docs/archive/test-history/INTEGRATION_TEST_MOCK_PLAN.md

# View mock requirements (API catalog)
cat docs/operations/MOCK_SERVICE_REQUIREMENTS.md
```

---

## How Mocking Works

### Architecture
```
Test → Calls Code → Code tries aiohttp.ClientSession() 
                 → Intercepted by pytest fixture
                 → Returns MagicMock instead
                 → Mock returns fake HTTP responses
                 → Test continues with fake data
```

### Key Files
- `tests/integration/conftest.py` - Mock fixtures and setup
- `tests/integration/test_space_integration_mocked.py` - Mocked tests
- `tests/integration/test_multi_agent_workflow.py` - Needs mocking (Phase 2)

### Mock Pattern
```python
# 1. Create mock responses
mock_response = MagicMock()
mock_response.status = 200
mock_response.json = AsyncMock(return_value={"data": [...]})
mock_response.__aenter__ = AsyncMock(return_value=mock_response)
mock_response.__aexit__ = AsyncMock(return_value=None)

# 2. Create mock session
mock_session = MagicMock()
mock_session.get = MagicMock(return_value=mock_response)
mock_session.__aenter__ = AsyncMock(return_value=mock_session)
mock_session.__aexit__ = AsyncMock(return_value=None)

# 3. Patch aiohttp
with patch('aiohttp.ClientSession', return_value=mock_session):
    # Test code runs with mocked HTTP
    result = await function_that_uses_http()
```

---

## Next Steps (Phase 2)

### To Mock Remaining Tests (4-6 hours)

1. **Analyze Failing Tests**
   ```bash
   pytest tests/integration/test_multi_agent_workflow.py -v
   # See what endpoints they call
   ```

2. **Extend conftest.py**
   - Add Letta pagination responses
   - Add user registration mocks
   - Add room join mocks
   - Add display name mocks

3. **Update Test File**
   ```python
   # tests/integration/test_multi_agent_workflow.py
   
   @pytest.mark.integration
   @pytest.mark.asyncio
   async def test_discover_and_create_agents(patched_http_session):
       # Now uses mocks instead of real services
       manager = AgentUserManager(config)
       agents = await manager.get_letta_agents()
       assert len(agents) == 3  # Mocked data
   ```

4. **Verify**
   ```bash
   pytest tests/integration/ -v
   # Should see 14/14 passing
   ```

---

## API Endpoints to Mock

See complete catalog in: `docs/operations/MOCK_SERVICE_REQUIREMENTS.md`

### Letta API
- `GET /v1/agents?limit=100` - List agents (with pagination)

### Matrix Synapse (11 endpoints)
- `POST /_matrix/client/r0/login` - Admin/user login
- `POST /_matrix/client/v3/register` - Create user
- `POST /_matrix/client/r0/createRoom` - Create room/space
- `POST /_matrix/client/r0/rooms/{id}/join` - Join room
- `PUT /_matrix/client/r0/rooms/{id}/state/m.room.name` - Set name
- `GET /_matrix/client/r0/rooms/{id}/state` - Get state
- `PUT /_matrix/client/r0/rooms/{id}/state/m.space.child/{child}` - Add to space
- `PUT /_matrix/client/r0/rooms/{id}/state/m.space.parent/{parent}` - Set parent
- `GET /_matrix/client/r0/joined_rooms` - List user's rooms
- `PUT /_matrix/client/v3/profile/{user}/displayname` - Set display name
- And more...

---

## Troubleshooting

### "coroutine was never awaited" Error
**Fix**: Use `MagicMock()` not `AsyncMock()` for response objects
```python
# WRONG
mock_response = AsyncMock()

# RIGHT  
mock_response = MagicMock()
mock_response.json = AsyncMock(return_value={...})
```

### Mock Not Being Used
**Fix**: Ensure patch target matches actual import
```python
# Code uses: src.core.agent_user_manager.aiohttp.ClientSession
# Patch must be: patch('src.core.agent_user_manager.aiohttp.ClientSession')
```

### Tests Pass Individually But Fail in Suite
**Fix**: Mock state bleeding between tests
```python
# Ensure patchers are stopped in fixture cleanup
yield mock_session
for patcher in patchers:
    patcher.stop()
```

---

## Resources

### Documentation
- **Mock Requirements**: `docs/operations/MOCK_SERVICE_REQUIREMENTS.md`
- **Implementation Plan**: `docs/archive/test-history/INTEGRATION_TEST_MOCK_PLAN.md`
- **Session Notes**: `docs/archive/test-history/SESSION_MOCK_TEST_FIX.md`
- **Test Failures**: `docs/archive/test-history/FAILING_TESTS_ANALYSIS.md`

### Code
- **Mock Fixtures**: `tests/integration/conftest.py`
- **Working Example**: `tests/integration/test_space_integration_mocked.py`
- **Unit Test Patterns**: `tests/unit/test_user_creation.py`

### External
- **pytest-asyncio**: https://pytest-asyncio.readthedocs.io/
- **unittest.mock**: https://docs.python.org/3/library/unittest.mock.html
- **Matrix Spec**: https://spec.matrix.org/v1.5/client-server-api/

---

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Mocked integration tests | 6 | 14 |
| Tests passing in CI | 210/217 (97%) | 217/217 (100%) |
| Test execution time | 0.07s (mocked) | < 1 min (all) |
| External dependencies | 0 (mocked) | 0 (all) |

---

**Last Updated**: 2025-11-16  
**Next Action**: Extend mocks to remaining 4 multi-agent workflow tests
