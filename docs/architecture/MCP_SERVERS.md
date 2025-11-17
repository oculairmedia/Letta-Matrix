# MCP Servers Architecture

**Status**: 🟢 Production Ready
**Last Updated**: 2025-11-17
**Owner**: MCP Integration Team

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [MCP Protocol](#mcp-protocol)
- [Matrix MCP Server](#matrix-mcp-server)
- [Letta Agent MCP Server](#letta-agent-mcp-server)
- [Tool Catalog](#tool-catalog)
- [Authentication and Security](#authentication-and-security)
- [Integration Patterns](#integration-patterns)
- [Performance Characteristics](#performance-characteristics)
- [Error Handling](#error-handling)
- [Testing Strategies](#testing-strategies)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Model Context Protocol (MCP) servers provide a standardized interface for AI agents to interact with Matrix, enabling seamless communication and tool access without manual token management.

### Key Features

- ✅ Pre-authenticated Matrix operations
- ✅ Standardized MCP protocol implementation
- ✅ HTTP and WebSocket transport
- ✅ Inter-agent messaging support
- ✅ Automatic room discovery and management
- ✅ Built-in error handling and retries

### MCP Servers

1. **Matrix MCP Server** - General Matrix operations (rooms, messages)
2. **Letta Agent MCP Server** - Inter-agent messaging and agent discovery

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Letta Agents                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                 │
│  │ Agent A  │  │ Agent B  │  │ Agent C  │                 │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                 │
└───────┼─────────────┼─────────────┼──────────────────────────┘
        │             │             │
        │  MCP Tool   │  MCP Tool   │  MCP Tool
        │  Calls      │  Calls      │  Calls
        │             │             │
        └─────────────┴─────────────┴───────────────┐
                                                    │
        ┌───────────────────────────────────────────▼─────┐
        │           MCP HTTP Server (Port 8016)           │
        │                                                  │
        │  ┌────────────────┐    ┌────────────────────┐  │
        │  │  Matrix MCP    │    │  Letta Agent MCP   │  │
        │  │  - send msg    │    │  - agent message   │  │
        │  │  - list rooms  │    │  - list agents     │  │
        │  │  - read room   │    │  - get agent room  │  │
        │  │  - join room   │    └────────────────────┘  │
        │  │  - create room │                            │
        │  └────────────────┘                            │
        └──────────────────┬──────────────────────────────┘
                           │
                           │  Matrix API Calls
                           │
        ┌──────────────────▼──────────────────────────────┐
        │        Matrix API Service (Port 8004)           │
        └──────────────────┬──────────────────────────────┘
                           │
        ┌──────────────────▼──────────────────────────────┐
        │        Matrix Homeserver (Port 8008)            │
        └─────────────────────────────────────────────────┘
```

### Transport Layers

**HTTP Streaming** (Port 8016)
- Server-Sent Events (SSE) for responses
- JSON request/response format
- Suitable for long-running operations

**WebSocket** (Port 8015)
- Bidirectional real-time communication
- Lower latency
- Persistent connections

---

## MCP Protocol

### Protocol Overview

Model Context Protocol (MCP) is a standard for AI agents to access tools and resources.

**Key Concepts**:
- **Tools**: Functions agents can call
- **Resources**: Data agents can read
- **Prompts**: Reusable prompt templates

### MCP Message Format

**Tool Call Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "matrix_send_message",
    "arguments": {
      "room_id": "!abc123:matrix.oculair.ca",
      "message": "Hello from agent!"
    }
  }
}
```

**Tool Call Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"success\": true, \"event_id\": \"$xyz...\"}"
      }
    ]
  }
}
```

### Tool Discovery

**List Tools Request**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list"
}
```

**List Tools Response**:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "matrix_send_message",
        "description": "Send a message to a Matrix room",
        "inputSchema": {
          "type": "object",
          "properties": {
            "room_id": {"type": "string"},
            "message": {"type": "string"}
          },
          "required": ["room_id", "message"]
        }
      }
    ]
  }
}
```

---

## Matrix MCP Server

### Overview

Provides general Matrix operations for AI agents with pre-configured authentication.

### Available Tools

#### 1. matrix_send_message

**Purpose**: Send messages to Matrix rooms

**Arguments**:
- `room_id` (string, required): Target room ID
- `message` (string, required): Message content

**Example**:
```python
# Agent calls tool
matrix_send_message(
    room_id="!LWmNEJcwPwVWlbmNqe:matrix.oculair.ca",
    message="Hello from Letta agent!"
)

# Returns
{
  "success": true,
  "event_id": "$abc123...",
  "room_id": "!LWmNEJcwPwVWlbmNqe:matrix.oculair.ca"
}
```

**Implementation**:
```python
class MatrixSendMessageTool:
    def __init__(self, matrix_api_url, homeserver, username, password):
        self.matrix_api_url = matrix_api_url
        self.homeserver = homeserver
        self.username = username
        self.password = password

    async def execute(self, room_id: str, message: str):
        # 1. Get access token
        token = await self._get_token()

        # 2. Send message
        response = await self.session.post(
            f"{self.matrix_api_url}/messages/send",
            json={
                "room_id": room_id,
                "message": message,
                "token": token
            }
        )

        return await response.json()
```

#### 2. matrix_list_rooms

**Purpose**: List all rooms the agent has access to

**Arguments**: None

**Example**:
```python
# Agent calls tool
rooms = matrix_list_rooms()

# Returns
{
  "success": true,
  "rooms": [
    {
      "room_id": "!abc...",
      "name": "General Chat",
      "topic": "Main discussion room",
      "members": 5
    },
    {
      "room_id": "!xyz...",
      "name": "Meridian - Letta Agent Chat",
      "topic": "Chat with Meridian (Letta Agent)",
      "members": 3
    }
  ]
}
```

#### 3. matrix_read_room

**Purpose**: Read recent messages from a room

**Arguments**:
- `room_id` (string, required): Room to read from
- `limit` (integer, optional): Number of messages (default: 10)

**Example**:
```python
# Agent calls tool
messages = matrix_read_room(
    room_id="!abc123...",
    limit=20
)

# Returns
{
  "success": true,
  "messages": [
    {
      "event_id": "$msg1...",
      "sender": "@user:matrix.oculair.ca",
      "body": "Hello!",
      "timestamp": 1700000000000
    }
  ],
  "room_id": "!abc123..."
}
```

#### 4. matrix_join_room

**Purpose**: Join a Matrix room

**Arguments**:
- `room_id` (string): Room ID (e.g., `!abc:domain`)
- `room_alias` (string): Room alias (e.g., `#general:domain`)

One of `room_id` or `room_alias` is required.

**Example**:
```python
# Join by ID
matrix_join_room(room_id="!abc123:matrix.oculair.ca")

# Or join by alias
matrix_join_room(room_alias="#general:matrix.oculair.ca")

# Returns
{
  "success": true,
  "room_id": "!abc123:matrix.oculair.ca"
}
```

#### 5. matrix_create_room

**Purpose**: Create new Matrix rooms

**Arguments**:
- `name` (string, required): Room name
- `topic` (string, optional): Room topic/description
- `is_public` (boolean, optional): Public or private (default: false)
- `invite_users` (array, optional): List of user IDs to invite

**Example**:
```python
# Create private room
matrix_create_room(
    name="Project Discussion",
    topic="Discuss project updates",
    is_public=False,
    invite_users=["@alice:matrix.oculair.ca", "@bob:matrix.oculair.ca"]
)

# Returns
{
  "success": true,
  "room_id": "!newroom123:matrix.oculair.ca",
  "name": "Project Discussion"
}
```

---

## Letta Agent MCP Server

### Overview

Specialized MCP server for inter-agent communication with correct sender identity.

### Available Tools

#### 1. matrix_agent_message

**Purpose**: Send message to another agent with correct identity

**Arguments**:
- `to_agent_id` (string, required): Target agent's ID
- `message` (string, required): Message content

**Example**:
```python
# Agent A sends to Agent B
matrix_agent_message(
    to_agent_id="agent-597b5756-2915-4560-ba6b-91005f085166",
    message="Hello Agent B! How are you doing?"
)

# Returns
{
  "success": true,
  "event_id": "$abc123...",
  "room_id": "!agent_b_room...",
  "from_agent": "Agent A"
}
```

**Key Features**:
- ✅ Authenticates as sending agent
- ✅ Auto-joins target room if needed
- ✅ Includes sender metadata
- ✅ Synchronous (immediate response)

**Implementation**:
```python
class MatrixAgentMessageTool:
    async def execute(self, to_agent_id: str, message: str):
        # 1. Look up target agent's room
        target_room = await self._find_agent_room(to_agent_id)

        # 2. Get sender agent info (from context)
        from_agent = await self._get_current_agent()

        # 3. Authenticate as sender agent
        agent_token = await self._get_agent_token(from_agent)

        # 4. Join target room (if needed)
        await self._ensure_agent_in_room(
            agent_token, target_room, from_agent
        )

        # 5. Send message with metadata
        event_id = await self._send_with_metadata(
            room_id=target_room,
            message=message,
            from_agent_id=from_agent["id"],
            from_agent_name=from_agent["name"],
            token=agent_token
        )

        return {
            "success": True,
            "event_id": event_id,
            "room_id": target_room,
            "from_agent": from_agent["name"]
        }
```

**Message Metadata**:
```json
{
  "msgtype": "m.text",
  "body": "Hello Agent B!",
  "m.letta.from_agent_id": "agent-A-id",
  "m.letta.from_agent_name": "Agent A"
}
```

See: [INTER_AGENT_MESSAGING.md](INTER_AGENT_MESSAGING.md) for full details

#### 2. list_agents

**Purpose**: Discover available Letta agents

**Arguments**: None

**Example**:
```python
# Get list of agents
agents = list_agents()

# Returns
{
  "success": true,
  "agents": [
    {
      "id": "agent-4bea3f4e-...",
      "name": "Meridian",
      "matrix_user_id": "@agent_4bea3f4e_...:matrix.oculair.ca"
    },
    {
      "id": "agent-597b5756-...",
      "name": "Cascade",
      "matrix_user_id": "@agent_597b5756_...:matrix.oculair.ca"
    }
  ]
}
```

#### 3. get_agent_room

**Purpose**: Find an agent's Matrix room ID

**Arguments**:
- `agent_id` (string, required): Agent's ID

**Example**:
```python
# Get room for agent
get_agent_room(agent_id="agent-4bea3f4e-...")

# Returns
{
  "success": true,
  "room_id": "!abc123:matrix.oculair.ca",
  "agent_name": "Meridian"
}
```

---

## Tool Catalog

### Matrix Operations Matrix

| Tool | Purpose | Auth | Async | Inter-Agent |
|------|---------|------|-------|-------------|
| `matrix_send_message` | Send to any room | @letta | No | No |
| `matrix_list_rooms` | Discover rooms | @letta | No | No |
| `matrix_read_room` | Get messages | @letta | No | No |
| `matrix_join_room` | Join room | @letta | No | No |
| `matrix_create_room` | Create room | @letta | No | No |
| `matrix_agent_message` | Agent→Agent msg | Agent user | No | Yes |
| `list_agents` | Discover agents | None | No | No |
| `get_agent_room` | Find agent room | None | No | No |

### Tool Selection Guide

**General Matrix Operations** → Use Matrix MCP Server tools
- Sending to human users
- Managing rooms
- Administrative tasks

**Inter-Agent Communication** → Use Letta Agent MCP tools
- Agent-to-agent messaging
- Finding other agents
- Agent discovery

---

## Authentication and Security

### Pre-configured Authentication

All Matrix MCP tools use pre-configured credentials:

```python
# Environment configuration
MATRIX_HOMESERVER_URL=http://synapse:8008
MATRIX_USERNAME=@letta:matrix.oculair.ca
MATRIX_PASSWORD=letta
```

**Benefits**:
- ✅ No manual token management
- ✅ Automatic token refresh
- ✅ Session persistence
- ✅ Rate limit handling

### Token Management

```python
class MatrixAuthManager:
    async def get_token(self):
        # 1. Try cached token
        if self.cached_token and not self._is_expired():
            return self.cached_token

        # 2. Login if needed
        response = await self.client.login(
            username=self.username,
            password=self.password
        )

        # 3. Cache token
        self.cached_token = response.access_token
        self.token_expiry = time.time() + 3600

        return self.cached_token
```

### Security Model

**Matrix MCP Server**:
- Authenticates as `@letta:matrix.oculair.ca`
- Has access to all rooms @letta is in
- Administrative/monitoring role
- Cannot modify other users

**Letta Agent MCP Server**:
- Authenticates as individual agent users
- Each agent has own credentials
- Isolated permissions per agent
- Agent can only access own rooms + invited rooms

### Permission Boundaries

```python
# Matrix MCP tools CAN:
- Send messages to any room @letta is in
- List rooms @letta can see
- Read messages from accessible rooms
- Create new rooms as @letta

# Matrix MCP tools CANNOT:
- Access rooms @letta isn't in
- Modify server settings
- Create/delete users
- Override room permissions

# Agent MCP tools CAN:
- Send messages as the agent
- Join public rooms
- Interact with other agents
- Read agent's own rooms

# Agent MCP tools CANNOT:
- Impersonate other agents
- Access admin functions
- Modify other agents' rooms
```

---

## Integration Patterns

### Pattern 1: Basic Matrix Interaction

**Use Case**: Agent wants to send a status update

```python
# Agent code
result = await matrix_send_message(
    room_id="!status:matrix.oculair.ca",
    message="Task completed successfully!"
)

if result["success"]:
    print(f"Message sent: {result['event_id']}")
```

### Pattern 2: Room Discovery

**Use Case**: Find all available conversation rooms

```python
# List all rooms
rooms = await matrix_list_rooms()

# Filter for agent rooms
agent_rooms = [
    room for room in rooms["rooms"]
    if "Letta Agent Chat" in room.get("name", "")
]

print(f"Found {len(agent_rooms)} agent rooms")
```

### Pattern 3: Inter-Agent Collaboration

**Use Case**: Agent A asks Agent B for help

```python
# Agent A code
result = await matrix_agent_message(
    to_agent_id="agent-B-id",
    message="Can you help me analyze this data?"
)

# Agent B receives with context:
# "[INTER-AGENT MESSAGE from Agent A]
#  Can you help me analyze this data?
#  ---
#  ⚠️ SYSTEM INSTRUCTION
#  Respond using matrix_agent_message tool..."

# Agent B responds
response = await matrix_agent_message(
    to_agent_id="agent-A-id",
    message="Sure! Please share the data."
)
```

### Pattern 4: Room-based Workflows

**Use Case**: Create project-specific room with team

```python
# Create project room
room = await matrix_create_room(
    name="Q4 Planning",
    topic="Quarterly planning discussion",
    is_public=False,
    invite_users=["@alice:domain", "@bob:domain"]
)

# Send welcome message
await matrix_send_message(
    room_id=room["room_id"],
    message="Welcome to Q4 Planning! Let's get started."
)

# Join the room for monitoring
await matrix_join_room(room_id=room["room_id"])
```

### Pattern 5: Message Monitoring

**Use Case**: Monitor conversation for keywords

```python
# Get recent messages
messages = await matrix_read_room(
    room_id="!monitoring:domain",
    limit=50
)

# Check for keywords
urgent_messages = [
    msg for msg in messages["messages"]
    if "urgent" in msg["body"].lower()
]

if urgent_messages:
    # Alert someone
    await matrix_send_message(
        room_id="!alerts:domain",
        message=f"Found {len(urgent_messages)} urgent messages"
    )
```

---

## Performance Characteristics

### Response Times

**Matrix MCP Tools**:
- `matrix_list_rooms`: 200-500ms (cached)
- `matrix_send_message`: 300-800ms
- `matrix_read_room`: 200-600ms (varies with limit)
- `matrix_join_room`: 500-1500ms (includes federation)
- `matrix_create_room`: 800-2000ms

**Letta Agent MCP Tools**:
- `matrix_agent_message`: 500-1500ms (includes auto-join)
- `list_agents`: 100-300ms
- `get_agent_room`: 50-100ms (mapping lookup)

### Throughput

**Concurrent Operations**:
- 10 parallel tool calls: ✅ Supported
- 50 parallel tool calls: ✅ Supported (may see queuing)
- 100+ parallel: ⚠️ Rate limiting may apply

**Message Throughput**:
- Single tool: ~5-10 messages/second
- With connection pooling: ~50-100 messages/second
- Synapse limit: Effectively unlimited (configured 10000/sec)

### Optimization Strategies

#### 1. Connection Pooling

```python
# Shared connection pool
connector = aiohttp.TCPConnector(
    limit=100,
    limit_per_host=50,
    ttl_dns_cache=300
)

session = aiohttp.ClientSession(connector=connector)
```

#### 2. Token Caching

```python
# Cache tokens for 1 hour
if self.cached_token and time.time() < self.token_expiry:
    return self.cached_token
```

#### 3. Batch Operations

```python
# Send multiple messages efficiently
async def send_batch(messages):
    tasks = [
        matrix_send_message(msg["room_id"], msg["text"])
        for msg in messages
    ]
    return await asyncio.gather(*tasks)
```

---

## Error Handling

### Error Categories

**1. Network Errors**
```python
{
  "success": false,
  "error": "ConnectionError",
  "message": "Failed to connect to Matrix server"
}
```

**2. Authentication Errors**
```python
{
  "success": false,
  "error": "AuthenticationError",
  "message": "Invalid access token"
}
```

**3. Permission Errors**
```python
{
  "success": false,
  "error": "M_FORBIDDEN",
  "message": "User not in room"
}
```

**4. Rate Limiting**
```python
{
  "success": false,
  "error": "M_LIMIT_EXCEEDED",
  "message": "Too many requests",
  "retry_after_ms": 5000
}
```

### Retry Logic

All tools implement exponential backoff:

```python
async def execute_with_retry(func, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            return await func()
        except RateLimitError as e:
            if attempt == max_attempts - 1:
                raise
            wait_time = e.retry_after_ms / 1000
            await asyncio.sleep(wait_time)
        except TransientError as e:
            if attempt == max_attempts - 1:
                raise
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            await asyncio.sleep(wait_time)
```

### Error Recovery

**Automatic Recovery**:
- ✅ Token expiration → Auto-refresh
- ✅ Network timeout → Retry with backoff
- ✅ Rate limiting → Wait and retry
- ✅ Transient errors → Retry up to 3 times

**Manual Intervention Required**:
- ❌ Invalid credentials → Update environment
- ❌ Forbidden → Check permissions
- ❌ Room not found → Verify room ID
- ❌ User banned → Server admin action

---

## Testing Strategies

### Unit Testing

**Test individual tools**:
```python
import pytest
from matrix_mcp_tools import MatrixSendMessageTool

@pytest.mark.asyncio
async def test_send_message():
    tool = MatrixSendMessageTool(
        matrix_api_url="http://test:8004",
        homeserver="http://test:8008",
        username="@test:domain",
        password="test"
    )

    result = await tool.execute(
        room_id="!test:domain",
        message="Test message"
    )

    assert result["success"] == True
    assert "event_id" in result
```

### Integration Testing

**Test with live Matrix**:
```bash
# Start test environment
docker-compose up -d

# Run integration tests
pytest tests/integration/test_mcp_tools.py
```

### End-to-End Testing

**Test agent interactions**:
```python
# Agent A sends to Agent B
result = await matrix_agent_message(
    to_agent_id="agent-B-test-id",
    message="Test inter-agent message"
)

# Verify Agent B received it
messages = await matrix_read_room(
    room_id=agent_b_room_id,
    limit=1
)

assert messages["messages"][0]["body"] == "Test inter-agent message"
```

### Performance Testing

**Load testing**:
```python
import asyncio
import time

async def performance_test():
    start = time.time()

    # Send 100 messages concurrently
    tasks = [
        matrix_send_message("!test:domain", f"Message {i}")
        for i in range(100)
    ]

    results = await asyncio.gather(*tasks)
    duration = time.time() - start

    print(f"Sent 100 messages in {duration:.2f}s")
    print(f"Throughput: {100/duration:.2f} msg/sec")
```

---

## Best Practices

### 1. Tool Selection

- ✅ Use specific tools for specific purposes
- ✅ Use `matrix_agent_message` for agent-to-agent
- ✅ Use `matrix_send_message` for general messaging
- ❌ Don't use general tools for inter-agent messaging

### 2. Error Handling

- ✅ Always check `success` field in response
- ✅ Handle errors gracefully
- ✅ Log errors with context
- ❌ Don't ignore error responses

### 3. Performance

- ✅ Reuse MCP server connections
- ✅ Batch operations when possible
- ✅ Cache room IDs and agent mappings
- ❌ Don't create new connections per call

### 4. Security

- ✅ Use environment variables for credentials
- ✅ Validate input parameters
- ✅ Check room permissions before operations
- ❌ Don't hardcode tokens or passwords

### 5. Monitoring

- ✅ Log all tool calls
- ✅ Track response times
- ✅ Monitor error rates
- ❌ Don't operate without observability

---

## Troubleshooting

### MCP Server Not Responding

**Check server status**:
```bash
docker ps | grep mcp
curl http://localhost:8015/health
```

**Common issues**:
- Container not running → `docker-compose up -d mcp-server`
- Port conflict → Check `docker-compose ps`
- Network issue → Check Docker network

### Tool Execution Failed

**Check logs**:
```bash
docker logs mcp-server-1 --tail 100 -f
```

**Common errors**:
- "Connection refused" → Check Matrix homeserver
- "Unauthorized" → Verify credentials
- "Room not found" → Verify room ID

### Authentication Issues

**Verify credentials**:
```bash
# Check environment
docker exec mcp-server-1 env | grep MATRIX

# Test login
curl -X POST http://localhost:8004/login \
  -d '{"username": "@letta:matrix.oculair.ca", "password": "letta"}'
```

### Inter-Agent Messaging Not Working

**Diagnostic steps**:
1. Verify both agents exist: `list_agents()`
2. Check agent has room: `get_agent_room(agent_id)`
3. Test with `matrix_agent_message`
4. Check logs for context enhancement

See: [INTER_AGENT_MESSAGING.md](INTER_AGENT_MESSAGING.md)

### Performance Issues

**Check metrics**:
```bash
# Response times
docker logs mcp-server-1 | grep "response_time"

# Error rate
docker logs mcp-server-1 | grep "ERROR"
```

**Optimization**:
- Enable connection pooling
- Increase rate limits if needed
- Use batch operations
- Cache frequently accessed data

---

## Related Documentation

### Architecture
- [OVERVIEW.md](OVERVIEW.md) - System architecture overview
- [MATRIX_INTEGRATION.md](MATRIX_INTEGRATION.md) - Matrix homeserver integration
- [AGENT_MANAGEMENT.md](AGENT_MANAGEMENT.md) - Agent sync and room management
- [INTER_AGENT_MESSAGING.md](INTER_AGENT_MESSAGING.md) - Inter-agent communication details

### Operations
- [TESTING.md](../operations/TESTING.md) - Testing strategies
- [DEPLOYMENT.md](../operations/DEPLOYMENT.md) - Deployment procedures

### Historical
- See `docs/MATRIX_MCP_TOOLS.md` for original implementation
- See `docs/INTER_AGENT_MESSAGING_FIX.md` for messaging evolution

### External References
- [MCP Specification](https://modelcontextprotocol.io/) - Official MCP protocol
- [matrix-nio Documentation](https://matrix-nio.readthedocs.io/) - Python Matrix client
- [Matrix Client-Server API](https://spec.matrix.org/latest/client-server-api/)

---

**Status**: 🟢 Production Ready
**Active Tools**: 8 (5 Matrix + 3 Agent)
**Transport**: HTTP (8016) + WebSocket (8015)
**Last Verified**: 2025-11-17
