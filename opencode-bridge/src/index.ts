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

import "dotenv/config";
import * as sdk from "matrix-js-sdk";
import { createOpencodeClient } from "@opencode-ai/sdk";
import { createServer, IncomingMessage, ServerResponse } from "http";
import { fetchAgentMappings } from "./agent-mappings.js";
// Types
interface OpenCodeRegistration {
  id: string;
  port: number;
  hostname: string;
  sessionId: string;
  directory: string;
  rooms: string[]; // Room IDs to monitor
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
    homeserverUrl:
      process.env.MATRIX_HOMESERVER_URL || "https://matrix.oculair.ca",
    accessToken: process.env.MATRIX_ACCESS_TOKEN || "",
  },
  bridge: {
    port: parseInt(process.env.BRIDGE_PORT || "3200"),
  },
  matrixApi: {
    baseUrl: process.env.MATRIX_API_URL || "http://127.0.0.1:8000",
  },
  opencode: {
    defaultHost: process.env.OPENCODE_DEFAULT_HOST || "127.0.0.1",
  },
};

// State
const registrations = new Map<string, OpenCodeRegistration>();
const identityToRegistration = new Map<string, OpenCodeRegistration>(); // @oc_* MXID -> registration
let matrixClient: sdk.MatrixClient | null = null;
let agentMappings: Record<string, AgentMapping> = {};
let discoveryInterval: NodeJS.Timeout | null = null;

// Matrix server domain for identity derivation
const MATRIX_DOMAIN = process.env.MATRIX_DOMAIN || "matrix.oculair.ca";

/**
 * Derive Matrix identity MXID from directory path
 * Supports both old format (@oc_xxx) and new v2 format (@oc_xxx_v2)
 * Returns both for registration mapping
 */
function deriveMatrixIdentity(directory: string): string {
  const dirName =
    directory
      .split("/")
      .filter((p) => p)
      .pop() || "default";
  const localpart = `oc_${dirName.toLowerCase().replace(/[^a-z0-9]/g, "_")}`;
  // Return v2 format as primary
  return `@${localpart}_v2:${MATRIX_DOMAIN}`;
}

/**
 * Derive both old and new Matrix identity formats
 */
function deriveMatrixIdentities(directory: string): string[] {
  const dirName =
    directory
      .split("/")
      .filter((p) => p)
      .pop() || "default";
  const localpart = `oc_${dirName.toLowerCase().replace(/[^a-z0-9]/g, "_")}`;
  return [
    `@${localpart}:${MATRIX_DOMAIN}`, // old format
    `@${localpart}_v2:${MATRIX_DOMAIN}`, // new v2 format
  ];
}

/**
 * Extract @oc_* mentions from message body
 * Returns array of MXIDs that match OpenCode identity pattern (including v2 suffix)
 * 
 * IMPORTANT: Only extracts mentions from the NEW message content, not quoted/replied content
 */
function extractOpenCodeMentions(body: string): string[] {
  // Strip Matrix reply fallback (quoted previous messages)
  // Format: "> <@user:domain> original message\n\nnew message"
  const lines = body.split('\n');
  let actualMessage = body;
  
  // If message starts with "> ", it's a reply - extract only the new content
  if (lines[0].startsWith('> ')) {
    // Find first non-quote line (actual new message starts after blank line following quotes)
    const firstNonQuoteIdx = lines.findIndex((line, idx) => 
      idx > 0 && line === '' && lines[idx - 1].startsWith('>')
    );
    if (firstNonQuoteIdx >= 0 && firstNonQuoteIdx < lines.length - 1) {
      actualMessage = lines.slice(firstNonQuoteIdx + 1).join('\n');
    }
  }
  
  // Match both old @oc_xxx and new @oc_xxx_v2 patterns in the actual message only
  const mentionRegex = /@oc_[a-z0-9_]+(_v2)?:[a-z0-9._-]+/gi;
  const matches = actualMessage.match(mentionRegex) || [];
  return [...new Set(matches)]; // Deduplicate
}

