/**
 * Room Manager - DM room creation and management
 */

import type { MatrixClient } from '@vector-im/matrix-bot-sdk';
import type { Storage } from './storage.js';
import type { MatrixClientPool } from './client-pool.js';
import type { DMRoomMapping, RoomInfo, MatrixEvent } from '../types/index.js';

/**
 * Room info with name for server-wide listing
 */
export interface ServerRoomInfo {
  roomId: string;
  name?: string;
  topic?: string;
  memberCount?: number;
  canonicalAlias?: string;
}

export class RoomManager {
  private storage: Storage;
  private clientPool: MatrixClientPool;

  constructor(storage: Storage, clientPool: MatrixClientPool) {
    this.storage = storage;
    this.clientPool = clientPool;
  }

  /**
   * Get or create DM room between two users
   */
  async getOrCreateDMRoom(
    fromMxid: string,
    toMxid: string
  ): Promise<string> {
    // Check if DM room already exists
    const existing = this.storage.getDMRoom(fromMxid, toMxid);
    if (existing) {
      console.log('[RoomManager] Using existing DM room:', existing.roomId);
      return existing.roomId;
    }

    // Create new DM room
    console.log('[RoomManager] Creating new DM room:', fromMxid, '<->', toMxid);
    return await this.createDMRoom(fromMxid, toMxid);
  }

  /**
   * Create DM room between two users
   */
  private async createDMRoom(fromMxid: string, toMxid: string): Promise<string> {
    // Get client for the initiator
    const fromIdentity = this.storage.getIdentityByMXID(fromMxid);
    if (!fromIdentity) {
      throw new Error(`Identity not found for MXID: ${fromMxid}`);
    }

    const client = await this.clientPool.getClient(fromIdentity);

    // Create DM room
    const roomId = await client.createRoom({
      preset: 'trusted_private_chat',
      is_direct: true,
      invite: [toMxid],
      initial_state: [],
      power_level_content_override: {
        users: {
          [fromMxid]: 100,
          [toMxid]: 100
        }
      }
    });

    // Mark room as direct message
    const directRooms = await this.getDirectRooms(client, fromMxid);
    directRooms[toMxid] = directRooms[toMxid] || [];
    if (!directRooms[toMxid].includes(roomId)) {
      directRooms[toMxid].push(roomId);
    }
    await this.setDirectRooms(client, fromMxid, directRooms);

    // Create mapping
    const key = this.createDMKey(fromMxid, toMxid);
    const mapping: DMRoomMapping = {
      key,
      roomId,
      participants: [fromMxid, toMxid].sort() as [string, string],
      createdAt: Date.now(),
      lastActivityAt: Date.now()
    };

    await this.storage.saveDMRoom(mapping);

    console.log('[RoomManager] Created DM room:', roomId);
    return roomId;
  }

  /**
   * Get direct rooms for a user from account data
   */
  private async getDirectRooms(
    client: MatrixClient,
    mxid: string
  ): Promise<Record<string, string[]>> {
    try {
      const accountData = await client.getAccountData('m.direct') as Record<string, string[]> | null;
      return accountData || {};
    } catch (error) {
      return {};
    }
  }

  /**
   * Set direct rooms for a user in account data
   */
  private async setDirectRooms(
    client: MatrixClient,
    mxid: string,
    directRooms: Record<string, string[]>
  ): Promise<void> {
    await client.setAccountData('m.direct', directRooms);
  }

