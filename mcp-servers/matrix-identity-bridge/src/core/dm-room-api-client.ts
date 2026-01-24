import type { DMRoomMapping } from '../types/index.js';

interface DMRoomResponse {
  room_id: string;
  participant_1: string;
  participant_2: string;
  key: string;
  created_at: number;
  last_activity_at: number;
}

interface DMRoomListResponse {
  success: boolean;
  count: number;
  dm_rooms: DMRoomResponse[];
}

function apiResponseToMapping(resp: DMRoomResponse): DMRoomMapping {
  return {
    key: resp.key,
    roomId: resp.room_id,
    participants: [resp.participant_1, resp.participant_2] as [string, string],
    createdAt: resp.created_at,
    lastActivityAt: resp.last_activity_at
  };
}

export class DMRoomApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = process.env.MATRIX_API_URL || 'http://matrix-api:8000') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  private async fetch<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      ...options.headers
    };

    const response = await fetch(url, { ...options, headers });
    
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`API error ${response.status}: ${text}`);
    }

    return response.json() as Promise<T>;
  }

  async getDMRoom(mxid1: string, mxid2: string): Promise<DMRoomMapping | undefined> {
    try {
      const params = new URLSearchParams({ mxid1, mxid2 });
      const resp = await this.fetch<DMRoomResponse>(`/api/v1/dm-rooms/lookup?${params}`);
      return apiResponseToMapping(resp);
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      if (errMsg.includes('404')) {
        return undefined;
      }
      console.error('[DMRoomApiClient] getDMRoom error:', errMsg);
      return undefined;
    }
  }

  async getDMRoomByRoomId(roomId: string): Promise<DMRoomMapping | undefined> {
    try {
      const resp = await this.fetch<DMRoomResponse>(`/api/v1/dm-rooms/by-room-id/${encodeURIComponent(roomId)}`);
      return apiResponseToMapping(resp);
    } catch (error: unknown) {
      const errMsg = error instanceof Error ? error.message : String(error);
      if (errMsg.includes('404')) {
        return undefined;
      }
      console.error('[DMRoomApiClient] getDMRoomByRoomId error:', errMsg);
      return undefined;
    }
  }

  async getAllDMRooms(): Promise<DMRoomMapping[]> {
    try {
      const resp = await this.fetch<DMRoomListResponse>('/api/v1/dm-rooms');
      return resp.dm_rooms.map(apiResponseToMapping);
    } catch (error: unknown) {
      console.error('[DMRoomApiClient] getAllDMRooms error:', error instanceof Error ? error.message : String(error));
      return [];
    }
  }

  async getDMRoomsForUser(mxid: string): Promise<DMRoomMapping[]> {
    try {
      const params = new URLSearchParams({ user_mxid: mxid });
      const resp = await this.fetch<DMRoomListResponse>(`/api/v1/dm-rooms?${params}`);
      return resp.dm_rooms.map(apiResponseToMapping);
    } catch (error: unknown) {
      console.error('[DMRoomApiClient] getDMRoomsForUser error:', error instanceof Error ? error.message : String(error));
      return [];
    }
  }

  async saveDMRoom(mapping: DMRoomMapping): Promise<boolean> {
    try {
      const [p1, p2] = mapping.participants.sort();
      await this.fetch('/api/v1/dm-rooms', {
        method: 'POST',
        body: JSON.stringify({
          room_id: mapping.roomId,
          mxid1: p1,
          mxid2: p2
        })
      });
      return true;
    } catch (error: unknown) {
      console.error('[DMRoomApiClient] saveDMRoom error:', error instanceof Error ? error.message : String(error));
      return false;
    }
  }

  async updateActivity(mxid1: string, mxid2: string): Promise<void> {
    // The API updates last_activity_at on lookup/create, so just touch it
    await this.getDMRoom(mxid1, mxid2);
  }
}

let _apiClient: DMRoomApiClient | null = null;

export function getDMRoomApiClient(): DMRoomApiClient {
  if (!_apiClient) {
    _apiClient = new DMRoomApiClient();
  }
  return _apiClient;
}
