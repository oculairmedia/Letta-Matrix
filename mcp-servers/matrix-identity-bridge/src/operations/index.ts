/**
 * Operations barrel export and router
 */

import { ErrorCode, McpError } from '@modelcontextprotocol/sdk/types.js';
import {
  MatrixOperation,
  MatrixMessagingSchema,
  MatrixMessagingArgs,
  MatrixOperationType,
  OperationContext,
  OperationHandler,
  OperationResult
} from './types.js';

// Import operation modules
import * as message from './message.js';
import * as room from './room.js';
import * as identity from './identity.js';
import * as letta from './letta.js';
import * as opencode from './opencode.js';

// Re-export types
export {
  MatrixOperation,
  MatrixMessagingSchema,
  MatrixMessagingArgs,
  MatrixOperationType,
  OperationContext,
  OperationHandler,
  OperationResult
};

// Operation registry - maps operation names to handlers
const operations: Record<MatrixOperationType, OperationHandler> = {
  // Message operations
  send: message.send,
  read: message.read,
  react: message.react,
  edit: message.edit,
  typing: message.typing,
  subscribe: message.subscribe,
  unsubscribe: message.unsubscribe,

  // Room operations
  room_join: room.room_join,
  room_leave: room.room_leave,
  room_info: room.room_info,
  room_list: room.room_list,
  room_create: room.room_create,
  room_invite: room.room_invite,
  room_search: room.room_search,

  // Identity operations
  identity_create: identity.identity_create,
  identity_get: identity.identity_get,
  identity_list: identity.identity_list,
  identity_derive: identity.identity_derive,

  // Letta operations
  letta_send: letta.letta_send,
  letta_chat: letta.letta_chat,
  letta_lookup: letta.letta_lookup,
  letta_list: letta.letta_list,
  letta_identity: letta.letta_identity,

  // OpenCode operations
  opencode_connect: opencode.opencode_connect,
  opencode_send: opencode.opencode_send,
  opencode_status: opencode.opencode_status
};

/**
 * Route an operation to its handler
 */
export async function handleOperation(
  args: MatrixMessagingArgs,
  ctx: OperationContext
): Promise<OperationResult> {
  const handler = operations[args.operation];
  
  if (!handler) {
    throw new McpError(ErrorCode.InvalidParams, `Unknown operation: ${args.operation}`);
  }

  return handler(args, ctx);
}

/**
 * Get the tool description for the unified tool
 */
export function getToolDescription(): string {
  return `Matrix messaging with 26 operations. Use 'operation' param to select: send, read, react, edit, typing, subscribe, unsubscribe, room_join, room_leave, room_info, room_list, room_create, room_invite, room_search, identity_create, identity_get, identity_list, identity_derive, letta_send, letta_chat, letta_lookup, letta_list, letta_identity, opencode_connect, opencode_send, opencode_status`;
}

/**
 * Get the JSON schema for the unified tool
 */
export function getToolSchema(): Record<string, unknown> {
  return {
    type: 'object',
    properties: {
      operation: {
        type: 'string',
        enum: MatrixOperation.options,
        description: 'The operation to perform'
      },
      identity_id: { type: 'string', description: 'Identity ID' },
      id: { type: 'string', description: 'Unique ID for identity_create' },
      localpart: { type: 'string', description: 'Matrix username' },
      display_name: { type: 'string', description: 'Display name' },
      avatar_url: { type: 'string', description: 'Avatar URL (mxc://)' },
      type: { type: 'string', enum: ['custom', 'letta', 'opencode'], description: 'Identity type' },
      message: { type: 'string', description: 'Message text' },
      to_mxid: { type: 'string', description: 'Target MXID (@user:domain)' },
      msgtype: { type: 'string', description: 'Message type' },
      event_id: { type: 'string', description: 'Event ID' },
      emoji: { type: 'string', description: 'Reaction emoji' },
      new_content: { type: 'string', description: 'New content for edit' },
      room_id: { type: 'string', description: 'Room ID' },
      room_id_or_alias: { type: 'string', description: 'Room ID or alias' },
      name: { type: 'string', description: 'Room name' },
      topic: { type: 'string', description: 'Room topic' },
      is_public: { type: 'boolean', description: 'Public room flag' },
      invite: { type: 'array', items: { type: 'string' }, description: 'MXIDs to invite' },
      user_mxid: { type: 'string', description: 'User MXID for invite' },
      query: { type: 'string', description: 'Search query' },
      limit: { type: 'number', description: 'Result limit' },
      typing: { type: 'boolean', description: 'Typing state' },
      timeout: { type: 'number', description: 'Timeout in ms' },
      rooms: { type: 'array', items: { type: 'string' }, description: 'Room IDs' },
      event_types: { type: 'array', items: { type: 'string' }, description: 'Event types' },
      subscription_id: { type: 'string', description: 'Subscription ID' },
      directory: { type: 'string', description: 'Directory path' },
      session_id: { type: 'string', description: 'Session ID' },
      explicit: { type: 'string', description: 'Explicit identity ID' },
      agent_id: { type: 'string', description: 'Letta agent ID' }
    },
    required: ['operation']
  };
}
