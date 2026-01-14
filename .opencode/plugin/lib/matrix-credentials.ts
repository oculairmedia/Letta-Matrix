import { existsSync } from "fs";
import { mkdir, readFile, writeFile } from "fs/promises";
import path from "path";

export interface MatrixCredentials {
  userId: string;
  accessToken: string;
  homeserver: string;
  origin: "identity-bridge" | "local";
}

interface StoredCredentials extends MatrixCredentials {
  password?: string;
  createdAt: number;
  lastUsedAt: number;
}

interface IdentityBridgeRecord {
  mxid: string;
  accessToken: string;
  homeserver?: string;
}

interface ResolveCredentialsOptions {
  directory: string;
  worktree: string;
  homeserver: string;
  userIdOverride?: string;
}

const DEFAULT_PASSWORD_SECRET = "opencode_matrix_2024";

function deriveOpenCodeId(directory: string): string {
  const encoded = Buffer.from(directory)
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
  return `opencode_v2_${encoded}`;
}

function deriveOpenCodeLocalpart(directory: string): string {
  const projectName = path.basename(directory) || "unknown";
  return `oc_${projectName.toLowerCase().replace(/[^a-z0-9_]/g, "_")}_v2`;
}

function deriveDisplayName(directory: string): string {
  const projectName = path.basename(directory) || "Unknown";
  const formatted = projectName
    .split(/[-_]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
  return `OpenCode: ${formatted || "Project"}`;
}

function generateDeterministicPassword(localpart: string, secret: string): string {
  const input = `${localpart}:${secret}`;
  let hash = 0;
  for (let i = 0; i < input.length; i += 1) {
    const char = input.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash &= hash;
  }

  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let password = "OC_";
  const absHash = Math.abs(hash);
  for (let i = 0; i < 24; i += 1) {
    password += chars.charAt((absHash + i * 7) % chars.length);
  }
  return password;
}

async function verifyToken(homeserver: string, accessToken: string): Promise<boolean> {
  try {
    const response = await fetch(`${homeserver}/_matrix/client/v3/account/whoami`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function registerUser(
  homeserver: string,
  localpart: string,
  password: string,
  registrationToken: string
): Promise<{ userId: string; accessToken: string }> {
  const response = await fetch(`${homeserver}/_matrix/client/v3/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: localpart,
      password,
      auth: { type: "m.login.registration_token", token: registrationToken },
    }),
  });

  if (response.status === 200) {
    const result = await response.json() as { user_id: string; access_token: string };
    return { userId: result.user_id, accessToken: result.access_token };
  }

  if (response.status === 400) {
    const error = await response.json() as { errcode?: string; error?: string };
    if (error.errcode === "M_USER_IN_USE") {
      throw new Error("USER_EXISTS");
    }
    throw new Error(`Registration failed: ${error.errcode} - ${error.error}`);
  }

  const text = await response.text();
  throw new Error(`Registration failed: ${response.status} - ${text}`);
}

async function loginUser(
  homeserver: string,
  localpart: string,
  password: string
): Promise<{ userId: string; accessToken: string }> {
  const response = await fetch(`${homeserver}/_matrix/client/v3/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      type: "m.login.password",
      identifier: { type: "m.id.user", user: localpart },
      password,
    }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Login failed: ${response.status} - ${text}`);
  }

  const result = await response.json() as { user_id: string; access_token: string };
  return { userId: result.user_id, accessToken: result.access_token };
}

async function setDisplayName(
  homeserver: string,
  userId: string,
  displayName: string,
  accessToken: string
): Promise<void> {
  await fetch(`${homeserver}/_matrix/client/v3/profile/${encodeURIComponent(userId)}/displayname`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ displayname: displayName }),
  }).catch(() => undefined);
}

function parseUserIdLocalpart(userId: string): string {
  const match = userId.match(/^@([^:]+):/);
  if (!match) {
    throw new Error(`Invalid Matrix userId: ${userId}`);
  }
  return match[1];
}

async function loadIdentityBridgeCredentials(
  directory: string,
  worktree: string,
  userIdOverride?: string
): Promise<MatrixCredentials | null> {
  const identityDataDir =
    process.env.MATRIX_IDENTITY_BRIDGE_DATA_DIR ||
    process.env.MATRIX_IDENTITY_DATA_DIR ||
    path.join(worktree, "mcp-servers", "matrix-identity-bridge", "data");

  const identitiesPath = path.join(identityDataDir, "identities.json");
  if (!existsSync(identitiesPath)) {
    return null;
  }

  const data = await readFile(identitiesPath, "utf-8");
  const identities = JSON.parse(data) as Record<string, IdentityBridgeRecord>;
  const identityId = deriveOpenCodeId(directory);
  const record = identities[identityId];

  if (record?.accessToken && record?.mxid) {
    return {
      userId: record.mxid,
      accessToken: record.accessToken,
      homeserver: record.homeserver || "",
      origin: "identity-bridge",
    };
  }

  if (userIdOverride) {
    const fallback = Object.values(identities).find(
      (entry) => entry.mxid === userIdOverride
    );
    if (fallback?.accessToken && fallback?.mxid) {
      return {
        userId: fallback.mxid,
        accessToken: fallback.accessToken,
        homeserver: fallback.homeserver || "",
        origin: "identity-bridge",
      };
    }
  }

  return null;
}

async function loadLocalCredentials(
  credentialsPath: string
): Promise<StoredCredentials | null> {
  if (!existsSync(credentialsPath)) {
    return null;
  }
  const raw = await readFile(credentialsPath, "utf-8");
  return JSON.parse(raw) as StoredCredentials;
}

async function saveLocalCredentials(
  credentialsPath: string,
  credentials: StoredCredentials
): Promise<void> {
  await mkdir(path.dirname(credentialsPath), { recursive: true });
  await writeFile(credentialsPath, JSON.stringify(credentials, null, 2));
}

async function getOrCreateLocalCredentials(
  options: ResolveCredentialsOptions
): Promise<MatrixCredentials> {
  const credentialsPath = path.join(options.worktree, ".opencode", "matrix-credentials.json");
  const localpart = options.userIdOverride
    ? parseUserIdLocalpart(options.userIdOverride)
    : deriveOpenCodeLocalpart(options.directory);
  const passwordSecret = process.env.MATRIX_PASSWORD_SECRET || DEFAULT_PASSWORD_SECRET;
  const password = generateDeterministicPassword(localpart, passwordSecret);

  const saved = await loadLocalCredentials(credentialsPath);
  if (saved?.accessToken) {
    const valid = await verifyToken(options.homeserver, saved.accessToken);
    if (valid) {
      saved.lastUsedAt = Date.now();
      await saveLocalCredentials(credentialsPath, saved);
      return {
        userId: saved.userId,
        accessToken: saved.accessToken,
        homeserver: options.homeserver,
        origin: "local",
      };
    }
  }

  const registrationToken = process.env.MATRIX_REGISTRATION_TOKEN;
  if (!registrationToken) {
    throw new Error("MATRIX_REGISTRATION_TOKEN is required to provision credentials");
  }

  const displayName = deriveDisplayName(options.directory);
  let userId: string;
  let accessToken: string;

  try {
    const registered = await registerUser(options.homeserver, localpart, password, registrationToken);
    userId = registered.userId;
    accessToken = registered.accessToken;
    await setDisplayName(options.homeserver, userId, displayName, accessToken);
  } catch (error: any) {
    if (error?.message === "USER_EXISTS") {
      const loggedIn = await loginUser(options.homeserver, localpart, password);
      userId = loggedIn.userId;
      accessToken = loggedIn.accessToken;
    } else {
      throw error;
    }
  }

  const credentials: StoredCredentials = {
    userId,
    accessToken,
    homeserver: options.homeserver,
    createdAt: Date.now(),
    lastUsedAt: Date.now(),
    password,
    origin: "local",
  };

  await saveLocalCredentials(credentialsPath, credentials);

  return {
    userId: credentials.userId,
    accessToken: credentials.accessToken,
    homeserver: credentials.homeserver,
    origin: "local",
  };
}

export async function resolveMatrixCredentials(
  options: ResolveCredentialsOptions
): Promise<MatrixCredentials> {
  const identityBridge = await loadIdentityBridgeCredentials(
    options.directory,
    options.worktree,
    options.userIdOverride
  );

  if (identityBridge) {
    return {
      ...identityBridge,
      homeserver: identityBridge.homeserver || options.homeserver,
    };
  }

  return getOrCreateLocalCredentials(options);
}
