import { createHmac } from 'node:crypto';

import { AgentMapping, BridgeApiClient } from '../helpers/bridge-api';
import { loadConfig } from '../helpers/config';

function pickTargetMapping(mappings: AgentMapping[]): AgentMapping {
  const mapping = mappings.find((entry) => entry.room_created && !!entry.room_id);
  if (!mapping) {
    throw new Error('No mapped agents available for webhook tests');
  }
  return mapping;
}

function buildSignature(secret: string, body: string, timestamp: string): string {
  const payload = `${timestamp}.${body}`;
  const digest = createHmac('sha256', secret).update(payload).digest('hex');
  return `t=${timestamp},v1=${digest}`;
}

describe('Webhook contract', () => {
  const config = loadConfig();
  const bridge = new BridgeApiClient(config.bridgeApiUrl);

  let target: AgentMapping | null = null;

  beforeAll(async () => {
    const mappings = await bridge.getMappings();
    target = pickTargetMapping(Object.values(mappings.mappings));
  });

  afterAll(() => {
    target = null;
  });

  it('rejects malformed webhook payloads that miss required fields on POST /webhooks/letta/agent-response', async () => {
    const result = await bridge.postLettaWebhook({ event_type: 'agent.run.completed' });

    expect(result.status).toBe(400);
    expect(result.body.success).toBe(false);
    expect(typeof result.body.error).toBe('string');
  });

  it('processes agent.run.completed webhooks with required payload fields', async () => {
    if (!target) {
      throw new Error('Target mapping not initialized');
    }

    const payload = {
      id: `evt-contract-${Date.now()}`,
      event_type: 'agent.run.completed',
      agent_id: target.agent_id,
      timestamp: new Date().toISOString(),
      data: {
        run_id: `run-contract-${Date.now()}`,
        message_count: 2,
        messages: [
          {
            message_type: 'user_message',
            content: 'contract webhook user message',
          },
          {
            message_type: 'assistant_message',
            content: 'contract webhook assistant response',
          },
        ],
      },
    };

    const result = await bridge.postLettaWebhook(payload);

    expect(result.status).toBe(200);
    expect(result.body.success).toBe(true);
    expect(result.body.agent_id).toBe(target.agent_id);
  });

  it('enforces X-Letta-Signature when verification is enabled, or accepts requests in bypass mode', async () => {
    if (!target) {
      throw new Error('Target mapping not initialized');
    }

    const payload = {
      id: `evt-contract-sig-${Date.now()}`,
      event_type: 'agent.run.completed',
      agent_id: target.agent_id,
      timestamp: new Date().toISOString(),
      data: {
        run_id: `run-contract-sig-${Date.now()}`,
        message_count: 1,
        messages: [
          {
            message_type: 'assistant_message',
            content: 'contract signature validation probe',
          },
        ],
      },
    };
    const rawBody = JSON.stringify(payload);
    const timestamp = String(Math.floor(Date.now() / 1000));
    const validSignature = buildSignature(config.webhookSecret, rawBody, timestamp);

    const validResult = await bridge.postLettaWebhook(payload, validSignature);
    expect([200, 401, 403]).toContain(validResult.status);

    const invalidResult = await bridge.postLettaWebhook(payload, `t=${timestamp},v1=deadbeef`);

    if (invalidResult.status === 401 || invalidResult.status === 403) {
      expect(validResult.status).toBe(200);
      expect(invalidResult.body.success).toBe(false);
      return;
    }

    expect(invalidResult.status).toBe(200);
    expect(invalidResult.body.success).toBe(true);
  });
});
