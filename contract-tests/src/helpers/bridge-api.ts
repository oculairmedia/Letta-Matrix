export interface BridgeHealthResponse {
  status: string;
  authenticated: boolean;
  timestamp: string;
  agent_sync_available: boolean;
}

export interface AgentProvisioningHealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'error' | 'unavailable';
  total_agents?: number;
  agents_with_rooms?: number;
  agents_missing_rooms?: string[];
  missing_count?: number;
  timestamp: string;
  message?: string;
}

export interface AgentMapping {
  agent_id: string;
  agent_name: string;
  matrix_user_id: string;
  matrix_password: string;
  room_id: string | null;
  room_created: boolean;
  created: boolean;
  invitation_status: Record<string, string>;
}

export interface AgentMappingsResponse {
  success: boolean;
  message: string;
  mappings: Record<string, AgentMapping>;
}

export interface AgentRoomResponse {
  success: boolean;
  agent_id: string;
  agent_name: string;
  room_id: string;
  matrix_user_id: string;
  room_created: boolean;
  invitation_status: Record<string, string>;
}

export interface NewAgentWebhookRequest {
  agent_id: string;
  timestamp: string;
}

export interface GenericBridgeResponse {
  success: boolean;
  message?: string;
  timestamp?: string;
  error?: string;
  event_type?: string;
  agent_id?: string;
}

export interface ConversationRegistrationRequest {
  agent_id: string;
  matrix_event_id?: string;
  matrix_room_id?: string;
  opencode_sender?: string;
}

export interface ConversationRegistrationResponse {
  success: boolean;
  agent_id: string;
  opencode_sender: string | null;
}

export interface HttpResult<T> {
  status: number;
  body: T;
}

export class BridgeApiClient {
  constructor(private readonly baseUrl: string) {}

  async health(): Promise<BridgeHealthResponse> {
    return this.fetchJson<BridgeHealthResponse>('/health');
  }

  async agentProvisioningHealth(): Promise<AgentProvisioningHealthResponse> {
    return this.fetchJson<AgentProvisioningHealthResponse>('/health/agent-provisioning');
  }

  async getMappings(): Promise<AgentMappingsResponse> {
    return this.fetchJson<AgentMappingsResponse>('/agents/mappings');
  }

  async getAgentRoom(agentId: string): Promise<HttpResult<AgentRoomResponse | GenericBridgeResponse>> {
    return this.fetchWithStatus<AgentRoomResponse | GenericBridgeResponse>(`/agents/${encodeURIComponent(agentId)}/room`);
  }

  async triggerNewAgentWebhook(payload: NewAgentWebhookRequest): Promise<HttpResult<GenericBridgeResponse>> {
    return this.fetchWithStatus<GenericBridgeResponse>('/webhook/new-agent', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
  }

  async postLettaWebhook(payload: Record<string, unknown>, signature?: string): Promise<HttpResult<GenericBridgeResponse>> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (signature) {
      headers['X-Letta-Signature'] = signature;
    }
    return this.fetchWithStatus<GenericBridgeResponse>('/webhooks/letta/agent-response', {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });
  }

  async registerConversation(payload: ConversationRegistrationRequest): Promise<HttpResult<ConversationRegistrationResponse | GenericBridgeResponse>> {
    return this.fetchWithStatus<ConversationRegistrationResponse | GenericBridgeResponse>('/conversations/register', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
  }

  private async fetchJson<T>(path: string): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`);
    if (!response.ok) {
      throw new Error(`Bridge API call failed for ${path}: ${response.status} ${await response.text()}`);
    }
    return (await response.json()) as T;
  }

  private async fetchWithStatus<T>(path: string, init?: RequestInit): Promise<HttpResult<T>> {
    const response = await fetch(`${this.baseUrl}${path}`, init);
    return {
      status: response.status,
      body: (await response.json()) as T,
    };
  }
}