/**
 * Extract the directory name pattern from an OpenCode MXID
 * @oc_letta_v2:matrix.oculair.ca -> "letta"
 * @oc_my_project_v2:matrix.oculair.ca -> "my_project" (or "my-project" with dashes)
 */
function extractDirNameFromMxid(mxid: string): string | null {
  // Match @oc_<name>_v2 or @oc_<name> pattern
  const match = mxid.match(/@oc_([a-z0-9_]+?)(_v2)?:/i);
  if (!match) return null;
  return match[1];
}

/**
 * Find a registration by trying to match MXID to discovered OpenCode instances
 * This is called when no direct registration is found for an @mention
 */
async function findOrCreateRegistrationForMxid(
  mxid: string,
): Promise<OpenCodeRegistration | undefined> {
  const dirNamePattern = extractDirNameFromMxid(mxid);
  if (!dirNamePattern) {
    console.log(
      `[Bridge] Could not extract directory pattern from MXID: ${mxid}`,
    );
    return undefined;
  }

  console.log(
    `[Bridge] Looking for OpenCode instance matching pattern: ${dirNamePattern}`,
  );

  // First, run discovery to find any running OpenCode instances
  await discoverAllOpenCodeInstances();

  // Check if we now have a registration for this MXID
  let registration = identityToRegistration.get(mxid);
  if (registration) {
    console.log(
      `[Bridge] Found registration after discovery: ${registration.id}`,
    );
    return registration;
  }

  // Try to find a registration whose directory matches the pattern
  // The pattern might be "letta" which should match "/opt/stacks/letta" or "/opt/stacks/letta-code"
  for (const [id, reg] of registrations.entries()) {
    const regDirName =
      reg.directory
        .split("/")
        .filter((p) => p)
        .pop() || "";
    const regDirNormalized = regDirName
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "_");

    // Check if the directory name matches the pattern
    if (
      regDirNormalized === dirNamePattern ||
      regDirNormalized.startsWith(dirNamePattern)
    ) {
      // Check if this registration's derived identities include our target MXID
      const derivedIdentities = deriveMatrixIdentities(reg.directory);
      if (derivedIdentities.includes(mxid)) {
        // Map this registration to the MXID
        identityToRegistration.set(mxid, reg);
        console.log(`[Bridge] Mapped ${mxid} to existing registration ${id}`);
        return reg;
      }
    }
  }

  console.log(`[Bridge] No matching OpenCode instance found for ${mxid}`);
  return undefined;
}

// Load agent mappings
async function loadAgentMappings(): Promise<void> {
  try {
    agentMappings = await fetchAgentMappings(config.matrixApi.baseUrl, fetch);
    console.log(
      `[Bridge] Loaded ${Object.keys(agentMappings).length} agent mappings`,
    );
  } catch (e) {
    console.error("[Bridge] Failed to load agent mappings:", e);
  }
}

// Get agent info from room
function getAgentForRoom(roomId: string): AgentMapping | undefined {
  return Object.values(agentMappings).find((m) => m.room_id === roomId);
}

// Discovery service URL (runs on host, accessible via localhost since we use network_mode: host)
const DISCOVERY_SERVICE_URL =
  process.env.DISCOVERY_SERVICE_URL || "http://127.0.0.1:3202";

interface DiscoveredInstance {
  pid: number;
  directory: string;
  port: number;
  hostname: string;
}

