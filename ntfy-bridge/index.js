/**
 * ntfy-to-PM Bridge
 * 
 * Subscribes to ntfy topics and forwards notifications to the PM agent
 * for triage and awareness of system failures.
 */

const NTFY_URL = process.env.NTFY_URL || 'http://ntfy:80';
const NTFY_TOPICS = (process.env.NTFY_TOPICS || 'alerts,failures,deploys').split(',').map(t => t.trim());
const BRIDGE_URL = process.env.BRIDGE_URL || 'http://matrix-messaging-mcp:3100';
const PM_AGENT = process.env.PM_AGENT || 'Huly - Matrix Synapse Deployment';
const CALLER_DIR = process.env.CALLER_DIR || '/opt/stacks/matrix-synapse-deployment';

async function forwardToPM(notification) {
  const { topic, title, message, priority, tags } = notification;
  
  const priorityEmoji = {
    5: '🚨', // max/urgent
    4: '⚠️', // high
    3: '📢', // default
    2: '📝', // low
    1: '💤', // min
  }[priority] || '📢';
  
  const formattedMessage = [
    `${priorityEmoji} **NTFY Alert** [${topic}]`,
    title ? `**${title}**` : null,
    message,
    tags?.length ? `Tags: ${tags.join(', ')}` : null,
  ].filter(Boolean).join('\n');
  
  try {
    const response = await fetch(`${BRIDGE_URL}/mcp/call`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'Accept': 'application/json',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: Date.now(),
        method: 'tools/call',
        params: {
          name: 'matrix_messaging',
          arguments: {
            operation: 'talk_to_agent',
            agent: PM_AGENT,
            message: formattedMessage,
            caller_directory: CALLER_DIR,
          },
        },
      }),
    });
    
    const result = await response.json();
    if (result.result?.content?.[0]?.text) {
      const parsed = JSON.parse(result.result.content[0].text);
      if (parsed.success) {
        console.log(`[ntfy-bridge] Forwarded to PM: ${topic} - ${title || message?.slice(0, 50)}`);
      } else {
        console.error(`[ntfy-bridge] Failed to forward: ${parsed.error}`);
      }
    } else if (result.error) {
      console.error(`[ntfy-bridge] MCP error: ${result.error.message}`);
    }
  } catch (error) {
    console.error(`[ntfy-bridge] Error forwarding to PM:`, error.message);
  }
}

async function subscribeToTopic(topic) {
  const url = `${NTFY_URL}/${topic}/sse`;
  console.log(`[ntfy-bridge] Subscribing to ${url}`);
  
  while (true) {
    try {
      const response = await fetch(url);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        
        for (const line of lines) {
          if (line.startsWith('data:')) {
            try {
              const data = JSON.parse(line.slice(5).trim());
              if (data.event === 'message') {
                console.log(`[ntfy-bridge] Received: ${topic} - ${data.title || data.message?.slice(0, 50)}`);
                await forwardToPM({
                  topic,
                  title: data.title,
                  message: data.message,
                  priority: data.priority,
                  tags: data.tags,
                });
              }
            } catch (e) {
              // Ignore parse errors for non-JSON lines
            }
          }
        }
      }
    } catch (error) {
      console.error(`[ntfy-bridge] Connection error for ${topic}:`, error.message);
    }
    
    console.log(`[ntfy-bridge] Reconnecting to ${topic} in 5s...`);
    await new Promise(r => setTimeout(r, 5000));
  }
}

async function main() {
  console.log(`[ntfy-bridge] Starting ntfy-to-PM bridge`);
  console.log(`[ntfy-bridge] NTFY URL: ${NTFY_URL}`);
  console.log(`[ntfy-bridge] Topics: ${NTFY_TOPICS.join(', ')}`);
  console.log(`[ntfy-bridge] PM Agent: ${PM_AGENT}`);
  
  // Subscribe to all topics in parallel
  await Promise.all(NTFY_TOPICS.map(topic => subscribeToTopic(topic)));
}

main().catch(console.error);
