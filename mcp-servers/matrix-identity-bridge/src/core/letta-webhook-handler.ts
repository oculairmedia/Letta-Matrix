/**
 * Letta Webhook Handler
 * 
 * Handles webhooks from Letta server for agent.run.completed events.
 * Replaces polling-based ResponseMonitor with push notifications.
 * 
 * Webhook payload structure (WebhookEvent from Letta):
 * {
 *   "id": "evt-xxx",
 *   "event_type": "agent.run.completed",
 *   "timestamp": "ISO8601",
 *   "agent_id": "agent-xxx",
 *   "organization_id": "org-xxx",
 *   "data": {
 *     "run_id": "run-xxx",
 *     "stop_reason": {...},
 *     "usage": {...},
 *     "message_count": N,
 *     "messages": [...]  // Array of response messages
 *   }
 * }
 */

import crypto from 'crypto';
import type http from 'http';
import type { MatrixClientPool } from './client-pool.js';
import type { Storage } from './storage.js';
import { getConversationTracker } from './conversation-tracker.js';
import { getResponseMonitor } from './response-monitor.js';

export interface LettaWebhookConfig {
  /** HMAC secret for signature verification */
  webhookSecret?: string;
  /** Skip signature verification (dev mode only) */
  skipVerification?: boolean;
  /** Matrix API URL for agent room lookups */
  matrixApiUrl?: string;
  /** Post silent audit messages for non-Matrix conversations (default: true) */
  auditNonMatrixConversations?: boolean;
}

/**
 * Content part in array format (Letta v1 style)
 * Example: {type: "text", text: "Hello world"}
 */
export interface LettaContentPart {
  type: string;
  text?: string;
  // Other potential fields for non-text content types
  [key: string]: unknown;
}

export interface LettaMessage {
  message_type: string;
  /** 
   * Content can be:
   * - string: Direct text content
   * - LettaContentPart[]: Array of content parts, each with {type, text}
   * - object: Other structured content
   */
  content?: string | LettaContentPart[] | object;
  role?: string;
  date?: string;
  assistant_message?: string;
  // Additional fields for debugging
  id?: string;
  name?: string;
}

export interface LettaRunCompletedPayload {
  id?: string;
  event_type: 'agent.run.completed';
  agent_id: string;
  organization_id?: string;
  timestamp?: string;
  data?: {
    run_id?: string;
    stop_reason?: object;
    usage?: object;
    message_count?: number;
    messages?: LettaMessage[];
  };
}

export interface LettaWebhookResult {
  success: boolean;
  responsePosted: boolean;
  responseContent?: string;
  error?: string;
  agentId?: string;
  roomId?: string;
}

export class LettaWebhookHandler {
  private clientPool: MatrixClientPool;
  private storage: Storage;
  private config: LettaWebhookConfig;
  private roomCache: Map<string, { roomId: string; cachedAt: number }> = new Map();
  private readonly CACHE_TTL_MS = 60000; // 1 minute cache

  constructor(
    clientPool: MatrixClientPool,
    storage: Storage,
    config?: LettaWebhookConfig
  ) {
    this.clientPool = clientPool;
    this.storage = storage;
    this.config = {
      webhookSecret: config?.webhookSecret || process.env.LETTA_WEBHOOK_SECRET,
      skipVerification: config?.skipVerification || process.env.NODE_ENV === 'development',
      matrixApiUrl: config?.matrixApiUrl || process.env.MATRIX_API_URL || 'http://matrix-api:8000',
      auditNonMatrixConversations: config?.auditNonMatrixConversations ?? (process.env.AUDIT_NON_MATRIX !== 'false')
    };
  }