// Discover all running OpenCode instances via the discovery service
async function discoverAllOpenCodeInstances(): Promise<void> {
  try {
    // Query the discovery service running on the host
    const response = await fetch(`${DISCOVERY_SERVICE_URL}/discover`);
    if (!response.ok) {
      console.log(`[Bridge] Discovery service returned ${response.status}`);
      return;
    }

    const instances: DiscoveredInstance[] = await response.json();

    if (instances.length === 0) {
      console.log("[Bridge] No OpenCode instances discovered");
      return;
    }

    console.log(`[Bridge] Discovered ${instances.length} OpenCode instance(s)`);

    for (const instance of instances) {
      const { pid, directory, port, hostname } = instance;

      if (!directory || !port || port <= 0) {
        continue;
      }

      const id = `${hostname}:${port}:opencode-${pid}`;

      // Check if already registered with correct port
      const existing = registrations.get(id);
      let registration: OpenCodeRegistration;

      if (existing) {
        existing.lastSeen = Date.now();
        registration = existing;
      } else {
        // Create new registration
        registration = {
          id,
          port,
          hostname: hostname || "127.0.0.1",
          sessionId: `opencode-${pid}`,
          directory,
          rooms: [],
          registeredAt: Date.now(),
          lastSeen: Date.now(),
        };
        registrations.set(id, registration);
        console.log(`[Bridge] Auto-discovered OpenCode: ${id} (${directory})`);
      }

      // Always ensure identity mappings point to the correct (latest) registration
      // This handles the case where:
      // 1. Identity mappings were deleted during stale cleanup
      // 2. A stale registration exists with wrong port (prefer newer registration)
      // 3. Multiple registrations exist for same directory (prefer most recently seen)
      const matrixIdentities = deriveMatrixIdentities(directory);
      for (const identity of matrixIdentities) {
        const currentMapping = identityToRegistration.get(identity);
        // Update mapping if:
        // - No mapping exists, OR
        // - Current mapping's directory matches (update to latest port), OR
        // - Current mapping is older than this registration (prefer newer)
        const shouldUpdate = !currentMapping || 
          currentMapping.directory === directory ||
          currentMapping.lastSeen < registration.lastSeen;
        if (shouldUpdate) {
          identityToRegistration.set(identity, registration);
        }
      }
    }

    // Clean up stale registrations (older than 5 minutes)
    const STALE_TIMEOUT = 300000; // 5 minutes
    const now = Date.now();
    for (const [id, reg] of registrations.entries()) {
      if (now - reg.lastSeen > STALE_TIMEOUT) {
        console.log(`[Bridge] Removing stale registration: ${id}`);
        registrations.delete(id);
        // Also remove from identity map
        const matrixIdentities = deriveMatrixIdentities(reg.directory);
        for (const identity of matrixIdentities) {
          identityToRegistration.delete(identity);
        }
      }
    }
  } catch (error) {
    console.error("[Bridge] Failed to discover OpenCode instances:", error);
  }
}

// Get the most recently active session ID for a directory
async function getActiveSessionId(
  baseUrl: string,
  directory: string,
): Promise<string | null> {
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
    console.error("[Bridge] Failed to list sessions:", error);
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
  agentNameOverride?: string,
): Promise<boolean> {
  // Use override if provided, otherwise look up from room
  const agentName =
    agentNameOverride || getAgentForRoom(roomId)?.agent_name || "Unknown Agent";

  // Format the message for injection
  const messageText = `[Message from ${agentName}]\n${content}`;

  // Try to forward with current registration
  const result = await tryForwardToRegistration(
    registration,
    messageText,
    content,
  );
  if (result) {
    return true;
  }

  // If failed, run discovery to find updated ports
  console.log(
    `[Bridge] Forward failed, running discovery service to refresh registrations...`,
  );
  await discoverAllOpenCodeInstances();

  // Find the updated registration for this directory
  const updatedRegistration = findRegistrationByDirectory(
    registration.directory,
  );
  if (!updatedRegistration) {
    console.error(
      `[Bridge] No registration found for directory after discovery: ${registration.directory}`,
    );
    return false;
  }

  // If port changed, try again with updated registration
  if (updatedRegistration.port !== registration.port) {
    console.log(
      `[Bridge] Port changed from ${registration.port} to ${updatedRegistration.port}, retrying...`,
    );
    return await tryForwardToRegistration(
      updatedRegistration,
      messageText,
      content,
    );
  }

  console.error(
    `[Bridge] Forward failed and no port change detected for ${registration.directory}`,
  );
  return false;
}

// Helper: Find registration by directory
function findRegistrationByDirectory(
  directory: string,
): OpenCodeRegistration | undefined {
  for (const reg of registrations.values()) {
    if (reg.directory === directory) {
      return reg;
    }
  }
  return undefined;
}

