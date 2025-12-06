/**
 * OpenCode Integration Service
 * Derives Matrix identities from OpenCode working directories
 */

import type { Storage } from '../core/storage.js';
import { IdentityManager } from '../core/identity-manager.js';
import type { MatrixIdentity } from '../types/index.js';

export interface OpenCodeSession {
  directory: string;
  identityId: string;
  mxid: string;
  displayName: string;
  connectedAt: number;
  lastActivityAt: number;
}

export interface OpenCodeConfig {
  baseUrl?: string;  // OpenCode API URL (optional)
}

export class OpenCodeService {
  private storage: Storage;
  private identityManager: IdentityManager;
  private config: OpenCodeConfig;
  private sessions: Map<string, OpenCodeSession> = new Map();

  constructor(
    storage: Storage,
    identityManager: IdentityManager,
    config: OpenCodeConfig = {}
  ) {
    this.storage = storage;
    this.identityManager = identityManager;
    this.config = config;
  }

  /**
   * Derive identity ID from directory path
   * Uses the directory name as a human-readable identifier
   */
  deriveIdentityId(directory: string): string {
    return IdentityManager.generateOpenCodeId(directory);
  }

  /**
   * Derive Matrix localpart from directory path
   * Extracts project name and sanitizes for Matrix
   */
  deriveLocalpart(directory: string): string {
    return IdentityManager.generateOpenCodeLocalpart(directory);
  }

  /**
   * Derive display name from directory path
   * Makes a human-readable name from the project directory
   */
  deriveDisplayName(directory: string): string {
    // Extract project name from path
    const parts = directory.split('/').filter(p => p.length > 0);
    const projectName = parts[parts.length - 1] || 'Unknown';
    
    // Format nicely: my-project -> My Project
    const formatted = projectName
      .split(/[-_]/)
      .map(word => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(' ');
    
    return `OpenCode: ${formatted}`;
  }

  /**
   * Connect an OpenCode instance to Matrix
   * Creates or retrieves the Matrix identity for this directory
   */
  async connect(
    directory: string,
    displayNameOverride?: string,
    sessionId?: string
  ): Promise<OpenCodeSession> {
    const identityId = this.deriveIdentityId(directory);
    
    // Check if session already exists
    const existingSession = this.sessions.get(directory);
    if (existingSession) {
      existingSession.lastActivityAt = Date.now();
      return existingSession;
    }

    // Get or create Matrix identity
    const localpart = this.deriveLocalpart(directory);
    const displayName = displayNameOverride || this.deriveDisplayName(directory);
    
    const identity = await this.identityManager.getOrCreateIdentity({
      id: identityId,
      localpart,
      displayName,
      type: 'opencode'
    });

    // Create session
    const session: OpenCodeSession = {
      directory,
      identityId,
      mxid: identity.mxid,
      displayName: identity.displayName,
      connectedAt: Date.now(),
      lastActivityAt: Date.now()
    };

    this.sessions.set(directory, session);
    console.log('[OpenCodeService] Connected:', directory, '->', identity.mxid);

    return session;
  }

  /**
   * Disconnect an OpenCode instance
   */
  disconnect(directory: string): boolean {
    const deleted = this.sessions.delete(directory);
    if (deleted) {
      console.log('[OpenCodeService] Disconnected:', directory);
    }
    return deleted;
  }

  /**
   * Get session for a directory
   */
  getSession(directory: string): OpenCodeSession | undefined {
    return this.sessions.get(directory);
  }

  /**
   * Get all active sessions
   */
  getAllSessions(): OpenCodeSession[] {
    return Array.from(this.sessions.values());
  }

  /**
   * Get identity for a directory (creates if needed)
   */
  async getOrCreateIdentity(directory: string): Promise<MatrixIdentity> {
    const identityId = this.deriveIdentityId(directory);
    
    // Check if identity already exists
    const existing = this.storage.getIdentity(identityId);
    if (existing) {
      return existing;
    }

    // Create new identity
    const localpart = this.deriveLocalpart(directory);
    const displayName = this.deriveDisplayName(directory);
    
    return await this.identityManager.getOrCreateIdentity({
      id: identityId,
      localpart,
      displayName,
      type: 'opencode'
    });
  }

  /**
   * Get or create a default OpenCode identity
   * Used when no specific directory is provided - enables "just works" messaging
   * This is the fallback identity for any OpenCode instance
   */
  async getOrCreateDefaultIdentity(): Promise<MatrixIdentity> {
    const defaultId = 'opencode_default';
    const defaultLocalpart = 'opencode';
    const defaultDisplayName = 'OpenCode';
    
    // Check if default identity already exists
    const existing = this.storage.getIdentity(defaultId);
    if (existing) {
      console.log('[OpenCodeService] Using existing default identity:', existing.mxid);
      return existing;
    }

    // Create default identity
    console.log('[OpenCodeService] Creating default OpenCode identity...');
    const identity = await this.identityManager.getOrCreateIdentity({
      id: defaultId,
      localpart: defaultLocalpart,
      displayName: defaultDisplayName,
      type: 'opencode'
    });
    
    console.log('[OpenCodeService] Created default identity:', identity.mxid);
    return identity;
  }

  /**
   * Check if a directory has a Matrix identity
   */
  hasIdentity(directory: string): boolean {
    const identityId = this.deriveIdentityId(directory);
    return !!this.storage.getIdentity(identityId);
  }

  /**
   * Get identity by directory
   */
  getIdentity(directory: string): MatrixIdentity | undefined {
    const identityId = this.deriveIdentityId(directory);
    return this.storage.getIdentity(identityId);
  }

  /**
   * List all OpenCode identities
   */
  async listIdentities(): Promise<Array<{
    identityId: string;
    directory: string;
    mxid: string;
    displayName: string;
    isConnected: boolean;
  }>> {
    const identities = await this.identityManager.listIdentities('opencode');
    
    return identities.map(identity => {
      // Decode directory from identity ID
      const encoded = identity.id.replace(/^opencode_/, '');
      let directory = '';
      try {
        // Reverse the base64url encoding
        const base64 = encoded.replace(/-/g, '+').replace(/_/g, '/');
        directory = Buffer.from(base64, 'base64').toString('utf-8');
      } catch {
        directory = 'unknown';
      }

      return {
        identityId: identity.id,
        directory,
        mxid: identity.mxid,
        displayName: identity.displayName,
        isConnected: this.sessions.has(directory)
      };
    });
  }

  /**
   * Get status summary
   */
  getStatus(): {
    totalIdentities: number;
    activeSessions: number;
    sessions: OpenCodeSession[];
  } {
    const sessions = this.getAllSessions();
    const identities = this.storage.getAllIdentities().filter(i => i.type === 'opencode');

    return {
      totalIdentities: identities.length,
      activeSessions: sessions.length,
      sessions
    };
  }
}
