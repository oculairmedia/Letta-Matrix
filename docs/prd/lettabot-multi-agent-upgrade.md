# PRD: Lettabot Multi-Agent Upgrade

> **Goal**: Upgrade lettabot from single-agent to multi-agent, replacing the Python Matrix-Letta bridge entirely.

## Background

### Current Architecture (Two Systems)

**Lettabot** (`/opt/stacks/lettabot`) — 2,451 lines TypeScript
- Single agent, single Matrix user (`@lettabot:matrix.oculair.ca`)
- Handles its own DM room only
- Skips all bridge-managed rooms (rooms with `@agent_*` members)
- Uses `@letta-ai/letta-code-sdk` with conversations API, streaming, typing indicators
- Has `<system-reminder>` XML envelope, `<no-reply/>` suppression, group batching, live-edit streaming
- Multi-channel: Matrix, Telegram, Slack, Discord, WhatsApp, Signal

**Python Bridge** (`/opt/stacks/matrix-synapse-deployment`) — ~7,000 lines Python
- 30+ agents, each with dedicated Matrix user and room
- Agent discovery via Letta API polling
- Matrix user provisioning, room creation, space management
- Message routing with 7 filter rules
- Streaming responses (live-edit and progress-then-delete modes)
- Webhook processing, health endpoints, ntfy alerting
- Uses raw HTTP to Letta API (no SDK, no conversations API)

### Why Replace

1. Python bridge uses raw HTTP — lettabot already has the modern SDK patterns
2. Two runtimes (Python + Node.js) caused the double-delivery bug
3. 7,000 lines Python can be replaced by extending 2,451 lines TypeScript
4. Lettabot's streaming, typing, and `<no-reply/>` are superior to the Python bridge's implementation

---

## Architecture

### New Model

Lettabot becomes the **sole Matrix-Letta bridge**. It manages ALL agents:

