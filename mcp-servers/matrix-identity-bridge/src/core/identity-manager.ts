/**
 * Identity Manager - Auto-provision Matrix users via Synapse Admin API
 */

import type { MatrixIdentity, IdentityProvisionRequest } from '../types/index.js';
import type { Storage } from './storage.js';

interface SynapseUserCreateRequest {
  password?: string;
  displayname?: string;
  avatar_url?: string;
  admin?: boolean;
}

interface SynapseUserResponse {
  name: string;
  displayname?: string;
  avatar_url?: string;
}

export class IdentityManager {
  private storage: Storage;
  private homeserverUrl: string;
  private adminToken: string;

  constructor(
    storage: Storage,
    homeserverUrl: string,
    adminToken: string
  ) {
    this.storage = storage;
    this.homeserverUrl = homeserverUrl.replace(/\/$/, ''); // Remove trailing slash
    this.adminToken = adminToken;
  }

  /**
   * Get or create identity by ID
   * If identity exists in storage, return it
   * Otherwise, provision new Matrix user
   */
  async getOrCreateIdentity(request: IdentityProvisionRequest): Promise<MatrixIdentity> {
    // Check if identity already exists
    const existing = this.storage.getIdentity(request.id);
    if (existing) {
      // Update last used timestamp
      existing.lastUsedAt = Date.now();
      await this.storage.saveIdentity(existing);
      console.log('[IdentityManager] Using existing identity:', request.id);
      return existing;
    }

    // Provision new Matrix user
    console.log('[IdentityManager] Provisioning new identity:', request.id);
    return await this.provisionIdentity(request);
  }

  /**
   * Provision a new Matrix user via Synapse Admin API
   */
  private async provisionIdentity(request: IdentityProvisionRequest): Promise<MatrixIdentity> {
    const mxid = `@${request.localpart}:${this.extractDomain()}`;
    console.log('[IdentityManager] Creating MXID:', mxid);
    
    // Generate random password for the user
    const password = this.generatePassword();

    // Create user via Synapse Admin API
    console.log('[IdentityManager] Calling Synapse admin API...');
    const createResponse = await this.createSynapseUser(
      request.localpart,
      password,
      request.displayName,
      request.avatarUrl
    );
    console.log('[IdentityManager] User created:', createResponse);

    // Login to get access token
    console.log('[IdentityManager] Logging in user...');
    const accessToken = await this.loginUser(request.localpart, password);
    console.log('[IdentityManager] Got access token');

    // Create identity object
    const identity: MatrixIdentity = {
      id: request.id,
      mxid,
      displayName: request.displayName,
      avatarUrl: request.avatarUrl,
      accessToken,
      type: request.type,
      createdAt: Date.now(),
      lastUsedAt: Date.now()
    };

    // Save to storage
    await this.storage.saveIdentity(identity);

    console.log('[IdentityManager] Provisioned identity:', {
      id: identity.id,
      mxid: identity.mxid,
      type: identity.type
    });

    return identity;
  }

