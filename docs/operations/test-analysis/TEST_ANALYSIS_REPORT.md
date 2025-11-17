# Test Analysis Report: test_discover_and_create_agents

## Executive Summary
The test `test_discover_and_create_agents` (lines 46-113 in test_multi_agent_workflow.py) is testing agent discovery and Matrix user creation. However, **the test has a critical architectural mismatch**: it mocks HTTP calls incorrectly, and doesn't account for all the HTTP endpoints that will actually be called during execution.

---

## 1. HTTP ENDPOINTS CALLED

### A. Letta API Endpoints (Hardcoded to port 8289)
**Location**: `src/core/agent_user_manager.py:171`

1. **GET http://192.168.50.90:8289/v1/agents?limit=100**
   - Called by: `get_letta_agents()`
   - Purpose: Fetch list of agents with pagination
   - Expected Response:
     ```json
     {
       "data": [
         {"id": "agent-001", "name": "Agent Alpha"},
         {"id": "agent-002", "name": "Agent Beta"}
       ]
     }
     ```
   - Headers Required: `Authorization: Bearer lettaSecurePass123`
   - Handles Pagination: Uses `after` cursor parameter

2. **GET http://192.168.50.90:8289/v1/agents/{agent_id}/messages**
   - Called by: `room_manager.import_recent_history()` 
   - Purpose: Fetch recent conversation history for UI continuity
   - Expected Response: Array of message objects or dict with "items" key
   - Status Codes: 200 (success), other (fails silently with warning)

### B. Matrix Synapse API Endpoints (Hardcoded to {homeserver_url})

1. **POST /_matrix/client/r0/login**
   - Called by:
     - `user_manager.get_admin_token()` (line 48)
     - `room_manager.auto_accept_invitations_with_tracking()` (line 300)
     - `room_manager.create_or_update_agent_room()` (line 179)
   - Purpose: Login to obtain access tokens
   - Request Body:
     ```json
     {
       "type": "m.login.password",
       "user": "username_localpart",
       "password": "password"
     }
     ```
   - Expected Response (200):
     ```json
     {
       "access_token": "syt_token_value",
       "user_id": "@username:matrix.domain",
       "device_id": "device_id",
       "home_server": "matrix.domain"
     }
     ```

2. **POST /_matrix/client/v3/register**
   - Called by: `user_manager.create_matrix_user()` (line 153)
   - Purpose: Create new Matrix user account
   - Request Body:
     ```json
     {
       "username": "agent_username",
       "password": "generated_password",
       "auth": {"type": "m.login.dummy"}
     }
     ```
   - Expected Response (200 or 400):
     ```json
     {
       "user_id": "@username:matrix.domain",
       "access_token": "token_from_registration",
       "device_id": "device_id"
     }
     ```
   - Error: 400 with `errcode: "M_USER_IN_USE"` if user already exists

3. **PUT /_matrix/client/v3/profile/{user_id}/displayname**
   - Called by: `user_manager.set_user_display_name()` (line 208)
   - Purpose: Set display name for newly created user
   - Headers: `Authorization: Bearer {user_token}`
   - Request Body:
     ```json
     {
       "displayname": "Agent Name"
     }
     ```
   - Expected Response (200):
     ```json
     {}
     ```

4. **PUT /_matrix/client/r0/profile/{user_id}/displayname**
   - Called by: `user_manager.update_display_name()` (line 245)
   - Purpose: Update display name for existing user (admin operation)
   - Headers: `Authorization: Bearer {admin_token}`
   - Request Body:
     ```json
     {
       "displayname": "New Name"
     }
     ```
   - Expected Response (200):
     ```json
     {}
     ```

5. **POST /_matrix/client/r0/createRoom**
   - Called by: `room_manager.create_or_update_agent_room()` (line 204)
   - Purpose: Create dedicated room for agent communication
   - Headers: `Authorization: Bearer {agent_token}`
   - Request Body:
     ```json
     {
       "name": "Agent Name - Letta Agent Chat",
       "topic": "Private chat with Letta agent: Agent Name",
       "preset": "trusted_private_chat",
       "invite": ["@admin:matrix.oculair.ca", "@matrixadmin:...", "@letta:..."],
       "is_direct": false,
       "initial_state": [...]
     }
     ```
   - Expected Response (200):
     ```json
     {
       "room_id": "!newroom123:matrix.domain"
     }
     ```

