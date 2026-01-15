import { describe, it, expect, beforeEach, afterEach, jest } from '@jest/globals';
import { AgentMappingStore } from '../src/core/agent-mapping-store.js';

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = jest.fn() as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  jest.restoreAllMocks();
});

describe('AgentMappingStore', () => {
  it('uses API mapping for agent lookups', async () => {
    const fetchMock = globalThis.fetch as unknown as { mockResolvedValue: (value: unknown) => void };
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        room_id: '!apiroom:matrix.test',
        agent_id: 'agent-1'
      })
    });

    const store = new AgentMappingStore({
      apiUrl: 'http://matrix-api:8000'
    });

    const mapping = await store.getMappingByAgentId('agent-1');

    expect(mapping?.room_id).toBe('!apiroom:matrix.test');
  });

  it('returns null when API has no mapping', async () => {
    const fetchMock = globalThis.fetch as unknown as { mockResolvedValue: (value: unknown) => void };
    fetchMock.mockResolvedValue({ ok: false });

    const store = new AgentMappingStore({
      apiUrl: 'http://matrix-api:8000'
    });

    const mapping = await store.getMappingByAgentId('agent-2');

    expect(mapping).toBeNull();
  });
});
