/**
 * HTTP Transport using official MCP SDK StreamableHTTPServerTransport
 * 
 * Endpoints:
 * - POST /mcp - MCP protocol requests
 * - GET /health - Health check
 * - POST /webhook/tool-selector - Webhook for tool selector notifications
 * - GET /conversations - List active conversations (monitoring)
 */

import http from 'http';
import { Server as MCPServer } from '@modelcontextprotocol/sdk/server/index.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { randomUUID } from 'crypto';
import { getConversationTracker, type WebhookPayload } from './core/conversation-tracker.js';
import { getResponseMonitor } from './core/response-monitor.js';
import { runWithContextAsync, extractAgentIdFromHeaders } from './core/request-context.js';

export interface HttpTransportOptions {
  port: number;
  host: string;
}

export class HttpTransport {
  private httpServer: http.Server;
  private mcpServer: MCPServer;
  private options: HttpTransportOptions;
  private transport: StreamableHTTPServerTransport;

  constructor(mcpServer: MCPServer, options: HttpTransportOptions) {
    this.mcpServer = mcpServer;
    this.options = options;
    
    // Create the official SDK transport with JSON response mode (not SSE)
    this.transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: () => randomUUID(),
      enableJsonResponse: true,  // Use JSON responses instead of SSE
    });

    this.httpServer = http.createServer(this.handleRequest.bind(this));
  }

  private async handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    const url = new URL(req.url || '/', `http://${req.headers.host}`);

    // CORS headers for all responses
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept, Mcp-Session-Id');
    res.setHeader('Access-Control-Expose-Headers', 'Mcp-Session-Id');

    // Handle CORS preflight
    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    // Health check endpoint
    if (url.pathname === '/health') {
      const tracker = getConversationTracker();
      const stats = tracker.getStats();
      
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ 
        status: 'healthy',
        service: 'matrix-messaging-mcp',
        timestamp: new Date().toISOString(),
        conversation_tracking: stats
      }));
      return;
    }

    // Webhook endpoint for tool selector
    if (url.pathname === '/webhook/tool-selector' && req.method === 'POST') {
      await this.handleToolSelectorWebhook(req, res);
      return;
    }

    // Conversation monitoring endpoint
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

    // MCP endpoint - delegate to SDK transport with agent context
    if (url.pathname === '/mcp') {
      try {
        // Extract agent ID from headers (sent by Letta)
        const agentId = extractAgentIdFromHeaders(req.headers as Record<string, string | string[] | undefined>);
        
        if (agentId) {
          console.log(`[HttpTransport] MCP request from agent: ${agentId}`);
        }
        
        // Run the MCP request handler within our context
        // This makes agentId available to tool handlers via getAgentIdFromContext()
        await runWithContextAsync(
          { agentId, requestId: randomUUID() },
          () => this.transport.handleRequest(req, res)
        );
      } catch (error) {
        console.error('[HttpTransport] Error handling MCP request:', error);
        if (!res.headersSent) {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({
            jsonrpc: '2.0',
            id: null,
            error: {
              code: -32603,
              message: error instanceof Error ? error.message : 'Internal error'
            }
          }));
        }
      }
      return;
    }

    // 404 for other paths
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not Found' }));
  }

  async start(): Promise<void> {
    // Connect MCP server to transport
    await this.mcpServer.connect(this.transport);
    
    return new Promise((resolve) => {
      this.httpServer.listen(this.options.port, this.options.host, () => {
        console.log(`[HttpTransport] MCP server listening on http://${this.options.host}:${this.options.port}/mcp`);
        console.log(`[HttpTransport] Health check available at http://${this.options.host}:${this.options.port}/health`);
        console.log(`[HttpTransport] Using official SDK StreamableHTTPServerTransport`);
        resolve();
      });
    });
  }

  async stop(): Promise<void> {
    await this.transport.close();
    return new Promise((resolve, reject) => {
      this.httpServer.close((err) => {
        if (err) {
          reject(err);
        } else {
          console.log('[HttpTransport] Server stopped');
          resolve();
        }
      });
    });
  }

  /**
   * Handle webhook from Tool Selector service
   * 
   * This is called when the tool selector triggers a new run after attaching tools.
   * We use this to track the conversation across runs and monitor for responses.
   */
  private async handleToolSelectorWebhook(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    try {
      // Parse request body
      const body = await this.readRequestBody(req);
      const payload = JSON.parse(body) as WebhookPayload;
      
      console.log('[HttpTransport] Received tool-selector webhook:', JSON.stringify(payload, null, 2));

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
        console.log(`[HttpTransport] Webhook tracked for conversation ${result.conversation.matrix_event_id}`);
        
        // Start monitoring for agent response in the background
        const responseMonitor = getResponseMonitor();
        if (responseMonitor) {
          // Don't await - let it run in the background
          responseMonitor.monitorConversation(result.conversation)
            .then(monitorResult => {
              if (monitorResult.success) {
                console.log(`[HttpTransport] Response successfully posted for ${result.conversation!.matrix_event_id}`);
              } else if (monitorResult.timedOut) {
                console.log(`[HttpTransport] Response monitoring timed out for ${result.conversation!.matrix_event_id}`);
              } else {
                console.log(`[HttpTransport] Response monitoring ended without posting: ${monitorResult.error}`);
              }
            })
            .catch(error => {
              console.error(`[HttpTransport] Response monitoring error:`, error);
            });
        } else {
          console.warn('[HttpTransport] ResponseMonitor not initialized - cannot monitor for response');
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
        console.log(`[HttpTransport] Webhook not tracked: ${result.reason}`);
        
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({
          status: 'ok',
          tracking: false,
          reason: result.reason
        }));
      }

    } catch (error) {
      console.error('[HttpTransport] Error handling tool-selector webhook:', error);
      
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'error',
        message: error instanceof Error ? error.message : 'Internal error'
      }));
    }
  }

  /**
   * Read request body as string
   */
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
}
