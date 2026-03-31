# Architecture Diagrams

## Old Architecture (❌ Problem)

```
┌─────────────────────────────────────────────────────────────┐
│                     P2P Network                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Message arrives
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              handleIncomingMessage()                         │
│  - Receives P2P message                                      │
│  - Gets active session ID                                    │
│  - Immediately calls client.session.prompt()                 │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Creates new user message
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  OpenCode Session                            │
│                                                              │
│  User: What's the status?                                   │
│  Agent: The status is...                                    │
│                                                              │
│  User: [P2P Message from agent-2] Can you help?  ← INJECTED │
│  Agent: Sure, I can help...                      ← FORCED   │
│                                                              │
│  User: [P2P Message from agent-3] Update...      ← INJECTED │
│  Agent: Thanks for the update...                 ← FORCED   │
│                                                              │
│  User: I meant OUR task!                                    │
└─────────────────────────────────────────────────────────────┘
```

**Problems:**
- ❌ Each P2P message creates a new user message
- ❌ Agent must respond to each P2P message
- ❌ Conversation becomes cluttered
- ❌ User loses control of conversation flow

---

## New Architecture (✅ Solution)

```
┌─────────────────────────────────────────────────────────────┐
│                     P2P Network                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Message arrives
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              handleIncomingMessage()                         │
│  - Receives P2P message                                      │
│  - Gets active session ID                                    │
│  - Queues message: queueMessage(sessionId, identity, content)│
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Stores in queue
                            ▼
┌─────────────────────────────────────────────────────────────┐
│         Message Queue (Map<sessionId, P2PMessage[]>)         │
│                                                              │
│  session-123: [                                              │
│    { identity: "agent-2", content: "Can you help?", ... },  │
│    { identity: "agent-3", content: "Update...", ... }       │
│  ]                                                           │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Waits for user action
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    User Types Message                        │
│  "What's the status?"                                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Triggers hook
                            ▼
┌─────────────────────────────────────────────────────────────┐
│   experimental.chat.messages.transform Hook                  │
│                                                              │
│  1. Find last user message                                  │
│  2. Get pending P2P messages from queue                     │
│  3. Format messages                                         │
│  4. Prepend to user's message text                          │
│  5. Clear queue                                             │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ Modified message
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  OpenCode Session                            │
│                                                              │
│  User: What's the status?                                   │
│  Agent: The status is...                                    │
│                                                              │
│  User: ┌──────────────────────────────────────┐            │
│        │ ## P2P Network Messages (2)          │            │
│        │                                       │            │
│        │ **From agent-2:**                    │            │
│        │ Can you help with task X?            │            │
│        │                                       │            │
│        │ **From agent-3:**                    │            │
│        │ Update: Task Y is complete           │            │
│        │                                       │            │
│        │ ---                                   │            │
│        │                                       │            │
│        │ What's the status?                   │            │
│        └──────────────────────────────────────┘            │
│                                                              │
│  Agent: Looking at our task status...         ← NATURAL    │
│         [Aware of P2P context, responds to user's question] │
└─────────────────────────────────────────────────────────────┘
```

**Benefits:**
- ✅ P2P messages are queued, not immediately injected
- ✅ Messages are prepended as context to user's message
- ✅ Agent sees P2P messages but responds to user's question
- ✅ Clean conversation flow
- ✅ User maintains control

---

## Hook Execution Flow

```
User types message
      │
      ▼
┌─────────────────────────────────────────┐
│  OpenCode prepares message for LLM      │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  experimental.chat.messages.transform   │
│  hook is called                         │
│                                         │
│  Input: { messages: [...] }             │
│  Output: { messages: [...] } (modified) │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  Our hook:                              │
│  1. messages = output.messages          │
│  2. lastUserMsg = messages[last]        │
│  3. sessionID = lastUserMsg.info.sessionID │
│  4. pending = queue.get(sessionID)      │
│  5. if (pending.length > 0):            │
│      textPart.text = p2p + original     │
│      queue.delete(sessionID)            │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  Modified messages sent to LLM          │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  Agent sees P2P context + user message  │
│  and responds naturally                 │
└─────────────────────────────────────────┘
```

---

## Data Flow Comparison

### Old Flow (Immediate Injection)
```
P2P Message → client.session.prompt() → New User Message → Agent Response
     ↓              ↓                         ↓                  ↓
  Arrives      Immediate                 Forced             Required
```

### New Flow (Deferred Injection)
```
P2P Message → Queue → User Action → Hook → Prepend → Agent Response
     ↓          ↓         ↓          ↓        ↓           ↓
  Arrives    Store      Wait      Inject   Context    Natural
```

---

## Component Interaction

```
┌──────────────────────────────────────────────────────────────┐
│                      P2P Bridge Plugin                        │
│                                                               │
│  ┌────────────────┐    ┌──────────────────┐                 │
│  │  Daemon Process│───▶│ Message Parser   │                 │
│  │  (real-a2a)    │    │ parseP2PMessage()│                 │
│  └────────────────┘    └──────────────────┘                 │
│                               │                               │
│                               ▼                               │
│                    ┌──────────────────────┐                  │
│                    │ handleIncomingMessage│                  │
│                    └──────────────────────┘                  │
│                               │                               │
│                               ▼                               │
│                    ┌──────────────────────┐                  │
│                    │   queueMessage()     │                  │
│                    └──────────────────────┘                  │
│                               │                               │
│                               ▼                               │
│  ┌─────────────────────────────────────────────────┐        │
│  │  pendingMessages: Map<sessionId, P2PMessage[]>  │        │
│  └─────────────────────────────────────────────────┘        │
│                               │                               │
│                               │ (waits for user action)       │
│                               ▼                               │
│  ┌─────────────────────────────────────────────────┐        │
│  │  experimental.chat.messages.transform Hook      │        │
│  │  - Finds last user message                      │        │
│  │  - Gets pending messages                        │        │
│  │  - Prepends to message text                     │        │
│  │  - Clears queue                                 │        │
│  └─────────────────────────────────────────────────┘        │
│                                                               │
└──────────────────────────────────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   OpenCode Session   │
                    │   (Modified Message) │
                    └──────────────────────┘
```

---

## Session State Management

```
Session A                    Session B
    │                            │
    ▼                            ▼
┌─────────┐                ┌─────────┐
│ Queue A │                │ Queue B │
│ [msg1]  │                │ [msg3]  │
│ [msg2]  │                │ [msg4]  │
└─────────┘                └─────────┘
    │                            │
    │ User types in A            │ User types in B
    ▼                            ▼
┌─────────┐                ┌─────────┐
│ Inject  │                │ Inject  │
│ msg1+2  │                │ msg3+4  │
└─────────┘                └─────────┘
    │                            │
    ▼                            ▼
┌─────────┐                ┌─────────┐
│ Clear A │                │ Clear B │
└─────────┘                └─────────┘
```

Each session has its own queue, preventing cross-contamination.

