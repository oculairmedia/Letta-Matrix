import { readFile } from "node:fs/promises";
import path from "node:path";
import { homedir } from "node:os";
import yaml from "yaml";
import type { MatrixConfig, MatrixFilters } from "./types";

const CONFIG_PATH = ".claude/matrix-context.yaml";
const GLOBAL_CONFIG_PATH = path.join(homedir(), ".config/claude-code-matrix/config.yaml");
const LEGACY_GLOBAL_CONFIG_PATH = path.join(homedir(), ".claude/matrix-context.yaml");

function expandEnv(value: string): string {
  return value.replace(/\$\{([A-Z0-9_]+)\}/g, (_match, name: string) => {
    return process.env[name] ?? "";
  });
}

function normalizeFilters(filters: unknown): MatrixFilters {
  if (!filters || typeof filters !== "object") {
    return { msgtype: "m.text", senders: [] };
  }

  const record = filters as Record<string, unknown>;
  const msgtype = typeof record.msgtype === "string" ? record.msgtype : "m.text";
  const senders = Array.isArray(record.senders)
    ? record.senders.filter((sender) => typeof sender === "string")
    : [];

  return { msgtype, senders };
}

export async function loadConfig(cwd: string): Promise<MatrixConfig | null> {
  const localConfigPath = path.join(cwd, CONFIG_PATH);
  
  // Try local config first, then fallback to global
  let configPath = localConfigPath;
  let isGlobal = false;
  
  try {
    await readFile(localConfigPath, "utf-8");
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      try {
        await readFile(GLOBAL_CONFIG_PATH, "utf-8");
        configPath = GLOBAL_CONFIG_PATH;
        isGlobal = true;
      } catch {
        try {
          await readFile(LEGACY_GLOBAL_CONFIG_PATH, "utf-8");
          configPath = LEGACY_GLOBAL_CONFIG_PATH;
          isGlobal = true;
        } catch {
          return null;
        }
      }
    } else {
      console.error("Failed to read local Matrix config", error);
      return null;
    }
  }

  try {
    const raw = await readFile(configPath, "utf-8");
    const data = yaml.parse(raw) as Record<string, unknown> | null;

    if (!data || typeof data !== "object") {
      console.error("Matrix config is not a valid YAML object");
      return null;
    }

    const homeserver = typeof data.homeserver === "string" ? data.homeserver : "";
    const accessTokenRaw = typeof data.accessToken === "string" ? data.accessToken : "";
    const userId = typeof data.userId === "string" ? data.userId : "";
    const roomsRaw = Array.isArray(data.rooms) ? data.rooms : [];

    const accessToken = expandEnv(accessTokenRaw);
    const rooms = roomsRaw.filter((room) => typeof room === "string");
    const filters = normalizeFilters(data.filters);

    const identityBridgeRaw = (data as any).identityBridge;
    const identityBridge =
      identityBridgeRaw && typeof identityBridgeRaw === "object" ? identityBridgeRaw : undefined;

    const identityBridgeEnabled = identityBridge?.enabled === true;

    if (!homeserver || rooms.length === 0) {
      console.error("Matrix config missing required fields");
      return null;
    }

    if (!identityBridgeEnabled && (!accessToken || !userId)) {
      console.error("Matrix config missing required fields");
      return null;
    }

    return {
      homeserver,
      accessToken,
      userId,
      rooms,
      filters,
      identityBridge,
      isGlobal, // Add flag to indicate if using global config
    } as any;
  } catch (error: unknown) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return null;
    }

    console.error("Failed to load Matrix config", error);
    return null;
  }
}

export function getConfigPath(cwd: string): string {
  return path.join(cwd, CONFIG_PATH);
}
