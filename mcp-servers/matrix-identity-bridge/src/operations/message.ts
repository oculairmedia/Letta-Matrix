/**
 * Message operation handlers
 */

import { ErrorCode, McpError } from '@modelcontextprotocol/sdk/types.js';
import { IdentityManager } from '../core/identity-manager.js';
import { getAgentIdFromContext } from '../core/request-context.js';
import {
  OperationContext,
  MatrixMessagingArgs,
  OperationHandler,
  result,
  requireParam,
  requireIdentity
} from './types.js';

/**
 * Helper to resolve identity - supports both direct identity_id and agent_id auto-derivation
 * 
 * Priority for agent_id resolution:
 * 1. args.agent_id (explicit in tool call)
 * 2. args.__injected_agent_id (injected by proxy from X-Agent-Id header)
 * 3. getAgentIdFromContext() (AsyncLocalStorage, rarely works due to proxy boundary)
 */
async function resolveIdentity(args: MatrixMessagingArgs, ctx: OperationContext) {
  // If identity_id is provided directly, use it
  if (args.identity_id) {
    const identity = ctx.storage.getIdentity(args.identity_id);
    if (!identity) {
      throw new McpError(ErrorCode.InvalidRequest, `Identity not found: ${args.identity_id}`);
    }
    return identity;
  }
  
  // Get agent_id from multiple sources
  let agentId = args.agent_id;
  let source = 'args.agent_id';
  
  // Check for injected agent_id from proxy (from X-Agent-Id header)
  if (!agentId && (args as any).__injected_agent_id) {
    agentId = (args as any).__injected_agent_id;
    source = 'X-Agent-Id header (proxy injected)';
    console.log(`[resolveIdentity] Using agent_id from ${source}: ${agentId}`);
  }
  
  // Fallback to AsyncLocalStorage context (unlikely to work due to proxy architecture)
  if (!agentId) {
    const contextAgentId = getAgentIdFromContext();
    if (contextAgentId) {
      agentId = contextAgentId;
      source = 'AsyncLocalStorage context';
      console.log(`[resolveIdentity] Using agent_id from ${source}: ${agentId}`);
    }
  }
  
  // If we have an agent_id (from args or context), auto-derive the identity
  if (agentId && ctx.lettaService) {
    const identityId = await ctx.lettaService.getOrCreateAgentIdentity(agentId);
    const identity = ctx.storage.getIdentity(identityId);
    if (!identity) {
      throw new McpError(ErrorCode.InternalError, `Failed to get identity for agent: ${agentId}`);
    }
    console.log(`[resolveIdentity] Auto-derived identity ${identityId} from agent_id ${agentId} (source: ${source})`);
    return identity;
  }
  
  throw new McpError(ErrorCode.InvalidParams, 
    'Either identity_id or agent_id is required for send operation. ' +
    'TIP: Letta should automatically send X-Agent-Id header which gets injected into tool args.');
}

export const send: OperationHandler = async (args, ctx) => {
  const identity = await resolveIdentity(args, ctx);
  const message = requireParam(args.message, 'message');

  // Determine target room - either direct room_id or DM room from to_mxid
  let roomId: string;
  let targetInfo: { to?: string; room_id?: string };
  
  if (args.room_id) {
    // Direct room send - use provided room_id
    roomId = args.room_id;
    targetInfo = { room_id: roomId };
    console.log(`[Send] Sending to room directly: ${roomId}`);
  } else if (args.to_mxid) {
    // DM send - get or create DM room
    roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, args.to_mxid);
    targetInfo = { to: args.to_mxid, room_id: roomId };
    await ctx.storage.updateDMActivity(identity.mxid, args.to_mxid);
    console.log(`[Send] Sending DM to ${args.to_mxid} in room ${roomId}`);
  } else {
    throw new McpError(ErrorCode.InvalidParams, 'Either room_id or to_mxid is required for send operation');
  }

  const client = await ctx.clientPool.getClient(identity);
  
  // Build message content
  const content: Record<string, unknown> = {
    msgtype: args.msgtype || 'm.text',
    body: message
  };
  
  // Add rich reply if reply_to_event_id is provided
  if (args.reply_to_event_id) {
    console.log(`[Send] Adding rich reply to event: ${args.reply_to_event_id}`);
    content['m.relates_to'] = {
      'm.in_reply_to': {
        event_id: args.reply_to_event_id
      }
    };
  }
  
  console.log(`[Send] Content being sent:`, JSON.stringify(content));
  const eventId = await client.sendMessage(roomId, content);

  return result({ 
    event_id: eventId, 
    room_id: roomId, 
    from: identity.mxid,
    identity_id: identity.id,
    ...targetInfo 
  });
};

