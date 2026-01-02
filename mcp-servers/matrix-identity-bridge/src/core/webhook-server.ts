/**
 * Webhook Server for Cross-Run Tracking
 * 
 * A separate HTTP server that handles webhooks from the Tool Selector
 * and provides monitoring endpoints. Runs alongside the MCP server.
 */

import http from 'http';
import { getConversationTracker, type WebhookPayload } from './conversation-tracker.js';
import { getResponseMonitor } from './response-monitor.js';
import { getLettaWebhookHandler, type LettaRunCompletedPayload } from './letta-webhook-handler.js';

export interface WebhookServerConfig {
  port: number;
  host: string;
}

export class WebhookServer {
  private server: http.Server;
  private config: WebhookServerConfig;

  constructor(config: WebhookServerConfig) {
    this.config = config;
    this.server = http.createServer(this.handleRequest.bind(this));
  }

  private async handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    const url = new URL(req.url || '/', `http://${req.headers.host}`);

    // CORS headers
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    // Handle CORS preflight
    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    try {
      // Health check
      if (url.pathname === '/health' || url.pathname === '/webhook/health') {
        const tracker = getConversationTracker();
        const stats = tracker.getStats();
        const monitor = getResponseMonitor();
        
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          status: 'healthy',
          service: 'matrix-webhook-server',
          timestamp: new Date().toISOString(),
          conversation_tracking: stats,
          active_monitors: monitor?.getActiveMonitorCount() ?? 0
        }));
        return;
      }

      // Conversations monitoring endpoint
      if (url.pathname === '/conversations' && req.method === 'GET') {
        const tracker = getConversationTracker();
        const active = tracker.getActiveConversations();
        const stats = tracker.getStats();
        
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          stats,
          active_conversations: active.map(conv => ({
            matrix_event_id: conv.matrix_event_id,
            matrix_room_id: conv.matrix_room_id,
            agent_id: conv.agent_id,
            status: conv.status,
            runs: conv.runs.length,
            tools_attached: conv.tools_attached,
            created_at: conv.created_at,
            updated_at: conv.updated_at
          }))
        }));
        return;
      }

      // Tool selector webhook endpoint
      if (url.pathname === '/webhook/tool-selector' && req.method === 'POST') {
        await this.handleToolSelectorWebhook(req, res);
        return;
      }

      // Letta agent.run.completed webhook endpoint
      if ((url.pathname === '/webhooks/letta/agent-response' || 
           url.pathname === '/webhook/letta/agent-response') && req.method === 'POST') {
        await this.handleLettaAgentResponseWebhook(req, res);
        return;
      }

      // Start conversation endpoint (called by letta-matrix-client)
      if (url.pathname === '/conversations/start' && req.method === 'POST') {
        await this.handleStartConversation(req, res);
        return;
      }

      // 404 for unknown paths
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not Found', path: url.pathname }));

    } catch (error) {
      console.error('[WebhookServer] Request error:', error);
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        error: 'Internal Server Error',
        message: error instanceof Error ? error.message : 'Unknown error'
      }));
    }
  }

  private async handleToolSelectorWebhook(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    // Parse request body
    const body = await this.readRequestBody(req);
    const payload = JSON.parse(body) as WebhookPayload;
    
    console.log('[WebhookServer] Received tool-selector webhook:', JSON.stringify(payload, null, 2));

    // Validate payload
    if (payload.event !== 'run_triggered') {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ 
        status: 'error', 
        message: 'Unknown event type',
        received: payload.event 
      }));
      return;
    }

    if (!payload.agent_id) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ 
        status: 'error', 
        message: 'Missing agent_id' 
      }));
      return;
    }

    // Process webhook with ConversationTracker
    const tracker = getConversationTracker();
    const result = tracker.handleWebhook(payload);

    if (result.tracked && result.conversation) {
      console.log(`[WebhookServer] Webhook tracked for conversation ${result.conversation.matrix_event_id}`);
      
      // Start monitoring for agent response in the background
      const responseMonitor = getResponseMonitor();
      if (responseMonitor) {
        // Don't await - let it run in the background
        responseMonitor.monitorConversation(result.conversation)
          .then(monitorResult => {
            if (monitorResult.success) {
              console.log(`[WebhookServer] Response successfully posted for ${result.conversation!.matrix_event_id}`);
            } else if (monitorResult.timedOut) {
              console.log(`[WebhookServer] Response monitoring timed out for ${result.conversation!.matrix_event_id}`);
            } else {
              console.log(`[WebhookServer] Response monitoring ended: ${monitorResult.error}`);
            }
          })
          .catch(error => {
            console.error(`[WebhookServer] Response monitoring error:`, error);
          });
      } else {
        console.warn('[WebhookServer] ResponseMonitor not initialized');
      }
      
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'ok',
        tracking: true,
        monitoring: responseMonitor ? true : false,
        conversation_id: result.conversation.matrix_event_id,
        tools_attached: payload.tools_attached?.length || 0
      }));
    } else {
      console.log(`[WebhookServer] Webhook not tracked: ${result.reason}`);
      
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'ok',
        tracking: false,
        reason: result.reason
      }));
    }
  }

  /**
   * Handle request to start tracking a conversation (called by letta-matrix-client)
   */
  private async handleStartConversation(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    const body = await this.readRequestBody(req);
    const payload = JSON.parse(body) as {
      matrix_event_id: string;
      matrix_room_id: string;
      agent_id: string;
      original_query?: string;
    };

    console.log('[WebhookServer] Starting conversation tracking:', JSON.stringify(payload, null, 2));

    if (!payload.matrix_event_id || !payload.matrix_room_id || !payload.agent_id) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'error',
        message: 'Missing required fields: matrix_event_id, matrix_room_id, agent_id'
      }));
      return;
    }

    const tracker = getConversationTracker();
    const conv = tracker.startConversation(
      payload.matrix_event_id,
      payload.matrix_room_id,
      payload.agent_id,
      payload.original_query
    );

    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'ok',
      conversation_id: conv.matrix_event_id,
      agent_id: conv.agent_id,
      tracking: true
    }));
  }

  /**
   * Handle Letta agent.run.completed webhook
   * 
   * This replaces polling-based response detection with push notifications.
   * When Letta completes an agent run, it sends this webhook with the messages.
   */
  private async handleLettaAgentResponseWebhook(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    const handler = getLettaWebhookHandler();
    
    if (!handler) {
      console.error('[WebhookServer] LettaWebhookHandler not initialized');
      res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'error',
        message: 'Letta webhook handler not initialized'
      }));
      return;
    }

    // Read request body
    const body = await this.readRequestBody(req);
    
    // Verify signature
    const signature = req.headers['x-letta-signature'] as string | undefined;
    if (!handler.verifySignature(body, signature)) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'error',
        message: 'Invalid webhook signature'
      }));
      return;
    }

    let payload: LettaRunCompletedPayload;
    try {
      payload = JSON.parse(body);
      const msgCount = payload.data?.messages?.length ?? 0;
      const assistantMsgs = payload.data?.messages?.filter(m => m.message_type === 'assistant_message') ?? [];
      const lastAssistant = assistantMsgs[assistantMsgs.length - 1];
      

      
      let contentPreview = 'none';
      let contentType = 'undefined';
      if (lastAssistant?.content) {
        contentType = Array.isArray(lastAssistant.content) ? 'array' : typeof lastAssistant.content;
        if (typeof lastAssistant.content === 'string') {
          contentPreview = lastAssistant.content.substring(0, 100);
        } else if (Array.isArray(lastAssistant.content)) {
          const textParts = lastAssistant.content
            .filter((p: unknown) => p && typeof p === 'object' && (p as Record<string, unknown>).type === 'text')
            .map((p: unknown) => (p as Record<string, unknown>).text as string)
            .filter(Boolean);
          contentPreview = textParts.length > 0 
            ? textParts.join(' ').substring(0, 100) 
            : `[${lastAssistant.content.length} parts, no text]`;
        } else {
          contentPreview = JSON.stringify(lastAssistant.content).substring(0, 100);
        }
      }
      console.log(`[WebhookServer] Parsed: ${msgCount} msgs, ${assistantMsgs.length} assistant, type=${contentType}, content="${contentPreview}..."`);
    } catch (error) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'error',
        message: 'Invalid JSON payload'
      }));
      return;
    }

    console.log('[WebhookServer] Received Letta webhook:', JSON.stringify({
      event_type: payload.event_type,
      agent_id: payload.agent_id,
      run_id: payload.data?.run_id,
      message_count: payload.data?.messages?.length || 0
    }));

    // Validate event type
    if (payload.event_type !== 'agent.run.completed') {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'error',
        message: 'Unsupported event type',
        received: payload.event_type,
        supported: ['agent.run.completed']
      }));
      return;
    }

    // Handle the webhook
    const result = await handler.handleRunCompleted(payload);

    // Return result
    res.writeHead(result.success ? 200 : 500, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: result.success ? 'ok' : 'error',
      response_posted: result.responsePosted,
      agent_id: result.agentId,
      room_id: result.roomId,
      error: result.error
    }));
  }

  private readRequestBody(req: http.IncomingMessage): Promise<string> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      
      req.on('data', (chunk: Buffer) => {
        chunks.push(chunk);
      });
      
      req.on('end', () => {
        resolve(Buffer.concat(chunks).toString('utf-8'));
      });
      
      req.on('error', reject);
    });
  }

  async start(): Promise<void> {
    return new Promise((resolve) => {
      this.server.listen(this.config.port, this.config.host, () => {
        console.log(`[WebhookServer] Listening on http://${this.config.host}:${this.config.port}`);
        console.log(`[WebhookServer] Endpoints:`);
        console.log(`  - Tool Selector: POST /webhook/tool-selector`);
        console.log(`  - Letta Agent Response: POST /webhooks/letta/agent-response`);
        console.log(`  - Conversations: GET /conversations`);
        console.log(`  - Health: GET /health`);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.server.close((err) => {
        if (err) {
          reject(err);
        } else {
          console.log('[WebhookServer] Stopped');
          resolve();
        }
      });
    });
  }
}

// Singleton
let webhookServerInstance: WebhookServer | undefined;

export function getWebhookServer(): WebhookServer | undefined {
  return webhookServerInstance;
}

export function createWebhookServer(config: WebhookServerConfig): WebhookServer {
  webhookServerInstance = new WebhookServer(config);
  return webhookServerInstance;
}
