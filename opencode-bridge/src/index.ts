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
import { RegistrationDB, buildDatabaseUrlCandidates } from "./db.js";
import { isAdmissibleForRoomDelivery } from "./sender-filter.js";
import {
  createBucket,
  tryConsume,
  type TokenBucket,
  type TokenBucketConfig,
} from "./rate-limiter.js";

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

interface QueuedMessage {
  roomId: string;
  sender: string;
  senderMxid: string;
  body: string;
  eventId: string;
  queuedAt: number;
}

// WebSocket keepalive interval (ping every 30s to detect dead connections)
const WS_PING_INTERVAL_MS = 30000;

// Matrix init retry backoff: 1s, 2s, 4s, ..., capped at 30s. Retries forever.
const MATRIX_INIT_BASE_DELAY_MS = 1000;
const MATRIX_INIT_MAX_DELAY_MS = 30000;

// Agent mappings init retry backoff: 1s, 2s, 4s, ..., capped at 60s. Retries until first success.
// Required because agentBotMxids gates the Path 1 bot-sender drop; without it the feedback loop can re-open.
const MAPPINGS_INIT_BASE_DELAY_MS = 1000;
const MAPPINGS_INIT_MAX_DELAY_MS = 60000;

// Per-room token bucket for outbound Matrix posts. 20-msg burst, 5/sec sustained.
// Hard cap so that any future routing bug can't turn into an unbounded flood.
const OUTBOUND_RATE_CONFIG: TokenBucketConfig = { capacity: 20, refillPerSec: 5 };
const OUTBOUND_DROP_LOG_WINDOW_MS = 5000;

// Dedup window for incoming Matrix events. Protects against SDK redelivery
// (rejoin / initial sync) so one logical message can't reach a plugin twice.
const PROCESSED_EVENT_TTL_MS = 5 * 60 * 1000;

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
let registrationDb: RegistrationDB | null = null;
let matrixClient: sdk.MatrixClient | null = null;
let agentMappings: Record<string, AgentMapping> = {};
let agentMappingsByRoom: Map<string, AgentMapping> = new Map();
let agentBotMxids: Set<string> = new Set();
let agentMappingsLastFetched = 0;
let matrixInitAttempt = 0;
let matrixInitScheduled = false;
let matrixInitInFlight = false;
let mappingsInitialized = false;
let mappingsInitAttempt = 0;
let mappingsInitScheduled = false;
const outboundRoomBuckets = new Map<string, TokenBucket>();
const outboundRoomDropStats = new Map<string, { dropped: number; lastLogMs: number }>();
const processedEventTimestamps = new Map<string, number>();
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

const OC_MXID_SHAPE = /^@oc_[a-z0-9_]+(_v2)?:[a-z0-9.-]+$/i;

function extractOpenCodeMentions(body: string, mentionUserIds?: readonly string[]): string[] {
  // Pill clients write the target's displayname into body and the real MXID
  // into m.mentions.user_ids (MSC 3952). Body regex alone misses them whenever
  // the OC session's displayname doesn't equal its localpart.
  const found = new Set<string>();

  if (mentionUserIds && mentionUserIds.length > 0) {
    for (const mxid of mentionUserIds) {
      if (typeof mxid === "string" && OC_MXID_SHAPE.test(mxid)) {
        found.add(mxid);
      }
    }
  }

  const lines = body.split('\n');
  let actualMessage = body;

  if (lines[0]?.startsWith('> ')) {
    const firstNonQuoteIdx = lines.findIndex((line, idx) =>
      idx > 0 && line === '' && lines[idx - 1].startsWith('>')
    );
    if (firstNonQuoteIdx >= 0 && firstNonQuoteIdx < lines.length - 1) {
      actualMessage = lines.slice(firstNonQuoteIdx + 1).join('\n');
    }
  }

  const mentionRegex = /@oc_[a-z0-9_]+(_v2)?:[a-z0-9._-]+/gi;
  for (const match of actualMessage.match(mentionRegex) || []) {
    found.add(match);
  }
  return [...found];
}

