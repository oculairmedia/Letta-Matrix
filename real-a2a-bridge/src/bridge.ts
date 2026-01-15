import { P2PClient } from "./p2p-client";
import { MatrixClientWrapper } from "./matrix-client";
import { Router } from "./router";
import { getOrCreateCredentials } from "./matrix-auth";
import type {
  BridgeConfig,
  P2PMessage,
  MatrixMessage,
  ProcessedMessage,
} from "./types";

interface HealthStats {
  startedAt: number;
  messagesForwardedP2PToMatrix: number;
  messagesForwardedMatrixToP2P: number;
  dedupeDrops: number;
  lastHealthCheck: number;
  intervalMessagesP2PToMatrix: number;
  intervalMessagesMatrixToP2P: number;
  intervalDedupeDrops: number;
}

export class Bridge {
  private p2p: P2PClient;
  private matrix: MatrixClientWrapper | null = null;
  private router: Router;
  private config: BridgeConfig;
  private processedMessages: Map<string, ProcessedMessage>;
  private readonly MESSAGE_CACHE_TTL = 3600000;
  private healthStats: HealthStats;
  private healthCheckInterval: NodeJS.Timeout | null = null;
  private cleanupInterval: NodeJS.Timeout | null = null;

  constructor(config: BridgeConfig) {
    this.config = config;
    this.processedMessages = new Map();
    this.router = new Router("./config/routes.yaml");

    this.p2p = new P2PClient(
      config.p2p.identity,
      config.p2p.room,
      config.p2p.ticket
    );

    this.healthStats = {
      startedAt: Date.now(),
      messagesForwardedP2PToMatrix: 0,
      messagesForwardedMatrixToP2P: 0,
      dedupeDrops: 0,
      lastHealthCheck: Date.now(),
      intervalMessagesP2PToMatrix: 0,
      intervalMessagesMatrixToP2P: 0,
      intervalDedupeDrops: 0,
    };

    this.router.on("reload", () => {
      console.log("[Bridge] Routes reloaded, joining new rooms...");
      this.joinConfiguredRooms();
    });
  }

  private async joinConfiguredRooms(): Promise<void> {
    if (!this.matrix) return;
    const rooms = this.router.getAllRooms();
    await this.matrix.joinRooms(rooms);
  }

  private startMessageCleanup(): void {
    this.cleanupInterval = setInterval(() => {
      const now = Date.now();
      const expired: string[] = [];

      for (const [id, msg] of this.processedMessages.entries()) {
        if (now - msg.processed > this.MESSAGE_CACHE_TTL) {
          expired.push(id);
        }
      }

      for (const id of expired) {
        this.processedMessages.delete(id);
      }

      if (expired.length > 0) {
        console.log(`[Bridge] Cleaned ${expired.length} expired message IDs`);
      }
    }, 60000);
  }

  private startHealthCheck(): void {
    this.healthCheckInterval = setInterval(() => {
      const intervalMinutes = this.config.bridge.healthCheckIntervalMs / 60000;
      console.log(
        `[Bridge Health] Last ${intervalMinutes}min: ` +
          `P2P→Matrix: ${this.healthStats.intervalMessagesP2PToMatrix}, ` +
          `Matrix→P2P: ${this.healthStats.intervalMessagesMatrixToP2P}, ` +
          `Dedupe drops: ${this.healthStats.intervalDedupeDrops}`
      );
      console.log(
        `[Bridge Health] Total: ` +
          `P2P→Matrix: ${this.healthStats.messagesForwardedP2PToMatrix}, ` +
          `Matrix→P2P: ${this.healthStats.messagesForwardedMatrixToP2P}, ` +
          `Cache size: ${this.processedMessages.size}`
      );

      this.healthStats.intervalMessagesP2PToMatrix = 0;
      this.healthStats.intervalMessagesMatrixToP2P = 0;
      this.healthStats.intervalDedupeDrops = 0;
      this.healthStats.lastHealthCheck = Date.now();
    }, this.config.bridge.healthCheckIntervalMs);
  }

