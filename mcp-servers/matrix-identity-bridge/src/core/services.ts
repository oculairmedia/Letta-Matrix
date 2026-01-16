import "dotenv/config";
import { Storage } from "./storage.js";
import { IdentityManager } from "./identity-manager.js";
import { MatrixClientPool } from "./client-pool.js";
import { RoomManager } from "./room-manager.js";
import { SubscriptionManager } from "./subscription-manager.js";
import { LettaService } from "../letta/letta-service.js";
import { OpenCodeService } from "../opencode/opencode-service.js";
import { setToolContext } from "./tool-context.js";
import { getConversationTracker } from "./conversation-tracker.js";
import { initializeResponseMonitor } from "./response-monitor.js";
import { createWebhookServer } from "./webhook-server.js";
import { initializeLettaWebhookHandler } from "./letta-webhook-handler.js";

export interface Services {
  storage: Storage;
  identityManager: IdentityManager;
  clientPool: MatrixClientPool;
  roomManager: RoomManager;
  subscriptionManager: SubscriptionManager;
  lettaService: LettaService | null;
  openCodeService: OpenCodeService;
  conversationTracker: ReturnType<typeof getConversationTracker>;
  webhookServer: Awaited<ReturnType<typeof createWebhookServer>> | null;
  currentAgentId?: string;
}

let _services: Services | null = null;
let _initPromise: Promise<void> | null = null;

const config = {
  homeserverUrl:
    process.env.MATRIX_HOMESERVER_URL || "https://matrix.oculair.ca",
  adminToken: process.env.MATRIX_ADMIN_TOKEN || "",
  dataDir: process.env.DATA_DIR || "./data",
  webhookPort: parseInt(process.env.WEBHOOK_PORT || "3101", 10),
  lettaApiUrl: process.env.LETTA_API_URL,
  lettaApiKey: process.env.LETTA_API_KEY,
};

export async function initializeServices(): Promise<void> {
  if (_services) return;

  if (_initPromise) {
    await _initPromise;
    return;
  }

  _initPromise = (async () => {
    console.log("[Services] Initializing Matrix Messaging services...");
    console.log(`[Services] Homeserver: ${config.homeserverUrl}`);

    const storage = new Storage(config.dataDir);
    await storage.initialize();

    const identityManager = new IdentityManager(
      storage,
      config.homeserverUrl,
      config.adminToken
    );

    const clientPool = new MatrixClientPool(
      storage,
      config.homeserverUrl,
      `${config.dataDir}/clients`
    );
    await clientPool.initialize();

    const roomManager = new RoomManager(storage, clientPool);
    const subscriptionManager = new SubscriptionManager(storage, clientPool);

    let lettaService: LettaService | null = null;
    if (config.lettaApiUrl) {
      lettaService = new LettaService(
        { baseUrl: config.lettaApiUrl, apiKey: config.lettaApiKey },
        storage,
        identityManager
      );
      console.log("[Services] Letta integration enabled");
    }

    const openCodeService = new OpenCodeService(storage, identityManager, {});

    const conversationTracker = getConversationTracker({
      maxAgeSeconds: parseInt(
        process.env.CONVERSATION_TIMEOUT_SECONDS || "300",
        10
      ),
      cleanupIntervalSeconds: 60,
    });
    console.log("[Services] Conversation tracker initialized");

    if (lettaService) {
      initializeResponseMonitor(
        lettaService.getClient(),
        clientPool,
        storage,
        conversationTracker,
        {
          maxWaitSeconds: parseInt(process.env.MAX_RESPONSE_WAIT || "60", 10),
          pollIntervalSeconds: parseInt(
            process.env.RESPONSE_POLL_INTERVAL || "2",
            10
          ),
        }
      );
      console.log("[Services] Response monitor initialized");
    }

    let webhookServer: Awaited<ReturnType<typeof createWebhookServer>> | null =
      null;
    webhookServer = createWebhookServer({
      port: config.webhookPort,
      host: "0.0.0.0",
    });

    initializeLettaWebhookHandler(clientPool, storage, {
      matrixApiUrl: process.env.MATRIX_API_URL,
      webhookSecret: process.env.LETTA_WEBHOOK_SECRET,
      skipVerification: process.env.LETTA_WEBHOOK_SKIP_VERIFICATION === "true",
      auditNonMatrixConversations: process.env.AUDIT_NON_MATRIX !== "false",
    });
    console.log("[Services] Letta webhook handler initialized");

    await webhookServer.start();
    console.log("[Services] Webhook server started on port", config.webhookPort);

    _services = {
      storage,
      identityManager,
      clientPool,
      roomManager,
      subscriptionManager,
      lettaService,
      openCodeService,
      conversationTracker,
      webhookServer,
    };

    setToolContext({
      storage,
      identityManager,
      clientPool,
      roomManager,
      subscriptionManager,
      lettaService,
      openCodeService,
    });

    console.log("[Services] All services initialized");
  })();

  await _initPromise;
}

export function getServices(): Services | null {
  return _services;
}

export async function shutdownServices(): Promise<void> {
  if (!_services) return;

  console.log("[Services] Shutting down...");
  _services.conversationTracker.stop();
  if (_services.webhookServer) {
    await _services.webhookServer.stop();
  }
  await _services.clientPool.stopAll();
  _services = null;
  _initPromise = null;
}
