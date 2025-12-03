/**
 * Message operation handlers
 */

import {
  OperationContext,
  MatrixMessagingArgs,
  OperationHandler,
  result,
  requireParam,
  requireIdentity
} from './types.js';

export const send: OperationHandler = async (args, ctx) => {
  const identity = requireIdentity(ctx, args.identity_id);
  const to_mxid = requireParam(args.to_mxid, 'to_mxid');
  const message = requireParam(args.message, 'message');

  const roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, to_mxid);
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
  await ctx.storage.updateDMActivity(identity.mxid, to_mxid);

  return result({ event_id: eventId, room_id: roomId, from: identity.mxid, to: to_mxid });
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
