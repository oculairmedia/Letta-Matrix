/**
 * HTTP Proxy for Agent Context Injection
 * 
 * This proxy sits in front of the mcp-framework server to:
 * 1. Extract X-Agent-Id header from incoming requests
 * 2. Store session_id -> agent_id mapping for tool handlers
 * 3. Forward requests to mcp-framework on an internal port
 * 
 * Why this is needed:
 * - mcp-framework doesn't expose HTTP headers to tool handlers
 * - Letta sends X-Agent-Id header to identify which agent is calling
 * - We need this to auto-resolve agent Matrix identities
 * 
 * Architecture:
 * - Client sends request to proxy (port 3100) with X-Agent-Id header
 * - Proxy extracts session_id and agent_id, stores mapping
 * - Proxy forwards to mcp-framework (port 3102)
 * - mcp-framework's tool handlers use getSessionAgentId(sessionId)
 */

import http from 'http';
import { 
  extractAgentIdFromHeaders, 
  extractSessionIdFromHeaders,
  setSessionAgentId,
  getActiveSessions
} from './request-context.js';

export interface ProxyConfig {
  /** External port clients connect to */
  externalPort: number;
  /** Internal port where mcp-framework listens */
  internalPort: number;
  host: string;
}

export class HttpAgentProxy {
  private server: http.Server;
  private config: ProxyConfig;
  // Track agent_id for requests before we get session_id back
  private pendingAgentIds: Map<string, string> = new Map();

  constructor(config: ProxyConfig) {
    this.config = config;
    this.server = http.createServer(this.handleRequest.bind(this));
  }

  private async handleRequest(
    req: http.IncomingMessage,
    res: http.ServerResponse
  ): Promise<void> {
    const url = new URL(req.url || '/', `http://${req.headers.host}`);

    // CORS headers for all responses
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept, Mcp-Session-Id, X-Agent-Id');
    res.setHeader('Access-Control-Expose-Headers', 'Mcp-Session-Id');

    // Handle CORS preflight
    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    // Health check endpoint (proxy's own health)
    if (url.pathname === '/health') {
      const sessions = getActiveSessions();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: 'healthy',
        service: 'matrix-mcp-proxy',
        timestamp: new Date().toISOString(),
        upstreamPort: this.config.internalPort,
        activeSessions: sessions.size,
        sessions: Array.from(sessions.entries()).map(([sid, data]) => ({
          sessionId: sid.substring(0, 8) + '...',
          agentId: data.agentId.substring(0, 20) + '...',
          ageSeconds: Math.round((Date.now() - data.timestamp) / 1000)
        }))
      }));
      return;
    }

    // Extract agent ID and existing session ID from headers
    const agentId = extractAgentIdFromHeaders(req.headers as Record<string, string | string[] | undefined>);
    const existingSessionId = extractSessionIdFromHeaders(req.headers as Record<string, string | string[] | undefined>);
    
    if (agentId) {
      console.log(`[HttpProxy] Request from agent: ${agentId} to ${url.pathname}${existingSessionId ? ` (session: ${existingSessionId.substring(0, 8)}...)` : ' (no session yet)'}`);
      
      // If we have an existing session, store the mapping immediately
      if (existingSessionId) {
        setSessionAgentId(existingSessionId, agentId);
      }
    }

    // Proxy the request and capture response headers
    await this.proxyRequestWithSessionCapture(req, res, agentId);
  }

  /**
   * Read the request body
   */
  private readBody(req: http.IncomingMessage): Promise<string> {
    return new Promise((resolve, reject) => {
      const chunks: Buffer[] = [];
      req.on('data', (chunk: Buffer) => chunks.push(chunk));
      req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
      req.on('error', reject);
    });
  }

  /**
   * Inject agent_id into tools/call requests
   * 
   * This modifies the request body to add __injected_agent_id to tool arguments
   * so tool handlers can access the calling agent's identity.
   */
  private injectAgentIdIntoBody(body: string, agentId: string): string {
    try {
      const json = JSON.parse(body);
      
      // Only inject for tools/call method
      if (json.method === 'tools/call' && json.params?.arguments) {
        // Add injected agent_id to arguments
        json.params.arguments.__injected_agent_id = agentId;
        return JSON.stringify(json);
      }
      
      return body;
    } catch (e) {
      // Not valid JSON, return as-is
      return body;
    }
  }

  private async proxyRequestWithSessionCapture(
    clientReq: http.IncomingMessage,
    clientRes: http.ServerResponse,
    agentId: string | undefined
  ): Promise<void> {
    return new Promise(async (resolve) => {
      // Read the request body first
      let body = await this.readBody(clientReq);
      
      // Inject agent_id into tools/call requests
      if (agentId && body) {
        body = this.injectAgentIdIntoBody(body, agentId);
      }

      // Build proxy request options
      const headers = { ...clientReq.headers, host: `localhost:${this.config.internalPort}` };
      // Update content-length since body may have changed
      if (body) {
        headers['content-length'] = Buffer.byteLength(body).toString();
      }
      
      const options: http.RequestOptions = {
        hostname: 'localhost',
        port: this.config.internalPort,
        path: clientReq.url,
        method: clientReq.method,
        headers
      };

      const proxyReq = http.request(options, (proxyRes) => {
        // Capture session ID from response headers (set by mcp-framework for new sessions)
        const newSessionId = proxyRes.headers['mcp-session-id'];
        if (newSessionId && agentId) {
          const sessionId = Array.isArray(newSessionId) ? newSessionId[0] : newSessionId;
          console.log(`[HttpProxy] New session ${sessionId.substring(0, 8)}... mapped to agent ${agentId.substring(0, 20)}...`);
          setSessionAgentId(sessionId, agentId);
        }
        
        // Copy status and headers
        clientRes.writeHead(proxyRes.statusCode || 200, proxyRes.headers);
        
        // Pipe response body
        proxyRes.pipe(clientRes, { end: true });
        proxyRes.on('end', () => resolve());
      });

      proxyReq.on('error', (error) => {
        console.error('[HttpProxy] Upstream connection error:', error.message);
        if (!clientRes.headersSent) {
          clientRes.writeHead(502, { 'Content-Type': 'application/json' });
          clientRes.end(JSON.stringify({
            jsonrpc: '2.0',
            id: null,
            error: {
              code: -32603,
              message: `Upstream error: ${error.message}`
            }
          }));
        }
        resolve();
      });

      // Write body and end request
      if (body) {
        proxyReq.write(body);
      }
      proxyReq.end();
    });
  }

  async start(): Promise<void> {
    return new Promise((resolve) => {
      this.server.listen(this.config.externalPort, this.config.host, () => {
        console.log(`[HttpProxy] Listening on http://${this.config.host}:${this.config.externalPort}`);
        console.log(`[HttpProxy] Forwarding to mcp-framework on port ${this.config.internalPort}`);
        console.log(`[HttpProxy] X-Agent-Id headers will be captured and made available to tools`);
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
          console.log('[HttpProxy] Stopped');
          resolve();
        }
      });
    });
  }
}
