/**
 * OpenCode Room Management
 * Creates and manages Matrix rooms for OpenCode instances
 * Follows same permission structure as Letta agent rooms
 */

import fs from 'fs/promises';
import path from 'path';
import type { ToolContext } from './tool-context.js';
import type { MatrixIdentity } from '../types/index.js';

export interface OpenCodeRoomMapping {
  directory: string;
  room_id: string;
  identity_id: string;
  identity_mxid: string;
  created_at: number;
  invitation_status: Record<string, string>;
}

const getOpenCodeRoomMappingsPath = (): string => {
  if (process.env.OPENCODE_ROOM_MAPPINGS_PATH) {
    return process.env.OPENCODE_ROOM_MAPPINGS_PATH;
  }
  const dataDir = process.env.DATA_DIR || '/app/data';
  return path.join(dataDir, 'opencode_room_mappings.json');
};

const loadOpenCodeRoomMappings = async (): Promise<Record<string, OpenCodeRoomMapping>> => {
  const mappingsPath = getOpenCodeRoomMappingsPath();
  try {
    const raw = await fs.readFile(mappingsPath, 'utf-8');
    const data = JSON.parse(raw) as Record<string, OpenCodeRoomMapping>;
    return data && typeof data === 'object' ? data : {};
  } catch (error: any) {
    if (error?.code === 'ENOENT') {
      return {};
    }
    console.error('[OpenCodeRooms] Failed to load room mappings:', error);
    return {};
  }
};

const saveOpenCodeRoomMappings = async (mappings: Record<string, OpenCodeRoomMapping>): Promise<void> => {
  const mappingsPath = getOpenCodeRoomMappingsPath();
  await fs.mkdir(path.dirname(mappingsPath), { recursive: true });
  await fs.writeFile(mappingsPath, JSON.stringify(mappings, null, 2), 'utf-8');
};

/**
 * Derive a stable key from directory path for room mapping storage
 */
const deriveRoomKey = (directory: string): string => {
  // Use base64 encoding of directory path for stable key
  return Buffer.from(directory).toString('base64url');
};

/**
 * Get or create a Matrix room for an OpenCode instance
 * Uses same permission structure as Letta agent rooms:
 * - preset: private_chat (invite-only)
 * - history_visibility: shared
 */
