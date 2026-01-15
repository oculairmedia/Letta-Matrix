# P2P Context Injection - Complete Guide

## 📋 Summary

Your p2p-bridge plugin was **injecting messages as prompts** instead of **context**. This caused the agent to respond to every P2P message, cluttering the conversation.

By studying oh-my-opencode's context injection system, we've created a fixed version that **queues messages** and **injects them as context** into the next user message.

## 📚 Documentation Files

1. **[CONTEXT_INJECTION_ANALYSIS.md](./CONTEXT_INJECTION_ANALYSIS.md)**
   - How oh-my-opencode injects context
   - The 3-layer context injection system
   - Context Collector pattern explained

2. **[CODE_COMPARISON.md](./CODE_COMPARISON.md)**
   - Side-by-side comparison of old vs new code
   - Exact line-by-line differences
   - Architectural changes explained

3. **[P2P_BRIDGE_MIGRATION_GUIDE.md](./P2P_BRIDGE_MIGRATION_GUIDE.md)**
   - Step-by-step migration instructions
   - Flow diagrams
   - Testing procedures

4. **[ADVANCED_CONTEXT_INJECTION.md](./ADVANCED_CONTEXT_INJECTION.md)**
   - Optional: Full Context Collector pattern
   - Priority system
   - Advanced features

## 🚀 Quick Start

### Option 1: Use the Fixed Version (Recommended)

```bash
# Backup current version
cp .opencode/plugin/p2p-bridge.ts .opencode/plugin/p2p-bridge.ts.backup

# Use the fixed version
cp .opencode/plugin/p2p-bridge-fixed.ts .opencode/plugin/p2p-bridge.ts

# Restart OpenCode
```

### Option 2: Manual Migration

Apply these changes to your existing `p2p-bridge.ts`:

1. **Add message queue:**
   ```typescript
   const pendingMessages = new Map<string, P2PMessage[]>()
   ```

2. **Replace `handleIncomingMessage`:**
   ```typescript
   // OLD: await client.session.prompt({ ... })
   // NEW: queueMessage(sessionId, identity, content)
   ```

3. **Add hook to plugin return:**
   ```typescript
   return {
     tool: { /* ... */ },
     "experimental.chat.messages.transform": async (_input, output) => {
       // Inject queued messages into last user message
     }
   }
   ```

See [CODE_COMPARISON.md](./CODE_COMPARISON.md) for full details.

## 🔍 What Changed

### Before (❌ Problem)
```
P2P Message → client.session.prompt() → New User Message → Agent Must Respond
```

### After (✅ Solution)
```
P2P Message → Queue → User Types → Hook Prepends Context → Agent Sees Both
```

## 🎯 Key Insights from oh-my-opencode

### 1. Context Collector Pattern
- **Register** context with a session ID
- **Queue** it until the right moment
- **Inject** via hooks (not prompts)
- **Clear** after injection

### 2. Hook Types for Context Injection

| Hook | When | Use Case |
|------|------|----------|
| `chat.message` | Before message sent | Modify outgoing message |
| `experimental.chat.messages.transform` | Before LLM call | Modify message history |
| `tool.execute.after` | After tool runs | Append to tool output |

### 3. Injection Strategies

1. **Prepend to user message** (Best for P2P)
   - Messages become context
   - No forced responses
   - Clean conversation

2. **Append to tool output** (Good for file-based context)
   - README.md injection
   - AGENTS.md injection
   - Directory-specific context

3. **Supervisor tags** (For system messages)
   - Gray box in UI
   - System-level context

## 📊 Results

### Conversation Before Fix
```
User: What's the status?
Assistant: The status is...

[P2P Message from agent-2]
Can you help with task X?
Assistant: Sure, I can help with task X...

[P2P Message from agent-3]
Update: Task Y is complete
Assistant: Thanks for the update...

User: I meant the status of OUR task!
```

### Conversation After Fix
```
User: What's the status?

[Agent sees:]
---
## P2P Network Messages (2)

**From agent-2:**
Can you help with task X?

**From agent-3:**
Update: Task Y is complete

---

What's the status?


Assistant: Looking at our task status... [responds naturally, aware of P2P context]
```

## 🧪 Testing

1. **Check status:**
   ```typescript
   // Use p2p_status tool
   {
     "connected": true,
     "identity": "opencode-matrix-synapse-deployment",
     "room": "agent-swarm-global",
     "ticket": "set",
     "pendingMessages": 2  // ← Shows queued messages
   }
   ```

2. **Send test message:**
   ```bash
   real-a2a send --identity test-agent "Test message"
   ```

3. **Type any message in OpenCode:**
   - P2P messages will be prepended automatically
   - Agent sees them as context, not as questions

## 🎓 Lessons Learned

### From oh-my-opencode Architecture

1. **Hooks are powerful** - Use them to modify behavior without changing core logic
2. **Context vs Prompts** - Context doesn't require responses, prompts do
3. **Deferred injection** - Queue now, inject later = better UX
4. **Session-scoped state** - Track state per session, not globally

### Best Practices

1. ✅ **DO** use hooks for context injection
2. ✅ **DO** queue messages for batch processing
3. ✅ **DO** prepend context to user messages
4. ❌ **DON'T** use `client.session.prompt()` for context
5. ❌ **DON'T** create new messages for background events
6. ❌ **DON'T** force agent responses to system events

## 🔗 References

- **oh-my-opencode source:** `oh-my-opencode-reference/`
- **Context injector:** `src/features/context-injector/`
- **Directory README injector:** `src/hooks/directory-readme-injector/`
- **Hooks documentation:** `src/hooks/AGENTS.md`

## 📝 Files Created

- ✅ `CONTEXT_INJECTION_ANALYSIS.md` - Theory and patterns
- ✅ `CODE_COMPARISON.md` - Side-by-side code diff
- ✅ `P2P_BRIDGE_MIGRATION_GUIDE.md` - Migration steps
- ✅ `ADVANCED_CONTEXT_INJECTION.md` - Advanced patterns
- ✅ `.opencode/plugin/p2p-bridge-fixed.ts` - Fixed implementation
- ✅ `README_P2P_CONTEXT_INJECTION.md` - This file

## 🚦 Next Steps

1. **Review** the fixed implementation in `p2p-bridge-fixed.ts`
2. **Test** in a safe environment
3. **Migrate** when ready
4. **Monitor** logs for injection events
5. **Iterate** based on usage patterns

## 💡 Future Enhancements

Consider adding:
- **Message expiry** - Clear old messages after N minutes
- **Priority levels** - Urgent messages could trigger notifications
- **Filtering** - Ignore certain message types or senders
- **Persistence** - Save queue to disk for plugin reloads
- **UI indicators** - Show pending message count in status bar

---

**Questions?** Review the documentation files or examine the oh-my-opencode reference implementation.