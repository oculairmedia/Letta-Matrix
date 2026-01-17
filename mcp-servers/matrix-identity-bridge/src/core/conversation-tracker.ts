/**
 * Conversation Tracker for Cross-Run Tracking
 * 
 * Tracks conversations across multiple Letta runs to handle tool attachments.
 * When an agent calls find_tools, tools are attached but not available until
 * a new run starts. This tracker links the original user message to responses
 * from subsequent runs.
 */

export interface RunInfo {
  run_id: string;
  triggered_by: 'user_message' | 'system_trigger' | 'webhook';
  timestamp: string;
  status: 'active' | 'completed' | 'timeout';
  parent_run_id?: string;
}

export interface ConversationState {
  matrix_room_id: string;
  matrix_event_id: string;
  agent_id: string;
  runs: RunInfo[];
  status: 'pending' | 'active' | 'completed' | 'timeout';
  created_at: string;
  updated_at: string;
  original_query?: string;
  tools_attached?: string[];
  response?: string;
}

export interface WebhookPayload {
  event: 'run_triggered';
  agent_id: string;
  new_run_id?: string;
  trigger_type: 'tool_attachment';
  tools_attached: string[];
  query?: string;
  timestamp: string;
}

export class ConversationTracker {
  private conversations: Map<string, ConversationState> = new Map();
  private agentConversations: Map<string, string> = new Map(); // agent_id -> matrix_event_id
  private runToConversation: Map<string, string> = new Map(); // run_id -> matrix_event_id
  
  // Configuration
  private readonly maxAgeMs: number;
  private readonly cleanupIntervalMs: number;
  private cleanupTimer?: NodeJS.Timeout;

  constructor(options?: {
    maxAgeSeconds?: number;
    cleanupIntervalSeconds?: number;
  }) {
    this.maxAgeMs = (options?.maxAgeSeconds ?? 300) * 1000; // Default 5 minutes
    this.cleanupIntervalMs = (options?.cleanupIntervalSeconds ?? 60) * 1000; // Default 1 minute
    
    // Start cleanup timer
    this.startCleanupTimer();
  }

  /**
   * Start tracking a new conversation from a Matrix message
   */
  startConversation(
    matrixEventId: string,
    matrixRoomId: string,
    agentId: string,
    originalQuery?: string
  ): ConversationState {
    const now = new Date().toISOString();
    
    const conv: ConversationState = {
      matrix_room_id: matrixRoomId,
      matrix_event_id: matrixEventId,
      agent_id: agentId,
      runs: [],
      status: 'pending',
      created_at: now,
      updated_at: now,
      original_query: originalQuery
    };

    this.conversations.set(matrixEventId, conv);
    this.agentConversations.set(agentId, matrixEventId);
    
    console.log(`[ConversationTracker] Started tracking conversation ${matrixEventId} for agent ${agentId}`);
    return conv;
  }

  /**
   * Add a run to an existing conversation
   */
  addRun(
    matrixEventId: string,
    runId: string,
    triggeredBy: RunInfo['triggered_by'],
    parentRunId?: string
  ): boolean {
    const conv = this.conversations.get(matrixEventId);
    if (!conv) {
      console.warn(`[ConversationTracker] No conversation found for ${matrixEventId}`);
      return false;
    }

    const now = new Date().toISOString();
    
    // Mark previous active runs as completed
    for (const run of conv.runs) {
      if (run.status === 'active') {
        run.status = 'completed';
      }
    }

    const runInfo: RunInfo = {
      run_id: runId,
      triggered_by: triggeredBy,
      timestamp: now,
      status: 'active',
      parent_run_id: parentRunId || this.getActiveRunId(conv)
    };

    conv.runs.push(runInfo);
    conv.status = 'active';
    conv.updated_at = now;
    
    // Update run mapping
    this.runToConversation.set(runId, matrixEventId);
    
    console.log(`[ConversationTracker] Added run ${runId} to conversation ${matrixEventId} (triggered by: ${triggeredBy})`);
    return true;
  }

  /**
   * Handle webhook from tool selector
   */
  handleWebhook(payload: WebhookPayload): {
    tracked: boolean;
    conversation?: ConversationState;
    reason?: string;
  } {
    const { agent_id, new_run_id, tools_attached, query, timestamp } = payload;
    
    // Find active conversation for this agent
    const conv = this.getConversationByAgent(agent_id);
    
    if (!conv) {
      console.warn(`[ConversationTracker] Webhook received for agent ${agent_id} but no active conversation found`);
      return { 
        tracked: false, 
        reason: 'no_active_conversation' 
      };
    }

    // Update conversation with tool info
    conv.tools_attached = tools_attached;
    conv.updated_at = timestamp || new Date().toISOString();
    
    // If we have a new run_id from the webhook, track it
    if (new_run_id) {
      this.addRun(conv.matrix_event_id, new_run_id, 'webhook');
    }
    
    console.log(`[ConversationTracker] Webhook processed for conversation ${conv.matrix_event_id}: ${tools_attached.length} tools attached`);
    
    return {
      tracked: true,
      conversation: conv
    };
  }

  /**
   * Get conversation by Matrix event ID
   */
  getConversation(matrixEventId: string): ConversationState | undefined {
    return this.conversations.get(matrixEventId);
  }

  /**
   * Get active conversation for an agent
   */
  getConversationByAgent(agentId: string): ConversationState | undefined {
    const eventId = this.agentConversations.get(agentId);
    if (!eventId) return undefined;
    
    const conv = this.conversations.get(eventId);
    if (conv && (conv.status === 'active' || conv.status === 'pending')) {
      return conv;
    }
    
    return undefined;
  }

