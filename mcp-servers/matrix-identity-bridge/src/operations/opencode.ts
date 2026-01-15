/**
 * OpenCode integration operation handlers
 */

import {
  OperationContext,
  MatrixMessagingArgs,
  OperationHandler,
  result,
  requireParam
} from './types.js';

export const opencode_connect: OperationHandler = async (args, ctx) => {
  const directory = requireParam(args.directory, 'directory');

  const session = await ctx.openCodeService.connect(directory, args.display_name, args.session_id);

  return result({
    directory: session.directory,
    identity_id: session.identityId,
    mxid: session.mxid,
    display_name: session.displayName,
    connected_at: session.connectedAt
  });
};

export const opencode_send: OperationHandler = async (args, ctx) => {
  const directory = requireParam(args.directory, 'directory');
  const to_mxid = requireParam(args.to_mxid, 'to_mxid');
  const message = requireParam(args.message, 'message');

  const identity = await ctx.openCodeService.getOrCreateIdentity(directory);
  const roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, to_mxid);
  const client = await ctx.clientPool.getClient(identity);
  const eventId = await client.sendMessage(roomId, { msgtype: 'm.text', body: message });
  await ctx.storage.updateDMActivity(identity.mxid, to_mxid);

  return result({
    event_id: eventId,
    room_id: roomId,
    directory,
    identity_id: identity.id,
    from: identity.mxid,
    to: to_mxid
  });
};

export const opencode_status: OperationHandler = async (args, ctx) => {
  if (args.directory) {
    const session = ctx.openCodeService.getSession(args.directory);
    const identity = await ctx.openCodeService.getIdentity(args.directory);

    return result({
      directory: args.directory,
      connected: !!session,
      has_identity: !!identity,
      session: session ? {
        identity_id: session.identityId,
        mxid: session.mxid,
        connected_at: session.connectedAt,
        last_activity_at: session.lastActivityAt
      } : null,
      identity: identity ? {
        id: identity.id,
        mxid: identity.mxid,
        display_name: identity.displayName
      } : null
    });
  }

  const status = await ctx.openCodeService.getStatus();

  return result({
    total_identities: status.totalIdentities,
    active_sessions: status.activeSessions,
    sessions: status.sessions.map(s => ({
      directory: s.directory,
      identity_id: s.identityId,
      mxid: s.mxid,
      connected_at: s.connectedAt
    }))
  });
};
