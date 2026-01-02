import 'dotenv/config';
/**
 * Matrix Messaging MCP Server
 * 
 * Using mcp-framework for proper HTTP stream transport.
 * Tools are auto-discovered from the dist/tools directory.
 * 
 * Architecture:
 * - HTTP Proxy (external port) captures X-Agent-Id headers
 * - mcp-framework (internal port) handles MCP protocol
 * - AsyncLocalStorage bridges context between them
 */

import { MCPServer } from 'mcp-framework';
import { Storage } from './core/storage.js';
import { IdentityManager } from './core/identity-manager.js';
import { MatrixClientPool } from './core/client-pool.js';
import { RoomManager } from './core/room-manager.js';
import { SubscriptionManager } from './core/subscription-manager.js';
import { LettaService } from './letta/letta-service.js';
import { OpenCodeService } from './opencode/opencode-service.js';
import { setToolContext } from './core/tool-context.js';
import { getConversationTracker } from './core/conversation-tracker.js';
import { initializeResponseMonitor } from './core/response-monitor.js';
import { createWebhookServer } from './core/webhook-server.js';
import { initializeLettaWebhookHandler } from './core/letta-webhook-handler.js';
import { HttpAgentProxy } from './core/http-proxy.js';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const config = {
  homeserverUrl: process.env.MATRIX_HOMESERVER_URL || 'https://matrix.oculair.ca',
  adminToken: process.env.MATRIX_ADMIN_TOKEN || '',
  dataDir: process.env.DATA_DIR || './data',
  port: parseInt(process.env.PORT || '3100', 10),  // External port (proxy)
  internalPort: parseInt(process.env.INTERNAL_PORT || '3102', 10),  // Internal mcp-framework port
  webhookPort: parseInt(process.env.WEBHOOK_PORT || '3101', 10),
  lettaApiUrl: process.env.LETTA_API_URL,
  lettaApiKey: process.env.LETTA_API_KEY
};

