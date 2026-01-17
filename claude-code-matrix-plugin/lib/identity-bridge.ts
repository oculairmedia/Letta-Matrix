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
  
  // Derive identity ID and localpart from cwd
  const identityId = `claude_code_${Buffer.from(cwd)
    .toString("base64")
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")}`;
  
  const projectName = cwd.split("/").filter(Boolean).pop() || "project";
  const localpart = `cc_${projectName.toLowerCase().replace(/[^a-z0-9_]/g, "_")}`;
  const displayName = `Claude Code: ${projectName}`;
  const mxid = `@${localpart}:matrix.oculair.ca`;

  // Check if identity already exists
  try {
    const existingResponse = await fetch(
      `${apiBase}/api/v1/internal/identities/${encodeURIComponent(identityId)}`,
      {
        headers: {
          "X-Internal-Key": getInternalApiKey(),
        },
      }
    );

    if (existingResponse.ok) {
      const existing = await existingResponse.json();
      if (existing?.access_token && existing?.mxid) {
        return {
          identity_id: identityId,
          mxid: String(existing.mxid),
          access_token: String(existing.access_token),
        };
      }
    }
  } catch (error) {
    // Identity doesn't exist, will create below
  }

  // Create new identity via internal API
  const createResponse = await fetch(`${apiBase}/api/v1/internal/identities`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Key": getInternalApiKey(),
    },
    body: JSON.stringify({
      id: identityId,
      identity_type: "custom",
      mxid: mxid,
      display_name: displayName,
    }),
  });

  if (!createResponse.ok) {
    const errorText = await createResponse.text().catch(() => "");
    // If 409 conflict, identity exists - try to fetch it
    if (createResponse.status === 409) {
      const fetchResponse = await fetch(
        `${apiBase}/api/v1/internal/identities/${encodeURIComponent(identityId)}`,
        {
          headers: {
            "X-Internal-Key": getInternalApiKey(),
          },
        }
      );
      if (fetchResponse.ok) {
        const existing = await fetchResponse.json();
        if (existing?.access_token && existing?.mxid) {
          return {
            identity_id: identityId,
            mxid: String(existing.mxid),
            access_token: String(existing.access_token),
          };
        }
      }
    }
    throw new Error(`Failed to create identity: ${createResponse.status} ${errorText}`);
  }

  // Fetch full identity with access_token
  const fullResponse = await fetch(
    `${apiBase}/api/v1/internal/identities/${encodeURIComponent(identityId)}`,
    {
      headers: {
        "X-Internal-Key": getInternalApiKey(),
      },
    }
  );

  if (!fullResponse.ok) {
    throw new Error(`Failed to fetch identity details: ${fullResponse.status}`);
  }

  const full = await fullResponse.json();

  if (!full?.access_token || !full?.mxid) {
    throw new Error("Identity details missing access_token or mxid");
  }

  return {
    identity_id: identityId,
    mxid: String(full.mxid),
    access_token: String(full.access_token),
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