async function loadAgentMappings(): Promise<boolean> {
  try {
    agentMappings = await fetchAgentMappings(config.matrixApi.baseUrl, fetch);
    agentMappingsByRoom = new Map(Object.values(agentMappings).map((m) => [m.room_id, m]));
    agentBotMxids = new Set(
      Object.values(agentMappings)
        .map((m) => m.matrix_user_id)
        .filter((id): id is string => typeof id === "string" && id.length > 0)
    );
    agentMappingsLastFetched = Date.now();
    return true;
  } catch (err) {
    console.warn(`[Bridge] Failed to load agent mappings:`, err);
    return false;
  }
}

function scheduleMappingsInit(delayMs: number): void {
  if (mappingsInitScheduled || mappingsInitialized) return;
  mappingsInitScheduled = true;
  setTimeout(() => {
    mappingsInitScheduled = false;
    loadAgentMappings().then((ok) => {
      if (ok) {
        mappingsInitialized = true;
        mappingsInitAttempt = 0;
        console.log(
          `[Bridge] Agent mappings loaded (${agentBotMxids.size} bot identities tracked)`,
        );
        return;
      }
      mappingsInitAttempt++;
      const backoff = Math.min(
        MAPPINGS_INIT_BASE_DELAY_MS * Math.pow(2, Math.max(0, mappingsInitAttempt - 1)),
        MAPPINGS_INIT_MAX_DELAY_MS,
      );
      console.warn(
        `[Bridge] Agent mappings init attempt ${mappingsInitAttempt} failed. Retrying in ${backoff}ms.`,
      );
      scheduleMappingsInit(backoff);
    });
  }, delayMs);
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

function getOutboundBucket(roomId: string, nowMs: number): TokenBucket {
  let bucket = outboundRoomBuckets.get(roomId);
  if (!bucket) {
    bucket = createBucket(OUTBOUND_RATE_CONFIG, nowMs);
    outboundRoomBuckets.set(roomId, bucket);
  }
  return bucket;
}

function recordOutboundDrop(roomId: string, nowMs: number): void {
  let stats = outboundRoomDropStats.get(roomId);
  if (!stats) {
    stats = { dropped: 0, lastLogMs: nowMs };
    outboundRoomDropStats.set(roomId, stats);
  }
  stats.dropped++;
  if (nowMs - stats.lastLogMs >= OUTBOUND_DROP_LOG_WINDOW_MS) {
    console.warn(
      `[Bridge] Rate-limit dropped ${stats.dropped} outbound messages for room ${roomId} in the last ${nowMs - stats.lastLogMs}ms`,
    );
    stats.dropped = 0;
    stats.lastLogMs = nowMs;
  }
}

async function handleOutboundMessage(
  registration: OpenCodeRegistration,
  outbound: WsOutboundMessage
): Promise<void> {
  const roomId = registration.rooms[0] || "";

  if (!roomId || !matrixClient) return;

  // Silently discard <no-reply/> responses — posting them to Matrix
  // causes an echo loop (bridge sees its own message as new input).
  const trimmed = (outbound.content || "").trim();
  if (trimmed === "<no-reply/>" || trimmed === "`<no-reply/>`") {
    return;
  }

  const now = Date.now();
  const bucket = getOutboundBucket(roomId, now);
  if (!tryConsume(bucket, OUTBOUND_RATE_CONFIG, now)) {
    recordOutboundDrop(roomId, now);
    return;
  }

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

async function restoreRegistrationsFromDb(): Promise<void> {
  if (!registrationDb) return;

  try {
    const rows = await registrationDb.getAll();
    for (const row of rows) {
      const sessionId = row.session_id || "restored";
      const id = `${row.directory}:${sessionId}`;
      const registration: OpenCodeRegistration = {
        id,
        directory: row.directory,
        sessionId,
        rooms: Array.isArray(row.rooms) ? row.rooms : [],
        registeredAt: new Date(row.created_at).getTime(),
        lastSeen: new Date(row.updated_at).getTime(),
        ws: null,
        status: "disconnected",
        messageQueue: [],
      };

      registrations.set(id, registration);
      directoryToRegistration.set(row.directory, registration);

      const matrixIdentities = Array.isArray(row.identity_mxids) && row.identity_mxids.length > 0
        ? row.identity_mxids
        : deriveMatrixIdentities(row.directory);
      for (const identity of matrixIdentities) {
        identityToRegistration.set(identity, registration);
      }

      for (const roomId of registration.rooms) {
        roomToRegistration.set(roomId, registration);
      }
    }

    if (rows.length > 0) {
      console.log(`[Bridge] Restored ${rows.length} registrations from PostgreSQL`);
    }
  } catch (err) {
    console.warn("[Bridge] Failed to restore registrations from PostgreSQL, continuing in-memory only:", err);
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

function wasEventProcessed(eventId: string, nowMs: number): boolean {
  if (!eventId) return false;
  const ts = processedEventTimestamps.get(eventId);
  if (ts === undefined) return false;
  if (nowMs - ts > PROCESSED_EVENT_TTL_MS) {
    processedEventTimestamps.delete(eventId);
    return false;
  }
  return true;
}

function markEventProcessed(eventId: string, nowMs: number): void {
  if (!eventId) return;
  processedEventTimestamps.set(eventId, nowMs);
  if (processedEventTimestamps.size > 5000) {
    const cutoff = nowMs - PROCESSED_EVENT_TTL_MS;
    for (const [key, timestamp] of processedEventTimestamps) {
      if (timestamp < cutoff) processedEventTimestamps.delete(key);
    }
  }
}

async function handleMatrixMessage(event: sdk.MatrixEvent, room: sdk.Room): Promise<void> {
  if (event.getSender() === matrixClient?.getUserId()) return;
  if (event.getType() !== "m.room.message") return;

  const content = event.getContent();
  if (content.msgtype !== "m.text" && content.msgtype !== "m.notice") return;

  const roomId = room.roomId;
  const senderMxid = event.getSender() || "unknown";
  const body = content.body || "";
  const eventId = event.getId() || "";
  const rawMentions = (content as { "m.mentions"?: { user_ids?: unknown } })["m.mentions"]?.user_ids;
  const mentionUserIds: string[] = Array.isArray(rawMentions)
    ? rawMentions.filter((id): id is string => typeof id === "string")
    : [];

  const now = Date.now();
  if (wasEventProcessed(eventId, now)) {
    console.log(`[Bridge] Duplicate event ${eventId} in room ${roomId} from ${senderMxid}, skipping`);
    return;
  }
  markEventProcessed(eventId, now);

  const senderMember = room.getMember?.(senderMxid);
  const senderName = senderMember?.name || getAgentForRoom(roomId)?.agent_name || senderMxid;

  // Path 1: Room-registered delivery (direct room → WebSocket mapping)
  const roomRegistration = roomToRegistration.get(roomId);
  if (roomRegistration) {
    const roomOwnerIdentities = deriveMatrixIdentities(roomRegistration.directory);
    const admission = isAdmissibleForRoomDelivery({
      senderMxid,
      body,
      mentionUserIds,
      roomOwnerIdentities,
      agentBotMxids,
      matrixDomain: MATRIX_DOMAIN,
    });
    if (!admission.admit) {
      console.log(`[Bridge] Room-registered drop (${admission.reason}): room=${roomId} sender=${senderMxid}`);
      return;
    }
    const forwarded = forwardToOpenCode(roomRegistration, roomId, senderName, senderMxid, body, eventId);
    if (!forwarded) {
      queueMessage(roomRegistration, roomId, senderName, senderMxid, body, eventId);
    }
    console.log(`[Bridge] Room-registered forward to ${roomRegistration.id}: ${forwarded ? 'SUCCESS' : 'QUEUED'} room=${roomId} event=${eventId} - "${body.substring(0, 80)}"`);
    return;
  }

  // Path 2: Mention-based delivery (@oc_* mentions in body or m.mentions pills)
  const mentions = extractOpenCodeMentions(body, mentionUserIds);
  if (mentions.length === 0) return;

  for (const mention of mentions) {
    // Don't forward a mention to the sender themselves (echo prevention)
    if (mention === senderMxid) continue;

    const registration = identityToRegistration.get(mention);
    if (registration) {
      if (forwardToOpenCode(registration, roomId, senderName, senderMxid, body, eventId)) {
        console.log(`[Bridge] Mention-based forward to ${registration.id} for ${mention}: SUCCESS room=${roomId} event=${eventId}`);
      } else {
        queueMessage(registration, roomId, senderName, senderMxid, body, eventId);
        console.log(`[Bridge] Mention-based forward to ${registration.id} for ${mention}: QUEUED room=${roomId} event=${eventId}`);
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

function computeMatrixInitBackoff(): number {
  return Math.min(
    MATRIX_INIT_BASE_DELAY_MS * Math.pow(2, Math.max(0, matrixInitAttempt - 1)),
    MATRIX_INIT_MAX_DELAY_MS,
  );
}

function scheduleMatrixInit(delayMs: number): void {
  if (matrixInitScheduled || matrixInitInFlight) return;
  matrixInitScheduled = true;
  setTimeout(() => {
    matrixInitScheduled = false;
    initMatrix().catch((err) => {
      console.error(`[Bridge] initMatrix threw unexpectedly:`, err);
      scheduleMatrixInit(computeMatrixInitBackoff());
    });
  }, delayMs);
}

async function initMatrix(): Promise<void> {
  if (!config.matrix.accessToken) {
    console.warn("[Bridge] MATRIX_ACCESS_TOKEN not set — Matrix integration disabled");
    return;
  }
  if (matrixClient) return;
  if (matrixInitInFlight) return;
  matrixInitInFlight = true;
  matrixInitAttempt++;

  try {
    // Resolve the actual user owning the access token via /whoami so the
    // client identity can never drift from the token (previously hard-coded,
    // which silently broke routing after token rotations / user renames).
    let resolvedUserId: string;
    try {
      const whoamiRes = await fetch(
        `${config.matrix.homeserverUrl.replace(/\/$/, "")}/_matrix/client/v3/account/whoami`,
        { headers: { Authorization: `Bearer ${config.matrix.accessToken}` } },
      );
      if (!whoamiRes.ok) {
        throw new Error(`whoami HTTP ${whoamiRes.status}`);
      }
      const whoami = (await whoamiRes.json()) as { user_id?: string };
      if (!whoami.user_id) throw new Error("whoami response missing user_id");
      resolvedUserId = whoami.user_id;
      console.log(`[Bridge] Resolved Matrix identity via whoami: ${resolvedUserId}`);
    } catch (err: any) {
      const backoff = computeMatrixInitBackoff();
      console.error(
        `[Bridge] initMatrix attempt ${matrixInitAttempt} failed (whoami): ${err?.message || err}. Retrying in ${backoff}ms.`,
      );
      scheduleMatrixInit(backoff);
      return;
    }

    let client: sdk.MatrixClient;
    try {
      client = sdk.createClient({
        baseUrl: config.matrix.homeserverUrl,
        accessToken: config.matrix.accessToken,
        userId: resolvedUserId,
      });

      client.on(sdk.RoomEvent.Timeline, (event, room, toStartOfTimeline) => {
        if (toStartOfTimeline) return;
        if (room) {
          handleMatrixMessage(event, room).catch((err) => {
            console.error(`[Bridge] Error handling Matrix message in ${room?.roomId}:`, err);
          });
        }
      });

      client.on(sdk.RoomMemberEvent.Membership, async (_event, member) => {
        if (member.membership === "invite" && member.userId === client.getUserId()) {
          try {
            await client.joinRoom(member.roomId);
          } catch (err: any) {
            console.warn(`[Bridge] Failed to auto-join room ${member.roomId}:`, err?.message || err);
          }
        }
      });

      await client.startClient({ initialSyncLimit: 1 });
    } catch (err: any) {
      const backoff = computeMatrixInitBackoff();
      console.error(
        `[Bridge] initMatrix attempt ${matrixInitAttempt} failed (startClient): ${err?.message || err}. Retrying in ${backoff}ms.`,
      );
      scheduleMatrixInit(backoff);
      return;
    }

    matrixClient = client;
    matrixInitAttempt = 0;
    console.log(`[Bridge] Matrix client ready as ${resolvedUserId}`);
  } finally {
    matrixInitInFlight = false;
  }
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
    req.on("end", async () => {
      try {
        const data = JSON.parse(body);
        const { sessionId, directory, rooms } = data;

        if (!sessionId || !directory) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Missing required fields: sessionId, directory" }));
          return;
        }

        const id = `${directory}:${sessionId}`;
        const effectiveRooms = Array.isArray(rooms) ? rooms : [];
        
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

          if (registrationDb) {
            const matrixIdentities = deriveMatrixIdentities(directory);
            try {
              await registrationDb.upsert(directory, effectiveRooms, matrixIdentities, sessionId);
            } catch (err) {
              console.warn(`[Bridge] Failed to persist registration for ${directory}:`, err);
            }
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

        if (registrationDb) {
          try {
            await registrationDb.upsert(directory, effectiveRooms, matrixIdentities, sessionId);
          } catch (err) {
            console.warn(`[Bridge] Failed to persist registration for ${directory}:`, err);
          }
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
    req.on("end", async () => {
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

        if (registrationDb && registration) {
          try {
            await registrationDb.remove(registration.directory);
          } catch (err) {
            console.warn(`[Bridge] Failed to remove registration for ${registration.directory}:`, err);
          }
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
    req.on("end", async () => {
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

        if (registrationDb && updated) {
          try {
            await registrationDb.updateRooms(directory, rooms);
          } catch (err) {
            console.warn(`[Bridge] Failed to persist room updates for ${directory}:`, err);
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
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    console.warn("[Bridge] DATABASE_URL not set, running with in-memory registrations only");
  } else {
    const databaseUrlCandidates = buildDatabaseUrlCandidates(databaseUrl);
    let initialized = false;
    let lastError: unknown = null;

    for (let index = 0; index < databaseUrlCandidates.length; index += 1) {
      const candidateUrl = databaseUrlCandidates[index];
      try {
        if (index > 0) {
          console.warn(`[Bridge] Retrying PostgreSQL with fallback URL (${index + 1}/${databaseUrlCandidates.length})`);
        }

        registrationDb = new RegistrationDB(candidateUrl);
        await registrationDb.init();
        await restoreRegistrationsFromDb();
        initialized = true;
        break;
      } catch (err) {
        lastError = err;
        registrationDb = null;
      }
    }

    if (!initialized) {
      console.warn("[Bridge] PostgreSQL unavailable, running with in-memory registrations only:", lastError);
    }
  }

  const server = createServer(handleRequest);

  const wss = new WebSocketServer({ server, path: "/ws" });
  wss.on("connection", handleWebSocketConnection);

  server.listen(config.bridge.port);
  scheduleMappingsInit(0);
  scheduleMatrixInit(0);
  setInterval(cleanupStaleRegistrations, 60000);
}

process.on("SIGTERM", () => {
  process.exit(0);
});

main().catch(console.error);
