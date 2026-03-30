import { describe, expect, it, jest } from '@jest/globals';

import { resolveTalkToOpenCodeRoomId } from '../src/core/opencode-rooms.js';
import type { MatrixIdentity } from '../src/types/index.js';
import type { ToolContext } from '../src/core/tool-context.js';

const makeIdentity = (id: string, mxid: string): MatrixIdentity => ({
  id,
  mxid,
  displayName: id,
  accessToken: 'token',
  type: 'opencode',
  createdAt: 1,
  lastUsedAt: 1,
});

describe('resolveTalkToOpenCodeRoomId', () => {
  it('prefers an explicit room id and updates bridge registration', async () => {
    const toolContext = {} as ToolContext;
    const getOrCreateRoom = jest.fn<typeof import('../src/core/opencode-rooms.js').getOrCreateOpenCodeRoom>();
    const updateRegistration = jest.fn<typeof import('../src/core/opencode-rooms.js').updateBridgeRegistration>(async () => undefined);

    const roomId = await resolveTalkToOpenCodeRoomId(
      '!explicit:matrix.test',
      {
        directory: '/opt/stacks/example',
        identity: makeIdentity('opencode-example', '@oc_example:matrix.test'),
        rooms: ['!existing:matrix.test'],
      },
      makeIdentity('caller', '@caller:matrix.test'),
      toolContext,
      { getOrCreateRoom, updateRegistration },
    );

    expect(roomId).toBe('!explicit:matrix.test');
    expect(updateRegistration).toHaveBeenCalledWith('/opt/stacks/example', '!explicit:matrix.test');
    expect(getOrCreateRoom).not.toHaveBeenCalled();
  });

  it('uses the registered instance room when no explicit room id is provided', async () => {
    const toolContext = {} as ToolContext;
    const getOrCreateRoom = jest.fn<typeof import('../src/core/opencode-rooms.js').getOrCreateOpenCodeRoom>();
    const updateRegistration = jest.fn<typeof import('../src/core/opencode-rooms.js').updateBridgeRegistration>(async () => undefined);

    const roomId = await resolveTalkToOpenCodeRoomId(
      undefined,
      {
        directory: '/opt/stacks/example',
        identity: makeIdentity('opencode-example', '@oc_example:matrix.test'),
        rooms: ['!existing:matrix.test'],
      },
      makeIdentity('caller', '@caller:matrix.test'),
      toolContext,
      { getOrCreateRoom, updateRegistration },
    );

    expect(roomId).toBe('!existing:matrix.test');
    expect(updateRegistration).not.toHaveBeenCalled();
    expect(getOrCreateRoom).not.toHaveBeenCalled();
  });

  it('creates and registers a room when the instance has no rooms', async () => {
    const toolContext = {} as ToolContext;
    const getOrCreateRoom = jest.fn<typeof import('../src/core/opencode-rooms.js').getOrCreateOpenCodeRoom>(async () => '!created:matrix.test');
    const updateRegistration = jest.fn<typeof import('../src/core/opencode-rooms.js').updateBridgeRegistration>(async () => undefined);

    const roomId = await resolveTalkToOpenCodeRoomId(
      undefined,
      {
        directory: '/opt/stacks/example',
        identity: makeIdentity('opencode-example', '@oc_example:matrix.test'),
        rooms: [],
      },
      makeIdentity('caller', '@caller:matrix.test'),
      toolContext,
      { getOrCreateRoom, updateRegistration },
    );

    expect(roomId).toBe('!created:matrix.test');
    expect(getOrCreateRoom).toHaveBeenCalledWith(
      '/opt/stacks/example',
      expect.objectContaining({ mxid: '@oc_example:matrix.test' }),
      expect.objectContaining({ mxid: '@caller:matrix.test' }),
      toolContext,
    );
    expect(updateRegistration).toHaveBeenCalledWith('/opt/stacks/example', '!created:matrix.test');
  });
});