export const getOrCreateOpenCodeRoom = async (
  directory: string,
  opencodeIdentity: MatrixIdentity,
  callerIdentity: MatrixIdentity,
  ctx: ToolContext
): Promise<string> => {
  const roomKey = deriveRoomKey(directory);
  const mappings = await loadOpenCodeRoomMappings();
  const existing = mappings[roomKey];

  const client = await ctx.clientPool.getClient(opencodeIdentity);

  // Extract project name for room name
  const projectName = directory.split('/').filter(Boolean).pop() || 'OpenCode';
  const roomName = `OpenCode: ${projectName}`;

  const createNewRoom = async (): Promise<string> => {
    const bridgeUserMxid = process.env.OPENCODE_BRIDGE_MXID || '@oc_matrix_synapse_deployment:matrix.oculair.ca';
    const adminMxid = process.env.MATRIX_ADMIN_USERNAME || '@admin:matrix.oculair.ca';
    const ownerMxid = process.env.OPENCODE_OWNER_MXID || '@oculair:matrix.oculair.ca';
    
    const inviteList = new Set<string>();
    if (callerIdentity.mxid !== opencodeIdentity.mxid) {
      inviteList.add(callerIdentity.mxid);
    }
    if (bridgeUserMxid !== opencodeIdentity.mxid) {
      inviteList.add(bridgeUserMxid);
    }
    if (adminMxid !== opencodeIdentity.mxid) {
      inviteList.add(adminMxid);
    }
    if (ownerMxid !== opencodeIdentity.mxid) {
      inviteList.add(ownerMxid);
    }
    
    const roomId = await client.createRoom({
      name: roomName,
      topic: `Room for OpenCode instance: ${directory}`,
      preset: 'private_chat',
      invite: Array.from(inviteList),  // Invite at creation time
      initial_state: [
        { type: 'm.room.history_visibility', content: { history_visibility: 'shared' } },
      ],
    });
    
    console.log(`[OpenCodeRooms] Created room ${roomId} with invites: ${Array.from(inviteList).join(', ')}`);

    const invitationStatus: Record<string, string> = {};
    for (const mxid of inviteList) {
      invitationStatus[mxid] = 'invited';
    }
    
    mappings[roomKey] = {
      directory,
      room_id: roomId,
      identity_id: opencodeIdentity.id,
      identity_mxid: opencodeIdentity.mxid,
      created_at: Date.now(),
      invitation_status: invitationStatus,
    };
    await saveOpenCodeRoomMappings(mappings);

    return roomId;
  };

  if (existing?.room_id) {
    // Try to join existing room
    try {
      await client.joinRoom(existing.room_id);
    } catch (error) {
      console.warn('[OpenCodeRooms] Could not join existing room, will create new:', error);
      return await createNewRoom();
    }

    const bridgeUserMxid = process.env.OPENCODE_BRIDGE_MXID || '@oc_matrix_synapse_deployment:matrix.oculair.ca';
    const adminMxid = process.env.MATRIX_ADMIN_USERNAME || '@admin:matrix.oculair.ca';
    const ownerMxid = process.env.OPENCODE_OWNER_MXID || '@oculair:matrix.oculair.ca';
    let needsSave = false;
    
    const toInvite: string[] = [];
    if (callerIdentity.mxid !== opencodeIdentity.mxid && !existing.invitation_status?.[callerIdentity.mxid]) {
      toInvite.push(callerIdentity.mxid);
    }
    if (bridgeUserMxid !== opencodeIdentity.mxid && !existing.invitation_status?.[bridgeUserMxid]) {
      toInvite.push(bridgeUserMxid);
    }
    if (adminMxid !== opencodeIdentity.mxid && !existing.invitation_status?.[adminMxid]) {
      toInvite.push(adminMxid);
    }
    if (ownerMxid !== opencodeIdentity.mxid && !existing.invitation_status?.[ownerMxid]) {
      toInvite.push(ownerMxid);
    }
    
    for (const mxid of toInvite) {
      try {
        await ctx.roomManager.inviteUser(opencodeIdentity.id, existing.room_id, mxid);
        existing.invitation_status = { ...(existing.invitation_status ?? {}), [mxid]: 'invited' };
        needsSave = true;
        console.log(`[OpenCodeRooms] Invited ${mxid} to existing room ${existing.room_id}`);
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : String(error);
        if (!errorMessage.includes('M_FORBIDDEN')) {
          console.warn(`[OpenCodeRooms] Failed to invite ${mxid}:`, errorMessage);
        }
      }
    }

    if (needsSave) {
      mappings[roomKey] = existing;
      await saveOpenCodeRoomMappings(mappings);
    }

    return existing.room_id;
  }

  return await createNewRoom();
};

/**
 * Get existing room for an OpenCode directory (without creating)
 */
export const getOpenCodeRoom = async (directory: string): Promise<string | undefined> => {
  const roomKey = deriveRoomKey(directory);
  const mappings = await loadOpenCodeRoomMappings();
  return mappings[roomKey]?.room_id;
};

/**
 * Update room registration in the bridge
 */
export const updateBridgeRegistration = async (
  directory: string,
  roomId: string
): Promise<void> => {
  const bridgeUrl = process.env.OPENCODE_BRIDGE_URL || 'http://127.0.0.1:3201';
  try {
    await fetch(`${bridgeUrl}/update-rooms`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ directory, rooms: [roomId] }),
    });
  } catch (error) {
    console.warn('[OpenCodeRooms] Failed to update bridge registration:', error);
  }
};
