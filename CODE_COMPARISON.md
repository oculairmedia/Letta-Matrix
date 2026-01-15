# Code Comparison: Old vs New P2P Bridge

## Side-by-Side Comparison

### Message Handling

#### OLD (p2p-bridge.ts) - Lines 64-112
```typescript
async function handleIncomingMessage(identity: string, content: string) {
  if (identity === P2P_IDENTITY) return
  
  client.app.log({
    body: {
      service: "p2p-bridge",
      level: "info",
      message: `Received from ${identity}`,
      extra: { content: content.substring(0, 100) }
    }
  }).catch(() => {})

  const sessionId = await getActiveSessionId()
  if (!sessionId) {
    client.app.log({
      body: {
        service: "p2p-bridge",
        level: "warn",
        message: "No active session to inject message into"
      }
    }).catch(() => {})
    return
  }

  try {
    const formattedMessage = `[P2P Message from ${identity}]\n${content}`
    
    // ❌ PROBLEM: Creates a new user message
    await client.session.prompt({
      path: { id: sessionId },
      body: {
        parts: [{ type: "text", text: formattedMessage }],
      },
    })
    
    client.app.log({
      body: {
        service: "p2p-bridge",
        level: "info",
        message: `Injected message into session ${sessionId}`
      }
    }).catch(() => {})
  } catch (e: any) {
    client.app.log({
      body: {
        service: "p2p-bridge", 
        level: "error",
        message: `Failed to inject prompt: ${e.message}`
      }
    }).catch(() => {})
  }
}
```

#### NEW (p2p-bridge-fixed.ts) - Lines 70-95
```typescript
function queueMessage(sessionId: string, identity: string, content: string) {
  if (!pendingMessages.has(sessionId)) {
    pendingMessages.set(sessionId, [])
  }
  
  pendingMessages.get(sessionId)!.push({
    identity,
    content,
    timestamp: Date.now()
  })
  
  client.app.log({
    body: {
      service: "p2p-bridge",
      level: "info",
      message: `Queued message from ${identity} for session ${sessionId}`,
      extra: { queueSize: pendingMessages.get(sessionId)!.length }
    }
  }).catch(() => {})
}

async function handleIncomingMessage(identity: string, content: string) {
  if (identity === P2P_IDENTITY) return
  
  const sessionId = await getActiveSessionId()
  if (!sessionId) {
    client.app.log({
      body: {
        service: "p2p-bridge",
        level: "warn",
        message: "No active session to queue message for"
      }
    }).catch(() => {})
    return
  }

  // ✅ SOLUTION: Just queue it
  queueMessage(sessionId, identity, content)
}
```

### Plugin Return Value

#### OLD (p2p-bridge.ts) - Lines 241-297
```typescript
return {
  tool: {
    p2p_send: tool({ /* ... */ }),
    p2p_connect: tool({ /* ... */ }),
    p2p_status: tool({ /* ... */ }),
  },
  // ❌ NO HOOKS - Messages are injected via client.session.prompt()
}
```

#### NEW (p2p-bridge-fixed.ts) - Lines 241-362
```typescript
return {
  tool: {
    p2p_send: tool({ /* ... */ }),
    p2p_connect: tool({ /* ... */ }),
    p2p_status: tool({ 
      // ✅ Now shows pending message count
      async execute() {
        const sessionId = await getActiveSessionId()
        const queueSize = sessionId ? (pendingMessages.get(sessionId)?.length || 0) : 0
        
        return JSON.stringify({
          connected: daemon !== null,
          identity: P2P_IDENTITY,
          room: P2P_ROOM,
          ticket: P2P_TICKET ? "set" : "not set",
          pendingMessages: queueSize,  // ← NEW
        })
      }
    }),
  },

  // ✅ NEW: Hook to inject queued messages
  "experimental.chat.messages.transform": async (_input, output) => {
    const { messages } = output
    
    if (messages.length === 0) return

    // Find last user message
    let lastUserMessageIndex = -1
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].info.role === "user") {
        lastUserMessageIndex = i
        break
      }
    }

    if (lastUserMessageIndex === -1) return

    const lastUserMessage = messages[lastUserMessageIndex]
    const sessionID = (lastUserMessage.info as any).sessionID
    
    if (!sessionID) return

    const pending = pendingMessages.get(sessionID)
    if (!pending || pending.length === 0) return

    // Find text part in last user message
    const textPartIndex = lastUserMessage.parts.findIndex(
      (p: any) => p.type === "text" && p.text
    )

    if (textPartIndex === -1) return

    const textPart = lastUserMessage.parts[textPartIndex] as any
    const originalText = textPart.text || ""
    
    // PREPEND P2P messages to user's message
    const p2pContext = formatPendingMessages(pending)
    textPart.text = `${p2pContext}${originalText}`

    client.app.log({
      body: {
        service: "p2p-bridge",
        level: "info",
        message: `Injected ${pending.length} P2P messages into session ${sessionID}`
      }
    }).catch(() => {})

    // Clear the queue
    pendingMessages.delete(sessionID)
  },
}
```

## Key Architectural Differences

| Aspect | OLD | NEW |
|--------|-----|-----|
| **Storage** | None (immediate injection) | `Map<sessionId, P2PMessage[]>` queue |
| **Injection Method** | `client.session.prompt()` | `experimental.chat.messages.transform` hook |
| **Timing** | Immediate when P2P message arrives | Deferred until next user message |
| **Message Type** | New user message | Prepended context to existing message |
| **Agent Behavior** | Must respond to each P2P message | Sees P2P messages as context |
| **Conversation Flow** | Interrupted | Seamless |

## Pattern Learned from oh-my-opencode

This follows the **Context Collector Pattern** used in oh-my-opencode:

1. **Collect/Queue** context from various sources
2. **Register** it with a session ID
3. **Inject** it via hooks at the right moment
4. **Clear** after injection

See `oh-my-opencode-reference/src/features/context-injector/` for the full pattern.

