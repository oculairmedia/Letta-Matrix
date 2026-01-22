# Implementation Plan: OpenCode Instance Messaging

## Overview

This document details the implementation steps for the OpenCode messaging feature defined in `opencode-messaging.md`.

## File Changes

### 1. Schema Updates

**File:** `mcp-servers/matrix-identity-bridge/src/tools/MatrixMessaging.ts`

Add new operations to the schema:

```typescript
operation: z.enum([
  // ... existing
  'opencode_list',
  'talk_to_opencode'
])

target: z.string().optional().describe(
  'Target OpenCode instance for talk_to_opencode. Can be project name (e.g., "matrix-synapse-deployment") or full directory path.'
)
```

Update tool description to document new operations.

### 2. OpenCode Service Extensions

**File:** `mcp-servers/matrix-identity-bridge/src/opencode/opencode-service.ts`

Add methods:

```typescript
/**
 * Get active OpenCode instances from bridge registrations
 */
async getActiveInstances(thresholdSeconds: number = 120): Promise<ActiveOpenCodeInstance[]>

/**
 * Find active instance by project name or directory
 */
async findActiveInstance(target: string): Promise<ActiveOpenCodeInstance | undefined>

/**
 * Check if an instance is currently active
 */
async isInstanceActive(directory: string): Promise<boolean>
```

Types:
```typescript
interface ActiveOpenCodeInstance {
  directory: string;
  projectName: string;
  identity: MatrixIdentity;
  registration: OpenCodeBridgeRegistration;
  lastSeen: Date;
  rooms: string[];
}
```

### 3. Operation Handlers

**File:** `mcp-servers/matrix-identity-bridge/src/tools/MatrixMessaging.ts`

#### `opencode_list` Handler

```typescript
case 'opencode_list': {
  const instances = await ctx.openCodeService.getActiveInstances();
  
  return result({
    success: true,
    instances: instances.map(inst => ({
      directory: inst.directory,
      project_name: inst.projectName,
      identity: inst.identity.mxid,
      display_name: inst.identity.displayName,
      active: true,
      last_seen: inst.lastSeen.toISOString(),
      rooms: inst.rooms
    })),
    count: instances.length
  });
}
```

#### `talk_to_opencode` Handler

```typescript
case 'talk_to_opencode': {
  const target = requireParam(input.target, 'target');
  const message = requireParam(input.message, 'message');
  
  // 1. Find active instance
  const instance = await ctx.openCodeService.findActiveInstance(target);
  if (!instance) {
    // Check if instance exists but is inactive
    const identity = await ctx.openCodeService.getIdentityByTarget(target);
    if (identity) {
      return result({
        success: false,
        error: `OpenCode instance '${target}' is not currently active`,
        suggestion: 'The instance may have terminated. Use opencode_list to see active instances.',
        last_known_identity: identity.mxid
      });
    }
    return result({
      success: false,
      error: `OpenCode instance '${target}' not found`,
      suggestion: 'Use opencode_list to see available instances.'
    });
  }
  
  // 2. Resolve sender identity
  const senderIdentity = await resolveCallerIdentity(ctx, callerDirectory, callerName, effectiveSource);
  
  // 3. Find shared room or use instance's room
  const roomId = await findSharedRoom(ctx, senderIdentity, instance);
  if (!roomId) {
    return result({
      success: false,
      error: `No shared room found with OpenCode instance '${target}'`,
      suggestion: 'Ensure both identities are members of a common room.'
    });
  }
  
  // 4. Send message with @mention
  const mentionMessage = `${instance.identity.mxid} ${message}`;
  const client = await ctx.clientPool.getClient(senderIdentity);
  const eventId = await client.sendMessage(roomId, {
    msgtype: 'm.text',
    body: mentionMessage
  });
  
  return result({
    success: true,
    target: instance.projectName,
    target_identity: instance.identity.mxid,
    room_id: roomId,
    event_id: eventId,
    message: message,
    note: 'Message delivered to active OpenCode instance'
  });
}
```

