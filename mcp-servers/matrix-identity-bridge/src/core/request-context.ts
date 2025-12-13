/**
 * Request Context Management
 * 
 * Since we use a proxy architecture, AsyncLocalStorage doesn't work across
 * the HTTP boundary. Instead, we use a session-based approach:
 * 
 * 1. Proxy extracts X-Agent-Id header and MCP session ID
 * 2. Stores mapping: session_id -> agent_id
 * 3. Tool handlers look up agent_id using session_id from request
 * 
 * We also maintain AsyncLocalStorage for cases where context can propagate.
 */

import { AsyncLocalStorage } from 'async_hooks';

export interface RequestContext {
  agentId?: string;
  sessionId?: string;
  requestId?: string;
}

// Global AsyncLocalStorage instance (for same-process context)
const requestContextStorage = new AsyncLocalStorage<RequestContext>();

// Session-based agent ID storage (for cross-HTTP-boundary context)
const sessionAgentMap = new Map<string, { agentId: string; timestamp: number }>();

// Cleanup old sessions after 1 hour
const SESSION_TTL_MS = 60 * 60 * 1000;

/**
 * Store agent ID for a session (called by proxy when it sees a new session)
 */
export function setSessionAgentId(sessionId: string, agentId: string): void {
  sessionAgentMap.set(sessionId, { agentId, timestamp: Date.now() });
  console.log(`[RequestContext] Stored agent ${agentId} for session ${sessionId}`);
  
  // Cleanup old sessions
  const now = Date.now();
  for (const [sid, data] of sessionAgentMap.entries()) {
    if (now - data.timestamp > SESSION_TTL_MS) {
      sessionAgentMap.delete(sid);
    }
  }
}

/**
 * Get agent ID for a session
 */
export function getSessionAgentId(sessionId: string): string | undefined {
  const data = sessionAgentMap.get(sessionId);
  if (data) {
    // Update timestamp on access
    data.timestamp = Date.now();
    return data.agentId;
  }
  return undefined;
}

/**
 * Run a function within a request context
 */
export function runWithContext<T>(context: RequestContext, fn: () => T): T {
  return requestContextStorage.run(context, fn);
}

/**
 * Run an async function within a request context
 */
export async function runWithContextAsync<T>(context: RequestContext, fn: () => Promise<T>): Promise<T> {
  return requestContextStorage.run(context, fn);
}

/**
 * Get the current request context
 */
export function getRequestContext(): RequestContext | undefined {
  return requestContextStorage.getStore();
}

/**
 * Get the agent ID from the current request context (AsyncLocalStorage)
 */
export function getAgentIdFromContext(): string | undefined {
  const ctx = requestContextStorage.getStore();
  return ctx?.agentId;
}

/**
 * Extract agent ID from HTTP headers
 */
export function extractAgentIdFromHeaders(headers: Record<string, string | string[] | undefined>): string | undefined {
  // Check both casing variants
  const agentId = headers['x-agent-id'] || headers['X-Agent-Id'];
  if (Array.isArray(agentId)) {
    return agentId[0];
  }
  return agentId;
}

/**
 * Extract session ID from HTTP headers
 */
export function extractSessionIdFromHeaders(headers: Record<string, string | string[] | undefined>): string | undefined {
  const sessionId = headers['mcp-session-id'] || headers['Mcp-Session-Id'];
  if (Array.isArray(sessionId)) {
    return sessionId[0];
  }
  return sessionId;
}

/**
 * Get all active sessions (for debugging)
 */
export function getActiveSessions(): Map<string, { agentId: string; timestamp: number }> {
  return new Map(sessionAgentMap);
}
