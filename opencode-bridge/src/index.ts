/**
 * OpenCode Matrix Bridge
 * 
 * Syncs Matrix rooms and forwards messages to registered OpenCode instances.
 * 
 * Flow:
 * 1. OpenCode instances register via HTTP API (port, session, rooms to monitor)
 * 2. Bridge syncs Matrix rooms using a bot account
 * 3. When messages arrive, bridge forwards to registered OpenCode instances
 * 4. Uses OpenCode SDK to inject messages via session.prompt({ noReply: true })
 */

import 'dotenv/config';
import * as sdk from 'matrix-js-sdk';
import { createOpencodeClient } from '@opencode-ai/sdk';
import { createServer, IncomingMessage, ServerResponse } from 'http';
import { readFileSync, existsSync } from 'fs';

// Types
interface OpenCodeRegistration {
  id: string;
  port: number;
  hostname: string;
  sessionId: string;
  directory: string;
  rooms: string[];  // Room IDs to monitor
  registeredAt: number;
  lastSeen: number;
}

interface AgentMapping {
  agent_id: string;
  agent_name: string;
  matrix_user_id: string;
  room_id: string;
}

// Configuration
const config = {
  matrix: {
    homeserverUrl: process.env.MATRIX_HOMESERVER_URL || 'https://matrix.oculair.ca',
    accessToken: process.env.MATRIX_ACCESS_TOKEN || '',
  },
  bridge: {
    port: parseInt(process.env.BRIDGE_PORT || '3200'),
    agentMappingsPath: process.env.AGENT_MAPPINGS_PATH || '',
  },
  opencode: {
    defaultHost: process.env.OPENCODE_DEFAULT_HOST || '127.0.0.1',
  }
};

// State
const registrations = new Map<string, OpenCodeRegistration>();
const identityToRegistration = new Map<string, OpenCodeRegistration>();  // @oc_* MXID -> registration
let matrixClient: sdk.MatrixClient | null = null;
let agentMappings: Record<string, AgentMapping> = {};
let discoveryInterval: NodeJS.Timeout | null = null;

// Matrix server domain for identity derivation
const MATRIX_DOMAIN = process.env.MATRIX_DOMAIN || 'matrix.oculair.ca';

/**
 * Derive Matrix identity MXID from directory path
 * e.g., /opt/stacks/matrix-synapse-deployment -> @oc_matrix_synapse_deployment:matrix.oculair.ca
 */
function deriveMatrixIdentity(directory: string): string {
  const dirName = directory.split('/').filter(p => p).pop() || 'default';
  const localpart = `oc_${dirName.toLowerCase().replace(/[^a-z0-9]/g, '_')}`;
  return `@${localpart}:${MATRIX_DOMAIN}`;
}

/**
 * Extract @oc_* mentions from message body
 * Returns array of MXIDs that match OpenCode identity pattern
 */
function extractOpenCodeMentions(body: string): string[] {
  const mentionRegex = /@oc_[a-z0-9_]+:[a-z0-9._-]+/gi;
  const matches = body.match(mentionRegex) || [];
  return [...new Set(matches)];  // Deduplicate
}

// Load agent mappings
function loadAgentMappings(): void {
  if (config.bridge.agentMappingsPath && existsSync(config.bridge.agentMappingsPath)) {
    try {
      agentMappings = JSON.parse(readFileSync(config.bridge.agentMappingsPath, 'utf-8'));
      console.log(`[Bridge] Loaded ${Object.keys(agentMappings).length} agent mappings`);
    } catch (e) {
      console.error('[Bridge] Failed to load agent mappings:', e);
    }
  }
}

// Get agent info from room
function getAgentForRoom(roomId: string): AgentMapping | undefined {
  return Object.values(agentMappings).find(m => m.room_id === roomId);
}

