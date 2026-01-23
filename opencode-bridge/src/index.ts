/**
 * OpenCode Matrix Bridge
 *
 * Syncs Matrix rooms and forwards messages to registered OpenCode instances via WebSocket.
 *
 * Flow:
 * 1. OpenCode plugins register via HTTP API
 * 2. Plugins connect via WebSocket and authenticate with registrationId
 * 3. Bridge syncs Matrix rooms using a bot account
 * 4. When messages with @oc_* mentions arrive, bridge pushes to connected plugins via WebSocket
 * 5. Plugins inject messages into their local OpenCode sessions
 */

import "dotenv/config";
import * as sdk from "matrix-js-sdk";
import { createServer, IncomingMessage, ServerResponse } from "http";
import { WebSocketServer, WebSocket } from "ws";
import { fetchAgentMappings } from "./agent-mappings.js";

interface OpenCodeRegistration {
  id: string;
  directory: string;
  sessionId: string;
  rooms: string[];
  registeredAt: number;
  lastSeen: number;
  ws: WebSocket | null;
}

interface AgentMapping {
  agent_id: string;
  agent_name: string;
  matrix_user_id: string;
  room_id: string;
}

interface WsAuthMessage {
  type: "auth";
  registrationId: string;
}

interface WsMatrixMessage {
  type: "matrix_message";
  sender: string;
  senderMxid: string;
  roomId: string;
  body: string;
  eventId: string;
}

const config = {
  matrix: {
    homeserverUrl: process.env.MATRIX_HOMESERVER_URL || "https://matrix.oculair.ca",
    accessToken: process.env.MATRIX_ACCESS_TOKEN || "",
  },
  bridge: {
    port: parseInt(process.env.BRIDGE_PORT || "3201"),
  },
  matrixApi: {
    baseUrl: process.env.MATRIX_API_URL || "http://127.0.0.1:8000",
  },
};

const registrations = new Map<string, OpenCodeRegistration>();
const identityToRegistration = new Map<string, OpenCodeRegistration>();
const roomToRegistration = new Map<string, OpenCodeRegistration>();
let matrixClient: sdk.MatrixClient | null = null;
let agentMappings: Record<string, AgentMapping> = {};

const MATRIX_DOMAIN = process.env.MATRIX_DOMAIN || "matrix.oculair.ca";
const STALE_TIMEOUT = 300000;

function deriveMatrixIdentities(directory: string): string[] {
  const dirName = directory.split("/").filter((p) => p).pop() || "default";
  const localpart = `oc_${dirName.toLowerCase().replace(/[^a-z0-9]/g, "_")}`;
  return [
    `@${localpart}:${MATRIX_DOMAIN}`,
    `@${localpart}_v2:${MATRIX_DOMAIN}`,
  ];
}

function extractOpenCodeMentions(body: string): string[] {
  const lines = body.split('\n');
  let actualMessage = body;
  
  if (lines[0].startsWith('> ')) {
    const firstNonQuoteIdx = lines.findIndex((line, idx) => 
      idx > 0 && line === '' && lines[idx - 1].startsWith('>')
    );
    if (firstNonQuoteIdx >= 0 && firstNonQuoteIdx < lines.length - 1) {
      actualMessage = lines.slice(firstNonQuoteIdx + 1).join('\n');
    }
  }
  
  const mentionRegex = /@oc_[a-z0-9_]+(_v2)?:[a-z0-9._-]+/gi;
  const matches = actualMessage.match(mentionRegex) || [];
  return [...new Set(matches)];
}

async function loadAgentMappings(): Promise<void> {
  try {
    agentMappings = await fetchAgentMappings(config.matrixApi.baseUrl, fetch);
    console.log(`[Bridge] Loaded ${Object.keys(agentMappings).length} agent mappings`);
  } catch (e) {
    console.error("[Bridge] Failed to load agent mappings:", e);
  }
}

function getAgentForRoom(roomId: string): AgentMapping | undefined {
  return Object.values(agentMappings).find((m) => m.room_id === roomId);
}

function cleanupStaleRegistrations(): void {
  const now = Date.now();
  for (const [id, reg] of registrations.entries()) {
    if (now - reg.lastSeen > STALE_TIMEOUT) {
      console.log(`[Bridge] Removing stale registration: ${id}`);
      if (reg.ws) {
        reg.ws.close();
      }
      registrations.delete(id);
      const identities = deriveMatrixIdentities(reg.directory);
      for (const identity of identities) {
        if (identityToRegistration.get(identity) === reg) {
          identityToRegistration.delete(identity);
        }
      }
      for (const roomId of reg.rooms) {
        if (roomToRegistration.get(roomId) === reg) {
          roomToRegistration.delete(roomId);
        }
      }
    }
  }
}

