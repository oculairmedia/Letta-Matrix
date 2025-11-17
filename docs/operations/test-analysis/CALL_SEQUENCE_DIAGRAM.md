# HTTP Call Sequence Diagram
## test_discover_and_create_agents Execution Flow

```
TEST START: test_discover_and_create_agents()
│
├─ Step 1: agent_manager.get_letta_agents()
│  │
│  ├─ Creates: aiohttp.ClientSession() [NEW SESSION #1]
│  │
│  └─ GET http://192.168.50.90:8289/v1/agents?limit=100
│     │
│     ├─ Headers: Authorization: Bearer lettaSecurePass123
│     │
│     └─ Response (200):
│        {
│          "data": [
│            {"id": "agent-001", "name": "Agent Alpha"},
│            {"id": "agent-002", "name": "Agent Beta"}
│          ]
│        }
│
├─ Step 2: FOR EACH agent in agents:
│
├─────────────────────────────────────────────────────────
│ PROCESSING: agent-001
├─────────────────────────────────────────────────────────
│
├─ 2.1: agent_manager.create_user_for_agent(agent-001)
│  │
│  ├─ 2.1.1: generate_username("Agent Alpha", "agent-001")
│  │          → Returns: "agent_001"
│  │
│  ├─ 2.1.2: create_matrix_user("agent_001", "generated_pwd", "Letta Agent: Agent Alpha")
│  │  │
│  │  ├─ Creates: aiohttp.ClientSession() [NEW SESSION #2]
│  │  │
│  │  └─ POST http://test-synapse:8008/_matrix/client/v3/register
│  │     │
│  │     ├─ Request:
│  │     │  {
│  │     │    "username": "agent_001",
│  │     │    "password": "generated_pwd",
│  │     │    "auth": {"type": "m.login.dummy"}
│  │     │  }
│  │     │
│  │     └─ Response (200):
│  │        {
│  │          "user_id": "@agent_001:matrix.oculair.ca",
│  │          "access_token": "user_token_123",
│  │          "device_id": "device123"
│  │        }
│  │
│  │  ├─ [Inside register response handling]
│  │  └─ set_user_display_name("@agent_001:...", "Agent Alpha", "user_token_123")
│  │     │
│  │     └─ PUT http://test-synapse:8008/_matrix/client/v3/profile/@agent_001:.../displayname
│  │        │
│  │        ├─ Headers: Authorization: Bearer user_token_123
│  │        │
│  │        ├─ Request: {"displayname": "Agent Alpha"}
│  │        │
│  │        └─ Response (200): {}
│  │
│  ├─ 2.1.3: update_display_name("@agent_001:...", "Agent Alpha")
│  │  │
│  │  ├─ get_admin_token()
│  │  │  │
│  │  │  ├─ Creates: aiohttp.ClientSession() [NEW SESSION #3]
│  │  │  │
│  │  │  └─ POST http://test-synapse:8008/_matrix/client/r0/login
│  │  │     │
│  │  │     ├─ Request:
│  │     │  {
│  │     │    "type": "m.login.password",
│  │     │    "user": "test",
│  │     │    "password": "test_password"
│  │     │  }
│  │     │
│  │     └─ Response (200):
│  │        {
│  │          "access_token": "admin_token_1",
│  │          "user_id": "@test:matrix.test"
│  │        }
│  │
│  │  └─ PUT http://test-synapse:8008/_matrix/client/r0/profile/@agent_001:.../displayname
│  │     │
│  │     ├─ Headers: Authorization: Bearer admin_token_1
│  │     │
│  │     ├─ Request: {"displayname": "Agent Alpha"}
│  │     │
│  │     └─ Response (200): {}
│  │
│  ├─ 2.1.4: create_or_update_agent_room("agent-001")
│  │  │
│  │  ├─ Creates: aiohttp.ClientSession() [NEW SESSION #4]
│  │  │
│  │  ├─ Step A: Agent User Login
│  │  │  │
│  │  │  └─ POST http://test-synapse:8008/_matrix/client/r0/login
│  │  │     │
│  │  │     ├─ Request:
│  │  │     │  {
│  │  │     │    "type": "m.login.password",
│  │  │     │    "user": "agent_001",
│  │  │     │    "password": "generated_pwd"
│  │  │     │  }
│  │  │     │
│  │  │     └─ Response (200):
│  │  │        {
│  │  │          "access_token": "agent_token_1",
│  │  │          "user_id": "@agent_001:matrix.oculair.ca"
│  │  │        }
│  │  │
│  │  ├─ Step B: Create Room As Agent
│  │  │  │
│  │  │  └─ POST http://test-synapse:8008/_matrix/client/r0/createRoom
│  │  │     │
│  │  │     ├─ Headers: Authorization: Bearer agent_token_1
│  │  │     │
│  │  │     ├─ Request:
│  │  │     │  {
│  │  │     │    "name": "Agent Alpha - Letta Agent Chat",
│  │  │     │    "topic": "Private chat with Letta agent: Agent Alpha",
│  │  │     │    "preset": "trusted_private_chat",
│  │  │     │    "invite": ["@admin:matrix.oculair.ca", "@test:...", ...],
│  │  │     │    "is_direct": false,
│  │  │     │    "initial_state": [...]
│  │  │     │  }
│  │  │     │
│  │  │     └─ Response (200):
│  │  │        {
│  │  │          "room_id": "!room1:matrix.oculair.ca"
│  │  │        }
│  │  │
│  │  ├─ Step C: Add Room to Space
│  │  │  │ (skipped if space_manager has no space_id)
│  │  │
│  │  ├─ Step D: Auto-Accept Invitations
│  │  │  │
│  │  │  ├─ For @test:matrix.test (admin):
│  │  │  │  │
│  │  │  │  ├─ Creates: aiohttp.ClientSession() [NEW SESSION #5]
│  │  │  │  │
│  │  │  │  ├─ POST http://test-synapse:8008/_matrix/client/r0/login
│  │  │  │  │  └─ Response (200): {"access_token": "admin_token_2", ...}
│  │  │  │  │
│  │  │  │  └─ POST http://test-synapse:8008/_matrix/client/r0/rooms/!room1:.../join
│  │  │  │     │
│  │  │  │     ├─ Headers: Authorization: Bearer admin_token_2
│  │  │  │     │
│  │  │  │     └─ Response (200): {"room_id": "!room1:..."}
│  │  │  │
│  │  │  └─ For @test:matrix.test (letta user):
│  │  │     │
│  │  │     ├─ Creates: aiohttp.ClientSession() [NEW SESSION #6]
│  │  │     │
│  │  │     ├─ POST http://test-synapse:8008/_matrix/client/r0/login
│  │  │     │  └─ Response (200): {"access_token": "letta_token_1", ...}
│  │  │     │
│  │  │     └─ POST http://test-synapse:8008/_matrix/client/r0/rooms/!room1:.../join
│  │  │        └─ Response (200): {"room_id": "!room1:..."}
│  │  │
│  │  └─ Step E: Import Recent History
│  │     │
│  │     ├─ GET http://192.168.50.90:8289/v1/agents/agent-001/messages
│  │     │  └─ Response (200): [...message_objects...] or error (fails silently)
│  │     │
│  │     └─ Uses matrix-nio AsyncClient to send messages to room
│  │
│  └─ Update mapping for agent-001 ✓
│
├─────────────────────────────────────────────────────────
│ PROCESSING: agent-002
├─────────────────────────────────────────────────────────
│
└─ SAME SEQUENCE AS agent-001
   (All HTTP calls repeated for agent-002)


## SUMMARY OF HTTP CALLS FOR 2 AGENTS

POST /login                  : 6 times (1x admin for token cache + 2x per agent for room creation + 2x per agent for invitations + 1x extra)
POST /register               : 2 times (1x per agent)
POST /createRoom             : 2 times (1x per agent)
POST /rooms/{id}/join        : 4 times (2x per agent for admin + letta users)
PUT /profile/{id}/displayname: 4 times (2x per agent - from register + from admin)
GET /v1/agents              : 1 time
GET /v1/agents/{id}/messages : 2 times (1x per agent)

TOTAL: 21 HTTP CALLS


## THE PROBLEM

The test mocks:
- GET: 3 responses (agents list + 2 detail responses that are never used)
- POST: 1 generic response for ALL post requests
- PUT: 1 response for ALL put requests

But actually needs 21+ responses with specific content!

The test patches:
- get_global_session() in agent_user_manager

But the code creates NEW sessions:
- aiohttp.ClientSession() in get_letta_agents() [NOT PATCHED]
- aiohttp.ClientSession() in create_matrix_user() [NOT PATCHED]
- aiohttp.ClientSession() in get_admin_token() [NOT PATCHED]
- aiohttp.ClientSession() in create_or_update_agent_room() [NOT PATCHED]
- aiohttp.ClientSession() in auto_accept_invitations_with_tracking() [NOT PATCHED]

Result: Test attempts REAL HTTP calls → FAILS because endpoints don't exist
