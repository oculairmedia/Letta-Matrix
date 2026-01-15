/**
 * Matrix Authentication - Auto-provision user via registration token
 * 
 * Uses the same pattern as matrix-identity-bridge to automatically
 * create and authenticate a Matrix user for the bridge.
 */

import { existsSync, readFileSync, writeFileSync, mkdirSync } from "fs";
import { join, dirname } from "path";

export interface MatrixCredentials {
  userId: string;
  accessToken: string;
  password: string;
  homeserver: string;
  createdAt: number;
  lastUsedAt: number;
}

export interface MatrixAuthConfig {
  homeserver: string;
  serverName: string;
  registrationToken: string;
  localpart: string;
  displayName: string;
  credentialsPath: string;
  passwordSecret: string;
}

/**
 * Generate deterministic password from localpart and secret
 * This allows recovery if we lose credentials but user still exists
 */
function generateDeterministicPassword(localpart: string, secret: string): string {
  const input = `${localpart}:${secret}`;
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    const char = input.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash;
  }
  
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let password = 'P2P_'; // Prefix for identification
  const absHash = Math.abs(hash);
  for (let i = 0; i < 24; i++) {
    password += chars.charAt((absHash + i * 7) % chars.length);
  }
  return password;
}

/**
 * Load saved credentials from disk
 */
function loadCredentials(path: string): MatrixCredentials | null {
  try {
    if (existsSync(path)) {
      const data = readFileSync(path, 'utf-8');
      return JSON.parse(data);
    }
  } catch (error) {
    console.warn('[MatrixAuth] Failed to load credentials:', error);
  }
  return null;
}

/**
 * Save credentials to disk
 */
function saveCredentials(path: string, credentials: MatrixCredentials): void {
  try {
    const dir = dirname(path);
    if (!existsSync(dir)) {
      mkdirSync(dir, { recursive: true });
    }
    writeFileSync(path, JSON.stringify(credentials, null, 2));
    console.log('[MatrixAuth] Credentials saved to:', path);
  } catch (error) {
    console.error('[MatrixAuth] Failed to save credentials:', error);
  }
}

/**
 * Register new Matrix user via registration token
 */
async function registerUser(
  homeserver: string,
  localpart: string,
  password: string,
  registrationToken: string
): Promise<{ userId: string; accessToken: string }> {
  const url = `${homeserver}/_matrix/client/v3/register`;
  
  const body = {
    username: localpart,
    password,
    auth: {
      type: 'm.login.registration_token',
      token: registrationToken
    }
  };

  console.log('[MatrixAuth] Registering user:', localpart);
  
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (response.status === 200) {
    const result = await response.json() as { user_id: string; access_token: string };
    console.log('[MatrixAuth] Registration successful:', result.user_id);
    return {
      userId: result.user_id,
      accessToken: result.access_token
    };
  }
  
  if (response.status === 400) {
    const error = await response.json() as { errcode?: string; error?: string };
    if (error.errcode === 'M_USER_IN_USE') {
      console.log('[MatrixAuth] User already exists, will try login');
      throw new Error('USER_EXISTS');
    }
    throw new Error(`Registration failed: ${error.errcode} - ${error.error}`);
  }

  const errorText = await response.text();
  throw new Error(`Registration failed: ${response.status} - ${errorText}`);
}

/**
 * Login existing Matrix user
 */
