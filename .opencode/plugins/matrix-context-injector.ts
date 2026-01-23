import type { Plugin } from "@opencode-ai/plugin";
import { existsSync } from "fs";
import { mkdir, readFile, unlink, writeFile } from "fs/promises";
import path from "path";
import WebSocket from "ws";

const BRIDGE_URL = process.env.OPENCODE_BRIDGE_URL || "http://127.0.0.1:3201";
const BRIDGE_WS_URL = process.env.OPENCODE_BRIDGE_WS_URL || "ws://127.0.0.1:3201/ws";
const HEARTBEAT_INTERVAL_MS = 2 * 60 * 1000;
const WS_RECONNECT_BASE_MS = 1000;
const WS_RECONNECT_MAX_MS = 30000;

interface MatrixMessage {
  type: "matrix_message";
  sender: string;
  senderMxid: string;
  roomId: string;
  body: string;
  eventId: string;
}

interface OutboundMessage {
  type: "outbound_message";
  role: "assistant" | "user";
  content: string;
  messageId: string;
}

function getSessionUpdatedAtMs(session: any): number {
  const value = session?.time?.updated;
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

async function getActiveSessionId(client: any, targetDirectory: string): Promise<string | null> {
  try {
    const response = await client.session.list();
    const sessions = Array.isArray(response.data) ? response.data : [];

    const targetResolved = path.resolve(targetDirectory);
    const matching = sessions.filter((session: any) => {
      if (!session?.directory || typeof session.directory !== "string") return false;
      return path.resolve(session.directory) === targetResolved;
    });

    if (matching.length === 0) return null;

    const sorted = [...matching].sort(
      (a: any, b: any) => getSessionUpdatedAtMs(b) - getSessionUpdatedAtMs(a)
    );
    return sorted[0].id;
  } catch {
    return null;
  }
}

async function acquireLock(lockPath: string): Promise<void> {
  if (existsSync(lockPath)) {
    const contents = await readFile(lockPath, "utf-8").catch(() => "");
    if (contents) {
      try {
        const parsed = JSON.parse(contents) as { pid?: number };
        if (typeof parsed.pid === "number") {
          try {
            process.kill(parsed.pid, 0);
          } catch {
            await unlink(lockPath).catch(() => undefined);
          }
        }
      } catch {
      }
    }

    if (existsSync(lockPath)) {
      throw new Error(
        `Matrix bridge plugin already running. Remove lock at ${lockPath}. ${contents ? `Details: ${contents}` : ""}`
      );
    }
  }

  const payload = JSON.stringify({
    pid: process.pid,
    startedAt: new Date().toISOString(),
  });
  await writeFile(lockPath, payload, { flag: "wx" });
}

async function releaseLock(lockPath: string): Promise<void> {
  if (!existsSync(lockPath)) return;
  await unlink(lockPath).catch(() => undefined);
}

async function registerWithBridge(
  directory: string,
  sessionId: string
): Promise<{ id: string; wsUrl: string } | null> {
  try {
    const response = await fetch(`${BRIDGE_URL}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sessionId,
        directory,
        rooms: [],
      }),
    });
    if (!response.ok) return null;
    const data = (await response.json()) as { id?: string; wsUrl?: string };
    if (!data.id) return null;
    return { id: data.id, wsUrl: data.wsUrl || BRIDGE_WS_URL };
  } catch {
    return null;
  }
}

async function sendHeartbeat(registrationId: string): Promise<boolean> {
  try {
    const response = await fetch(`${BRIDGE_URL}/heartbeat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: registrationId }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function unregisterFromBridge(registrationId: string): Promise<void> {
  try {
    await fetch(`${BRIDGE_URL}/unregister`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: registrationId }),
    });
  } catch {
  }
}

