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
 * Helper to require a parameter
 */
export function requireParam<T>(value: T | undefined, name: string): T {
  if (value === undefined || value === null) {
    throw new Error(`Missing required parameter: ${name}`);
  }
  return value;
}

/**
 * Helper to require an identity
 */
export function requireIdentity(identity_id: string | undefined) {
  const ctx = getToolContext();
  const id = requireParam(identity_id, 'identity_id');
  const identity = ctx.storage.getIdentity(id);
  if (!identity) {
    throw new Error(`Identity not found: ${id}`);
  }
  return identity;
}

/**
 * Helper to require Letta service
 */
export function requireLetta(): LettaService {
  const ctx = getToolContext();
  if (!ctx.lettaService) {
    throw new Error('Letta integration not configured');
  }
  return ctx.lettaService;
}
