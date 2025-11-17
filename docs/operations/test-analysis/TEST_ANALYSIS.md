# Analysis Report: test_sync_agents_to_users

## Overview
This report analyzes the test `test_sync_agents_to_users` in `tests/integration/test_multi_agent_workflow.py` (lines 116-157) and identifies HTTP mocking gaps.

---

## 1. HTTP ENDPOINTS REQUIRED

### 1.1 Agent Discovery
- **GET** `http://192.168.50.90:8289/v1/agents?limit=100`
  - Returns: List of agents with id and name
  - Called by: `get_letta_agents()`
  - Response format:
    ```json
    {
      "data": [{"id": "agent-sync-test", "name": "Sync Test Agent"}]
    }
    ```

### 1.2 Admin Authentication  
- **POST** `http://test-synapse:8008/_matrix/client/r0/login`
  - Request: `{"type": "m.login.password", "user": "admin", "password": "admin_password"}`
  - Returns: Access token
  - Called by: `get_admin_token()` in `user_manager.py`
  - Response:
    ```json
    {"access_token": "admin_token_xyz", "user_id": "@admin:matrix.oculair.ca"}
    ```

### 1.3 User Registration
- **POST** `http://test-synapse:8008/_matrix/client/v3/register`
  - Request: `{"username": "agent_sync_test", "password": "...", "auth": {"type": "m.login.dummy"}}`
  - Returns: User creation confirmation + access_token
  - Called by: `create_matrix_user()`
  - Response: 
    ```json
    {"user_id": "@agent_sync_test:matrix.oculair.ca", "access_token": "user_token"}
    ```

### 1.4 User Check
- **POST** `http://test-synapse:8008/_matrix/client/v3/login`
  - Called by: `check_user_exists()` (via dummy password test)
  - Response: 403 if user exists, 404 if not

### 1.5 Display Name Update
- **PUT** `http://test-synapse:8008/_matrix/client/r0/profile/@agent_sync_test:matrix.oculair.ca/displayname`
  - Request: `{"displayname": "Sync Test Agent"}`
  - Called by: `update_display_name()`
  - Response: Success (200)

### 1.6 Space Creation (from space_manager)
- **POST** `http://test-synapse:8008/_matrix/client/v3/createRoom`
  - Request: Room creation with `"creation_content": {"type": "m.space"}`
  - Returns: Space ID
  - Called by: `space_manager.create_letta_agents_space()`

### 1.7 Room Creation (from room_manager)
- **POST** `http://test-synapse:8008/_matrix/client/v3/createRoom`
  - Request: Standard room creation
  - Returns: Room ID
  - Called by: `create_or_update_agent_room()`

---

## 2. REQUEST PATTERNS

### 2.1 Agents List Request
```
GET http://192.168.50.90:8289/v1/agents?limit=100
Headers:
  Authorization: Bearer lettaSecurePass123
  Content-Type: application/json
```

### 2.2 User Creation Request
```
POST http://test-synapse:8008/_matrix/client/v3/register
Headers:
  Content-Type: application/json
Body:
{
  "username": "agent_sync_test",
  "password": "generated_password_here",
  "auth": {"type": "m.login.dummy"}
}
```

### 2.3 Display Name Update
```
PUT http://test-synapse:8008/_matrix/client/r0/profile/@agent_sync_test:matrix.oculair.ca/displayname
Headers:
  Authorization: Bearer admin_token_xyz
  Content-Type: application/json
Body:
{
  "displayname": "Sync Test Agent"
}
```

---

## 3. EXPECTED RESPONSES

| Endpoint | Status | Format |
|----------|--------|--------|
| GET /v1/agents | 200 | `{"data": [...]}`  |
| POST /login | 200 | `{"access_token": "...", "user_id": "..."}` |
| POST /register | 200 | `{"user_id": "...", "access_token": "..."}` |
| POST /createRoom | 200 | `{"room_id": "..."}` |
| PUT /displayname | 200 | `{}` |
| POST /check user | 403 or 404 | Error response |

---

## 4. DEPENDENCIES & FIXTURES

### Current Fixtures Used
- `agent_manager`: Creates AgentUserManager with temp storage
- `mock_aiohttp_session`: Basic mock session (defined in tests/conftest.py)
- `mock_config`: Basic config with URLs

### Issues with Current Setup
1. **Incomplete mock_aiohttp_session**: The fixture provides generic mocks without URL-specific responses
2. **Missing patch for global_session**: `get_global_session()` is called but not properly patched
3. **No space_manager mocking**: Space creation calls are not mocked
4. **No room_manager mocking**: Room creation calls are not mocked
5. **Order-dependent mocks**: `mock_aiohttp_session.get` uses `side_effect` with list - order matters!

---

## 5. CURRENT MOCKING APPROACH

### What's Implemented
```python
# Lines 119-149 in test_sync_agents_to_users
agents_response = AsyncMock()
agents_response.status = 200
agents_response.json = AsyncMock(return_value={"data": [{"id": "agent-sync-test"}]})
agents_response.__aenter__ = AsyncMock(return_value=agents_response)
agents_response.__aexit__ = AsyncMock(return_value=None)

mock_aiohttp_session.get = Mock(side_effect=[agents_response, detail_response])
mock_aiohttp_session.post = Mock(return_value=token_response)
mock_aiohttp_session.put = Mock(return_value=create_response)
```

