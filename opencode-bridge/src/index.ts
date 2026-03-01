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
  status: 'connected' | 'degraded' | 'disconnected';
  messageQueue: QueuedMessage[];
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

interface QueuedMessage {
  roomId: string;
  sender: string;
  senderMxid: string;
  body: string;
  eventId: string;
  queuedAt: number;
}

let openCodeRoomMappings: Record<string, OpenCodeRoomMapping> = {};
let roomMappingsByDir: Map<string, OpenCodeRoomMapping> = new Map();

function loadOpenCodeRoomMappings(): void {
  const mappingsPath = process.env.OPENCODE_ROOM_MAPPINGS_PATH;
  if (!mappingsPath) return;
  
  try {
    const raw = readFileSync(mappingsPath, "utf-8");
    openCodeRoomMappings = JSON.parse(raw);
    roomMappingsByDir = new Map(Object.values(openCodeRoomMappings).map((m) => [m.directory, m]));
  } catch (err) {
    console.warn(`[Bridge] Failed to load room mappings from ${mappingsPath}:`, err);
  }
}

function getRoomForDirectory(directory: string): string | undefined {
  return roomMappingsByDir.get(directory)?.room_id;
}

function getMappingForDirectory(directory: string): OpenCodeRoomMapping | undefined {
  return roomMappingsByDir.get(directory);
}

// WebSocket keepalive interval (ping every 30s to detect dead connections)
const WS_PING_INTERVAL_MS = 30000;

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
const directoryToRegistration = new Map<string, OpenCodeRegistration>();
let matrixClient: sdk.MatrixClient | null = null;
let agentMappings: Record<string, AgentMapping> = {};
let agentMappingsByRoom: Map<string, AgentMapping> = new Map();
let agentMappingsLastFetched = 0;
const AGENT_MAPPINGS_CACHE_TTL = 300000;

const MATRIX_DOMAIN = process.env.MATRIX_DOMAIN || "matrix.oculair.ca";
const STALE_TIMEOUT = 900000; // 15 minutes (was 5min - too aggressive, caused reconnect storms)
const MAX_QUEUE_SIZE = 50;
const QUEUE_MESSAGE_TTL_MS = 300000; // 5 minutes

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
    agentMappingsByRoom = new Map(Object.values(agentMappings).map((m) => [m.room_id, m]));
    agentMappingsLastFetched = Date.now();
  } catch (err) {
    console.warn(`[Bridge] Failed to load agent mappings:`, err);
  }
}

function getAgentForRoom(roomId: string): AgentMapping | undefined {
  return agentMappingsByRoom.get(roomId);
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
      directoryToRegistration.delete(reg.directory);
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
  } catch (err) {
    console.error(`[Bridge] Failed to send outbound message to Matrix room ${roomId}:`, err);
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
    console.warn(`[Bridge] Cannot forward to ${registration.id}: WebSocket ${!registration.ws ? 'is null' : `readyState=${registration.ws.readyState}`}`);
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
    return true;
  } catch (err) {
    console.error(`[Bridge] Failed to forward to WebSocket for ${registration.id}:`, err);
    return false;
  }
}

function queueMessage(
  registration: OpenCodeRegistration,
  roomId: string,
  sender: string,
  senderMxid: string,
  body: string,
  eventId: string
): void {
  const now = Date.now();
  registration.messageQueue = registration.messageQueue.filter(m => now - m.queuedAt < QUEUE_MESSAGE_TTL_MS);

  if (registration.messageQueue.length >= MAX_QUEUE_SIZE) {
    console.warn(`[Bridge] Queue full for ${registration.id} (${MAX_QUEUE_SIZE} messages), dropping oldest`);
    registration.messageQueue.shift();
  }

  registration.messageQueue.push({ roomId, sender, senderMxid, body, eventId, queuedAt: now });
  console.log(`[Bridge] Queued message for ${registration.id} (queue: ${registration.messageQueue.length})`);
}

