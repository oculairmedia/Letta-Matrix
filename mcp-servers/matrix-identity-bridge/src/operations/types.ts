/**
 * Shared types for operation handlers
 */

import { z } from 'zod';
import { Storage } from '../core/storage.js';
import { IdentityManager } from '../core/identity-manager.js';
import { MatrixClientPool } from '../core/client-pool.js';
import { RoomManager } from '../core/room-manager.js';
import { SubscriptionManager } from '../core/subscription-manager.js';
import { LettaService } from '../letta/letta-service.js';
import { OpenCodeService } from '../opencode/opencode-service.js';
import { ErrorCode, McpError } from '@modelcontextprotocol/sdk/types.js';

// All supported operations
export const MatrixOperation = z.enum([
  // Message operations
  'send', 'read', 'react', 'edit', 'typing', 'subscribe', 'unsubscribe',
  // Room operations
  'room_join', 'room_leave', 'room_info', 'room_list', 'room_create', 'room_invite', 'room_search',
  // Identity operations
  'identity_create', 'identity_get', 'identity_list', 'identity_derive',
  // Letta integration
  'letta_send', 'letta_chat', 'letta_lookup', 'letta_list', 'letta_identity',
  // OpenCode integration
  'opencode_connect', 'opencode_send', 'opencode_status'
]);

export type MatrixOperationType = z.infer<typeof MatrixOperation>;

// Unified input schema
export const MatrixMessagingSchema = z.object({
  operation: MatrixOperation.describe('The operation to perform'),
  
  // Identity parameters
  identity_id: z.string().optional().describe('Identity ID for the operation'),
  id: z.string().optional().describe('Unique ID (for identity_create)'),
  localpart: z.string().optional().describe('Matrix username without @domain'),
  display_name: z.string().optional().describe('Display name'),
  avatar_url: z.string().optional().describe('Avatar URL (mxc://)'),
  type: z.enum(['custom', 'letta', 'opencode']).optional().describe('Identity type'),
  
  // Message parameters
  message: z.string().optional().describe('Message text'),
  to_mxid: z.string().optional().describe('Target user MXID (@user:domain)'),
  msgtype: z.string().optional().describe('Message type (default: m.text)'),
  event_id: z.string().optional().describe('Event ID for reactions/edits'),
  reply_to_event_id: z.string().optional().describe('Event ID to reply to (creates threaded reply)'),
  emoji: z.string().optional().describe('Reaction emoji'),
  new_content: z.string().optional().describe('New content for edits'),
  
  // Room parameters
  room_id: z.string().optional().describe('Room ID'),
  room_id_or_alias: z.string().optional().describe('Room ID or alias'),
  name: z.string().optional().describe('Room name'),
  topic: z.string().optional().describe('Room topic'),
  is_public: z.boolean().optional().describe('Whether room is public'),
  invite: z.array(z.string()).optional().describe('MXIDs to invite'),
  user_mxid: z.string().optional().describe('User MXID for invites'),
  query: z.string().optional().describe('Search query'),
  limit: z.number().optional().describe('Result limit'),
  
  // Typing parameters
  typing: z.boolean().optional().describe('Typing indicator state'),
  timeout: z.number().optional().describe('Typing timeout in ms'),
  
  // Subscription parameters
  rooms: z.array(z.string()).optional().describe('Room IDs for subscription'),
  event_types: z.array(z.string()).optional().describe('Event types to filter'),
  subscription_id: z.string().optional().describe('Subscription ID'),
  
  // Identity derivation
  directory: z.string().optional().describe('Directory path'),
  session_id: z.string().optional().describe('Session ID'),
  explicit: z.string().optional().describe('Explicit identity ID'),
  
  // Letta parameters
  agent_id: z.string().optional().describe('Letta agent ID')
});

export type MatrixMessagingArgs = z.infer<typeof MatrixMessagingSchema>;

// Context passed to all operation handlers
export interface OperationContext {
  storage: Storage;
  identityManager: IdentityManager;
  clientPool: MatrixClientPool;
  roomManager: RoomManager;
  subscriptionManager: SubscriptionManager;
  lettaService: LettaService | null;
  openCodeService: OpenCodeService;
}

// Standard result type - must match MCP SDK CallToolResult
export interface OperationResult {
  content: Array<{ type: 'text'; text: string }>;
  [key: string]: unknown;  // Index signature for MCP SDK compatibility
}

// Operation handler type
export type OperationHandler = (
  args: MatrixMessagingArgs,
  ctx: OperationContext
) => Promise<OperationResult>;

// Helper to create successful result
export function result(data: Record<string, unknown>): OperationResult {
  return {
    content: [{
      type: 'text' as const,
      text: JSON.stringify({ success: true, ...data }, null, 2)
    }]
  };
}

// Helper to require a parameter
export function requireParam<T>(value: T | undefined, name: string): T {
  if (value === undefined || value === null) {
    throw new McpError(ErrorCode.InvalidParams, `Missing required parameter: ${name}`);
  }
  return value;
}

// Helper to require an identity
export async function requireIdentity(ctx: OperationContext, identity_id: string | undefined) {
  const id = requireParam(identity_id, 'identity_id');
  const identity = await ctx.storage.getIdentityAsync(id);
  if (!identity) {
    throw new McpError(ErrorCode.InvalidRequest, `Identity not found: ${id}`);
  }
  return identity;
}

// Helper to require Letta service
export function requireLetta(ctx: OperationContext): LettaService {
  if (!ctx.lettaService) {
    throw new McpError(ErrorCode.InvalidRequest, 'Letta integration not configured');
  }
  return ctx.lettaService;
}
