import fs from 'fs/promises';
import path from 'path';
import { AgentMappingStore } from './agent-mapping-store.ts';
import type { ToolContext } from './tool-context.js';

export type AgentMapping = {
  agent_id: string;
  agent_name?: string;
  matrix_user_id?: string;
  matrix_password?: string;
  created?: boolean;
  room_id?: string;
  room_created?: boolean;
  matrix_room_id?: string;
  invitation_status?: Record<string, string>;
};

const getAgentMappingsPath = (): string => {
  if (process.env.AGENT_USER_MAPPINGS_PATH) {
    return process.env.AGENT_USER_MAPPINGS_PATH;
  }

  const dataDir = process.env.DATA_DIR || '/app/data';
  return path.join(dataDir, 'agent_user_mappings.json');
};

const loadAgentMappings = async (): Promise<Record<string, AgentMapping>> => {
  const mappingsPath = getAgentMappingsPath();

  try {
    const raw = await fs.readFile(mappingsPath, 'utf-8');
    const data = JSON.parse(raw) as Record<string, AgentMapping>;
    return data && typeof data === 'object' ? data : {};
  } catch (error: any) {
    if (error?.code === 'ENOENT') {
      return {};
    }
    console.error('[MatrixMessaging] Failed to load agent mappings:', error);
    return {};
  }
};

const saveAgentMappings = async (mappings: Record<string, AgentMapping>): Promise<void> => {
  const mappingsPath = getAgentMappingsPath();
  await fs.mkdir(path.dirname(mappingsPath), { recursive: true });
  await fs.writeFile(mappingsPath, JSON.stringify(mappings, null, 2), 'utf-8');
};

export const getOrCreateAgentRoom = async (
  agentId: string,
  agentName: string,
  callerIdentity: { mxid: string },
  ctx: ToolContext
): Promise<string> => {
  const matrixApiUrl = process.env.MATRIX_API_URL || 'http://matrix-api:8000';
  const mappingStore = new AgentMappingStore({ apiUrl: matrixApiUrl });
  const apiMapping = await mappingStore.getMappingByAgentId(agentId);

  const mappings = await loadAgentMappings();
  const existing = mappings[agentId];
  const existingRoom = apiMapping?.room_id || apiMapping?.matrix_room_id || existing?.room_id || existing?.matrix_room_id;

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

    mappings[agentId] = {
      ...existing,
      agent_id: agentId,
      agent_name: agentName,
      matrix_user_id: agentIdentity.mxid,
      matrix_password: agentIdentity.password,
      created: true,
      room_id: roomId,
      room_created: true,
      invitation_status: {
        ...(existing?.invitation_status ?? {}),
        [callerIdentity.mxid]: 'invited',
      },
    };

    await saveAgentMappings(mappings);
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
      mappings[agentId] = {
        ...existing,
        agent_id: agentId,
        agent_name: agentName,
        matrix_user_id: agentIdentity.mxid,
        matrix_password: agentIdentity.password,
        room_id: existingRoom,
        invitation_status: {
          ...(existing?.invitation_status ?? {}),
          [callerIdentity.mxid]: 'invited',
        },
      };
      await saveAgentMappings(mappings);
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
