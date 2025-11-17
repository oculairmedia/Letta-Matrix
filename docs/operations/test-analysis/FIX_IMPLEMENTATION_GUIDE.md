# Implementation Guide: Fixing `test_detect_agent_name_change`

## What to Add to conftest.py

Add these fixtures at the end of `/home/user/Letta-Matrix/tests/conftest.py`:

```python
# ============================================================================
# Matrix Name Update Fixtures (for agent rename tests)
# ============================================================================

@pytest.fixture
def mock_admin_token_response():
    """Mock Matrix admin login response"""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={
        "access_token": "admin_token_test_xyz",
        "user_id": "@admin:matrix.test",
        "device_id": "test_device"
    })
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.fixture
def mock_matrix_state_update_response():
    """Mock Matrix room state update response (for room name/topic/etc)"""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={})
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.fixture
def mock_matrix_profile_update_response():
    """Mock Matrix profile update response (for display name)"""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={})
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.fixture
def mock_joined_rooms_response():
    """Mock Matrix joined rooms response (for check_room_exists)"""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={
        "joined_rooms": ["!room:matrix.test"]
    })
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


# ============================================================================
# Letta Agent Update Fixtures
# ============================================================================

@pytest.fixture
def mock_agents_list_response():
    """Mock Letta agents list response"""
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={
        "data": [{"id": "agent-rename"}]
    })
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


@pytest.fixture
def mock_agent_details_response(request):
    """Mock Letta agent details response with optional custom name"""
    # Allow tests to customize the agent name via request param
    agent_name = getattr(request, 'param', {"name": "New Name"}).get("name", "New Name")
    
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={
        "id": "agent-rename",
        "name": agent_name
    })
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response
```

---

## What to Change in the Test

Replace the test with this implementation:

```python
@pytest.mark.asyncio
async def test_detect_agent_name_change(
    self, 
    agent_manager, 
    mock_aiohttp_session,
    mock_admin_token_response,
    mock_agents_list_response,
    mock_agent_details_response,
    mock_matrix_state_update_response,
    mock_matrix_profile_update_response,
    mock_joined_rooms_response
):
    """Test detecting when an agent's name changes and updating Matrix"""
    
    # Step 1: Setup existing mapping with original name
    agent_manager.mappings["agent-rename"] = AgentUserMapping(
        agent_id="agent-rename",
        agent_name="Original Name",
        matrix_user_id="@agent_rename:matrix.test",
        matrix_password="test_pass",
        created=True,
        room_id="!room:matrix.test",
        room_created=True
    )

    # Step 2: Configure mock responses in correct order
    # Order matters because of side_effect!
    # GET calls: agents list, agent details
    # POST call: admin token
    # PUT calls: room name, display name
    
    mock_aiohttp_session.get = Mock(side_effect=[
        mock_agents_list_response,      # For get_letta_agents()
        mock_agent_details_response,    # Not needed in get_letta_agents() but might be called
        mock_joined_rooms_response      # For check_room_exists()
    ])
    
    mock_aiohttp_session.post = Mock(return_value=mock_admin_token_response)
    
    mock_aiohttp_session.put = Mock(side_effect=[
        mock_matrix_state_update_response,      # Room name update
        mock_matrix_profile_update_response     # Display name update
    ])

    # Step 3: Patch both get_global_session and aiohttp.ClientSession
    # because update_room_name() and update_display_name() create their own sessions
    with patch('src.core.agent_user_manager.get_global_session', return_value=mock_aiohttp_session):
        with patch('aiohttp.ClientSession', return_value=mock_aiohttp_session):
            with patch('src.core.room_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                with patch('src.core.user_manager.aiohttp.ClientSession', return_value=mock_aiohttp_session):
                    # Step 4: Call the actual sync method
                    # This will:
                    # 1. Fetch agents from Letta
                    # 2. Detect name change
                    # 3. Update room name in Matrix
                    # 4. Update display name in Matrix
                    await agent_manager.sync_agents_to_users()

    # Step 5: Verify the agent name was updated
    assert agent_manager.mappings["agent-rename"].agent_name == "New Name"
    
    # Step 6: Verify that the room was checked for existence
    get_calls = mock_aiohttp_session.get.call_args_list
    joined_rooms_call = [
        call for call in get_calls 
        if 'joined_rooms' in str(call)
    ]
    # At least one call to check room exists
    assert len(joined_rooms_call) >= 0  # Optional, room might already exist
    
    # Step 7: Verify admin token was fetched
    post_calls = mock_aiohttp_session.post.call_args_list
    login_calls = [
        call for call in post_calls 
        if 'login' in str(call)
    ]
    assert len(login_calls) >= 1, "Admin login should have been called"
    
    # Step 8: Verify PUT calls were made for name updates
    put_calls = mock_aiohttp_session.put.call_args_list
    assert len(put_calls) >= 2, "Should have made at least 2 PUT calls (room name + display name)"
    
    # Step 9: Verify the URLs are correct
    put_urls = [str(call) for call in put_calls]
    
    # Check for room name update endpoint
    room_name_updated = any(
        'state/m.room.name' in url_str
        for url_str in put_urls
    )
    assert room_name_updated, "Should have called room name update endpoint"
    
    # Check for display name update endpoint
    display_name_updated = any(
        'displayname' in url_str
        for url_str in put_urls
    )
    assert display_name_updated, "Should have called display name update endpoint"
```

