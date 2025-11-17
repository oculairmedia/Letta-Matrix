# Integration Test Mock Service Implementation Plan

_Created: 2025-11-16_  
_Status: Planning_

## Executive Summary

Integration tests currently fail in CI because they attempt to connect to real Letta API (`http://192.168.50.90:8289`) and Matrix Synapse (`test-synapse:8008`) instances that don't exist in the GitHub Actions environment. This document catalogs all external dependencies and provides an implementation plan for mock services.

---

## Current State

### Test Failures in CI

**Failing Tests:** 5/14 integration tests (35% failure rate)
- `TestAgentDiscoveryAndCreation::test_discover_and_create_agents`
- `TestAgentDiscoveryAndCreation::test_sync_agents_to_users`
- `TestRoomCreationAndManagement::test_create_room_for_agent`
- `TestAgentNameUpdates::test_detect_agent_name_change`
- `TestErrorRecovery::test_partial_agent_sync_failure`

**Root Causes:**
1. **Letta API** - Connection refused to `http://192.168.50.90:8289/v1/agents`
2. **Matrix Synapse** - DNS resolution failure for `test-synapse:8008`

**Error Messages:**
```
ERROR: Cannot connect to host test-synapse:8008 ssl:default [Temporary failure in name resolution]
ERROR: Error getting Letta agents from agents endpoint:
```

---

## External Service Dependencies

### 1. Letta API Service

**Base URL:** `http://192.168.50.90:8289` (production) / `http://test-letta:8283` (tests)

#### Endpoints Used

| Endpoint | Method | Purpose | Response Format |
|----------|--------|---------|-----------------|
| `/v1/agents` | GET | List all agents with pagination | `{"data": [{"id": "agent-xxx"}]}` OR `[{"id": "agent-xxx"}]` |
| `/v1/agents/{agent_id}` | GET | Get agent details | `{"id": "agent-xxx", "name": "Agent Name"}` |
| `/v1/agents/{agent_id}/messages` | POST | Send message to agent | `{"messages": [...]}` |

#### Request/Response Examples

**GET /v1/agents?limit=100&cursor=xxx**
```json
{
  "data": [
    {"id": "agent-001"},
    {"id": "agent-002"}
  ]
}
```

**GET /v1/agents/agent-001**
```json
{
  "id": "agent-001",
  "name": "Agent Alpha",
  "created_at": "2025-01-01T00:00:00Z"
}
```

**POST /v1/agents/agent-001/messages**
```json
Request: {
  "messages": [{"role": "user", "content": "Hello"}]
}

Response: {
  "messages": [
    {
      "message_type": "assistant_message",
      "assistant_message": "Response text"
    }
  ]
}
```

#### Authentication
- **Header:** `Authorization: Bearer {LETTA_TOKEN}`
- **Token:** Configured via `LETTA_TOKEN` environment variable

---

### 2. Matrix Synapse Service

**Base URL:** `http://test-synapse:8008` (tests) / `http://192.168.50.90:8008` (production)

#### Endpoints Used

| Endpoint | Method | Purpose | Response Format |
|----------|--------|---------|-----------------|
| `/_matrix/client/r0/login` | POST | Admin/user login | `{"access_token": "xxx", "user_id": "@user:domain"}` |
| `/_matrix/client/v3/login` | POST | User authentication | `{"access_token": "xxx"}` |
| `/_matrix/client/v3/register` | POST | Create new user | `{"user_id": "@user:domain"}` |
| `/_matrix/client/r0/createRoom` | POST | Create room/space | `{"room_id": "!xxx:domain"}` |
| `/_matrix/client/r0/rooms/{room_id}/join` | POST | Join room | `{"room_id": "!xxx:domain"}` |
| `/_matrix/client/r0/rooms/{room_id}/state/m.room.name` | PUT | Set room name | `{"event_id": "$xxx"}` |
| `/_matrix/client/r0/rooms/{room_id}/state/m.space.child/{child_id}` | PUT | Add child to space | `{"event_id": "$xxx"}` |
| `/_matrix/client/r0/rooms/{room_id}/state/m.space.parent/{parent_id}` | PUT | Set parent space | `{"event_id": "$xxx"}` |
| `/_matrix/client/r0/joined_rooms` | GET | List joined rooms | `{"joined_rooms": ["!xxx:domain"]}` |
| `/_matrix/client/v3/profile/{user_id}/displayname` | PUT | Set display name | `{}` |

#### Request/Response Examples

**POST /_matrix/client/r0/login**
```json
Request: {
  "type": "m.login.password",
  "user": "admin",
  "password": "admin_password"
}

Response: {
  "user_id": "@admin:matrix.test",
  "access_token": "syt_YWRtaW4_xxx",
  "device_id": "DEVICEID",
  "home_server": "matrix.test"
}
```