export const read: OperationHandler = async (args, ctx) => {
  const identity_id = requireParam(args.identity_id, 'identity_id');
  const room_id = requireParam(args.room_id, 'room_id');

  const messages = await ctx.roomManager.readMessages(identity_id, room_id, args.limit || 50);

  return result({
    room_id,
    message_count: messages.length,
    messages: messages.map(m => ({
      event_id: m.event_id,
      sender: m.sender,
      timestamp: m.origin_server_ts,
      content: m.content
    }))
  });
};

export const react: OperationHandler = async (args, ctx) => {
  const identity = requireIdentity(ctx, args.identity_id);
  const room_id = requireParam(args.room_id, 'room_id');
  const event_id = requireParam(args.event_id, 'event_id');
  const emoji = requireParam(args.emoji, 'emoji');

  const client = await ctx.clientPool.getClient(identity);
  const reactionEventId = await client.sendEvent(room_id, 'm.reaction', {
    'm.relates_to': {
      rel_type: 'm.annotation',
      event_id,
      key: emoji
    }
  });

  return result({ reaction_event_id: reactionEventId, room_id, target_event_id: event_id, emoji });
};

export const edit: OperationHandler = async (args, ctx) => {
  const identity = requireIdentity(ctx, args.identity_id);
  const room_id = requireParam(args.room_id, 'room_id');
  const event_id = requireParam(args.event_id, 'event_id');
  const new_content = requireParam(args.new_content, 'new_content');

  const client = await ctx.clientPool.getClient(identity);
  const editEventId = await client.sendEvent(room_id, 'm.room.message', {
    msgtype: 'm.text',
    body: `* ${new_content}`,
    'm.new_content': { msgtype: 'm.text', body: new_content },
    'm.relates_to': { rel_type: 'm.replace', event_id }
  });

  return result({ edit_event_id: editEventId, room_id, original_event_id: event_id });
};

export const typing: OperationHandler = async (args, ctx) => {
  const identity = requireIdentity(ctx, args.identity_id);
  const room_id = requireParam(args.room_id, 'room_id');
  const typingState = requireParam(args.typing, 'typing');

  const client = await ctx.clientPool.getClient(identity);
  await client.doRequest(
    'PUT',
    `/_matrix/client/v3/rooms/${encodeURIComponent(room_id)}/typing/${encodeURIComponent(identity.mxid)}`,
    {},
    { typing: typingState, timeout: args.timeout || 30000 }
  );

  return result({ room_id, typing: typingState, timeout: args.timeout || 30000 });
};

export const subscribe: OperationHandler = async (args, ctx) => {
  const identity_id = requireParam(args.identity_id, 'identity_id');

  const subscription = await ctx.subscriptionManager.subscribe(
    identity_id,
    args.rooms,
    args.event_types
  );

  return result({
    subscription_id: subscription.id,
    identity_id: subscription.identityId,
    rooms: subscription.rooms,
    event_types: subscription.eventTypes,
    status: 'active'
  });
};

export const unsubscribe: OperationHandler = async (args, ctx) => {
  const subscription_id = requireParam(args.subscription_id, 'subscription_id');
  const deleted = ctx.subscriptionManager.unsubscribe(subscription_id);

  return result({ subscription_id, status: deleted ? 'cancelled' : 'not_found' });
};
