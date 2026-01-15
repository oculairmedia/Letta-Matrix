import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fetchAgentMappings } from '../agent-mappings.js';

test('fetchAgentMappings returns mappings from API', async () => {
  const fetchMock: typeof fetch = async () => {
    return new Response(
      JSON.stringify({
        mappings: {
          'agent-1': {
            agent_id: 'agent-1',
            agent_name: 'Agent One',
            matrix_user_id: '@agent_1:matrix.test',
            room_id: '!room:matrix.test'
          }
        }
      }),
      { status: 200 }
    );
  };

  const mappings = await fetchAgentMappings('http://localhost:8000', fetchMock);

  assert.equal(mappings['agent-1'].room_id, '!room:matrix.test');
});

test('fetchAgentMappings throws when API fails', async () => {
  const fetchMock: typeof fetch = async () => {
    return new Response('', { status: 500 });
  };

  await assert.rejects(
    () => fetchAgentMappings('http://localhost:8000', fetchMock),
    /Failed to load agent mappings: 500/
  );
});
