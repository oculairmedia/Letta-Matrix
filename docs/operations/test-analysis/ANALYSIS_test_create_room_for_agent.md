# Detailed Analysis: test_create_room_for_agent

**File**: `tests/integration/test_multi_agent_workflow.py:169-208`  
**Status**: Currently insufficient mocking - test will fail

---

## Executive Summary

The test `test_create_room_for_agent` verifies room creation for Letta agents but has incomplete mocking that causes test failures. The actual method makes **9 different HTTP calls** across multiple endpoints, but the test only mocks **2 responses**.

---

## Call Chain & HTTP Endpoints

### Full Execution Flow

```
test_create_room_for_agent()
  ↓
agent_manager.create_or_update_agent_room("agent-room-test")
  ↓
room_manager.create_or_update_agent_room(agent_id, mapping)
  ├─ [1] POST /_matrix/client/r0/login                          (agent login)
  ├─ [2] POST /_matrix/client/r0/createRoom                     (create room)
  ├─ auto_accept_invitations_with_tracking(room_id, mapping)
  │  ├─ [3] POST /_matrix/client/r0/login                       (admin login)
  │  ├─ [4] POST /_matrix/client/r0/rooms/{roomId}/join        (admin join)
  │  ├─ [5] POST /_matrix/client/r0/login                       (letta bot login)
  │  └─ [6] POST /_matrix/client/r0/rooms/{roomId}/join        (letta join)
  ├─ space_manager.add_room_to_space(room_id, agent_name)
  │  ├─ [7] PUT /_matrix/client/r0/rooms/{spaceId}/state/m.space.child/{roomId}
  │  └─ [8] PUT /_matrix/client/r0/rooms/{roomId}/state/m.space.parent/{spaceId}
  └─ import_recent_history(agent_id, ...)
     └─ [9] GET /v1/agents/{agentId}/messages               (Letta API)
```

---

## HTTP Endpoints Required

| # | Method | Endpoint | Purpose | Status |
|---|--------|----------|---------|--------|
| 1 | POST | `/_matrix/client/r0/login` | Agent login | ✓ Mocked |
| 2 | POST | `/_matrix/client/r0/createRoom` | Room creation | ✓ Mocked |
| 3 | POST | `/_matrix/client/r0/login` | Admin login | ✗ Missing |
| 4 | POST | `/_matrix/client/r0/rooms/{roomId}/join` | Admin join | ✗ Missing |
| 5 | POST | `/_matrix/client/r0/login` | Letta bot login | ✗ Missing |
| 6 | POST | `/_matrix/client/r0/rooms/{roomId}/join` | Letta join | ✗ Missing |
| 7 | PUT | `/_matrix/client/r0/rooms/{spaceId}/state/m.space.child/{roomId}` | Add to space | ✗ Missing |
| 8 | PUT | `/_matrix/client/r0/rooms/{roomId}/state/m.space.parent/{spaceId}` | Parent space | ✗ Missing |
| 9 | GET | `/v1/agents/{agentId}/messages` | History import | ✗ Missing |

---

## Current Mocking Issues

### Issue 1: Insufficient POST Responses
**Current code (line 198)**:
```python
mock_aiohttp_session.post = Mock(side_effect=[login_response, room_response])
```

**Problem**: Only 2 responses provided, but **6 POST requests** are made
- Call 1: Login as agent → ✓ Returns `login_response`
- Call 2: Create room → ✓ Returns `room_response`
- Call 3: Login as admin → ✗ **StopIteration Error!**

**Error**: `IndexError` or `StopIteration` when auto_accept_invitations runs

### Issue 2: No PUT Mocking
Room manager calls `space_manager.add_room_to_space()` (line 259), which makes:
- `PUT /_matrix/client/r0/rooms/{spaceId}/state/m.space.child/{roomId}`
- `PUT /_matrix/client/r0/rooms/{roomId}/state/m.space.parent/{spaceId}`