  /**
   * Join room
   */
  async joinRoom(identityId: string, roomIdOrAlias: string): Promise<string> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    const roomId = await client.joinRoom(roomIdOrAlias);
    console.log('[RoomManager] Joined room:', roomId, 'as', identityId);
    return roomId;
  }

  /**
   * Leave room
   */
  async leaveRoom(identityId: string, roomId: string): Promise<void> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    await client.leaveRoom(roomId);
    console.log('[RoomManager] Left room:', roomId, 'as', identityId);
  }

  /**
   * Get room info
   */
  async getRoomInfo(identityId: string, roomId: string): Promise<RoomInfo> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    const [name, topic, avatarUrl, members] = await Promise.all([
      this.getRoomName(client, roomId),
      this.getRoomTopic(client, roomId),
      this.getRoomAvatar(client, roomId),
      this.getRoomMembers(client, roomId)
    ]);

    const identity = await this.storage.getIdentityAsync(identityId);
    const isDirect = identity
      ? this.storage.getDMRoomsForUser(identity.mxid).some(dm => dm.roomId === roomId)
      : false;

    return {
      roomId,
      name,
      topic,
      avatarUrl,
      memberCount: members.length,
      isDirect
    };
  }

  /**
   * Get room name
   */
  private async getRoomName(client: MatrixClient, roomId: string): Promise<string | undefined> {
    try {
      const state = await client.getRoomStateEvent(roomId, 'm.room.name', '');
      return state?.name;
    } catch {
      return undefined;
    }
  }

  /**
   * Get room topic
   */
  private async getRoomTopic(client: MatrixClient, roomId: string): Promise<string | undefined> {
    try {
      const state = await client.getRoomStateEvent(roomId, 'm.room.topic', '');
      return state?.topic;
    } catch {
      return undefined;
    }
  }

  /**
   * Get room avatar
   */
  private async getRoomAvatar(client: MatrixClient, roomId: string): Promise<string | undefined> {
    try {
      const state = await client.getRoomStateEvent(roomId, 'm.room.avatar', '');
      return state?.url;
    } catch {
      return undefined;
    }
  }

  /**
   * Get room members
   */
  private async getRoomMembers(client: MatrixClient, roomId: string): Promise<string[]> {
    try {
      const members = await client.getJoinedRoomMembers(roomId);
      return members;
    } catch (error) {
      console.error('[RoomManager] Error getting room members:', error);
      return [];
    }
  }

  /**
   * List joined rooms for identity
   */
  async listJoinedRooms(identityId: string): Promise<string[]> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    return await client.getJoinedRooms();
  }

  /**
   * Set room topic
   */
  async setRoomTopic(identityId: string, roomId: string, topic: string): Promise<void> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    await client.sendStateEvent(roomId, 'm.room.topic', '', { topic });
    console.log('[RoomManager] Set room topic:', roomId);
  }

  /**
   * Set room name
   */
  async setRoomName(identityId: string, roomId: string, name: string): Promise<void> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    await client.sendStateEvent(roomId, 'm.room.name', '', { name });
    console.log('[RoomManager] Set room name:', roomId);
  }

  /**
   * Invite user to room
   */
  async inviteUser(identityId: string, roomId: string, userMxid: string): Promise<void> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    await client.inviteUser(userMxid, roomId);
    console.log('[RoomManager] Invited user:', userMxid, 'to', roomId);
  }

  /**
   * Kick user from room
   */
  async kickUser(
    identityId: string,
    roomId: string,
    userMxid: string,
    reason?: string
  ): Promise<void> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    await client.kickUser(userMxid, roomId, reason);
    console.log('[RoomManager] Kicked user:', userMxid, 'from', roomId);
  }

  /**
   * Read room messages (paginated)
   */
  async readMessages(
    identityId: string,
    roomId: string,
    limit: number = 50
  ): Promise<MatrixEvent[]> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }

    // Use doRequest to get room messages
    const timeline = await client.doRequest('GET', `/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/messages`, {
      dir: 'b',
      limit: limit.toString()
    }) as { chunk: MatrixEvent[] };
    
    return timeline.chunk || [];
  }

  private createDMKey(mxid1: string, mxid2: string): string {
    return [mxid1, mxid2].sort().join('<->');
  }

  async listServerRooms(
    adminToken: string,
    homeserverUrl: string
  ): Promise<ServerRoomInfo[]> {
    const syncUrl = `${homeserverUrl}/_matrix/client/v3/sync?timeout=0`;
    
    const response = await fetch(syncUrl, {
      headers: { 'Authorization': `Bearer ${adminToken}` }
    });
    
    if (!response.ok) {
      throw new Error(`Failed to fetch server rooms: ${response.status}`);
    }
    
    const syncData = await response.json() as {
      rooms?: {
        join?: Record<string, {
          state?: { events?: Array<{ type: string; content: Record<string, unknown> }> }
        }>
      }
    };
    
    const joinedRooms = syncData.rooms?.join || {};
    const rooms: ServerRoomInfo[] = [];
    
    for (const [roomId, roomData] of Object.entries(joinedRooms)) {
      const stateEvents = roomData.state?.events || [];
      const nameEvent = stateEvents.find(e => e.type === 'm.room.name');
      const topicEvent = stateEvents.find(e => e.type === 'm.room.topic');
      const aliasEvent = stateEvents.find(e => e.type === 'm.room.canonical_alias');
      
      rooms.push({
        roomId,
        name: nameEvent?.content?.name as string | undefined,
        topic: topicEvent?.content?.topic as string | undefined,
        canonicalAlias: aliasEvent?.content?.alias as string | undefined
      });
    }
    
    return rooms;
  }

  async findRoomsByName(
    query: string,
    adminToken: string,
    homeserverUrl: string
  ): Promise<ServerRoomInfo[]> {
    const allRooms = await this.listServerRooms(adminToken, homeserverUrl);
    const lowerQuery = query.toLowerCase();
    
    return allRooms.filter(room => {
      const nameMatch = room.name?.toLowerCase().includes(lowerQuery);
      const aliasMatch = room.canonicalAlias?.toLowerCase().includes(lowerQuery);
      const topicMatch = room.topic?.toLowerCase().includes(lowerQuery);
      return nameMatch || aliasMatch || topicMatch;
    });
  }

  async getRoomMembersPublic(
    identityId: string,
    roomId: string
  ): Promise<Array<{ mxid: string; displayName?: string }>> {
    const client = await this.clientPool.getClientById(identityId);
    if (!client) {
      throw new Error(`Client not found for identity: ${identityId}`);
    }
    
    const members = await client.getJoinedRoomMembers(roomId);
    const memberDetails: Array<{ mxid: string; displayName?: string }> = [];
    
    for (const mxid of members) {
      try {
        const profile = await client.doRequest(
          'GET',
          `/_matrix/client/v3/profile/${encodeURIComponent(mxid)}`
        ) as { displayname?: string };
        memberDetails.push({ mxid, displayName: profile.displayname });
      } catch {
        memberDetails.push({ mxid });
      }
    }
    
    return memberDetails;
  }

  async getRoomMembersAdmin(
    roomId: string,
    adminToken: string,
    homeserverUrl: string
  ): Promise<Array<{ mxid: string; displayName?: string; membership: string }>> {
    const stateResponse = await fetch(
      `${homeserverUrl}/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/state`,
      {
        headers: {
          'Authorization': `Bearer ${adminToken}`,
          'Content-Type': 'application/json'
        }
      }
    );

    if (!stateResponse.ok) {
      const err = await stateResponse.text();
      throw new Error(`Failed to get room state: ${err}`);
    }

    const state = await stateResponse.json() as Array<{
      type: string;
      state_key?: string;
      content?: { membership?: string; displayname?: string };
    }>;

    const members: Array<{ mxid: string; displayName?: string; membership: string }> = [];

    for (const event of state) {
      if (event.type === 'm.room.member' && event.state_key && event.content?.membership) {
        members.push({
          mxid: event.state_key,
          displayName: event.content.displayname,
          membership: event.content.membership
        });
      }
    }

    return members;
  }
}