// Helper: Try to forward message to a specific registration
async function tryForwardToRegistration(
  registration: OpenCodeRegistration,
  messageText: string,
  contentPreview: string,
): Promise<boolean> {
  try {
    const { port, hostname } = registration;

    const client = createOpencodeClient({
      baseUrl: `http://${hostname}:${port}`,
    });

    // Get the actual active session ID for this directory
    const sessionId = await getActiveSessionId(
      `http://${hostname}:${port}`,
      registration.directory,
    );

    if (!sessionId) {
      console.error(
        `[Bridge] No active session found for directory: ${registration.directory}`,
      );
      return false;
    }

    // Send message to session - let the OpenCode agent see and respond to it
    await client.session.prompt({
      path: { id: sessionId },
      body: {
        parts: [{ type: "text", text: messageText }],
      },
    });

    // Update last seen on success
    registration.lastSeen = Date.now();

    console.log(
      `[Bridge] Forwarded message to OpenCode ${registration.id} (session ${sessionId}): ${contentPreview.substring(0, 50)}...`,
    );
    return true;
  } catch (error) {
    console.error(
      `[Bridge] Failed to forward to OpenCode ${registration.id}:`,
      error,
    );
    return false;
  }
}

// Handle incoming Matrix message - forward ONLY if message @mentions an OpenCode identity
async function handleMatrixMessage(
  event: sdk.MatrixEvent,
  room: sdk.Room,
): Promise<void> {
  // Ignore our own messages
  if (event.getSender() === matrixClient?.getUserId()) return;

  // Only handle text messages
  if (event.getType() !== "m.room.message") return;
  const content = event.getContent();
  if (content.msgtype !== "m.text") return;

  const roomId = room.roomId;
  const sender = event.getSender() || "unknown";
  const body = content.body || "";

  // Extract @oc_* mentions from the message
  const mentions = extractOpenCodeMentions(body);

  // If no OpenCode mentions, ignore this message entirely
  if (mentions.length === 0) {
    return;
  }

  console.log(
    `[Bridge] Message with @mention(s): ${mentions.join(", ")} from ${sender}`,
  );

  // Forward to each mentioned OpenCode identity
  for (const mention of mentions) {
    let registration = identityToRegistration.get(mention);

    if (!registration) {
      console.log(
        `[Bridge] No registration found for ${mention}, attempting discovery...`,
      );
      // Try to find or create a registration by discovering running OpenCode instances
      registration = await findOrCreateRegistrationForMxid(mention);

      if (!registration) {
        console.log(`[Bridge] Could not find OpenCode instance for ${mention}`);
        continue;
      }
    }

    // Get sender's display name for better context
    const senderMember = room.getMember?.(sender);
    const senderName =
      senderMember?.name || getAgentForRoom(roomId)?.agent_name || sender;

    console.log(`[Bridge] Forwarding to ${registration.id} (${mention})`);

    try {
      await forwardToOpenCode(
        registration,
        roomId,
        sender,
        body,
        event.getId() || "",
        senderName,
      );
    } catch (error) {
      console.error(`[Bridge] Failed to forward to ${registration.id}:`, error);
    }
  }
}