function forwardToOpenCode(
  registration: OpenCodeRegistration,
  roomId: string,
  sender: string,
  senderMxid: string,
  body: string,
  eventId: string
): boolean {
  if (!registration.ws || registration.ws.readyState !== WebSocket.OPEN) {
    console.log(`[Bridge] No active WebSocket for ${registration.id}`);
    return false;
  }

  const message: WsMatrixMessage = {
    type: "matrix_message",
    sender,
    senderMxid,
    roomId,
    body,
    eventId,
  };

  try {
    registration.ws.send(JSON.stringify(message));
    registration.lastSeen = Date.now();
    console.log(`[Bridge] Forwarded message to ${registration.id} via WebSocket`);
    return true;
  } catch (error) {
    console.error(`[Bridge] Failed to send via WebSocket to ${registration.id}:`, error);
    return false;
  }
}

async function handleMatrixMessage(event: sdk.MatrixEvent, room: sdk.Room): Promise<void> {
  if (event.getSender() === matrixClient?.getUserId()) return;
  if (event.getType() !== "m.room.message") return;
  
  const content = event.getContent();
  if (content.msgtype !== "m.text") return;

  const roomId = room.roomId;
  const senderMxid = event.getSender() || "unknown";
  const body = content.body || "";

  const senderMember = room.getMember?.(senderMxid);
  const senderName = senderMember?.name || getAgentForRoom(roomId)?.agent_name || senderMxid;
  const eventId = event.getId() || "";

  const roomRegistration = roomToRegistration.get(roomId);
  if (roomRegistration) {
    console.log(`[Bridge] Message in OpenCode room ${roomId} from ${senderName}`);
    forwardToOpenCode(roomRegistration, roomId, senderName, senderMxid, body, eventId);
    return;
  }

  const mentions = extractOpenCodeMentions(body);
  if (mentions.length === 0) return;

  console.log(`[Bridge] Message with @mention(s): ${mentions.join(", ")} from ${senderMxid}`);

  for (const mention of mentions) {
    const registration = identityToRegistration.get(mention);

    if (!registration) {
      console.log(`[Bridge] No registration found for ${mention}`);
      continue;
    }

    console.log(`[Bridge] Forwarding to ${registration.id} (${mention})`);
    forwardToOpenCode(registration, roomId, senderName, senderMxid, body, eventId);
  }
}

async function initMatrix(): Promise<void> {
  if (!config.matrix.accessToken) {
    console.error("[Bridge] No Matrix access token configured");
    return;
  }

  matrixClient = sdk.createClient({
    baseUrl: config.matrix.homeserverUrl,
    accessToken: config.matrix.accessToken,
    userId: "@oc_matrix_synapse_deployment:matrix.oculair.ca",
  });

  matrixClient.on(sdk.RoomEvent.Timeline, (event, room, toStartOfTimeline) => {
    if (toStartOfTimeline) return;

    if (room) {
      handleMatrixMessage(event, room).catch((err) => {
        console.error("[Bridge] Error in handleMatrixMessage:", err);
      });
    }
  });

  matrixClient.on(sdk.RoomMemberEvent.Membership, async (event, member) => {
    if (member.membership === "invite" && member.userId === matrixClient?.getUserId()) {
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

  await matrixClient.startClient({ initialSyncLimit: 0 });
  console.log("[Bridge] Matrix client started, syncing...");
}

async function handleRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url || "/", `http://localhost:${config.bridge.port}`);

  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  if (url.pathname === "/health" && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      status: "ok",
      registrations: registrations.size,
      connectedPlugins: Array.from(registrations.values()).filter(r => r.ws?.readyState === WebSocket.OPEN).length,
      matrixConnected: matrixClient?.isLoggedIn() || false,
    }));
    return;
  }

  if (url.pathname === "/registrations" && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      count: registrations.size,
      registrations: Array.from(registrations.values()).map(r => ({
        id: r.id,
        directory: r.directory,
        sessionId: r.sessionId,
        rooms: r.rooms,
        registeredAt: r.registeredAt,
        lastSeen: r.lastSeen,
        wsConnected: r.ws?.readyState === WebSocket.OPEN,
      })),
    }));
    return;
  }

  if (url.pathname === "/register" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const data = JSON.parse(body);
        const { sessionId, directory, rooms } = data;

        if (!sessionId || !directory) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Missing required fields: sessionId, directory" }));
          return;
        }

        const id = `${directory}:${sessionId}`;
        
        const existing = registrations.get(id);
        if (existing) {
          existing.lastSeen = Date.now();
          existing.rooms = rooms || [];
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ 
            success: true, 
            id, 
            wsUrl: `ws://127.0.0.1:${config.bridge.port}/ws`,
            registration: { ...existing, ws: undefined }
          }));
          return;
        }

        const registration: OpenCodeRegistration = {
          id,
          directory,
          sessionId,
          rooms: rooms || [],
          registeredAt: Date.now(),
          lastSeen: Date.now(),
          ws: null,
        };

        registrations.set(id, registration);

        const matrixIdentities = deriveMatrixIdentities(directory);
        for (const identity of matrixIdentities) {
          identityToRegistration.set(identity, registration);
        }
        for (const roomId of registration.rooms) {
          roomToRegistration.set(roomId, registration);
        }
        
        console.log(`[Bridge] Registered: ${id} -> ${matrixIdentities.join(", ")}`);

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ 
          success: true, 
          id, 
          matrixIdentities,
          wsUrl: `ws://127.0.0.1:${config.bridge.port}/ws`,
          registration: { ...registration, ws: undefined }
        }));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  if (url.pathname === "/heartbeat" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const data = JSON.parse(body);
        const { id, directory } = data;

        let registration: OpenCodeRegistration | undefined;

        if (id) {
          registration = registrations.get(id);
        }

        if (!registration && directory) {
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
          res.end(JSON.stringify({
            success: true,
            id: registration.id,
            lastSeen: registration.lastSeen,
            wsConnected: registration.ws?.readyState === WebSocket.OPEN,
          }));
        } else {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Registration not found", id, directory }));
        }
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  if (url.pathname === "/unregister" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const { id } = JSON.parse(body);
        const registration = registrations.get(id);
        
        if (registration) {
          if (registration.ws) {
            registration.ws.close();
          }
          const matrixIdentities = deriveMatrixIdentities(registration.directory);
          for (const identity of matrixIdentities) {
            identityToRegistration.delete(identity);
          }
          for (const roomId of registration.rooms) {
            roomToRegistration.delete(roomId);
          }
          console.log(`[Bridge] Unregistered: ${id}`);
        }
        
        const deleted = registrations.delete(id);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: deleted }));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

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

  if (url.pathname === "/update-rooms" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const { directory, rooms } = JSON.parse(body);

        if (!directory || !Array.isArray(rooms)) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Missing required fields: directory, rooms (array)" }));
          return;
        }

        let updated = false;
        for (const reg of registrations.values()) {
          if (reg.directory === directory) {
            for (const oldRoom of reg.rooms) {
              roomToRegistration.delete(oldRoom);
            }
            reg.rooms = rooms;
            for (const newRoom of rooms) {
              roomToRegistration.set(newRoom, reg);
            }
            reg.lastSeen = Date.now();
            updated = true;
            console.log(`[Bridge] Updated rooms for ${directory}: ${rooms.join(", ")}`);
          }
        }

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: updated, directory, rooms }));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ error: "Not found" }));
}

