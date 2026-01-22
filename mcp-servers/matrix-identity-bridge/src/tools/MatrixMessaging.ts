/**
 * Unified Matrix Messaging Tool
 * 
 * Single tool with 26 operations via the 'operation' parameter.
 */

import { z } from 'zod';
import type { InferSchema, ToolMetadata } from 'xmcp';
import { headers } from 'xmcp/dist/runtime/headers.js';
import { initializeServices } from '../core/services';
import { getToolContext, result, requireParam, requireIdentity, requireLetta, ToolContext } from '../core/tool-context';
import { IdentityManager } from '../core/identity-manager';
import { getCallerContext, resolveCallerIdentity, resolveCallerIdentityId, type CallerContext } from '../core/caller-context';
import { getOrCreateAgentRoom } from '../core/agent-rooms';
import { autoRegisterWithBridge } from '../core/opencode-bridge';
import { getAdminToken, getAdminConfig } from '../core/admin-auth.js';

// All supported operations
const operations = [
  'send', 'read', 'react', 'edit', 'typing', 'subscribe', 'unsubscribe',
  'room_join', 'room_leave', 'room_info', 'room_list', 'room_create', 'room_invite', 'room_search',
  'room_find', 'room_members',
  'identity_create', 'identity_get', 'identity_list', 'identity_derive',
  'letta_send', 'letta_chat', 'letta_lookup', 'letta_list', 'letta_identity',
  'talk_to_agent',
  'opencode_connect', 'opencode_send', 'opencode_notify', 'opencode_status'
] as const;


export const schema = {
  operation: z.enum(operations).describe(
    'The operation to perform. Common operations: ' +
    'send (send message to user/room), ' +
    'letta_chat (chat with Letta agent), ' +
    'read (read room messages), ' +
    'room_list (list joined rooms)'
  ),
  
  // === CALLER CONTEXT (REQUIRED for remote MCP) ===
  caller_directory: z.string().optional().describe(
    'Working directory path for OpenCode operations. Example: /opt/stacks/my-project'
  ),
  caller_name: z.string().optional().describe(
    'Display name override. Example: "OpenCode - MyProject"'
  ),
  caller_source: z.enum(['opencode', 'claude-code']).optional().describe(
    'Explicit caller source. REQUIRED for remote MCP to identify correct identity type.'
  ),
  
  // === IDENTITY PARAMETERS ===
  identity_id: z.string().optional().describe(
    'Identity ID for operations requiring an identity. Use identity_list to find available IDs.'
  ),
  sender_identity_id: z.string().optional().describe(
    'Explicit sender identity ID for talk_to_agent. Overrides caller/OpenCode identity.'
  ),
  id: z.string().optional().describe('Unique ID when creating a new identity'),
  localpart: z.string().optional().describe(
    'Matrix username (without @domain). Example: "mybot" becomes @mybot:matrix.example.com'
  ),
  display_name: z.string().optional().describe('Human-readable display name. Example: "My Assistant Bot"'),
  avatar_url: z.string().optional().describe('Avatar image URL in mxc:// format'),
  type: z.enum(['custom', 'letta', 'opencode']).optional().describe('Identity type: custom, letta, or opencode'),
  
  // === MESSAGE PARAMETERS ===
  message: z.string().optional().describe('The message text to send'),
  to_mxid: z.string().optional().describe(
    'Target user Matrix ID. Format: @username:domain. Example: @meridian:matrix.oculair.ca'
  ),
  msgtype: z.string().optional().describe('Message type: m.text (default), m.notice, m.emote'),
  event_id: z.string().optional().describe(
    'Event ID for reactions/edits. Format: $eventId. Get from message read results.'
  ),
  reply_to_event_id: z.string().optional().describe(
    'Event ID to reply to (creates threaded reply). Format: $eventId'
  ),
  emoji: z.string().optional().describe('Reaction emoji. Example: "👍" or "✅"'),
  new_content: z.string().optional().describe('New message content when editing'),
  
  // === ROOM PARAMETERS ===
  room_id: z.string().optional().describe(
    'Room ID. Format: !roomId:domain. Example: !abc123:matrix.oculair.ca'
  ),
  room_id_or_alias: z.string().optional().describe(
    'Room ID or alias. Alias format: #roomname:domain'
  ),
  name: z.string().optional().describe('Room name when creating a room'),
  topic: z.string().optional().describe('Room topic/description'),
  is_public: z.boolean().optional().describe('true for public room, false for private (default)'),
  invite: z.array(z.string()).optional().describe('List of user MXIDs to invite when creating room'),
  user_mxid: z.string().optional().describe('User MXID to invite. Format: @user:domain'),
  query: z.string().optional().describe('Search query text for room_search or room_find'),
  limit: z.number().optional().describe('Max results to return (default: 50 for read, 10 for search)'),
  scope: z.enum(['joined', 'server']).optional().describe('Room scope for room_list: joined (identity rooms) or server (all admin rooms)'),
  
  // === TYPING INDICATOR ===
  typing: z.boolean().optional().describe('true to show typing, false to stop'),
  timeout: z.number().optional().describe('Typing indicator timeout in milliseconds (default: 30000)'),
  
  // === SUBSCRIPTION PARAMETERS ===
  rooms: z.array(z.string()).optional().describe('List of room IDs to subscribe to'),
  event_types: z.array(z.string()).optional().describe('Event types to filter. Example: ["m.room.message"]'),
  subscription_id: z.string().optional().describe('Subscription ID for unsubscribe'),
  
  // === OPENCODE/DIRECTORY PARAMETERS ===
  directory: z.string().optional().describe(
    'Working directory path for OpenCode operations. Example: /opt/stacks/my-project'
  ),
  session_id: z.string().optional().describe('Session ID for identity derivation'),
  explicit: z.string().optional().describe('Explicit identity ID to use'),
  
  // === LETTA AGENT PARAMETERS ===
  agent_id: z.string().optional().describe(
    'Letta agent ID (UUID). Use letta_list to find available agents.'
  ),
  agent_name: z.string().optional().describe(
    'Agent name (e.g., "Meridian", "BMO"). Supports fuzzy matching - will find closest match.'
  ),
  agent: z.string().optional().describe(
    'Agent name OR ID - the simplest way to specify an agent. Examples: "Meridian", "BMO", or a full UUID.'
  ),
  
  // === INTERNAL: Injected by proxy from X-Agent-Id header ===
  // This is NOT set by users - it's automatically injected by the HTTP proxy
  // when Letta calls this MCP server with the X-Agent-Id header
  __injected_agent_id: z.string().optional().describe(
    'Internal: Agent ID injected by proxy from X-Agent-Id header. DO NOT SET MANUALLY.'
  )
};

