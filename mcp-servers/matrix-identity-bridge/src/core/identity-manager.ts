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
  access_token?: string;
}

export class IdentityManager {
  private storage: Storage;
  private homeserverUrl: string;
  private adminToken: string;
  private serverName: string;

  constructor(
    storage: Storage,
    homeserverUrl: string,
    adminToken: string,
    serverName?: string
  ) {
    this.storage = storage;
    this.homeserverUrl = homeserverUrl.replace(/\/$/, ''); // Remove trailing slash
    this.adminToken = adminToken;
    // Use explicit server name or extract from MATRIX_SERVER_NAME env var or fall back to URL
    this.serverName = serverName || process.env.MATRIX_SERVER_NAME || this.extractDomainFromUrl();
    console.log(`[IdentityManager] Using server name: ${this.serverName}`);
  }
  
  /**
   * Extract domain from URL (fallback only)
   */
  private extractDomainFromUrl(): string {
    try {
      const url = new URL(this.homeserverUrl);
      return url.hostname;
    } catch {
      return 'matrix.oculair.ca'; // Safe default
    }
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
    
    // Use deterministic password based on localpart + secret
    // This allows recovery if we lose the identity but user still exists
    const passwordSecret = process.env.MATRIX_PASSWORD_SECRET || 'mcp_identity_bridge_2024';
    const password = this.generateDeterministicPassword(request.localpart, passwordSecret);

    // Create user via registration API - this may return an access token directly
    console.log('[IdentityManager] Registering user...');
    const createResult = await this.createSynapseUser(
      request.localpart,
      password,
      request.displayName,
      request.avatarUrl
    );
    console.log('[IdentityManager] Registration result:', createResult);

    // Get access token - either from registration or via login
    let accessToken: string;
    if (createResult.access_token) {
      // Registration returned an access token directly
      console.log('[IdentityManager] Using access token from registration');
      accessToken = createResult.access_token;
    } else {
      // Need to login to get access token
      console.log('[IdentityManager] Logging in user...');
      accessToken = await this.loginUser(request.localpart, password);
      console.log('[IdentityManager] Got access token via login');
    }

    // Create identity object (store password for recovery)
    const identity: MatrixIdentity = {
      id: request.id,
      mxid,
      displayName: request.displayName,
      avatarUrl: request.avatarUrl,
      accessToken,
      password, // Store password for token recovery
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
        console.log('[IdentityManager] User created:', result.user_id, 'has token:', !!result.access_token);
        
        // Set display name using the returned token
        if (result.access_token) {
          await this.setDisplayName(result.user_id || `@${localpart}:${this.extractDomain()}`, displayName, result.access_token);
        }
        
        // Return access_token so we don't need to login separately
        return { 
          name: result.user_id || localpart, 
          displayname: displayName,
          access_token: result.access_token
        };
      } else if (response.status === 400) {
        const errorData = await response.json() as { errcode?: string; error?: string };
        if (errorData.errcode === 'M_USER_IN_USE') {
          console.log('[IdentityManager] User already exists:', localpart);
          
          // First, try to login with the provided password (in case we got lucky)
          // This handles cases where registration succeeded but we didn't save properly
          try {
            const loginToken = await this.loginUser(localpart, password);
            console.log('[IdentityManager] Login succeeded with new password - returning token');
            return { name: localpart, displayname: displayName, access_token: loginToken };
          } catch (loginErr) {
            console.log('[IdentityManager] Login with new password failed, trying admin reset');
          }
          
          // Try to reset password via admin API (may not work on all servers)
          try {
            await this.resetUserPassword(localpart, password);
            console.log('[IdentityManager] Password reset succeeded');
            return { name: localpart, displayname: displayName };
          } catch (resetErr) {
            // Last resort: Check if user exists in identities.json with a stored password
            const allIdentities = this.storage.getAllIdentities();
            const existingIdentity = allIdentities.find(i => i.mxid === `@${localpart}:${this.extractDomain()}`);
            if (existingIdentity?.password) {
              console.log('[IdentityManager] Found stored password, trying login');
              try {
                const storedToken = await this.loginUser(localpart, existingIdentity.password);
                return { name: localpart, displayname: displayName, access_token: storedToken };
              } catch (storedLoginErr) {
                console.log('[IdentityManager] Stored password login failed');
              }
            }
            
            // Cannot recover - user exists on Matrix server but we don't have credentials
            throw new Error(
              `User @${localpart}:${this.extractDomain()} exists on Matrix server but identity was lost. ` +
              `Manual intervention required: Either delete the Matrix user or restore the identity from backup.`
            );
          }
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
   * Reset user password via admin API (for when user exists but we lost credentials)
   */
  private async resetUserPassword(localpart: string, newPassword: string): Promise<void> {
    const userId = `@${localpart}:${this.extractDomain()}`;
    // Try Synapse admin API first
    const synapseUrl = `${this.homeserverUrl}/_synapse/admin/v1/reset_password/${encodeURIComponent(userId)}`;
    
    console.log('[IdentityManager] Resetting password for:', userId);
    
    try {
      const response = await fetch(synapseUrl, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${this.adminToken}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ 
          new_password: newPassword,
          logout_devices: false
        })
      });
      
      if (response.ok) {
        console.log('[IdentityManager] Password reset successful via Synapse admin API');
        return;
      }
      
      // If Synapse API fails, try Tuwunel/generic approach - update user via v2 API
      const v2Url = `${this.homeserverUrl}/_synapse/admin/v2/users/${encodeURIComponent(userId)}`;
      const v2Response = await fetch(v2Url, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${this.adminToken}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ password: newPassword })
      });
      
      if (v2Response.ok) {
        console.log('[IdentityManager] Password reset successful via v2 users API');
        return;
      }
      
      console.warn('[IdentityManager] Password reset failed:', await v2Response.text());
    } catch (err) {
      console.error('[IdentityManager] Error resetting password:', err);
      throw new Error(`Failed to reset password for existing user ${userId}`);
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
   * Includes v2 suffix to avoid conflicts with old identities
   */
  static generateOpenCodeId(directory: string): string {
    // Use base64 encoding of directory path for uniqueness
    const encoded = Buffer.from(directory).toString('base64')
      .replace(/=/g, '')
      .replace(/\+/g, '-')
      .replace(/\//g, '_');
    // v2 suffix to differentiate from old identities with wrong domain
    return `opencode_v2_${encoded}`;
  }

  /**
   * Generate Letta localpart from agent_id
   * 
   * IMPORTANT: This must match the format used by matrix-client's user_manager.py
   * Format: agent_{uuid_with_underscores} (NOT letta_agent_...)
   * 
   * The matrix-client (Python) creates users as @agent_{uuid}:matrix.oculair.ca
   * We must use the same format to avoid creating duplicate Matrix users.
   */
  static generateLettaLocalpart(agentId: string): string {
    // Extract UUID from agent ID (remove 'agent-' prefix if present)
    let cleanId = agentId;
    if (cleanId.startsWith('agent-')) {
      cleanId = cleanId.substring(6); // Remove 'agent-' prefix
    }
    
    // Replace hyphens with underscores for Matrix compatibility
    cleanId = cleanId.replace(/-/g, '_');
    
    // Ensure it only contains valid characters (lowercase alphanumeric + underscore)
    cleanId = cleanId.toLowerCase().replace(/[^a-z0-9_]/g, '');
    
    // Create username as 'agent_{id}' to match matrix-client format
    return `agent_${cleanId}`;
  }

  /**
   * Generate OpenCode localpart from directory
   * Includes version suffix (v2) to avoid conflicts with old users created with wrong domain
   */
  static generateOpenCodeLocalpart(directory: string): string {
    // Extract project name from directory path
    const projectName = directory.split('/').pop() || 'unknown';
    // v2 suffix to differentiate from old users created with 127.0.0.1 domain
    return `oc_${projectName.toLowerCase().replace(/[^a-z0-9_]/g, '_')}_v2`;
  }

  /**
   * Get the Matrix server name (domain part of MXIDs)
   */
  private extractDomain(): string {
    return this.serverName;
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

  /**
   * Generate deterministic password from localpart and secret
   * This allows recovery if identity is lost but user exists on Matrix server
   */
  private generateDeterministicPassword(localpart: string, secret: string): string {
    // Simple hash-based password generation
    // In production, use a proper crypto hash, but this works for our use case
    const input = `${localpart}:${secret}`;
    let hash = 0;
    for (let i = 0; i < input.length; i++) {
      const char = input.charCodeAt(i);
      hash = ((hash << 5) - hash) + char;
      hash = hash & hash; // Convert to 32bit integer
    }
    
    // Generate password from hash
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let password = 'MCP_'; // Prefix for identification
    const absHash = Math.abs(hash);
    for (let i = 0; i < 24; i++) {
      password += chars.charAt((absHash + i * 7) % chars.length);
    }
    return password;
  }
}
