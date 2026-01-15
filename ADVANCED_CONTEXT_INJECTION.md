# Advanced Context Injection Patterns

## Option 1: Simple Queue (Current Implementation)

**File:** `.opencode/plugin/p2p-bridge-fixed.ts`

✅ **Pros:**
- Simple to understand
- Self-contained
- No external dependencies

❌ **Cons:**
- Messages lost on plugin reload
- No priority system
- No deduplication

## Option 2: Full Context Collector Pattern

If you want to adopt the full oh-my-opencode pattern:

### Step 1: Create Context Types

```typescript
// .opencode/plugin/p2p-context/types.ts
export type ContextPriority = "critical" | "high" | "normal" | "low"

export interface P2PContextEntry {
  id: string
  identity: string
  content: string
  priority: ContextPriority
  timestamp: number
  nodeId?: string
}

export interface PendingP2PContext {
  merged: string
  entries: P2PContextEntry[]
  hasContent: boolean
}
```

### Step 2: Create Context Collector

```typescript
// .opencode/plugin/p2p-context/collector.ts
import type { P2PContextEntry, PendingP2PContext, ContextPriority } from "./types"

const PRIORITY_ORDER: Record<ContextPriority, number> = {
  critical: 0,
  high: 1,
  normal: 2,
  low: 3,
}

export class P2PContextCollector {
  private sessions: Map<string, Map<string, P2PContextEntry>> = new Map()

  register(
    sessionID: string,
    identity: string,
    content: string,
    priority: ContextPriority = "normal"
  ): void {
    if (!this.sessions.has(sessionID)) {
      this.sessions.set(sessionID, new Map())
    }

    const sessionMap = this.sessions.get(sessionID)!
    const key = `${identity}:${Date.now()}`

    const entry: P2PContextEntry = {
      id: key,
      identity,
      content,
      priority,
      timestamp: Date.now(),
    }

    sessionMap.set(key, entry)
  }

  getPending(sessionID: string): PendingP2PContext {
    const sessionMap = this.sessions.get(sessionID)

    if (!sessionMap || sessionMap.size === 0) {
      return {
        merged: "",
        entries: [],
        hasContent: false,
      }
    }

    const entries = this.sortEntries([...sessionMap.values()])
    const merged = this.formatEntries(entries)

    return {
      merged,
      entries,
      hasContent: entries.length > 0,
    }
  }

  consume(sessionID: string): PendingP2PContext {
    const pending = this.getPending(sessionID)
    this.clear(sessionID)
    return pending
  }

  clear(sessionID: string): void {
    this.sessions.delete(sessionID)
  }

  hasPending(sessionID: string): boolean {
    const sessionMap = this.sessions.get(sessionID)
    return sessionMap !== undefined && sessionMap.size > 0
  }

  private sortEntries(entries: P2PContextEntry[]): P2PContextEntry[] {
    return entries.sort((a, b) => {
      const priorityDiff = PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority]
      if (priorityDiff !== 0) return priorityDiff
      return a.timestamp - b.timestamp
    })
  }

  private formatEntries(entries: P2PContextEntry[]): string {
    if (entries.length === 0) return ""
    
    const formatted = entries.map(entry => {
      const priorityBadge = entry.priority === "critical" ? "🔴 " : 
                           entry.priority === "high" ? "🟡 " : ""
      return `${priorityBadge}**From ${entry.identity}:**\n${entry.content}`
    }).join("\n\n")
    
    return `\n\n---\n\n## P2P Network Messages (${entries.length})\n\n${formatted}\n\n---\n\n`
  }
}

export const p2pContextCollector = new P2PContextCollector()
```

### Step 3: Use in Plugin

```typescript
// .opencode/plugin/p2p-bridge-advanced.ts
import { p2pContextCollector } from "./p2p-context/collector"

export const P2PBridgeAdvanced: Plugin = async ({ client, ... }) => {
  // ... daemon setup ...

  async function handleIncomingMessage(identity: string, content: string) {
    if (identity === P2P_IDENTITY) return
    
    const sessionId = await getActiveSessionId()
    if (!sessionId) return

    // Determine priority based on content
    const priority = content.includes("URGENT") ? "critical" :
                    content.includes("IMPORTANT") ? "high" : "normal"

    // Register with collector
    p2pContextCollector.register(sessionId, identity, content, priority)
    
    client.app.log({
      body: {
        service: "p2p-bridge",
        level: "info",
        message: `Registered ${priority} message from ${identity}`,
      }
    }).catch(() => {})
  }

  return {
    tool: { /* ... */ },

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
      
      if (!sessionID || !p2pContextCollector.hasPending(sessionID)) return

      const textPartIndex = lastUserMessage.parts.findIndex(
        (p: any) => p.type === "text" && p.text
      )

      if (textPartIndex === -1) return

      const textPart = lastUserMessage.parts[textPartIndex] as any
      const originalText = textPart.text || ""
      
      // Consume pending context
      const pending = p2pContextCollector.consume(sessionID)
      textPart.text = `${pending.merged}${originalText}`

      client.app.log({
        body: {
          service: "p2p-bridge",
          level: "info",
          message: `Injected ${pending.entries.length} P2P messages`,
        }
      }).catch(() => {})
    },
  }
}
```

## Comparison

| Feature | Simple Queue | Context Collector |
|---------|-------------|-------------------|
| **Priority** | ❌ No | ✅ Yes (critical/high/normal/low) |
| **Deduplication** | ❌ No | ✅ By ID |
| **Sorting** | ❌ FIFO only | ✅ Priority + timestamp |
| **Formatting** | Basic | Rich (badges, sections) |
| **Reusability** | ❌ Plugin-specific | ✅ Can be shared |
| **Complexity** | Low | Medium |

## Recommendation

- **Start with Simple Queue** (p2p-bridge-fixed.ts) - Good enough for most cases
- **Upgrade to Context Collector** if you need:
  - Priority handling
  - Multiple context sources
  - Shared context system across plugins
  - Advanced formatting

## Next Steps

1. Test the simple queue version first
2. If it works well, you're done!
3. If you need priorities or more features, implement the Context Collector pattern

