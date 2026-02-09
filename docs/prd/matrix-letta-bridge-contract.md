# Matrix-Letta Bridge: Behavioral Contract Specification

> **Purpose**: Defines the observable behaviors of the Matrix-Letta bridge. The TypeScript rewrite MUST satisfy every behavior in this document. Integration tests validate compliance.

## 1. System Overview

The bridge connects Letta AI agents to Matrix chat rooms. Each Letta agent gets:
- A dedicated Matrix user (e.g., `@agent_meridian_abc123:matrix.oculair.ca`)
- A dedicated Matrix room (e.g., "Meridian - Letta Agent Chat")
- Bidirectional message routing: Matrix messages → Letta API, Letta responses → Matrix room

## 2. Agent Lifecycle

### 2.1 Agent Discovery
- **Method**: Poll Letta SDK `client.agents.list(limit=500)`, deduplicate by agent ID
- **Trigger**: Periodic sync loop + webhook `POST /webhook/new-agent`

### 2.2 Matrix User Creation
| Field | Format |
|-------|--------|
| Username | `agent_{safe_name}_{id_suffix}` |
| Full MXID | `@{username}:matrix.oculair.ca` |
| Display name | Agent's Letta display name |
| Password | Secure random, stored in PostgreSQL |

### 2.3 Room Creation
| Property | Value |
|----------|-------|
| Name | `"{agent_name} - Letta Agent Chat"` |
| Topic | `"Private chat with Letta agent: {agent_name}"` |
| Preset | `trusted_private_chat` |
| Guest access | `forbidden` |
| History visibility | `shared` |

**Initial members** (invited + auto-joined):
- `@admin:matrix.oculair.ca`
- `@letta:matrix.oculair.ca`
- `@oc_matrix_synapse_deployment:matrix.oculair.ca` (OpenCode bridge)
- `@agent_mail_bridge:matrix.oculair.ca`

**Post-creation**:
- Room added to "Letta Agents" space
- Last 15 messages imported from Letta history (marked `m.letta_historical`)

### 2.4 Agent Name Changes
- Detected during sync by comparing stored name vs Letta name
- Updates: room name, Matrix user display name, stored mapping

### 2.5 Agent Removal (Soft Delete)
1. Agent disappears from Letta → mark `removed_at = now()`
2. Grace period: **2 hours**
3. If agent reappears within grace: cancel removal (`removed_at = null`)
4. After grace: hard delete — leave room (all users), remove from space, delete DB mapping

### 2.6 Room Drift Detection
- If stored room_id doesn't match actual room: update mapping
- If stored room doesn't exist: recreate room

## 3. Message Routing (Matrix → Letta)

### 3.1 Event Types Processed
- `m.room.message` (text) — Primary
- `org.matrix.msc3381.poll.response` — Poll votes
- File uploads (m.image, m.file) — Forwarded to Letta

### 3.2 Message Filtering (MUST NOT forward)

| Filter | Condition |
|--------|-----------|
| Self-messages | `sender == bridge_user_id` |
| Historical imports | `m.letta_historical` flag in event content |
| Bridge-originated | `m.bridge_originated` flag in event content |
| Duplicate events | Same `event_id` within 3600s TTL |
| Room's own agent | `sender == room_agent_user_id` (except @mention routing) |
| Unmapped rooms | No agent mapping for room |
| Disabled agents | Agent ID in `DISABLED_AGENT_IDS` |

### 3.3 Message Formatting

**Standard user message**:
```
[Matrix: @user:domain in Room Name | Format: markdown+html]

{message body}
```

