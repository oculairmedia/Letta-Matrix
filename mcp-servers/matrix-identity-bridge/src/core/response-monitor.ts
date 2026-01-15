/**
 * Response Monitor for Cross-Run Tracking
 * 
 * Monitors Letta agents for responses after tool attachments and posts
 * them back to the original Matrix thread.
 */

import type { Letta } from '@letta-ai/letta-client';
import type { MatrixClientPool } from './client-pool.js';
import type { Storage } from './storage.js';
import type { ConversationState, ConversationTracker } from './conversation-tracker.js';

export interface ResponseMonitorConfig {
  maxWaitSeconds: number;
  pollIntervalSeconds: number;
}

export interface MonitorResult {
  success: boolean;
  responsePosted: boolean;
  responseContent?: string;
  error?: string;
  timedOut?: boolean;
}

export class ResponseMonitor {
  private lettaClient: Letta;
  private clientPool: MatrixClientPool;
  private storage: Storage;
  private tracker: ConversationTracker;
  private config: ResponseMonitorConfig;
  private activeMonitors: Map<string, AbortController> = new Map();

  constructor(
    lettaClient: Letta,
    clientPool: MatrixClientPool,
    storage: Storage,
    tracker: ConversationTracker,
    config?: Partial<ResponseMonitorConfig>
  ) {
    this.lettaClient = lettaClient;
    this.clientPool = clientPool;
    this.storage = storage;
    this.tracker = tracker;
    this.config = {
      maxWaitSeconds: config?.maxWaitSeconds ?? 60,
      pollIntervalSeconds: config?.pollIntervalSeconds ?? 2
    };
  }

  /**
   * Start monitoring for a conversation after tool attachment
   */
  async monitorConversation(conv: ConversationState): Promise<MonitorResult> {
    const { matrix_event_id, matrix_room_id, agent_id, created_at, runs } = conv;
    
    // Get the most recent run_id to filter messages
    const activeRun = runs.length > 0 ? runs[runs.length - 1] : null;
    const targetRunId = activeRun?.run_id;
    
    console.log(`[ResponseMonitor] Starting to monitor conversation ${matrix_event_id} for agent ${agent_id}, run: ${targetRunId || 'none'}`);
    
    // Create abort controller for this monitor
    const abortController = new AbortController();
    this.activeMonitors.set(matrix_event_id, abortController);
    
    const convCreatedAt = new Date(created_at).getTime();
    let elapsed = 0;
    const maxWaitMs = this.config.maxWaitSeconds * 1000;
    const pollIntervalMs = this.config.pollIntervalSeconds * 1000;

    try {
      while (elapsed < maxWaitMs) {
        // Check if aborted
        if (abortController.signal.aborted) {
          console.log(`[ResponseMonitor] Monitoring cancelled for ${matrix_event_id}`);
          return { success: false, responsePosted: false, error: 'cancelled' };
        }

        try {
          // Get recent messages for the agent
          const messagesPage = this.lettaClient.agents.messages.list(agent_id, {
            limit: 20
          });
          
          // Look for new assistant messages from the specific run
          for await (const msg of messagesPage) {
            // Check if this is an assistant message
            if (msg.message_type === 'assistant_message' && msg.content) {
              // Get the run_id from the message (if available)
              const msgRunId = (msg as { run_id?: string }).run_id;
              
              // If we have a target run_id, only accept messages from that run
              if (targetRunId && msgRunId && msgRunId !== targetRunId) {
                continue; // Skip messages from other runs
              }
              
              // Parse the date timestamp (Letta uses 'date' not 'created_at')
              const msgDate = (msg as { date?: string }).date;
              const msgCreatedAt = msgDate ? new Date(msgDate).getTime() : 0;
              
              // Check if message is newer than conversation start
              if (msgCreatedAt > convCreatedAt) {
                console.log(`[ResponseMonitor] Found new response for conversation ${matrix_event_id} from run ${msgRunId || 'unknown'}`);
                
                // Extract content - may be string or array
                const contentStr = typeof msg.content === 'string' 
                  ? msg.content 
                  : JSON.stringify(msg.content);
                
                // Skip if this looks like an inter-agent message relay (not the actual response)
                if (contentStr.includes('[INTER-AGENT MESSAGE from') || contentStr.includes('[MESSAGE FROM OPENCODE USER]')) {
                  console.log(`[ResponseMonitor] Skipping inter-agent/opencode relay message`);
                  continue;
                }
                
                // Post response to Matrix
                await this.postResponseToMatrix(conv, contentStr);
                
                // Mark conversation as complete
                this.tracker.completeConversation(matrix_event_id);
                
                return {
                  success: true,
                  responsePosted: true,
                  responseContent: contentStr
                };
              }
            }
          }

        } catch (error) {
          console.error(`[ResponseMonitor] Error polling messages:`, error);
          // Continue polling despite errors
        }

        // Wait before next poll
        await this.sleep(pollIntervalMs, abortController.signal);
        elapsed += pollIntervalMs;
      }

      // Timeout reached
      console.log(`[ResponseMonitor] Timeout waiting for response for ${matrix_event_id}`);
      
      // Post timeout message to Matrix
      await this.postResponseToMatrix(
        conv,
        "I'm still processing your request. The operation may take longer than expected."
      );
      
      // Mark conversation as timed out (handled by tracker cleanup)
      return {
        success: false,
        responsePosted: true,
        timedOut: true
      };

    } finally {
      // Clean up abort controller
      this.activeMonitors.delete(matrix_event_id);
    }
  }