6. **POST /_matrix/client/r0/rooms/{room_id}/join**
   - Called by: `room_manager.auto_accept_invitations_with_tracking()` (line 328)
   - Purpose: Accept room invitation for admin and letta users
   - Headers: `Authorization: Bearer {user_token}`
   - Request Body: `{}` (empty)
   - Expected Response (200):
     ```json
     {
       "room_id": "!room_id:matrix.domain"
     }
     ```
   - Status Code 403: "already in the room" or other error

---

## 2. COMPLETE REQUEST SEQUENCE FOR test_discover_and_create_agents

When the test calls:
```python
agents = await agent_manager.get_letta_agents()
for agent in agents:
    await agent_manager.create_user_for_agent(agent)
```

### Request Sequence:

1. **First GET request**: Letta `/v1/agents`
   - Returns: `{"data": [{"id": "agent-001"}, {"id": "agent-002"}]}`

2. **For agent-001**:
   - **POST /login** (admin user) → get admin token
   - **POST /register** → create Matrix user `@agent_001:matrix.oculair.ca`
   - **PUT /profile/{user_id}/displayname** → set display name (using user's own token from register response)
   - **POST /login** (as agent user) → get agent token
   - **POST /createRoom** → create dedicated room
   - **POST /login** (admin) → get admin token to accept invitation
   - **POST /rooms/{room_id}/join** → admin joins room
   - **POST /login** (letta user) → get letta token to accept invitation
   - **POST /rooms/{room_id}/join** → letta joins room
   - **GET /agents/{agent_id}/messages** → fetch history (in import_recent_history)

3. **For agent-002**: (Same sequence as agent-001)

---

## 3. CURRENT MOCKING APPROACH (Test)

### What the test currently does:

```python
def test_discover_and_create_agents(self, agent_manager, mock_aiohttp_session):
    # Mock setup
    agents_response = AsyncMock()
    agents_response.status = 200
    agents_response.json = AsyncMock(return_value={"data": [...]})
    
    # Mock GET/POST/PUT calls
    mock_aiohttp_session.get = Mock(side_effect=[agents_response, detail_1, detail_2])
    mock_aiohttp_session.post = Mock(return_value=token_response)
    mock_aiohttp_session.put = Mock(return_value=create_response)
    
    # Patch the global session
    with patch('src.core.agent_user_manager.get_global_session', 
               return_value=mock_aiohttp_session):
        agents = await agent_manager.get_letta_agents()
        for agent in agents:
            await agent_manager.create_user_for_agent(agent)
```

### Problems with Current Approach:

1. **Session Creation Issue**: `get_letta_agents()` creates its own session:
   ```python
   async with aiohttp.ClientSession() as session:
       async with session.get(agents_endpoint, headers=headers) as response:
   ```
   - This is NOT the global session, so the patch has no effect

2. **Missing Side Effects**: The test sets:
   - `mock_aiohttp_session.get = Mock(side_effect=[...])`  - Only 3 responses, but there will be many more GET calls
   - `mock_aiohttp_session.post = Mock(return_value=token_response)` - Returns same response for ALL POST calls
   - `mock_aiohttp_session.put = Mock(return_value=create_response)` - Only 1 PUT response

3. **Missing Context Manager Setup**: The responses need proper `__aenter__` and `__aexit__` for async context managers

4. **Agent Details Not Fetched**: The test has `detail_response_1` and `detail_response_2` mocked but the actual code in `get_letta_agents()` (line 207) doesn't fetch individual agent details - it just returns what's in the "data" array

5. **No Matrix-Specific Mocking**: The test doesn't account for:
   - User creation (register endpoint)
   - Display name updates
   - Room creation
   - Join/accept invitation flows

---

## 4. FIXTURE DEPENDENCIES

### Fixtures Used by Test:
1. **`agent_manager`** (defined in test file, line 29):
   ```python
   @pytest.fixture
   async def agent_manager(mock_config, tmp_path):
       manager = AgentUserManager(mock_config)
       manager.mappings_file = str(tmp_path / "test_mappings.json")
       await manager.load_existing_mappings()
       return manager
   ```
   - Depends on: `mock_config`, `tmp_path`

2. **`mock_config`** (from tests/conftest.py, line 24):
   - Provides: homeserver_url, letta_api_url, username, password, etc.

3. **`mock_aiohttp_session`** (from tests/conftest.py, line 59):
   ```python
   @pytest.fixture
   def mock_aiohttp_session():
       """Mock aiohttp ClientSession for HTTP requests"""
       session = AsyncMock(spec=aiohttp.ClientSession)
       response = AsyncMock()
       response.status = 200
       response.json = AsyncMock(return_value={"success": True})
       
       session.post = Mock(return_value=response)
       session.get = Mock(return_value=response)
       # ...
   ```
   - Current Implementation: **Too generic, not URL-aware**

### What's Missing:
- There's NO `mock_aiohttp_session` in integration/conftest.py
- The main conftest.py has it, but it's generic and not suitable for complex workflows
- There's NO `patched_http_session` fixture being used, which is more sophisticated

---

## 5. AgentUserManager METHOD CALLS

### Methods Called in Test:

1. **`agent_manager.get_letta_agents()`** (line 166 in agent_user_manager.py):
   - Creates own `aiohttp.ClientSession()`
   - Makes GET request to `http://192.168.50.90:8289/v1/agents?limit=100`
   - Handles pagination with cursor
   - Returns: `List[dict]` with `id` and `name`

2. **`agent_manager.create_user_for_agent(agent)`** (line 435):
   - Calls `self.generate_username()` → user_manager.generate_username()
   - Calls `self.create_matrix_user()` → user_manager.create_matrix_user()
     - Creates own session, POSTs to `/register`
     - Returns `True/False`
   - Calls `self.update_display_name()` → user_manager.update_display_name()
     - Calls `self.get_admin_token()` first (creates session, POST /login)
     - Then PUTs to `/profile/{user_id}/displayname`
   - Calls `self.create_or_update_agent_room()` → room_manager.create_or_update_agent_room()
     - Creates own session for agent login (POST /login)
     - POSTs to `/createRoom`
     - Calls `self.auto_accept_invitations_with_tracking()`
       - Creates own session for each user login
       - POSTs to `/login` and `/rooms/{room_id}/join` for each user
     - Calls `self.import_recent_history()`
       - GETs from Letta `/agents/{id}/messages`
       - Uses matrix-nio client to send messages

### Key Insight:
Every manager method creates its own `aiohttp.ClientSession()` context, **not using the global session**. This means patching `get_global_session` has NO EFFECT on actual HTTP calls.

---

## 6. ROOT CAUSE ANALYSIS

### Why The Test Is Likely Failing:

1. **Patch Not Applied**: The code doesn't use `get_global_session()` where HTTP calls are made
   - `get_letta_agents()` creates its own session (line 187)
   - `user_manager.create_matrix_user()` creates its own session (line 165)
   - All other methods create their own sessions

2. **Real HTTP Calls Attempted**: Without mocking at the right level, the test tries to make real HTTP calls to:
   - `http://192.168.50.90:8289/v1/agents` (doesn't exist in test environment)
   - `http://test-synapse:8008/` endpoints (mock server from config)

3. **Async Context Manager Issues**: Response objects need proper `__aenter__` and `__aexit__` implementation

4. **Incomplete Mock Setup**: Even if the patch worked, the test only mocks 5 GET calls and 1 POST return value, but many more are needed

---

## 7. CONFTEST FIXTURES ANALYSIS

### tests/conftest.py - Current State:

**`mock_aiohttp_session` (lines 59-78)**:
- Generic fixture - returns same response for all calls
- No URL-based routing
- Missing proper context manager setup for some calls

**NOT SUITABLE** for this test because:
- Doesn't handle URL-specific responses
- Doesn't account for multiple different endpoints
- Mocks are too generic

**Integration conftest.py**:
- Has `mock_http_session` (lines 68-195) - More sophisticated with URL routing!
- Has `patched_http_session` (lines 199-227) - Better fixture!
- These are NOT being used in the test

---

## 8. WHAT NEEDS TO BE FIXED

### Option 1: Use `patched_http_session` (RECOMMENDED)
The integration/conftest.py already has a better fixture that:
- Routes responses based on URL patterns
- Handles multiple HTTP methods correctly
- Patches all relevant modules

### Option 2: Create Test-Specific Mock
Implement comprehensive mock with:
- URL-aware routing (distinguish Letta vs Matrix endpoints)
- Proper response sequences for login → user creation → room creation → join
- Session factory mock that returns configured session for each call

### Option 3: Patch at Session Creation Level
Instead of patching `get_global_session()`, patch:
- `aiohttp.ClientSession()` constructor directly
- Make it return the mock session instance

---

## RECOMMENDATIONS FOR CONFTEST.PY

### Add to tests/integration/conftest.py:

```python
@pytest.fixture
def mock_letta_agents_list():
    """Mock Letta agents list response"""
    return {
        "data": [
            {"id": "agent-001", "name": "Agent Alpha"},
            {"id": "agent-002", "name": "Agent Beta"}
        ]
    }

@pytest.fixture
def comprehensive_mock_session(
    mock_config,
    mock_letta_agents_list
):
    """
    Comprehensive mock session for multi-agent workflow tests
    Handles all HTTP calls without requiring real endpoints
    """
    session = AsyncMock()
    
    # Track call sequences
    get_call_count = [0]
    post_call_count = [0]
    put_call_count = [0]
    
    async def mock_get(url, **kwargs):
        call_num = get_call_count[0]
        get_call_count[0] += 1
        
        if "agents" in url.lower():
            response = AsyncMock()
            response.status = 200
            response.json = AsyncMock(return_value=mock_letta_agents_list)
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=None)
            return response
        
        # Default response
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(return_value={})
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)
        return response
    
    async def mock_post(url, **kwargs):
        response = AsyncMock()
        
        if "login" in url.lower():
            response.status = 200
            response.json = AsyncMock(return_value={
                "access_token": f"token_{post_call_count[0]}",
                "user_id": "@test:matrix.test",
                "device_id": "device123"
            })
        elif "register" in url.lower():
            response.status = 200
            response.json = AsyncMock(return_value={
                "access_token": "user_token_123",
                "user_id": "@agent:matrix.test",
                "device_id": "device123"
            })
        elif "createroom" in url.lower():
            response.status = 200
            response.json = AsyncMock(return_value={
                "room_id": f"!room{post_call_count[0]}:matrix.test"
            })
        elif "join" in url.lower():
            response.status = 200
            response.json = AsyncMock(return_value={
                "room_id": "!room:matrix.test"
            })
        else:
            response.status = 200
            response.json = AsyncMock(return_value={})
        
        post_call_count[0] += 1
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)
        return response
    
    async def mock_put(url, **kwargs):
        response = AsyncMock()
        response.status = 200
        response.json = AsyncMock(return_value={})
        response.text = AsyncMock(return_value="OK")
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)
        put_call_count[0] += 1
        return response
    
    session.get = AsyncMock(side_effect=mock_get)
    session.post = AsyncMock(side_effect=mock_post)
    session.put = AsyncMock(side_effect=mock_put)
    session.closed = False
    
    return session


@pytest.fixture
def patched_aiohttp_for_tests(comprehensive_mock_session, monkeypatch):
    """
    Patch aiohttp.ClientSession to use mock session
    This catches all session creations, not just global ones
    """
    class MockSessionFactory:
        async def __aenter__(self):
            return comprehensive_mock_session
        
        async def __aexit__(self, *args):
            pass
        
        def __call__(self, *args, **kwargs):
            return self
    
    # Patch aiohttp.ClientSession constructor
    mock_factory = MockSessionFactory()
    monkeypatch.setattr('aiohttp.ClientSession', mock_factory)
    
    return comprehensive_mock_session
```

---

## SUMMARY TABLE

| Aspect | Current | Issue | Needed |
|--------|---------|-------|--------|
| **Session Patching** | `get_global_session()` | Not used by code | Patch `aiohttp.ClientSession()` constructor |
| **Mock Session** | Generic AsyncMock | Doesn't handle multiple endpoints | URL-aware mock with routing |
| **Response Sequences** | Fixed 3 GET responses | Not enough for full workflow | Dynamic response based on call count |
| **Context Managers** | Partial setup | Missing for some responses | All responses need `__aenter__/__aexit__` |
| **Fixture Location** | tests/conftest.py | Separate from integration tests | tests/integration/conftest.py |
| **Fixture Quality** | Basic | Too generic | Comprehensive URL-routing |

---

## CONCLUSION

The test `test_discover_and_create_agents` will fail because:

1. **Patch doesn't apply**: Code creates own sessions, not using global session
2. **Mock is incomplete**: Only 5 responses for 20+ HTTP calls
3. **Real calls attempted**: Without proper mocking, test tries real HTTP
4. **Complex flow not mocked**: Multi-step workflows (login→register→create room→join) not properly sequenced

**Solution**: Create a comprehensive session mock at the `aiohttp.ClientSession` level that routes requests by URL pattern, or use the existing `patched_http_session` from integration/conftest.py.
