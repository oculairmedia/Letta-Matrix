import { jest } from '@jest/globals';

import { LettaService } from '../src/letta/letta-service.js';

describe('LettaService name reconciliation', () => {
  afterEach(() => {
    jest.restoreAllMocks();
    jest.useRealTimers();
  });

  it('updates identity and agent mapping when Letta name changes', async () => {
    const getAllIdentitiesAsync = jest.fn(async () => [
      {
        id: 'letta_agent-123',
        mxid: '@agent_123:matrix.test',
        displayName: 'Old Name',
        avatarUrl: 'mxc://avatar',
        accessToken: 'token',
        type: 'letta' as const,
        createdAt: Date.now(),
        lastUsedAt: Date.now(),
      },
    ]);
    const storage = {
      getAllIdentitiesAsync,
    };
    const updateIdentity = jest.fn(async () => undefined);
    const identityManager = {
      updateIdentity,
    };
    const service = new LettaService(
      { baseUrl: 'http://letta.test' },
      storage as never,
      identityManager as never,
    );

    jest.spyOn(service, 'listAgents').mockResolvedValue([
      { id: 'agent-123', name: 'New Name' },
    ]);

    const fetchMock = jest.fn(async () => new Response(
      JSON.stringify({ success: true, mapping: {} }),
      {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      },
    ));
    global.fetch = fetchMock as typeof fetch;

    const summary = await service.reconcileAgentDisplayNames();

    expect(summary).toEqual({
      checked: 1,
      updated: 1,
      missingIdentity: 0,
      failed: 0,
    });
    expect(updateIdentity).toHaveBeenCalledWith(
      'letta_agent-123',
      'New Name',
      'mxc://avatar',
    );
    expect(fetchMock).toHaveBeenCalledWith(
      'http://matrix-api:8000/agents/agent-123/mapping',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ agent_name: 'New Name' }),
      }),
    );
  });

  it('tracks missing identities without attempting updates', async () => {
    const getAllIdentitiesAsync = jest.fn(async () => []);
    const storage = {
      getAllIdentitiesAsync,
    };
    const updateIdentity = jest.fn(async () => undefined);
    const identityManager = {
      updateIdentity,
    };
    const service = new LettaService(
      { baseUrl: 'http://letta.test' },
      storage as never,
      identityManager as never,
    );

    jest.spyOn(service, 'listAgents').mockResolvedValue([
      { id: 'agent-123', name: 'Only Agent' },
    ]);

    const fetchMock = jest.fn(async () => new Response(null, { status: 200 }));
    global.fetch = fetchMock as typeof fetch;

    const summary = await service.reconcileAgentDisplayNames();

    expect(summary).toEqual({
      checked: 1,
      updated: 0,
      missingIdentity: 1,
      failed: 0,
    });
    expect(updateIdentity).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('starts and stops a reconciliation timer once', () => {
    jest.useFakeTimers();

    const getAllIdentitiesAsync = jest.fn(async () => []);
    const storage = {
      getAllIdentitiesAsync,
    };
    const updateIdentity = jest.fn(async () => undefined);
    const identityManager = {
      updateIdentity,
    };
    const service = new LettaService(
      { baseUrl: 'http://letta.test' },
      storage as never,
      identityManager as never,
    );

    const reconcileSpy = jest
      .spyOn(service, 'reconcileAgentDisplayNames')
      .mockResolvedValue({ checked: 0, updated: 0, missingIdentity: 0, failed: 0 });

    service.start();
    service.start();

    expect(reconcileSpy).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(300000);
    expect(reconcileSpy).toHaveBeenCalledTimes(2);

    service.stop();
    jest.advanceTimersByTime(300000);
    expect(reconcileSpy).toHaveBeenCalledTimes(2);
  });
});