function drainMessageQueue(registration: OpenCodeRegistration): void {
  if (registration.messageQueue.length === 0) return;

  const now = Date.now();
  const validMessages = registration.messageQueue.filter(m => now - m.queuedAt < QUEUE_MESSAGE_TTL_MS);
  registration.messageQueue = [];

  if (validMessages.length === 0) return;

  console.log(`[Bridge] Draining ${validMessages.length} queued messages for ${registration.id}`);

  let delivered = 0;
  for (const msg of validMessages) {
    if (forwardToOpenCode(registration, msg.roomId, msg.sender, msg.senderMxid, msg.body, msg.eventId)) {
      delivered++;
    } else {
      const remaining = validMessages.slice(validMessages.indexOf(msg));
      registration.messageQueue = remaining;
      console.warn(`[Bridge] WS dropped during drain for ${registration.id}, re-queued ${remaining.length} messages`);
      break;
    }
  }

  if (delivered > 0) {
    console.log(`[Bridge] Drained ${delivered}/${validMessages.length} messages for ${registration.id}`);
  }
}

function isOpenCodeIdentity(mxid: string): boolean {
  return mxid.includes(":matrix.oculair.ca") && (
    mxid.startsWith("@oc_") || 
    mxid.startsWith("@opencode_")
  );
}

