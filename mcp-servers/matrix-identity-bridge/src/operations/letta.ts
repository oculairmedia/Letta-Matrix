/**
 * Letta integration operation handlers
 */

import { ErrorCode, McpError } from '@modelcontextprotocol/sdk/types.js';
import { IdentityManager } from '../core/identity-manager.js';
import {
  OperationContext,
  MatrixMessagingArgs,
  OperationHandler,
  result,
  requireParam,
  requireLetta
} from './types.js';

export const letta_send: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');
  const to_mxid = requireParam(args.to_mxid, 'to_mxid');
  const message = requireParam(args.message, 'message');

  const identityId = await letta.getOrCreateAgentIdentity(agent_id);
  const identity = ctx.storage.getIdentity(identityId);
  if (!identity) {
    throw new McpError(ErrorCode.InternalError, `Failed to get identity for agent: ${agent_id}`);
  }

  const roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, to_mxid);
  const client = await ctx.clientPool.getClient(identity);
  const eventId = await client.sendMessage(roomId, { msgtype: 'm.text', body: message });
  await ctx.storage.updateDMActivity(identity.mxid, to_mxid);

  return result({
    event_id: eventId,
    room_id: roomId,
    agent_id,
    identity_id: identityId,
    from: identity.mxid,
    to: to_mxid
  });
};

export const letta_chat: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');
  const message = requireParam(args.message, 'message');

  const response = await letta.sendMessage(agent_id, message);

  return result({ agent_id, input: message, response: response.messages });
};

export const letta_lookup: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');

  const agent = await letta.getAgent(agent_id);
  if (!agent) {
    throw new McpError(ErrorCode.InvalidRequest, `Letta agent not found: ${agent_id}`);
  }

  const identityId = IdentityManager.generateLettaId(agent_id);
  const identity = ctx.storage.getIdentity(identityId);

  return result({
    agent: {
      id: agent.id,
      name: agent.name,
      description: agent.description,
      model: agent.model
    },
    matrix_identity: identity ? {
      identity_id: identityId,
      mxid: identity.mxid,
      display_name: identity.displayName
    } : null,
    has_matrix_identity: !!identity
  });
};

export const letta_list: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agents = await letta.listAgents();

  const agentsWithIdentities = agents.map(agent => {
    const identityId = IdentityManager.generateLettaId(agent.id);
    const identity = ctx.storage.getIdentity(identityId);

    return {
      agent_id: agent.id,
      name: agent.name,
      description: agent.description,
      model: agent.model,
      matrix_identity: identity ? { identity_id: identityId, mxid: identity.mxid } : null
    };
  });

  return result({ count: agents.length, agents: agentsWithIdentities });
};

export const letta_identity: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');

  const identityId = await letta.getOrCreateAgentIdentity(agent_id);
  const identity = ctx.storage.getIdentity(identityId);
  if (!identity) {
    throw new McpError(ErrorCode.InternalError, `Failed to create identity for agent: ${agent_id}`);
  }

  const agent = await letta.getAgent(agent_id);

  return result({
    agent_id,
    agent_name: agent?.name,
    identity: {
      id: identity.id,
      mxid: identity.mxid,
      display_name: identity.displayName,
      type: identity.type,
      created_at: identity.createdAt
    }
  });
};
