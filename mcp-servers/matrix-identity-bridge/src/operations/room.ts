/**
 * Room operation handlers
 */

import {
  OperationContext,
  MatrixMessagingArgs,
  OperationHandler,
  result,
  requireParam,
  requireIdentity
} from './types.js';

export const room_join: OperationHandler = async (args, ctx) => {
  const identity_id = requireParam(args.identity_id, 'identity_id');
  const room_id_or_alias = requireParam(args.room_id_or_alias, 'room_id_or_alias');

  const roomId = await ctx.roomManager.joinRoom(identity_id, room_id_or_alias);
  return result({ room_id: roomId });
};

export const room_leave: OperationHandler = async (args, ctx) => {
  const identity_id = requireParam(args.identity_id, 'identity_id');
  const room_id = requireParam(args.room_id, 'room_id');

  await ctx.roomManager.leaveRoom(identity_id, room_id);
  return result({ room_id });
};

export const room_info: OperationHandler = async (args, ctx) => {
  const identity_id = requireParam(args.identity_id, 'identity_id');
  const room_id = requireParam(args.room_id, 'room_id');

  const info = await ctx.roomManager.getRoomInfo(identity_id, room_id);
  return result({ ...info } as Record<string, unknown>);
};

export const room_list: OperationHandler = async (args, ctx) => {
  const identity_id = requireParam(args.identity_id, 'identity_id');

  const rooms = await ctx.roomManager.listJoinedRooms(identity_id);
  return result({ rooms, count: rooms.length });
};

export const room_create: OperationHandler = async (args, ctx) => {
  const identity = await requireIdentity(ctx, args.identity_id);
  const name = requireParam(args.name, 'name');

  const client = await ctx.clientPool.getClient(identity);
  const roomId = await client.createRoom({
    name,
    topic: args.topic,
    preset: args.is_public ? 'public_chat' : 'private_chat',
    visibility: args.is_public ? 'public' : 'private',
    invite: args.invite || []
  });

  return result({
    room_id: roomId,
    name,
    topic: args.topic,
    is_public: args.is_public || false,
    invited: args.invite || []
  });
};

export const room_invite: OperationHandler = async (args, ctx) => {
  const identity = await requireIdentity(ctx, args.identity_id);
  const room_id = requireParam(args.room_id, 'room_id');
  const user_mxid = requireParam(args.user_mxid, 'user_mxid');

  const client = await ctx.clientPool.getClient(identity);
  await client.inviteUser(user_mxid, room_id);

  return result({ room_id, invited_user: user_mxid });
};

export const room_search: OperationHandler = async (args, ctx) => {
  const identity = await requireIdentity(ctx, args.identity_id);
  const room_id = requireParam(args.room_id, 'room_id');
  const query = requireParam(args.query, 'query');

  const client = await ctx.clientPool.getClient(identity);
  const searchResult = await client.doRequest('POST', '/_matrix/client/v3/search', {}, {
    search_categories: {
      room_events: {
        search_term: query,
        filter: { rooms: [room_id] },
        order_by: 'recent',
        keys: ['content.body'],
        event_context: { before_limit: 0, after_limit: 0 }
      }
    }
  }) as { search_categories?: { room_events?: { results?: Array<{ result: { event_id: string; sender: string; origin_server_ts: number; content: Record<string, unknown> } }> } } };

  const results = searchResult.search_categories?.room_events?.results || [];
  const limitedResults = results.slice(0, args.limit || 10);

  return result({
    room_id,
    query,
    result_count: limitedResults.length,
    results: limitedResults.map(r => ({
      event_id: r.result.event_id,
      sender: r.result.sender,
      timestamp: r.result.origin_server_ts,
      content: r.result.content
    }))
  });
};
