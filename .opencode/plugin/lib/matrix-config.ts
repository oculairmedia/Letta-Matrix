import { readFile } from "fs/promises";
import path from "path";
import { parse } from "yaml";

export interface MatrixConfig {
  homeserver: string;
  defaultRoomId: string;
  projectRooms: Record<string, string>;
  userId?: string;
  noReply: boolean;
  filters: {
    msgtypes: string[];
  };
  subscribeRooms: string[];
  roomLabels: Record<string, string>;
  configPath: string;
}

const DEFAULT_MSGTYPES = ["m.text"];
const SECRET_KEYS = ["accessToken", "token", "password", "registrationToken", "secret"];

function findSecretKeys(value: unknown, pathPrefix: string[] = []): string[] {
  if (!value || typeof value !== "object") return [];

  if (Array.isArray(value)) {
    return value.flatMap((item, index) => findSecretKeys(item, [...pathPrefix, String(index)]));
  }

  const entries = Object.entries(value as Record<string, unknown>);
  const matches: string[] = [];

  for (const [key, nested] of entries) {
    const keyLower = key.toLowerCase();
    if (SECRET_KEYS.some((secret) => keyLower.includes(secret.toLowerCase()))) {
      matches.push([...pathPrefix, key].join("."));
    }
    matches.push(...findSecretKeys(nested, [...pathPrefix, key]));
  }

  return matches;
}

function ensureString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`Matrix config ${label} must be a non-empty string`);
  }
  return value.trim();
}

function normalizeProjectRooms(value: unknown): Record<string, string> {
  if (!value) return {};
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Matrix config projectRooms must be a map of name -> roomId");
  }

  const entries = Object.entries(value as Record<string, unknown>);
  const rooms: Record<string, string> = {};
  for (const [key, roomId] of entries) {
    rooms[key] = ensureString(roomId, `projectRooms.${key}`);
  }
  return rooms;
}

function normalizeFilters(value: unknown): { msgtypes: string[] } {
  if (!value || typeof value !== "object") {
    return { msgtypes: [...DEFAULT_MSGTYPES] };
  }

  const filters = value as { msgtypes?: unknown };
  if (!filters.msgtypes) {
    return { msgtypes: [...DEFAULT_MSGTYPES] };
  }

  if (!Array.isArray(filters.msgtypes)) {
    throw new Error("Matrix config filters.msgtypes must be an array");
  }

  const msgtypes = filters.msgtypes.map((msgtype) => ensureString(msgtype, "filters.msgtypes"));
  return { msgtypes };
}

async function findConfigPath(worktree: string): Promise<string> {
  const overridePath = process.env.OPENCODE_MATRIX_CONFIG;
  if (overridePath) return overridePath;

  const localPath = path.join(worktree, ".opencode", "matrix.yaml");
  try {
    await readFile(localPath, "utf-8");
    return localPath;
  } catch {}

  const homeDir = process.env.HOME || process.env.USERPROFILE || "/root";
  const globalPath = path.join(homeDir, ".config", "opencode", "matrix.yaml");
  try {
    await readFile(globalPath, "utf-8");
    return globalPath;
  } catch {}

  return localPath;
}

export async function loadMatrixConfig(worktree: string): Promise<MatrixConfig> {
  const configPath = await findConfigPath(worktree);

  const raw = await readFile(configPath, "utf-8");
  const parsed = parse(raw) as Record<string, unknown>;
  if (!parsed || typeof parsed !== "object") {
    throw new Error("Matrix config must be a YAML object");
  }

  const secretKeys = findSecretKeys(parsed);
  if (secretKeys.length > 0) {
    throw new Error(`Matrix config must not include secrets: ${secretKeys.join(", ")}`);
  }

  const homeserver = ensureString(parsed.homeserver, "homeserver");
  const defaultRoomId = ensureString(parsed.defaultRoomId, "defaultRoomId");
  const userId = typeof parsed.userId === "string" ? parsed.userId.trim() : undefined;
  const noReply = typeof parsed.noReply === "boolean" ? parsed.noReply : false;
  const projectRooms = normalizeProjectRooms(parsed.projectRooms);
  const filters = normalizeFilters(parsed.filters);

  const extraRooms: string[] = [];
  if (Array.isArray(parsed.extraRooms)) {
    for (const room of parsed.extraRooms) {
      extraRooms.push(ensureString(room, "extraRooms[]"));
    }
  }

  const subscribeRooms = Array.from(
    new Set([defaultRoomId, ...Object.values(projectRooms), ...extraRooms])
  );

  const roomLabels: Record<string, string> = {
    [defaultRoomId]: "default",
  };

  for (const [label, roomId] of Object.entries(projectRooms)) {
    roomLabels[roomId] = label;
  }

  return {
    homeserver,
    defaultRoomId,
    projectRooms,
    userId,
    noReply,
    filters,
    subscribeRooms,
    roomLabels,
    configPath,
  };
}