### 4. Helper Functions

**File:** `mcp-servers/matrix-identity-bridge/src/opencode/opencode-service.ts`

```typescript
/**
 * Extract project name from directory path
 */
extractProjectName(directory: string): string {
  return directory.split('/').filter(Boolean).pop() || 'unknown';
}

/**
 * Match target string to directory
 * Supports: full path, project name, partial match
 */
matchTarget(target: string, directory: string): boolean {
  // Exact directory match
  if (target === directory) return true;
  
  // Project name match
  const projectName = this.extractProjectName(directory);
  if (target.toLowerCase() === projectName.toLowerCase()) return true;
  
  // Partial match (contains)
  if (directory.toLowerCase().includes(target.toLowerCase())) return true;
  
  return false;
}
```

### 5. Room Resolution

**File:** `mcp-servers/matrix-identity-bridge/src/opencode/opencode-service.ts`

```typescript
/**
 * Find a room where both sender and target can communicate
 */
async findSharedRoom(
  senderIdentity: MatrixIdentity,
  targetInstance: ActiveOpenCodeInstance
): Promise<string | undefined> {
  // 1. Check target's registered rooms
  for (const roomId of targetInstance.rooms) {
    // Verify sender can access this room
    const senderClient = await this.clientPool.getClient(senderIdentity);
    try {
      const joinedRooms = await senderClient.getJoinedRooms();
      if (joinedRooms.includes(roomId)) {
        return roomId;
      }
    } catch {
      continue;
    }
  }
  
  // 2. Fallback: try to join sender to target's first room
  if (targetInstance.rooms.length > 0) {
    const roomId = targetInstance.rooms[0];
    try {
      const senderClient = await this.clientPool.getClient(senderIdentity);
      await senderClient.joinRoom(roomId);
      return roomId;
    } catch {
      return undefined;
    }
  }
  
  return undefined;
}
```

## Testing Plan

### Unit Tests

1. `getActiveInstances()` - filters by threshold correctly
2. `findActiveInstance()` - matches by name and directory
3. `matchTarget()` - handles various input formats
4. `extractProjectName()` - extracts correctly from paths

### Integration Tests

1. `opencode_list` returns active instances
2. `opencode_list` excludes stale registrations
3. `talk_to_opencode` delivers to active instance
4. `talk_to_opencode` fails gracefully for inactive instance
5. `talk_to_opencode` fails gracefully for unknown instance
6. Message includes correct @mention format

### Manual Testing

1. Start OpenCode instance A
2. From Letta agent, call `opencode_list` - verify A appears
3. From Letta agent, call `talk_to_opencode` to A - verify delivery
4. Stop OpenCode instance A
5. From Letta agent, call `opencode_list` - verify A no longer appears
6. From Letta agent, call `talk_to_opencode` to A - verify error

## Rollout Plan

1. Implement and test locally
2. Deploy to staging (if available)
3. Test with real Letta agents
4. Deploy to production
5. Update agent prompts to use new operations

## Documentation Updates

1. Update MCP tool description with new operations
2. Add examples to tool help text
3. Document in AGENTS.md for agent awareness

## Estimated Effort

| Task | Estimate |
|------|----------|
| Schema updates | 30 min |
| OpenCode service extensions | 2 hours |
| Operation handlers | 2 hours |
| Room resolution logic | 1 hour |
| Testing | 2 hours |
| Documentation | 30 min |
| **Total** | **8 hours** |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Bridge registrations stale | Use conservative threshold, add heartbeat if needed |
| Room access issues | Graceful fallback, clear error messages |
| Plugin not receiving messages | Verify plugin config, add troubleshooting docs |

## Success Criteria

1. Agents can list active OpenCode instances
2. Agents can send messages to active instances
3. Messages are received by target OpenCode sessions
4. Inactive instances return clear errors (no silent failures)
5. No message queuing or persistence (by design)
