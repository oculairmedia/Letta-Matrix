import 'dotenv/config';
/**
 * Matrix Messaging MCP Server
 * 
 * Using mcp-framework for proper HTTP stream transport.
 * Tools are auto-discovered from the dist/tools directory.
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
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Configuration
const config = {
  homeserverUrl: process.env.MATRIX_HOMESERVER_URL || 'https://matrix.oculair.ca',
  adminToken: process.env.MATRIX_ADMIN_TOKEN || '',
  dataDir: process.env.DATA_DIR || './data',
  port: parseInt(process.env.PORT || '3100', 10),
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

  // Determine which transport to use based on environment
  const useStdio = process.env.MCP_TRANSPORT === 'stdio';
  
  // Create MCP server with appropriate transport
  // The basePath tells the framework where to find the tools directory
  const server = new MCPServer({
    name: 'matrix-messaging',
    version: '2.0.0',
    basePath: __dirname,  // Tools will be loaded from __dirname/tools
    transport: useStdio ? {
      type: 'stdio'
    } : {
      type: 'http-stream',
      options: {
        port: config.port,
        endpoint: '/mcp',
        cors: {
          allowOrigin: '*',
          allowMethods: 'GET, POST, DELETE, OPTIONS',
          allowHeaders: 'Content-Type, Authorization, Mcp-Session-Id',
          exposeHeaders: 'Mcp-Session-Id'
        }
      }
    }
  });

  // Start server - tools are auto-discovered from the tools directory
  await server.start();

  if (useStdio) {
    console.log(`[MatrixMCP] Server running on STDIO transport`);
  } else {
    console.log(`[MatrixMCP] Server running on http://0.0.0.0:${config.port}/mcp`);
  }

  // Graceful shutdown
  process.on('SIGINT', async () => {
    console.log('[MatrixMCP] Shutting down...');
    await clientPool.stopAll();
    await server.stop();
    process.exit(0);
  });

  process.on('SIGTERM', async () => {
    console.log('[MatrixMCP] Shutting down...');
    await clientPool.stopAll();
    await server.stop();
    process.exit(0);
  });
}

main().catch((error) => {
  console.error('[MatrixMCP] Fatal error:', error);
  process.exit(1);
});