  /**
   * Get conversation by run ID
   */
  getConversationByRunId(runId: string): ConversationState | undefined {
    const eventId = this.runToConversation.get(runId);
    if (!eventId) return undefined;
    return this.conversations.get(eventId);
  }

  /**
   * Mark a conversation as completed
   */
  completeConversation(matrixEventId: string): void {
    const conv = this.conversations.get(matrixEventId);
    if (!conv) return;

    conv.status = 'completed';
    conv.updated_at = new Date().toISOString();
    
    // Mark all runs as completed
    for (const run of conv.runs) {
      if (run.status === 'active') {
        run.status = 'completed';
      }
    }

    // Clean up agent mapping
    if (this.agentConversations.get(conv.agent_id) === matrixEventId) {
      this.agentConversations.delete(conv.agent_id);
    }
    
    console.log(`[ConversationTracker] Completed conversation ${matrixEventId}`);
  }

  completeWithResponse(matrixEventId: string, response: string): void {
    const conv = this.conversations.get(matrixEventId);
    if (!conv) return;

    conv.status = 'completed';
    conv.response = response;
    conv.updated_at = new Date().toISOString();
    
    for (const run of conv.runs) {
      if (run.status === 'active') {
        run.status = 'completed';
      }
    }

    if (this.agentConversations.get(conv.agent_id) === matrixEventId) {
      this.agentConversations.delete(conv.agent_id);
    }
    
    console.log(`[ConversationTracker] Completed conversation ${matrixEventId} with response (${response.length} chars)`);
  }

  /**
   * Mark a specific run as completed
   */
  completeRun(runId: string): void {
    const eventId = this.runToConversation.get(runId);
    if (!eventId) return;
    
    const conv = this.conversations.get(eventId);
    if (!conv) return;

    for (const run of conv.runs) {
      if (run.run_id === runId) {
        run.status = 'completed';
        break;
      }
    }
    
    conv.updated_at = new Date().toISOString();
  }

  /**
   * Get the original run ID for a conversation
   */
  getOriginalRunId(conv: ConversationState): string | undefined {
    return conv.runs[0]?.run_id;
  }

  /**
   * Get the active run ID for a conversation
   */
  getActiveRunId(conv: ConversationState): string | undefined {
    const active = conv.runs.filter(r => r.status === 'active');
    return active[active.length - 1]?.run_id;
  }

  /**
   * Check if a conversation is waiting for a response after tool attachment
   */
  isWaitingForToolResponse(matrixEventId: string): boolean {
    const conv = this.conversations.get(matrixEventId);
    if (!conv) return false;
    
    return conv.status === 'active' && 
           conv.tools_attached !== undefined && 
           conv.tools_attached.length > 0;
  }

  /**
   * Get all active conversations (for monitoring)
   */
  getActiveConversations(): ConversationState[] {
    const active: ConversationState[] = [];
    for (const conv of this.conversations.values()) {
      if (conv.status === 'active' || conv.status === 'pending') {
        active.push(conv);
      }
    }
    return active;
  }

  /**
   * Get statistics about tracked conversations
   */
  getStats(): {
    total: number;
    active: number;
    pending: number;
    completed: number;
    timeout: number;
  } {
    let active = 0, pending = 0, completed = 0, timeout = 0;
    
    for (const conv of this.conversations.values()) {
      switch (conv.status) {
        case 'active': active++; break;
        case 'pending': pending++; break;
        case 'completed': completed++; break;
        case 'timeout': timeout++; break;
      }
    }
    
    return { 
      total: this.conversations.size, 
      active, 
      pending, 
      completed, 
      timeout 
    };
  }

  /**
   * Clean up old conversations
   */
  cleanup(): number {
    const now = Date.now();
    const toRemove: string[] = [];
    
    for (const [eventId, conv] of this.conversations) {
      const createdAt = new Date(conv.created_at).getTime();
      const age = now - createdAt;
      
      if (age > this.maxAgeMs) {
        // Mark as timeout if still active
        if (conv.status === 'active' || conv.status === 'pending') {
          conv.status = 'timeout';
          console.log(`[ConversationTracker] Conversation ${eventId} timed out after ${Math.round(age / 1000)}s`);
        }
        
        // Remove completed/timeout conversations older than max age
        if (conv.status === 'completed' || conv.status === 'timeout') {
          toRemove.push(eventId);
        }
      }
    }
    
    for (const eventId of toRemove) {
      const conv = this.conversations.get(eventId);
      if (conv) {
        // Clean up all mappings
        if (this.agentConversations.get(conv.agent_id) === eventId) {
          this.agentConversations.delete(conv.agent_id);
        }
        for (const run of conv.runs) {
          this.runToConversation.delete(run.run_id);
        }
        this.conversations.delete(eventId);
      }
    }
    
    if (toRemove.length > 0) {
      console.log(`[ConversationTracker] Cleaned up ${toRemove.length} old conversations`);
    }
    
    return toRemove.length;
  }

  /**
   * Start periodic cleanup
   */
  private startCleanupTimer(): void {
    this.cleanupTimer = setInterval(() => {
      this.cleanup();
    }, this.cleanupIntervalMs);
    
    // Don't keep process alive just for cleanup
    this.cleanupTimer.unref();
  }

  /**
   * Stop the tracker and clean up
   */
  stop(): void {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = undefined;
    }
  }
}

// Singleton instance
let trackerInstance: ConversationTracker | undefined;

export function getConversationTracker(options?: {
  maxAgeSeconds?: number;
  cleanupIntervalSeconds?: number;
}): ConversationTracker {
  if (!trackerInstance) {
    trackerInstance = new ConversationTracker(options);
  }
  return trackerInstance;
}
