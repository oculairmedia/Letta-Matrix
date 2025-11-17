# Quick Fix Summary for test_discover_and_create_agents

## The Core Problem in 3 Points

1. **Wrong Patching Target**: Test patches `get_global_session()` but code creates `aiohttp.ClientSession()` directly
2. **Incomplete Mocks**: Test has 3 GET and 1 POST mock, but 21+ HTTP calls are needed
3. **Architectural Mismatch**: Code uses independent session managers; test assumes shared global session

## Failing HTTP Calls

```
Expected by test        | Actually called
------------------------+---------------------------
get_global_session()    | aiohttp.ClientSession() [5 times]
3x GET responses        | 9x GET calls needed
1x POST response        | 6x POST calls needed
1x PUT response         | 4x PUT calls needed
```

## File Locations & Line Numbers

| Component | File | Lines | Issue |
|-----------|------|-------|-------|
| Test | `tests/integration/test_multi_agent_workflow.py` | 46-113 | Incomplete mocking |
| get_letta_agents() | `src/core/agent_user_manager.py` | 166-269 | Creates own session (187) |
| create_matrix_user() | `src/core/user_manager.py` | 140-194 | Creates own session (165) |
| get_admin_token() | `src/core/user_manager.py` | 37-72 | Creates own session (58) |
| create_or_update_agent_room() | `src/core/room_manager.py` | 145-285 | Creates own session (189) |
| auto_accept_invitations_with_tracking() | `src/core/room_manager.py` | 287-361 | Creates own session (309) |
| Mock fixture | `tests/conftest.py` | 59-78 | Too generic, not URL-aware |

## Recommended Fix (FASTEST)

**Option 1: Patch aiohttp.ClientSession Constructor**

```python
import aiohttp
from unittest.mock import patch, AsyncMock

@pytest.fixture
def mock_all_http_calls(mock_aiohttp_session, monkeypatch):
    """Patch aiohttp.ClientSession at the constructor level"""
    
    class MockSessionFactory:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return mock_aiohttp_session
        
        async def __aexit__(self, *args):
            pass
        
        def get(self, *args, **kwargs):
            return mock_aiohttp_session.get(*args, **kwargs)
        
        def post(self, *args, **kwargs):
            return mock_aiohttp_session.post(*args, **kwargs)
        
        def put(self, *args, **kwargs):
            return mock_aiohttp_session.put(*args, **kwargs)
    
    monkeypatch.setattr('aiohttp.ClientSession', MockSessionFactory)
    return mock_aiohttp_session
```

**Option 2: Use Existing Integration Fixture (RECOMMENDED)**

The file `tests/integration/conftest.py` already has `patched_http_session` (lines 199-227) which:
- Routes responses by URL pattern
- Patches multiple modules
- Handles context managers properly

Just use it instead:

```python
async def test_discover_and_create_agents(self, agent_manager, patched_http_session):
    # Now all HTTP calls are properly mocked
    agents = await agent_manager.get_letta_agents()
    for agent in agents:
        await agent_manager.create_user_for_agent(agent)
```

## What conftest.py Needs

**Add to tests/integration/conftest.py after line 227:**

```python
@pytest.fixture
def mock_session_with_url_routing():
    """
    Session mock that routes based on URL patterns
    Handles all method calls (GET, POST, PUT, DELETE)
    """
    session = AsyncMock()
    
    async def make_response(status, json_data):
        resp = AsyncMock()
        resp.status = status
        resp.json = AsyncMock(return_value=json_data)
        resp.text = AsyncMock(return_value=str(json_data))
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=None)
        return resp
    
    async def route_get(url, **kwargs):
        if "agents" in url:
            return await make_response(200, {
                "data": [
                    {"id": "agent-001", "name": "Agent Alpha"},
                    {"id": "agent-002", "name": "Agent Beta"}
                ]
            })
        return await make_response(200, {})
    
    async def route_post(url, **kwargs):
        if "login" in url:
            return await make_response(200, {
                "access_token": f"token_{id(kwargs)}",
                "user_id": "@test:matrix.test"
            })
        elif "register" in url:
            return await make_response(200, {
                "access_token": "user_token",
                "user_id": "@agent:matrix.test"
            })
        elif "createroom" in url:
            return await make_response(200, {"room_id": "!room:matrix.test"})
        elif "join" in url:
            return await make_response(200, {"room_id": "!room:matrix.test"})
        return await make_response(200, {})
    
    async def route_put(url, **kwargs):
        return await make_response(200, {})
    
    session.get = AsyncMock(side_effect=route_get)
    session.post = AsyncMock(side_effect=route_post)
    session.put = AsyncMock(side_effect=route_put)
    session.closed = False
    
    return session
```

## Expected HTTP Endpoints & Response Format

### Letta Endpoints
- `GET http://192.168.50.90:8289/v1/agents?limit=100` → `{"data": [{"id": "...", "name": "..."}]}`
- `GET http://192.168.50.90:8289/v1/agents/{id}/messages` → `[{...}]` or `{"items": [...]}`

### Matrix Endpoints  
- `POST /_matrix/client/r0/login` → `{"access_token": "...", "user_id": "@...", "device_id": "..."}`
- `POST /_matrix/client/v3/register` → `{"user_id": "@...", "access_token": "...", "device_id": "..."}`
- `POST /_matrix/client/r0/createRoom` → `{"room_id": "!..."}`
- `POST /_matrix/client/r0/rooms/{id}/join` → `{"room_id": "!..."}`
- `PUT /_matrix/client/v3/profile/{id}/displayname` → `{}`
- `PUT /_matrix/client/r0/profile/{id}/displayname` → `{}`

## Files to Check

1. **Verify current mocking**: `tests/conftest.py` lines 59-78
2. **Check better fixture**: `tests/integration/conftest.py` lines 199-227  
3. **Review session creation**: `src/core/agent_user_manager.py` line 187
4. **All session.post() calls**: search for `async with session.post` in src/

## Key Insights

- Every HTTP method (get_letta_agents, create_matrix_user, etc.) creates its own session
- The global session patching has zero effect on actual code execution
- Need URL-aware mock that routes responses based on endpoint patterns
- The test attempts ~21 HTTP calls but only mocks 5 responses
- Integration tests should use `patched_http_session` fixture from integration/conftest.py

---

**Status**: Ready to implement. Choose Option 1 (quick patch) or Option 2 (use existing fixture).
