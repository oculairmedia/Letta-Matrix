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
    throw new Error('No mapped room available for streaming test');
  }
  return fallback;
}

function findMessageByEventId(events: MatrixEvent[], eventId: string): MatrixEvent | undefined {
  return events.find((event) => event.event_id === eventId);
}

function findAgentReplyAfterTimestamp(
  events: MatrixEvent[],
  agentUserId: string,
  thresholdTs: number,
): MatrixEvent | undefined {
  return events.find((event) => {
    if (event.sender !== agentUserId) {
      return false;
    }
    if (event.origin_server_ts <= thresholdTs) {
      return false;
    }
    const body = event.content.body;
    return typeof body === 'string' && body.trim().length > 0;
  });
}

describe('Streaming and delivery contract', () => {
  const config = loadConfig();
  const bridge = new BridgeApiClient(config.bridgeApiUrl);
  const matrix = new MatrixClient(config.matrixHomeserverUrl);

  let target: AgentMapping | null = null;

  beforeAll(async () => {
    await matrix.login(config.matrixAdminUserId, requireMatrixAdminPassword(config), 'contract-tests-streaming');
    const mappings = await bridge.getMappings();
    target = pickTargetMapping(Object.values(mappings.mappings));
  });

  afterAll(() => {
    target = null;
  });

  it('delivers assistant response to the agent room as the mapped agent Matrix user', async () => {
    if (!target?.room_id) {
      throw new Error('Target mapping has no room_id');
    }

    const prompt = `CONTRACT_STREAM_${Date.now()} Reply with a short acknowledgment.`;
    const sendResult = await matrix.sendTextMessage(target.room_id, prompt);
    const sentEventId = sendResult.event_id;

    let sentEventTs = Date.now();
    const sentEventDeadline = Date.now() + 20_000;
    while (Date.now() < sentEventDeadline) {
      const page = await matrix.getRoomMessages(target.room_id, 'b', 40);
      const sentEvent = findMessageByEventId(page.chunk, sentEventId);
      if (sentEvent) {
        sentEventTs = sentEvent.origin_server_ts;
        break;
      }
      await sleep(config.pollingIntervalMs);
    }

    let agentReply: MatrixEvent | undefined;
    const responseDeadline = Date.now() + Math.min(config.maxWaitMs, 90_000);
    while (Date.now() < responseDeadline) {
      const page = await matrix.getRoomMessages(target.room_id, 'b', 60);
      agentReply = findAgentReplyAfterTimestamp(page.chunk, target.matrix_user_id, sentEventTs);
      if (agentReply) {
        break;
      }
      await sleep(config.pollingIntervalMs);
    }

    expect(agentReply).toBeDefined();
    if (!agentReply) {
      return;
    }
    expect(agentReply.sender).toBe(target.matrix_user_id);
    expect(typeof agentReply.content.body).toBe('string');
    expect((agentReply.content.body as string).length).toBeGreaterThan(0);
  });
});
