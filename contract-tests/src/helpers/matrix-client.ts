export interface MatrixLoginResponse {
  access_token: string;
  device_id: string;
  user_id: string;
  well_known?: Record<string, unknown>;
  home_server?: string;
}

export interface MatrixSendResponse {
  event_id: string;
}

export interface MatrixEvent {
  type: string;
  sender: string;
  event_id: string;
  origin_server_ts: number;
  content: Record<string, unknown>;
}

export interface MatrixMessagesResponse {
  chunk: MatrixEvent[];
  start?: string;
  end?: string;
}

export interface MatrixRoomStateEvent {
  type: string;
  state_key: string;
  sender: string;
  event_id: string;
  content: Record<string, unknown>;
}

export interface MatrixJoinedMembersResponse {
  joined: Record<string, { display_name?: string; avatar_url?: string }>;
}

export interface MatrixWhoAmIResponse {
  user_id: string;
  device_id?: string;
  is_guest?: boolean;
}

export class MatrixClient {
  private accessToken: string | null;

  constructor(
    private readonly homeserverUrl: string,
    accessToken?: string,
  ) {
    this.accessToken = accessToken ?? null;
  }

  setAccessToken(accessToken: string): void {
    this.accessToken = accessToken;
  }

  getAccessToken(): string | null {
    return this.accessToken;
  }

  async login(userId: string, password: string, deviceName = 'contract-tests'): Promise<MatrixLoginResponse> {
    const body = {
      type: 'm.login.password',
      identifier: {
        type: 'm.id.user',
        user: userId,
      },
      password,
      initial_device_display_name: deviceName,
    };

    const response = await fetch(`${this.homeserverUrl}/_matrix/client/v3/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      throw new Error(`Matrix login failed: ${response.status} ${await response.text()}`);
    }

    const data = (await response.json()) as MatrixLoginResponse;
    this.accessToken = data.access_token;
    return data;
  }

  async whoAmI(): Promise<MatrixWhoAmIResponse> {
    const response = await fetch(`${this.homeserverUrl}/_matrix/client/v3/account/whoami`, {
      headers: this.authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Matrix whoami failed: ${response.status} ${await response.text()}`);
    }
    return (await response.json()) as MatrixWhoAmIResponse;
  }

  async sendTextMessage(roomId: string, body: string, extraContent?: Record<string, unknown>): Promise<MatrixSendResponse> {
    return this.sendRoomEvent(roomId, {
      msgtype: 'm.text',
      body,
      ...extraContent,
    });
  }

  async sendRoomEvent(roomId: string, content: Record<string, unknown>, eventType = 'm.room.message'): Promise<MatrixSendResponse> {
    const txnId = `contract-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const encodedRoomId = encodeURIComponent(roomId);
    const encodedEventType = encodeURIComponent(eventType);
    const endpoint = `${this.homeserverUrl}/_matrix/client/v3/rooms/${encodedRoomId}/send/${encodedEventType}/${txnId}`;

    const response = await fetch(endpoint, {
      method: 'PUT',
      headers: {
        ...this.authHeaders(),
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(content),
    });

    if (!response.ok) {
      throw new Error(`Matrix send event failed: ${response.status} ${await response.text()}`);
    }

    return (await response.json()) as MatrixSendResponse;
  }

  async getRoomMessages(roomId: string, direction: 'b' | 'f' = 'b', limit = 30, from?: string): Promise<MatrixMessagesResponse> {
    const encodedRoomId = encodeURIComponent(roomId);
    const url = new URL(`${this.homeserverUrl}/_matrix/client/v3/rooms/${encodedRoomId}/messages`);
    url.searchParams.set('dir', direction);
    url.searchParams.set('limit', String(limit));
    if (from) {
      url.searchParams.set('from', from);
    }

    const response = await fetch(url, {
      headers: this.authHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Matrix get messages failed: ${response.status} ${await response.text()}`);
    }

    return (await response.json()) as MatrixMessagesResponse;
  }

  async getRoomState(roomId: string): Promise<MatrixRoomStateEvent[]> {
    const encodedRoomId = encodeURIComponent(roomId);
    const response = await fetch(`${this.homeserverUrl}/_matrix/client/v3/rooms/${encodedRoomId}/state`, {
      headers: this.authHeaders(),
    });

    if (!response.ok) {
      throw new Error(`Matrix get room state failed: ${response.status} ${await response.text()}`);
    }

    return (await response.json()) as MatrixRoomStateEvent[];
  }

  async getJoinedMembers(roomId: string): Promise<MatrixJoinedMembersResponse> {
    const encodedRoomId = encodeURIComponent(roomId);
    const response = await fetch(`${this.homeserverUrl}/_matrix/client/v3/rooms/${encodedRoomId}/joined_members`, {
      headers: this.authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Matrix joined members failed: ${response.status} ${await response.text()}`);
    }
    return (await response.json()) as MatrixJoinedMembersResponse;
  }

  private authHeaders(): Record<string, string> {
    if (!this.accessToken) {
      throw new Error('Matrix access token is not set');
    }
    return {
      Authorization: `Bearer ${this.accessToken}`,
    };
  }
}
