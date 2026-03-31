# OpenCode + Matrix Integration Design

## Overview

Auto-provision Matrix entities (users, rooms) for OpenCode instances to enable seamless persistent communication across the network.

### Key Insight

OpenCode instances are scoped to working directories. Each instance needs a persistent Matrix identity to:
1. Send and receive messages
2. Maintain conversation history
3. Enable network-wide communication with other agents

---

## OpenCode Architecture Understanding

### Tool Types

**Custom Tools** (`.opencode/tool/`)
- TypeScript/JavaScript function definitions
- Can invoke any language (Python, Bash, etc.)
- Context available: `agent`, `sessionID`, `messageID`
- Registered per-session with LLM
- Naming: `filename.ts` → tool name, or `filename_exportname.ts` → multiple tools

**Plugins** (`.opencode/plugin/`)
- Hook into OpenCode lifecycle events
- Receive context: `project`, `client`, `$`, `directory`, `worktree`
- Can define custom tools within plugins
- Can subscribe to events (session created, file edited, commands, etc.)

### Key Context Available

From plugin context:
- `directory`: Current working directory (can derive identity)
- `project`: Project metadata
- `client`: OpenCode SDK client (can programmatically interact)
- `worktree`: Git repo path

From tool context:
- `agent`: Agent name/ID
- `sessionID`: Current session ID
- `messageID`: Current message ID

### Integration Points

1. **Session Creation** → Capture working directory for identity
2. **Plugin Hook** → Register Matrix user on first session
3. **Custom Tool** → Provide `matrix_send` and `matrix_read` operations
4. **MCP Server** → Handle auto-provisioning and message routing

---

## Architecture Design

### Flow Diagram

```
OpenCode Instance (working directory)
    ↓
[Plugin: session.created event]
    ↓ Extract directory path → Hash/slugify to identity
    ↓
[Register with MCP Server]
    ↓
MCP Server auto-provisions:
- @identity:matrix.oculair.ca (Matrix user)
- Returns access_token + session metadata
    ↓
OpenCode stores token locally for session duration
    ↓
[Custom Tool: matrix_send]
    ↓
MCP Server sends message via Matrix
    ↓
[Matrix Client listens for messages]
    ↓
Messages appear in OpenCode as context
```

### Components

#### 1. OpenCode Plugin (`~/.config/opencode/plugin/matrix.ts`)

**Responsibilities:**
- Hook into `session.created` event
- Extract working directory → derive identity
- Call MCP server to register/get Matrix credentials
- Store token in session metadata (local only, not persisted)
- Optionally: Listen to `session.idle` and send summary to Matrix

**Flow:**
```typescript
// On session.created
1. Get directory from context
2. Hash/slugify: /opt/stacks/matrix-synapse-deployment → opencode-matrix-synapse-deployment
3. Call MCP: register_identity(identity, directory)
4. Store returned token in sessionMetadata
5. Make token available to tools
```

#### 2. OpenCode Custom Tool (`~/.config/opencode/tool/matrix.ts`)

**Capabilities:**
```typescript
matrix_send(
  to: string,           // Agent/user/room
  message: string,      // Content
  from?: string,        // Override identity (optional)
  metadata?: object     // Custom metadata
)

matrix_read(
  room_id?: string,     // Optional room filter
  limit?: number        // Message limit
)

matrix_join(
  room_id_or_alias: string
)
```

**Implementation:**
- Uses MCP `matrix_message` tool under the hood
- Passes current session token + identity
- Returns success/room_id for future reference

#### 3. MCP Server Enhancements (`src/mcp/http_server.py`)

**New Functionality:**

1. **Capture clientInfo during initialize**
   ```python
   # In _process_request, when method == 'initialize'
   clientInfo = params.get('clientInfo', {})
   session.metadata['client'] = clientInfo
   ```

2. **New Tool: `matrix_identity`**
   ```python
   matrix_identity(
       action: 'register' | 'get' | 'revoke',
       identity: str,        # Requested identity
       directory?: str,      # Working directory
       metadata?: dict
   )
   ```
   
   **register action:**
   - Check if @identity:matrix.oculair.ca exists
   - If not: auto-create with random password
   - Return: `{access_token, user_id, room_id}`
   
   **get action:**
   - Return existing identity info if available
   - Return error if doesn't exist
   
   **revoke action:**
   - Logout and deactivate if needed
   - Remove from metadata tracking

3. **Enhance `matrix_send`**
   - Accept `from_identity` as fallback
   - If not in params, use session-stored identity
   - Auto-create room if doesn't exist between from/to identities
   - Create DM room or join existing

---

## Identity Resolution Strategy

**Priority Order:**

1. **Explicit parameter**: `from_identity="custom-name"` in tool call
2. **Environment variable**: `MATRIX_IDENTITY=worker-123` (for containerized agents)
3. **MCP clientInfo**: Use `clientInfo.name` from handshake
4. **Directory-derived**: Hash `/opt/stacks/foo` → `opencode-foo`
5. **Session ID**: Use first 8 chars of sessionID as fallback
6. **Generated UUID**: `agent-{uuid}` if all else fails