// Initialize Matrix client
async function initMatrix(): Promise<void> {
  if (!config.matrix.accessToken) {
    console.error("[Bridge] No Matrix access token configured");
    return;
  }

  matrixClient = sdk.createClient({
    baseUrl: config.matrix.homeserverUrl,
    accessToken: config.matrix.accessToken,
    userId: "@oc_matrix_synapse_deployment_v2:matrix.oculair.ca", // The OpenCode identity (v2)
  });

  // Set up event handlers
  matrixClient.on(sdk.RoomEvent.Timeline, (event, room, toStartOfTimeline) => {
    // Skip old messages during initial sync
    if (toStartOfTimeline) return;

    const eventType = event.getType();
    const sender = event.getSender();

    // Log all message events for debugging
    if (eventType === "m.room.message") {
      const content = event.getContent();
      console.log(
        `[Bridge] Timeline event: type=${eventType}, sender=${sender}, body="${content.body?.substring(0, 50)}..."`,
      );
    }

    if (room) {
      handleMatrixMessage(event, room).catch((err) => {
        console.error("[Bridge] Error in handleMatrixMessage:", err);
      });
    }
  });

  // Auto-accept room invites
  matrixClient.on(sdk.RoomMemberEvent.Membership, async (event, member) => {
    if (
      member.membership === "invite" &&
      member.userId === matrixClient?.getUserId()
    ) {
      const roomId = member.roomId;
      console.log(
        `[Bridge] Received invite to room ${roomId}, auto-accepting...`,
      );
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
  console.log("[Bridge] Matrix client started, syncing...");
}

// HTTP API handlers
async function handleRequest(
  req: IncomingMessage,
  res: ServerResponse,
): Promise<void> {
  const url = new URL(req.url || "/", `http://localhost:${config.bridge.port}`);

  // CORS headers
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  // Health check
  if (url.pathname === "/health" && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        status: "ok",
        registrations: registrations.size,
        matrixConnected: matrixClient?.isLoggedIn() || false,
      }),
    );
    return;
  }

  // List registrations
  if (url.pathname === "/registrations" && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        count: registrations.size,
        registrations: Array.from(registrations.values()),
      }),
    );
    return;
  }

  // Discover OpenCode instances - manually trigger discovery
  if (
    url.pathname === "/discover" &&
    (req.method === "GET" || req.method === "POST")
  ) {
    try {
      await discoverAllOpenCodeInstances();
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          success: true,
          count: registrations.size,
          registrations: Array.from(registrations.values()),
          identityMappings: Object.fromEntries(identityToRegistration),
        }),
      );
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(error) }));
    }
    return;
  }

  if (url.pathname === "/status" && req.method === "GET") {
    const directory = url.searchParams.get("directory");
    if (!directory) {
      res.writeHead(400, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Missing required query param: directory" }));
      return;
    }

    let best: OpenCodeRegistration | undefined;
    for (const reg of registrations.values()) {
      if (reg.directory !== directory) continue;
      if (!best || reg.lastSeen > best.lastSeen) {
        best = reg;
      }
    }

    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        directory,
        registered: !!best,
        registration: best || null,
      }),
    );
    return;
  }

  // Register OpenCode instance
  if (url.pathname === "/register" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const data = JSON.parse(body);
        const { port, hostname, sessionId, directory, rooms } = data;

        if (port === undefined || !sessionId || !directory) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              error: "Missing required fields: port, sessionId, directory",
            }),
          );
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

        // Map both old and new Matrix identity formats to this registration
        const matrixIdentities = deriveMatrixIdentities(directory);
        for (const identity of matrixIdentities) {
          identityToRegistration.set(identity, registration);
        }
        console.log(
          `[Bridge] Registered: ${id} -> ${matrixIdentities.join(", ")}`,
        );

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify({ success: true, id, matrixIdentities, registration }),
        );
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  // Heartbeat - keep registration alive
  if (url.pathname === "/heartbeat" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const data = JSON.parse(body);
        const { id, directory } = data;

        // Find registration by ID or directory
        let registration: OpenCodeRegistration | undefined;

        if (id) {
          registration = registrations.get(id);
        }

        if (!registration && directory) {
          // Find by directory
          for (const reg of registrations.values()) {
            if (reg.directory === directory) {
              registration = reg;
              break;
            }
          }
        }

        if (registration) {
          registration.lastSeen = Date.now();
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              success: true,
              id: registration.id,
              lastSeen: registration.lastSeen,
            }),
          );
        } else {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({ error: "Registration not found", id, directory }),
          );
        }
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  // Unregister OpenCode instance
  if (url.pathname === "/unregister" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const { id } = JSON.parse(body);
        const registration = registrations.get(id);
        const deleted = registrations.delete(id);

        // Also remove from identity map (both old and new formats)
        if (registration) {
          const matrixIdentities = deriveMatrixIdentities(
            registration.directory,
          );
          for (const identity of matrixIdentities) {
            identityToRegistration.delete(identity);
          }
          console.log(
            `[Bridge] Unregistered: ${id} (${matrixIdentities.join(", ")})`,
          );
        }

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: deleted }));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  // List available rooms (from agent mappings)
  if (url.pathname === "/rooms" && req.method === "GET") {
    await loadAgentMappings();
    const rooms = Object.values(agentMappings).map((m) => ({
      room_id: m.room_id,
      agent_name: m.agent_name,
      agent_id: m.agent_id,
    }));

    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ count: rooms.length, rooms }));
    return;
  }

  // Notify OpenCode instance - explicit message forwarding
  // Called by MCP tool when an agent wants to send to OpenCode
  if (url.pathname === "/notify" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        const data = JSON.parse(body);
        const { directory, message, sender, agentName } = data;

        if (!directory || !message) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              error: "Missing required fields: directory, message",
            }),
          );
          return;
        }

        // Find most recent registration by directory
        let targetRegistration: OpenCodeRegistration | undefined;
        for (const reg of registrations.values()) {
          if (reg.directory === directory) {
            // Pick the most recently registered/seen one
            if (
              !targetRegistration ||
              reg.lastSeen > targetRegistration.lastSeen
            ) {
              targetRegistration = reg;
            }
          }
        }

        if (!targetRegistration) {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              error: "No OpenCode instance registered for directory",
              directory,
              registeredDirectories: Array.from(registrations.values()).map(
                (r) => r.directory,
              ),
            }),
          );
          return;
        }

        // Forward to OpenCode
        const success = await forwardToOpenCode(
          targetRegistration,
          "", // roomId not needed for explicit notify
          sender || "unknown",
          message,
          "", // eventId not needed
          agentName, // pass agent name for cleaner formatting
        );

        if (success) {
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(
            JSON.stringify({
              success: true,
              forwarded_to: targetRegistration.id,
            }),
          );
        } else {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Failed to forward to OpenCode" }));
        }
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  // 404
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "Not found" }));
}

