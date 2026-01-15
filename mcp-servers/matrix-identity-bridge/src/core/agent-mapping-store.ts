import { AgentMappingClient } from './agent-mapping-client.js';

 type AgentMappingRecord = Record<string, unknown> & {
  room_id?: string;
  matrix_room_id?: string;
};

export class AgentMappingStore {
  private client: AgentMappingClient;

  constructor(options: { apiUrl: string }) {
    this.client = new AgentMappingClient(options.apiUrl);
  }

  async getMappingByAgentId(agentId: string): Promise<AgentMappingRecord | null> {
    const apiMapping = await this.client.getAgentRoom(agentId);
    if (apiMapping?.room_id) {
      return apiMapping as AgentMappingRecord;
    }

    return null;
  }
}
