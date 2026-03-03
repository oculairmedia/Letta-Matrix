/**
 * Agent Room Management
 * 
 * Uses the AgentMappingApiClient to read/write agent mappings from the
 * centralized PostgreSQL database via the matrix-api REST service.
 * 
 * This replaces the old JSON file + separate REST client approach.
 * The database (via mapping_service) is the SINGLE SOURCE OF TRUTH.
 */

import { getAgentMappingApi, type AgentMappingRecord } from './agent-mapping-api.js';
import type { ToolContext } from './tool-context.js';

export type AgentMapping = AgentMappingRecord;

// --- In-memory cache for hot-path lookups (TTL-based) ---
let cachedMappings: Record<string, AgentMappingRecord> | null = null;
let cacheTimestamp = 0;
const CACHE_TTL = 30_000; // 30 seconds — DB is source of truth, cache is just for perf

const getCachedMappings = async (): Promise<Record<string, AgentMappingRecord>> => {
  if (cachedMappings && Date.now() - cacheTimestamp < CACHE_TTL) {
    return cachedMappings;
  }
  const api = getAgentMappingApi();
  cachedMappings = await api.getAll();
  cacheTimestamp = Date.now();
  return cachedMappings;
};

/** Invalidate local cache after a write */
const invalidateCache = (): void => {
  cachedMappings = null;
  cacheTimestamp = 0;
};

export const getOrCreateAgentRoom = async (
  agentId: string,
  agentName: string,
  callerIdentity: { mxid: string },
  ctx: ToolContext
): Promise<string> => {
  const api = getAgentMappingApi();

  // Check DB for existing mapping
  const dbMapping = await api.getByAgentId(agentId);
  const existingRoom = dbMapping?.room_id;

  if (!ctx.lettaService) {
    throw new Error('Letta service not available for agent room provisioning');
  }

  const agentIdentityId = await ctx.lettaService.getOrCreateAgentIdentity(agentId);
  const agentIdentity = await ctx.storage.getIdentityAsync(agentIdentityId);
  if (!agentIdentity) {
    throw new Error(`Identity not found for agent: ${agentId}`);
  }

  const client = await ctx.clientPool.getClient(agentIdentity);

  const createNewRoom = async (): Promise<string> => {
    const roomId = await client.createRoom({
      name: agentName,
      topic: `Room for ${agentName}`,
      preset: 'private_chat',
      initial_state: [
        { type: 'm.room.history_visibility', content: { history_visibility: 'shared' } },
      ],
    });

    try {
      await ctx.roomManager.inviteUser(agentIdentityId, roomId, callerIdentity.mxid);
    } catch (error) {
      console.error('[MatrixMessaging] Failed to invite caller to agent room:', error);
    }

    // Write mapping to DB (upsert — creates or updates)
    await api.upsert({
      agent_id: agentId,
      agent_name: agentName,
      matrix_user_id: agentIdentity.mxid,
      matrix_password: agentIdentity.password || '',
      room_id: roomId,
      room_created: true,
    });
    invalidateCache();

    console.log(`[MatrixMessaging] Created agent room ${roomId} for ${agentName}`);
    return roomId;
  };

  if (existingRoom) {
    try {
      await client.joinRoom(existingRoom);
    } catch (error) {
      console.warn('[MatrixMessaging] Agent identity could not join existing room, keeping existing room:', error);
      return existingRoom;
    }

    try {
      await ctx.roomManager.inviteUser(agentIdentityId, existingRoom, callerIdentity.mxid);
      return existingRoom;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      if (errorMessage.includes('M_FORBIDDEN') || errorMessage.includes('not in the room')) {
        console.warn('[MatrixMessaging] Invite failed for existing room, keeping existing room:', errorMessage);
        return existingRoom;
      }
      console.warn('[MatrixMessaging] Invite failed for existing room, continuing:', errorMessage);
      return existingRoom;
    }
  }

  return await createNewRoom();
};
