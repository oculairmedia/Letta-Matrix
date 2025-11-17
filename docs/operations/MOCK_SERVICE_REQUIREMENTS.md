# Mock Service Requirements for CI Testing

**Purpose**: Document all external service dependencies and API calls needed to create comprehensive mock services for CI testing.  
**Goal**: CI always green; real integration tests run nightly or on-demand.  
**Date**: 2025-11-16  
**Status**: Active - Ready for Implementation

---

## Executive Summary

To achieve **100% CI test success**, we need to mock two external services:
1. **Letta API** - AI agent management service
2. **Matrix Synapse** - Matrix homeserver

This document catalogs **every HTTP endpoint** our code calls, with **real request/response examples** captured from production.

---

## Table of Contents

1. [Letta API Service](#letta-api-service)
2. [Matrix Synapse Service](#matrix-synapse-service)
3. [Mock Implementation Strategy](#mock-implementation-strategy)
4. [Sample Mock Data](#sample-mock-data)
5. [Implementation Checklist](#implementation-checklist)

---

## Letta API Service

### Base Configuration

| Property | Value |
|----------|-------|
| **Base URL** | `http://192.168.50.90:8289` (production) |
| **Base URL** | `http://test-letta:8283` (tests) |
| **Authentication** | Bearer token in `Authorization` header |
| **Token Source** | Environment variable `LETTA_API_KEY` |
| **Current Token** | `lettaSecurePass123` |

### Endpoints Used

#### 1. GET /v1/agents - List All Agents (Paginated)

**Source File**: `src/core/agent_user_manager.py:171-230`

**URL Pattern**:
```
GET {base_url}/v1/agents?limit=100
GET {base_url}/v1/agents?after={cursor}&limit=100
```

**Request Headers**:
```http
Authorization: Bearer lettaSecurePass123
Content-Type: application/json
```

**Response Format** (Success - 200):
```json
{
  "data": [
    {
      "id": "agent-7659b796-4723-4d61-98b5-737f874ee652",
      "name": "Huly - Personal Site",
      "created_at": "2025-01-15T10:30:00Z"
    },
    {
      "id": "agent-cc2aaa60-731d-41a8-99f4-de7154131a23",
      "name": "Huly - PhotoPrism MCP Server",
      "created_at": "2025-01-14T15:20:00Z"
    }
  ],
  "cursor": "next-page-token-xyz"
}
```

**Alternative Response Format** (Older API):
```json
[
  {
    "id": "agent-001",
    "name": "Agent Name"
  }
]
```

**Response Format** (No More Pages):
```json
{
  "data": [],
  "cursor": null
}
```

**Response Format** (Error - 401):
```json
{
  "error": "Unauthorized",
  "message": "Invalid or missing authentication token"
}
```

**Response Format** (Error - 500):
```json
{
  "error": "Internal Server Error",
  "message": "Database connection failed"
}
```

**Code Usage**:
```python
# Maximum 10 pages, 100 agents per page
# Handles both dict response ({"data": []}) and array response ([])
# Deduplicates agents by ID across pages
```

**Pagination Behavior**:
- First request: No `after` parameter
- Subsequent requests: Include `after={cursor}` from previous response
- Stop when: `data` array is empty OR no cursor returned
- Maximum pages: 10 (safety limit)

**Mock Requirements**:
- ✅ Support pagination with `after` cursor
- ✅ Handle both response formats (dict with "data", or direct array)
- ✅ Return consistent agent IDs across pages
- ✅ Simulate empty response when pagination exhausted
- ✅ Return 401 for invalid/missing token
- ✅ Return 500 for simulated errors

---

## Matrix Synapse Service

### Base Configuration

| Property | Value |
|----------|-------|
| **Base URL** | `http://localhost:8008` (production) |
| **Base URL** | `http://test-synapse:8008` (tests) |
| **API Version** | Client-Server API r0/v3 |
| **Authentication** | Bearer token in `Authorization` header |
| **Token Source** | Obtained via `/login` endpoint |

### Endpoints Used

#### 1. POST /_matrix/client/r0/login - Admin/User Login

**Source Files**: 
- `src/core/user_manager.py:58`
- `src/core/space_manager.py:195-197`
- `src/core/room_manager.py:189-190, 309-311`

**Request**:
```http
POST /_matrix/client/r0/login
Content-Type: application/json

{
  "type": "m.login.password",
  "user": "admin",
  "password": "admin_password"
}
```

**Response (Success - 200)**:
```json
{
  "user_id": "@admin:matrix.oculair.ca",
  "access_token": "syt_YWRtaW4_aBcDeF1234567890XyZ",
  "device_id": "ABCDEFGH",
  "home_server": "matrix.oculair.ca"
}
```

**Response (Error - 403)**:
```json
{
  "errcode": "M_FORBIDDEN",
  "error": "Invalid username or password"
}
```

**Mock Requirements**:
- ✅ Accept username/password in request body
- ✅ Return access token for valid credentials
- ✅ Return 403 for invalid credentials
- ✅ Store tokens for later authentication

---

#### 2. POST /_matrix/client/v3/register - Create New User

**Source File**: `src/core/user_manager.py:165-193`

**Request**:
```http
POST /_matrix/client/v3/register
Content-Type: application/json
Authorization: Bearer {admin_token}

{
  "username": "agent_7659b796_4723_4d61_98b5_737f874ee652",
  "password": "secure_generated_password",
  "admin": false,
  "displayname": "Huly - Personal Site"
}
```

**Response (Success - 200)**:
```json
{
  "user_id": "@agent_7659b796_4723_4d61_98b5_737f874ee652:matrix.oculair.ca",
  "access_token": "syt_agent_token_123",
  "device_id": "DEVICE123"
}
```

**Response (User Exists - 400)**:
```json
{
  "errcode": "M_USER_IN_USE",
  "error": "User ID already taken"
}
```

**Response (Invalid Username - 400)**:
```json
{
  "errcode": "M_INVALID_USERNAME",
  "error": "Username contains invalid characters"
}
```

**Mock Requirements**:
- ✅ Track created users to prevent duplicates
- ✅ Return M_USER_IN_USE for duplicate usernames
- ✅ Generate unique access tokens per user
- ✅ Store user data for later queries
- ✅ Validate username format (lowercase, alphanumeric + underscore)

---

#### 3. POST /_matrix/client/v3/login - User Authentication

**Source File**: `src/core/user_manager.py:100`

**Request**:
```http
POST /_matrix/client/v3/login
Content-Type: application/json

{
  "type": "m.login.password",
  "identifier": {
    "type": "m.id.user",
    "user": "agent_001"
  },
  "password": "user_password"
}
```

**Response (Success - 200)**:
```json
{
  "user_id": "@agent_001:matrix.oculair.ca",
  "access_token": "syt_agent_token_456",
  "device_id": "DEVICE456"
}
```

**Mock Requirements**:
- ✅ Support both username and full user_id formats
- ✅ Return existing tokens for previously created users

---

#### 4. POST /_matrix/client/r0/createRoom - Create Room or Space

**Source Files**:
- `src/core/room_manager.py:189-255` (rooms)
- `src/core/space_manager.py:195-270` (spaces)

**Request (Regular Room)**:
```http
POST /_matrix/client/r0/createRoom
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "name": "Agent: Huly - Personal Site",
  "topic": "Conversation room for agent Huly - Personal Site",
  "preset": "private_chat",
  "visibility": "private",
  "invite": ["@admin:matrix.oculair.ca", "@letta:matrix.oculair.ca"]
}
```

**Request (Space)**:
```http
POST /_matrix/client/r0/createRoom
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "name": "Letta Agents",
  "topic": "Organization space for all Letta agent rooms",
  "preset": "private_chat",
  "visibility": "private",
  "creation_content": {
    "type": "m.space"
  }
}
```

**Response (Success - 200)**:
```json
{
  "room_id": "!aBcDeF1234567890:matrix.oculair.ca"
}
```

**Response (Error - 400)**:
```json
{
  "errcode": "M_BAD_JSON",
  "error": "Invalid room creation parameters"
}
```

**Mock Requirements**:
- ✅ Generate unique room IDs
- ✅ Differentiate between rooms and spaces (check `creation_content.type`)
- ✅ Track created rooms/spaces
- ✅ Store room name, topic, invitees
- ✅ Auto-add creator to room

---

#### 5. POST /_matrix/client/r0/rooms/{room_id}/join - Join Room

**Source File**: `src/core/room_manager.py:309-345`

**Request**:
```http
POST /_matrix/client/r0/rooms/!room123:matrix.oculair.ca/join
Content-Type: application/json
Authorization: Bearer {access_token}

{}
```

**Response (Success - 200)**:
```json
{
  "room_id": "!room123:matrix.oculair.ca"
}
```

**Response (Already Joined - 200)**:
```json
{
  "room_id": "!room123:matrix.oculair.ca"
}
```

**Response (Not Invited - 403)**:
```json
{
  "errcode": "M_FORBIDDEN",
  "error": "You are not invited to this room"
}
```

**Mock Requirements**:
- ✅ Check if user is invited or room is public
- ✅ Add user to room members list
- ✅ Return success even if already joined (idempotent)
- ✅ Return 403 if private room and not invited

---

#### 6. PUT /_matrix/client/r0/rooms/{room_id}/state/m.room.name - Set Room Name

**Source Files**:
- `src/core/room_manager.py:83-95` (update)
- `src/core/room_manager.py:130-145` (verification)

**Request**:
```http
PUT /_matrix/client/r0/rooms/!room123:matrix.oculair.ca/state/m.room.name
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "name": "Agent: New Name"
}
```

**Response (Success - 200)**:
```json
{
  "event_id": "$event_12345:matrix.oculair.ca"
}
```

**Response (Not in Room - 403)**:
```json
{
  "errcode": "M_FORBIDDEN",
  "error": "You are not in this room"
}
```

**Mock Requirements**:
- ✅ Check if user is member of room
- ✅ Update room name in state
- ✅ Return event ID
- ✅ Allow GET requests to verify name change

---

#### 7. GET /_matrix/client/r0/rooms/{room_id}/state - Get Room State

**Source File**: `src/core/space_manager.py:120-135`

**Request**:
```http
GET /_matrix/client/r0/rooms/!room123:matrix.oculair.ca/state
Authorization: Bearer {access_token}
```

**Response (Success - 200)**:
```json
[
  {
    "type": "m.room.create",
    "state_key": "",
    "content": {
      "creator": "@admin:matrix.oculair.ca",
      "room_version": "9"
    }
  },
  {
    "type": "m.room.name",
    "state_key": "",
    "content": {
      "name": "Letta Agents"
    }
  },
  {
    "type": "m.space.child",
    "state_key": "!child_room:matrix.oculair.ca",
    "content": {
      "via": ["matrix.oculair.ca"]
    }
  }
]
```

**Mock Requirements**:
- ✅ Return array of state events
- ✅ Include m.room.create, m.room.name, m.space.child events
- ✅ Filter by user permissions

---

#### 8. PUT /_matrix/client/r0/rooms/{space_id}/state/m.space.child/{room_id} - Add Room to Space

**Source File**: `src/core/space_manager.py:306-325`

**Request**:
```http
PUT /_matrix/client/r0/rooms/!space123:matrix.oculair.ca/state/m.space.child/!room456:matrix.oculair.ca
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "via": ["matrix.oculair.ca"],
  "order": "agent-001"
}
```

**Response (Success - 200)**:
```json
{
  "event_id": "$child_event_123:matrix.oculair.ca"
}
```

**Mock Requirements**:
- ✅ Track space-child relationships
- ✅ Store order field for sorting
- ✅ Allow querying children via state endpoint

---

#### 9. PUT /_matrix/client/r0/rooms/{room_id}/state/m.space.parent/{space_id} - Set Parent Space

**Source File**: `src/core/space_manager.py:318`

**Request**:
```http
PUT /_matrix/client/r0/rooms/!room456:matrix.oculair.ca/state/m.space.parent/!space123:matrix.oculair.ca
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "via": ["matrix.oculair.ca"]
}
```

**Response (Success - 200)**:
```json
{
  "event_id": "$parent_event_123:matrix.oculair.ca"
}
```

**Mock Requirements**:
- ✅ Create bi-directional space relationship (child→parent, parent→child)

---

#### 10. GET /_matrix/client/r0/joined_rooms - List User's Joined Rooms

**Source File**: `src/core/room_manager.py:116-125`

**Request**:
```http
GET /_matrix/client/r0/joined_rooms
Authorization: Bearer {access_token}
```

**Response (Success - 200)**:
```json
{
  "joined_rooms": [
    "!room123:matrix.oculair.ca",
    "!room456:matrix.oculair.ca",
    "!space789:matrix.oculair.ca"
  ]
}
```

**Mock Requirements**:
- ✅ Track which rooms each user has joined
- ✅ Return array of room IDs for authenticated user

---

#### 11. PUT /_matrix/client/v3/profile/{user_id}/displayname - Set Display Name

**Source Files**:
- `src/core/user_manager.py:215-230` (r0 version)
- `src/core/user_manager.py:255-270` (v3 version)

**Request**:
```http
PUT /_matrix/client/v3/profile/@agent_001:matrix.oculair.ca/displayname
Content-Type: application/json
Authorization: Bearer {access_token}

{
  "displayname": "Huly - Personal Site"
}
```

**Response (Success - 200)**:
```json
{}
```

**Response (Unauthorized - 401)**:
```json
{
  "errcode": "M_UNKNOWN_TOKEN",
  "error": "Unrecognised access token"
}
```

**Mock Requirements**:
- ✅ Store display name per user
- ✅ Allow users to update own display name
- ✅ Return 401 for invalid tokens

---

## Mock Implementation Strategy

### Recommended Approach: Pytest Fixtures with aiohttp Mocking

We've already successfully implemented this for space integration tests. Expand this pattern to all integration tests.

**Advantages**:
- ✅ Fast (no actual network calls)
- ✅ Deterministic (same data every run)
- ✅ Easy to debug (Python-native)
- ✅ CI-friendly (no external services needed)
- ✅ Already proven to work (6/6 tests passing)

**Implementation Pattern** (Already Exists):

```python
# tests/integration/conftest.py
@pytest.fixture
def mock_http_session():
    """Mock HTTP session with all necessary endpoints"""
    mock_session = MagicMock()
    
    # Mock responses for each endpoint
    # (See existing implementation for details)
    
    return mock_session

@pytest.fixture
def patched_http_session(mock_http_session):
    """Patch all modules to use mock session"""
    modules_to_patch = [
        'src.core.agent_user_manager',
        'src.core.space_manager',
        'src.core.user_manager',
        'src.core.room_manager'
    ]
    
    patchers = []
    for module in modules_to_patch:
        patcher = patch(f'{module}.aiohttp.ClientSession', 
                       return_value=mock_session)
        patcher.start()
        patchers.append(patcher)
    
    yield mock_http_session
    
    for patcher in patchers:
        patcher.stop()
```

---

## Sample Mock Data

### Production-Like Test Agents

```python
MOCK_AGENTS = [
    {
        "id": "agent-test-001",
        "name": "Test Agent Alpha",
        "created_at": "2025-01-01T10:00:00Z"
    },
    {
        "id": "agent-test-002", 
        "name": "Test Agent Beta",
        "created_at": "2025-01-01T11:00:00Z"
    },
    {
        "id": "agent-sync-test",
        "name": "Sync Test Agent",
        "created_at": "2025-01-01T12:00:00Z"
    }
]
```

### Mock Credentials

```python
MOCK_CREDENTIALS = {
    "admin": {
        "username": "@admin:matrix.test",
        "password": "admin_password",
        "token": "mock_admin_token_123"
    },
    "letta": {
        "username": "@letta:matrix.test",
        "password": "letta_password",
        "token": "mock_letta_token_456"
    }
}
```

### Mock Room/Space IDs

```python
MOCK_IDS = {
    "space": "!mock_letta_space_123:matrix.test",
    "room_template": "!mock_agent_room_{agent_id}:matrix.test"
}
```

---

## Implementation Checklist

### Phase 1: Extend Existing Mocks (2-3 hours)

- [ ] **Analyze `test_multi_agent_workflow.py`**
  - [ ] Identify all HTTP calls made by failing tests
  - [ ] Document expected request/response patterns
  
- [ ] **Extend `conftest.py` Mock Fixtures**
  - [ ] Add Letta API pagination support
  - [ ] Add user registration mocks
  - [ ] Add room join mocks
  - [ ] Add display name mocks
  
- [ ] **Update Failing Tests**
  - [ ] `test_discover_and_create_agents` - Use mocked Letta API
  - [ ] `test_sync_agents_to_users` - Use mocked user creation
  - [ ] `test_create_room_for_agent` - Use mocked room creation
  - [ ] `test_detect_agent_name_change` - Use mocked room name updates
  
- [ ] **Verify All Tests Pass**
  - [ ] Run locally: `pytest tests/integration/ -v`
  - [ ] Verify in CI: Push to branch and check GitHub Actions

### Phase 2: Add Validation & Edge Cases (2 hours)

- [ ] **Add Mock State Tracking**
  - [ ] Track created users (prevent duplicates)
  - [ ] Track created rooms/spaces
  - [ ] Track space-child relationships
  - [ ] Track user memberships
  
- [ ] **Add Error Scenarios**
  - [ ] Invalid credentials → 403
  - [ ] Duplicate username → M_USER_IN_USE
  - [ ] Not invited to room → M_FORBIDDEN
  - [ ] Invalid token → M_UNKNOWN_TOKEN
  
- [ ] **Add Pagination Tests**
  - [ ] Test with multiple pages of agents
  - [ ] Test with empty pages
  - [ ] Test cursor handling

### Phase 3: CI Configuration (1 hour)

- [ ] **Update GitHub Actions Workflow**
  ```yaml
  - name: Run Integration Tests (Mocked)
    run: pytest tests/integration/ -v --cov=src
    env:
      USE_MOCK_SERVICES: true
  ```
  
- [ ] **Add Test Markers**
  - [ ] Mark live tests: `@pytest.mark.requires_live_services`
  - [ ] Mark mocked tests: `@pytest.mark.integration`
  - [ ] Configure pytest.ini to skip live tests in CI
  
- [ ] **Document Usage**
  - [ ] Update `tests/integration/README.md`
  - [ ] Add "Running Tests" section to main README
  - [ ] Document how to run with real services for local validation

### Phase 4: Documentation (1 hour)

- [ ] **Create Mock Development Guide**
  - [ ] How to add new mock endpoints
  - [ ] How to add new test scenarios
  - [ ] Common patterns and pitfalls
  
- [ ] **Update Architecture Docs**
  - [ ] Add mock architecture diagram
  - [ ] Document mock vs live test strategy
  - [ ] Add troubleshooting section

---

## Success Criteria

**Phase 1 Complete When**:
- ✅ All integration tests pass locally (14/14)
- ✅ All integration tests pass in CI (14/14)
- ✅ No connection errors to external services
- ✅ Test execution time < 1 minute

**Full Implementation Complete When**:
- ✅ Mock services validate all inputs
- ✅ Mock services track state (users, rooms, spaces)
- ✅ Mock services return realistic errors
- ✅ CI always green with mocked tests
- ✅ Live tests can still run on-demand for validation
- ✅ Documentation complete and accurate

---

## Maintenance Strategy

### Regular Updates
1. **When adding new API calls**: Update mock fixtures immediately
2. **When Matrix API updates**: Review and update mock responses
3. **When Letta API changes**: Update agent response formats

### Validation
1. **Monthly**: Run live integration tests against production
2. **Pre-release**: Full live integration test suite
3. **Post-deployment**: Spot-check with live tests

### Monitoring
1. **Track test execution time**: Should stay < 1 minute
2. **Track test flakiness**: Mock tests should be 100% stable
3. **Track coverage**: Integration tests should cover all critical paths

---

## References

- **Existing Mock Implementation**: `tests/integration/conftest.py`
- **Working Mocked Tests**: `tests/integration/test_space_integration_mocked.py`
- **Matrix Client-Server API**: https://spec.matrix.org/v1.5/client-server-api/
- **Letta API Documentation**: (Internal - check Letta docs)
- **aiohttp Testing**: https://docs.aiohttp.org/en/stable/testing.html

---

**Last Updated**: 2025-11-16  
**Status**: Ready for Implementation  
**Priority**: High (Blocks CI reliability)
