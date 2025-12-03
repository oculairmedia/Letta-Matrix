/**
 * Matrix Messaging MCP Server
 * Unified Matrix messaging tool with modular operation handlers
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  McpError
} from '@modelcontextprotocol/sdk/types.js';
import dotenv from 'dotenv';
import { z } from 'zod';

// Core components
import { Storage } from './core/storage.js';
import { IdentityManager } from './core/identity-manager.js';
import { MatrixClientPool } from './core/client-pool.js';
import { RoomManager } from './core/room-manager.js';
import { SubscriptionManager } from './core/subscription-manager.js';
import { HttpTransport } from './http-transport.js';

// Integration services
import { LettaService } from './letta/letta-service.js';
import { OpenCodeService } from './opencode/opencode-service.js';

// Unified operations
import {
  MatrixMessagingSchema,
  OperationContext,
  handleOperation,
  getToolDescription,
  getToolSchema
} from './operations/index.js';

// Load environment variables
dotenv.config();

class MatrixMessagingServer {
  private server: Server;
  private ctx: OperationContext;

  constructor() {
    this.server = new Server(
      { name: 'matrix-messaging-mcp', version: '0.2.0' },
      { capabilities: { tools: {} } }
    );

    // Initialize context with all components
    this.ctx = this.initializeContext();
    this.setupHandlers();
  }

  private initializeContext(): OperationContext {
    const dataDir = process.env.DATA_DIR || './data';
    const homeserverUrl = process.env.MATRIX_HOMESERVER_URL || 'https://matrix.oculair.ca';
    const adminToken = process.env.MATRIX_ADMIN_TOKEN || '';

    if (!adminToken) {
      throw new Error('MATRIX_ADMIN_TOKEN environment variable is required');
    }

    const storage = new Storage(dataDir);
    const identityManager = new IdentityManager(storage, homeserverUrl, adminToken);
    const clientPool = new MatrixClientPool(storage, homeserverUrl);
    const roomManager = new RoomManager(storage, clientPool);
    const subscriptionManager = new SubscriptionManager(storage, clientPool);

    // Initialize Letta service if configured
    let lettaService: LettaService | null = null;
    const lettaUrl = process.env.LETTA_API_URL;
    const lettaToken = process.env.LETTA_API_TOKEN;
    if (lettaUrl) {
      lettaService = new LettaService(
        { baseUrl: lettaUrl, apiKey: lettaToken },
        storage,
        identityManager
      );
      console.log('[Server] Letta integration enabled:', lettaUrl);
    }

    // Initialize OpenCode service
    const openCodeService = new OpenCodeService(storage, identityManager);
    console.log('[Server] OpenCode integration enabled');

    return {
      storage,
      identityManager,
      clientPool,
      roomManager,
      subscriptionManager,
      lettaService,
      openCodeService
    };
  }

  private setupHandlers(): void {
    // List tools - just ONE unified tool
    this.server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: [{
        name: 'matrix_messaging',
        description: getToolDescription(),
        inputSchema: getToolSchema()
      }]
    }));

    // Handle tool calls
    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      if (request.params.name !== 'matrix_messaging') {
        throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${request.params.name}`);
      }

      try {
        const args = MatrixMessagingSchema.parse(request.params.arguments);
        return await handleOperation(args, this.ctx);
      } catch (error) {
        if (error instanceof z.ZodError) {
          throw new McpError(
            ErrorCode.InvalidParams,
            `Invalid parameters: ${error.errors.map(e => `${e.path.join('.')}: ${e.message}`).join(', ')}`
          );
        }
        if (error instanceof McpError) throw error;
        throw new McpError(
          ErrorCode.InternalError,
          `Operation failed: ${error instanceof Error ? error.message : 'Unknown error'}`
        );
      }
    });
  }

  async start(): Promise<void> {
    await this.ctx.storage.initialize();
    await this.ctx.clientPool.initialize();

    console.log('[Server] Matrix Messaging MCP initialized (unified tool)');

    const useHttp = process.env.MCP_TRANSPORT === 'http' || process.env.MCP_SERVER_PORT;

    if (useHttp) {
      const port = parseInt(process.env.MCP_SERVER_PORT || '3100');
      const host = process.env.MCP_SERVER_HOST || '0.0.0.0';
      const httpTransport = new HttpTransport(this.server, { port, host });
      await httpTransport.start();
    } else {
      const transport = new StdioServerTransport();
      await this.server.connect(transport);
      console.log('[Server] Connected via stdio transport');
    }
  }

  async shutdown(): Promise<void> {
    console.log('[Server] Shutting down...');
    await this.ctx.clientPool.stopAll();
    process.exit(0);
  }
}

// Start server
const server = new MatrixMessagingServer();
server.start().catch(console.error);

// Handle shutdown
process.on('SIGINT', () => server.shutdown());
process.on('SIGTERM', () => server.shutdown());