**POST /_matrix/client/v3/register**
```json
Request: {
  "username": "agent_001",
  "password": "secure_password",
  "admin": false,
  "displayname": "Agent 001"
}

Response: {
  "user_id": "@agent_001:matrix.test",
  "access_token": "syt_xxx",
  "device_id": "DEVICEID"
}

Error (user exists): {
  "errcode": "M_USER_IN_USE",
  "error": "User ID already taken"
}
```

**POST /_matrix/client/r0/createRoom**
```json
Request: {
  "name": "Agent: Agent Alpha",
  "preset": "private_chat",
  "visibility": "private",
  "creation_content": {
    "type": "m.space"  // For spaces
  }
}

Response: {
  "room_id": "!abc123:matrix.test"
}
```

#### Authentication
- **Header:** `Authorization: Bearer {access_token}`
- **Token:** Obtained via `/login` endpoint

---

## Mock Service Architecture

### Option 1: In-Process HTTP Mock Server (Recommended)

Use `aioresponses` library to intercept HTTP calls within test process.

**Pros:**
- No external services needed
- Fast test execution
- Easy to debug
- Works in CI without setup

**Cons:**
- Requires updating all integration tests
- Mock responses must be maintained

**Implementation:**
```python
# tests/mocks/letta_mock.py
from aioresponses import aioresponses

class LettaMockServer:
    def __init__(self):
        self.agents = {}
        self.mock = aioresponses()
    
    def add_agent(self, agent_id, name):
        self.agents[agent_id] = {"id": agent_id, "name": name}
    
    def setup(self):
        # Mock GET /v1/agents
        self.mock.get(
            "http://test-letta:8283/v1/agents",
            payload={"data": list(self.agents.values())}
        )
        
        # Mock GET /v1/agents/{id}
        for agent_id, data in self.agents.items():
            self.mock.get(
                f"http://test-letta:8283/v1/agents/{agent_id}",
                payload=data
            )
```

### Option 2: Docker Compose Test Services

Spin up lightweight mock servers in CI using Docker Compose.

**Pros:**
- More realistic (actual HTTP servers)
- Tests verify network interactions
- Can share mocks across test suites

**Cons:**
- Slower CI builds
- More complex setup
- Additional dependencies

**Implementation:**
```yaml
# docker-compose.test.yml
version: '3.8'
services:
  mock-letta:
    image: mockserver/mockserver
    environment:
      MOCKSERVER_INITIALIZATION_JSON_PATH: /config/letta-expectations.json
    volumes:
      - ./tests/mocks/letta-expectations.json:/config/letta-expectations.json
  
  mock-synapse:
    image: mockserver/mockserver
    environment:
      MOCKSERVER_INITIALIZATION_JSON_PATH: /config/synapse-expectations.json
    volumes:
      - ./tests/mocks/synapse-expectations.json:/config/synapse-expectations.json
```

### Option 3: Pytest Plugin with Fixtures

Create reusable fixtures that auto-mock based on test markers.

**Implementation:**
```python
# tests/conftest.py
@pytest.fixture
def mock_letta_api(mock_aiohttp_session):
    """Auto-mock Letta API for integration tests"""
    # Setup mock responses
    agents_response = {...}
    mock_aiohttp_session.get.side_effect = [agents_response, ...]
    return mock_aiohttp_session

@pytest.mark.integration
@pytest.mark.use_mock_letta
def test_something(mock_letta_api):
    # Test runs with mocked Letta API
    pass
```

---

## Recommended Implementation Plan

### Phase 1: Foundation (Sprint A.1)
**Goal:** Get integration tests passing in CI

1. **Install aioresponses**
   ```bash
   pip install aioresponses
   ```

2. **Create Mock Fixtures** (`tests/mocks/`)
   - `letta_mock.py` - Letta API mock class
   - `synapse_mock.py` - Synapse API mock class
   - `__init__.py` - Export mock classes

3. **Update conftest.py**
   - Add `@pytest.fixture` for `mock_letta_server`
   - Add `@pytest.fixture` for `mock_synapse_server`
   - Auto-activate mocks for tests with `@pytest.mark.integration`

4. **Fix Failing Tests**
   - Update 5 failing integration tests to use mocks
   - Verify all 14 integration tests pass locally
   - Push and verify CI passes

**Estimated Effort:** 4-6 hours

### Phase 2: Enhancement (Sprint A.2)
**Goal:** Make mocks more realistic and maintainable

