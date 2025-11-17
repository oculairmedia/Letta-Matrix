# Test Analysis: test_discover_and_create_agents
## Complete Documentation Index

This analysis covers the test at:
**File**: `tests/integration/test_multi_agent_workflow.py`  
**Test**: `TestAgentDiscoveryAndCreation.test_discover_and_create_agents`  
**Lines**: 46-113

---

## Documentation Files Generated

### 1. QUICK_FIX_SUMMARY.md
**Purpose**: Quick reference for implementing the fix  
**Contains**:
- Core problem in 3 points
- Failing HTTP calls summary
- File locations and line numbers  
- Two implementation options (quick vs recommended)
- Expected endpoints and response formats
- Key insights

**Read this first if you want to**: Fix the test quickly

---

### 2. TEST_ANALYSIS_REPORT.md (Comprehensive)
**Purpose**: In-depth technical analysis  
**Contains**:
- Executive summary
- All HTTP endpoints called (Letta + Matrix)
- Complete request sequence for each agent
- Current mocking approach and problems
- Fixture dependencies
- AgentUserManager method calls
- Root cause analysis
- conftest.py analysis
- Detailed recommendations with code samples
- Summary table

**Read this if you want to**: Understand everything deeply

---

### 3. CALL_SEQUENCE_DIAGRAM.md
**Purpose**: Visual flow of HTTP calls  
**Contains**:
- ASCII diagram of execution flow
- Step-by-step sequence for each agent
- Summary of HTTP call counts
- Problem breakdown
- Why the test fails

**Read this if you want to**: See the flow visually

---

## Quick Summary

### The Problem
Test patches `get_global_session()` but code creates `aiohttp.ClientSession()` directly → patch never applied → real HTTP calls attempted → test fails

### The Scale
- Expected by test: 3 GET + 1 POST + 1 PUT mocks
- Actually needed: 9 GET + 6 POST + 4 PUT calls
- 21+ HTTP calls total for processing 2 agents

### The Fix (Recommended)
Use existing `patched_http_session` fixture from `tests/integration/conftest.py` (lines 199-227)  
Or patch `aiohttp.ClientSession()` constructor directly

---

## Critical Code Locations

### Where Sessions Are Created (NOT using global)
| Method | File | Line | Creates Own Session? |
|--------|------|------|----------------------|
| `get_letta_agents()` | `src/core/agent_user_manager.py` | 187 | YES |
| `create_matrix_user()` | `src/core/user_manager.py` | 165 | YES |
| `get_admin_token()` | `src/core/user_manager.py` | 58 | YES |
| `create_or_update_agent_room()` | `src/core/room_manager.py` | 189 | YES |
| `auto_accept_invitations_with_tracking()` | `src/core/room_manager.py` | 309 | YES |

### Where Mocks Are Defined
| Fixture | File | Lines | Quality |
|---------|------|-------|---------|
| `mock_aiohttp_session` | `tests/conftest.py` | 59-78 | Generic, not URL-aware |
| `patched_http_session` | `tests/integration/conftest.py` | 199-227 | Good, URL-routing |
| `mock_http_session` | `tests/integration/conftest.py` | 68-195 | Good, URL-routing |

---

## HTTP Endpoints Called

### Letta API (Port 8289)
```
GET  http://192.168.50.90:8289/v1/agents?limit=100
GET  http://192.168.50.90:8289/v1/agents/{id}/messages
```

### Matrix Synapse API (Port 8008)
```
POST   /_matrix/client/r0/login
POST   /_matrix/client/v3/register
POST   /_matrix/client/r0/createRoom
POST   /_matrix/client/r0/rooms/{id}/join
PUT    /_matrix/client/v3/profile/{user_id}/displayname
PUT    /_matrix/client/r0/profile/{user_id}/displayname
```

---

## Test Execution Flow

