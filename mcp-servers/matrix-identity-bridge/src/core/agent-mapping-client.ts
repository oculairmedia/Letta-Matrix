type AgentRoomResponse = {
  success?: boolean;
  agent_id?: string;
  agent_name?: string;
  room_id?: string;
  matrix_user_id?: string;
  room_created?: boolean;
  invitation_status?: Record<string, string>;
};

type AgentMappingsResponse = {
  success?: boolean;
  mappings?: Record<string, Record<string, unknown>>;
};

export class AgentMappingClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async getAgentRoom(agentId: string): Promise<AgentRoomResponse | null> {
    const response = await fetch(`${this.baseUrl}/agents/${agentId}/room`);
    if (!response.ok) {
      return null;
    }

    const data = (await response.json()) as AgentRoomResponse;
    if (data.success === false) {
      return null;
    }

    return data;
  }

  async getAllMappings(): Promise<Record<string, Record<string, unknown>>> {
    const response = await fetch(`${this.baseUrl}/agents/mappings`);
    if (!response.ok) {
      return {};
    }

    const data = (await response.json()) as AgentMappingsResponse;
    if (data.success === false || !data.mappings) {
      return {};
    }

    return data.mappings;
  }
}
