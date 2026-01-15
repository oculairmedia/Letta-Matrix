# Context Injection Analysis: oh-my-opencode vs p2p-bridge

## Problem with Current p2p-bridge Implementation

Your p2p-bridge plugin is **injecting messages as new prompts** using `client.session.prompt()`, which:
- Creates a **new user message** in the conversation
- Forces the agent to respond to each P2P message
- Does **NOT** inject context into the current conversation flow

## How oh-my-opencode Injects Context

oh-my-opencode uses a **3-layer context injection system**:

### 1. Context Collector (Centralized Registry)
**File:** `src/features/context-injector/collector.ts`

```typescript
class ContextCollector {
  private sessions: Map<string, Map<string, ContextEntry>>
  
  // Register context to be injected later
  register(sessionID: string, options: RegisterContextOptions): void
  
  // Get pending context without consuming
  getPending(sessionID: string): PendingContext
  
  // Get and clear pending context
  consume(sessionID: string): PendingContext
}
```

**Key insight:** Context is **registered** but not immediately injected. It's held until the right moment.

### 2. Hook-Based Injection Points
**File:** `src/features/context-injector/injector.ts`

Two injection strategies:

#### Strategy A: `chat.message` Hook (Older)
```typescript
"chat.message": async (input, output) => {
  // Inject into the parts array BEFORE message is sent
  const result = injectPendingContext(collector, input.sessionID, output.parts)
}
```

#### Strategy B: `experimental.chat.messages.transform` Hook (Newer, Better)
```typescript
"experimental.chat.messages.transform": async (_input, output) => {
  // Find last user message
  const lastUserMessage = messages[lastUserMessageIndex]
  
  // Prepend context to the text part
  textPart.text = `${pending.merged}\n\n---\n\n${originalText}`
}
```

**Key insight:** Context is **prepended** to existing messages, not added as new messages.

### 3. Directory-Specific Injectors
**File:** `src/hooks/directory-readme-injector/index.ts`

Pattern used for injecting README.md files:

```typescript
"tool.execute.after": async (input, output) => {
  // When a file is read, find README.md in parent directories
  const readmePaths = findReadmeMdUp(dir)
  
  // Append to tool output (not as new message!)
  output.output += `\n\n[Project README: ${readmePath}]\n${content}`
}
```

**Key insight:** Context is appended to **tool outputs**, making it part of the conversation context.

## How to Fix p2p-bridge

### Option 1: Use Context Collector Pattern (Recommended)

1. **Register P2P messages** instead of prompting:
```typescript
// Instead of:
await client.session.prompt({ ... })

// Do this:
contextCollector.register(sessionID, {
  id: `p2p-${Date.now()}`,
  source: "p2p-bridge",
  content: `[P2P Message from ${identity}]\n${content}`,
  priority: "high"
})
```

2. **Create a hook** to inject on next user message:
```typescript
return {
  "experimental.chat.messages.transform": async (_input, output) => {
    // Context will be automatically prepended to next user message
  }
}
```

### Option 2: Append to Tool Output (Simpler)

If you have a tool that's frequently called, append P2P messages to its output:

```typescript
"tool.execute.after": async (input, output) => {
  const pending = getPendingP2PMessages(input.sessionID)
  if (pending.length > 0) {
    output.output += `\n\n[P2P Messages]\n${pending.join('\n')}`
  }
}
```

### Option 3: Use Supervisor Tag (OpenCode-Specific)

Inject as supervisor context (appears in gray box):

```typescript
// This would require using the supervisor mechanism
// Check if OpenCode SDK supports this
```

## Key Differences Summary

| Aspect | Your Current Approach | oh-my-opencode Approach |
|--------|----------------------|------------------------|
| **Injection Method** | `client.session.prompt()` | Hook into `chat.message` or `messages.transform` |
| **Message Type** | New user message | Prepended to existing message |
| **Agent Response** | Required for each P2P msg | No response needed |
| **Context Flow** | Interrupts conversation | Seamless integration |
| **Timing** | Immediate | Deferred until next user action |

## Recommended Implementation

1. Create a **message queue** for P2P messages
2. Use `experimental.chat.messages.transform` hook to **prepend** queued messages
3. Clear queue after injection
4. Optionally use **priority levels** for urgent messages

This way, P2P messages become **context** rather than **prompts**.

