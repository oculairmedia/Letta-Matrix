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
  const identity = await ctx.storage.getIdentityAsync(identityId);
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

  const response = await letta.sendMessage(agent_id, message, args.conversation_id);

  return result({
    agent_id,
    input: message,
    response: response.messages,
    ...(response.conversation_id && { conversation_id: response.conversation_id })
  });
};

export const letta_lookup: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');

  const agent = await letta.getAgent(agent_id);
  if (!agent) {
    throw new McpError(ErrorCode.InvalidRequest, `Letta agent not found: ${agent_id}`);
  }

  const identityId = IdentityManager.generateLettaId(agent_id);
  const identity = await ctx.storage.getIdentityAsync(identityId);

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
  if (args.limit !== undefined && (!Number.isInteger(args.limit) || args.limit < 1)) {
    throw new McpError(ErrorCode.InvalidParams, 'Invalid limit: must be a positive integer');
  }


  const agents = await letta.listAgents({
    limit: args.limit
  });

  const agentsWithIdentities = await Promise.all(
    agents.map(async agent => {
      const identityId = IdentityManager.generateLettaId(agent.id);
      const identity = await ctx.storage.getIdentityAsync(identityId);

      return {
        agent_id: agent.id,
        name: agent.name,
        description: agent.description,
        model: agent.model,
        matrix_identity: identity ? { identity_id: identityId, mxid: identity.mxid } : null
      };
    })
  );

  return result({ count: agents.length, agents: agentsWithIdentities });
};

export const parallel_dispatch: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const targets = requireParam(args.targets, 'targets');

  if (!Array.isArray(targets) || targets.length === 0) {
    throw new McpError(ErrorCode.InvalidParams, 'targets must be a non-empty array of {agent, message} pairs');
  }

  // Dispatch all messages in parallel
  const results = await Promise.all(
    targets.map(async (target: { agent: string; message: string }) => {
      try {
        // Resolve agent name to ID
        const resolved = await letta.resolveAgentName(target.agent);
        if (!resolved) {
          return { agent: target.agent, success: false, error: `Agent not found: "${target.agent}"` };
        }

        const response = await letta.sendMessage(resolved.agent_id, target.message);
        return {
          agent: target.agent,
          agent_id: resolved.agent_id,
          agent_name: resolved.agent_name,
          success: true,
          response: response.messages
        };
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        return { agent: target.agent, success: false, error: errMsg };
      }
    })
  );

  const succeeded = results.filter(r => r.success).length;
  const failed = results.filter(r => !r.success).length;

  return result({
    total: results.length,
    succeeded,
    failed,
    results
  });
};

export const broadcast: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const message = requireParam(args.message, 'message');
  const agentNames = requireParam(args.agent_names, 'agent_names');

  if (!Array.isArray(agentNames) || agentNames.length === 0) {
    throw new McpError(ErrorCode.InvalidParams, 'agent_names must be a non-empty array of agent names or IDs');
  }

  const results = await Promise.all(
    agentNames.map(async (name: string) => {
      try {
        const resolved = await letta.resolveAgentName(name);
        if (!resolved) {
          return { agent: name, success: false, error: `Agent not found: "${name}"` };
        }

        const response = await letta.sendMessage(resolved.agent_id, message);
        return {
          agent: name,
          agent_id: resolved.agent_id,
          agent_name: resolved.agent_name,
          success: true,
          response: response.messages
        };
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        return { agent: name, success: false, error: errMsg };
      }
    })
  );

  const succeeded = results.filter(r => r.success).length;
  const failed = results.filter(r => !r.success).length;

  return result({
    message,
    total: results.length,
    succeeded,
    failed,
    results
  });
};

export const letta_conversations: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');

  try {
    const client = letta.getClient();
    // List messages for the agent, which will show conversation threads
    const messages = await client.agents.messages.list(agent_id, { limit: args.limit || 100 });

    // Group by conversation_id
    const conversations: Record<string, { message_count: number; last_message?: string; last_timestamp?: string }> = {};

    for await (const msg of messages) {
      const convId = (msg as any).conversation_id || 'default';
      if (!conversations[convId]) {
        conversations[convId] = { message_count: 0 };
      }
      conversations[convId].message_count++;
      if ((msg as any).created_at) {
        conversations[convId].last_timestamp = (msg as any).created_at;
      }
    }

    return result({
      agent_id,
      conversation_count: Object.keys(conversations).length,
      conversations
    });
  } catch (error) {
    const errMsg = error instanceof Error ? error.message : String(error);
    throw new McpError(ErrorCode.InternalError, `Failed to list conversations: ${errMsg}`);
  }
};

export const talk_to_agent: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const message = requireParam(args.message, 'message');

  // Accept agent from multiple input formats
  const agentInput = args.agent || args.agent_name || args.agent_id;
  if (!agentInput) {
    const suggestions = await letta.getSuggestions('', 5);
    throw new McpError(ErrorCode.InvalidParams,
      `Missing agent - specify which agent to talk to.\n\n` +
      `Usage: {operation: "talk_to_agent", agent: "AgentName", message: "Hello!"}\n\n` +
      `Available agents:\n` +
      suggestions.map(s => `  • ${s}`).join('\n')
    );
  }

  // Resolve agent name/id
  const resolved = await letta.resolveAgentName(agentInput);
  if (!resolved) {
    const suggestions = await letta.getSuggestions(agentInput, 3);
    throw new McpError(ErrorCode.InvalidParams,
      `Agent not found: "${agentInput}"\n` +
      (suggestions.length > 0
        ? `Did you mean:\n${suggestions.map(s => `  • ${s}`).join('\n')}\n`
        : '') +
      `Use {operation: "letta_list"} to see all agents.`
    );
  }

  const agent_id = resolved.agent_id;
  const agent_name = resolved.agent_name;

  // Auto-attach matrix_messaging tool to the receiving agent
  await letta.ensureMatrixToolAttached(agent_id);

  // Send message to agent via Letta API
  const response = await letta.sendMessage(agent_id, message, args.conversation_id);

  return result({
    agent_id,
    agent_name,
    match_type: resolved.match_type,
    confidence: resolved.confidence,
    input: message,
    conversation_id: response.conversation_id,
    response: response.messages
  });
};

export const letta_identity: OperationHandler = async (args, ctx) => {
  const letta = requireLetta(ctx);
  const agent_id = requireParam(args.agent_id, 'agent_id');

  const identityId = await letta.getOrCreateAgentIdentity(agent_id);
  const identity = await ctx.storage.getIdentityAsync(identityId);
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