```
agent_manager.get_letta_agents()
│
└─ GET /v1/agents
   │
   └─ Returns: [agent-001, agent-002]
      │
      ├─ FOR agent-001:
      │  │
      │  └─ agent_manager.create_user_for_agent(agent-001)
      │     │
      │     ├─ create_matrix_user()
      │     │  └─ POST /register
      │     │
      │     ├─ update_display_name()  
      │     │  ├─ POST /login (get admin token)
      │     │  └─ PUT /profile/{id}/displayname
      │     │
      │     ├─ create_or_update_agent_room()
      │     │  ├─ POST /login (as agent)
      │     │  ├─ POST /createRoom
      │     │  ├─ auto_accept_invitations_with_tracking()
      │     │  │  ├─ 2x POST /login
      │     │  │  └─ 2x POST /rooms/{id}/join
      │     │  │
      │     │  └─ import_recent_history()
      │     │     ├─ GET /agents/{id}/messages
      │     │     └─ Uses matrix-nio to send messages
      │
      └─ FOR agent-002: (Repeat all of the above)
```

---

## Implementation Options

### Option 1: Quick Patch (5 minutes)
Patch `aiohttp.ClientSession` constructor in conftest.py

Pros: Fast, minimal changes  
Cons: Doesn't use existing infrastructure

### Option 2: Use Existing Fixture (2 minutes)  
Change test to use `patched_http_session` instead of `mock_aiohttp_session`

Pros: Fastest, uses existing code  
Cons: Need to verify fixture coverage

### Option 3: Add URL-Routing Mock (10 minutes)
Create new fixture with full URL routing in integration/conftest.py

Pros: Reusable, comprehensive  
Cons: More code to add

---

## Files You Should Check

1. **The Test Itself**
   - Path: `tests/integration/test_multi_agent_workflow.py`
   - Lines: 46-113
   - Issue: Incomplete mocking, wrong patching target

2. **Current Mock Fixture**
   - Path: `tests/conftest.py`  
   - Lines: 59-78
   - Issue: Generic, not URL-aware, missing context managers

3. **Better Integration Fixture**
   - Path: `tests/integration/conftest.py`
   - Lines: 199-227 (`patched_http_session`) - USE THIS
   - Lines: 68-195 (`mock_http_session`) - Alternative

4. **Code That Creates Sessions**
   - Path: `src/core/agent_user_manager.py`
   - Line: 187 - First session creation
   - Path: `src/core/user_manager.py`
   - Line: 165 - Second session creation
   - Path: `src/core/room_manager.py`
   - Lines: 189, 309 - More sessions

---

## Root Causes

### Cause 1: Wrong Patching Target
```python
# What test does:
with patch('src.core.agent_user_manager.get_global_session', 
           return_value=mock_aiohttp_session):

# What code does:
async with aiohttp.ClientSession() as session:  # <-- NOT patched!
```

### Cause 2: Incomplete Mocks
- Test: 3 GET, 1 POST, 1 PUT responses
- Needed: 9 GET, 6 POST, 4 PUT responses

### Cause 3: Missing Context Managers  
- Responses must implement `__aenter__` and `__aexit__`
- Not all test mocks have proper setup

---

## How to Verify the Fix

After implementing the fix, the test should:
1. Successfully call `get_letta_agents()` without HTTP errors
2. Create 2 agents without attempting real HTTP calls
3. Mock all responses with appropriate status codes
4. Pass all assertions about agent mappings

Run with:
```bash
pytest tests/integration/test_multi_agent_workflow.py::TestAgentDiscoveryAndCreation::test_discover_and_create_agents -v
```

---

## Additional Resources

### Related Tests
- `test_sync_agents_to_users()` - Similar issue (line 115)
- `test_create_room_for_agent()` - Similar issue (line 169)
- `test_detect_agent_name_change()` - Similar issue (line 253)

All integration tests likely need the same fixture fix.

### Related Code
- `AgentUserManager` - Main class being tested
- `MatrixUserManager` - Handles user creation
- `MatrixRoomManager` - Handles room creation
- `MatrixSpaceManager` - Handles space operations

---

## Next Steps

1. Read `QUICK_FIX_SUMMARY.md` (2 minutes)
2. Choose implementation option
3. Implement the fix
4. Run the test to verify
5. Apply same fix to other integration tests

---

**Generated**: 2025-11-17  
**Test Location**: `tests/integration/test_multi_agent_workflow.py:46-113`  
**Status**: Analysis complete, ready for implementation
