import { Bridge } from "./src/bridge";
import type { BridgeConfig } from "./src/types";
import { readFileSync, existsSync } from "fs";
import { resolve } from "path";

const DEFAULT_CONFIG_PATH = "./config/bridge-config.json";

function loadConfigFromFile(configPath: string): Partial<BridgeConfig> {
  const fullPath = resolve(configPath);
  if (!existsSync(fullPath)) {
    return {};
  }
  const content = readFileSync(fullPath, "utf-8");
  return JSON.parse(content);
}

function loadConfigFromEnv(): BridgeConfig {
  return {
    matrix: {
      homeserver: process.env.MATRIX_HOMESERVER_URL || "https://matrix.oculair.ca",
      serverName: process.env.MATRIX_SERVER_NAME || "matrix.oculair.ca",
      registrationToken: process.env.MATRIX_REGISTRATION_TOKEN || "matrix_mcp_secret_token_2024",
      roomId: "",
      localpart: process.env.P2P_BRIDGE_LOCALPART || "p2p_bridge",
      displayName: process.env.P2P_BRIDGE_DISPLAY_NAME || "P2P Bridge",
      adminUsername: process.env.MATRIX_ADMIN_USERNAME,
      adminPassword: process.env.MATRIX_ADMIN_PASSWORD,
    },
    p2p: {
      room: process.env.P2P_ROOM || "agent-swarm-global",
      identity: process.env.P2P_IDENTITY || "matrix-bridge",
      ticket: process.env.P2P_TICKET || undefined,
    },
    bridge: {
      logMessages: process.env.LOG_MESSAGES !== "false",
      healthCheckIntervalMs: parseInt(process.env.HEALTH_CHECK_INTERVAL_MS || "300000"),
      opencodeBridgeUrl: process.env.OPENCODE_BRIDGE_URL,
      opencodeDirectory: process.env.OPENCODE_DIRECTORY,
    },
  };
}

function mergeConfigs(envConfig: BridgeConfig, fileConfig: Partial<BridgeConfig>): BridgeConfig {
  return {
    matrix: { ...envConfig.matrix, ...fileConfig.matrix },
    p2p: { ...envConfig.p2p, ...fileConfig.p2p },
    bridge: { ...envConfig.bridge, ...fileConfig.bridge },
  };
}

async function main() {
  console.log("[Main] Starting real-a2a Matrix Bridge...");

  const envConfig = loadConfigFromEnv();
  const configPath = process.argv[2] || DEFAULT_CONFIG_PATH;
  const fileConfig = loadConfigFromFile(configPath);
  const config = mergeConfigs(envConfig, fileConfig);

  console.log("[Main] Config loaded:");
  console.log(`  Matrix homeserver: ${config.matrix.homeserver}`);
  console.log(`  P2P topic: ${config.p2p.room}`);
  console.log(`  P2P identity: ${config.p2p.identity}`);
  console.log(`  Routes config: ./config/routes.yaml`);
  console.log(`  Admin invite: ${config.matrix.adminUsername ? "enabled" : "disabled"}`);
  console.log(`  OpenCode push: ${config.bridge.opencodeBridgeUrl ? `${config.bridge.opencodeBridgeUrl} → ${config.bridge.opencodeDirectory}` : "disabled"}`);

  const bridge = new Bridge(config);

  process.on("SIGINT", async () => {
    console.log("\n[Main] Received SIGINT, shutting down...");
    await bridge.stop();
    process.exit(0);
  });

  process.on("SIGTERM", async () => {
    console.log("\n[Main] Received SIGTERM, shutting down...");
    await bridge.stop();
    process.exit(0);
  });

  try {
    await bridge.start();
  } catch (err) {
    console.error("[Main] Fatal error:", err);
    process.exit(1);
  }
}

main();