**Current**: `mock_aiohttp_session.put` not configured  
**Error**: AttributeError or no mock return value

### Issue 3: No GET Mocking
History import calls (line 269):
```python
await self.import_recent_history(agent_id, agent_username, agent_password, room_id)
```

Which makes: `GET http://192.168.50.90:8289/v1/agents/{agentId}/messages`

**Current**: `mock_aiohttp_session.get` not configured  
**Error**: Missing mock or incorrect response

### Issue 4: Fragile side_effect List
Using positional `side_effect=[...]` breaks if:
- Call order changes in the codebase
- Different code paths are taken
- Additional calls are added

**Better approach**: URL-based routing with pattern matching

### Issue 5: Incomplete Context Manager Setup
The test manually sets `__aenter__` and `__aexit__` for specific responses, but auto_accept_invitations creates new session contexts that aren't mocked properly.

---

## Request & Response Details

### [1] Agent Login
**URL**: `POST {homeserver_url}/_matrix/client/r0/login`

**Request**:
```json
{
  "type": "m.login.password",
  "user": "agent_room_test",
  "password": "test_pass"
}
```

**Response** (200):
```json
{
  "access_token": "agent_token",
  "user_id": "@agent_room_test:matrix.test",
  "device_id": "DEVICE123",
  "home_server": "matrix.test"
}
```

### [2] Room Creation
**URL**: `POST {homeserver_url}/_matrix/client/r0/createRoom`

**Headers**: `Authorization: Bearer agent_token`

**Request**:
```json
{
  "name": "Room Test Agent - Letta Agent Chat",
  "topic": "Private chat with Letta agent: Room Test Agent",
  "preset": "trusted_private_chat",
  "invite": [
    "@admin:matrix.oculair.ca",
    "@matrixadmin:matrix.oculair.ca",
    "@letta:matrix.oculair.ca"
  ],
  "is_direct": false,
  "initial_state": [
    {
      "type": "m.room.guest_access",
      "state_key": "",
      "content": {"guest_access": "forbidden"}
    },
    {
      "type": "m.room.history_visibility",
      "state_key": "",
      "content": {"history_visibility": "shared"}
    }
  ]
}
```

**Response** (200):
```json
{
  "room_id": "!newroom:matrix.test"
}
```

### [3-6] Admin/Letta Login & Join
Similar to agent login, but with different credentials

**Login endpoints**: All POST `/_matrix/client/r0/login`  
**Join endpoints**: All POST `/_matrix/client/r0/rooms/{roomId}/join`

### [7-8] Space Integration
**PUT** requests to room state endpoints with space relationship data

### [9] Letta History
**GET** `http://192.168.50.90:8289/v1/agents/{agentId}/messages`

---

## Why Dependencies & Fixtures Matter

The test uses:
1. **`agent_manager` fixture** (line 29-34) - Creates AgentUserManager
2. **`mock_aiohttp_session` fixture** (line 59-78 in conftest.py) - Generic HTTP mock

**Problem**: `mock_aiohttp_session` is too generic
- Doesn't distinguish between endpoints
- Doesn't handle multiple calls to same endpoint type
- Uses fragile `side_effect` list

---

## What This Method Actually Does (Source Code Analysis)

### In `agent_user_manager.py` (line 537-540):
```python
async def create_or_update_agent_room(self, agent_id: str):
    mapping = self.mappings.get(agent_id)
    return await self.room_manager.create_or_update_agent_room(agent_id, mapping)
```

Delegates to room_manager.

### In `room_manager.py` (line 145-285):

**Step 1: Check preconditions**
- Agent user must be created (line 147)
- Check if room already exists (line 152-165)
- Check for existing rooms (line 167-175)

**Step 2: Login as agent user** (line 179-190)
- POST to `/{homeserver_url}/_matrix/client/r0/login`
- Get agent access token