  /**
   * Verify webhook signature using HMAC-SHA256
   * Letta uses Stripe-style format: t=timestamp,v1=signature
   */
  verifySignature(payload: string, signature: string | undefined): boolean {
    if (this.config.skipVerification) {
      console.log('[LettaWebhook] Signature verification skipped (dev mode)');
      return true;
    }

    if (!this.config.webhookSecret) {
      console.warn('[LettaWebhook] No webhook secret configured, skipping verification');
      return true;
    }

    if (!signature) {
      console.error('[LettaWebhook] No signature provided in request');
      return false;
    }

    // Parse Stripe-style signature format: t=timestamp,v1=signature
    const parts = signature.split(',');
    let timestamp: string | undefined;
    let providedSignature: string | undefined;

    for (const part of parts) {
      const [key, value] = part.split('=');
      if (key === 't') timestamp = value;
      if (key === 'v1') providedSignature = value;
    }

    if (!timestamp || !providedSignature) {
      console.error('[LettaWebhook] Invalid signature format, expected t=timestamp,v1=signature');
      return false;
    }

    // Compute expected signature: HMAC-SHA256(timestamp.payload)
    const signedPayload = `${timestamp}.${payload}`;
    const expectedSignature = crypto
      .createHmac('sha256', this.config.webhookSecret)
      .update(signedPayload)
      .digest('hex');

    // Use timing-safe comparison
    try {
      const isValid = crypto.timingSafeEqual(
        Buffer.from(providedSignature, 'hex'),
        Buffer.from(expectedSignature, 'hex')
      );

      if (!isValid) {
        console.error('[LettaWebhook] Signature verification failed');
      }

      return isValid;
    } catch (error) {
      console.error('[LettaWebhook] Signature comparison error:', error);
      return false;
    }
  }

  /**
   * Handle agent.run.completed webhook
   */
  async handleRunCompleted(payload: LettaRunCompletedPayload): Promise<LettaWebhookResult> {
    const { agent_id, data } = payload;
    const run_id = data?.run_id;
    const messages = data?.messages;

    console.log(`[LettaWebhook] Received agent.run.completed for agent ${agent_id}, run ${run_id || 'unknown'}, messages: ${messages?.length || 0}`);

    // Find the Matrix room for this agent
    const roomId = await this.findMatrixRoomForAgent(agent_id);
    if (!roomId) {
      console.warn(`[LettaWebhook] No Matrix room found for agent ${agent_id}`);
      return {
        success: false,
        responsePosted: false,
        error: 'no_matrix_room',
        agentId: agent_id
      };
    }

    // Extract assistant message content from the messages array
    const assistantContent = this.extractAssistantContent(messages);
    if (!assistantContent) {
      console.log(`[LettaWebhook] No assistant message content in webhook for agent ${agent_id}`);
      return {
        success: true,
        responsePosted: false,
        error: 'no_assistant_content',
        agentId: agent_id,
        roomId
      };
    }

    // Skip inter-agent relay messages
    if (this.isInterAgentRelay(assistantContent)) {
      console.log(`[LettaWebhook] Skipping inter-agent relay message for agent ${agent_id}`);
      return {
        success: true,
        responsePosted: false,
        error: 'inter_agent_relay',
        agentId: agent_id,
        roomId
      };
    }

    // Check if we have an active CROSS-RUN conversation to reply to
    // IMPORTANT: Only post to Matrix if:
    // 1. There's a tracked conversation (cross-run scenario with tool attachments)
    // 2. The conversation is waiting for a tool response
    //
    // Normal Matrix conversations are handled by matrix-client directly.
    // Webhooks are ONLY for cross-run scenarios where matrix-client can't wait.
    const tracker = getConversationTracker();
    const conversation = tracker.getConversationByAgent(agent_id);

    // Determine if this is a cross-run scenario (Matrix conversation with tools attached)
    const isCrossRunScenario = conversation && 
      conversation.tools_attached && 
      conversation.tools_attached.length > 0;

    // If no active tracked conversation OR not a cross-run scenario,
    // post a SILENT audit message instead of a regular response
    if (!isCrossRunScenario) {
      // Post silent audit message if enabled
      if (this.config.auditNonMatrixConversations) {
        const source = conversation ? 'matrix-direct' : 'external';
        console.log(`[LettaWebhook] Posting silent audit for ${agent_id} (source: ${source}, content length: ${assistantContent.length}, preview: "${assistantContent.substring(0, 100)}...")`);
        
        try {
          await this.postSilentAudit(agent_id, roomId, assistantContent, source, run_id);
          return {
            success: true,
            responsePosted: true,
            responseContent: `[AUDIT] ${assistantContent.substring(0, 100)}...`,
            agentId: agent_id,
            roomId
          };
        } catch (error) {
          console.error(`[LettaWebhook] Failed to post audit:`, error);
          return {
            success: false,
            responsePosted: false,
            error: 'audit_post_failed',
            agentId: agent_id,
            roomId
          };
        }
      }
      
      console.log(`[LettaWebhook] No cross-run conversation for ${agent_id}, audit disabled`);
      return {
        success: true,
        responsePosted: false,
        error: 'no_crossrun_conversation',
        agentId: agent_id,
        roomId
      };
    }

    console.log(`[LettaWebhook] Cross-run response for ${conversation!.matrix_event_id} (${conversation!.tools_attached!.length} tools attached)`)

    // Post the response to Matrix
    try {
      await this.postToMatrix(agent_id, roomId, assistantContent, conversation?.matrix_event_id);

      if (conversation) {
        const monitor = getResponseMonitor();
        if (monitor) {
          monitor.cancelMonitoring(conversation.matrix_event_id);
        }
        tracker.completeConversation(conversation.matrix_event_id);
      }

      console.log(`[LettaWebhook] Posted response to Matrix room ${roomId} for agent ${agent_id}`);

      return {
        success: true,
        responsePosted: true,
        responseContent: assistantContent.substring(0, 200) + (assistantContent.length > 200 ? '...' : ''),
        agentId: agent_id,
        roomId
      };

    } catch (error) {
      console.error(`[LettaWebhook] Failed to post to Matrix:`, error);
      return {
        success: false,
        responsePosted: false,
        error: error instanceof Error ? error.message : 'matrix_post_failed',
        agentId: agent_id,
        roomId
      };
    }
  }

