export interface LettaAgent {
  id: string;
  name: string;
  created_at?: string;
  updated_at?: string;
}

interface LettaAgentsEnvelope {
  agents?: LettaAgent[];
  data?: LettaAgent[];
}

export class LettaClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
  ) {}

  async listAgents(limit = 200): Promise<LettaAgent[]> {
    const url = new URL(`${this.baseUrl}/v1/agents`);
    url.searchParams.set('limit', String(limit));
    const response = await fetch(url, {
      headers: this.authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Letta list agents failed: ${response.status} ${await response.text()}`);
    }

    const payload = (await response.json()) as LettaAgent[] | LettaAgentsEnvelope;
    if (Array.isArray(payload)) {
      return payload;
    }
    if (Array.isArray(payload.agents)) {
      return payload.agents;
    }
    if (Array.isArray(payload.data)) {
      return payload.data;
    }
    throw new Error('Letta list agents returned unexpected payload shape');
  }

  async getAgent(agentId: string): Promise<LettaAgent> {
    const response = await fetch(`${this.baseUrl}/v1/agents/${encodeURIComponent(agentId)}`, {
      headers: this.authHeaders(),
    });
    if (!response.ok) {
      throw new Error(`Letta get agent failed: ${response.status} ${await response.text()}`);
    }
    return (await response.json()) as LettaAgent;
  }

  private authHeaders(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.token}`,
    };
  }
}
