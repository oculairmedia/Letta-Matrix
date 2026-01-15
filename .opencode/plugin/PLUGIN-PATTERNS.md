# OpenCode Plugin Development Patterns

## Critical Rule: Non-Blocking Plugin Initialization

**OpenCode plugins MUST return immediately.** Blocking the plugin initialization with `await` calls will hang OpenCode startup.

### ❌ BAD - Blocks OpenCode Startup

```typescript
export const MyPlugin: Plugin = async ({ client }) => {
  await client.app.log(...)           // BLOCKS
  await someAsyncSetup()              // BLOCKS
  await connectToExternalService()    // BLOCKS

  return { ... }  // Never reached if above fails
}
```

**Problems:**
- OpenCode hangs during startup waiting for async operations
- If any operation fails, entire plugin load fails
- No way to recover without restart
- User sees frozen application

### ✅ GOOD - Returns Immediately, Async Work in Background

```typescript
export const MyPlugin: Plugin = async ({ client }) => {

  // Fire-and-forget logging (no await)
  client.app.log(...).catch(() => {})

  // All async work in background with setTimeout
  setTimeout(async () => {
    try {
      await someAsyncSetup()
      await connectToExternalService()
      
      client.app.log({
        body: {
          service: "my-plugin",
          level: "info",
          message: "Plugin initialized successfully"
        }
      }).catch(() => {})
      
    } catch (error: any) {
      client.tui.showToast({
        body: { 
          message: `Plugin error: ${error.message}`, 
          variant: "error" 
        }
      }).catch(() => {})
    }
  }, 2000)

  return { ... }  // Returns immediately
}
```

**Benefits:**
- OpenCode starts immediately
- Async work happens in background
- Errors don't crash plugin load
- User can continue working while setup completes

---

## Historical Violations (Learn from These Mistakes)

### 1. Matrix Context Injector (matrix-context-injector.ts)

**Original Problem:**
```typescript
export const MatrixContextInjector: Plugin = async ({ client }) => {
  await acquireLock(lockPath)              // ❌ BLOCKS
  const config = await loadMatrixConfig()  // ❌ BLOCKS
  const creds = await resolveCredentials() // ❌ BLOCKS
  await syncClient.start()                 // ❌ BLOCKS
  await syncClient.joinRooms()             // ❌ BLOCKS
  
  return {}
}
```

**Fixed with setTimeout pattern:**
```typescript
export const MatrixContextInjector: Plugin = async ({ client }) => {
  // ... setup shared state ...
  
  setTimeout(async () => {
    try {
      await acquireLock(lockPath)              // ✅ Non-blocking
      const config = await loadMatrixConfig()  // ✅ Non-blocking
      const creds = await resolveCredentials() // ✅ Non-blocking
      await syncClient.start()                 // ✅ Non-blocking
      await syncClient.joinRooms()             // ✅ Non-blocking
    } catch (error) {
      // Handle errors without crashing plugin
    }
  }, 2000)
  
  return {}  // Returns immediately
}
```

### 2. P2P Bridge (p2p-bridge.ts)

**Original Problem:**
```typescript
export const P2PBridge: Plugin = async ({ client }) => {
  await client.app.log({...})  // ❌ BLOCKS
  // ... other blocking calls
  
  return {}
}
```

**Fix:**
```typescript
export const P2PBridge: Plugin = async ({ client }) => {
  // Fire-and-forget
  client.app.log({...}).catch(() => {})  // ✅ Non-blocking
  
  return {}  // Returns immediately
}
```

### 3. Matrix Bridge Registration (matrix-bridge-registration.js)

**Original Problem:**
```typescript
export const MatrixBridgeRegistration: Plugin = async ({ client }) => {
  const response = await fetch(...)  // ❌ BLOCKS
  await registerWithHomeserver(...)  // ❌ BLOCKS
  
  return {}
}
```

**Fix:**
```typescript
export const MatrixBridgeRegistration: Plugin = async ({ client }) => {
  setTimeout(async () => {
    const response = await fetch(...)  // ✅ Non-blocking
    await registerWithHomeserver(...)  // ✅ Non-blocking
  }, 2000)
  
  return {}  // Returns immediately
}
```

---

## Pattern: Fire-and-Forget Logging

OpenCode client methods like `client.app.log()` and `client.tui.showToast()` return promises. **Never await them in plugin init.**

```typescript
// ❌ BAD
await client.app.log({ body: { message: "..." } })

// ✅ GOOD
client.app.log({ body: { message: "..." } }).catch(() => {})
```

---

## Pattern: Background Initialization with Error Handling

```typescript
export const MyPlugin: Plugin = async ({ client, directory }) => {
  const log = (level: string, message: string, extra?: any) => {
    client.app.log({
      body: { service: "my-plugin", level, message, extra }
    }).catch(() => {})
  }

  const showError = (message: string) => {
    client.tui.showToast({
      body: { message, variant: "error" }
    }).catch(() => {})
  }

  // Cleanup on exit
  const shutdown = () => {
    // cleanup logic
  }
  
  process.on("exit", shutdown)
  process.on("SIGINT", () => { shutdown(); process.exit(0) })
  process.on("SIGTERM", () => { shutdown(); process.exit(0) })

  // All async work in background
  setTimeout(async () => {
    try {
      // Step 1
      const config = await loadConfig(directory)
      log("info", "Config loaded", { path: config.path })
      
      // Step 2
      await connectToService(config)
      log("info", "Service connected")
      
      // Step 3
      await startListening()
      log("info", "Plugin ready")
      
    } catch (error: any) {
      log("error", "Plugin failed to initialize", { 
        error: error.message 
      })
      showError(`Plugin error: ${error.message}`)
      shutdown()
    }
  }, 2000)

  return {}  // Returns immediately
}
```

---

## Why 2000ms setTimeout?

The 2-second delay ensures:
1. OpenCode core services are fully initialized
2. Client API is ready to receive calls
3. TUI is rendered and can show toasts
4. Other plugins have had time to load

**Do not reduce below 2000ms** unless you have a specific reason and have tested thoroughly.

---

## Checklist for Plugin Development

Before submitting a plugin:

- [ ] Plugin returns `{}` immediately (no `await` before `return`)
- [ ] All async work wrapped in `setTimeout(..., 2000)`
- [ ] All `client.app.log()` calls use `.catch(() => {})`
- [ ] All `client.tui.showToast()` calls use `.catch(() => {})`
- [ ] Error handling doesn't crash the plugin
- [ ] Cleanup handlers registered for process exit signals
- [ ] Tested that OpenCode starts even if plugin logic fails

---

## Key Takeaway

**The Golden Rule:** If you see `await` before `return {}` in a plugin, it's wrong. Move that `await` inside a `setTimeout`.
