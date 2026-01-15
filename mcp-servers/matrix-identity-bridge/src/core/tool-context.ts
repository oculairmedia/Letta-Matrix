/**
 * Shared context for all tools - singleton pattern
 */

import { Storage } from '../core/storage.js';
import { IdentityManager } from '../core/identity-manager.js';
import { MatrixClientPool } from '../core/client-pool.js';
import { RoomManager } from '../core/room-manager.js';
import { SubscriptionManager } from '../core/subscription-manager.js';
import { LettaService } from '../letta/letta-service.js';
import { OpenCodeService } from '../opencode/opencode-service.js';

export interface ToolContext {
  storage: Storage;
  identityManager: IdentityManager;
  clientPool: MatrixClientPool;
  roomManager: RoomManager;
  subscriptionManager: SubscriptionManager;
  lettaService: LettaService | null;
  openCodeService: OpenCodeService;
}

let _context: ToolContext | null = null;

export function setToolContext(ctx: ToolContext): void {
  _context = ctx;
}

export function getToolContext(): ToolContext {
  if (!_context) {
    throw new Error('Tool context not initialized. Call setToolContext() first.');
  }
  return _context;
}

/**
 * Helper to create successful result
 */
export function result(data: Record<string, unknown>): string {
  return JSON.stringify({ success: true, ...data }, null, 2);
}

/**
 * Helper to require a parameter with helpful error messages
 */
export function requireParam<T>(value: T | undefined, name: string, hint?: string): T {
  if (value === undefined || value === null) {
    const hints: Record<string, string> = {
      'message': 'Provide the message text to send.',
      'to_mxid': 'Provide target user Matrix ID. Format: @username:domain (e.g., "@meridian:matrix.oculair.ca"). This will auto-create a DM room if needed.',
      'room_id': 'Provide room ID. Format: !roomId:domain (e.g., "!abc123:matrix.oculair.ca"). TIP: Use {operation: "room_list", identity_id: "your-id"} to find your rooms.',
      'identity_id': 'Provide your identity ID. TIP: Use {operation: "identity_list"} first to see available identities and find your identity_id.',
      'agent_id': 'Provide Letta agent UUID. TIP: Use {operation: "letta_list"} first to see all agents and their agent_id values.',
      'event_id': 'Provide event ID (starts with $). TIP: Get event_id from the results of {operation: "read", identity_id: "...", room_id: "..."}.',
      'emoji': 'Provide reaction emoji (e.g., "👍", "✅", "❤️").',
      'query': 'Provide search query text.',
      'name': 'Provide a name for the room.',
      'localpart': 'Provide Matrix username (without @domain). Example: "mybot" becomes @mybot:matrix.oculair.ca',
      'display_name': 'Provide human-readable display name (e.g., "My Assistant Bot").',
      'directory': 'Provide working directory path (e.g., "/opt/stacks/my-project").',
      'subscription_id': 'Provide subscription ID. You get this from the result of a previous {operation: "subscribe"} call.',
      'new_content': 'Provide the new message content for the edit.',
      'typing': 'Provide true to show typing indicator, false to stop.',
      'type': 'Provide identity type: "custom", "letta", or "opencode".',
      'room_id_or_alias': 'Provide room ID (!roomId:domain) or room alias (#roomname:domain).',
      'user_mxid': 'Provide user Matrix ID to invite. Format: @username:domain'
    };
    const hintText = hint || hints[name] || '';
    throw new Error(`Missing required parameter: ${name}${hintText ? `. ${hintText}` : ''}`);
  }
  return value;
}

/**
 * Helper to require an identity with helpful error
 */
export async function requireIdentity(identity_id: string | undefined) {
  const ctx = getToolContext();
  const id = requireParam(identity_id, 'identity_id');
  const identity = await ctx.storage.getIdentityAsync(id);
  if (!identity) {
    const available = (await ctx.storage.getAllIdentitiesAsync()).slice(0, 5).map((i: { id: string }) => i.id);
    const hint = available.length > 0 
      ? `Available identities: ${available.join(', ')}${available.length >= 5 ? '...' : ''}`
      : 'No identities found. Use identity_create to create one.';
    throw new Error(`Identity not found: "${id}". ${hint}`);
  }
  return identity;
}

/**
 * Helper to require Letta service with helpful error
 */
export function requireLetta(): LettaService {
  const ctx = getToolContext();
  if (!ctx.lettaService) {
    throw new Error(
      'Letta integration not configured. Set LETTA_API_URL environment variable ' +
      '(e.g., "http://192.168.50.90:8283") to enable Letta operations.'
    );
  }
  return ctx.lettaService;
}