### Problems
1. `get_global_session` is NOT patched - the test patches it locally but managers call it directly
2. `detail_response` is defined but agents in `get_letta_agents()` already have names - detail call may not happen
3. No mocks for intermediate calls like:
   - `check_user_exists()` login attempt
   - `set_user_display_name()` after user creation
   - Space creation
   - Room creation with agent user
   - Admin invitations
4. Missing response for `create_response` (no `json()` method defined on line 142)

---

## 6. IDENTIFIED ISSUES

### Critical Issues
1. **Missing global_session patch location**: The test patches `get_global_session` inside a `with` block for one call sequence, but `ensure_core_users_exist()` and other methods create their own sessions

   ```python
   # Line 101 - only patches inside THIS with block
   with patch('src.core.agent_user_manager.get_global_session', ...):
       # But sync_agents_to_users calls get_admin_token which creates its own session!
   ```

2. **Incomplete response mocks**: 
   - `create_response` (line 142) is missing `.json()` method - will fail if code tries to parse response
   - `detail_response` is defined but may not be called correctly

3. **Manager initialization bypasses mocks**:
   - `AgentUserManager.__init__` creates `space_manager`, `user_manager`, `room_manager`
   - These managers will call `get_global_session()` which is NOT mocked properly

### Test Assertion Issues
- Test only checks if agent is in mappings with correct name
- Doesn't verify HTTP calls actually occurred
- Doesn't check if user was actually "created" (mapping.created flag)
- Doesn't check if room was created

---

## 7. COMPARISON: test_discover_and_create_agents vs test_sync_agents_to_users

| Aspect | test_discover_and_create_agents | test_sync_agents_to_users |
|--------|----------------------------------|--------------------------|
| **Method Called** | `get_letta_agents()` + `create_user_for_agent()` in loop | `sync_agents_to_users()` unified method |
| **Scope** | Manual step-by-step | End-to-end orchestration |
| **HTTP Calls** | ~6 (GET agents, GET details x2, POST token, PUT user) | ~15+ (includes space, room, invitations, display names) |
| **Mock Completeness** | Mostly sufficient for called methods | INSUFFICIENT - missing many nested calls |
| **Assertions** | Simple mapping count checks | Only checks mapping exists + agent name |

**Key difference**: `test_sync_agents_to_users()` calls a method that does much more than the sum of steps in the first test. It also calls `ensure_core_users_exist()`, space creation, room creation, and invitation handling - all of which are NOT mocked in the current test.

---

## 8. ROOT CAUSE OF TEST FAILURES

The test will fail at one of these points:

1. **`ensure_core_users_exist()` call** (line 325 in agent_user_manager.py):
   - Calls `user_manager.get_admin_token()` 
   - `get_admin_token()` creates its own `aiohttp.ClientSession()` (not mocked!)
   - Will try to hit real Matrix API

2. **Space creation** (lines 333-348):
   - Calls `space_manager.create_letta_agents_space()`
   - Makes actual POST requests to create space
   - Not mocked - will fail

3. **Room creation** (lines 405-408):
   - Calls `create_or_update_agent_room()`
   - Makes actual POST requests to create room
   - Not mocked - will fail

4. **Display name update** (lines 391-397):
   - Calls `update_display_name()`
   - Requires working admin token
   - Will fail due to unmocked token fetch

---

## 9. RECOMMENDATIONS FOR conftest.py

### Add to tests/integration/conftest.py:

