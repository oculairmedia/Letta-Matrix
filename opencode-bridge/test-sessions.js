import { createOpencodeClient } from '@opencode-ai/sdk';

const client = createOpencodeClient({
  baseUrl: 'http://127.0.0.1:33069'
});

try {
  const sessions = await client.session.list();
  console.log('Sessions:', JSON.stringify(sessions, null, 2));
} catch (error) {
  console.error('Error:', error.message);
}