// Discover all running OpenCode instances on the host
async function discoverAllOpenCodeInstances(): Promise<void> {
  try {
    const { execSync } = await import('child_process');
    
    // Find all opencode processes with their PIDs
    const psList = execSync("ps aux | grep opencode | grep -v grep | awk '{print $2}'", { encoding: 'utf-8' }).trim();
    if (!psList) {
      console.log('[Bridge] No OpenCode processes found');
      return;
    }
    
    const pids = psList.split('\n').filter(p => p);
    console.log(`[Bridge] Found ${pids.length} OpenCode process(es)`);
    
    for (const pid of pids) {
      try {
        // Get working directory for this PID
        const cwd = execSync(`lsof -p ${pid} 2>/dev/null | grep cwd | awk '{print $NF}'`, { encoding: 'utf-8' }).trim();
        
        // Get listening port for this PID
        const portResult = execSync(`ss -tlnp 2>/dev/null | grep "pid=${pid}" | grep -oP ':\\K\\d+' | head -1`, { encoding: 'utf-8' }).trim();
        const port = parseInt(portResult, 10);
        
        if (cwd && port && port > 0) {
          const id = `127.0.0.1:${port}:opencode-${pid}`;
          
          // Check if already registered with correct port
          const existing = registrations.get(id);
          if (existing) {
            existing.lastSeen = Date.now();
            continue;
          }
          
          // Create new registration
          const registration: OpenCodeRegistration = {
            id,
            port,
            hostname: '127.0.0.1',
            sessionId: `opencode-${pid}`,
            directory: cwd,
            rooms: [],
            registeredAt: Date.now(),
            lastSeen: Date.now(),
          };
          
          registrations.set(id, registration);
          console.log(`[Bridge] Auto-discovered OpenCode: ${id} (${cwd})`);
        }
      } catch (error) {
        console.error(`[Bridge] Failed to process PID ${pid}:`, error);
      }
    }
    
    // Clean up stale registrations (older than 60 seconds)
    const now = Date.now();
    for (const [id, reg] of registrations.entries()) {
      if (now - reg.lastSeen > 60000) {
        console.log(`[Bridge] Removing stale registration: ${id}`);
        registrations.delete(id);
      }
    }
  } catch (error) {
    console.error('[Bridge] Failed to discover OpenCode instances:', error);
  }
}

// Discover OpenCode port dynamically
async function discoverOpenCodePort(directory: string): Promise<number | null> {
  try {
    const { execSync } = await import('child_process');
    // Find opencode process listening port
    const result = execSync("ss -tlnp | grep opencode | grep -oP ':\\K\\d+' | head -1", { encoding: 'utf-8' }).trim();
    const port = parseInt(result, 10);
    if (port && port > 0) {
      console.log(`[Bridge] Discovered OpenCode port: ${port}`);
      return port;
    }
  } catch (error) {
    console.error('[Bridge] Failed to discover OpenCode port:', error);
  }
  return null;
}

// Get the most recently active session ID for a directory
async function getActiveSessionId(baseUrl: string, directory: string): Promise<string | null> {
  try {
    const client = createOpencodeClient({ baseUrl });
    const result = await client.session.list();
    
    if (!result.data) {
      return null;
    }
    
    // Find sessions for this directory, sorted by most recently updated
    const sessions = result.data
      .filter((s: any) => s.directory === directory)
      .sort((a: any, b: any) => b.time.updated - a.time.updated);
    
    if (sessions.length > 0) {
      return sessions[0].id;
    }
  } catch (error) {
    console.error('[Bridge] Failed to list sessions:', error);
  }
  return null;
}

// Forward message to OpenCode instance
async function forwardToOpenCode(
  registration: OpenCodeRegistration,
  roomId: string,
  sender: string,
  content: string,
  eventId: string,
  agentNameOverride?: string
): Promise<boolean> {
  try {
    let port = registration.port;
    let hostname = registration.hostname;
    
    // Try the registered port first
    let client = createOpencodeClient({
      baseUrl: `http://${hostname}:${port}`,
    });

    // Use override if provided, otherwise look up from room
    const agentName = agentNameOverride || getAgentForRoom(roomId)?.agent_name || 'Unknown Agent';
    
    // Format the message for injection
    const messageText = `[Message from ${agentName}]\n${content}`;

    try {
      // Get the actual active session ID for this directory
      const sessionId = await getActiveSessionId(`http://${hostname}:${port}`, registration.directory);
      
      if (!sessionId) {
        console.error(`[Bridge] No active session found for directory: ${registration.directory}`);
        return false;
      }
      
      // Send message to session - let the OpenCode agent see and respond to it
      // Using prompt WITHOUT noReply so the AI actually processes the message
      await client.session.prompt({
        path: { id: sessionId },
        body: {
          parts: [{ type: 'text', text: messageText }],
        },
      });

      console.log(`[Bridge] Forwarded message to OpenCode ${registration.id} (session ${sessionId}): ${content.substring(0, 50)}...`);
      return true;
    } catch (firstError) {
      // If registered port failed, try to discover the current port
      console.log(`[Bridge] Registered port ${port} failed, attempting discovery...`);
      const discoveredPort = await discoverOpenCodePort(registration.directory);
      
      if (discoveredPort && discoveredPort !== port) {
        console.log(`[Bridge] Retrying with discovered port ${discoveredPort}`);
        client = createOpencodeClient({
          baseUrl: `http://${hostname}:${discoveredPort}`,
        });
        
        // Get session ID for the discovered port
        const sessionId = await getActiveSessionId(`http://${hostname}:${discoveredPort}`, registration.directory);
        
        if (!sessionId) {
          console.error(`[Bridge] No active session found on discovered port`);
          throw firstError;
        }
        
        await client.session.prompt({
          path: { id: sessionId },
          body: {
            noReply: true,
            parts: [{ type: 'text', text: messageText }],
          },
        });
        
        // Update the registration with the new port
        registration.port = discoveredPort;
        registration.lastSeen = Date.now();
        
        console.log(`[Bridge] Forwarded message to OpenCode on discovered port ${discoveredPort} (session ${sessionId}): ${content.substring(0, 50)}...`);
        return true;
      }
      
      throw firstError;
    }
  } catch (error) {
    console.error(`[Bridge] Failed to forward to OpenCode ${registration.id}:`, error);
    return false;
  }
}

