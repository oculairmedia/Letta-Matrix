import type { MatrixConfig } from "./types";

type IdentityResult = {
  identity_id: string;
  mxid: string;
  access_token: string;
};

function getApiBaseUrl(config: MatrixConfig): string {
  const fromConfig = (config as any)?.identityBridge?.url;
  if (typeof fromConfig === "string" && fromConfig.length > 0) {
    return fromConfig;
  }

  const envUrl = process.env.MATRIX_API_URL;
  if (envUrl && envUrl.length > 0) return envUrl;

  return "http://127.0.0.1:8004";
}

function getInternalApiKey(): string {
  return process.env.INTERNAL_API_KEY || "matrix-identity-internal-key";
}

export async function ensureClaudeCodeIdentity(config: MatrixConfig, cwd: string): Promise<IdentityResult | null> {
  const enabled = (config as any)?.identityBridge?.enabled;
  if (!enabled) return null;

  const apiBase = getApiBaseUrl(config);
  
  const response = await fetch(`${apiBase}/api/v1/internal/identities/provision`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Key": getInternalApiKey(),
    },
    body: JSON.stringify({
      directory: cwd,
      identity_type: "claudecode",
    }),
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => "");
    throw new Error(`Failed to provision identity: ${response.status} ${errorText}`);
  }

  const result = await response.json();

  if (!result?.success) {
    throw new Error(`Provision failed: ${result?.error || "Unknown error"}`);
  }

  if (!result?.access_token || !result?.mxid) {
    throw new Error("Provision response missing access_token or mxid");
  }

  return {
    identity_id: String(result.identity_id),
    mxid: String(result.mxid),
    access_token: String(result.access_token),
  };
}

export async function ensureJoinedRooms(identityId: string, rooms: string[], config: MatrixConfig): Promise<void> {
  const enabled = (config as any)?.identityBridge?.enabled;
  if (!enabled) return;

  const apiBase = getApiBaseUrl(config);

  for (const roomId of rooms) {
    try {
      const response = await fetch(`${apiBase}/api/v1/identities/${encodeURIComponent(identityId)}/join`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Internal-Key": getInternalApiKey(),
        },
        body: JSON.stringify({ room_id: roomId }),
      });

      if (response.ok || response.status === 404) continue;

      const errorText = await response.text().catch(() => "");
      if (errorText.includes("M_FORBIDDEN") || 
          errorText.includes("cannot join") ||
          errorText.includes("already joined")) {
        continue;
      }
    } catch (error) {
      console.error(`Error joining room ${roomId}:`, error);
    }
  }
}
