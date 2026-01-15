/**
 * OpenCode SSE Bridge
 * Bidirectional event forwarding between OpenCode and Matrix
 */

import { EventEmitter } from 'events';
import type { Storage } from '../core/storage.js';
import type { MatrixClientPool } from '../core/client-pool.js';
import type { RoomManager } from '../core/room-manager.js';
import type { OpenCodeService } from './opencode-service.js';

export interface OpenCodeEvent {
  type: string;
  sessionId?: string;
  data?: Record<string, unknown>;
  timestamp: number;
}

export interface BridgeSession {
  directory: string;
  sessionId: string;
  identityId: string;
  mxid: string;
  targetRoomId?: string;  // Room to forward events to
  targetMxid?: string;    // User to DM events to
  sseConnected: boolean;
  matrixSubscribed: boolean;
  createdAt: number;
  lastEventAt: number;
  eventCount: number;
}

export interface BridgeConfig {
  openCodeBaseUrl: string;
  reconnectIntervalMs?: number;
  heartbeatIntervalMs?: number;
}

export class OpenCodeSSEBridge extends EventEmitter {
  private storage: Storage;
  private clientPool: MatrixClientPool;
  private roomManager: RoomManager;
  private openCodeService: OpenCodeService;
  private config: BridgeConfig;
  
  private sessions: Map<string, BridgeSession> = new Map();
  private sseConnections: Map<string, AbortController> = new Map();
  private reconnectTimers: Map<string, NodeJS.Timeout> = new Map();

  constructor(
    storage: Storage,
    clientPool: MatrixClientPool,
    roomManager: RoomManager,
    openCodeService: OpenCodeService,
    config: BridgeConfig
  ) {
    super();
    this.storage = storage;
    this.clientPool = clientPool;
    this.roomManager = roomManager;
    this.openCodeService = openCodeService;
    this.config = {
      reconnectIntervalMs: 5000,
      heartbeatIntervalMs: 30000,
      ...config
    };
  }

  /**
   * Start bridge for an OpenCode session
   */
  async startBridge(
    directory: string,
    sessionId: string,
    options: {
      targetRoomId?: string;
      targetMxid?: string;
    } = {}
  ): Promise<BridgeSession> {
    // Get or create identity
    const identity = await this.openCodeService.getOrCreateIdentity(directory);
    
    const session: BridgeSession = {
      directory,
      sessionId,
      identityId: identity.id,
      mxid: identity.mxid,
      targetRoomId: options.targetRoomId,
      targetMxid: options.targetMxid,
      sseConnected: false,
      matrixSubscribed: false,
      createdAt: Date.now(),
      lastEventAt: Date.now(),
      eventCount: 0
    };

    this.sessions.set(sessionId, session);

    // Start SSE connection to OpenCode
    await this.connectSSE(session);

    // Subscribe to Matrix messages if we have a target
    if (options.targetRoomId || options.targetMxid) {
      await this.subscribeMatrix(session);
    }

    console.log('[SSEBridge] Started bridge for session:', sessionId);
    return session;
  }

  /**
   * Stop bridge for a session
   */
  stopBridge(sessionId: string): boolean {
    const session = this.sessions.get(sessionId);
    if (!session) {
      return false;
    }

    // Abort SSE connection
    const controller = this.sseConnections.get(sessionId);
    if (controller) {
      controller.abort();
      this.sseConnections.delete(sessionId);
    }

    // Clear reconnect timer
    const timer = this.reconnectTimers.get(sessionId);
    if (timer) {
      clearTimeout(timer);
      this.reconnectTimers.delete(sessionId);
    }

    this.sessions.delete(sessionId);
    console.log('[SSEBridge] Stopped bridge for session:', sessionId);
    return true;
  }