async function main() {
  console.log('[MatrixMCP] Starting Matrix Messaging MCP Server...');
  console.log(`[MatrixMCP] Homeserver: ${config.homeserverUrl}`);
  console.log(`[MatrixMCP] Port: ${config.port}`);
  console.log(`[MatrixMCP] Base path: ${__dirname}`);

  // Initialize core services
  const storage = new Storage(config.dataDir);
  await storage.initialize();

  const identityManager = new IdentityManager(
    storage,
    config.homeserverUrl,
    config.adminToken
  );

  const clientPool = new MatrixClientPool(
    storage,
    config.homeserverUrl,
    `${config.dataDir}/clients`
  );
  await clientPool.initialize();

  const roomManager = new RoomManager(storage, clientPool);

  const subscriptionManager = new SubscriptionManager(storage, clientPool);

  // Initialize Letta service if configured
  let lettaService: LettaService | null = null;
  if (config.lettaApiUrl) {
    lettaService = new LettaService(
      { baseUrl: config.lettaApiUrl, apiKey: config.lettaApiKey },
      storage,
      identityManager
    );
    console.log('[MatrixMCP] Letta integration enabled');
  }

  // Initialize OpenCode service
  const openCodeService = new OpenCodeService(storage, identityManager, {});

  // Initialize conversation tracker for cross-run tracking
  const conversationTracker = getConversationTracker({
    maxAgeSeconds: parseInt(process.env.CONVERSATION_TIMEOUT_SECONDS || '300', 10),
    cleanupIntervalSeconds: 60
  });
  console.log('[MatrixMCP] Conversation tracker initialized');

  // Initialize response monitor if Letta is configured
  if (lettaService) {
    const responseMonitor = initializeResponseMonitor(
      lettaService.getClient(),
      clientPool,
      storage,
      conversationTracker,
      {
        maxWaitSeconds: parseInt(process.env.MAX_RESPONSE_WAIT || '60', 10),
        pollIntervalSeconds: parseInt(process.env.RESPONSE_POLL_INTERVAL || '2', 10)
      }
    );
    console.log('[MatrixMCP] Response monitor initialized');
  }

  // Determine which transport to use based on environment
  const useStdio = process.env.MCP_TRANSPORT === 'stdio';

  // Start webhook server for cross-run tracking (only in HTTP mode, not stdio)
  let webhookServer: Awaited<ReturnType<typeof createWebhookServer>> | null = null;
  if (!useStdio) {
    webhookServer = createWebhookServer({
      port: config.webhookPort,
      host: '0.0.0.0'
    });
    
    initializeLettaWebhookHandler(clientPool, storage, {
      matrixApiUrl: process.env.MATRIX_API_URL,
      webhookSecret: process.env.LETTA_WEBHOOK_SECRET,
      skipVerification: process.env.LETTA_WEBHOOK_SKIP_VERIFICATION === 'true',
      auditNonMatrixConversations: process.env.AUDIT_NON_MATRIX !== 'false'
    });
    console.log('[MatrixMCP] Letta webhook handler initialized');
    
    await webhookServer.start();
    console.log('[MatrixMCP] Webhook server started on port', config.webhookPort);
  } else {
    console.log('[MatrixMCP] Webhook server disabled in stdio mode');
  }

  // Set global context for tools BEFORE server starts
  setToolContext({
    storage,
    identityManager,
    clientPool,
    roomManager,
    subscriptionManager,
    lettaService,
    openCodeService
  });

  console.log('[MatrixMCP] Tool context initialized');
  
  // Create MCP server with appropriate transport
  // The basePath tells the framework where to find the tools directory
  // For HTTP mode, mcp-framework listens on internal port, proxy on external
  const mcpPort = useStdio ? config.port : config.internalPort;
  
  const server = new MCPServer({
    name: 'matrix-messaging',
    version: '2.0.0',
    basePath: __dirname,  // Tools will be loaded from __dirname/tools
    transport: useStdio ? {
      type: 'stdio'
    } : {
      type: 'http-stream',
      options: {
        port: mcpPort,  // Internal port for mcp-framework
        endpoint: '/mcp',
        cors: {
          allowOrigin: '*',
          allowMethods: 'GET, POST, DELETE, OPTIONS',
          allowHeaders: 'Content-Type, Authorization, Mcp-Session-Id, X-Agent-Id',
          exposeHeaders: 'Mcp-Session-Id'
        }
      }
    }
  });

  // For HTTP mode, start the proxy BEFORE mcp-framework (since server.start() blocks)
  let httpProxy: HttpAgentProxy | null = null;
  if (!useStdio) {
    httpProxy = new HttpAgentProxy({
      externalPort: config.port,  // External port clients connect to
      internalPort: config.internalPort,  // Internal mcp-framework port
      host: '0.0.0.0'
    });
    await httpProxy.start();
    console.log(`[MatrixMCP] Agent context proxy running on http://0.0.0.0:${config.port}`);
    console.log(`[MatrixMCP] mcp-framework will run internally on port ${config.internalPort}`);
  }

  // Start mcp-framework server (this blocks until shutdown for HTTP mode)
  // Don't await - let it run in the background and use its built-in signal handlers
  const serverPromise = server.start();
  
  // Wait a bit for the server to actually start listening
  await new Promise(resolve => setTimeout(resolve, 100));

  if (useStdio) {
    console.log(`[MatrixMCP] Server running on STDIO transport`);
    // For stdio, we need to wait for the server
    await serverPromise;
  } else {
    console.log(`[MatrixMCP] External endpoint: http://0.0.0.0:${config.port}/mcp`);
    // For HTTP, server runs in background, we continue to set up shutdown handlers
  }

  // Graceful shutdown
  const shutdown = async () => {
    console.log('[MatrixMCP] Shutting down...');
    conversationTracker.stop();
    if (webhookServer) {
      await webhookServer.stop();
    }
    if (httpProxy) {
      await httpProxy.stop();
    }
    await clientPool.stopAll();
    await server.stop();
    process.exit(0);
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

main().catch((error) => {
  console.error('[MatrixMCP] Fatal error:', error);
  process.exit(1);
});