  /**
   * Find the Matrix room ID for an agent
   * Uses the Matrix API to query the PostgreSQL database
   */
  private async findMatrixRoomForAgent(agentId: string): Promise<string | undefined> {
    // Check cache first
    const cached = this.roomCache.get(agentId);
    if (cached && Date.now() - cached.cachedAt < this.CACHE_TTL_MS) {
      console.log(`[LettaWebhook] Using cached room ${cached.roomId} for agent ${agentId}`);
      return cached.roomId;
    }

    // Query Matrix API for agent room mapping
    try {
      const response = await fetch(`${this.config.matrixApiUrl}/agents/${agentId}/room`);
      
      if (response.ok) {
        const data = await response.json() as { room_id?: string; agent_name?: string };
        if (data.room_id) {
          // Cache the result
          this.roomCache.set(agentId, { roomId: data.room_id, cachedAt: Date.now() });
          console.log(`[LettaWebhook] Found room ${data.room_id} for agent ${agentId} (${data.agent_name || 'unknown'})`);
          return data.room_id;
        }
      } else if (response.status === 404) {
        console.warn(`[LettaWebhook] Agent ${agentId} not found in Matrix API`);
      } else {
        console.error(`[LettaWebhook] Matrix API error: ${response.status} ${response.statusText}`);
      }
    } catch (error) {
      console.error(`[LettaWebhook] Failed to query Matrix API:`, error);
    }

    // No room found for this agent
    console.warn(`[LettaWebhook] No room mapping found for agent ${agentId}`);
    return undefined;
  }

  private extractAssistantContent(
    messages?: LettaMessage[]
  ): string | undefined {
    if (!messages || messages.length === 0) {
      return undefined;
    }

    let longestContent: string | undefined;
    let longestLength = 0;

    for (const msg of messages) {
      if (msg.message_type === 'assistant_message') {
        const extracted = this.extractContentText(msg.content) || msg.assistant_message;
        if (extracted && extracted.length > longestLength) {
          longestContent = extracted;
          longestLength = extracted.length;
        }
      }
    }

    return longestContent;
  }

  private extractContentText(content: LettaMessage['content']): string | undefined {
    if (!content) {
      return undefined;
    }

    if (typeof content === 'string') {
      return content;
    }

    if (Array.isArray(content)) {
      const textParts: string[] = [];
      for (const part of content) {
        if (part && typeof part === 'object' && 'type' in part) {
          if (part.type === 'text' && typeof part.text === 'string') {
            textParts.push(part.text);
          }
        }
      }
      if (textParts.length > 0) {
        return textParts.join('\n');
      }
      console.warn('[LettaWebhook] Content array has no text parts:', JSON.stringify(content).substring(0, 200));
      return undefined;
    }

    if (typeof content === 'object' && 'text' in content && typeof (content as { text: unknown }).text === 'string') {
      return (content as { text: string }).text;
    }

    console.warn('[LettaWebhook] Unknown content format:', typeof content, JSON.stringify(content).substring(0, 200));
    return JSON.stringify(content);
  }