  /**
   * Post a response back to Matrix as a reply to the original message
   */
  private async postResponseToMatrix(
    conv: ConversationState,
    content: string
  ): Promise<void> {
    const { matrix_room_id, matrix_event_id, agent_id } = conv;
    
    try {
      // Get an identity that can post to the room
      // Preferably the agent's identity, or fall back to admin
      // Identity IDs are stored as "letta_agent-UUID" (keeping the agent- prefix)
      const agentIdentityId = `letta_${agent_id}`;
      console.log(`[ResponseMonitor] Looking for identity: ${agentIdentityId}`);
      let client = await this.clientPool.getClientById(agentIdentityId);
      
      // If no agent identity, try to get any identity that's in the room
      if (!client) {
        // Fall back to admin or first available identity
        const identities = await this.storage.getAllIdentitiesAsync();
        for (const identity of identities) {
          client = await this.clientPool.getClientById(identity.id);
          if (client) {
            // Check if client is in the room
            try {
              const joinedRooms = await client.getJoinedRooms();
              if (joinedRooms.includes(matrix_room_id)) {
                break;
              }
            } catch {
              // Continue to next identity
            }
          }
          client = undefined;
        }
      }

      if (!client) {
        console.error(`[ResponseMonitor] No client available to post to room ${matrix_room_id}`);
        return;
      }

      // Send message as a reply to the original
      await client.sendMessage(matrix_room_id, {
        msgtype: 'm.text',
        body: content,
        'm.relates_to': {
          'm.in_reply_to': {
            event_id: matrix_event_id
          }
        }
      });

      console.log(`[ResponseMonitor] Posted response to ${matrix_room_id} as reply to ${matrix_event_id}`);

    } catch (error) {
      console.error(`[ResponseMonitor] Failed to post response to Matrix:`, error);
      throw error;
    }
  }

  /**
   * Cancel monitoring for a conversation
   */
  cancelMonitoring(matrixEventId: string): boolean {
    const controller = this.activeMonitors.get(matrixEventId);
    if (controller) {
      controller.abort();
      this.activeMonitors.delete(matrixEventId);
      console.log(`[ResponseMonitor] Cancelled monitoring for ${matrixEventId}`);
      return true;
    }
    return false;
  }

  /**
   * Get count of active monitors
   */
  getActiveMonitorCount(): number {
    return this.activeMonitors.size;
  }

  /**
   * Sleep with abort signal support
   */
  private sleep(ms: number, signal?: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(resolve, ms);
      
      if (signal) {
        signal.addEventListener('abort', () => {
          clearTimeout(timeout);
          reject(new Error('Aborted'));
        }, { once: true });
      }
    });
  }
}

// Singleton instance
let monitorInstance: ResponseMonitor | undefined;

export function getResponseMonitor(): ResponseMonitor | undefined {
  return monitorInstance;
}

export function initializeResponseMonitor(
  lettaClient: Letta,
  clientPool: MatrixClientPool,
  storage: Storage,
  tracker: ConversationTracker,
  config?: Partial<ResponseMonitorConfig>
): ResponseMonitor {
  monitorInstance = new ResponseMonitor(lettaClient, clientPool, storage, tracker, config);
  return monitorInstance;
}
