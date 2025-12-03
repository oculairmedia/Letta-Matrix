/**
 * HTTP Transport using official MCP SDK StreamableHTTPServerTransport
 */

import http from 'http';
import { Server as MCPServer } from '@modelcontextprotocol/sdk/server/index.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { randomUUID } from 'crypto';

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
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ 
        status: 'healthy',
        service: 'matrix-messaging-mcp',
        timestamp: new Date().toISOString()
      }));
      return;
    }

    // MCP endpoint - delegate to SDK transport
    if (url.pathname === '/mcp') {
      try {
        await this.transport.handleRequest(req, res);
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
}