async function handleMatrixMessage(event: sdk.MatrixEvent, room: sdk.Room): Promise<void> {
  // Removed noisy per-event log — only log forwarding outcomes
  if (event.getSender() === matrixClient?.getUserId()) return;
  if (event.getType() !== "m.room.message") return;
  
  const content = event.getContent();
  if (content.msgtype !== "m.text" && content.msgtype !== "m.notice") return;

  const roomId = room.roomId;
  const senderMxid = event.getSender() || "unknown";
  const body = content.body || "";
  const eventId = event.getId() || "";

  // Skip outbound messages we sent ourselves (echo prevention)
  if (wasMessageSentByUs(eventId)) return;

  const senderMember = room.getMember?.(senderMxid);
  const senderName = senderMember?.name || getAgentForRoom(roomId)?.agent_name || senderMxid;

  // Path 1: Room-registered delivery (direct room → WebSocket mapping)
  const roomRegistration = roomToRegistration.get(roomId);
  if (roomRegistration) {
    // Only skip messages from the room's OWN OpenCode identity (echo prevention)
    const roomOwnerIdentities = deriveMatrixIdentities(roomRegistration.directory);
    if (roomOwnerIdentities.includes(senderMxid)) {
      return;
    }
    const forwarded = forwardToOpenCode(roomRegistration, roomId, senderName, senderMxid, body, eventId);
    if (!forwarded) {
      queueMessage(roomRegistration, roomId, senderName, senderMxid, body, eventId);
    }
    console.log(`[Bridge] Room-registered forward to ${roomRegistration.id}: ${forwarded ? 'SUCCESS' : 'QUEUED'} - "${body.substring(0, 80)}"`);
    return;
  }

  // Path 2: Mention-based delivery (@oc_* mentions in message body)
  const mentions = extractOpenCodeMentions(body);
  if (mentions.length === 0) return;

  for (const mention of mentions) {
    // Don't forward a mention to the sender themselves (echo prevention)
    if (mention === senderMxid) continue;

    const registration = identityToRegistration.get(mention);
    if (registration) {
      if (forwardToOpenCode(registration, roomId, senderName, senderMxid, body, eventId)) {
        console.log(`[Bridge] Mention-based forward to ${registration.id} for ${mention}: SUCCESS`);
      } else {
        queueMessage(registration, roomId, senderName, senderMxid, body, eventId);
        console.log(`[Bridge] Mention-based forward to ${registration.id} for ${mention}: QUEUED`);
      }
      continue;
    }
    // Fallback: scan all registrations for a matching identity
    let forwarded = false;
    let matchedReg: OpenCodeRegistration | null = null;
    for (const [, reg] of registrations) {
      const identities = deriveMatrixIdentities(reg.directory);
      if (identities.includes(mention)) {
        matchedReg = reg;
        if (reg.ws && reg.ws.readyState === WebSocket.OPEN) {
          console.log(`[Bridge] Found active WebSocket via ${reg.id} for ${mention}`);
          if (forwardToOpenCode(reg, roomId, senderName, senderMxid, body, eventId)) {
            identityToRegistration.set(mention, reg);
            forwarded = true;
            break;
          }
        }
      }
    }
    if (!forwarded && matchedReg) {
      queueMessage(matchedReg, roomId, senderName, senderMxid, body, eventId);
      identityToRegistration.set(mention, matchedReg);
      console.log(`[Bridge] No active WebSocket for ${mention}, queued on ${matchedReg.id}`);
    } else if (!forwarded) {
      console.log(`[Bridge] No registration found for ${mention}`);
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
      handleMatrixMessage(event, room).catch((err) => {
        console.error(`[Bridge] Error handling Matrix message in ${room?.roomId}:`, err);
      });
    }
  });

  matrixClient.on(sdk.RoomMemberEvent.Membership, async (event, member) => {
    if (member.membership === "invite" && member.userId === matrixClient?.getUserId()) {
      try {
        await matrixClient?.joinRoom(member.roomId);
      } catch (err: any) {
        console.warn(`[Bridge] Failed to auto-join room ${member.roomId}:`, err?.message || err);
      }
    }
  });

  await matrixClient.startClient({ initialSyncLimit: 1 });
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
      connectedPlugins: Array.from(registrations.values()).filter(r => r.status === 'connected').length,
      degradedPlugins: Array.from(registrations.values()).filter(r => r.status === 'degraded').length,
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
        status: r.status,
        wsConnected: r.status === 'connected',
        queuedMessages: r.messageQueue.length,
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
        
        // Clean up old registrations from the same directory
        for (const [oldId, oldReg] of registrations.entries()) {
          if (oldReg.directory === directory && oldId !== id) {
            console.log(`[Bridge] Cleaning up stale registration ${oldId} for ${directory}`);
            if (oldReg.ws) oldReg.ws.close();
            for (const roomId of oldReg.rooms) {
              if (roomToRegistration.get(roomId) === oldReg) {
                roomToRegistration.delete(roomId);
              }
            }
            const oldIdentities = deriveMatrixIdentities(oldReg.directory);
            for (const identity of oldIdentities) {
              if (identityToRegistration.get(identity) === oldReg) {
                identityToRegistration.delete(identity);
              }
            }
            directoryToRegistration.delete(oldReg.directory);
            registrations.delete(oldId);
          }
        }
        
        const existing = registrations.get(id);
        if (existing) {
          existing.lastSeen = Date.now();
          if (!existing.messageQueue) (existing as any).messageQueue = [];
          if (!existing.status) (existing as any).status = existing.ws?.readyState === WebSocket.OPEN ? 'connected' : 'disconnected';
          for (const oldRoom of existing.rooms) {
            roomToRegistration.delete(oldRoom);
          }
          existing.rooms = effectiveRooms;
          for (const roomId of effectiveRooms) {
            roomToRegistration.set(roomId, existing);
          }
          directoryToRegistration.set(directory, existing);
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
          status: 'disconnected',
          messageQueue: [],
        };

        registrations.set(id, registration);
        directoryToRegistration.set(directory, registration);

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
          registration = directoryToRegistration.get(directory);
        }

        if (registration) {
          registration.lastSeen = Date.now();
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({
            success: true,
            id: registration.id,
            lastSeen: registration.lastSeen,
            status: registration.status,
            wsConnected: registration.status === 'connected',
            queuedMessages: registration.messageQueue?.length || 0,
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
          directoryToRegistration.delete(registration.directory);
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
    if (Date.now() - agentMappingsLastFetched > AGENT_MAPPINGS_CACHE_TTL) {
      await loadAgentMappings();
      agentMappingsLastFetched = Date.now();
    }
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
        const reg = directoryToRegistration.get(directory);
        if (reg) {
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

        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: updated, directory, rooms }));
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  // Ensure bridge Matrix client is a member of a room (so it can see responses)
  if (url.pathname === "/ensure-joined" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", async () => {
      try {
        const { room_id } = JSON.parse(body);
        if (!room_id) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Missing required field: room_id" }));
          return;
        }
        if (!matrixClient) {
          res.writeHead(503, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Matrix client not initialized" }));
          return;
        }
        // Check if already joined
        const room = matrixClient.getRoom(room_id);
        if (room && room.getMyMembership() === "join") {
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ success: true, already_joined: true }));
          return;
        }
        // Try to join (will work if invited or public)
        try {
          await matrixClient.joinRoom(room_id);
          console.log(`[Bridge] Joined room ${room_id} for response monitoring`);
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ success: true, joined: true }));
        } catch (joinErr: any) {
          console.log(`[Bridge] Could not join ${room_id}: ${joinErr?.message || joinErr}`);
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ success: false, error: "Could not join room", needs_invite: true }));
        }
      } catch (e) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "Invalid JSON" }));
      }
    });
    return;
  }

  // Direct message delivery to OpenCode instance (bypasses Matrix roundtrip)
  if (url.pathname === "/notify" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      try {
        const { directory, message, sender, agentName } = JSON.parse(body);

        if (!directory || !message) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Missing required fields: directory, message" }));
          return;
        }

        // Find registration for this directory
        const registration = directoryToRegistration.get(directory);

        if (!registration) {
          res.writeHead(404, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: `No registration found for directory: ${directory}` }));
          return;
        }

        const senderName = agentName || sender || "Unknown";
        const eventId = `notify-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const forwarded = forwardToOpenCode(
          registration,
          registration.rooms[0] || "",
          senderName,
          sender || "notify",
          message,
          eventId
        );

        let queued = false;
        if (!forwarded) {
          queueMessage(registration, registration.rooms[0] || "", senderName, sender || "notify", message, eventId);
          queued = true;
        }

        console.log(`[Bridge] /notify to ${registration.id}: ${forwarded ? 'SUCCESS' : 'QUEUED'} from ${senderName}`);

        res.writeHead(forwarded ? 200 : 202, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          success: forwarded,
          queued,
          forwarded_to: registration.id,
          error: !forwarded && !queued ? "WebSocket not connected" : undefined,
        }));
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
  let pingInterval: NodeJS.Timeout | null = null;

  // Start ping/pong keepalive to detect dead connections early
  pingInterval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.ping();
    }
  }, WS_PING_INTERVAL_MS);

  ws.on("pong", () => {
    // Update lastSeen on pong to keep registration alive
    if (authenticatedRegistration) {
      authenticatedRegistration.lastSeen = Date.now();
    }
  });

  ws.on("message", (data) => {
    try {
      const message = JSON.parse(data.toString());

      if (message.type === "auth") {
        const authMsg = message as WsAuthMessage;
        let registration = registrations.get(authMsg.registrationId);

        if (!registration) {
          registration = directoryToRegistration.get(authMsg.registrationId);
        }

        if (!registration) {
          for (const reg of registrations.values()) {
            if (reg.id.includes(authMsg.registrationId)) {
              registration = reg;
              break;
            }
          }
        }

        if (registration) {
          if (registration.ws && registration.ws !== ws) registration.ws.close();
          registration.ws = ws;
          registration.lastSeen = Date.now();
          registration.status = 'connected';
          directoryToRegistration.set(registration.directory, registration);
          authenticatedRegistration = registration;
          
          for (const roomId of registration.rooms) {
            roomToRegistration.set(roomId, registration);
          }
          
          ws.send(JSON.stringify({ type: "auth_success", registrationId: registration.id }));
          console.log(`[Bridge] WebSocket authenticated: ${registration.id}`);
          drainMessageQueue(registration);
        } else {
          ws.send(JSON.stringify({ type: "auth_error", error: "Registration not found" }));
          console.log(`[Bridge] WebSocket auth failed: registration not found for ${authMsg.registrationId}`);
        }
      } else if (message.type === "outbound_message" && authenticatedRegistration) {
        const outbound = message as WsOutboundMessage;
        handleOutboundMessage(authenticatedRegistration, outbound);
      }
    } catch (err) {
      console.warn(`[Bridge] WebSocket message parse error:`, err);
    }
  });

  ws.on("close", (code, reason) => {
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    const regId = authenticatedRegistration?.id || 'unauthenticated';
    console.log(`[Bridge] WebSocket closed for ${regId}: code=${code}, reason=${reason?.toString() || 'none'}`);
    if (authenticatedRegistration?.ws === ws) {
      authenticatedRegistration.ws = null;
      authenticatedRegistration.status = 'degraded';
      console.warn(`[Bridge] Registration ${authenticatedRegistration.id} degraded: WebSocket closed (code=${code})`);
    }
  });

  ws.on("error", (err) => {
    console.warn(`[Bridge] WebSocket error for ${authenticatedRegistration?.id || 'unknown'}:`, err.message);
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
