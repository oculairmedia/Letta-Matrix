/**
 * Agent Mapping API Client
 * 
 * Talks to the matrix-api Python service which owns the PostgreSQL database.
 * This is the SINGLE SOURCE OF TRUTH for agent ↔ Matrix user ↔ room mappings.
 * 
 * Replaces the old agent-mapping-client.ts (HTTP) + agent-mapping-store.ts (wrapper)
 * + agent_user_mappings.json (disk file) approach.
 */

export interface AgentMappingRecord {
  agent_id: string;
  agent_name: string;
  matrix_user_id: string;
  matrix_password: string;
  room_id: string | null;
  room_created: boolean;
  invitation_status?: Record<string, string>;
}

interface ApiResponse<T = unknown> {
  success: boolean;
  message?: string;
  mapping?: T;
  mappings?: Record<string, T>;
  agent_id?: string;
  agent_name?: string;
  room_id?: string;
  matrix_user_id?: string;
  room_created?: boolean;
  invitation_status?: Record<string, string>;
}

export class AgentMappingApiClient {
  private baseUrl: string;

  constructor(baseUrl?: string) {
    this.baseUrl = (baseUrl || process.env.MATRIX_API_URL || 'http://matrix-api:8000').replace(/\/$/, '');
  }

  /**
   * Get agent mapping by agent ID (read from DB via REST)
   */
  async getByAgentId(agentId: string): Promise<AgentMappingRecord | null> {
    try {
      const response = await fetch(`${this.baseUrl}/agents/${agentId}/room`);
      if (!response.ok) {
        if (response.status === 404) return null;
        return null;
      }

      const data = await response.json() as ApiResponse;
      if (data.success === false) return null;

      return {
        agent_id: data.agent_id || agentId,
        agent_name: data.agent_name || '',
        matrix_user_id: data.matrix_user_id || '',
        matrix_password: '', // GET endpoint doesn't return password for security
        room_id: data.room_id || null,
        room_created: data.room_created ?? false,
        invitation_status: data.invitation_status,
      };
    } catch (error) {
      console.error('[AgentMappingApi] getByAgentId failed:', error);
      return null;
    }
  }

  /**
   * Get all agent mappings (read from DB via REST)
   */
  async getAll(): Promise<Record<string, AgentMappingRecord>> {
    try {
      const response = await fetch(`${this.baseUrl}/agents/mappings`);
      if (!response.ok) return {};

      const data = await response.json() as ApiResponse<AgentMappingRecord>;
      if (data.success === false || !data.mappings) return {};

      return data.mappings as Record<string, AgentMappingRecord>;
    } catch (error) {
      console.error('[AgentMappingApi] getAll failed:', error);
      return {};
    }
  }

  /**
   * Create or update an agent mapping (upsert — full write to DB)
   */
  async upsert(mapping: {
    agent_id: string;
    agent_name: string;
    matrix_user_id: string;
    matrix_password: string;
    room_id?: string | null;
    room_created?: boolean;
  }): Promise<AgentMappingRecord | null> {
    try {
      const response = await fetch(`${this.baseUrl}/agents/${mapping.agent_id}/mapping`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_name: mapping.agent_name,
          matrix_user_id: mapping.matrix_user_id,
          matrix_password: mapping.matrix_password,
          room_id: mapping.room_id || null,
          room_created: mapping.room_created ?? false,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error('[AgentMappingApi] upsert failed:', response.status, errorText);
        return null;
      }

      const data = await response.json() as ApiResponse<AgentMappingRecord>;
      return (data.mapping as AgentMappingRecord) || null;
    } catch (error) {
      console.error('[AgentMappingApi] upsert failed:', error);
      return null;
    }
  }

  /**
   * Partially update an agent mapping (e.g., sync password after reset, set room_id)
   */
  async update(agentId: string, fields: {
    agent_name?: string;
    matrix_user_id?: string;
    matrix_password?: string;
    room_id?: string;
    room_created?: boolean;
  }): Promise<AgentMappingRecord | null> {
    try {
      const response = await fetch(`${this.baseUrl}/agents/${agentId}/mapping`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error('[AgentMappingApi] update failed:', response.status, errorText);
        return null;
      }

      const data = await response.json() as ApiResponse<AgentMappingRecord>;
      return (data.mapping as AgentMappingRecord) || null;
    } catch (error) {
      console.error('[AgentMappingApi] update failed:', error);
      return null;
    }
  }
}

// Singleton instance
let _instance: AgentMappingApiClient | null = null;

export function getAgentMappingApi(): AgentMappingApiClient {
  if (!_instance) {
    _instance = new AgentMappingApiClient();
  }
  return _instance;
}
