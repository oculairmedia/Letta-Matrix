import { AgentMapping, BridgeApiClient } from '../helpers/bridge-api';
import { loadConfig, requireMatrixAdminPassword } from '../helpers/config';
import { MatrixClient, MatrixEvent } from '../helpers/matrix-client';

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pickTargetMapping(mappings: AgentMapping[]): AgentMapping {
  const preferred = mappings.find((mapping) => mapping.agent_name.toLowerCase() === 'bmo' && mapping.room_created && !!mapping.room_id);
  if (preferred) {
    return preferred;
  }

  const fallback = mappings.find((mapping) => mapping.room_created && !!mapping.room_id);
  if (!fallback) {
    throw new Error('No mapped room is available for message-routing tests');
  }
  return fallback;
}

function findAgentResponse(events: MatrixEvent[], agentUserId: string, token: string): MatrixEvent | undefined {
  return events.find((event) => {
    if (event.sender !== agentUserId) {
      return false;
    }
    const body = event.content.body;
    return typeof body === 'string' && body.includes(token);
  });
}

describe('Message routing contract', () => {
  const config = loadConfig();
  const bridge = new BridgeApiClient(config.bridgeApiUrl);
  const matrix = new MatrixClient(config.matrixHomeserverUrl);

  let target: AgentMapping | null = null;

  beforeAll(async () => {
    await matrix.login(config.matrixAdminUserId, requireMatrixAdminPassword(config), 'contract-tests-message-routing');
    const mappings = await bridge.getMappings();
    target = pickTargetMapping(Object.values(mappings.mappings));
  });

  afterAll(() => {
    target = null;
  });

  it('does not forward messages flagged m.bridge_originated to Letta for re-response', async () => {
    if (!target?.room_id) {
      throw new Error('Target mapping has no room_id');
    }

    const token = `contract-bridge-originated-${Date.now()}`;
    await matrix.sendTextMessage(target.room_id, token, { m_bridge_originated: true, 'm.bridge_originated': true });

    let agentReply: MatrixEvent | undefined;
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      const page = await matrix.getRoomMessages(target.room_id, 'b', 40);
      agentReply = findAgentResponse(page.chunk, target.matrix_user_id, token);
      if (agentReply) {
        break;
      }
      await sleep(2_000);
    }

    expect(agentReply).toBeUndefined();
  });

  it('does not forward messages flagged m.letta_historical to Letta for re-response', async () => {
    if (!target?.room_id) {
      throw new Error('Target mapping has no room_id');
    }

    const token = `contract-letta-historical-${Date.now()}`;
    await matrix.sendTextMessage(target.room_id, token, { m_letta_historical: true, 'm.letta_historical': true });

    let agentReply: MatrixEvent | undefined;
    const deadline = Date.now() + 25_000;
    while (Date.now() < deadline) {
      const page = await matrix.getRoomMessages(target.room_id, 'b', 40);
      agentReply = findAgentResponse(page.chunk, target.matrix_user_id, token);
      if (agentReply) {
        break;
      }
      await sleep(2_000);
    }

    expect(agentReply).toBeUndefined();
  });

  it('registers active conversation context with TTL tracking on POST /conversations/register', async () => {
    if (!target) {
      throw new Error('Target mapping not initialized');
    }

    const opencodeSender = '@oc_contract_tests:matrix.oculair.ca';
    const result = await bridge.registerConversation({
      agent_id: target.agent_id,
      matrix_room_id: target.room_id ?? undefined,
      matrix_event_id: `$contract-${Date.now()}`,
      opencode_sender: opencodeSender,
    });

    expect(result.status).toBe(200);
    expect(result.body.success).toBe(true);

    if ('agent_id' in result.body && 'opencode_sender' in result.body) {
      expect(result.body.agent_id).toBe(target.agent_id);
      expect(result.body.opencode_sender).toBe(opencodeSender);
    }
  });
});
