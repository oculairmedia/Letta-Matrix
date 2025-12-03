/**
 * Unified Matrix Messaging Tool
 * 
 * Single tool with 26 operations via the 'operation' parameter.
 */

import { MCPTool } from 'mcp-framework';
import { z } from 'zod';
import { getToolContext, result, requireParam, requireIdentity, requireLetta } from '../core/tool-context.js';
import { IdentityManager } from '../core/identity-manager.js';

// All supported operations
const operations = [
  'send', 'read', 'react', 'edit', 'typing', 'subscribe', 'unsubscribe',
  'room_join', 'room_leave', 'room_info', 'room_list', 'room_create', 'room_invite', 'room_search',
  'identity_create', 'identity_get', 'identity_list', 'identity_derive',
  'letta_send', 'letta_chat', 'letta_lookup', 'letta_list', 'letta_identity',
  'opencode_connect', 'opencode_send', 'opencode_notify', 'opencode_status'
] as const;

const schema = z.object({
  operation: z.enum(operations).describe('The operation to perform'),
  
  // Caller context - OpenCode should pass this automatically
  caller_directory: z.string().optional().describe('Working directory of the calling agent (e.g. /opt/stacks/my-project). OpenCode passes this automatically.'),
  caller_name: z.string().optional().describe('Display name override for the caller (e.g. "OpenCode - My Project")'),
  
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

type Input = z.infer<typeof schema>;

class MatrixMessaging extends MCPTool<typeof schema> {
  name = 'matrix_messaging';
  description = 'Matrix messaging with 27 operations. Use operation param: send, read, react, edit, typing, subscribe, unsubscribe, room_join, room_leave, room_info, room_list, room_create, room_invite, room_search, identity_create, identity_get, identity_list, identity_derive, letta_send, letta_chat, letta_lookup, letta_list, letta_identity, opencode_connect, opencode_send, opencode_notify, opencode_status';
  schema = schema;

  async execute(input: Input): Promise<string> {
    const ctx = getToolContext();

    // Auto-register with OpenCode bridge if caller_directory is provided
    if (input.caller_directory) {
      await this.autoRegisterWithBridge(input.caller_directory, input.room_id);
    }

    switch (input.operation) {
      // === MESSAGE OPERATIONS ===
      case 'send': {
        let identity;
        
        // If caller_directory is provided, use OpenCode identity (auto-create if needed)
        if (input.caller_directory) {
          identity = await ctx.openCodeService.getOrCreateIdentity(input.caller_directory);
          // Update display name if caller_name provided
          if (input.caller_name && identity.displayName !== input.caller_name) {
            // TODO: Could update display name here if needed
          }
        } else if (input.identity_id) {
          identity = requireIdentity(input.identity_id);
        } else {
          throw new Error('Either caller_directory or identity_id is required for send operation');
        }
        
        const message = requireParam(input.message, 'message');
        const client = await ctx.clientPool.getClient(identity);
        
        // Support either room_id (direct) or to_mxid (lookup/create DM)
        let roomId: string;
        if (input.room_id) {
          roomId = input.room_id;
        } else if (input.to_mxid) {
          roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, input.to_mxid);
          await ctx.storage.updateDMActivity(identity.mxid, input.to_mxid);
        } else {
          throw new Error('Either room_id or to_mxid is required for send operation');
        }
        
        // Build message content
        const content: Record<string, unknown> = {
          msgtype: input.msgtype || 'm.text',
          body: message
        };
        
        // Add rich reply if reply_to_event_id is provided
        if (input.reply_to_event_id) {
          console.log(`[MatrixMessaging] Adding rich reply to event: ${input.reply_to_event_id}`);
          content['m.relates_to'] = {
            'm.in_reply_to': {
              event_id: input.reply_to_event_id
            }
          };
        }
        
        console.log(`[MatrixMessaging] Sending content:`, JSON.stringify(content));
        const eventId = await client.sendMessage(roomId, content);
        console.log(`[MatrixMessaging] Sent event: ${eventId}`);
        
        return result({ event_id: eventId, room_id: roomId, from: identity.mxid, identity_id: identity.id });
      }

      case 'read': {
        const identity_id = requireParam(input.identity_id, 'identity_id');
        const room_id = requireParam(input.room_id, 'room_id');
        const messages = await ctx.roomManager.readMessages(identity_id, room_id, input.limit || 50);
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
      }

      case 'react': {
        const identity = requireIdentity(input.identity_id);
        const room_id = requireParam(input.room_id, 'room_id');
        const event_id = requireParam(input.event_id, 'event_id');
        const emoji = requireParam(input.emoji, 'emoji');
        const client = await ctx.clientPool.getClient(identity);
        const reactionEventId = await client.sendEvent(room_id, 'm.reaction', {
          'm.relates_to': { rel_type: 'm.annotation', event_id, key: emoji }
        });
        return result({ reaction_event_id: reactionEventId, room_id, target_event_id: event_id, emoji });
      }

      case 'edit': {
        const identity = requireIdentity(input.identity_id);
        const room_id = requireParam(input.room_id, 'room_id');
        const event_id = requireParam(input.event_id, 'event_id');
        const new_content = requireParam(input.new_content, 'new_content');
        const client = await ctx.clientPool.getClient(identity);
        const editEventId = await client.sendEvent(room_id, 'm.room.message', {
          msgtype: 'm.text',
          body: `* ${new_content}`,
          'm.new_content': { msgtype: 'm.text', body: new_content },
          'm.relates_to': { rel_type: 'm.replace', event_id }
        });
        return result({ edit_event_id: editEventId, room_id, original_event_id: event_id });
      }

      case 'typing': {
        const identity = requireIdentity(input.identity_id);
        const room_id = requireParam(input.room_id, 'room_id');
        const typingState = requireParam(input.typing, 'typing');
        const client = await ctx.clientPool.getClient(identity);
        await client.doRequest(
          'PUT',
          `/_matrix/client/v3/rooms/${encodeURIComponent(room_id)}/typing/${encodeURIComponent(identity.mxid)}`,
          {},
          { typing: typingState, timeout: input.timeout || 30000 }
        );
        return result({ room_id, typing: typingState, timeout: input.timeout || 30000 });
      }

      case 'subscribe': {
        const identity_id = requireParam(input.identity_id, 'identity_id');
        const subscription = await ctx.subscriptionManager.subscribe(identity_id, input.rooms, input.event_types);
        return result({
          subscription_id: subscription.id,
          identity_id: subscription.identityId,
          rooms: subscription.rooms,
          event_types: subscription.eventTypes,
          status: 'active'
        });
      }

      case 'unsubscribe': {
        const subscription_id = requireParam(input.subscription_id, 'subscription_id');
        const deleted = ctx.subscriptionManager.unsubscribe(subscription_id);
        return result({ subscription_id, status: deleted ? 'cancelled' : 'not_found' });
      }

      // === ROOM OPERATIONS ===
      case 'room_join': {
        const identity_id = requireParam(input.identity_id, 'identity_id');
        const room_id_or_alias = requireParam(input.room_id_or_alias, 'room_id_or_alias');
        const roomId = await ctx.roomManager.joinRoom(identity_id, room_id_or_alias);
        return result({ room_id: roomId });
      }

      case 'room_leave': {
        const identity_id = requireParam(input.identity_id, 'identity_id');
        const room_id = requireParam(input.room_id, 'room_id');
        await ctx.roomManager.leaveRoom(identity_id, room_id);
        return result({ room_id });
      }

      case 'room_info': {
        const identity_id = requireParam(input.identity_id, 'identity_id');
        const room_id = requireParam(input.room_id, 'room_id');
        const info = await ctx.roomManager.getRoomInfo(identity_id, room_id);
        return result({ ...info } as Record<string, unknown>);
      }

      case 'room_list': {
        const identity_id = requireParam(input.identity_id, 'identity_id');
        const rooms = await ctx.roomManager.listJoinedRooms(identity_id);
        return result({ rooms, count: rooms.length });
      }

      case 'room_create': {
        const identity = requireIdentity(input.identity_id);
        const name = requireParam(input.name, 'name');
        const client = await ctx.clientPool.getClient(identity);
        const roomId = await client.createRoom({
          name,
          topic: input.topic,
          preset: input.is_public ? 'public_chat' : 'private_chat',
          visibility: input.is_public ? 'public' : 'private',
          invite: input.invite || []
        });
        return result({ room_id: roomId, name, topic: input.topic, is_public: input.is_public || false, invited: input.invite || [] });
      }

      case 'room_invite': {
        const identity = requireIdentity(input.identity_id);
        const room_id = requireParam(input.room_id, 'room_id');
        const user_mxid = requireParam(input.user_mxid, 'user_mxid');
        const client = await ctx.clientPool.getClient(identity);
        await client.inviteUser(user_mxid, room_id);
        return result({ room_id, invited_user: user_mxid });
      }

      case 'room_search': {
        const identity = requireIdentity(input.identity_id);
        const room_id = requireParam(input.room_id, 'room_id');
        const query = requireParam(input.query, 'query');
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
        const limitedResults = results.slice(0, input.limit || 10);
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
      }

      // === IDENTITY OPERATIONS ===
      case 'identity_create': {
        const id = requireParam(input.id, 'id');
        const localpart = requireParam(input.localpart, 'localpart');
        const display_name = requireParam(input.display_name, 'display_name');
        const type = requireParam(input.type, 'type');
        const identity = await ctx.identityManager.getOrCreateIdentity({
          id, localpart, displayName: display_name, avatarUrl: input.avatar_url, type
        });
        return result({ identity: { id: identity.id, mxid: identity.mxid, display_name: identity.displayName, type: identity.type } });
      }

      case 'identity_get': {
        const identity = requireIdentity(input.identity_id);
        return result({
          identity: {
            id: identity.id, mxid: identity.mxid, display_name: identity.displayName,
            avatar_url: identity.avatarUrl, type: identity.type,
            created_at: identity.createdAt, last_used_at: identity.lastUsedAt
          }
        });
      }

      case 'identity_list': {
        const identities = await ctx.identityManager.listIdentities(input.type);
        return result({
          count: identities.length,
          identities: identities.map(i => ({
            id: i.id, mxid: i.mxid, display_name: i.displayName, type: i.type,
            created_at: i.createdAt, last_used_at: i.lastUsedAt
          }))
        });
      }

      case 'identity_derive': {
        let identityId: string;
        let source: string;
        if (input.explicit) {
          identityId = input.explicit;
          source = 'explicit';
        } else if (input.directory) {
          identityId = ctx.openCodeService.deriveIdentityId(input.directory);
          source = 'directory';
        } else if (input.agent_id) {
          identityId = IdentityManager.generateLettaId(input.agent_id);
          source = 'agent_id';
        } else if (input.session_id) {
          identityId = `session_${input.session_id.substring(0, 16)}`;
          source = 'session_id';
        } else {
          throw new Error('Must provide one of: directory, agent_id, session_id, or explicit');
        }
        const identity = ctx.storage.getIdentity(identityId);
        return result({ identity_id: identityId, source, registered: !!identity, mxid: identity?.mxid });
      }

      // === LETTA OPERATIONS ===
      case 'letta_send': {
        const letta = requireLetta();
        const agent_id = requireParam(input.agent_id, 'agent_id');
        const to_mxid = requireParam(input.to_mxid, 'to_mxid');
        const message = requireParam(input.message, 'message');
        const identityId = await letta.getOrCreateAgentIdentity(agent_id);
        const identity = ctx.storage.getIdentity(identityId);
        if (!identity) throw new Error(`Failed to get identity for agent: ${agent_id}`);
        const roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, to_mxid);
        const client = await ctx.clientPool.getClient(identity);
        const eventId = await client.sendMessage(roomId, { msgtype: 'm.text', body: message });
        await ctx.storage.updateDMActivity(identity.mxid, to_mxid);
        return result({ event_id: eventId, room_id: roomId, agent_id, identity_id: identityId, from: identity.mxid, to: to_mxid });
      }

      case 'letta_chat': {
        // Simplified: Just send message to Matrix as OpenCode identity
        // The existing Matrix bridge handles forwarding to Letta and responses
        const agent_id = requireParam(input.agent_id, 'agent_id');
        const message = requireParam(input.message, 'message');
        
        // Get OpenCode identity for the caller
        const callerIdentity = await ctx.openCodeService.getOrCreateIdentity(
          input.caller_directory || '/opt/stacks/default'
        );
        
        // Look up the existing Letta agent chat room from agent_user_mappings.json
        let roomId: string | null = null;
        try {
          const fs = await import('fs');
          const mappingsPath = '/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_user_mappings.json';
          if (fs.existsSync(mappingsPath)) {
            const mappings = JSON.parse(fs.readFileSync(mappingsPath, 'utf-8'));
            const agentMapping = mappings[agent_id];
            if (agentMapping?.room_id) {
              roomId = agentMapping.room_id;
              console.log(`[MatrixMessaging] letta_chat: Using agent room ${roomId} for ${agentMapping.agent_name || agent_id}`);
            }
          }
        } catch (e) {
          console.error('[MatrixMessaging] Failed to read agent mappings:', e);
        }
        
        if (!roomId) {
          throw new Error(`No Matrix room found for agent ${agent_id}. Check agent_user_mappings.json`);
        }
        
        const client = await ctx.clientPool.getClient(callerIdentity);
        
        // Make sure caller has joined the room
        try {
          await client.joinRoom(roomId);
        } catch (e) {
          // May already be joined, ignore
        }
        
        // Send the message to Matrix - bridge will handle forwarding to Letta
        const eventId = await client.sendMessage(roomId, {
          msgtype: 'm.text',
          body: message
        });
        
        return result({ 
          success: true,
          agent_id, 
          room_id: roomId,
          event_id: eventId,
          sender: callerIdentity.mxid,
          message,
          note: 'Message sent to Matrix. Agent will respond via Matrix bridge.'
        });
      }

      case 'letta_lookup': {
        const letta = requireLetta();
        const agent_id = requireParam(input.agent_id, 'agent_id');
        const agent = await letta.getAgent(agent_id);
        if (!agent) throw new Error(`Letta agent not found: ${agent_id}`);
        const identityId = IdentityManager.generateLettaId(agent_id);
        const identity = ctx.storage.getIdentity(identityId);
        return result({
          agent: { id: agent.id, name: agent.name, description: agent.description, model: agent.model },
          matrix_identity: identity ? { identity_id: identityId, mxid: identity.mxid, display_name: identity.displayName } : null,
          has_matrix_identity: !!identity
        });
      }

      case 'letta_list': {
        const letta = requireLetta();
        const agents = await letta.listAgents();
        const agentsWithIdentities = agents.map(agent => {
          const identityId = IdentityManager.generateLettaId(agent.id);
          const identity = ctx.storage.getIdentity(identityId);
          return {
            agent_id: agent.id, name: agent.name, description: agent.description, model: agent.model,
            matrix_identity: identity ? { identity_id: identityId, mxid: identity.mxid } : null
          };
        });
        return result({ count: agents.length, agents: agentsWithIdentities });
      }

      case 'letta_identity': {
        const letta = requireLetta();
        const agent_id = requireParam(input.agent_id, 'agent_id');
        const identityId = await letta.getOrCreateAgentIdentity(agent_id);
        const identity = ctx.storage.getIdentity(identityId);
        if (!identity) throw new Error(`Failed to create identity for agent: ${agent_id}`);
        const agent = await letta.getAgent(agent_id);
        return result({
          agent_id, agent_name: agent?.name,
          identity: { id: identity.id, mxid: identity.mxid, display_name: identity.displayName, type: identity.type, created_at: identity.createdAt }
        });
      }

      // === OPENCODE OPERATIONS ===
      case 'opencode_connect': {
        const directory = requireParam(input.directory, 'directory');
        const session = await ctx.openCodeService.connect(directory, input.display_name, input.session_id);
        return result({
          directory: session.directory, identity_id: session.identityId, mxid: session.mxid,
          display_name: session.displayName, connected_at: session.connectedAt
        });
      }

      case 'opencode_send': {
        const directory = requireParam(input.directory, 'directory');
        const to_mxid = requireParam(input.to_mxid, 'to_mxid');
        const message = requireParam(input.message, 'message');
        const identity = await ctx.openCodeService.getOrCreateIdentity(directory);
        const roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, to_mxid);
        const client = await ctx.clientPool.getClient(identity);
        const eventId = await client.sendMessage(roomId, { msgtype: 'm.text', body: message });
        await ctx.storage.updateDMActivity(identity.mxid, to_mxid);
        return result({ event_id: eventId, room_id: roomId, directory, identity_id: identity.id, from: identity.mxid, to: to_mxid });
      }

      case 'opencode_notify': {
        // Send a message to an OpenCode instance via the bridge
        // Used by Letta agents to explicitly send to OpenCode
        const directory = requireParam(input.directory, 'directory');
        const message = requireParam(input.message, 'message');
        const agentName = input.display_name || 'Letta Agent';
        const sender = input.identity_id || 'unknown';
        
        // Call the bridge's /notify endpoint
        const bridgeUrl = process.env.OPENCODE_BRIDGE_URL || 'http://127.0.0.1:3201';
        const response = await fetch(`${bridgeUrl}/notify`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            directory,
            message,
            sender,
            agentName
          })
        });
        
        const data = await response.json() as { success?: boolean; error?: string; forwarded_to?: string };
        
        if (!response.ok || !data.success) {
          throw new Error(data.error || 'Failed to notify OpenCode');
        }
        
        return result({ 
          success: true, 
          directory, 
          forwarded_to: data.forwarded_to,
          message: message.substring(0, 100) + (message.length > 100 ? '...' : '')
        });
      }

      case 'opencode_status': {
        if (input.directory) {
          const session = ctx.openCodeService.getSession(input.directory);
          const identity = ctx.openCodeService.getIdentity(input.directory);
          return result({
            directory: input.directory,
            connected: !!session,
            has_identity: !!identity,
            session: session ? { identity_id: session.identityId, mxid: session.mxid, connected_at: session.connectedAt, last_activity_at: session.lastActivityAt } : null,
            identity: identity ? { id: identity.id, mxid: identity.mxid, display_name: identity.displayName } : null
          });
        }
        const status = ctx.openCodeService.getStatus();
        return result({
          total_identities: status.totalIdentities,
          active_sessions: status.activeSessions,
          sessions: status.sessions.map(s => ({ directory: s.directory, identity_id: s.identityId, mxid: s.mxid, connected_at: s.connectedAt }))
        });
      }

      default:
        throw new Error(`Unknown operation: ${input.operation}`);
    }
  }

  /**
   * Auto-register the calling OpenCode instance with the bridge.
   * This enables auto-forwarding of messages back to OpenCode.
   */
  private async autoRegisterWithBridge(directory: string, roomId?: string): Promise<void> {
    const bridgeUrl = process.env.OPENCODE_BRIDGE_URL || 'http://127.0.0.1:3201';
    
    try {
      // Try to detect OpenCode's API port from environment or use a discovery mechanism
      // OpenCode sets OPENCODE_API_PORT or we can try to read from a known location
      let port = parseInt(process.env.OPENCODE_API_PORT || '0', 10);
      
      // If no port in env, try to read from OpenCode's runtime file
      if (!port) {
        try {
          const fs = await import('fs');
          const path = await import('path');
          const runtimeFile = path.join(directory, '.opencode', 'runtime.json');
          if (fs.existsSync(runtimeFile)) {
            const runtime = JSON.parse(fs.readFileSync(runtimeFile, 'utf-8'));
            port = runtime.port || runtime.apiPort || 0;
          }
        } catch {
          // Ignore - no runtime file
        }
      }
      
      // Also try reading from a socket file or process list
      if (!port) {
        try {
          const { execSync } = await import('child_process');
          // Find opencode process listening port
          const result = execSync("ss -tlnp | grep opencode | grep -oP ':\\K\\d+' | head -1", { encoding: 'utf-8' }).trim();
          port = parseInt(result, 10) || 0;
        } catch {
          // Ignore - can't find port
        }
      }
      
      if (!port) {
        console.log('[MatrixMessaging] Could not detect OpenCode port for auto-registration');
        return;
      }

      // Build rooms array
      const rooms: string[] = [];
      if (roomId) rooms.push(roomId);

      // Check if already registered with same port
      const checkResponse = await fetch(`${bridgeUrl}/registrations`);
      if (checkResponse.ok) {
        const data = await checkResponse.json() as { registrations: Array<{ directory: string; port: number }> };
        const existing = data.registrations.find(r => r.directory === directory && r.port === port);
        if (existing) {
          // Already registered with same port, skip
          return;
        }
      }

      // Register with bridge
      const response = await fetch(`${bridgeUrl}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          port,
          hostname: '127.0.0.1',
          sessionId: `opencode-${Date.now()}`,
          directory,
          rooms
        })
      });

      if (response.ok) {
        const result = await response.json() as { id: string };
        console.log(`[MatrixMessaging] Auto-registered with bridge: ${result.id}`);
      }
    } catch (error) {
      // Don't fail the operation if registration fails
      console.error('[MatrixMessaging] Auto-registration failed:', error);
    }
  }
}

export default MatrixMessaging;
