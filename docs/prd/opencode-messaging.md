# PRD: OpenCode Instance Messaging

## Overview

Enable Letta agents and other OpenCode instances to send messages directly to active OpenCode instances through the Matrix messaging infrastructure.

## Problem Statement

Currently, the `talk_to_agent` operation only supports communication with Letta agents. When an agent (like Meridian) wants to send information back to an OpenCode instance that initiated a conversation, there's no mechanism to do so. The agent can only hope the OpenCode plugin picks up @mentions, which is unreliable.

OpenCode instances are stateless and ephemeral - they start, do work, and terminate. Unlike Letta agents which persist, OpenCode instances cannot receive queued messages. Messages must be delivered in real-time to active instances only.

## Goals

1. Allow agents to discover which OpenCode instances are currently active
2. Allow agents to send messages to specific active OpenCode instances
3. Ensure messages are only sent to instances that can receive them (no queuing)
4. Provide clear feedback when target instance is unavailable

## Non-Goals

1. Message queuing for offline instances
2. Persistent message history for OpenCode instances
3. OpenCode-to-OpenCode direct messaging (out of scope for v1)

## User Stories

### US1: Agent Responds to OpenCode Request
As a Letta agent, when an OpenCode instance asks me a question via `talk_to_agent`, I want to send my response directly back to that instance so they receive it in their session.

### US2: Agent Discovers Active Instances
As a Letta agent, I want to list currently active OpenCode instances so I know who I can communicate with.

### US3: Agent Handles Unavailable Instance
As a Letta agent, when I try to message an OpenCode instance that has terminated, I want a clear error message so I know the message wasn't delivered.

## Technical Design

### New Operations

#### 1. `opencode_list` - List Active OpenCode Instances

**Input:**
```typescript
{
  operation: "opencode_list"
}
```

**Output:**
```typescript
{
  success: true,
  instances: [
    {
      directory: "/opt/stacks/matrix-synapse-deployment",
      project_name: "matrix-synapse-deployment",
      identity: "@oc_matrix_synapse_deployment_v2:matrix.oculair.ca",
      display_name: "OpenCode: Matrix Synapse Deployment",
      active: true,
      last_seen: "2026-01-22T17:00:00Z",
      rooms: ["!roomId1:matrix.oculair.ca"]
    }
  ],
  count: 1
}
```

**Behavior:**
- Query OpenCode bridge `/registrations` endpoint
- Filter registrations by `lastSeen` within threshold (default: 120 seconds)
- Enrich with identity information from storage
- Return only instances that are confirmed active

#### 2. `talk_to_opencode` - Send Message to OpenCode Instance

**Input:**
```typescript
{
  operation: "talk_to_opencode",
  target: "matrix-synapse-deployment",  // project name OR directory path
  message: "Here is the information you requested..."
}
```

**Output (Success):**
```typescript
{
  success: true,
  target: "matrix-synapse-deployment",
  target_identity: "@oc_matrix_synapse_deployment_v2:matrix.oculair.ca",
  room_id: "!roomId:matrix.oculair.ca",
  event_id: "$eventId",
  message: "Here is the information you requested...",
  note: "Message delivered to active OpenCode instance"
}
```

**Output (Instance Not Active):**
```typescript
{
  success: false,
  error: "OpenCode instance 'matrix-synapse-deployment' is not currently active",
  suggestion: "The instance may have terminated. Use opencode_list to see active instances."
}
```

**Behavior:**
1. Resolve target to directory path (if project name given)
2. Check bridge registrations for active instance with matching directory
3. Verify `lastSeen` is within threshold
4. Get or create OpenCode identity for target directory
5. Find a room where both sender and target are joined
6. Send message with @mention of target identity
7. Return success or appropriate error

### Activity Detection

An OpenCode instance is considered "active" if:
- It has a registration in the bridge
- `lastSeen` timestamp is within the threshold (default: 120 seconds)
- The registration has a valid session ID

### Message Routing

Messages are routed through Matrix rooms:
1. Find rooms where target OpenCode identity is a member
2. Prefer rooms where sender is also a member
3. If no shared room, use the target's default room from config
4. Include @mention of target identity in message body

### Plugin Requirements

For messages to be received, the target OpenCode instance must:
1. Have the Matrix plugin loaded and running
2. Be syncing with Matrix homeserver
3. Be subscribed to the room where message is sent
4. Have @mention detection enabled

## API Changes

### MCP Tool Schema Addition

Add to `matrix_messaging` tool input schema:

```typescript
// New operations
operation: z.enum([
  // ... existing operations
  'opencode_list',
  'talk_to_opencode'
])

// New parameters
target: z.string().optional().describe(
  'Target OpenCode instance - project name or directory path. Required for talk_to_opencode.'
)
```

### Bridge API (Existing)

No changes needed - uses existing `/registrations` endpoint.

## Configuration

### Activity Threshold

Environment variable: `OPENCODE_ACTIVE_THRESHOLD_SECONDS`
Default: `120`

Instances with `lastSeen` older than this are considered inactive.

## Error Handling

| Scenario | Error Message |
|----------|---------------|
| Target not found | "OpenCode instance '{target}' not found. Use opencode_list to see available instances." |
| Target inactive | "OpenCode instance '{target}' is not currently active (last seen: {time})" |
| No shared room | "No shared room found with OpenCode instance '{target}'" |
| Message send failed | "Failed to send message to '{target}': {error}" |

## Success Metrics

1. Messages successfully delivered to active instances
2. Clear error feedback for inactive instances (no silent failures)
3. Agent adoption of `talk_to_opencode` for responses

## Security Considerations

1. Only active, registered instances can receive messages
2. Messages go through Matrix (existing auth/encryption)
3. No ability to spam inactive/non-existent instances

## Implementation Phases

### Phase 1: Core Implementation
- Implement `opencode_list` operation
- Implement `talk_to_opencode` operation
- Add activity threshold filtering

### Phase 2: Plugin Enhancement (if needed)
- Ensure plugin reliably detects @mentions
- Add delivery confirmation mechanism

## Open Questions

1. Should we support targeting by MXID directly?
2. Should `talk_to_opencode` auto-discover rooms or require explicit room?
3. Do we need a heartbeat mechanism to improve activity detection accuracy?

## Dependencies

- OpenCode bridge `/registrations` endpoint (existing)
- Matrix identity storage (existing)
- Matrix room membership (existing)
- OpenCode plugin @mention detection (existing, may need verification)