**Example Implementations:**

```typescript
// OpenCode Plugin
const identity = 
  process.env.MATRIX_IDENTITY ||
  slugify(directory) ||        // /opt/stacks/project → opencode-project
  `opencode-${sessionID.slice(0, 8)}`

// Containerized agent
process.env.MATRIX_IDENTITY = 'agent-worker-pool-1'

// MCP Server (Python)
identity = (
    from_identity or
    os.getenv('MATRIX_IDENTITY') or
    session.metadata.get('client', {}).get('name') or
    slugify(directory) or
    f"agent-{uuid.uuid4().hex[:8]}"
)
```

---

## Implementation Phases

### Phase 1: MCP Server Enhancements
- [x] Capture `clientInfo` in initialize
- [ ] Add `matrix_identity` tool for registration
- [ ] Store identity → Matrix user mapping
- [ ] Auto-create Matrix users on demand

### Phase 2: OpenCode Custom Tool
- [ ] Create `.opencode/tool/matrix.ts`
- [ ] Implement `matrix_send`, `matrix_read`, `matrix_join`
- [ ] Use stored session token
- [ ] Handle errors gracefully

### Phase 3: OpenCode Plugin
- [ ] Create `~/.config/opencode/plugin/matrix.ts`
- [ ] Hook into `session.created`
- [ ] Register identity with MCP server
- [ ] Store token in session context
- [ ] Optional: Hook into `session.idle` for summaries

### Phase 4: Testing & Documentation
- [ ] Test with various OpenCode configurations
- [ ] Document setup process
- [ ] Create example use cases

---

## Data Persistence

### What NOT to Persist
- Matrix access tokens (session-only)
- Session-specific metadata (ephemeral)

### What TO Persist (MCP Server)
- Identity → Matrix user_id mapping (file or DB)
- Room IDs for common agent pairs
- Historical identity registrations (for debugging)

**Storage Location:**
```
/opt/stacks/matrix-synapse-deployment/matrix_client_data/identities.json
{
  "opencode-matrix-synapse-deployment": {
    "user_id": "@opencode-matrix-synapse-deployment:matrix.oculair.ca",
    "created_at": "2025-11-30T...",
    "last_seen": "2025-11-30T...",
    "directory": "/opt/stacks/matrix-synapse-deployment"
  }
}
```

---

## Security Considerations

1. **Token Management**
   - Tokens live only in session memory
   - Not written to disk (except on server side with rotation)
   - Cleared on session end

2. **User Creation**
   - Generate secure random passwords on server
   - Only return to requesting session
   - Consider rate limiting (max identities per hour)

3. **Room Visibility**
   - All rooms should be private by default
   - Only invited users can access
   - No world-readable rooms

4. **Identity Spoofing**
   - Require explicit `MATRIX_IDENTITY` env var or directory path validation
   - Log all identity registrations
   - Implement approval workflow if needed

---

## Example Usage

### From OpenCode (with custom tool)

```typescript
// In an OpenCode session
// User asks: "Send a message to Meridian about the bug fix"

// OpenCode calls the custom tool:
await client.call('matrix_send', {
  to: 'meridian',
  message: 'Hey, I fixed the relay room auto-processing bug...'
  // from: derived automatically from working directory
})

// Output:
// ✓ Message sent to @meridian:matrix.oculair.ca
// Room: !abc123:matrix.oculair.ca
```

### From MCP (lower level)

```bash
# Register identity
curl -X POST http://localhost:8016/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "method": "tools/call",
    "params": {
      "name": "matrix_identity",
      "arguments": {
        "action": "register",
        "identity": "opencode-matrix-synapse-deployment",
        "directory": "/opt/stacks/matrix-synapse-deployment"
      }
    }
  }'

# Response:
# {
#   "access_token": "syt_opencode...",
#   "user_id": "@opencode-matrix-synapse-deployment:matrix.oculair.ca",
#   "sync_token": null
# }
```

---

## Benefits

✅ **Scalability**: Works with any agent, any platform (OpenCode, Cursor, Claude Code, etc.)
✅ **Persistence**: Each OpenCode instance has stable Matrix identity
✅ **Autonomy**: No pre-registration needed - auto-provisioned on first use
✅ **History**: All conversations logged in Matrix for future reference
✅ **Network-wide**: Agents can communicate across different systems
✅ **Security**: Tokens session-scoped, identities validated

---

## References

- OpenCode Docs: https://opencode.ai/docs/
- Custom Tools: https://opencode.ai/docs/custom-tools
- Plugins: https://opencode.ai/docs/plugins
- SDK: https://opencode.ai/docs/sdk
- MCP Spec: https://modelcontextprotocol.io/
