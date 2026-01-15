/**
 * Identity operation handlers
 */

import { ErrorCode, McpError } from '@modelcontextprotocol/sdk/types.js';
import { IdentityManager } from '../core/identity-manager.js';
import {
  OperationContext,
  MatrixMessagingArgs,
  OperationHandler,
  result,
  requireParam,
  requireIdentity
} from './types.js';

export const identity_create: OperationHandler = async (args, ctx) => {
  const id = requireParam(args.id, 'id');
  const localpart = requireParam(args.localpart, 'localpart');
  const display_name = requireParam(args.display_name, 'display_name');
  const type = requireParam(args.type, 'type');

  const identity = await ctx.identityManager.getOrCreateIdentity({
    id,
    localpart,
    displayName: display_name,
    avatarUrl: args.avatar_url,
    type
  });

  const shouldUpdate =
    (display_name && identity.displayName !== display_name) ||
    (args.avatar_url && identity.avatarUrl !== args.avatar_url);
  if (shouldUpdate) {
    try {
      await ctx.identityManager.updateIdentity(identity.id, display_name, args.avatar_url);
    } catch (error) {
      console.warn('[Identity] Failed to update identity profile:', error);
    }
  }

  return result({
    identity: {
      id: identity.id,
      mxid: identity.mxid,
      display_name: identity.displayName,
      type: identity.type
    }
  });
};

export const identity_get: OperationHandler = async (args, ctx) => {
  const identity = await requireIdentity(ctx, args.identity_id);

  return result({
    identity: {
      id: identity.id,
      mxid: identity.mxid,
      display_name: identity.displayName,
      avatar_url: identity.avatarUrl,
      type: identity.type,
      created_at: identity.createdAt,
      last_used_at: identity.lastUsedAt
    }
  });
};

export const identity_list: OperationHandler = async (args, ctx) => {
  const identities = await ctx.identityManager.listIdentities(args.type);

  return result({
    count: identities.length,
    identities: identities.map(i => ({
      id: i.id,
      mxid: i.mxid,
      display_name: i.displayName,
      type: i.type,
      created_at: i.createdAt,
      last_used_at: i.lastUsedAt
    }))
  });
};

export const identity_derive: OperationHandler = async (args, ctx) => {
  let identityId: string;
  let source: string;

  if (args.explicit) {
    identityId = args.explicit;
    source = 'explicit';
  } else if (args.directory) {
    identityId = ctx.openCodeService.deriveIdentityId(args.directory);
    source = 'directory';
  } else if (args.agent_id) {
    identityId = IdentityManager.generateLettaId(args.agent_id);
    source = 'agent_id';
  } else if (args.session_id) {
    identityId = `session_${args.session_id.substring(0, 16)}`;
    source = 'session_id';
  } else {
    throw new McpError(ErrorCode.InvalidParams, 'Must provide one of: directory, agent_id, session_id, or explicit');
  }

  const identity = await ctx.storage.getIdentityAsync(identityId);

  return result({
    identity_id: identityId,
    source,
    registered: !!identity,
    mxid: identity?.mxid
  });
};