**Inter-agent message** (from another agent's user):
```
[INTER-AGENT MESSAGE from AgentName]

{message body}

---
SYSTEM NOTE (INTER-AGENT COMMUNICATION)
The message above is from another Letta agent: AgentName (ID: agent-uuid).
Treat this as your MAIN task for this turn; the other agent is trying to
collaborate with you.
```

**OpenCode user message** (from `@oc_*` user):
```
[MESSAGE FROM OPENCODE USER]

{message body}

---
RESPONSE INSTRUCTION (OPENCODE BRIDGE):
This message is from an OpenCode user: @oc_username:domain
When you respond to this message, you MUST include their @mention (@oc_username:domain)
in your response so the OpenCode bridge can route your reply to them.
```

**Poll vote**:
```
[POLL VOTE] @voter:domain voted for: Option Name
(Poll: Original Question)
```

### 3.4 Letta API Call
- **Current**: `POST /v1/agents/{agent_id}/messages` (raw HTTP)
- **Target**: `@letta-ai/letta-code-sdk` with `session.send()` + conversations API
- **Conversations**: When `LETTA_CONVERSATIONS_ENABLED=true`, use conversation ID per room for context isolation

## 4. Response Delivery (Letta → Matrix)

### 4.1 Streaming Modes

**Mode A: Live-Edit** (`LETTA_STREAMING_LIVE_EDIT=true`)
- First event creates a message
- Subsequent events edit same message (debounced 0.5s)
- Final assistant response replaces entire message body
- User sees: single message that evolves

**Mode B: Progress-Then-Delete** (`LETTA_STREAMING_LIVE_EDIT=false`)
- Tool calls appear as progress: `tool_name...`
- Tool returns appear as: `tool_name` or `tool_name (failed)`
- Progress messages deleted when next event arrives
- Final assistant response stays as permanent message

**Mode C: Non-Streaming** (`LETTA_STREAMING_ENABLED=false`)
- Wait for complete response, send single message

### 4.2 Stream Event Types

| Event | Display | Behavior |
|-------|---------|----------|
| `PING` | Hidden | Keepalive, ignored |
| `REASONING` | Hidden | Internal thinking (filtered) |
| `TOOL_CALL` | Progress | Tool invocation |
| `TOOL_RETURN` | Progress | Tool result |
| `ASSISTANT` | Final | Text response (permanent) |
| `STOP` | Hidden | Stream end |
| `USAGE` | Hidden | Token stats |
| `ERROR` | Error | `error message` |
| `APPROVAL_REQUEST` | Prompt | Tool approval needed |

### 4.3 Response Posting
- Sent as agent's Matrix user (not bridge user)
- Includes rich reply context (`m.in_reply_to`) when replying
- Includes `@mention` of original sender (`m.mentions`)

### 4.4 Timeouts
- Total: `LETTA_STREAMING_TIMEOUT` (default 120s)
- Idle: `LETTA_STREAMING_IDLE_TIMEOUT` (default 120s)
- On timeout: post error to room + alert via ntfy

## 5. Inter-Agent Communication

### 5.1 @Mention Routing
- Agent mentions `@OtherAgent` in response
- Message forwarded to mentioned agent's room with `[Forwarded from SenderName]` prefix
- Prevents self-mentions and already-forwarded messages

### 5.2 Agent Mail Reverse Bridge
- Detects `m.agent_mail` metadata on incoming messages
- After Letta response, forwards reply back to Agent Mail via MCP

## 6. Special Commands

### 6.1 Poll Commands (parsed from agent responses)

| Command | Behavior |
|---------|----------|
| `/poll "Q?" "A" "B" "C"` | Create disclosed poll |
| `/poll undisclosed "Q?" "A" "B"` | Create undisclosed poll |
| `/poll-results $event_id` | Show poll results |
| `/poll-close $event_id` | Close poll, show final results |

### 6.2 Filesystem Commands (Letta Code)

| Command | Behavior |
|---------|----------|
| `/fs-link [path]` | Link agent to filesystem project |
| `/fs-run [--path=...] prompt` | Run filesystem task |
| `/fs-task [on\|off\|status]` | Toggle filesystem mode |

> **Note**: Letta Code integration (`LETTA_CODE_ENABLED`) may be dropped in rewrite — evaluate during implementation.

## 7. HTTP API

### 7.1 Health Endpoints

| Endpoint | Response |
|----------|----------|
| `GET /health` | `{status, authenticated, timestamp, agent_sync_available}` |
| `GET /health/agent-provisioning` | `{status: healthy\|degraded\|unhealthy, total_agents, agents_with_rooms, missing_count}` |

Status thresholds: healthy=0 missing, degraded=1-3 missing, unhealthy=4+ missing.

### 7.2 Agent Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/agents/mappings` | GET | All agent-room mappings |
| `/agents/{agent_id}/room` | GET | Room for specific agent |
| `/agents/matrix-memory/sync` | POST | Sync matrix_capabilities block |

### 7.3 Webhook Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/webhook/new-agent` | POST | Trigger agent sync |
| `/webhooks/letta/agent-response` | POST | Process Letta run completion |

### 7.4 Conversation Registration

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/conversations/register` | POST | Register active Matrix conversation (TTL 300s) |

### 7.5 Webhook Signature Verification
- Header: `X-Letta-Signature`
- Format: `t={timestamp},v1={signature}`
- Algorithm: `HMAC-SHA256("{timestamp}.{body}", LETTA_WEBHOOK_SECRET)`
- Skip if `NODE_ENV=development` or secret not set

## 8. Error Handling

### 8.1 User-Visible Errors (posted to room)

| Scenario | Message |
|----------|---------|
| Letta API error | `Sorry, I encountered an error while processing your message: {error[:100]}` |
| Streaming timeout | `Request timed out after {timeout} seconds` |
| Auth failure | `Failed to login as agent {username}` |
| File upload error | `File upload error: {error}` |

### 8.2 Alerting (ntfy)
- Auth failures (priority: high)
- Streaming timeouts (priority: default)
- Letta API errors (priority: default)
- Health check failures (priority: high)
- Deduplication: 5-minute window per alert key

## 9. Concurrency

### 9.1 Active Task Tracking
- Keyed by `(room_id, agent_id)`
- Prevents concurrent processing in same room
- Sends "Still processing..." notice if duplicate
- All tasks cancelled on shutdown

### 9.2 Event Deduplication
- SQLite-backed dedupe store
- TTL: 3600 seconds
- Atomic INSERT prevents race conditions

## 10. Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `LETTA_API_URL` | — | Letta server URL |
| `LETTA_TOKEN` | — | Letta API key |
| `MATRIX_HOMESERVER_URL` | — | Tuwunel URL |
| `MATRIX_USERNAME` | — | Bridge bot MXID |
| `MATRIX_PASSWORD` | — | Bridge bot password |
| `MATRIX_ADMIN_USERNAME` | — | Admin MXID |
| `MATRIX_ADMIN_PASSWORD` | — | Admin password |
| `DATABASE_URL` | — | PostgreSQL connection |
| `LETTA_STREAMING_ENABLED` | false | Enable streaming |
| `LETTA_STREAMING_TIMEOUT` | 120.0 | Stream timeout (s) |
| `LETTA_STREAMING_IDLE_TIMEOUT` | 120.0 | Idle timeout (s) |
| `LETTA_STREAMING_LIVE_EDIT` | false | Edit-in-place mode |
| `LETTA_CONVERSATIONS_ENABLED` | false | Conversations API |
| `LETTA_CODE_ENABLED` | true | Filesystem mode |
| `DISABLED_AGENT_IDS` | "" | Disabled agents (CSV) |
| `MATRIX_AGENT_SYNC_INTERVAL` | 300 | Sync interval (s) |
| `LETTA_WEBHOOK_SECRET` | — | Webhook HMAC secret |
| `NTFY_URL` | — | Alert endpoint |
| `NTFY_TOPIC` | — | Alert topic |

## 11. Database Schema

### agent_mappings
| Column | Type | Notes |
|--------|------|-------|
| agent_id | PK String | Letta agent ID |
| agent_name | String | Display name |
| matrix_user_id | String (unique) | Full MXID |
| matrix_password | String | Stored password |
| room_id | String (unique, nullable) | Matrix room ID |
| room_created | Boolean | Room exists |
| created_at | DateTime | First seen |
| updated_at | DateTime | Last modified |
| removed_at | DateTime (nullable) | Soft delete timestamp |

### invitation_status
| Column | Type | Notes |
|--------|------|-------|
| agent_id | FK → agent_mappings | Agent reference |
| invitee | String | MXID of invited user |
| status | String | joined/failed/pending |

## 12. Rewrite Decisions

### Keep (must have for parity)
- Agent sync loop with Letta polling
- Matrix user/room provisioning
- All 7 message filters
- All 3 message format types (standard, inter-agent, opencode)
- Streaming with live-edit mode
- Webhook processing with signature verification
- Health endpoints
- Agent soft-delete with 2h grace
- @mention routing
- ntfy alerting
- Concurrent task tracking

### Upgrade (use lettabot patterns)
- `@letta-ai/letta-code-sdk` instead of raw HTTP
- Conversations API (`createSession`/`resumeSession`) instead of stateless messages
- `<system-reminder>` XML envelope instead of plain-text context wrapping
- Typing indicators (refresh every 4s)
- `<no-reply/>` suppression
- Group message batching

### Evaluate (may drop)
- Letta Code filesystem integration (`/fs-*` commands)
- Poll commands (`/poll`)
- Agent Mail reverse bridge
- Identity API endpoints (may stay in identity bridge)
- DM room management (may stay in identity bridge)

### New (from lettabot)
- Bridge-managed room detection (skip rooms with `@agent_*` members)
- Message edits via `m.relates_to.rel_type: "m.replace"`
- Tool loop detection in streaming
- Tool approval management