**Step 3: Create room as agent** (line 203-240)
- POST to `/{homeserver_url}/_matrix/client/r0/createRoom`
- Specify invites for admin and letta users
- Get room_id from response
- Update mapping with room_id and room_created=True
- Save mappings

**Step 4: Add room to space** (line 257-263)
- Check if space exists via `space_manager.get_space_id()`
- If yes, call `space_manager.add_room_to_space()`
- Makes 2 PUT requests to set space relationships

**Step 5: Auto-accept invitations** (line 266)
- Call `auto_accept_invitations_with_tracking()`
- For admin and letta users:
  - Login with their credentials (POST login)
  - Join room (POST join)
  - Track invitation status

**Step 6: Import history** (line 269-275)
- Call `import_recent_history()`
- GET messages from Letta API
- Send historical messages to room (nio operations)

---

## Recommended Fix for conftest.py

Add URL-aware mocking fixture:

```python
@pytest.fixture
def mock_aiohttp_session_room_creation():
    """
    Comprehensive mock for room creation workflow.
    
    Routes HTTP requests by URL pattern:
    - login endpoints → login responses
    - createRoom → room creation response
    - join endpoints → join responses
    - space state → space responses
    - Letta agents API → message responses
    """
    session = AsyncMock(spec=aiohttp.ClientSession)
    
    # Create response mocks
    login_response = AsyncMock()
    login_response.status = 200
    login_response.json = AsyncMock(return_value={
        "access_token": "token_xyz",
        "user_id": "@user:matrix.test"
    })
    login_response.__aenter__ = AsyncMock(return_value=login_response)
    login_response.__aexit__ = AsyncMock(return_value=None)
    
    room_response = AsyncMock()
    room_response.status = 200
    room_response.json = AsyncMock(return_value={
        "room_id": "!room:matrix.test"
    })
    room_response.__aenter__ = AsyncMock(return_value=room_response)
    room_response.__aexit__ = AsyncMock(return_value=None)
    
    join_response = AsyncMock()
    join_response.status = 200
    join_response.json = AsyncMock(return_value={
        "room_id": "!room:matrix.test"
    })
    join_response.__aenter__ = AsyncMock(return_value=join_response)
    join_response.__aexit__ = AsyncMock(return_value=None)
    
    space_response = AsyncMock()
    space_response.status = 200
    space_response.json = AsyncMock(return_value={})
    space_response.__aenter__ = AsyncMock(return_value=space_response)
    space_response.__aexit__ = AsyncMock(return_value=None)
    
    messages_response = AsyncMock()
    messages_response.status = 200
    messages_response.json = AsyncMock(return_value={"items": []})
    messages_response.__aenter__ = AsyncMock(return_value=messages_response)
    messages_response.__aexit__ = AsyncMock(return_value=None)
    
    # URL-based routing
    def route_post(url, **kwargs):
        url_lower = url.lower()
        if "login" in url_lower:
            return login_response
        elif "createroom" in url_lower:
            return room_response
        elif "join" in url_lower:
            return join_response
        else:
            return space_response
    
    def route_put(url, **kwargs):
        return space_response
    
    def route_get(url, **kwargs):
        if "agents" in url.lower() and "messages" in url.lower():
            return messages_response
        return space_response
    
    session.post = Mock(side_effect=route_post)
    session.put = Mock(side_effect=route_put)
    session.get = Mock(side_effect=route_get)
    session.closed = False
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    
    return session
```

---

## Summary

| Aspect | Details |
|--------|---------|
| **Total HTTP calls needed** | 9 (6 POST, 2 PUT, 1 GET) |
| **Currently mocked** | 2 (login + createRoom) |
| **Missing mocks** | 7 endpoints |
| **Primary failure point** | StopIteration after 2nd POST |
| **Secondary issues** | No PUT/GET mocking, fragile side_effect |
| **Fix complexity** | Medium - add URL-aware routing fixture |