  /**
   * Connect to OpenCode SSE event stream
   */
  private async connectSSE(session: BridgeSession): Promise<void> {
    const controller = new AbortController();
    this.sseConnections.set(session.sessionId, controller);

    const url = `${this.config.openCodeBaseUrl}/event`;
    
    try {
      const response = await fetch(url, {
        headers: {
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache'
        },
        signal: controller.signal
      });

      if (!response.ok) {
        throw new Error(`SSE connection failed: ${response.status}`);
      }

      if (!response.body) {
        throw new Error('No response body for SSE');
      }

      session.sseConnected = true;
      console.log('[SSEBridge] SSE connected for session:', session.sessionId);

      // Process SSE stream
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              await this.handleOpenCodeEvent(session, data);
            } catch (e) {
              // Ignore parse errors for non-JSON data
            }
          }
        }
      }
    } catch (error: any) {
      if (error.name === 'AbortError') {
        console.log('[SSEBridge] SSE connection aborted for session:', session.sessionId);
        return;
      }

      console.error('[SSEBridge] SSE connection error:', error.message);
      session.sseConnected = false;

      // Schedule reconnect
      this.scheduleReconnect(session);
    }
  }

  /**
   * Schedule SSE reconnection
   */
  private scheduleReconnect(session: BridgeSession): void {
    const existing = this.reconnectTimers.get(session.sessionId);
    if (existing) {
      clearTimeout(existing);
    }

    const timer = setTimeout(async () => {
      this.reconnectTimers.delete(session.sessionId);
      if (this.sessions.has(session.sessionId)) {
        console.log('[SSEBridge] Reconnecting SSE for session:', session.sessionId);
        await this.connectSSE(session);
      }
    }, this.config.reconnectIntervalMs);

    this.reconnectTimers.set(session.sessionId, timer);
  }

  /**
   * Handle incoming OpenCode event
   */
  private async handleOpenCodeEvent(
    session: BridgeSession,
    event: Record<string, unknown>
  ): Promise<void> {
    session.lastEventAt = Date.now();
    session.eventCount++;

    const eventType = event.type as string;
    
    // Emit for external listeners
    this.emit('opencode:event', {
      sessionId: session.sessionId,
      event,
      timestamp: Date.now()
    });

    // Forward to Matrix based on event type
    switch (eventType) {
      case 'session.created':
        await this.forwardToMatrix(session, `🚀 OpenCode session started: ${session.directory}`);
        break;

      case 'session.idle':
        const summary = event.summary || 'Session went idle';
        await this.forwardToMatrix(session, `💤 Session idle: ${summary}`);
        break;

      case 'message.updated':
        if (event.role === 'assistant' && event.content) {
          await this.forwardToMatrix(session, `🤖 ${event.content}`);
        }
        break;

      case 'file.edited':
        const file = event.path || 'unknown file';
        await this.forwardToMatrix(session, `📝 File edited: ${file}`);
        break;

      case 'error':
        const errorMsg = event.message || 'Unknown error';
        await this.forwardToMatrix(session, `❌ Error: ${errorMsg}`);
        break;
    }
  }

  /**
   * Forward message to Matrix
   */
  private async forwardToMatrix(session: BridgeSession, message: string): Promise<void> {
    if (!session.targetRoomId && !session.targetMxid) {
      return;  // No target configured
    }

    try {
      const identity = await this.storage.getIdentityAsync(session.identityId);
      if (!identity) {
        console.error('[SSEBridge] Identity not found:', session.identityId);
        return;
      }

      const client = await this.clientPool.getClient(identity);

      let roomId = session.targetRoomId;
      if (!roomId && session.targetMxid) {
        // Get or create DM room
        roomId = await this.roomManager.getOrCreateDMRoom(identity.mxid, session.targetMxid);
      }

      if (roomId) {
        await client.sendMessage(roomId, {
          msgtype: 'm.text',
          body: message
        });
        console.log('[SSEBridge] Forwarded to Matrix:', message.substring(0, 50));
      }
    } catch (error) {
      console.error('[SSEBridge] Failed to forward to Matrix:', error);
    }
  }

  /**
   * Subscribe to Matrix messages for injection into OpenCode
   */
  private async subscribeMatrix(session: BridgeSession): Promise<void> {
    try {
      const identity = await this.storage.getIdentityAsync(session.identityId);
      if (!identity) {
        return;
      }

      const client = await this.clientPool.getClient(identity);

      // Listen for room messages
      client.on('room.message', async (roomId: string, event: any) => {
        // Only process messages from target room/user
        if (session.targetRoomId && roomId !== session.targetRoomId) {
          return;
        }

        // Don't process our own messages
        if (event.sender === identity.mxid) {
          return;
        }

        // Check if from target user in DM
        if (session.targetMxid && event.sender !== session.targetMxid) {
          return;
        }

        const content = event.content?.body;
        if (content) {
          if (session.targetRoomId) {
            const localpart = session.mxid.split(':')[0];
            const mentionMatches = [session.mxid, localpart].some((mention) => content.includes(mention));
            if (!mentionMatches) {
              return;
            }
          }

          await this.injectToOpenCode(session, event.sender, content);
        }
      });

      session.matrixSubscribed = true;
      console.log('[SSEBridge] Matrix subscription active for session:', session.sessionId);
    } catch (error) {
      console.error('[SSEBridge] Failed to subscribe to Matrix:', error);
    }
  }

  /**
   * Inject message into OpenCode session
   */
  private async injectToOpenCode(
    session: BridgeSession,
    sender: string,
    message: string
  ): Promise<void> {
    try {
      const url = `${this.config.openCodeBaseUrl}/session/${session.sessionId}/message`;
      
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          content: `[Matrix: ${sender}] ${message}`,
          noReply: true  // Don't trigger auto-response
        })
      });

      if (!response.ok) {
        throw new Error(`Injection failed: ${response.status}`);
      }

      console.log('[SSEBridge] Injected message to OpenCode:', message.substring(0, 50));
      
      this.emit('matrix:injected', {
        sessionId: session.sessionId,
        sender,
        message,
        timestamp: Date.now()
      });
    } catch (error) {
      console.error('[SSEBridge] Failed to inject to OpenCode:', error);
    }
  }

  /**
   * Get bridge session
   */
  getSession(sessionId: string): BridgeSession | undefined {
    return this.sessions.get(sessionId);
  }

  /**
   * List all active bridge sessions
   */
  listSessions(): BridgeSession[] {
    return Array.from(this.sessions.values());
  }

  /**
   * Get bridge status
   */
  getStatus(): {
    activeSessions: number;
    totalEvents: number;
    sessions: Array<{
      sessionId: string;
      directory: string;
      sseConnected: boolean;
      matrixSubscribed: boolean;
      eventCount: number;
    }>;
  } {
    const sessions = this.listSessions();
    return {
      activeSessions: sessions.length,
      totalEvents: sessions.reduce((sum, s) => sum + s.eventCount, 0),
      sessions: sessions.map(s => ({
        sessionId: s.sessionId,
        directory: s.directory,
        sseConnected: s.sseConnected,
        matrixSubscribed: s.matrixSubscribed,
        eventCount: s.eventCount
      }))
    };
  }

  /**
   * Stop all bridges
   */
  stopAll(): void {
    for (const sessionId of this.sessions.keys()) {
      this.stopBridge(sessionId);
    }
    console.log('[SSEBridge] All bridges stopped');
  }
}