// Handle incoming Matrix message - forward ONLY if message @mentions an OpenCode identity
async function handleMatrixMessage(event: sdk.MatrixEvent, room: sdk.Room): Promise<void> {
  // Ignore our own messages
  if (event.getSender() === matrixClient?.getUserId()) return;
  
  // Only handle text messages
  if (event.getType() !== 'm.room.message') return;
  const content = event.getContent();
  if (content.msgtype !== 'm.text') return;

  const roomId = room.roomId;
  const sender = event.getSender() || 'unknown';
  const body = content.body || '';

  // Extract @oc_* mentions from the message
  const mentions = extractOpenCodeMentions(body);
  
  // If no OpenCode mentions, ignore this message entirely
  if (mentions.length === 0) {
    return;
  }

  console.log(`[Bridge] Message with @mention(s): ${mentions.join(', ')} from ${sender}`);

  // Forward to each mentioned OpenCode identity
  for (const mention of mentions) {
    const registration = identityToRegistration.get(mention);
    
    if (!registration) {
      console.log(`[Bridge] No registration found for ${mention}`);
      continue;
    }

    // Get sender's display name for better context
    const senderMember = room.getMember?.(sender);
    const senderName = senderMember?.name || getAgentForRoom(roomId)?.agent_name || sender;
    
    console.log(`[Bridge] Forwarding to ${registration.id} (${mention})`);
    
    try {
      await forwardToOpenCode(
        registration,
        roomId,
        sender,
        body,
        event.getId() || '',
        senderName
      );
    } catch (error) {
      console.error(`[Bridge] Failed to forward to ${registration.id}:`, error);
    }
  }
}

// Initialize Matrix client
async function initMatrix(): Promise<void> {
  if (!config.matrix.accessToken) {
    console.error('[Bridge] No Matrix access token configured');
    return;
  }

  matrixClient = sdk.createClient({
    baseUrl: config.matrix.homeserverUrl,
    accessToken: config.matrix.accessToken,
    userId: '@oc_matrix_synapse_deployment:matrix.oculair.ca', // The OpenCode identity
  });

  // Set up event handlers
  matrixClient.on(sdk.RoomEvent.Timeline, (event, room, toStartOfTimeline) => {
    // Skip old messages during initial sync
    if (toStartOfTimeline) return;
    
    const eventType = event.getType();
    const sender = event.getSender();
    
    // Log all message events for debugging
    if (eventType === 'm.room.message') {
      const content = event.getContent();
      console.log(`[Bridge] Timeline event: type=${eventType}, sender=${sender}, body="${content.body?.substring(0, 50)}..."`);
    }
    
    if (room) {
      handleMatrixMessage(event, room).catch(err => {
        console.error('[Bridge] Error in handleMatrixMessage:', err);
      });
    }
  });

  // Auto-accept room invites
  matrixClient.on(sdk.RoomMemberEvent.Membership, async (event, member) => {
    if (member.membership === 'invite' && member.userId === matrixClient?.getUserId()) {
      const roomId = member.roomId;
      console.log(`[Bridge] Received invite to room ${roomId}, auto-accepting...`);
      try {
        await matrixClient?.joinRoom(roomId);
        console.log(`[Bridge] Successfully joined room ${roomId}`);
      } catch (err) {
        console.error(`[Bridge] Failed to join room ${roomId}:`, err);
      }
    }
  });

  // Start syncing
  await matrixClient.startClient({ initialSyncLimit: 0 });
  console.log('[Bridge] Matrix client started, syncing...');
}