```
Letta API (30+ agents)
    ↕ @letta-ai/letta-code-sdk
Lettabot (upgraded)
    ↕ matrix-bot-sdk (one client per agent user)
Tuwunel (Matrix homeserver)
    ↕
Element / Identity Bridge / OpenCode
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Matrix identity | One Matrix user per agent | Preserves existing room structure, agents appear as distinct users |
| Matrix client | One `matrix-bot-sdk` client as admin, send-as via API | Single sync loop, post as agent via access tokens |
| Letta SDK | One session per (agent, room) pair | Conversations API gives context isolation per room |
| Agent discovery | Poll Letta API + webhook trigger | Same as Python bridge — proven reliable |
| State storage | PostgreSQL (existing `matrix_letta` DB) | Already has schema, migration path clear |
| Other channels | Unchanged | Telegram/Slack/Discord/WhatsApp/Signal continue as single-agent DM |

### What Changes vs What Stays

**Stays the same (no changes):**
- All non-Matrix channels (Telegram, Slack, Discord, WhatsApp, Signal)
- Single-agent DM behavior for those channels
- `@letta-ai/letta-code-sdk` session management
- Streaming with live-edit
- `<system-reminder>` XML envelope
- `<no-reply/>` suppression
- Group message batching
- Tool loop detection
- Pairing system
- Cron/heartbeat/polling services (these stay single-agent for DM channels)

**Changes:**
- Matrix adapter: single user → multi-user (agent provisioning)
- Store: file-based → PostgreSQL (for multi-agent state)
- Bot core: single agent routing → per-room agent routing
- New: agent lifecycle management (sync loop)
- New: HTTP health/admin API
- New: webhook receiver
- New: ntfy alerting

---

## Changes by Component

### 1. Matrix Adapter (`channels/matrix.ts`)

**Current**: Single bot user, skips bridge-managed rooms, single `MatrixClient`.

**New**: Admin client for sync + per-agent posting.

#### 1.1 Multi-Identity Posting

The admin client runs the sync loop. To post AS an agent, we use the Matrix C-S API directly with the agent's access token (no need for separate `MatrixClient` instances per agent).

```typescript
interface AgentIdentity {
  agentId: string;
  agentName: string;
  matrixUserId: string;   // @agent_meridian_abc123:matrix.oculair.ca
  accessToken: string;
  roomId: string;
  conversationId?: string; // Letta conversation ID for this room
}
```

**Send-as-agent pattern**:
```typescript
async sendAsAgent(agent: AgentIdentity, roomId: string, content: object): Promise<string> {
  const txnId = `letta_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  const res = await fetch(
    `${homeserverUrl}/_matrix/client/v3/rooms/${roomId}/send/m.room.message/${txnId}`,
    {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${agent.accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(content),
    }
  );
  const data = await res.json();
  return data.event_id;
}
```

#### 1.2 Message Handling Changes

Remove the bridge-managed room skip logic. Instead:

1. On `room.message` event, look up room → agent mapping
2. If room has a mapped agent → route to that agent (multi-agent path)
3. If room has no mapping but is a DM with the bot → route to lettabot's own agent (existing single-agent path)
4. Apply message filters (see Section 3)

#### 1.3 Typing Indicators Per Room

Current lettabot sends typing every 4s in a single room. For multi-agent, typing is sent as the agent's identity using the agent's access token.

#### 1.4 Live-Edit Streaming Per Agent

Same live-edit pattern, but `editMessage` uses the agent's access token instead of the bot's.

---

### 2. Agent Store (`core/store.ts`)

**Current**: `lettabot-agent.json` with single `agentId` and `conversationId`.

**New**: Keep the existing JSON store for lettabot's own DM agent. Add a separate `AgentRegistry` backed by PostgreSQL for multi-agent state.

```typescript
interface AgentRegistry {
  getByAgentId(agentId: string): Promise<AgentIdentity | null>;
  getByRoomId(roomId: string): Promise<AgentIdentity | null>;
  getByMatrixUser(mxid: string): Promise<AgentIdentity | null>;
  getAllActive(): Promise<AgentIdentity[]>;
  upsert(agent: AgentIdentity): Promise<void>;
  softDelete(agentId: string): Promise<void>;
  hardDelete(agentId: string): Promise<void>;
  getExpiredRemovals(graceHours: number): Promise<AgentIdentity[]>;
}
```

**Database**: Reuse the existing `agent_mappings` table in PostgreSQL (`postgresql://letta:letta@192.168.50.90:5432/matrix_letta`). Add a `conversation_id` column.

Schema migration:
```sql
ALTER TABLE agent_mappings ADD COLUMN IF NOT EXISTS conversation_id TEXT;
```

---

### 3. Message Filtering

Port all 7 filters from the Python bridge. These apply to multi-agent rooms only (lettabot's own DM rooms keep current filtering).

| # | Filter | Implementation |
|---|--------|----------------|
| 1 | Self-messages | `sender === agentIdentity.matrixUserId` |
| 2 | Historical imports | `content['m.letta_historical'] === true` |
| 3 | Bridge-originated | `content['m.bridge_originated'] === true` |
| 4 | Duplicate events | Event ID dedup with TTL (in-memory Map with 1h expiry) |
| 5 | Room's own agent | `sender === roomAgent.matrixUserId` (except @mention routing) |
| 6 | Unmapped rooms | No agent mapping for room |
| 7 | Disabled agents | Agent ID in `DISABLED_AGENT_IDS` env var |

---

### 4. Message Formatting

Replace the Python bridge's plain-text wrapping with lettabot's existing `<system-reminder>` XML envelope. This is an **upgrade**, not a regression — the XML format gives agents better structured context.

**Current Python format**:
```
[Matrix: @user:domain in Room Name | Format: markdown+html]

Hello world
```

**New format** (lettabot's existing `formatMessageEnvelope`):
```xml
<system-reminder>
  <channel>matrix</channel>
  <chatId>!room:matrix.oculair.ca</chatId>
  <messageId>$event_id</messageId>
  <sender>
    <userId>@user:matrix.oculair.ca</userId>
    <name>Emmanuel</name>
  </sender>
  <timestamp>2026-02-08T21:47:24Z</timestamp>
  <format>markdown</format>
</system-reminder>

Hello world
```

**Inter-agent messages**: Use the same XML envelope with `<trigger>agent_message</trigger>` and sender info showing the originating agent.

**OpenCode user messages**: Detected by `@oc_*` sender prefix. Add `<opencode>true</opencode>` to the envelope so the agent knows to include @mentions in replies.

---

### 5. Bot Core (`core/bot.ts`)

#### 5.1 Agent Routing

Add a routing layer before session creation:

```typescript
private async routeMessage(msg: InboundMessage): Promise<{
  agentId: string;
  conversationId: string | null;
  identity: AgentIdentity;
} | null> {
  if (msg.channel !== 'matrix') {
    // Non-Matrix channels: use lettabot's own agent (unchanged)
    return {
      agentId: this.store.agentId,
      conversationId: this.store.conversationId,
      identity: null,
    };
  }

  // Matrix: look up agent by room
  const agent = await this.agentRegistry.getByRoomId(msg.chatId);
  if (!agent) return null; // Unmapped room

  return {
    agentId: agent.agentId,
    conversationId: agent.conversationId,
    identity: agent,
  };
}
```

#### 5.2 Per-Agent Session Management

Change `processMessage` to accept the routed agent context:

- `createSession(agentId, options)` for new conversations
- `resumeSession(conversationId, options)` for existing conversations
- Save `conversationId` back to `AgentRegistry` after first message

#### 5.3 Per-Agent Message Queue

Current: single global queue processed sequentially.

New: per-agent queues. Messages for different agents can be processed concurrently. Messages for the same agent are still sequential (Letta SDK limitation — one active session per agent).

```typescript
private agentQueues: Map<string, Array<QueuedMessage>> = new Map();
private agentProcessing: Set<string> = new Set();
```

#### 5.4 Response Delivery

When agent is routed from a multi-agent room:
- Send response as the agent's Matrix identity (not lettabot's bot user)
- Include reply context (`m.in_reply_to`) pointing to the original message
- Include `@mention` of the original sender

---

### 6. Agent Lifecycle Manager (NEW)

New module: `core/agent-lifecycle.ts`

#### 6.1 Sync Loop

Runs on a configurable interval (default: 300s) and on webhook trigger.

```
1. Poll Letta API: client.agents.list(limit=500)
2. Compare with AgentRegistry
3. For new agents: provision Matrix user + room
4. For existing agents: check name changes, room drift
5. For removed agents: soft-delete with 2h grace
6. Cleanup expired soft-deletes
```

#### 6.2 Matrix User Provisioning

For each new agent:

1. Generate username: `agent_{safe_name}_{id_suffix}`
2. Register user via Tuwunel admin API (or `/_synapse/admin/v2/users`)
3. Set display name to agent's Letta name
4. Store credentials in `agent_mappings` table

#### 6.3 Room Provisioning

For each new agent:

1. Login as agent user
2. Create room:
   - Name: `"{agent_name} - Letta Agent Chat"`
   - Topic: `"Private chat with Letta agent: {agent_name}"`
   - Preset: `trusted_private_chat`
   - Initial invites: admin, letta bot, OpenCode bridge, Agent Mail bridge
3. Add room to "Letta Agents" space
4. Auto-join all required members
5. Import last 15 messages from Letta history (marked `m.letta_historical`)

#### 6.4 Agent Removal

1. Agent disappears from Letta API → set `removed_at = now()`
2. If agent reappears within 2 hours → cancel (`removed_at = null`)
3. After 2 hours: leave room as all users, remove from space, hard-delete mapping

#### 6.5 Agent Name Changes

During sync, if `agent.name !== stored.agent_name`:
- Update room name
- Update Matrix user display name
- Update stored mapping

---

### 7. HTTP API (NEW)

New module: `api/routes.ts` — Add to lettabot's existing Express server.

#### 7.1 Health Endpoints

| Endpoint | Response |
|----------|----------|
| `GET /health` | `{status, authenticated, timestamp, agent_count}` |
| `GET /health/agent-provisioning` | `{status: healthy|degraded|unhealthy, total, with_rooms, missing}` |

Status: healthy=0 missing, degraded=1-3, unhealthy=4+.

#### 7.2 Agent Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/agents/mappings` | GET | All agent-room mappings |
| `/agents/:agentId/room` | GET | Room for specific agent |
| `/agents/sync` | POST | Trigger immediate sync |

#### 7.3 Webhook Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/webhooks/letta/new-agent` | POST | Trigger agent sync |
| `/webhooks/letta/agent-response` | POST | Process Letta run completion (audit trail) |

Webhook signature verification: `X-Letta-Signature` header, HMAC-SHA256, Stripe format (`t={ts},v1={sig}`).

#### 7.4 Conversation Registration

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/conversations/register` | POST | Register active Matrix conversation (TTL 300s) |

Used by identity bridge to prevent duplicate audit messages.

---

### 8. Inter-Agent Communication

#### 8.1 @Mention Routing

When an agent's response mentions another agent (`@OtherAgent`):
1. Look up mentioned agent in registry
2. Forward message to mentioned agent's room with metadata
3. Prevent self-mentions and already-forwarded messages

#### 8.2 Agent Mail Reverse Bridge

Detect `m.agent_mail` metadata on incoming messages. After Letta response, forward reply back to Agent Mail via MCP.

---

### 9. Alerting (NEW)

New module: `core/alerting.ts`

Send alerts to ntfy for operational events:

| Event | Priority |
|-------|----------|
| Agent auth failure | high |
| Streaming timeout | default |
| Letta API error | default |
| Health check failure | high |

Deduplication: 5-minute window per alert key.

Config: `NTFY_URL`, `NTFY_TOPIC` env vars.

---

### 10. Error Handling

Post errors to the agent's room (as the agent's identity):

| Scenario | Message |
|----------|---------|
| Letta API error | `Sorry, I encountered an error: {error}` |
| Streaming timeout | `Request timed out after {n} seconds` |
| Auth failure | `Failed to login as agent {name}` |

---

## Features to NOT Port

| Feature | Reason |
|---------|--------|
| Letta Code filesystem (`/fs-*` commands) | `LETTA_CODE_ENABLED=false` in production, unused |
| Poll commands (`/poll`) | Low usage, can add later if needed |
| Progress-then-delete streaming mode | Live-edit is strictly better, no reason to keep both |
| Python unit tests | Replaced by new TypeScript tests |
| Legacy conversation tracker (Node.js) | Replaced by conversations API |
| Matrix API FastAPI server (`matrix-api`) | Functionality absorbed into lettabot's Express server |

---

## Migration Plan

### Phase 1: Multi-Agent Store + Lifecycle (No Message Routing Yet)

1. Add PostgreSQL client to lettabot (`pg` or `postgres` package)
2. Implement `AgentRegistry` using existing `agent_mappings` table
3. Implement `AgentLifecycleManager` (sync loop, provisioning, cleanup)
4. Add health endpoints to Express server
5. **Test**: Run alongside Python bridge. Verify agents are discovered, users created, rooms provisioned. Python bridge still handles all message routing.

### Phase 2: Message Routing

1. Remove bridge-managed room skip logic from Matrix adapter
2. Add agent routing to `bot.ts` (room → agent lookup)
3. Implement per-agent session management (conversations API)
4. Implement per-agent message queues
5. Implement send-as-agent (Matrix API with agent access tokens)
6. Port message filters
7. **Test**: Route messages for ONE test agent through lettabot. Python bridge handles the rest.

### Phase 3: Full Cutover

1. Route ALL agents through lettabot
2. Disable Python bridge's message routing (keep API for backward compat)
3. Port inter-agent communication, Agent Mail reverse bridge
4. Port webhook processing, conversation registration
5. Add ntfy alerting
6. **Test**: Full regression — all agents respond, streaming works, typing indicators work, no double delivery.

### Phase 4: Cleanup

1. Stop Python bridge container (`matrix-client`)
2. Stop matrix-api container
3. Remove Python source code
4. Update docker-compose.yml
5. Update AGENTS.md and documentation

---

## Configuration (New Env Vars)

| Variable | Default | Purpose |
|----------|---------|---------|
| `MULTI_AGENT_ENABLED` | false | Enable multi-agent mode |
| `DATABASE_URL` | — | PostgreSQL connection for agent registry |
| `AGENT_SYNC_INTERVAL_S` | 300 | Letta agent sync interval |
| `AGENT_CLEANUP_GRACE_HOURS` | 2 | Soft-delete grace period |
| `MATRIX_ADMIN_USERNAME` | — | Admin MXID for user provisioning |
| `MATRIX_ADMIN_PASSWORD` | — | Admin password |
| `MATRIX_SERVER_NAME` | — | Matrix server name (e.g., matrix.oculair.ca) |
| `LETTA_WEBHOOK_SECRET` | — | Webhook HMAC secret |
| `NTFY_URL` | — | Alert endpoint |
| `NTFY_TOPIC` | — | Alert topic |
| `DISABLED_AGENT_IDS` | — | Comma-separated agent IDs to skip |

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Double delivery during migration | Phase 2 tests with single agent first; `MULTI_AGENT_ENABLED` flag for gradual rollout |
| Agent provisioning breaks existing rooms | Phase 1 reuses existing `agent_mappings` data — no re-provisioning needed |
| Letta SDK can't handle 30+ concurrent sessions | Per-agent queues serialize per agent; SDK sessions are stateless after close |
| Matrix rate limiting with 30+ agent users | Batch operations, respect rate limits, reuse access tokens |
| Regression in non-Matrix channels | No changes to Telegram/Slack/Discord/WhatsApp/Signal code paths |

---

## Success Criteria

1. All 30+ agents respond to messages in their rooms
2. Streaming with live-edit works for all agents
3. Typing indicators appear during processing
4. `<no-reply/>` suppression works (agent stays silent when appropriate)
5. Inter-agent @mention routing works
6. Agent lifecycle: new agents auto-provisioned, removed agents cleaned up after 2h
7. Health endpoints report accurately
8. No double delivery
9. Python bridge container stopped, no regressions
10. Non-Matrix channels (Telegram, etc.) completely unaffected

---

## Estimated Effort

| Phase | Scope | Estimate |
|-------|-------|----------|
| Phase 1: Store + Lifecycle | AgentRegistry, AgentLifecycleManager, health API | 2-3 sessions |
| Phase 2: Message Routing | Router, per-agent sessions, send-as-agent, filters | 3-4 sessions |
| Phase 3: Full Cutover | Inter-agent, webhooks, alerting, testing | 2-3 sessions |
| Phase 4: Cleanup | Remove Python, update config/docs | 1 session |
| **Total** | | **8-11 sessions** |
