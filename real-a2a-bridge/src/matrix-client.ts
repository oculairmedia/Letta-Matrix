import {
  createClient,
  MatrixClient,
  MatrixEvent,
  Room,
  ClientEvent,
  RoomEvent,
  RoomMemberEvent,
} from "matrix-js-sdk";
import { EventEmitter } from "events";
import type { MatrixMessage } from "./types";

interface AdminConfig {
  homeserver: string;
  username: string;
  password: string;
}

export class MatrixClientWrapper extends EventEmitter {
  private client: MatrixClient;
  private userId: string;
  private monitoredRooms: Set<string>;
  private joinedRooms: Set<string>;
  private adminConfig: AdminConfig | null = null;

  constructor(homeserver: string, accessToken: string, userId: string) {
    super();
    this.userId = userId;
    this.monitoredRooms = new Set();
    this.joinedRooms = new Set();

    this.client = createClient({
      baseUrl: homeserver,
      accessToken: accessToken,
      userId: userId,
    });

    this.setupEventHandlers();
  }

  setAdminConfig(config: AdminConfig): void {
    this.adminConfig = config;
  }

  private setupEventHandlers(): void {
    this.client.on(RoomEvent.Timeline as any, (event: MatrixEvent, room: Room) => {
      if (!room) return;
      if (event.getType() !== "m.room.message") return;
      if (event.getSender() === this.userId) return;

      this.handleMessage(event, room);
    });

    this.client.on(ClientEvent.Sync as any, (state: string) => {
      if (state === "PREPARED") {
        console.log("[Matrix] Client synced and ready");
        this.emit("ready");
      }
    });

    this.client.on(RoomMemberEvent.Membership as any, async (_event: any, member: any) => {
      if (member.membership === "invite" && member.userId === this.userId) {
        const roomId = member.roomId;
        console.log(`[Matrix] Received invite to ${roomId}, auto-accepting...`);
        try {
          await this.client.joinRoom(roomId);
          console.log(`[Matrix] Joined room ${roomId}`);
        } catch (err) {
          console.error(`[Matrix] Failed to join ${roomId}:`, err);
        }
      }
    });
  }

  private handleMessage(event: MatrixEvent, room: Room): void {
    const sender = event.getSender();
    if (!sender) return;

    const content = event.getContent();
    const body = content.body || "";
    const senderObj = event.sender;
    const displayName = senderObj?.name || sender;
    const timestamp = event.getTs() || Date.now();
    const eventId = event.getId() || "";
    const roomId = room.roomId;

    const message: MatrixMessage = {
      sender,
      displayName,
      body,
      timestamp,
      eventId,
      roomId,
      messageId: `matrix:${eventId}`,
    };

    this.emit("message", message);
  }

  async start(): Promise<void> {
    console.log("[Matrix] Starting client...");
    await this.client.startClient({ initialSyncLimit: 10 });

    return new Promise((resolve) => {
      if (this.client.getSyncState() === "PREPARED") {
        resolve();
        return;
      }

      const timeout = setTimeout(() => {
        console.warn("[Matrix] Sync timeout - proceeding anyway");
        resolve();
      }, 30000);

      this.once("ready", () => {
        clearTimeout(timeout);
        resolve();
      });
    });
  }

  async joinRooms(roomIds: string[]): Promise<void> {
    for (const roomId of roomIds) {
      await this.ensureInRoom(roomId);
    }
  }

  async ensureInRoom(roomId: string): Promise<boolean> {
    if (this.joinedRooms.has(roomId)) {
      return true;
    }

    try {
      await this.client.joinRoom(roomId);
      this.joinedRooms.add(roomId);
      this.monitoredRooms.add(roomId);
      console.log(`[Matrix] Joined room: ${roomId}`);
      return true;
    } catch (joinErr: any) {
      if (joinErr.errcode !== "M_FORBIDDEN") {
        console.error(`[Matrix] Failed to join ${roomId}:`, joinErr.message || joinErr);
        return false;
      }

      if (!this.adminConfig) {
        console.warn(`[Matrix] Cannot join ${roomId} - not invited and no admin config`);
        return false;
      }

      return await this.adminInviteAndJoin(roomId);
    }
  }

  private async adminInviteAndJoin(roomId: string): Promise<boolean> {
    if (!this.adminConfig) return false;

    console.log(`[Matrix] Attempting admin-assisted invite to ${roomId}...`);
    
    try {
      const loginRes = await fetch(`${this.adminConfig.homeserver}/_matrix/client/v3/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type: "m.login.password",
          identifier: { type: "m.id.user", user: this.adminConfig.username },
          password: this.adminConfig.password,
        }),
      });

      if (!loginRes.ok) {
        console.error(`[Matrix] Admin login failed: ${loginRes.status}`);
        return false;
      }

      const { access_token: adminToken } = (await loginRes.json()) as { access_token: string };

      const inviteRes = await fetch(
        `${this.adminConfig.homeserver}/_matrix/client/v3/rooms/${encodeURIComponent(roomId)}/invite`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${adminToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ user_id: this.userId }),
        }
      );

      if (!inviteRes.ok) {
        const err = await inviteRes.text();
        console.error(`[Matrix] Admin invite failed: ${inviteRes.status} - ${err}`);
        return false;
      }

      console.log(`[Matrix] Admin invited us to ${roomId}, joining...`);
      await this.client.joinRoom(roomId);
      this.joinedRooms.add(roomId);
      this.monitoredRooms.add(roomId);
      console.log(`[Matrix] Joined room via admin invite: ${roomId}`);
      return true;
    } catch (err) {
      console.error(`[Matrix] Admin invite flow failed:`, err);
      return false;
    }
  }

  async sendMessage(roomId: string, body: string, formattedBody?: string): Promise<boolean> {
    const inRoom = await this.ensureInRoom(roomId);
    if (!inRoom) {
      console.error(`[Matrix] Cannot send to ${roomId} - not in room`);
      return false;
    }

    const content: any = {
      msgtype: "m.text",
      body: body,
    };

    if (formattedBody) {
      content.format = "org.matrix.custom.html";
      content.formatted_body = formattedBody;
    }

    await this.client.sendMessage(roomId, content);
    return true;
  }

  async stop(): Promise<void> {
    await this.client.stopClient();
    console.log("[Matrix] Client stopped");
  }

  getUserId(): string {
    return this.userId;
  }

  getJoinedRooms(): string[] {
    return Array.from(this.monitoredRooms);
  }
}
