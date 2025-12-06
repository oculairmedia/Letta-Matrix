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
  'talk_to_agent', // NEW: Simplified agent chat with name resolution
  'opencode_connect', 'opencode_send', 'opencode_notify', 'opencode_status'
] as const;

const schema = z.object({
  operation: z.enum(operations).describe(
    'The operation to perform. Common operations: ' +
    'send (send message to user/room), ' +
    'letta_chat (chat with Letta agent), ' +
    'read (read room messages), ' +
    'room_list (list joined rooms)'
  ),
  
  // === CALLER CONTEXT (auto-populated by OpenCode) ===
  caller_directory: z.string().optional().describe(
    'Your working directory path. OpenCode auto-populates this. Example: /opt/stacks/my-project'
  ),
  caller_name: z.string().optional().describe(
    'Display name override. Example: "OpenCode - MyProject"'
  ),
  
  // === IDENTITY PARAMETERS ===
  identity_id: z.string().optional().describe(
    'Identity ID for operations requiring an identity. Use identity_list to find available IDs.'
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
  query: z.string().optional().describe('Search query text for room_search'),
  limit: z.number().optional().describe('Max results to return (default: 50 for read, 10 for search)'),
  
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
  )
});

type Input = z.infer<typeof schema>;

class MatrixMessaging extends MCPTool<typeof schema> {
  name = 'matrix_messaging';
  description = `Matrix messaging tool - talk to AI agents and send messages.

═══════════════════════════════════════════════════════════════════════════════
★ EASIEST WAY TO TALK TO AN AGENT ★
═══════════════════════════════════════════════════════════════════════════════

▶ USE talk_to_agent (RECOMMENDED - supports names!):
  {operation: "talk_to_agent", agent: "Meridian", message: "Hello!"}
  {operation: "talk_to_agent", agent: "BMO", message: "What's up?"}
  
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
• Responses are async - agent replies appear in Matrix`;
  schema = schema;

  /**
   * Get the effective caller directory - from input or environment variable.
   * This makes the tool resilient by checking multiple sources in order:
   * 1. Explicit input (caller_directory parameter)
   * 2. OPENCODE_PROJECT_DIR env var
   * 3. PWD env var (always available, set to working directory)
   */
  private getEffectiveCallerDirectory(input: Input): string | undefined {
    // Explicit input takes priority
    if (input.caller_directory) {
      return input.caller_directory;
    }
    // Fall back to OPENCODE_PROJECT_DIR environment variable
    const envDir = process.env.OPENCODE_PROJECT_DIR;
    if (envDir) {
      console.log(`[MatrixMessaging] Using OPENCODE_PROJECT_DIR: ${envDir}`);
      return envDir;
    }
    // Fall back to PWD - this is always set to the working directory
    const pwd = process.env.PWD;
    if (pwd) {
      console.log(`[MatrixMessaging] Using PWD: ${pwd}`);
      return pwd;
    }
    return undefined;
  }

