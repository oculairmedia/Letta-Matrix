import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { homedir } from "node:os";
import type { MatrixConfig, MatrixMessage } from "./types";

interface MatrixClientOptions {
  statePath: string;
  timeoutMs: number;
}

interface MatrixSyncResponse {
  next_batch?: string;
  rooms?: {
    join?: Record<
      string,
      {
        timeline?: {
          events?: Array<{ type?: string; sender?: string; origin_server_ts?: number; content?: Record<string, unknown> }>;
        };
      }
    >;
  };
}

export class MatrixClient {
  private config: MatrixConfig;
  private statePath: string;
  private timeoutMs: number;

  constructor(config: MatrixConfig, options: MatrixClientOptions) {
    this.config = config;
    this.statePath = options.statePath;
    this.timeoutMs = options.timeoutMs;
  }

  async getNewMessages(): Promise<MatrixMessage[]> {
    const since = await this.loadSyncToken();
    
    if (!since) {
      const initToken = await this.initializeSyncToken();
      if (initToken) {
        await this.saveSyncToken(initToken);
      }
      return [];
    }
    
    const url = new URL(`${this.config.homeserver}/_matrix/client/v3/sync`);
    url.searchParams.set("timeout", "0");
    url.searchParams.set("since", since);
    url.searchParams.set(
      "filter",
      JSON.stringify({
        room: {
          timeline: {
            limit: 20,
          },
        },
      }),
    );

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url.toString(), {
        method: "GET",
        headers: {
          Authorization: `Bearer ${this.config.accessToken}`,
        },
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Matrix sync failed: ${response.status} ${response.statusText}`);
      }

      const data = (await response.json()) as MatrixSyncResponse;
      if (data.next_batch) {
        await this.saveSyncToken(data.next_batch);
      }

      return this.extractMessages(data);
    } catch (error: unknown) {
      if ((error as Error).name === "AbortError") {
        const freshToken = await this.initializeSyncToken();
        if (freshToken) {
          await this.saveSyncToken(freshToken);
        }
        return [];
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  private extractMessages(data: MatrixSyncResponse): MatrixMessage[] {
    const joinedRooms = data.rooms?.join ?? {};
    const allowedSenders = new Set(this.config.filters.senders);
    const messages: MatrixMessage[] = [];

    const acceptAllRooms = this.config.rooms.length === 1 && this.config.rooms[0] === "*";
    const roomIds = acceptAllRooms ? null : new Set(this.config.rooms);

    for (const [roomId, roomData] of Object.entries(joinedRooms)) {
      if (roomIds && !roomIds.has(roomId)) continue;
      const events = roomData.timeline?.events ?? [];

      for (const event of events) {
        if (event.type !== "m.room.message") continue;
        if (!event.sender || event.sender === this.config.userId) continue;
        if (allowedSenders.size > 0 && !allowedSenders.has(event.sender)) continue;

        const content = event.content ?? {};
        const msgtype = content.msgtype;
        if (msgtype !== this.config.filters.msgtype) continue;

        const body = typeof content.body === "string" ? content.body : "";
        if (!body) continue;

        messages.push({
          roomId,
          sender: event.sender,
          body,
          timestamp: event.origin_server_ts ?? Date.now(),
        });
      }
    }

    return messages.sort((a, b) => a.timestamp - b.timestamp);
  }

  private async initializeSyncToken(): Promise<string | null> {
    const url = new URL(`${this.config.homeserver}/_matrix/client/v3/sync`);
    url.searchParams.set("timeout", "0");
    url.searchParams.set("filter", JSON.stringify({ room: { timeline: { limit: 1 } } }));

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url.toString(), {
        method: "GET",
        headers: { Authorization: `Bearer ${this.config.accessToken}` },
        signal: controller.signal,
      });

      if (!response.ok) return null;
      const data = (await response.json()) as MatrixSyncResponse;
      return data.next_batch || null;
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  }

  private async loadSyncToken(): Promise<string | null> {
    try {
      const raw = await readFile(this.statePath, "utf-8");
      const parsed = JSON.parse(raw) as { nextBatch?: string };
      return typeof parsed.nextBatch === "string" ? parsed.nextBatch : null;
    } catch (error: unknown) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return null;
      }
      return null;
    }
  }

  private async saveSyncToken(token: string): Promise<void> {
    await mkdir(path.dirname(this.statePath), { recursive: true });
    await writeFile(this.statePath, JSON.stringify({ nextBatch: token }), "utf-8");
  }
}

export function getStatePath(cwd: string, isGlobal?: boolean, userId?: string): string {
  if (isGlobal && userId) {
    // Per-identity state file when using global config with per-project identities
    const safeUserId = userId.replace(/[@:]/g, '_');
    return path.join(homedir(), ".config/claude-code-matrix", `state-${safeUserId}.json`);
  }
  if (isGlobal) {
    return path.join(homedir(), ".config/claude-code-matrix", "state.json");
  }
  return path.join(cwd, ".claude", "matrix-context-state.json");
}