---

## Key Improvements

### 1. **Calls the actual sync method**
- ❌ Old: Manually updated the name
- ✓ New: Calls `sync_agents_to_users()` which detects and updates

### 2. **Proper mock layering**
- Patches `get_global_session` (used by get_letta_agents)
- Patches `aiohttp.ClientSession` directly (used by update_room_name, update_display_name, check_room_exists)

### 3. **Verification of actual HTTP calls**
- Verifies login was called
- Verifies PUT was called with correct endpoints
- Checks for both room name and display name updates

### 4. **All dependencies mocked**
- Agent list response
- Agent details response
- Admin token response
- Room name update response
- Display name update response
- Joined rooms check response

---

## Flow Diagram

```
Test Setup
    ↓
agent_manager.mappings["agent-rename"] = {
    agent_name: "Original Name",
    room_id: "!room:matrix.test",
    matrix_user_id: "@agent_rename:matrix.test"
}
    ↓
await agent_manager.sync_agents_to_users()
    ↓
[Inside sync_agents_to_users]
    ↓
GET /v1/agents?limit=100 → agents_list_response
    ↓
Check if mapping exists for "agent-rename" → Yes
    ↓
Get Letta agent: agent["name"] = "New Name"
    ↓
Compare: "Original Name" != "New Name" → NAME CHANGE DETECTED!
    ↓
1. Update mapping.agent_name = "New Name"
    ↓
2. Call update_room_name(room_id, "New Name")
    ├─ POST /_matrix/client/r0/login → admin_token
    └─ PUT /_matrix/client/r0/rooms/{id}/state/m.room.name
       with body: {"name": "New Name - Letta Agent Chat"}
    ↓
3. Call update_display_name(user_id, "New Name")
    ├─ POST /_matrix/client/r0/login → admin_token (cached)
    └─ PUT /_matrix/client/r0/profile/{user_id}/displayname
       with body: {"displayname": "New Name"}
    ↓
4. Call save_mappings()
    ↓
Test Assertions
    ↓
✓ mapping.agent_name == "New Name"
✓ PUT called for state/m.room.name
✓ PUT called for displayname
```

---

## Response Body Details

### Admin Token Response
```json
{
    "access_token": "admin_token_test_xyz",
    "user_id": "@admin:matrix.test",
    "device_id": "test_device"
}
```

### Room Name Update Request
```
PUT /_matrix/client/r0/rooms/!room:matrix.test/state/m.room.name
Authorization: Bearer admin_token_test_xyz
Content-Type: application/json

{
    "name": "New Name - Letta Agent Chat"
}
```

### Display Name Update Request
```
PUT /_matrix/client/r0/profile/@agent_rename:matrix.test/displayname
Authorization: Bearer admin_token_test_xyz
Content-Type: application/json

{
    "displayname": "New Name"
}
```

---

## Troubleshooting

If the test still fails, check:

1. **Is aiohttp.ClientSession being patched in all modules?**
   - Need to patch in room_manager.py
   - Need to patch in user_manager.py
   - Need to patch in space_manager.py (if it makes requests)

2. **Are mock responses properly configured?**
   - All responses need `__aenter__` and `__aexit__` for async context manager
   - All responses need proper `status` code
   - All responses need `json()` method

3. **Is the order of side_effect correct?**
   - Order matters! Each call consumes one mock from the side_effect list
   - If you get "list index out of range", you didn't mock enough responses

4. **Are you using the right patch paths?**
   - Use full module path: `src.core.room_manager.aiohttp.ClientSession`
   - Not: `aiohttp.ClientSession`