  private isDuplicate(messageId: string): boolean {
    return this.processedMessages.has(messageId);
  }

  private markProcessed(messageId: string, origin: "matrix" | "p2p"): void {
    this.processedMessages.set(messageId, {
      id: messageId,
      origin,
      timestamp: Date.now(),
      processed: Date.now(),
    });
  }

  private async handleP2PMessage(msg: P2PMessage): Promise<void> {
    if (msg.fromName === this.config.p2p.identity) {
      return;
    }

    if (this.isDuplicate(msg.messageId)) {
      console.log(`[Bridge] Skipping duplicate P2P message: ${msg.messageId}`);
      this.healthStats.dedupeDrops++;
      this.healthStats.intervalDedupeDrops++;
      return;
    }

    this.markProcessed(msg.messageId, "p2p");

    const route = this.router.routeP2PMessage(this.config.p2p.room, msg.content);

    const plainBody = `[P2P:${msg.fromName}] ${msg.content}`;
    const formattedBody = `<strong>[P2P:${msg.fromName}]</strong> ${this.escapeHtml(msg.content)}`;

    if (this.config.bridge.logMessages) {
      console.log(`[Bridge P2P→Matrix] ${msg.fromName}: ${msg.content.substring(0, 80)}...`);
      if (route.primaryRoom) {
        console.log(`[Bridge] → Primary room: ${route.primaryRoom}`);
      }
      if (route.mentionRooms.length > 0) {
        console.log(`[Bridge] → Mention rooms: ${route.mentionRooms.join(", ")} (${route.mentionedAgents.join(", ")})`);
      }
    }

    const targetRooms = new Set<string>();
    if (route.primaryRoom) {
      targetRooms.add(route.primaryRoom);
    }
    for (const room of route.mentionRooms) {
      targetRooms.add(room);
    }

    if (targetRooms.size === 0) {
      console.warn("[Bridge] No target rooms for P2P message, dropping");
      return;
    }

    for (const roomId of targetRooms) {
      try {
        await this.matrix?.sendMessage(roomId, plainBody, formattedBody);
        this.healthStats.messagesForwardedP2PToMatrix++;
        this.healthStats.intervalMessagesP2PToMatrix++;
      } catch (err) {
        console.error(`[Bridge] Failed to send to ${roomId}:`, err);
      }
    }
  }

  private async handleMatrixMessage(msg: MatrixMessage): Promise<void> {
    if (this.isDuplicate(msg.messageId)) {
      console.log(`[Bridge] Skipping duplicate Matrix message: ${msg.messageId}`);
      this.healthStats.dedupeDrops++;
      this.healthStats.intervalDedupeDrops++;
      return;
    }

    this.markProcessed(msg.messageId, "matrix");

    const topic = this.router.routeMatrixMessage(msg.roomId);
    if (!topic) {
      return;
    }

    const formattedMsg = `[Matrix:${msg.displayName}] ${msg.body}`;

    if (this.config.bridge.logMessages) {
      console.log(`[Bridge Matrix→P2P] ${msg.displayName}: ${msg.body.substring(0, 80)}...`);
      console.log(`[Bridge] From room ${msg.roomId} → topic ${topic}`);
    }

    try {
      await this.p2p.sendMessage(formattedMsg);
      this.healthStats.messagesForwardedMatrixToP2P++;
      this.healthStats.intervalMessagesMatrixToP2P++;
    } catch (err) {
      console.error("[Bridge] Failed to forward Matrix→P2P:", err);
    }

    if (this.config.bridge.opencodeBridgeUrl && this.config.bridge.opencodeDirectory) {
      await this.notifyOpenCode(msg.displayName, msg.body);
    }
  }