// HTTP API handlers
function handleRequest(req: IncomingMessage, res: ServerResponse): void {
  const url = new URL(req.url || '/', `http://localhost:${config.bridge.port}`);
  
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // Health check
  if (url.pathname === '/health' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ 
      status: 'ok', 
      registrations: registrations.size,
      matrixConnected: matrixClient?.isLoggedIn() || false 
    }));
    return;
  }

  // List registrations
  if (url.pathname === '/registrations' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      count: registrations.size,
      registrations: Array.from(registrations.values()),
    }));
    return;
  }

  // Register OpenCode instance
  if (url.pathname === '/register' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        const { port, hostname, sessionId, directory, rooms } = data;

        if (!port || !sessionId || !directory) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing required fields: port, sessionId, directory' }));
          return;
        }

        const id = `${hostname || config.opencode.defaultHost}:${port}:${sessionId}`;
        const registration: OpenCodeRegistration = {
          id,
          port,
          hostname: hostname || config.opencode.defaultHost,
          sessionId,
          directory,
          rooms: rooms || [],
          registeredAt: Date.now(),
          lastSeen: Date.now(),
        };

        registrations.set(id, registration);
        
        // Map the derived Matrix identity to this registration
        const matrixIdentity = deriveMatrixIdentity(directory);
        identityToRegistration.set(matrixIdentity, registration);
        console.log(`[Bridge] Registered: ${id} -> ${matrixIdentity}`);

        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: true, id, matrixIdentity, registration }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // Unregister OpenCode instance
  if (url.pathname === '/unregister' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const { id } = JSON.parse(body);
        const registration = registrations.get(id);
        const deleted = registrations.delete(id);
        
        // Also remove from identity map
        if (registration) {
          const matrixIdentity = deriveMatrixIdentity(registration.directory);
          identityToRegistration.delete(matrixIdentity);
          console.log(`[Bridge] Unregistered: ${id} (${matrixIdentity})`);
        }
        
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: deleted }));
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // List available rooms (from agent mappings)
  if (url.pathname === '/rooms' && req.method === 'GET') {
    const rooms = Object.values(agentMappings).map(m => ({
      room_id: m.room_id,
      agent_name: m.agent_name,
      agent_id: m.agent_id,
    }));
    
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ count: rooms.length, rooms }));
    return;
  }

  // Notify OpenCode instance - explicit message forwarding
  // Called by MCP tool when an agent wants to send to OpenCode
  if (url.pathname === '/notify' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      try {
        const data = JSON.parse(body);
        const { directory, message, sender, agentName } = data;

        if (!directory || !message) {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Missing required fields: directory, message' }));
          return;
        }

        // Find most recent registration by directory
        let targetRegistration: OpenCodeRegistration | undefined;
        for (const reg of registrations.values()) {
          if (reg.directory === directory) {
            // Pick the most recently registered/seen one
            if (!targetRegistration || reg.lastSeen > targetRegistration.lastSeen) {
              targetRegistration = reg;
            }
          }
        }

        if (!targetRegistration) {
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ 
            error: 'No OpenCode instance registered for directory', 
            directory,
            registeredDirectories: Array.from(registrations.values()).map(r => r.directory)
          }));
          return;
        }

        // Forward to OpenCode
        const success = await forwardToOpenCode(
          targetRegistration,
          '', // roomId not needed for explicit notify
          sender || 'unknown',
          message,
          '', // eventId not needed
          agentName // pass agent name for cleaner formatting
        );

        if (success) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ success: true, forwarded_to: targetRegistration.id }));
        } else {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Failed to forward to OpenCode' }));
        }
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }

  // 404
  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Not found' }));
}

// Main
async function main(): Promise<void> {
  console.log('[Bridge] Starting OpenCode Matrix Bridge...');
  
  // Load agent mappings
  loadAgentMappings();

  // Start HTTP server
  const server = createServer(handleRequest);
  server.listen(config.bridge.port, () => {
    console.log(`[Bridge] HTTP API listening on port ${config.bridge.port}`);
  });

  // Initialize Matrix client
  await initMatrix();

  // Start periodic cleanup of stale registrations
  console.log('[Bridge] Starting registration cleanup...');
  discoveryInterval = setInterval(async () => {
    const now = Date.now();
    for (const [id, reg] of registrations.entries()) {
      if (now - reg.lastSeen > 60000) {
        console.log(`[Bridge] Removing stale registration: ${id}`);
        registrations.delete(id);
      }
    }
  }, 30000); // Clean up every 30 seconds

  console.log('[Bridge] OpenCode Matrix Bridge ready!');
  console.log('[Bridge] Endpoints:');
  console.log(`  POST /register    - Register OpenCode instance (manual)`);
  console.log(`  POST /unregister  - Unregister OpenCode instance`);
  console.log(`  GET  /registrations - List registered instances`);
  console.log(`  GET  /rooms       - List available agent rooms`);
  console.log(`  GET  /health      - Health check`);
  console.log('[Bridge] OpenCode instances should auto-register via plugin');
}

// Cleanup on exit
process.on('SIGTERM', () => {
  if (discoveryInterval) clearInterval(discoveryInterval);
  process.exit(0);
});

main().catch(console.error);
