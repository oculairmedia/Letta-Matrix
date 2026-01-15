# P2P Bridge Migration Guide

## The Problem

Your current `p2p-bridge.ts` uses `client.session.prompt()` which:
- ❌ Creates a **new user message** for each P2P message
- ❌ Forces the agent to **respond** to every P2P message
- ❌ **Interrupts** the conversation flow
- ❌ Clutters the conversation history

## The Solution

The fixed version uses `experimental.chat.messages.transform` hook which:
- ✅ **Queues** P2P messages instead of immediately prompting
- ✅ **Prepends** queued messages to the next user message
- ✅ Messages become **context**, not prompts
- ✅ No forced agent responses
- ✅ Clean conversation flow

## Key Changes

### 1. Message Queue (NEW)

```typescript
// Store messages per session
const pendingMessages = new Map<string, P2PMessage[]>()

function queueMessage(sessionId: string, identity: string, content: string) {
  if (!pendingMessages.has(sessionId)) {
    pendingMessages.set(sessionId, [])
  }
  
  pendingMessages.get(sessionId)!.push({
    identity,
    content,
    timestamp: Date.now()
  })
}
```

### 2. Handle Incoming Messages (CHANGED)

**Before:**
```typescript
async function handleIncomingMessage(identity: string, content: string) {
  // Immediately prompt the session
  await client.session.prompt({
    path: { id: sessionId },
    body: {
      parts: [{ type: "text", text: formattedMessage }],
    },
  })
}
```

**After:**
```typescript
async function handleIncomingMessage(identity: string, content: string) {
  const sessionId = await getActiveSessionId()
  if (!sessionId) return
  
  // Just queue it
  queueMessage(sessionId, identity, content)
}
```

### 3. Context Injection Hook (NEW)

```typescript
return {
  tool: { /* ... tools ... */ },
  
  // NEW: Inject queued messages into conversation
  "experimental.chat.messages.transform": async (_input, output) => {
    const { messages } = output
    
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
    
    const pending = pendingMessages.get(sessionID)
    if (!pending || pending.length === 0) return
    
    // Find text part
    const textPartIndex = lastUserMessage.parts.findIndex(
      (p: any) => p.type === "text" && p.text
    )
    
    if (textPartIndex === -1) return
    
    const textPart = lastUserMessage.parts[textPartIndex] as any
    const originalText = textPart.text || ""
    
    // PREPEND P2P messages
    const p2pContext = formatPendingMessages(pending)
    textPart.text = `${p2pContext}${originalText}`
    
    // Clear queue
    pendingMessages.delete(sessionID)
  }
}
```

## How It Works

### Flow Diagram

**Old Flow:**
```
P2P Message Arrives
  ↓
client.session.prompt() creates new user message
  ↓
Agent MUST respond
  ↓
Conversation interrupted
```

**New Flow:**
```
P2P Message Arrives
  ↓
Queue message for session
  ↓
User types next message
  ↓
Hook prepends P2P messages to user's message
  ↓
Agent sees P2P context + user message together
  ↓
Agent responds naturally
```

## Testing

1. **Start the daemon:**
   ```bash
   P2P_TICKET="your-ticket" opencode
   ```

2. **Send a test message from another agent:**
   ```bash
   real-a2a send --identity other-agent "Hello from P2P!"
   ```

3. **In OpenCode, type any message:**
   ```
   What's the status?
   ```

4. **The agent will see:**
   ```
   ---
   
   ## P2P Network Messages (1)
   
   **From other-agent:**
   Hello from P2P!
   
   ---
   
   What's the status?
   ```

## Benefits

1. **No Conversation Pollution:** P2P messages don't create separate conversation turns
2. **Natural Context:** Messages appear as context, not as questions requiring answers
3. **Batching:** Multiple P2P messages are batched together
4. **User Control:** Messages only appear when user interacts
5. **Clean History:** Conversation history stays focused on user-agent interaction

## Migration Steps

1. **Backup current plugin:**
   ```bash
   cp .opencode/plugin/p2p-bridge.ts .opencode/plugin/p2p-bridge.ts.backup
   ```

2. **Replace with fixed version:**
   ```bash
   cp .opencode/plugin/p2p-bridge-fixed.ts .opencode/plugin/p2p-bridge.ts
   ```

3. **Restart OpenCode**

4. **Test with `p2p_status` tool** to see pending messages

## Additional Improvements

Consider adding:

- **Priority levels:** Urgent messages could trigger notifications
- **Message expiry:** Clear old messages after N minutes
- **Filtering:** Ignore certain message types
- **Formatting:** Better markdown formatting for different message types

