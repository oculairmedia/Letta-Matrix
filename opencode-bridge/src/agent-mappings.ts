type AgentMapping = {
  agent_id: string;
  agent_name: string;
  matrix_user_id: string;
  room_id: string;
};

export async function fetchAgentMappings(baseUrl: string, fetchFn: typeof fetch): Promise<Record<string, AgentMapping>> {
  const response = await fetchFn(`${baseUrl.replace(/\/$/, "")}/agents/mappings`);
  if (!response.ok) {
    throw new Error(`Failed to load agent mappings: ${response.status}`);
  }

  const data = (await response.json()) as { mappings?: Record<string, AgentMapping> };
  if (!data.mappings) {
    throw new Error("Failed to load agent mappings: missing mappings");
  }

  return data.mappings;
}
