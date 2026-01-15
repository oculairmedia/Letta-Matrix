import {
  createClient,
  ClientEvent,
  MatrixClient,
  MatrixEvent,
  Room,
  RoomEvent,
} from "matrix-js-sdk";
import { EventEmitter } from "events";

function createQuietLogger() {
  const quiet = {
    trace: () => undefined,
    debug: () => undefined,
    info: () => undefined,
    warn: () => undefined,
    error: () => undefined,
    getChild: (_namespace: string) => quiet,
  };

  return quiet;
}

export interface MatrixMessage {
  roomId: string;
  sender: string;
  displayName: string;
  body: string;
  eventId: string;
  timestamp: number;
}

interface MatrixSyncOptions {
  homeserver: string;
  accessToken: string;
  userId: string;
  subscribeRooms: string[];
  msgtypes: string[];
}

export class MatrixSyncClient extends EventEmitter {
  private client: MatrixClient;
  private userId: string;
  private subscribeRooms: Set<string>;
  private msgtypes: Set<string>;
  private startedAt: number;

  constructor(options: MatrixSyncOptions) {
    super();
    this.userId = options.userId;
    this.subscribeRooms = new Set(options.subscribeRooms);
    this.msgtypes = new Set(options.msgtypes);
    this.startedAt = 0;

    this.client = createClient({
      baseUrl: options.homeserver,
      accessToken: options.accessToken,
      userId: options.userId,
      logger: createQuietLogger(),
    });

    this.setupHandlers();
  }

  private setupHandlers(): void {
    this.client.on(RoomEvent.Timeline as any, (event: MatrixEvent, room: Room) => {
      if (!room) return;
      if (!this.subscribeRooms.has(room.roomId)) return;
      if (event.getType() !== "m.room.message") return;
      if (event.getSender() === this.userId) return;

      const timestamp = event.getTs() || Date.now();
      if (this.startedAt && timestamp < this.startedAt) {
        return;
      }

      const content = event.getContent() as { body?: string; msgtype?: string };
      const msgtype = content?.msgtype;
      if (!msgtype || !this.msgtypes.has(msgtype)) return;

      const sender = event.getSender() || "";
      const displayName = event.sender?.name || sender;
      const body = content?.body || "";
      const eventId = event.getId() || "";

      if (!eventId || !sender) return;

      const message: MatrixMessage = {
        roomId: room.roomId,
        sender,
        displayName,
        body,
        eventId,
        timestamp,
      };

      this.emit("message", message);
    });

    this.client.on(ClientEvent.Sync as any, (state: string) => {
      if (state === "PREPARED") {
        this.emit("ready");
      }
    });
  }

  private async withRateLimitRetry<T>(operation: () => Promise<T>): Promise<T> {
    try {
      return await operation();
    } catch (error: any) {
      const retryAfterMs = error?.data?.retry_after_ms || error?.retry_after_ms;
      if (retryAfterMs) {
        await new Promise((resolve) => setTimeout(resolve, retryAfterMs));
        return operation();
      }
      throw error;
    }
  }

  async start(): Promise<void> {
    this.startedAt = Date.now();
    await this.client.startClient({ initialSyncLimit: 0 });

    await new Promise<void>((resolve) => {
      if (this.client.getSyncState() === "PREPARED") {
        resolve();
        return;
      }

      const timeout = setTimeout(() => resolve(), 15000);
      this.once("ready", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  async joinRooms(): Promise<void> {
    for (const roomId of this.subscribeRooms) {
      await this.withRateLimitRetry(() => this.client.joinRoom(roomId));
    }
  }

  async stop(): Promise<void> {
    await this.client.stopClient();
  }
}