export type Input = InferSchema<typeof schema>;

export const metadata: ToolMetadata = {
  name: 'matrix_messaging',
  description: `Matrix messaging tool - talk to AI agents and send messages.

═══════════════════════════════════════════════════════════════════════════════
★ REQUIRED: ALWAYS INCLUDE caller_directory ★
═══════════════════════════════════════════════════════════════════════════════

You MUST include caller_directory (your working directory) in EVERY call.
This identifies YOU to the agent so they can respond back to you.

▶ CORRECT USAGE:
  {
    operation: "talk_to_agent",
    agent: "Meridian",
    message: "Hello!",
    caller_directory: "/opt/stacks/my-project"  ← REQUIRED!
  }

▶ WITHOUT caller_directory: Agent cannot route response back to you!

═══════════════════════════════════════════════════════════════════════════════
★ TALKING TO AGENTS ★
═══════════════════════════════════════════════════════════════════════════════

▶ USE talk_to_agent (RECOMMENDED - supports names!):
  {operation: "talk_to_agent", agent: "Meridian", message: "Hello!", caller_directory: "/your/path"}
  {operation: "talk_to_agent", agent: "BMO", message: "What's up?", caller_directory: "/your/path"}
  
  • Just use the agent's NAME - no need to look up UUIDs!
  • Supports fuzzy matching: "meridian", "MERIDIAN", "Merid" all work
  • Also accepts agent_id if you have it

▶ COMMON AGENTS:
  • Meridian - Companion agent (opus-4-5)
  • BMO - Personal assistant (claude-sonnet-4)
  • GraphitiExplorer - Knowledge graph agent

═══════════════════════════════════════════════════════════════════════════════
ALTERNATIVE METHODS
═══════════════════════════════════════════════════════════════════════════════

▶ letta_chat (if you have the agent_id):
  {operation: "letta_chat", agent_id: "agent-uuid", message: "Hello!"}
  {operation: "letta_chat", agent_name: "Meridian", message: "Hello!"} ← also works!

▶ letta_list (to see all agents):
  {operation: "letta_list"}
  Returns all agents with their agent_id, name, and room info

▶ send (to a specific room):
  {operation: "send", room_id: "!roomId:matrix.oculair.ca", message: "Hello!"}

═══════════════════════════════════════════════════════════════════════════════
ALL OPERATIONS
═══════════════════════════════════════════════════════════════════════════════

LETTA AGENTS (PRIMARY):
  • talk_to_agent ★ - Easiest! Just needs agent name + message
  • letta_chat     - Send to agent's room (accepts agent_id OR agent_name)
  • letta_list     - List all agents with their rooms
  • letta_lookup   - Get agent details

OTHER OPERATIONS:
  • Messaging: send, read, react, edit, typing
  • Rooms: room_list, room_info, room_join, room_leave, room_create, room_invite
  • Identity: identity_list, identity_get, identity_create
  • OpenCode: opencode_connect, opencode_send, opencode_notify, opencode_status

═══════════════════════════════════════════════════════════════════════════════
HOW IT WORKS
═══════════════════════════════════════════════════════════════════════════════

Messages go to the AGENT'S ROOM (not DMs):
  1. You send: {operation: "talk_to_agent", agent: "Meridian", message: "Hi"}
  2. Message appears in Meridian's Matrix room
  3. Matrix bridge forwards to Letta
  4. Agent responds in the same room
  5. Response visible in Matrix clients (Element, etc.)

═══════════════════════════════════════════════════════════════════════════════
TIPS
═══════════════════════════════════════════════════════════════════════════════

• Use agent NAMES: "Meridian" not "agent-597b5756-..."
• Fuzzy matching: "meridian", "MERIDIAN", "Merid" all work
• Responses are async - agent replies appear in Matrix`,
};

