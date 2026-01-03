import type { MatrixIdentity } from '../types/index.js';

interface FullIdentityResponse {
  id: string;
  identity_type: string;
  mxid: string;
  display_name: string | null;
  avatar_url: string | null;
  access_token: string;
  password_hash: string | null;
  device_id: string | null;
  created_at: number | null;
  updated_at: number | null;
  last_used_at: number | null;
  is_active: boolean;
}

interface IdentityListResponse {
  success: boolean;
  count: number;
  identities: FullIdentityResponse[];
}

interface CreateIdentityRequest {
  id: string;
  identity_type: string;
  mxid: string;
  access_token: string;
  display_name?: string;
  avatar_url?: string;
  password_hash?: string;
  device_id?: string;
}

function apiResponseToIdentity(resp: FullIdentityResponse): MatrixIdentity {
  return {
    id: resp.id,
    mxid: resp.mxid,
    displayName: resp.display_name || resp.id,
    avatarUrl: resp.avatar_url || undefined,
    accessToken: resp.access_token,
    password: resp.password_hash || undefined,
    type: resp.identity_type as 'letta' | 'opencode' | 'custom',
    createdAt: resp.created_at || Date.now(),
    lastUsedAt: resp.last_used_at || Date.now()
  };
}

export class IdentityApiClient {
  private baseUrl: string;
  private internalKey: string;

  constructor(
    baseUrl: string = process.env.MATRIX_API_URL || 'http://matrix-api:8000',
    internalKey: string = process.env.INTERNAL_API_KEY || 'matrix-identity-internal-key'
  ) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.internalKey = internalKey;
  }

  private async fetch<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      'X-Internal-Key': this.internalKey,
      ...options.headers
    };

    const response = await fetch(url, { ...options, headers });
    
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`API error ${response.status}: ${text}`);
    }

    return response.json() as Promise<T>;
  }

  async getIdentity(id: string): Promise<MatrixIdentity | undefined> {
    try {
      const resp = await this.fetch<FullIdentityResponse>(`/api/v1/internal/identities/${encodeURIComponent(id)}`);
      return apiResponseToIdentity(resp);
    } catch (error: any) {
      if (error.message?.includes('404')) {
        return undefined;
      }
      console.error('[IdentityApiClient] getIdentity error:', error.message);
      return undefined;
    }
  }

  async getIdentityByMXID(mxid: string): Promise<MatrixIdentity | undefined> {
    try {
      const resp = await this.fetch<FullIdentityResponse>(`/api/v1/internal/identities/by-mxid/${encodeURIComponent(mxid)}`);
      return apiResponseToIdentity(resp);
    } catch (error: any) {
      if (error.message?.includes('404')) {
        return undefined;
      }
      console.error('[IdentityApiClient] getIdentityByMXID error:', error.message);
      return undefined;
    }
  }

  async getAllIdentities(type?: string): Promise<MatrixIdentity[]> {
    try {
      const query = type ? `?identity_type=${encodeURIComponent(type)}` : '';
      const resp = await this.fetch<IdentityListResponse>(`/api/v1/internal/identities${query}`);
      return resp.identities.map(apiResponseToIdentity);
    } catch (error: any) {
      console.error('[IdentityApiClient] getAllIdentities error:', error.message);
      return [];
    }
  }

  async saveIdentity(identity: MatrixIdentity): Promise<boolean> {
    try {
      const existing = await this.getIdentity(identity.id);
      
      if (existing) {
        await this.fetch(`/api/v1/identities/${encodeURIComponent(identity.id)}`, {
          method: 'PUT',
          body: JSON.stringify({
            display_name: identity.displayName,
            avatar_url: identity.avatarUrl,
            access_token: identity.accessToken,
            is_active: true
          })
        });
      } else {
        const request: CreateIdentityRequest = {
          id: identity.id,
          identity_type: identity.type,
          mxid: identity.mxid,
          access_token: identity.accessToken,
          display_name: identity.displayName,
          avatar_url: identity.avatarUrl,
          password_hash: identity.password,
          device_id: undefined
        };
        
        await this.fetch('/api/v1/identities', {
          method: 'POST',
          body: JSON.stringify(request)
        });
      }
      
      return true;
    } catch (error: any) {
      console.error('[IdentityApiClient] saveIdentity error:', error.message);
      return false;
    }
  }

  async deleteIdentity(id: string): Promise<boolean> {
    try {
      await this.fetch(`/api/v1/identities/${encodeURIComponent(id)}?hard_delete=true`, {
        method: 'DELETE'
      });
      return true;
    } catch (error: any) {
      console.error('[IdentityApiClient] deleteIdentity error:', error.message);
      return false;
    }
  }

  async markUsed(id: string): Promise<void> {
    try {
      await this.fetch(`/api/v1/identities/${encodeURIComponent(id)}`, {
        method: 'PUT',
        body: JSON.stringify({ is_active: true })
      });
    } catch (error: any) {
      console.error('[IdentityApiClient] markUsed error:', error.message);
    }
  }
}

let _apiClient: IdentityApiClient | null = null;

export function getIdentityApiClient(): IdentityApiClient {
  if (!_apiClient) {
    _apiClient = new IdentityApiClient();
  }
  return _apiClient;
}