  /**
   * Create Matrix user via standard registration API (Tuwunel/Synapse compatible)
   */
  private async createSynapseUser(
    localpart: string,
    password: string,
    displayName: string,
    avatarUrl?: string
  ): Promise<SynapseUserResponse> {
    const url = `${this.homeserverUrl}/_matrix/client/v3/register`;
    
    // Use registration token for Tuwunel (from environment)
    const registrationToken = process.env.MATRIX_REGISTRATION_TOKEN || 'matrix_mcp_secret_token_2024';
    
    const body = {
      username: localpart,
      password,
      auth: { 
        type: 'm.login.registration_token',
        token: registrationToken
      }
    };

    console.log('[IdentityManager] POST', url);
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(body)
      });

      console.log('[IdentityManager] Response status:', response.status);
      
      if (response.status === 200) {
        const result = await response.json() as { access_token?: string; user_id?: string };
        console.log('[IdentityManager] User created:', result.user_id);
        
        // Set display name using the returned token
        if (result.access_token) {
          await this.setDisplayName(result.user_id || `@${localpart}:${this.extractDomain()}`, displayName, result.access_token);
        }
        
        return { name: result.user_id || localpart, displayname: displayName };
      } else if (response.status === 400) {
        const errorData = await response.json() as { errcode?: string; error?: string };
        if (errorData.errcode === 'M_USER_IN_USE') {
          console.log('[IdentityManager] User already exists:', localpart);
          return { name: localpart, displayname: displayName };
        }
        throw new Error(`Failed to create user: ${errorData.errcode} - ${errorData.error}`);
      } else {
        const error = await response.text();
        console.error('[IdentityManager] Error response:', error);
        throw new Error(`Failed to create user: ${response.status} ${error}`);
      }
    } catch (err) {
      console.error('[IdentityManager] Fetch error:', err);
      throw err;
    }
  }
  
  /**
   * Set display name for a user
   */
  private async setDisplayName(userId: string, displayName: string, accessToken: string): Promise<void> {
    try {
      const url = `${this.homeserverUrl}/_matrix/client/v3/profile/${encodeURIComponent(userId)}/displayname`;
      const response = await fetch(url, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${accessToken}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ displayname: displayName })
      });
      
      if (response.status === 200) {
        console.log('[IdentityManager] Set display name:', displayName);
      } else {
        console.warn('[IdentityManager] Failed to set display name:', response.status);
      }
    } catch (err) {
      console.warn('[IdentityManager] Error setting display name:', err);
    }
  }

  /**
   * Login user to get access token
   */
  private async loginUser(localpart: string, password: string): Promise<string> {
    const url = `${this.homeserverUrl}/_matrix/client/v3/login`;
    
    const body = {
      type: 'm.login.password',
      identifier: {
        type: 'm.id.user',
        user: localpart
      },
      password
    };

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to login user: ${response.status} ${error}`);
    }

    const data = await response.json() as { access_token: string };
    return data.access_token;
  }

  /**
   * Get identity by ID
   */
  async getIdentity(id: string): Promise<MatrixIdentity | undefined> {
    return this.storage.getIdentity(id);
  }

  /**
   * Get identity by MXID
   */
  async getIdentityByMXID(mxid: string): Promise<MatrixIdentity | undefined> {
    return this.storage.getIdentityByMXID(mxid);
  }

  /**
   * List all identities
   */
  async listIdentities(type?: 'letta' | 'opencode' | 'custom'): Promise<MatrixIdentity[]> {
    const all = this.storage.getAllIdentities();
    if (type) {
      return all.filter(i => i.type === type);
    }
    return all;
  }

  /**
   * Update identity display name and avatar
   */
  async updateIdentity(
    id: string,
    displayName?: string,
    avatarUrl?: string
  ): Promise<MatrixIdentity> {
    const identity = this.storage.getIdentity(id);
    if (!identity) {
      throw new Error(`Identity not found: ${id}`);
    }

    // Update via Synapse Admin API
    const url = `${this.homeserverUrl}/_synapse/admin/v2/users/${identity.mxid}`;
    
    const body: Partial<SynapseUserCreateRequest> = {};
    if (displayName !== undefined) {
      body.displayname = displayName;
      identity.displayName = displayName;
    }
    if (avatarUrl !== undefined) {
      body.avatar_url = avatarUrl;
      identity.avatarUrl = avatarUrl;
    }

    const response = await fetch(url, {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${this.adminToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to update user: ${response.status} ${error}`);
    }

    // Save updated identity
    await this.storage.saveIdentity(identity);

    console.log('[IdentityManager] Updated identity:', id);
    return identity;
  }

  /**
   * Delete identity (deactivate Matrix user)
   */
  async deleteIdentity(id: string): Promise<boolean> {
    const identity = this.storage.getIdentity(id);
    if (!identity) {
      return false;
    }

    // Deactivate user via Synapse Admin API
    const url = `${this.homeserverUrl}/_synapse/admin/v1/deactivate/${identity.mxid}`;
    
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.adminToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ erase: false })
    });

    if (!response.ok) {
      console.error('[IdentityManager] Failed to deactivate user:', await response.text());
    }

    // Remove from storage
    await this.storage.deleteIdentity(id);

    console.log('[IdentityManager] Deleted identity:', id);
    return true;
  }

  /**
   * Generate Letta identity ID from agent_id
   */
  static generateLettaId(agentId: string): string {
    return `letta_${agentId}`;
  }

  /**
   * Generate OpenCode identity ID from directory
   */
  static generateOpenCodeId(directory: string): string {
    // Use base64 encoding of directory path for uniqueness
    const encoded = Buffer.from(directory).toString('base64')
      .replace(/=/g, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');
    return `opencode_${encoded}`;
  }

  /**
   * Generate Letta localpart from agent_id
   */
  static generateLettaLocalpart(agentId: string): string {
    // Sanitize agent_id for Matrix username (lowercase, alphanumeric + underscore)
    return `letta_${agentId.toLowerCase().replace(/[^a-z0-9_]/g, '_')}`;
  }

  /**
   * Generate OpenCode localpart from directory
   */
  static generateOpenCodeLocalpart(directory: string): string {
    // Extract project name from directory path
    const projectName = directory.split('/').pop() || 'unknown';
    return `oc_${projectName.toLowerCase().replace(/[^a-z0-9_]/g, '_')}`;
  }

  /**
   * Extract domain from homeserver URL
   */
  private extractDomain(): string {
    try {
      const url = new URL(this.homeserverUrl);
      return url.hostname;
    } catch {
      throw new Error(`Invalid homeserver URL: ${this.homeserverUrl}`);
    }
  }

  /**
   * Generate random password
   */
  private generatePassword(): string {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*';
    const length = 32;
    let password = '';
    for (let i = 0; i < length; i++) {
      password += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return password;
  }
}
