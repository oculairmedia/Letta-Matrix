# PRD: LettaBot WebSocket Gateway for Multi-Agent Streaming

**Bead**: bd-d7f9.8 (epic)
**Status**: Draft — Pending design decision (see Critical Questions)
**Author**: OpenCode
**Date**: 2026-02-09

## Problem

The Python bridge (`matrix-synapse-deployment`) calls the Letta REST API directly via `letta-client` Python SDK. This works but:

1. **No letta-code features** — the `@letta-ai/letta-code-sdk` provides session management, tool approval handling, filesystem tools, and conversation lifecycle that the REST API doesn't expose.
2. **Duplicated logic** — both lettabot and the bridge independently implement conversation management, retry logic, streaming parsing, and error recovery.
3. **No unified streaming protocol** — the bridge uses raw `conversations.messages.create(streaming=True)` with manual chunk parsing, while lettabot gets clean `session.send()` / `session.stream()` semantics.

## Critical Risk: letta-code-sdk is NOT Multi-Agent Safe

**Each `Session` spawns a separate `letta-code` CLI subprocess** (`spawn("node", [cliPath])`) that:
- Consumes ~50-100MB RAM per process
- Has NO reconnection capability — if the subprocess dies, the session is dead
- Has NO health checks for stale sessions
- Has NO message delivery guarantees (known issue #22)
- Has NO streaming resume — interrupted streams lose messages permanently

**Lettabot also serializes all messages** through a `this.processing` lock — only one message processes at a time. With 80+ agents, queue backlog would be catastrophic (minutes of delay).

**Conclusion**: `letta-code-sdk` CANNOT be used as a general-purpose gateway for all agents. It was designed for single-user interactive CLI sessions.

## Solution

Lettabot exposes a **WebSocket API** that the Python bridge connects to. All agent messages route through lettabot, which manages letta-code-sdk sessions internally.

```
Matrix room → Python bridge → WebSocket → Lettabot → letta-code-sdk → Letta Server
                                              ↓ (subprocess per session)
                                         letta-code CLI
```

## Architecture

### How letta-code-sdk Works (Key Insight)

Each `Session` spawns a **separate `letta-code` CLI subprocess**:
- `createSession(agentId)` → `node letta.js --agent {agentId} --output-format stream-json`
- `resumeSession(conversationId)` → `node letta.js --conversation {conversationId} --output-format stream-json`
- Communication is via stdin/stdout JSON lines
- Each subprocess is independent — multiple sessions can run in parallel

This means lettabot doesn't need to become "multi-agent" internally. Each WebSocket connection gets its own session with the requested agent.

### WebSocket Protocol

**Endpoint**: `ws://lettabot:8080/api/v1/agent-gateway`

**Authentication**: `X-Api-Key` header on upgrade request (same as existing HTTP API)

#### Client → Server Messages

```jsonc
// Start a session for an agent
{"type": "session_start", "agent_id": "agent-xxx", "conversation_id": "conv-xxx"}

// Send a message to the agent
{"type": "message", "content": "Hello agent", "request_id": "req-1"}

// Abort current operation
{"type": "abort", "request_id": "req-1"}

// Close session gracefully
{"type": "session_close"}
```

#### Server → Client Messages

```jsonc
// Session initialized
{"type": "session_init", "agent_id": "agent-xxx", "conversation_id": "conv-xxx", "session_id": "ses-xxx"}

// Streaming events (forwarded from letta-code-sdk)
{"type": "stream", "event": "assistant", "content": "Hello!", "uuid": "msg-xxx"}
{"type": "stream", "event": "tool_call", "tool_name": "Read", "tool_call_id": "tc-xxx", "uuid": "msg-xxx"}
{"type": "stream", "event": "tool_result", "content": "file contents...", "tool_call_id": "tc-xxx", "uuid": "msg-xxx"}
{"type": "stream", "event": "reasoning", "content": "thinking...", "uuid": "msg-xxx"}

// Terminal event
{"type": "result", "success": true, "conversation_id": "conv-xxx", "request_id": "req-1"}

// Error
{"type": "error", "code": "CONVERSATION_BUSY", "message": "Agent is processing another request", "request_id": "req-1"}
```

### Responsibility Split

| Concern | Owner | Rationale |
|---------|-------|-----------|
| Matrix sync loop | Python bridge | Already works, no change |
| Message formatting (system-reminder envelope) | Python bridge | Knows Matrix context (sender, room, timestamps) |
| Agent identity posting (send as @agent_xxx) | Python bridge | Owns Matrix identity pool |
| Typing indicators | Python bridge | Sends as agent identity |
| No-reply suppression | Python bridge | Post-processing of response |
| Live-edit streaming UI | Python bridge | Matrix-specific (edit messages in-place) |
| Session lifecycle (create/resume/close) | Lettabot gateway | Owns letta-code-sdk |
| Conversation-to-agent mapping | Lettabot gateway | One session per WS connection |
| Tool approval handling | Lettabot gateway | letta-code-sdk handles internally |
| Streaming event parsing | Lettabot gateway | Normalizes letta-code events to WS protocol |
| 409 BUSY retry | Lettabot gateway | Session-level retry before sending error to client |

### Session Lifecycle

```
1. Python bridge receives Matrix message
2. Bridge formats message with system-reminder envelope
3. Bridge opens WS connection to lettabot (or reuses existing)
4. Bridge sends: {"type": "session_start", "agent_id": "xxx"}
5. Lettabot creates Session(agentId) → spawns letta-code subprocess
6. Lettabot sends: {"type": "session_init", ...}
7. Bridge sends: {"type": "message", "content": "<system-reminder>..."}
8. Lettabot calls session.send() → session.stream()
9. Stream events forwarded to bridge via WS
10. Bridge processes events (live-edit, no-reply check, etc.)
11. Bridge posts final response to Matrix as agent identity
12. Session stays alive for future messages to same agent
```

### Connection Pooling

The bridge maintains a **pool of WebSocket connections**, one per active agent:
- Key: `agent_id`
- Value: WebSocket connection + session state
- Idle timeout: 5 minutes (close session, release subprocess)
- Max concurrent: configurable (default: 20)

When a message arrives for an agent:
1. Check pool for existing connection
2. If exists and healthy: reuse (send message on existing session)
3. If not: create new WS connection + session_start

### Failure Handling

| Failure | Behavior |
|---------|----------|
| WS connection drops | Bridge retries connection, creates new session |
| letta-code subprocess crashes | Lettabot detects, sends error event, bridge retries |
| Lettabot container restarts | All WS connections drop, bridge reconnects on next message |
| 409 CONVERSATION_BUSY | Lettabot retries internally (3x with backoff), then sends error to bridge |
| Session initialize timeout | Lettabot sends error, bridge falls back to direct Letta API |

### Fallback Strategy

The bridge should support **graceful degradation**:
- If lettabot WS is unavailable, fall back to direct `letta-client` SDK calls
- Feature flag: `LETTA_GATEWAY_ENABLED=true/false`
- Metric: track which path is used for observability

## Implementation Plan

### Phase 1: Lettabot WebSocket Server (lettabot repo)

1. Add `ws` dependency
2. Create `src/api/ws-gateway.ts` — WebSocket upgrade handler
3. Create `src/api/agent-session-manager.ts` — manages Session instances per WS connection
4. Wire into existing `api/server.ts` HTTP server (upgrade event)
5. Implement protocol: session_start → session_init, message → stream events
6. Add connection health checks (ping/pong)
7. Add idle session cleanup

### Phase 2: Python Bridge WebSocket Client (matrix-synapse-deployment repo)

1. Create `src/letta/ws_gateway_client.py` — async WS client with connection pooling
2. Create `src/letta/gateway_stream_reader.py` — parse WS stream events into existing `StreamEvent` format
3. Modify `send_to_letta_api_streaming()` — if gateway enabled, route through WS client
4. Modify `send_to_letta_api()` — same for non-streaming path
5. Add `LETTA_GATEWAY_URL` and `LETTA_GATEWAY_ENABLED` config
6. Keep existing direct API path as fallback

### Phase 3: Migration & Cleanup

1. Enable gateway for a subset of agents (canary)
2. Monitor for regressions
3. Gradually route all agents through gateway
4. Remove direct Letta API calls from bridge (or keep as fallback)
5. Remove `ConversationService` from bridge (lettabot owns this now)
6. Remove `StepStreamReader` from bridge (replaced by gateway stream reader)

## Constraints

- No regressions — existing bridge features must continue working
- Lettabot's existing channels (Telegram, Slack, etc.) must not be affected
- The WS gateway is additive — it doesn't change lettabot's internal message processing for its own channels
- Must handle 80+ agents concurrently (current agent count)

## Open Questions

1. Should conversation IDs be managed by the bridge or the gateway? Currently the bridge maps room→conversation via ConversationService. If the gateway owns sessions, should it also own conversation mapping?
2. Should the WS connection be per-agent or per-bridge-instance? Per-agent gives isolation but means 80+ connections. Per-instance with multiplexing is more efficient but adds protocol complexity.
3. How to handle file uploads? Currently the bridge uploads files via Letta API directly. Should this also route through the gateway?