  private async notifyOpenCode(agentName: string, message: string): Promise<void> {
    const bridgeUrl = this.config.bridge.opencodeBridgeUrl;
    const directory = this.config.bridge.opencodeDirectory;

    if (!bridgeUrl || !directory) return;

    try {
      const res = await fetch(`${bridgeUrl}/notify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          directory,
          message,
          sender: "p2p-bridge",
          agentName,
        }),
      });

      if (res.ok) {
        const data = (await res.json()) as { forwarded_to?: string };
        if (this.config.bridge.logMessages) {
          console.log(`[Bridge] → OpenCode notify: ${data.forwarded_to || "sent"}`);
        }
      } else {
        const errText = await res.text();
        console.warn(`[Bridge] OpenCode notify failed: ${res.status} - ${errText}`);
      }
    } catch (err) {
      console.warn(`[Bridge] OpenCode notify error:`, err);
    }
  }

  private escapeHtml(text: string): string {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  async start(): Promise<void> {
    console.log("[Bridge] Starting...");

    console.log("[Bridge] Provisioning Matrix credentials...");
    const credentials = await getOrCreateCredentials({
      homeserver: this.config.matrix.homeserver,
      serverName: this.config.matrix.serverName,
      registrationToken: this.config.matrix.registrationToken,
      localpart: this.config.matrix.localpart,
      displayName: this.config.matrix.displayName,
      credentialsPath: "./config/credentials.json",
      passwordSecret: "real_a2a_bridge_2024",
    });
    console.log(`[Bridge] Matrix user: ${credentials.userId}`);

    this.matrix = new MatrixClientWrapper(
      this.config.matrix.homeserver,
      credentials.accessToken,
      credentials.userId
    );

    if (this.config.matrix.adminUsername && this.config.matrix.adminPassword) {
      this.matrix.setAdminConfig({
        homeserver: this.config.matrix.homeserver,
        username: this.config.matrix.adminUsername,
        password: this.config.matrix.adminPassword,
      });
      console.log("[Bridge] Admin-assisted room joining enabled");
    }

    await this.p2p.start();
    console.log("[Bridge] P2P client started");

    this.p2p.on("ticket", (ticket: string) => {
      console.log("[Bridge] ========================================");
      console.log("[Bridge] P2P TICKET (share to join network):");
      console.log(ticket);
      console.log("[Bridge] ========================================");
    });

    this.p2p.on("message", (msg: P2PMessage) => {
      this.handleP2PMessage(msg);
    });

    this.p2p.on("peer-connected", (peerId: string) => {
      console.log(`[Bridge] Peer connected: ${peerId}`);
    });

    this.p2p.on("peer-disconnected", (peerId: string) => {
      console.log(`[Bridge] Peer disconnected: ${peerId}`);
    });

    await this.matrix.start();
    console.log("[Bridge] Matrix client started");

    await this.joinConfiguredRooms();

    this.matrix.on("message", (msg: MatrixMessage) => {
      this.handleMatrixMessage(msg);
    });

    this.startMessageCleanup();
    this.startHealthCheck();

    console.log("[Bridge] ✅ Bridge is running!");
    console.log(`[Bridge] Matrix user: ${credentials.userId}`);
    console.log(`[Bridge] P2P identity: ${this.config.p2p.identity}`);
    console.log(`[Bridge] P2P room/topic: ${this.config.p2p.room}`);
    console.log(`[Bridge] Routing: ${this.router.getAllTopics().length} topics, ${this.router.getAllAgents().length} agents`);
  }

  async stop(): Promise<void> {
    console.log("[Bridge] Stopping...");
    if (this.healthCheckInterval) clearInterval(this.healthCheckInterval);
    if (this.cleanupInterval) clearInterval(this.cleanupInterval);
    await Promise.all([this.p2p.stop(), this.matrix?.stop()]);
    console.log("[Bridge] Stopped");
  }

  getStats() {
    return {
      ...this.healthStats,
      cacheSize: this.processedMessages.size,
    };
  }
}