  /**
   * Check if message is an inter-agent relay (should be skipped)
   */
  private isInterAgentRelay(content: string): boolean {
    return (
      content.includes('[INTER-AGENT MESSAGE from') ||
      content.includes('[MESSAGE FROM OPENCODE USER]') ||
      content.includes('[FORWARDED FROM')
    );
  }

  /**
   * Post a silent audit message to Matrix (m.notice, no notification)
   * Used for non-Matrix conversations (CLI, API) to maintain audit trail
   */
  private async postSilentAudit(
    agentId: string,
    roomId: string,
    content: string,
    source: 'external' | 'matrix-direct',
    runId?: string
  ): Promise<void> {
    // Get a Matrix client for this agent
    const agentIdentityId = `letta_${agentId}`;
    let client = await this.clientPool.getClientById(agentIdentityId);

    // Fallback to finding any client in the room
    if (!client) {
      const identities = this.storage.getAllIdentities();
      for (const identity of identities) {
        const testClient = await this.clientPool.getClientById(identity.id);
        if (testClient) {
          try {
            const joinedRooms = await testClient.getJoinedRooms();
            if (joinedRooms.includes(roomId)) {
              client = testClient;
              break;
            }
          } catch {
            // Continue to next identity
          }
        }
      }
    }

    if (!client) {
      throw new Error(`No Matrix client available for room ${roomId}`);
    }

    // Truncate content for audit (keep it concise)
    const maxLength = 500;
    const truncatedContent = content.length > maxLength 
      ? content.substring(0, maxLength) + '...' 
      : content;

    // Format audit message with source indicator
    const sourceEmoji = source === 'external' ? '🖥️' : '💬';
    const sourceLabel = source === 'external' ? 'CLI/API' : 'Direct';
    
    // Build audit message - concise format
    const auditMessage = `${sourceEmoji} **[${sourceLabel}]** ${truncatedContent}`;

    // Post as m.notice (silent, no notification in most clients)
    await client.sendMessage(roomId, {
      msgtype: 'm.notice',  // Notice type = typically no notification
      body: auditMessage,
      format: 'org.matrix.custom.html',
      formatted_body: `<em>${sourceEmoji} <strong>[${sourceLabel}]</strong></em> ${this.escapeHtml(truncatedContent)}`,
      // Additional hint to suppress notifications
      'org.matrix.msc1767.message': [{
        body: auditMessage,
        mimetype: 'text/plain'
      }]
    });

    console.log(`[LettaWebhook] Posted silent audit to ${roomId} (${source})`);
  }

  /**
   * Escape HTML for formatted messages
   */
  private escapeHtml(text: string): string {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /**
   * Post message to Matrix room
   */
  private async postToMatrix(
    agentId: string,
    roomId: string,
    content: string,
    replyToEventId?: string
  ): Promise<void> {
    // Get a Matrix client for this agent
    const agentIdentityId = `letta_${agentId}`;
    let client = await this.clientPool.getClientById(agentIdentityId);

    // Fallback to finding any client in the room
    if (!client) {
      const identities = this.storage.getAllIdentities();
      for (const identity of identities) {
        const testClient = await this.clientPool.getClientById(identity.id);
        if (testClient) {
          try {
            const joinedRooms = await testClient.getJoinedRooms();
            if (joinedRooms.includes(roomId)) {
              client = testClient;
              break;
            }
          } catch {
            // Continue to next identity
          }
        }
      }
    }

    if (!client) {
      throw new Error(`No Matrix client available for room ${roomId}`);
    }

    // Build message content
    const messageContent: Record<string, unknown> = {
      msgtype: 'm.text',
      body: content
    };

    // Add reply relation if we have an event to reply to
    if (replyToEventId) {
      messageContent['m.relates_to'] = {
        'm.in_reply_to': {
          event_id: replyToEventId
        }
      };
    }

    await client.sendMessage(roomId, messageContent);
  }
}

// Singleton instance
let handlerInstance: LettaWebhookHandler | undefined;

export function getLettaWebhookHandler(): LettaWebhookHandler | undefined {
  return handlerInstance;
}

export function initializeLettaWebhookHandler(
  clientPool: MatrixClientPool,
  storage: Storage,
  config?: LettaWebhookConfig
): LettaWebhookHandler {
  handlerInstance = new LettaWebhookHandler(clientPool, storage, config);
  console.log('[LettaWebhook] Handler initialized');
  return handlerInstance;
}