async function loginUser(
  homeserver: string,
  localpart: string,
  password: string
): Promise<{ userId: string; accessToken: string }> {
  const url = `${homeserver}/_matrix/client/v3/login`;
  
  const body = {
    type: 'm.login.password',
    identifier: {
      type: 'm.id.user',
      user: localpart
    },
    password
  };

  console.log('[MatrixAuth] Logging in user:', localpart);
  
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Login failed: ${response.status} - ${errorText}`);
  }

  const result = await response.json() as { user_id: string; access_token: string };
  console.log('[MatrixAuth] Login successful:', result.user_id);
  return {
    userId: result.user_id,
    accessToken: result.access_token
  };
}

/**
 * Set display name for user
 */
async function setDisplayName(
  homeserver: string,
  userId: string,
  displayName: string,
  accessToken: string
): Promise<void> {
  const url = `${homeserver}/_matrix/client/v3/profile/${encodeURIComponent(userId)}/displayname`;
  
  try {
    const response = await fetch(url, {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${accessToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ displayname: displayName })
    });

    if (response.ok) {
      console.log('[MatrixAuth] Display name set:', displayName);
    } else {
      console.warn('[MatrixAuth] Failed to set display name:', response.status);
    }
  } catch (error) {
    console.warn('[MatrixAuth] Error setting display name:', error);
  }
}

/**
 * Verify access token is still valid
 */
async function verifyToken(homeserver: string, accessToken: string): Promise<boolean> {
  try {
    const url = `${homeserver}/_matrix/client/v3/account/whoami`;
    const response = await fetch(url, {
      headers: { 'Authorization': `Bearer ${accessToken}` }
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Get or create Matrix credentials for the bridge
 * 
 * Flow:
 * 1. Check for saved credentials
 * 2. If found and valid, return them
 * 3. If not found, register new user
 * 4. If registration fails (user exists), login with deterministic password
 * 5. Save credentials for future use
 */
export async function getOrCreateCredentials(config: MatrixAuthConfig): Promise<MatrixCredentials> {
  const {
    homeserver,
    serverName,
    registrationToken,
    localpart,
    displayName,
    credentialsPath,
    passwordSecret
  } = config;

  const userId = `@${localpart}:${serverName}`;
  const password = generateDeterministicPassword(localpart, passwordSecret);

  // Try to load existing credentials
  const saved = loadCredentials(credentialsPath);
  if (saved) {
    console.log('[MatrixAuth] Found saved credentials for:', saved.userId);
    
    // Verify token is still valid
    const valid = await verifyToken(homeserver, saved.accessToken);
    if (valid) {
      console.log('[MatrixAuth] Saved credentials are valid');
      saved.lastUsedAt = Date.now();
      saveCredentials(credentialsPath, saved);
      return saved;
    }
    
    console.log('[MatrixAuth] Saved credentials expired, will re-authenticate');
  }

  // Try to register or login
  let accessToken: string;
  let resultUserId: string;

  try {
    // Try registration first
    const result = await registerUser(homeserver, localpart, password, registrationToken);
    accessToken = result.accessToken;
    resultUserId = result.userId;
    
    // Set display name for new user
    await setDisplayName(homeserver, resultUserId, displayName, accessToken);
  } catch (error: any) {
    if (error.message === 'USER_EXISTS') {
      // User exists, try login
      const result = await loginUser(homeserver, localpart, password);
      accessToken = result.accessToken;
      resultUserId = result.userId;
    } else {
      throw error;
    }
  }

  // Save credentials
  const credentials: MatrixCredentials = {
    userId: resultUserId,
    accessToken,
    password,
    homeserver,
    createdAt: Date.now(),
    lastUsedAt: Date.now()
  };
  
  saveCredentials(credentialsPath, credentials);
  return credentials;
}

/**
 * Create default config from environment variables
 */
export function createAuthConfigFromEnv(): MatrixAuthConfig {
  const homeserver = process.env.MATRIX_HOMESERVER_URL || 'https://matrix.oculair.ca';
  const serverName = process.env.MATRIX_SERVER_NAME || 'matrix.oculair.ca';
  const registrationToken = process.env.MATRIX_REGISTRATION_TOKEN || 'matrix_mcp_secret_token_2024';
  const passwordSecret = process.env.MATRIX_PASSWORD_SECRET || 'real_a2a_bridge_2024';
  
  return {
    homeserver,
    serverName,
    registrationToken,
    localpart: 'p2p_bridge',
    displayName: 'P2P Bridge',
    credentialsPath: './config/credentials.json',
    passwordSecret
  };
}