  async execute(input: Input): Promise<string> {
    const ctx = getToolContext();

    // Get effective caller directory (from input or env)
    const callerDirectory = this.getEffectiveCallerDirectory(input);

    // Auto-register with OpenCode bridge if we have a caller directory
    if (callerDirectory) {
      await this.autoRegisterWithBridge(callerDirectory, input.room_id);
    }

    switch (input.operation) {
      // === MESSAGE OPERATIONS ===
      case 'send': {
        let identity;
        
        // If we have a caller directory (explicit or from env), use OpenCode identity
        if (callerDirectory) {
          identity = await ctx.openCodeService.getOrCreateIdentity(callerDirectory);
          // Update display name if caller_name provided
          if (input.caller_name && identity.displayName !== input.caller_name) {
            // TODO: Could update display name here if needed
          }
        } else if (input.agent_id && ctx.lettaService) {
          // Auto-derive identity from agent_id (like letta_send does)
          const identityId = await ctx.lettaService.getOrCreateAgentIdentity(input.agent_id);
          identity = ctx.storage.getIdentity(identityId);
          if (!identity) {
            throw new Error(`Failed to get identity for agent: ${input.agent_id}`);
          }
          console.log(`[MatrixMessaging] send: Auto-derived identity ${identityId} from agent_id ${input.agent_id}`);
        } else if (input.identity_id) {
          identity = requireIdentity(input.identity_id);
        } else {
          // No identity available - provide helpful error
          throw new Error(
            `No identity available for send operation.\n\n` +
            `OPTIONS:\n` +
            `• Use agent_id: {operation: "send", agent_id: "agent-uuid", room_id: "!room:domain", message: "Hi"}\n` +
            `• Use identity_id: {operation: "send", identity_id: "your-id", room_id: "!room:domain", message: "Hi"}\n\n` +
            `TIP: Use {operation: "letta_list"} to find agent IDs, or {operation: "identity_list"} for identities.`
          );
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
          throw new Error(
            `Missing input - need something to derive identity from.\n\n` +
            `OPTIONS:\n` +
            `• directory: Working directory path → {operation: "identity_derive", directory: "/opt/stacks/my-project"}\n` +
            `• agent_id: Letta agent UUID → {operation: "identity_derive", agent_id: "uuid"}\n` +
            `• session_id: Session identifier → {operation: "identity_derive", session_id: "session-123"}\n` +
            `• explicit: Known identity ID → {operation: "identity_derive", explicit: "my-identity-id"}`
          );
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
          caller_directory: callerDirectory
        }));
        
        try {
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
        
        // Get OpenCode identity for the caller
        // callerDirectory is derived from: input > OPENCODE_PROJECT_DIR > PWD
        // PWD is always available, so this should always work!
        let callerIdentity;
        console.log(`[MatrixMessaging] Getting identity for callerDirectory: ${callerDirectory || 'NONE'}`);
        try {
          if (callerDirectory) {
            callerIdentity = await ctx.openCodeService.getOrCreateIdentity(callerDirectory);
            console.log(`[MatrixMessaging] Got identity: ${callerIdentity.mxid}`);
          } else {
            // Fallback to default identity (shouldn't happen since PWD is always set)
            callerIdentity = await ctx.openCodeService.getOrCreateDefaultIdentity();
            console.log(`[MatrixMessaging] Using default OpenCode identity: ${callerIdentity.mxid}`);
          }
        } catch (identityError: unknown) {
          const errMsg = identityError instanceof Error ? identityError.message : String(identityError);
          console.error(`[MatrixMessaging] Failed to get/create identity: ${errMsg}`);
          throw new Error(`Failed to create sender identity: ${errMsg}`);
        }
        
        // Look up the agent's Matrix room from agent_user_mappings.json
        let roomId: string | null = null;
        try {
          const fs = await import('fs');
          const mappingsPath = '/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_user_mappings.json';
          if (fs.existsSync(mappingsPath)) {
            const mappings = JSON.parse(fs.readFileSync(mappingsPath, 'utf-8'));
            const agentMapping = mappings[agent_id];
            if (agentMapping?.room_id) {
              roomId = agentMapping.room_id;
              console.log(`[MatrixMessaging] Using room ${roomId} for ${agent_name}`);
            }
          }
        } catch (e) {
          console.error('[MatrixMessaging] Failed to read agent mappings:', e);
        }
        
        if (!roomId) {
          throw new Error(
            `No Matrix room configured for ${agent_name} (${agent_id}).\n\n` +
            `The agent exists but doesn't have a room set up yet.\n\n` +
            `TO CHECK AGENT STATUS:\n` +
            `  {operation: "letta_lookup", agent: "${agent_name}"}\n\n` +
            `This usually means the agent needs to be configured in agent_user_mappings.json.`
          );
        }
        
        const client = await ctx.clientPool.getClient(callerIdentity);
        
        // Ensure the caller can join the room
        // First, try to get the agent's identity to invite the caller
        let joined = false;
        try {
          // Try joining directly first (works if room is public or already invited)
          await client.joinRoom(roomId);
          joined = true;
          console.log(`[MatrixMessaging] ${callerIdentity.mxid} joined room ${roomId}`);
        } catch (joinError: unknown) {
          const errorMessage = joinError instanceof Error ? joinError.message : String(joinError);
          console.log(`[MatrixMessaging] Direct join failed: ${errorMessage}`);
          
          // If join failed, try to invite the caller using admin credentials
          try {
            // Get admin credentials from environment
            const adminUsername = process.env.MATRIX_ADMIN_USERNAME || '@admin:matrix.oculair.ca';
            const adminPassword = process.env.MATRIX_ADMIN_PASSWORD;
            const homeserverUrl = process.env.MATRIX_HOMESERVER_URL || 'http://127.0.0.1:6167';
            
            if (!adminPassword) {
              console.log(`[MatrixMessaging] No MATRIX_ADMIN_PASSWORD set, cannot invite`);
              throw new Error('Admin credentials not configured');
            }
            
            console.log(`[MatrixMessaging] Logging in as admin to invite ${callerIdentity.mxid}`);
            
            // Login as admin to get fresh token
            const loginResponse = await fetch(`${homeserverUrl}/_matrix/client/v3/login`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                type: 'm.login.password',
                identifier: { type: 'm.id.user', user: adminUsername.replace(/@([^:]+):.*/, '$1') },
                password: adminPassword
              })
            });
            
            if (!loginResponse.ok) {
              const err = await loginResponse.text();
              throw new Error(`Admin login failed: ${err}`);
            }
            
            const loginData = await loginResponse.json() as { access_token: string };
            const adminToken = loginData.access_token;
            console.log(`[MatrixMessaging] Admin login successful, sending invite...`);
            
            // Invite the user using admin token
            const inviteResponse = await fetch(
              `${homeserverUrl}/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/invite`,
              {
                method: 'POST',
                headers: {
                  'Authorization': `Bearer ${adminToken}`,
                  'Content-Type': 'application/json'
                },
                body: JSON.stringify({ user_id: callerIdentity.mxid })
              }
            );
            
            if (!inviteResponse.ok) {
              const err = await inviteResponse.text();
              throw new Error(`Invite failed: ${err}`);
            }
            
            console.log(`[MatrixMessaging] Invited ${callerIdentity.mxid} to room via admin`);
            
            // Now try to join again
            console.log(`[MatrixMessaging] Attempting to join after invite...`);
            await client.joinRoom(roomId);
            joined = true;
            console.log(`[MatrixMessaging] ${callerIdentity.mxid} joined room after admin invite`);
            
          } catch (inviteError: unknown) {
            const inviteErrorMessage = inviteError instanceof Error ? inviteError.message : String(inviteError);
            console.error(`[MatrixMessaging] Failed to invite/join via admin: ${inviteErrorMessage}`);
            if (inviteError instanceof Error && inviteError.stack) {
              console.error(`[MatrixMessaging] Stack: ${inviteError.stack}`);
            }
          }
        }
        
        if (!joined) {
          // Check why invite failed
          const agentIdentityId = `letta_${agent_id}`;
          const agentIdentity = ctx.storage.getIdentity(agentIdentityId);
          const debugInfo = agentIdentity 
            ? `Agent identity ${agentIdentity.mxid} found but invite/join failed`
            : `Agent identity ${agentIdentityId} NOT found in storage`;
          
          throw new Error(
            `Could not join room ${roomId} for ${agent_name}.\n\n` +
            `The caller identity ${callerIdentity.mxid} needs to be invited to the room.\n\n` +
            `Debug: ${debugInfo}\n\n` +
            `This may happen if:\n` +
            `• The room is private and requires an invite\n` +
            `• The agent's Matrix identity doesn't have permission to invite\n\n` +
            `Try having an admin invite ${callerIdentity.mxid} to the room.`
          );
        }
        
        // Send message to the agent's room
        const eventId = await client.sendMessage(roomId, {
          msgtype: 'm.text',
          body: message
        });
        
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
          note: `Message sent to ${agent_name}'s room. Agent will respond in Matrix.`
        });
        } catch (talkError: unknown) {
          const errMsg = talkError instanceof Error ? talkError.message : String(talkError);
          const errStack = talkError instanceof Error ? talkError.stack : '';
          console.error(`[MatrixMessaging] talk_to_agent error: ${errMsg}`);
          console.error(`[MatrixMessaging] Stack: ${errStack}`);
          return result({
            success: false,
            error: errMsg,
            caller_directory: callerDirectory,
            debug: 'Check MCP server logs for more details'
          });
        }
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
        const identity = ctx.storage.getIdentity(identityId);
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
        const identity = ctx.storage.getIdentity(identityId);
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
