/**
 * Identity Manager - Auto-provision Matrix users via Synapse Admin API
 */

import type { MatrixIdentity, IdentityProvisionRequest } from '../types/index.js';
import type { Storage } from './storage.js';
import { getAdminToken } from './admin-auth.js';

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
  private adminRoomId: string | null = null; // Cached admin room ID for Tuwunel

  constructor(
    storage: Storage,
    homeserverUrl: string,
    adminToken: string,
    serverName?: string
  ) {
    this.storage = storage;
    this.homeserverUrl = homeserverUrl.replace(/\/$/, '');
    this.adminToken = adminToken;
    this.serverName = serverName || process.env.MATRIX_SERVER_NAME || this.extractDomainFromUrl();
    console.log(`[IdentityManager] Using server name: ${this.serverName}`);
  }
  
  private async getToken(): Promise<string> {
    if (this.adminToken) {
      return this.adminToken;
    }
    return getAdminToken();
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
    const existing = await this.storage.getIdentityAsync(request.id);
    if (existing) {
      // Don't save back - this was overwriting display names with stale cached values
      // The lastUsedAt update is not critical enough to risk data corruption
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
            console.log('[IdentityManager] Password reset succeeded, logging in...');
            const resetLoginToken = await this.loginUser(localpart, password);
            return { name: localpart, displayname: displayName, access_token: resetLoginToken };
          } catch (resetErr) {
            // Last resort: Check if user exists in identities.json with a stored password
            const allIdentities = await this.storage.getAllIdentitiesAsync();
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
   * Reset user password - tries Tuwunel admin room first, then Synapse API fallback
   */
  private async resetUserPassword(localpart: string, newPassword: string): Promise<void> {
    const userId = `@${localpart}:${this.extractDomain()}`;
    console.log('[IdentityManager] Resetting password for:', userId);
    
    try {
      await this.resetPasswordViaTuwunelAdminRoom(localpart, newPassword);
      console.log('[IdentityManager] Password reset successful via Tuwunel admin room');
      return;
    } catch (tuwunelErr) {
      console.log('[IdentityManager] Tuwunel admin room failed, trying Synapse API:', tuwunelErr);
    }
    
    try {
      const token = await this.getToken();
      const synapseUrl = `${this.homeserverUrl}/_synapse/admin/v1/reset_password/${encodeURIComponent(userId)}`;
      const response = await fetch(synapseUrl, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
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
      
      const v2Url = `${this.homeserverUrl}/_synapse/admin/v2/users/${encodeURIComponent(userId)}`;
      const v2Response = await fetch(v2Url, {
        method: 'PUT',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ password: newPassword })
      });
      
      if (v2Response.ok) {
        console.log('[IdentityManager] Password reset successful via v2 users API');
        return;
      }
      
      const errorText = await v2Response.text();
      throw new Error(`All password reset methods failed. Last error: ${errorText}`);
    } catch (err) {
      console.error('[IdentityManager] All password reset methods failed:', err);
      throw new Error(`Failed to reset password for ${userId}`);
    }
  }

  /**
   * Reset password via Tuwunel admin room command
   * Sends "!admin users reset-password {localpart} {password}" to #admins:{serverName}
   */
  private async resetPasswordViaTuwunelAdminRoom(localpart: string, newPassword: string): Promise<void> {
    const token = await this.getToken();
    const adminRoomId = await this.getOrJoinAdminRoom(token);
    
    const command = `!admin users reset-password ${localpart} ${newPassword}`;
    const txnId = `mcp_pwd_reset_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    
    const sendUrl = `${this.homeserverUrl}/_matrix/client/v3/rooms/${encodeURIComponent(adminRoomId)}/send/m.room.message/${txnId}`;
    
    console.log('[IdentityManager] Sending admin command to room:', adminRoomId);
    
    const response = await fetch(sendUrl, {
      method: 'PUT',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        msgtype: 'm.text',
        body: command
      })
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Failed to send admin command: ${response.status} ${errorText}`);
    }
    
    // Wait for command to process (Tuwunel processes commands async)
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    console.log('[IdentityManager] Admin command sent successfully');
  }

  /**
   * Get or join the Tuwunel admin room (#admins:{serverName})
   */
  private async getOrJoinAdminRoom(token: string): Promise<string> {
    if (this.adminRoomId) {
      return this.adminRoomId;
    }
    
    const adminRoomAlias = `#admins:${this.serverName}`;
    console.log('[IdentityManager] Resolving admin room:', adminRoomAlias);
    
    const resolveUrl = `${this.homeserverUrl}/_matrix/client/v3/directory/room/${encodeURIComponent(adminRoomAlias)}`;
    const resolveResponse = await fetch(resolveUrl, {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    
    if (!resolveResponse.ok) {
      throw new Error(`Failed to resolve admin room alias ${adminRoomAlias}: ${resolveResponse.status}`);
    }
    
    const { room_id } = await resolveResponse.json() as { room_id: string };
    console.log('[IdentityManager] Admin room ID:', room_id);
    
    const joinUrl = `${this.homeserverUrl}/_matrix/client/v3/join/${encodeURIComponent(room_id)}`;
    const joinResponse = await fetch(joinUrl, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: '{}'
    });
    
    if (!joinResponse.ok && joinResponse.status !== 403) {
      // 403 usually means already joined, which is fine
      const errorText = await joinResponse.text();
      if (!errorText.includes('already in the room') && !errorText.includes('M_FORBIDDEN')) {
        console.warn('[IdentityManager] Join warning:', joinResponse.status, errorText);
      }
    }
    
    this.adminRoomId = room_id;
    return room_id;
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
    return this.storage.getIdentityAsync(id);
  }

  /**
   * Get identity by MXID
   */
  async getIdentityByMXID(mxid: string): Promise<MatrixIdentity | undefined> {
    return this.storage.getIdentityByMXIDAsync(mxid);
  }

  /**
   * List all identities
   */
  async listIdentities(type?: 'letta' | 'opencode' | 'custom'): Promise<MatrixIdentity[]> {
    const all = await this.storage.getAllIdentitiesAsync(type);
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
    const identity = await this.storage.getIdentityAsync(id);
    if (!identity) {
      throw new Error(`Identity not found: ${id}`);
    }

    const token = await this.getToken();
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
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const errorText = await response.text();
      const shouldFallback = response.status === 404 || errorText.includes('M_UNRECOGNIZED');

      if (shouldFallback && identity.accessToken) {
        const profileHeaders = {
          'Authorization': `Bearer ${identity.accessToken}`,
          'Content-Type': 'application/json'
        };

        if (displayName !== undefined) {
          const displayResponse = await fetch(
            `${this.homeserverUrl}/_matrix/client/v3/profile/${identity.mxid}/displayname`,
            {
              method: 'PUT',
              headers: profileHeaders,
              body: JSON.stringify({ displayname: displayName })
            }
          );
          if (!displayResponse.ok) {
            const displayError = await displayResponse.text();
            throw new Error(`Failed to update display name: ${displayResponse.status} ${displayError}`);
          }
        }

        if (avatarUrl !== undefined) {
          const avatarResponse = await fetch(
            `${this.homeserverUrl}/_matrix/client/v3/profile/${identity.mxid}/avatar_url`,
            {
              method: 'PUT',
              headers: profileHeaders,
              body: JSON.stringify({ avatar_url: avatarUrl })
            }
          );
          if (!avatarResponse.ok) {
            const avatarError = await avatarResponse.text();
            throw new Error(`Failed to update avatar: ${avatarResponse.status} ${avatarError}`);
          }
        }
      } else {
        throw new Error(`Failed to update user: ${response.status} ${errorText}`);
      }
    }

    await this.storage.saveIdentity(identity);

    console.log('[IdentityManager] Updated identity:', id);
    return identity;
  }

  /**
   * Delete identity (deactivate Matrix user)
   */
  async deleteIdentity(id: string): Promise<boolean> {
    const identity = await this.storage.getIdentityAsync(id);
    if (!identity) {
      return false;
    }

    const token = await this.getToken();
    const url = `${this.homeserverUrl}/_synapse/admin/v1/deactivate/${identity.mxid}`;
    
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
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