function handleWebSocketConnection(ws: WebSocket): void {
  console.log("[Bridge] New WebSocket connection");
  
  let authenticatedRegistration: OpenCodeRegistration | null = null;

  ws.on("message", (data) => {
    try {
      const message = JSON.parse(data.toString());

      if (message.type === "auth") {
        const authMsg = message as WsAuthMessage;
        let registration = registrations.get(authMsg.registrationId);

        if (!registration) {
          for (const reg of registrations.values()) {
            if (reg.directory === authMsg.registrationId || reg.id.includes(authMsg.registrationId)) {
              registration = reg;
              break;
            }
          }
        }

        if (registration) {
          if (registration.ws && registration.ws !== ws) {
            registration.ws.close();
          }
          registration.ws = ws;
          registration.lastSeen = Date.now();
          authenticatedRegistration = registration;
          
          console.log(`[Bridge] WebSocket authenticated for ${registration.id}`);
          ws.send(JSON.stringify({ type: "auth_success", registrationId: registration.id }));
        } else {
          console.log(`[Bridge] WebSocket auth failed - registration not found: ${authMsg.registrationId}`);
          ws.send(JSON.stringify({ type: "auth_error", error: "Registration not found" }));
        }
      }
    } catch (error) {
      console.error("[Bridge] Error processing WebSocket message:", error);
    }
  });

  ws.on("close", () => {
    if (authenticatedRegistration) {
      console.log(`[Bridge] WebSocket disconnected for ${authenticatedRegistration.id}`);
      if (authenticatedRegistration.ws === ws) {
        authenticatedRegistration.ws = null;
      }
    }
  });

  ws.on("error", (error) => {
    console.error("[Bridge] WebSocket error:", error);
  });
}

async function main(): Promise<void> {
  console.log("[Bridge] Starting OpenCode Matrix Bridge with WebSocket support...");

  await loadAgentMappings();

  const server = createServer(handleRequest);
  
  const wss = new WebSocketServer({ server, path: "/ws" });
  wss.on("connection", handleWebSocketConnection);
  
  server.listen(config.bridge.port, () => {
    console.log(`[Bridge] HTTP/WS server listening on port ${config.bridge.port}`);
  });

  await initMatrix();

  setInterval(cleanupStaleRegistrations, 60000);

  console.log("[Bridge] OpenCode Matrix Bridge ready!");
  console.log("[Bridge] Endpoints:");
  console.log(`  POST /register    - Register OpenCode instance`);
  console.log(`  POST /heartbeat   - Keep registration alive`);
  console.log(`  POST /unregister  - Unregister OpenCode instance`);
  console.log(`  GET  /registrations - List registered instances`);
  console.log(`  GET  /rooms       - List available agent rooms`);
  console.log(`  GET  /health      - Health check`);
  console.log(`  WS   /ws          - WebSocket endpoint for message delivery`);
}

process.on("SIGTERM", () => {
  process.exit(0);
});

main().catch(console.error);
