let cachedAdminToken: string | null = null;
let tokenExpiry: number = 0;

const TOKEN_TTL_MS = 5 * 60 * 1000;

export interface AdminAuthConfig {
  homeserverUrl: string;
  username: string;
  password: string;
}

export function getAdminConfig(): AdminAuthConfig {
  const homeserverUrl = process.env.MATRIX_HOMESERVER_URL || 'http://localhost:6167';
  const username = process.env.MATRIX_ADMIN_USERNAME || '@admin:matrix.oculair.ca';
  const password = process.env.MATRIX_ADMIN_PASSWORD || '';
  
  return { homeserverUrl, username, password };
}

export async function getAdminToken(): Promise<string> {
  const config = getAdminConfig();
  
  if (!config.password) {
    throw new Error('MATRIX_ADMIN_PASSWORD not configured');
  }
  
  if (cachedAdminToken && Date.now() < tokenExpiry) {
    return cachedAdminToken;
  }
  
  const localpart = config.username.replace(/@([^:]+):.*/, '$1');
  
  const loginResponse = await fetch(`${config.homeserverUrl}/_matrix/client/v3/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'm.login.password',
      identifier: { type: 'm.id.user', user: localpart },
      password: config.password
    })
  });
  
  if (!loginResponse.ok) {
    const err = await loginResponse.text();
    throw new Error(`Admin login failed: ${err}`);
  }
  
  const { access_token } = await loginResponse.json() as { access_token: string };
  
  cachedAdminToken = access_token;
  tokenExpiry = Date.now() + TOKEN_TTL_MS;
  
  return access_token;
}

export function clearAdminTokenCache(): void {
  cachedAdminToken = null;
  tokenExpiry = 0;
}
