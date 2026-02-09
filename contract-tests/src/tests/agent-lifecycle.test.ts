import { AgentMapping, BridgeApiClient } from '../helpers/bridge-api';
import { loadConfig, requireMatrixAdminPassword } from '../helpers/config';
import { LettaClient } from '../helpers/letta-client';
import { MatrixClient } from '../helpers/matrix-client';

function pickMappingsForRoomStateChecks(mappings: AgentMapping[], size: number): AgentMapping[] {
  return mappings.filter((mapping) => mapping.room_created && !!mapping.room_id).slice(0, size);
}

describe('Agent lifecycle contract', () => {
  const config = loadConfig();
  const bridge = new BridgeApiClient(config.bridgeApiUrl);
  const letta = new LettaClient(config.lettaApiUrl, config.lettaToken);
  const matrix = new MatrixClient(config.matrixHomeserverUrl);

  let mappings: AgentMapping[] = [];
  let lettaAgentIds = new Set<string>();

  beforeAll(async () => {
    const mappingResponse = await bridge.getMappings();
    mappings = Object.values(mappingResponse.mappings);

    const lettaAgents = await letta.listAgents(500);
    lettaAgentIds = new Set(lettaAgents.map((agent) => agent.id));

    await matrix.login(config.matrixAdminUserId, requireMatrixAdminPassword(config), 'contract-tests-agent-lifecycle');
  });

  afterAll(() => {
    mappings = [];
    lettaAgentIds = new Set<string>();
  });

  it('provisions agent mappings with required identity fields and room linkage', () => {
    expect(mappings.length).toBeGreaterThan(0);

    for (const mapping of mappings) {
      expect(mapping.agent_id.startsWith('agent-')).toBe(true);
      expect(mapping.agent_name.length).toBeGreaterThan(0);
      expect(mapping.matrix_user_id.startsWith('@agent_')).toBe(true);
      expect(mapping.matrix_user_id.endsWith(':matrix.oculair.ca')).toBe(true);

      if (mapping.room_created) {
        expect(mapping.room_id).toMatch(/^!.+:.+$/);
      }
    }
  });

  it('returns /agents/{agent_id}/room payload consistent with /agents/mappings', async () => {
    const sample = mappings.find((mapping) => mapping.room_created && !!mapping.room_id);
    expect(sample).toBeDefined();
    if (!sample) {
      return;
    }

    const roomResult = await bridge.getAgentRoom(sample.agent_id);
    expect(roomResult.status).toBe(200);
    expect(roomResult.body.success).toBe(true);

    if ('room_id' in roomResult.body && 'matrix_user_id' in roomResult.body) {
      expect(roomResult.body.room_id).toBe(sample.room_id);
      expect(roomResult.body.matrix_user_id).toBe(sample.matrix_user_id);
      expect(roomResult.body.room_created).toBe(true);
    }
  });

  it('maintains room naming contract {agent_name} - Letta Agent Chat for mapped rooms', async () => {
    const sampledMappings = pickMappingsForRoomStateChecks(mappings, 5);
    expect(sampledMappings.length).toBeGreaterThan(0);

    for (const mapping of sampledMappings) {
      if (!mapping.room_id) {
        continue;
      }
      const state = await matrix.getRoomState(mapping.room_id);
      const nameEvent = state.find((event) => event.type === 'm.room.name' && event.state_key === '');

      expect(nameEvent).toBeDefined();
      if (!nameEvent) {
        continue;
      }

      const expectedName = `${mapping.agent_name} - Letta Agent Chat`;
      const roomName = typeof nameEvent.content.name === 'string' ? nameEvent.content.name : '';
      expect(roomName).toBe(expectedName);
    }
  });

  it('keeps active mappings discoverable in Letta agent list', () => {
    const roomBackedMappings = mappings.filter((mapping) => mapping.room_created && !!mapping.room_id);
    const foundCount = roomBackedMappings.filter((mapping) => lettaAgentIds.has(mapping.agent_id)).length;
    const ratio = roomBackedMappings.length === 0 ? 0 : foundCount / roomBackedMappings.length;

    expect(foundCount).toBeGreaterThan(0);
    expect(ratio).toBeGreaterThanOrEqual(0.8);
  });
});