1. **Add State Management**
   - Track created users, rooms, spaces
   - Validate requests (e.g., can't create duplicate users)
   - Return appropriate error codes

2. **Add Response Builders**
   ```python
   class SynapseResponseBuilder:
       @staticmethod
       def user_exists_error():
           return {"errcode": "M_USER_IN_USE", "error": "..."}
   ```

3. **Document Mock Behavior**
   - Create `docs/TESTING_MOCKS.md`
   - Document how to add new mock responses
   - Document mock limitations

**Estimated Effort:** 3-4 hours

### Phase 3: CI Integration (Sprint A.3)
**Goal:** Ensure CI stays green and tests are reliable

1. **Update GitHub Actions**
   ```yaml
   # .github/workflows/tests.yml
   - name: Run integration tests
     env:
       USE_MOCK_SERVICES: true
       LETTA_API_URL: http://mock-letta:8283
       MATRIX_HOMESERVER_URL: http://mock-synapse:8008
     run: ./run_tests.sh integration
   ```

2. **Add Test Markers**
   ```python
   @pytest.mark.integration
   @pytest.mark.requires_mock_letta
   @pytest.mark.requires_mock_synapse
   def test_workflow():
       pass
   ```

3. **Add Skip Logic**
   ```python
   # Skip if real services not available
   @pytest.mark.skipif(
       not os.getenv("REAL_SERVICES_AVAILABLE"),
       reason="Requires real Letta/Synapse services"
   )
   def test_with_real_services():
       pass
   ```

**Estimated Effort:** 2-3 hours

---

## Mock Data Catalog

### Letta Agent Test Data

```python
TEST_AGENTS = {
    "agent-001": {
        "id": "agent-001",
        "name": "Agent Alpha",
        "created_at": "2025-01-01T00:00:00Z"
    },
    "agent-002": {
        "id": "agent-002",
        "name": "Agent Beta",
        "created_at": "2025-01-02T00:00:00Z"
    },
    "agent-sync-test": {
        "id": "agent-sync-test",
        "name": "Sync Test Agent",
        "created_at": "2025-01-03T00:00:00Z"
    }
}
```

### Matrix User Test Data

```python
TEST_USERS = {
    "@admin:matrix.test": {
        "user_id": "@admin:matrix.test",
        "password": "admin_pass",
        "access_token": "admin_token_123"
    },
    "@agent_001:matrix.test": {
        "user_id": "@agent_001:matrix.test",
        "password": "agent_pass_001",
        "access_token": "agent_token_001"
    }
}
```

### Matrix Room Test Data

```python
TEST_ROOMS = {
    "!testroom:matrix.test": {
        "room_id": "!testroom:matrix.test",
        "name": "Test Room",
        "creator": "@admin:matrix.test",
        "members": ["@admin:matrix.test"]
    }
}
```

---

## Success Criteria

**Phase 1 Complete When:**
- [ ] All 14 integration tests pass locally
- [ ] All 14 integration tests pass in CI
- [ ] No connection errors to external services
- [ ] Test execution time < 2 minutes

**Phase 2 Complete When:**
- [ ] Mock services validate requests
- [ ] Mock services return appropriate errors
- [ ] Documentation exists for adding mocks
- [ ] Code coverage for integration tests > 80%

**Phase 3 Complete When:**
- [ ] CI workflow uses mock services
- [ ] Tests can optionally use real services (local dev)
- [ ] Skip markers work correctly
- [ ] Test failures are deterministic

---

## Files to Create/Modify

### New Files
- `tests/mocks/__init__.py`
- `tests/mocks/letta_mock.py`
- `tests/mocks/synapse_mock.py`
- `tests/mocks/test_data.py`
- `docs/TESTING_MOCKS.md`

### Modified Files
- `tests/conftest.py` - Add mock fixtures
- `tests/integration/test_multi_agent_workflow.py` - Use mocks
- `.github/workflows/tests.yml` - Configure for mocks
- `requirements.txt` - Add `aioresponses`
- `pytest.ini` - Add test markers

---

## Next Steps for Implementation

1. Review and approve this plan
2. Create GitHub issue: "Implement mock services for integration tests"
3. Break down into subtasks:
   - Task 1: Install aioresponses and create mock classes
   - Task 2: Update conftest.py with fixtures
   - Task 3: Fix 5 failing integration tests
   - Task 4: Update CI workflow
   - Task 5: Document mock usage
4. Assign to sprint and begin work

---

## References

- [aioresponses Documentation](https://github.com/pnuckowski/aioresponses)
- [pytest Fixtures](https://docs.pytest.org/en/stable/fixture.html)
- [Matrix Client-Server API](https://spec.matrix.org/v1.5/client-server-api/)
- Integration test file: `tests/integration/test_multi_agent_workflow.py`
- Current test failures: CI run #19401341577

---

**Last Updated:** 2025-11-16  
**Owner:** Development Team  
**Status:** Ready for Implementation