// Main
async function main(): Promise<void> {
  console.log("[Bridge] Starting OpenCode Matrix Bridge...");

  // Load agent mappings
  await loadAgentMappings();

  // Start HTTP server
  const server = createServer(handleRequest);
  server.listen(config.bridge.port, () => {
    console.log(`[Bridge] HTTP API listening on port ${config.bridge.port}`);
  });

  // Initialize Matrix client
  await initMatrix();

  // Start periodic discovery to keep registrations up to date
  // This runs every 30 seconds to discover new OpenCode instances
  // and update identity mappings with correct ports
  console.log(
    "[Bridge] Starting periodic discovery (every 30 seconds)...",
  );
  discoveryInterval = setInterval(async () => {
    try {
      await discoverAllOpenCodeInstances();
    } catch (error) {
      console.error("[Bridge] Periodic discovery failed:", error);
    }
  }, 30000); // Discover every 30 seconds
  
  // Run initial discovery
  console.log("[Bridge] Running initial discovery...");
  await discoverAllOpenCodeInstances();

  console.log("[Bridge] OpenCode Matrix Bridge ready!");
  console.log("[Bridge] Endpoints:");
  console.log(`  POST /register    - Register OpenCode instance`);
  console.log(
    `  POST /heartbeat   - Keep registration alive (call every 2-3 min)`,
  );
  console.log(`  POST /unregister  - Unregister OpenCode instance`);
  console.log(`  POST /notify      - Forward message to OpenCode instance`);
  console.log(`  GET  /registrations - List registered instances`);
  console.log(`  GET  /rooms       - List available agent rooms`);
  console.log(`  GET  /health      - Health check`);
  console.log(
    "[Bridge] Registrations expire after 5 minutes without heartbeat",
  );
}

// Cleanup on exit
process.on("SIGTERM", () => {
  if (discoveryInterval) clearInterval(discoveryInterval);
  process.exit(0);
});

main().catch(console.error);
