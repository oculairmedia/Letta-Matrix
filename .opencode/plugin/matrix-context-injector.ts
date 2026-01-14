import type { Plugin } from "@opencode-ai/plugin";
import { existsSync } from "fs";
import { readFile, unlink, writeFile } from "fs/promises";
import path from "path";
import { loadMatrixConfig } from "./lib/matrix-config";
import { resolveMatrixCredentials } from "./lib/matrix-credentials";
import { MatrixSyncClient, type MatrixMessage } from "./lib/matrix-sync-client";

const DEDUPE_TTL_MS = 60 * 60 * 1000;
const DEDUPE_CLEANUP_MS = 60 * 1000;

class DedupeCache {
  private entries = new Map<string, number>();
  private interval: NodeJS.Timeout;

  constructor(private ttlMs: number, cleanupMs: number) {
    this.interval = setInterval(() => this.cleanup(), cleanupMs);
  }

  has(key: string): boolean {
    const expiresAt = this.entries.get(key);
    if (!expiresAt) return false;
    if (expiresAt <= Date.now()) {
      this.entries.delete(key);
      return false;
    }
    return true;
  }

  add(key: string): void {
    this.entries.set(key, Date.now() + this.ttlMs);
  }

  stop(): void {
    clearInterval(this.interval);
  }

  private cleanup(): void {
    const now = Date.now();
    for (const [key, expiresAt] of this.entries.entries()) {
      if (expiresAt <= now) {
        this.entries.delete(key);
      }
    }
  }
}

async function getActiveSessionId(client: any): Promise<string | null> {
  try {
    const response = await client.session.list();
    if (response.data && response.data.length > 0) {
      const sorted = [...response.data].sort((a: any, b: any) =>
        new Date(b.time?.updated || 0).getTime() - new Date(a.time?.updated || 0).getTime()
      );
      return sorted[0].id;
    }
  } catch {
    return null;
  }
  return null;
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
        `Matrix injector already running. Remove lock at ${lockPath}. ${contents ? `Details: ${contents}` : ""}`
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

function formatInjection(message: MatrixMessage, label: string): string {
  return `[Matrix from ${message.displayName} in ${label}]
${message.body}`;
}

export const MatrixContextInjector: Plugin = async ({ client, directory, worktree }) => {
  const baseDir = worktree || directory;
  const lockPath = path.join(baseDir, ".opencode", "matrix.lock");
  const dedupe = new DedupeCache(DEDUPE_TTL_MS, DEDUPE_CLEANUP_MS);

  const log = (level: "debug" | "info" | "warn" | "error", message: string, extra?: any) => {
    client.app.log({
      body: {
        service: "matrix-context-injector",
        level,
        message,
        extra,
      },
    }).catch(() => undefined);
  };

  let syncClient: MatrixSyncClient | null = null;

  const shutdown = () => {
    dedupe.stop();
    if (syncClient) {
      syncClient.stop().catch(() => undefined);
    }
    releaseLock(lockPath).catch(() => undefined);
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

  // Initialize in background - don't block plugin loading
  setTimeout(async () => {
    try {
      await acquireLock(lockPath);
    } catch (error: any) {
      log("error", error.message);
      client.tui.showToast({
        body: { message: error.message, variant: "error" },
      }).catch(() => undefined);
      return;
    }

    try {
      const config = await loadMatrixConfig(baseDir);
      const credentials = await resolveMatrixCredentials({
        directory,
        worktree: baseDir,
        homeserver: config.homeserver,
        userIdOverride: config.userId,
      });

      syncClient = new MatrixSyncClient({
        homeserver: config.homeserver,
        accessToken: credentials.accessToken,
        userId: credentials.userId,
        subscribeRooms: config.subscribeRooms,
        msgtypes: config.filters.msgtypes,
      });

      syncClient.on("message", async (message: MatrixMessage) => {
        if (dedupe.has(message.eventId)) return;
        dedupe.add(message.eventId);

        const label = config.roomLabels[message.roomId] || message.roomId;
        const injection = formatInjection(message, label);
        const sessionId = await getActiveSessionId(client);

        if (!sessionId) {
          log("warn", "No active session available for Matrix injection", {
            roomId: message.roomId,
            sender: message.sender,
          });
          client.tui.showToast({
            body: { message: "Matrix: no active session", variant: "warning" },
          }).catch(() => undefined);
          return;
        }

        try {
          await client.session.prompt({
            path: { id: sessionId },
            body: {
              noReply: config.noReply,
              parts: [{ type: "text", text: injection }],
            },
          });
          log("info", "Injected Matrix message", {
            roomId: message.roomId,
            eventId: message.eventId,
            sessionId,
          });
        } catch (err: any) {
          log("error", "Failed to inject Matrix message", {
            error: err?.message || err,
            roomId: message.roomId,
            eventId: message.eventId,
          });
          client.tui.showToast({
            body: { message: `Matrix inject failed: ${err?.message || err}`, variant: "error" },
          }).catch(() => undefined);
        }
      });

      await syncClient.start();
      await syncClient.joinRooms();
      log("info", "Matrix context injector started", {
        userId: credentials.userId,
        rooms: config.subscribeRooms,
        configPath: config.configPath,
        credentialsOrigin: credentials.origin,
      });
    } catch (error: any) {
      log("error", "Matrix context injector failed to start", {
        error: error?.message || error,
      });
      client.tui.showToast({
        body: { message: `Matrix injector error: ${error?.message || error}`, variant: "error" },
      }).catch(() => undefined);
      shutdown();
    }
  }, 2000);

  return {};
};
