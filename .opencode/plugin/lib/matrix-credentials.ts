export interface MatrixCredentials {
  userId: string;
  accessToken: string;
  homeserver: string;
  origin: "identity-bridge" | "provision-api";
}

interface ResolveCredentialsOptions {
  directory: string;
  worktree: string;
  homeserver: string;
  userIdOverride?: string;
}

interface ProvisionResponse {
  success: boolean;
  identity_id: string;
  mxid: string;
  access_token: string;
  display_name: string;
  error?: string;
}

function getApiBaseUrl(): string {
  return process.env.MATRIX_API_URL || "http://127.0.0.1:8004";
}

function getInternalApiKey(): string {
  return process.env.INTERNAL_API_KEY || "matrix-identity-internal-key";
}

export async function resolveMatrixCredentials(
  options: ResolveCredentialsOptions
): Promise<MatrixCredentials> {
  const apiBase = getApiBaseUrl();

  const response = await fetch(`${apiBase}/api/v1/internal/identities/provision`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Key": getInternalApiKey(),
    },
    body: JSON.stringify({
      directory: options.directory,
      identity_type: "opencode",
    }),
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => "");
    throw new Error(`Failed to provision identity: ${response.status} ${errorText}`);
  }

  const result = await response.json() as ProvisionResponse;

  if (!result.success) {
    throw new Error(`Provision failed: ${result.error || "Unknown error"}`);
  }

  if (!result.access_token || !result.mxid) {
    throw new Error("Provision response missing access_token or mxid");
  }

  return {
    userId: result.mxid,
    accessToken: result.access_token,
    homeserver: options.homeserver,
    origin: "provision-api",
  };
}