```python
@pytest.fixture
async def mock_sync_agents_responses(mock_aiohttp_session):
    """
    Complete mock responses for sync_agents_to_users workflow
    
    This fixture should handle ALL HTTP calls made during sync:
    - Agent discovery
    - User check/creation
    - Admin token generation  
    - Space creation
    - Room creation
    - Display name updates
    - Invitation handling
    """
    # Define all response mocks with proper context manager support
    
    # 1. Agents list response
    agents_response = AsyncMock()
    agents_response.status = 200
    agents_response.json = AsyncMock(return_value={
        "data": [{"id": "agent-sync-test", "name": "Sync Test Agent"}]
    })
    agents_response.__aenter__ = AsyncMock(return_value=agents_response)
    agents_response.__aexit__ = AsyncMock(return_value=None)
    
    # 2. Admin login response
    login_response = AsyncMock()
    login_response.status = 200
    login_response.json = AsyncMock(return_value={
        "access_token": "mock_admin_token_123",
        "user_id": "@admin:matrix.oculair.ca"
    })
    login_response.__aenter__ = AsyncMock(return_value=login_response)
    login_response.__aexit__ = AsyncMock(return_value=None)
    
    # 3. User check response (403 = user exists, for core users)
    check_response = AsyncMock()
    check_response.status = 403
    check_response.json = AsyncMock(return_value={
        "errcode": "M_FORBIDDEN"
    })
    check_response.__aenter__ = AsyncMock(return_value=check_response)
    check_response.__aexit__ = AsyncMock(return_value=None)
    
    # 4. User creation response
    user_create_response = AsyncMock()
    user_create_response.status = 200
    user_create_response.json = AsyncMock(return_value={
        "user_id": "@agent_sync_test:matrix.oculair.ca",
        "access_token": "mock_user_token_456"
    })
    user_create_response.__aenter__ = AsyncMock(return_value=user_create_response)
    user_create_response.__aexit__ = AsyncMock(return_value=None)
    
    # 5. Space creation response
    space_response = AsyncMock()
    space_response.status = 200
    space_response.json = AsyncMock(return_value={
        "room_id": "!mock_letta_space:matrix.oculair.ca"
    })
    space_response.__aenter__ = AsyncMock(return_value=space_response)
    space_response.__aexit__ = AsyncMock(return_value=None)
    
    # 6. Room creation response
    room_response = AsyncMock()
    room_response.status = 200
    room_response.json = AsyncMock(return_value={
        "room_id": "!mock_agent_room:matrix.oculair.ca"
    })
    room_response.__aenter__ = AsyncMock(return_value=room_response)
    room_response.__aexit__ = AsyncMock(return_value=None)
    
    # 7. Display name update response
    displayname_response = AsyncMock()
    displayname_response.status = 200
    displayname_response.json = AsyncMock(return_value={})
    displayname_response.__aenter__ = AsyncMock(return_value=displayname_response)
    displayname_response.__aexit__ = AsyncMock(return_value=None)
    
    # 8. Generic success response
    success_response = AsyncMock()
    success_response.status = 200
    success_response.json = AsyncMock(return_value={})
    success_response.text = AsyncMock(return_value="OK")
    success_response.__aenter__ = AsyncMock(return_value=success_response)
    success_response.__aexit__ = AsyncMock(return_value=None)
    
    # Create response router
    def mock_post(url, **kwargs):
        """Route POST requests to appropriate mock response"""
        url_lower = url.lower()
        if "login" in url_lower:
            return login_response
        elif "register" in url_lower or "v3/users" in url_lower:
            return user_create_response
        elif "createroom" in url_lower:
            # Check if creating space (has creation_content.type = m.space)
            json_data = kwargs.get('json', {})
            if json_data.get('creation_content', {}).get('type') == 'm.space':
                return space_response
            else:
                return room_response
        else:
            return success_response
    
    def mock_get(url, **kwargs):
        """Route GET requests"""
        url_lower = url.lower()
        if "agents" in url_lower:
            return agents_response
        else:
            return success_response
    
    def mock_put(url, **kwargs):
        """Route PUT requests"""
        url_lower = url.lower()
        if "displayname" in url_lower:
            return displayname_response
        else:
            return success_response
    
    mock_aiohttp_session.post = Mock(side_effect=mock_post)
    mock_aiohttp_session.get = Mock(side_effect=mock_get)
    mock_aiohttp_session.put = Mock(side_effect=mock_put)
    mock_aiohttp_session.delete = Mock(return_value=success_response)
    
    return mock_aiohttp_session


@pytest.fixture
def patch_all_sessions(mock_sync_agents_responses):
    """
    Patch get_global_session in ALL modules that use it
    
    Critical: Managers create their own ClientSession() objects
    This fixture ensures they all use the mocked session
    """
    modules = [
        'src.core.agent_user_manager',
        'src.core.user_manager',
        'src.core.space_manager',
        'src.core.room_manager'
    ]
    
    async def mock_get_session():
        return mock_sync_agents_responses
    
    patchers = []
    for module in modules:
        patcher = patch(f'{module}.get_global_session', side_effect=mock_get_session)
        patcher.start()
        patchers.append(patcher)
    
    # Also patch aiohttp.ClientSession to return our mock
    session_patcher = patch('aiohttp.ClientSession', return_value=mock_sync_agents_responses)
    session_patcher.start()
    patchers.append(session_patcher)
    
    yield mock_sync_agents_responses
    
    for patcher in patchers:
        patcher.stop()
```

### Updated test fixture usage:

```python
@pytest.fixture
async def agent_manager(mock_config, tmp_path, patch_all_sessions, integration_env_setup):
    """Create AgentUserManager with COMPLETE mocking"""
    manager = AgentUserManager(mock_config)
    manager.mappings_file = str(tmp_path / "test_mappings.json")
    await manager.load_existing_mappings()
    return manager
```

---

## 10. SUMMARY TABLE

| Category | Current Status | Needed |
|----------|----------------|--------|
| **Agents GET** | Mocked | ✓ Keep |
| **User creation POST** | Partially mocked | ✓ Complete with response.json() |
| **Admin token** | Not mocked | ✓ Add login mock |
| **Display name PUT** | Not mocked | ✓ Add PUT mock |
| **Space creation** | Not mocked | ✓ Add POST space mock |
| **Room creation** | Not mocked | ✓ Add POST room mock |
| **Session patching** | Incomplete | ✓ Patch all modules |
| **Context manager support** | Inconsistent | ✓ Ensure all have __aenter__/__aexit__ |
| **Error handling** | Not tested | ✓ Add 400/500 response scenarios |

---