export const MatrixContextInjector: Plugin = async ({ client, directory, worktree }) => {
  console.log("[Matrix-Plugin] ========== PLUGIN INITIALIZING ==========");
  const baseDir = worktree || directory;
  console.log(`[Matrix-Plugin] baseDir=${baseDir}`);
  const opencodeDirPath = path.join(baseDir, ".opencode");
  const lockPath = path.join(opencodeDirPath, "matrix.lock");

  if (!existsSync(opencodeDirPath)) {
    await mkdir(opencodeDirPath, { recursive: true });
  }

  const log = (level: "debug" | "info" | "warn" | "error", message: string, extra?: any) => {
    console.log(`[Matrix-Plugin] [${level}] ${message}`, extra || "");
    client.app.log({
      body: {
        service: "matrix-bridge-plugin",
        level,
        message,
        extra,
      },
    }).catch(() => undefined);
  };

  let bridgeRegistrationId: string | null = null;
  let heartbeatInterval: NodeJS.Timeout | null = null;
  let ws: WebSocket | null = null;
  let wsReconnectAttempts = 0;
  let wsReconnectTimeout: NodeJS.Timeout | null = null;
  let shuttingDown = false;
  const pendingMessages = new Map<string, { content: string; sentLength: number; timer: NodeJS.Timeout | null }>();
  const STREAM_SETTLE_MS = 500;
  
  const sendOutboundMessage = (role: "assistant" | "user", content: string, messageId: string) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    
    const outbound: OutboundMessage = {
      type: "outbound_message",
      role,
      content,
      messageId,
    };
    
    try {
      ws.send(JSON.stringify(outbound));
      log("debug", "Sent outbound message", { role, messageId, length: content.length });
    } catch (err) {
      log("error", "Failed to send outbound message", { error: err });
    }
  };
  
  const scheduleOutboundSend = (messageId: string, content: string, role: "assistant" | "user") => {
    let entry = pendingMessages.get(messageId);
    
    if (!entry) {
      entry = { content: "", sentLength: 0, timer: null };
      pendingMessages.set(messageId, entry);
    }
    
    entry.content = content;
    
    if (entry.timer) {
      clearTimeout(entry.timer);
    }
    
    entry.timer = setTimeout(() => {
      const e = pendingMessages.get(messageId);
      if (e && e.content.length > e.sentLength) {
        sendOutboundMessage(role, e.content, messageId);
        e.sentLength = e.content.length;
      }
    }, STREAM_SETTLE_MS);
    
    if (pendingMessages.size > 100) {
      const staleKeys = Array.from(pendingMessages.keys()).slice(0, 50);
      staleKeys.forEach(k => {
        const e = pendingMessages.get(k);
        if (e?.timer) clearTimeout(e.timer);
        pendingMessages.delete(k);
      });
    }
  };

  const shutdown = () => {
    shuttingDown = true;
    if (wsReconnectTimeout) {
      clearTimeout(wsReconnectTimeout);
      wsReconnectTimeout = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
    if (heartbeatInterval) {
      clearInterval(heartbeatInterval);
      heartbeatInterval = null;
    }
    if (bridgeRegistrationId) {
      unregisterFromBridge(bridgeRegistrationId).catch(() => undefined);
      bridgeRegistrationId = null;
    }
    releaseLock(lockPath).catch(() => undefined);
  };

  const connectWebSocket = (wsUrl: string, registrationId: string) => {
    if (shuttingDown) return;

    ws = new WebSocket(wsUrl);

    ws.on("open", () => {
      wsReconnectAttempts = 0;
      log("info", "WebSocket connected to bridge", { wsUrl });
      
      ws?.send(JSON.stringify({ type: "auth", registrationId }));
    });

    ws.on("message", async (data) => {
      try {
        const message = JSON.parse(data.toString());

        if (message.type === "auth_success") {
          log("info", "WebSocket authenticated", { registrationId: message.registrationId });
          return;
        }

        if (message.type === "auth_error") {
          log("error", "WebSocket auth failed", { error: message.error });
          return;
        }

        if (message.type === "matrix_message") {
          const matrixMsg = message as MatrixMessage;
          console.log(`[Matrix-Plugin] Received message from ${matrixMsg.sender} in ${matrixMsg.roomId}`);
          
          const sessionId = await getActiveSessionId(client, baseDir);
          console.log(`[Matrix-Plugin] Session lookup for ${baseDir}: ${sessionId || 'NOT FOUND'}`);
          
          if (!sessionId) {
            log("warn", "No active session for Matrix message", {
              sender: matrixMsg.sender,
              roomId: matrixMsg.roomId,
            });
            return;
          }

          const injection = `[Matrix from ${matrixMsg.sender} in ${matrixMsg.roomId}]\n${matrixMsg.body}`;

          try {
            console.log(`[Matrix-Plugin] Injecting message into session ${sessionId}`);
            
            // Use TUI API to append and submit the message
            await client.tui.appendPrompt({ body: { text: injection } });
            await client.tui.submitPrompt();
            
            console.log(`[Matrix-Plugin] Successfully injected message via TUI`);
            log("info", "Injected Matrix message", {
              sender: matrixMsg.sender,
              eventId: matrixMsg.eventId,
              sessionId,
            });
          } catch (err: any) {
            console.error(`[Matrix-Plugin] Failed to inject: ${err?.message || err}`);
            log("error", "Failed to inject Matrix message", {
              error: err?.message || err,
              sender: matrixMsg.sender,
              eventId: matrixMsg.eventId,
            });
          }
        }
      } catch (error) {
        log("error", "Failed to process WebSocket message", { error });
      }
    });

    ws.on("close", () => {
      if (shuttingDown) return;
      
      log("warn", "WebSocket disconnected, reconnecting...");
      scheduleReconnect(wsUrl, registrationId);
    });

    ws.on("error", (error) => {
      log("error", "WebSocket error", { error: error.message });
    });
  };

  const scheduleReconnect = (wsUrl: string, registrationId: string) => {
    if (shuttingDown || wsReconnectTimeout) return;

    wsReconnectAttempts++;
    const delay = Math.min(
      WS_RECONNECT_BASE_MS * Math.pow(2, wsReconnectAttempts - 1),
      WS_RECONNECT_MAX_MS
    );

    log("info", `Reconnecting in ${delay}ms (attempt ${wsReconnectAttempts})`);

    wsReconnectTimeout = setTimeout(() => {
      wsReconnectTimeout = null;
      connectWebSocket(wsUrl, registrationId);
    }, delay);
  };

  process.on("exit", shutdown);
  process.on("SIGINT", () => {
    shutdown();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    shutdown();
    process.exit(0);
  });

  setTimeout(async () => {
    try {
      await acquireLock(lockPath);
    } catch (error: any) {
      log("error", error.message);
      return;
    }

    try {
      const sessionId = `opencode-${process.pid}-${Date.now()}`;
      const result = await registerWithBridge(baseDir, sessionId);

      if (result) {
        bridgeRegistrationId = result.id;

        heartbeatInterval = setInterval(() => {
          if (bridgeRegistrationId) {
            sendHeartbeat(bridgeRegistrationId).catch(() => undefined);
          }
        }, HEARTBEAT_INTERVAL_MS);

        connectWebSocket(result.wsUrl, result.id);

        log("info", "Registered with opencode-bridge", {
          registrationId: bridgeRegistrationId,
          directory: baseDir,
          wsUrl: result.wsUrl,
        });
      } else {
        log("warn", "Failed to register with opencode-bridge", { directory: baseDir });
      }
    } catch (error: any) {
      log("error", "Matrix bridge plugin failed to start", {
        error: error?.message || error,
      });
      shutdown();
    }
  }, 2000);

  console.log("[Matrix-Plugin] ========== RETURNING HOOKS ==========");
  
  return {
    "assistant.message": async ({ message }: { message: any }) => {
      console.log("[Matrix-Plugin] ASSISTANT MESSAGE HOOK FIRED");
      if (!message?.content) {
        console.log("[Matrix-Plugin] No content in message");
        return;
      }
      
      const messageId = message.id || `msg-${Date.now()}`;
      console.log(`[Matrix-Plugin] Sending assistant message: ${message.content.substring(0, 50)}...`);
      sendOutboundMessage("assistant", message.content, messageId);
    },
    
    event: async ({ event }: { event: any }) => {
      console.log(`[Matrix-Plugin] EVENT: ${event.type}`);
    },
  };
};

export default MatrixContextInjector;
