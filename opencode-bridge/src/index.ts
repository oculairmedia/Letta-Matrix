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
import { readFileSync } from "fs";
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

interface WsOutboundMessage {
  type: "outbound_message";
  role: "assistant" | "user";
  content: string;
  messageId: string;
}

interface OpenCodeRoomMapping {
  directory: string;
  room_id: string;
  identity_id: string;
  identity_mxid: string;
}

let openCodeRoomMappings: Record<string, OpenCodeRoomMapping> = {};

function loadOpenCodeRoomMappings(): void {
  const mappingsPath = process.env.OPENCODE_ROOM_MAPPINGS_PATH;
  if (!mappingsPath) return;
  
  try {
    const raw = readFileSync(mappingsPath, "utf-8");
    openCodeRoomMappings = JSON.parse(raw);
  } catch {
  }
}

function getRoomForDirectory(directory: string): string | undefined {
  for (const mapping of Object.values(openCodeRoomMappings)) {
    if (mapping.directory === directory) {
      return mapping.room_id;
    }
  }
  return undefined;
}

function getMappingForDirectory(directory: string): OpenCodeRoomMapping | undefined {
  for (const mapping of Object.values(openCodeRoomMappings)) {
    if (mapping.directory === directory) {
      return mapping;
    }
  }
  return undefined;
}

// Track message IDs we've sent to Matrix to prevent echo loops
const sentOutboundMessageIds = new Set<string>();
const OUTBOUND_CACHE_TTL_MS = 60000; // 1 minute

function trackOutboundMessage(messageId: string): void {
  sentOutboundMessageIds.add(messageId);
  setTimeout(() => sentOutboundMessageIds.delete(messageId), OUTBOUND_CACHE_TTL_MS);
  
  // Cleanup if too many
  if (sentOutboundMessageIds.size > 1000) {
    const toDelete = Array.from(sentOutboundMessageIds).slice(0, 500);
    toDelete.forEach(id => sentOutboundMessageIds.delete(id));
  }
}

function wasMessageSentByUs(messageId: string): boolean {
  return sentOutboundMessageIds.has(messageId);
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
  } catch {
  }
}

function getAgentForRoom(roomId: string): AgentMapping | undefined {
  return Object.values(agentMappings).find((m) => m.room_id === roomId);
}