const getHeaders = () => {
  const requestHeaders = headers();
  return requestHeaders || {};
};

const getInjectedAgentId = (): string | undefined => {
  const requestHeaders = getHeaders();
  const raw = requestHeaders["x-agent-id"];
  if (Array.isArray(raw)) {
    return raw[0];
  }
  return raw;
};

const executeOperation = async (input: Input, ctx: ToolContext, callerContext: CallerContext): Promise<string> => {
  const callerDirectory = callerContext.directory;
  const callerName = callerContext.name;
  // Only use claude-code when EXPLICITLY requested. Telemetry fallback (source='claude-code') 
  // should NOT trigger Claude Code identity - that's for actual Claude Code sessions.
  const effectiveSource = callerContext.sourceOverride === 'claude-code' ? 'claude-code' : 
    (callerContext.source === 'claude-code' ? 'opencode' : callerContext.source);

  switch (input.operation) {
      // === MESSAGE OPERATIONS ===
      case 'send': {
        let identity;
        
        // Get agent_id from multiple sources:
        // 1. Explicit agent_id in input (user specified)
        // 2. __injected_agent_id (proxy injected from X-Agent-Id header)
        const agentId = input.agent_id || input.__injected_agent_id || getInjectedAgentId();
        
        // Priority order for identity resolution:
        // 1. Explicit identity_id (highest priority)
        // 2. agent_id - either explicit or injected from header (derives identity)
        // 3. callerDirectory / OpenCode identity (fallback for OpenCode callers)
        if (input.identity_id) {
          identity = await requireIdentity(input.identity_id);
          console.log(`[MatrixMessaging] send: Using explicit identity_id ${input.identity_id}`);
        } else if (agentId && ctx.lettaService) {
          const source = input.agent_id ? 'explicit' : 'X-Agent-Id header';
          console.log(`[MatrixMessaging] send: Resolving identity from agent_id (${source}): ${agentId}`);
          const identityId = await ctx.lettaService.getOrCreateAgentIdentity(agentId);
          identity = await ctx.storage.getIdentityAsync(identityId);
          if (!identity) {
            throw new Error(`Failed to get identity for agent: ${agentId}`);
          }
          console.log(`[MatrixMessaging] send: Auto-derived identity ${identityId} from agent_id ${agentId}`);
        } else {
          identity = await resolveCallerIdentity(
            ctx,
            callerDirectory,
            callerName,
            effectiveSource
          );
          if (effectiveSource === 'claude-code') {
            console.log(`[MatrixMessaging] send: Using Claude Code identity for ${callerDirectory}`);
          } else {
            console.log(`[MatrixMessaging] send: Using OpenCode identity for ${callerDirectory}`);
          }
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
          throw new Error(
            `Missing message destination - specify where to send the message.\n\n` +
            `OPTION 1: Use to_mxid for DMs (auto-creates room)\n` +
            `  Send to a user: {operation: "send", to_mxid: "@username:matrix.oculair.ca", message: "Hi"}\n` +
            `  Common users: @meridian:matrix.oculair.ca, @oculair:matrix.oculair.ca\n\n` +
            `OPTION 2: Use room_id for existing rooms\n` +
            `  First find rooms: {operation: "room_list", identity_id: "${identity.id}"}\n` +
            `  Then send: {operation: "send", room_id: "!roomId:domain", message: "Hi"}`
          );
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
        // Use doRequest directly to bypass SDK membership checks (avoids sync race conditions)
        const txnId = `m${Date.now()}`;
        const sendResult = await client.doRequest(
          'PUT',
          `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/send/m.room.message/${txnId}`,
          {},
          content
        ) as { event_id: string };
        const eventId = sendResult.event_id;
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
        const identity = await requireIdentity(input.identity_id);
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
        const identity = await requireIdentity(input.identity_id);
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
        const identity = await requireIdentity(input.identity_id);
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
        const room_id_or_alias = requireParam(input.room_id_or_alias, 'room_id_or_alias');
        const resolvedIdentityId = await resolveCallerIdentityId(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
        const roomId = await ctx.roomManager.joinRoom(resolvedIdentityId, room_id_or_alias);
        return result({ room_id: roomId });
      }

      case 'room_leave': {
        const room_id = requireParam(input.room_id, 'room_id');
        const resolvedIdentityId = await resolveCallerIdentityId(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
        await ctx.roomManager.leaveRoom(resolvedIdentityId, room_id);
        return result({ room_id });
      }

      case 'room_info': {
        const room_id = requireParam(input.room_id, 'room_id');
        const resolvedIdentityId = await resolveCallerIdentityId(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
        const info = await ctx.roomManager.getRoomInfo(resolvedIdentityId, room_id);
        return result({ ...info } as Record<string, unknown>);
      }

      case 'room_list': {
        const scope = input.scope || 'joined';
        
        if (scope === 'server') {
          const adminToken = await getAdminToken();
          const { homeserverUrl } = getAdminConfig();
          const rooms = await ctx.roomManager.listServerRooms(adminToken, homeserverUrl);
          return result({ 
            rooms: rooms.map(r => ({ room_id: r.roomId, name: r.name, topic: r.topic, alias: r.canonicalAlias })),
            count: rooms.length,
            scope: 'server'
          });
        }
        
        const resolvedIdentityId = await resolveCallerIdentityId(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
        const rooms = await ctx.roomManager.listJoinedRooms(resolvedIdentityId);
        return result({ rooms, count: rooms.length, scope: 'joined' });
      }

      case 'room_create': {
        const identity = await resolveCallerIdentity(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
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
        const identity = await resolveCallerIdentity(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
        const room_id = requireParam(input.room_id, 'room_id');
        const user_mxid = requireParam(input.user_mxid, 'user_mxid');
        const client = await ctx.clientPool.getClient(identity);
        await client.inviteUser(user_mxid, room_id);
        return result({ room_id, invited_user: user_mxid });
      }

      case 'room_search': {
        const identity = await resolveCallerIdentity(
          ctx,
          callerDirectory,
          callerName,
          effectiveSource,
          input.identity_id
        );
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

      case 'room_find': {
        const query = requireParam(input.query, 'query');
        const adminToken = await getAdminToken();
        const { homeserverUrl } = getAdminConfig();
        const rooms = await ctx.roomManager.findRoomsByName(query, adminToken, homeserverUrl);
        const limited = rooms.slice(0, input.limit || 20);
        
        return result({
          query,
          rooms: limited.map(r => ({ room_id: r.roomId, name: r.name, topic: r.topic, alias: r.canonicalAlias })),
          count: limited.length,
          total: rooms.length
        });
      }

      case 'room_members': {
        const room_id = requireParam(input.room_id, 'room_id');
        const adminToken = await getAdminToken();
        const { homeserverUrl } = getAdminConfig();
        const members = await ctx.roomManager.getRoomMembersAdmin(room_id, adminToken, homeserverUrl);
        
        return result({
          room_id,
          members,
          count: members.length
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

        const shouldUpdate =
          (display_name && identity.displayName !== display_name) ||
          (input.avatar_url && identity.avatarUrl !== input.avatar_url);
        if (shouldUpdate) {
          try {
            await ctx.identityManager.updateIdentity(identity.id, display_name, input.avatar_url);
          } catch (error) {
            console.warn('[MatrixMessaging] Failed to update identity profile:', error);
          }
        }

        return result({ identity: { id: identity.id, mxid: identity.mxid, display_name: identity.displayName, type: identity.type } });
      }

      case 'identity_get': {
        const identity = await requireIdentity(input.identity_id);
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
          throw new Error(
            `Missing input - need something to derive identity from.\n\n` +
            `OPTIONS:\n` +
            `• directory: Working directory path → {operation: "identity_derive", directory: "/opt/stacks/my-project"}\n` +
            `• agent_id: Letta agent UUID → {operation: "identity_derive", agent_id: "uuid"}\n` +
            `• session_id: Session identifier → {operation: "identity_derive", session_id: "session-123"}\n` +
            `• explicit: Known identity ID → {operation: "identity_derive", explicit: "my-identity-id"}`
          );
        }
        const identity = await ctx.storage.getIdentityAsync(identityId);
        return result({ identity_id: identityId, source, registered: !!identity, mxid: identity?.mxid });
      }

      // === LETTA OPERATIONS ===
      case 'letta_send': {
        const letta = requireLetta();
        const agent_id = requireParam(input.agent_id, 'agent_id');
        const to_mxid = requireParam(input.to_mxid, 'to_mxid');
        const message = requireParam(input.message, 'message');
        const identityId = await letta.getOrCreateAgentIdentity(agent_id);
        const identity = await ctx.storage.getIdentityAsync(identityId);
        if (!identity) {
          throw new Error(
            `Failed to get identity for agent: ${agent_id}\n\n` +
            `The agent exists but doesn't have a Matrix identity yet.\n\n` +
            `TO CREATE AN IDENTITY:\n` +
            `  {operation: "letta_identity", agent_id: "${agent_id}"}`
          );
        }
        const roomId = await ctx.roomManager.getOrCreateDMRoom(identity.mxid, to_mxid);
        const client = await ctx.clientPool.getClient(identity);
        const eventId = await client.sendMessage(roomId, { msgtype: 'm.text', body: message });
        await ctx.storage.updateDMActivity(identity.mxid, to_mxid);
        return result({ event_id: eventId, room_id: roomId, agent_id, identity_id: identityId, from: identity.mxid, to: to_mxid });
      }

      case 'talk_to_agent':
      case 'letta_chat': {
        // Unified agent chat - supports agent name, agent_name, or agent_id
        // Sends message to the agent's Matrix room
        console.log(`[MatrixMessaging] talk_to_agent called with:`, JSON.stringify({
          agent: input.agent,
          agent_name: input.agent_name, 
          agent_id: input.agent_id,
          message: input.message?.substring(0, 50),
          identity_id: input.identity_id,
          sender_identity_id: input.sender_identity_id,
          caller_directory: callerDirectory
        }));
        
        const letta = requireLetta();
        const message = requireParam(input.message, 'message');
        
        // Resolve agent - accept multiple input formats
        const agentInput = input.agent || input.agent_name || input.agent_id;
        if (!agentInput) {
          // Get suggestions for helpful error
          const suggestions = await letta.getSuggestions('', 5);
          throw new Error(
            `Missing agent - specify which agent to talk to.\n\n` +
            `EASIEST WAY:\n` +
            `  {operation: "talk_to_agent", agent: "Meridian", message: "Hello!"}\n\n` +
            `AVAILABLE AGENTS:\n` +
            suggestions.map(s => `  • ${s}`).join('\n') + '\n\n' +
            `TIP: Use agent names like "Meridian" or "BMO" - no need for UUIDs!`
          );
        }

        // Resolve agent name/id to actual agent_id
        const resolved = await letta.resolveAgentName(agentInput);
        if (!resolved) {
          const suggestions = await letta.getSuggestions(agentInput, 3);
          throw new Error(
            `Agent not found: "${agentInput}"\n\n` +
            (suggestions.length > 0 
              ? `DID YOU MEAN:\n${suggestions.map(s => `  • ${s}`).join('\n')}\n\n`
              : '') +
            `TO SEE ALL AGENTS:\n` +
            `  {operation: "letta_list"}\n\n` +
            `EXAMPLE:\n` +
            `  {operation: "talk_to_agent", agent: "Meridian", message: "Hello!"}`
          );
        }

        const agent_id = resolved.agent_id;
        const agent_name = resolved.agent_name;
        
        // Log resolution for debugging
        if (resolved.match_type !== 'exact_id') {
          console.log(`[MatrixMessaging] Resolved "${agentInput}" -> ${agent_name} (${agent_id}) via ${resolved.match_type} match (${Math.round(resolved.confidence * 100)}% confidence)`);
        }

        // Auto-attach matrix_messaging tool to the receiving agent
        // This ensures the agent can respond via Matrix
        const toolAttachResult = await letta.ensureMatrixToolAttached(agent_id);
        if (toolAttachResult.attached && !toolAttachResult.alreadyHad) {
          console.log(`[MatrixMessaging] Auto-attached matrix_messaging tool to ${agent_name}`);
        }
        
        let callerIdentity;
        let callerIdentitySource: 'explicit' | 'opencode' | 'letta_agent' | 'default' = 'default';
        
        // Check for injected agent ID from X-Agent-Id header (Letta agent calling)
        const headerAgentId = getInjectedAgentId();
        const callingAgentId = input.__injected_agent_id || headerAgentId;
        console.log(`[MatrixMessaging] talk_to_agent: Agent ID check - header=${headerAgentId}, injected=${input.__injected_agent_id}, callerDirectory=${callerDirectory}`);
        
        try {
          if (input.sender_identity_id || input.identity_id) {
            // Explicit identity specified
            const identityId = input.sender_identity_id ?? input.identity_id;
            if (!identityId) {
              throw new Error('Identity id not provided');
            }
            callerIdentity = await ctx.storage.getIdentityAsync(identityId);
            if (!callerIdentity) {
              throw new Error(`Identity not found: ${identityId}`);
            }
            callerIdentitySource = 'explicit';
            console.log(`[MatrixMessaging] talk_to_agent: Using explicit identity_id: ${callerIdentity.mxid}`);
          } else if (callingAgentId && ctx.lettaService) {
            // Letta agent calling via X-Agent-Id header - use that agent's Matrix identity
            console.log(`[MatrixMessaging] talk_to_agent: Resolving identity from X-Agent-Id header: ${callingAgentId}`);
            const identityId = await ctx.lettaService.getOrCreateAgentIdentity(callingAgentId);
            callerIdentity = await ctx.storage.getIdentityAsync(identityId);
            if (!callerIdentity) {
              throw new Error(`Failed to get identity for calling agent: ${callingAgentId}`);
            }
            callerIdentitySource = 'letta_agent';
            console.log(`[MatrixMessaging] talk_to_agent: Using Letta agent identity ${identityId} -> ${callerIdentity.mxid}`);
          } else if (callerDirectory) {
            // OpenCode/Claude Code caller
            callerIdentity = await resolveCallerIdentity(ctx, callerDirectory, callerName, effectiveSource);
            callerIdentitySource = effectiveSource === 'claude-code' ? 'explicit' : 'opencode';
            console.log(`[MatrixMessaging] talk_to_agent: Using ${effectiveSource} identity: ${callerIdentity.mxid}`);
          } else {
            // Fallback to default
            callerIdentitySource = 'default';
            callerIdentity = await ctx.openCodeService.getOrCreateDefaultIdentity();
            console.log(`[MatrixMessaging] talk_to_agent: Using default OpenCode identity: ${callerIdentity.mxid}`);
          }
        } catch (identityError: unknown) {
          const errMsg = identityError instanceof Error ? identityError.message : String(identityError);
          console.error(`[MatrixMessaging] talk_to_agent: Failed to get/create identity: ${errMsg}`);
          throw new Error(`Failed to create sender identity: ${errMsg}`);
        }

        const roomId = await getOrCreateAgentRoom(agent_id, agent_name, callerIdentity, ctx);

        // Only the caller identity joins - no additional invitees needed
        // (Previously added Claude Code identities alongside OpenCode, but that's redundant)
        const allInvitees = [{ identityId: callerIdentity.id, mxid: callerIdentity.mxid }];

        const { homeserverUrl } = getAdminConfig();

        const ensureJoinForIdentity = async (identityId: string, mxid: string): Promise<boolean> => {
          const client = await ctx.clientPool.getClientById(identityId);
          if (!client) {
            console.warn(`[MatrixMessaging] Client missing for identity ${identityId}`);
            return false;
          }

          try {
            await client.joinRoom(roomId);
            console.log(`[MatrixMessaging] ${mxid} joined room ${roomId}`);
            return true;
          } catch (joinError: unknown) {
            const errorMessage = joinError instanceof Error ? joinError.message : String(joinError);
            console.log(`[MatrixMessaging] Direct join failed for ${mxid}: ${errorMessage}`);

            try {
              const token = await getAdminToken();

              const membershipResponse = await fetch(
                `${homeserverUrl}/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state/m.room.member/${encodeURIComponent(mxid)}`,
                {
                  headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                  }
                }
              );

              if (membershipResponse.ok) {
                const membership = await membershipResponse.json() as { membership?: string };
                const membershipState = membership?.membership;

                if (membershipState === 'join') {
                  console.log(`[MatrixMessaging] ${mxid} already joined ${roomId}`);
                  return true;
                }

                if (membershipState === 'invite') {
                  await client.joinRoom(roomId);
                  console.log(`[MatrixMessaging] ${mxid} joined room after existing invite`);
                  return true;
                }
              }

              const inviteResponse = await fetch(
                `${homeserverUrl}/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/invite`,
                {
                  method: 'POST',
                  headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json'
                  },
                  body: JSON.stringify({ user_id: mxid })
                }
              );

              if (!inviteResponse.ok) {
                const err = await inviteResponse.text();
                throw new Error(`Invite failed: ${err}`);
              }

              await client.joinRoom(roomId);
              console.log(`[MatrixMessaging] ${mxid} joined room after admin invite`);
              return true;
            } catch (inviteError: unknown) {
              const inviteErrorMessage = inviteError instanceof Error ? inviteError.message : String(inviteError);
              console.error(`[MatrixMessaging] Failed to invite/join ${mxid}: ${inviteErrorMessage}`);
              return false;
            }
          }
        };

        const joinResults = await Promise.all(
          allInvitees.map(({ identityId, mxid }) => ensureJoinForIdentity(identityId, mxid))
        );

        if (!joinResults[0]) {
          const agentIdentityId = `letta_${agent_id}`;
          const agentIdentity = await ctx.storage.getIdentityAsync(agentIdentityId);
          const debugInfo = agentIdentity
            ? `Agent identity ${agentIdentity.mxid} found but invite/join failed`
            : `Agent identity ${agentIdentityId} NOT found in storage`;

          throw new Error(
            `Could not join room ${roomId} for ${agent_name}.

` +
            `The caller identity ${callerIdentity.mxid} needs to be invited to the room.

` +
            `Debug: ${debugInfo}

` +
            `This may happen if:
` +
            `• The room is private and requires an invite
` +
            `• The agent's Matrix identity doesn't have permission to invite

` +
            `Try having an admin invite ${callerIdentity.mxid} to the room.`
          );
        }

        // Send message to the agent's room
        const senderClient = await ctx.clientPool.getClient(callerIdentity);
        const eventId = await senderClient.sendMessage(roomId, {
          msgtype: 'm.text',
          body: message
        });
        
        const matrixApiUrl = process.env.MATRIX_API_URL || 'http://matrix-api:8000';
        const opencodeSender = (callerIdentity.mxid.startsWith('@oc_') || callerIdentity.mxid.startsWith('@cc_'))
          ? callerIdentity.mxid
          : undefined;
        try {
          await fetch(`${matrixApiUrl}/conversations/register`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
              agent_id, 
              matrix_event_id: eventId, 
              matrix_room_id: roomId,
              opencode_sender: opencodeSender
            })
          });
          console.log(`[MatrixMessaging] Registered conversation for ${agent_id}, opencode_sender=${opencodeSender}`);
        } catch (regError) {
          console.warn(`[MatrixMessaging] Failed to register conversation: ${regError}`);
        }
        
        // Start tracking this conversation for cross-run handling
                const { getConversationTracker } = await import('../core/conversation-tracker.ts');
        const tracker = getConversationTracker();
        const conv = tracker.startConversation(eventId, roomId, agent_id, message);
        console.log(`[MatrixMessaging] Started tracking conversation ${eventId} for agent ${agent_id}`);
        
        return result({ 
          success: true,
          agent: agent_name,
          agent_id,
          room_id: roomId,
          event_id: eventId,
          sender: callerIdentity.mxid,
          message,
          resolved_via: resolved.match_type !== 'exact_id' ? resolved.match_type : undefined,
          tool_attached: toolAttachResult.attached && !toolAttachResult.alreadyHad ? 'matrix_messaging' : undefined,
          tracking: {
            conversation_id: eventId,
            status: conv.status
          },
          note: `Message sent to ${agent_name}'s room. Agent will respond in Matrix. Conversation tracked for cross-run responses.`
        });
      }

      case 'letta_lookup': {
        const letta = requireLetta();
        
        // Accept agent, agent_name, or agent_id
        const agentInput = input.agent || input.agent_name || input.agent_id;
        if (!agentInput) {
          throw new Error(
            `Missing agent - specify which agent to look up.\n\n` +
            `EXAMPLE:\n` +
            `  {operation: "letta_lookup", agent: "Meridian"}\n` +
            `  {operation: "letta_lookup", agent_id: "agent-uuid"}`
          );
        }

        // Resolve agent name to ID
        const resolved = await letta.resolveAgentName(agentInput);
        if (!resolved) {
          const suggestions = await letta.getSuggestions(agentInput, 3);
          throw new Error(
            `Agent not found: "${agentInput}"\n\n` +
            (suggestions.length > 0 
              ? `DID YOU MEAN:\n${suggestions.map(s => `  • ${s}`).join('\n')}\n\n`
              : '') +
            `TO SEE ALL AGENTS:\n` +
            `  {operation: "letta_list"}`
          );
        }

        const agent = await letta.getAgent(resolved.agent_id);
        if (!agent) {
          throw new Error(`Agent data not found for: ${resolved.agent_id}`);
        }
        
        const identityId = IdentityManager.generateLettaId(resolved.agent_id);
        const identity = await ctx.storage.getIdentityAsync(identityId);
        return result({
          agent: { id: agent.id, name: agent.name, description: agent.description, model: agent.model },
          matrix_identity: identity ? { identity_id: identityId, mxid: identity.mxid, display_name: identity.displayName } : null,
          has_matrix_identity: !!identity,
          resolved_via: resolved.match_type !== 'exact_id' ? resolved.match_type : undefined
        });
      }

      case 'letta_list': {
        const letta = requireLetta();
        const agents = await letta.listAgents();
        const agentsWithIdentities = await Promise.all(
          agents.map(async agent => {
            const identityId = IdentityManager.generateLettaId(agent.id);
            const identity = await ctx.storage.getIdentityAsync(identityId);
            return {
              agent_id: agent.id, name: agent.name, description: agent.description, model: agent.model,
              matrix_identity: identity ? { identity_id: identityId, mxid: identity.mxid } : null
            };
          })
        );
        return result({ count: agents.length, agents: agentsWithIdentities });
      }

      case 'letta_identity': {
        const letta = requireLetta();
        
        // Accept agent, agent_name, or agent_id
        const agentInput = input.agent || input.agent_name || input.agent_id;
        if (!agentInput) {
          throw new Error(
            `Missing agent - specify which agent to get/create identity for.\n\n` +
            `EXAMPLE:\n` +
            `  {operation: "letta_identity", agent: "Meridian"}`
          );
        }

        // Resolve agent name to ID
        const resolved = await letta.resolveAgentName(agentInput);
        if (!resolved) {
          const suggestions = await letta.getSuggestions(agentInput, 3);
          throw new Error(
            `Agent not found: "${agentInput}"\n\n` +
            (suggestions.length > 0 
              ? `DID YOU MEAN:\n${suggestions.map(s => `  • ${s}`).join('\n')}\n\n`
              : '') +
            `TO SEE ALL AGENTS:\n` +
            `  {operation: "letta_list"}`
          );
        }

        const agent_id = resolved.agent_id;
        const identityId = await letta.getOrCreateAgentIdentity(agent_id);
        const identity = await ctx.storage.getIdentityAsync(identityId);
        if (!identity) {
          throw new Error(
            `Failed to create identity for ${resolved.agent_name}.\n\n` +
            `Something went wrong creating the Matrix identity.\n\n` +
            `TRY:\n` +
            `1. Verify the agent exists: {operation: "letta_lookup", agent: "${resolved.agent_name}"}\n` +
            `2. Check Letta service is running and accessible`
          );
        }
        const agent = await letta.getAgent(agent_id);
        return result({
          agent_id, agent_name: agent?.name,
          identity: { id: identity.id, mxid: identity.mxid, display_name: identity.displayName, type: identity.type, created_at: identity.createdAt },
          resolved_via: resolved.match_type !== 'exact_id' ? resolved.match_type : undefined
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
          const session = await ctx.openCodeService.getBridgeSession(input.directory);
          const identity = await ctx.openCodeService.getIdentity(input.directory);
          return result({
            directory: input.directory,
            connected: !!session,
            has_identity: !!identity,
            session: session ? { identity_id: session.identityId, mxid: session.mxid, connected_at: session.connectedAt, last_activity_at: session.lastActivityAt } : null,
            identity: identity ? { id: identity.id, mxid: identity.mxid, display_name: identity.displayName } : null
          });
        }
        const sessions = await ctx.openCodeService.listBridgeSessions();
        const identities = await ctx.storage.getAllIdentitiesAsync('opencode');
        return result({
          total_identities: identities.length,
          active_sessions: sessions.length,
          sessions: sessions.map((s) => ({ directory: s.directory, identity_id: s.identityId, mxid: s.mxid, connected_at: s.connectedAt }))
        });
      }

      default:
        throw new Error(
          `Unknown operation: "${input.operation}"\n\n` +
          `VALID OPERATIONS:\n` +
          `• Messaging: send, read, react, edit, typing\n` +
          `• Rooms: room_list, room_info, room_join, room_leave, room_create, room_invite, room_search\n` +
          `• Identity: identity_list, identity_get, identity_create, identity_derive\n` +
          `• Letta: letta_list, letta_chat, letta_send, letta_lookup, letta_identity\n` +
          `• OpenCode: opencode_connect, opencode_send, opencode_notify, opencode_status\n` +
          `• Subscriptions: subscribe, unsubscribe`
        );
    }
  }


const servicesInitPromise = initializeServices().catch((error) => {
  console.error("[MatrixMessaging] Failed to initialize services:", error);
});

export default async function matrixMessaging(input: Input): Promise<string> {
  try {
    await servicesInitPromise;
    const ctx = getToolContext();

    const injectedAgentId = getInjectedAgentId();
    const effectiveInput = injectedAgentId && !input.__injected_agent_id
      ? { ...input, __injected_agent_id: injectedAgentId }
      : input;

    const callerContext = await getCallerContext(effectiveInput);

    const effectiveSource = callerContext.sourceOverride || callerContext.source;
    if (callerContext.directory && effectiveSource !== 'claude-code') {
      await autoRegisterWithBridge(callerContext.directory, effectiveInput.room_id);
    }

    return await executeOperation(effectiveInput, ctx, callerContext);
  } catch (error: unknown) {
    const errMsg = error instanceof Error ? error.message : String(error);
    console.error(`[MatrixMessaging] Operation ${input.operation} failed: ${errMsg}`);
    return result({
      success: false,
      error: errMsg,
      operation: input.operation,
    });
  }
}