function cleanupStaleRegistrations(): void {
  const now = Date.now();
  for (const [id, reg] of registrations.entries()) {
    if (now - reg.lastSeen > STALE_TIMEOUT) {
      if (reg.ws) reg.ws.close();
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

const USER_DISPLAY_NAME = process.env.OPENCODE_USER_NAME || "Emmanuel";

async function handleOutboundMessage(
  registration: OpenCodeRegistration,
  outbound: WsOutboundMessage
): Promise<void> {
  const mapping = getMappingForDirectory(registration.directory);
  let roomId = registration.rooms[0] || mapping?.room_id || "";
  
  if (!roomId || !matrixClient) return;
  
  trackOutboundMessage(outbound.messageId);
  
  let messageContent = outbound.content;
  if (outbound.role === "user") {
    messageContent = `**${USER_DISPLAY_NAME}:** ${outbound.content}`;
  }
  
  try {
    await matrixClient.sendTextMessage(roomId, messageContent);
  } catch {
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
  if (!registration.ws || registration.ws.readyState !== WebSocket.OPEN) return false;

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
    return true;
  } catch {
    return false;
  }
}

function isOpenCodeIdentity(mxid: string): boolean {
  return mxid.includes(":matrix.oculair.ca") && (
    mxid.startsWith("@oc_") || 
    mxid.startsWith("@opencode_")
  );
}

async function handleMatrixMessage(event: sdk.MatrixEvent, room: sdk.Room): Promise<void> {
  if (event.getSender() === matrixClient?.getUserId()) return;
  if (event.getType() !== "m.room.message") return;
  
  const content = event.getContent();
  if (content.msgtype !== "m.text") return;

  const roomId = room.roomId;
  const senderMxid = event.getSender() || "unknown";
  const body = content.body || "";
  const eventId = event.getId() || "";

  if (isOpenCodeIdentity(senderMxid)) return;

  const senderMember = room.getMember?.(senderMxid);
  const senderName = senderMember?.name || getAgentForRoom(roomId)?.agent_name || senderMxid;

  const roomRegistration = roomToRegistration.get(roomId);
  if (roomRegistration) {
    const roomOwnerIdentities = deriveMatrixIdentities(roomRegistration.directory);
    if (roomOwnerIdentities.includes(senderMxid)) return;
    forwardToOpenCode(roomRegistration, roomId, senderName, senderMxid, body, eventId);
    return;
  }

  const mentions = extractOpenCodeMentions(body);
  if (mentions.length === 0) return;

  for (const mention of mentions) {
    const registration = identityToRegistration.get(mention);
    if (registration && forwardToOpenCode(registration, roomId, senderName, senderMxid, body, eventId)) {
      continue;
    }
    console.log(`[Bridge] No active WebSocket for ${registration?.id || mention}, scanning all registrations...`);
    let forwarded = false;
    for (const [, reg] of registrations) {
      if (!reg.ws || reg.ws.readyState !== WebSocket.OPEN) continue;
      const identities = deriveMatrixIdentities(reg.directory);
      if (identities.includes(mention)) {
        console.log(`[Bridge] Found active WebSocket via ${reg.id} for ${mention}`);
        if (forwardToOpenCode(reg, roomId, senderName, senderMxid, body, eventId)) {
          identityToRegistration.set(mention, reg);
          forwarded = true;
          break;
        }
      }
    }
    if (!forwarded) {
      console.log(`[Bridge] No active WebSocket for ${mention} in any registration`);
    }
  }
}

async function initMatrix(): Promise<void> {
  if (!config.matrix.accessToken) return;

  matrixClient = sdk.createClient({
    baseUrl: config.matrix.homeserverUrl,
    accessToken: config.matrix.accessToken,
    userId: "@oc_matrix_synapse_deployment:matrix.oculair.ca",
  });

  matrixClient.on(sdk.RoomEvent.Timeline, (event, room, toStartOfTimeline) => {
    if (toStartOfTimeline) return;
    if (room) {
      handleMatrixMessage(event, room).catch(() => {});
    }
  });

  matrixClient.on(sdk.RoomMemberEvent.Membership, async (event, member) => {
    if (member.membership === "invite" && member.userId === matrixClient?.getUserId()) {
      try {
        await matrixClient?.joinRoom(member.roomId);
      } catch {
      }
    }
  });

  await matrixClient.startClient({ initialSyncLimit: 0 });
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
        
        let effectiveRooms = rooms || [];
        if (effectiveRooms.length === 0) {
          const persistedRoom = getRoomForDirectory(directory);
          if (persistedRoom) {
            effectiveRooms = [persistedRoom];
          }
        }
        
        const existing = registrations.get(id);
        if (existing) {
          existing.lastSeen = Date.now();
          for (const oldRoom of existing.rooms) {
            roomToRegistration.delete(oldRoom);
          }
          existing.rooms = effectiveRooms;
          for (const roomId of effectiveRooms) {
            roomToRegistration.set(roomId, existing);
          }
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
          rooms: effectiveRooms,
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
          if (registration.ws) registration.ws.close();
          const matrixIdentities = deriveMatrixIdentities(registration.directory);
          for (const identity of matrixIdentities) {
            identityToRegistration.delete(identity);
          }
          for (const roomId of registration.rooms) {
            roomToRegistration.delete(roomId);
          }
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
          if (registration.ws && registration.ws !== ws) registration.ws.close();
          registration.ws = ws;
          registration.lastSeen = Date.now();
          authenticatedRegistration = registration;
          
          for (const roomId of registration.rooms) {
            roomToRegistration.set(roomId, registration);
          }
          
          ws.send(JSON.stringify({ type: "auth_success", registrationId: registration.id }));
        } else {
          ws.send(JSON.stringify({ type: "auth_error", error: "Registration not found" }));
        }
      } else if (message.type === "outbound_message" && authenticatedRegistration) {
        const outbound = message as WsOutboundMessage;
        handleOutboundMessage(authenticatedRegistration, outbound);
      }
    } catch {
    }
  });

  ws.on("close", () => {
    if (authenticatedRegistration?.ws === ws) {
      authenticatedRegistration.ws = null;
    }
  });

  ws.on("error", () => {
  });
}

async function main(): Promise<void> {
  loadOpenCodeRoomMappings();
  await loadAgentMappings();

  const server = createServer(handleRequest);
  
  const wss = new WebSocketServer({ server, path: "/ws" });
  wss.on("connection", handleWebSocketConnection);
  
  server.listen(config.bridge.port);
  await initMatrix();
  setInterval(cleanupStaleRegistrations, 60000);
}

process.on("SIGTERM", () => {
  process.exit(0);
});

main().catch(console.error);
