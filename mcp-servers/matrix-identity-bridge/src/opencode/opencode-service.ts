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

export interface ActiveOpenCodeInstance {
  directory: string;
  projectName: string;
  identity: MatrixIdentity;
  registration: OpenCodeBridgeRegistration;
  lastSeen: Date;
  rooms: string[];
}

export interface OpenCodeConfig {
  baseUrl?: string;  // OpenCode API URL (optional)
}

export interface OpenCodeBridgeRegistration {
  id: string;
  port: number;
  hostname: string;
  sessionId: string;
  directory: string;
  rooms: string[];
  registeredAt: number;
  lastSeen: number;
}

export class OpenCodeService {
  private storage: Storage;
  private identityManager: IdentityManager;
  private config: OpenCodeConfig;

  constructor(
    storage: Storage,
    identityManager: IdentityManager,
    config: OpenCodeConfig = {}
  ) {
    this.storage = storage;
    this.identityManager = identityManager;
    this.config = config;
  }

  private getBridgeUrl(): string {
    return process.env.OPENCODE_BRIDGE_URL || 'http://127.0.0.1:3201';
  }

  private async fetchBridgeRegistrations(): Promise<OpenCodeBridgeRegistration[]> {
    const bridgeUrl = this.getBridgeUrl();
    const response = await fetch(`${bridgeUrl}/registrations`);
    if (!response.ok) {
      throw new Error(`OpenCode bridge error: ${response.status} ${response.statusText}`);
    }
    const data = await response.json() as { registrations?: OpenCodeBridgeRegistration[] };
    return Array.isArray(data.registrations) ? data.registrations : [];
  }

  private async triggerBridgeDiscovery(): Promise<void> {
    const bridgeUrl = this.getBridgeUrl();
    try {
      await fetch(`${bridgeUrl}/discover`, { method: 'POST' });
    } catch {
      return;
    }
  }

  private pickLatestRegistrationForDirectory(
    registrations: OpenCodeBridgeRegistration[],
    directory: string
  ): OpenCodeBridgeRegistration | undefined {
    const matching = registrations.filter((r) => r.directory === directory);
    if (matching.length === 0) return undefined;
    return matching.sort((a, b) => (b.lastSeen || 0) - (a.lastSeen || 0))[0];
  }

  private pickLatestRegistrationPerDirectory(
    registrations: OpenCodeBridgeRegistration[]
  ): OpenCodeBridgeRegistration[] {
    const byDir = new Map<string, OpenCodeBridgeRegistration>();
    for (const reg of registrations) {
      const existing = byDir.get(reg.directory);
      if (!existing || (reg.lastSeen || 0) > (existing.lastSeen || 0)) {
        byDir.set(reg.directory, reg);
      }
    }
    return Array.from(byDir.values());
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
    void sessionId;

    const identity = await this.getOrCreateIdentity(directory, displayNameOverride);

    await this.triggerBridgeDiscovery();

    let registration: OpenCodeBridgeRegistration | undefined;
    try {
      const registrations = await this.fetchBridgeRegistrations();
      registration = this.pickLatestRegistrationForDirectory(registrations, directory);
    } catch {
      registration = undefined;
    }

    const connectedAt = registration?.registeredAt ? registration.registeredAt : Date.now();
    const lastActivityAt = registration?.lastSeen ? registration.lastSeen : Date.now();

    return {
      directory,
      identityId: identity.id,
      mxid: identity.mxid,
      displayName: identity.displayName,
      connectedAt,
      lastActivityAt
    };
  }

  /**
   * Disconnect an OpenCode instance
   */
  disconnect(directory: string): boolean {
    void directory;
    return false;
  }

  /**
   * Get session for a directory
   */
  getSession(directory: string): OpenCodeSession | undefined {
    void directory;
    return undefined;
  }

  /**
   * Get all active sessions
   */
  getAllSessions(): OpenCodeSession[] {
    return [];
  }

  async getBridgeSession(directory: string): Promise<OpenCodeSession | undefined> {
    await this.triggerBridgeDiscovery();

    let registration: OpenCodeBridgeRegistration | undefined;
    try {
      const registrations = await this.fetchBridgeRegistrations();
      registration = this.pickLatestRegistrationForDirectory(registrations, directory);
    } catch {
      registration = undefined;
    }

    if (!registration) return undefined;

    const identity = await this.getOrCreateIdentity(directory);

    return {
      directory,
      identityId: identity.id,
      mxid: identity.mxid,
      displayName: identity.displayName,
      connectedAt: registration.registeredAt || Date.now(),
      lastActivityAt: registration.lastSeen || Date.now()
    };
  }

  async listBridgeSessions(): Promise<OpenCodeSession[]> {
    await this.triggerBridgeDiscovery();

    let registrations: OpenCodeBridgeRegistration[] = [];
    try {
      registrations = await this.fetchBridgeRegistrations();
    } catch {
      registrations = [];
    }

    const latest = this.pickLatestRegistrationPerDirectory(registrations);

    const sessions: OpenCodeSession[] = [];
    for (const reg of latest) {
      try {
        const identity = await this.getOrCreateIdentity(reg.directory);
        sessions.push({
          directory: reg.directory,
          identityId: identity.id,
          mxid: identity.mxid,
          displayName: identity.displayName,
          connectedAt: reg.registeredAt || Date.now(),
          lastActivityAt: reg.lastSeen || Date.now()
        });
      } catch {
        continue;
      }
    }

    return sessions;
  }

  /**
   * Get identity for a directory (creates if needed)
   */
  async getOrCreateIdentity(directory: string, displayNameOverride?: string): Promise<MatrixIdentity> {
    const identityId = this.deriveIdentityId(directory);

    const existing = await this.storage.getIdentityAsync(identityId);
    if (existing) {
      if (displayNameOverride && existing.displayName !== displayNameOverride) {
        try {
          await this.identityManager.updateIdentity(existing.id, displayNameOverride);
          existing.displayName = displayNameOverride;
        } catch (error) {
          console.warn('[OpenCodeService] Failed to update identity display name:', error);
        }
      }
      return existing;
    }

    const localpart = this.deriveLocalpart(directory);
    const displayName = displayNameOverride || this.deriveDisplayName(directory);

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
    const existing = await this.storage.getIdentityAsync(defaultId);
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
  async hasIdentity(directory: string): Promise<boolean> {
    const identityId = this.deriveIdentityId(directory);
    return !!await this.storage.getIdentityAsync(identityId);
  }

  /**
   * Get identity by directory
   */
  async getIdentity(directory: string): Promise<MatrixIdentity | undefined> {
    const identityId = this.deriveIdentityId(directory);
    return this.storage.getIdentityAsync(identityId);
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

    let registrations: OpenCodeBridgeRegistration[] = [];
    try {
      registrations = await this.fetchBridgeRegistrations();
    } catch {
      registrations = [];
    }

    const activeDirs = new Set(registrations.map((r) => r.directory));

    return identities.map(identity => {
      const encoded = identity.id.replace(/^opencode_/, '');
      let directory = '';
      try {
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
        isConnected: activeDirs.has(directory)
      };
    });
  }

  /**
   * Get status summary
   */
  async getStatus(): Promise<{
    totalIdentities: number;
    activeSessions: number;
    sessions: OpenCodeSession[];
  }> {
    const identities = await this.storage.getAllIdentitiesAsync('opencode');

    let registrations: OpenCodeBridgeRegistration[] = [];
    try {
      registrations = await this.fetchBridgeRegistrations();
    } catch {
      registrations = [];
    }

    const latest = this.pickLatestRegistrationPerDirectory(registrations);

    const sessions: OpenCodeSession[] = [];
    for (const reg of latest) {
      try {
        const identity = await this.getOrCreateIdentity(reg.directory);
        sessions.push({
          directory: reg.directory,
          identityId: identity.id,
          mxid: identity.mxid,
          displayName: identity.displayName,
          connectedAt: reg.registeredAt || Date.now(),
          lastActivityAt: reg.lastSeen || Date.now()
        });
      } catch {
        continue;
      }
    }

    return {
      totalIdentities: identities.length,
      activeSessions: latest.length,
      sessions
    };
  }

  /**
   * Extract project name from directory path
   */
  extractProjectName(directory: string): string {
    return directory.split('/').filter(Boolean).pop() || 'unknown';
  }

  /**
   * Match target string to directory
   * Supports: full path, project name, partial match
   */
  matchTarget(target: string, directory: string): boolean {
    // Exact directory match
    if (target === directory) return true;
    
    // Project name match
    const projectName = this.extractProjectName(directory);
    if (target.toLowerCase() === projectName.toLowerCase()) return true;
    
    // Partial match (contains)
    if (directory.toLowerCase().includes(target.toLowerCase())) return true;
    
    return false;
  }

  /**
   * Get active OpenCode instances from bridge registrations
   * Only returns instances that have been seen within the threshold
   */
  async getActiveInstances(thresholdSeconds: number = 120): Promise<ActiveOpenCodeInstance[]> {
    await this.triggerBridgeDiscovery();

    let registrations: OpenCodeBridgeRegistration[] = [];
    try {
      registrations = await this.fetchBridgeRegistrations();
    } catch {
      registrations = [];
    }

    const now = Date.now();
    const thresholdMs = thresholdSeconds * 1000;

    // Filter to only active registrations (within threshold)
    const activeRegs = registrations.filter(reg => {
      const age = now - (reg.lastSeen || 0);
      return age < thresholdMs;
    });

    // Pick latest per directory
    const latest = this.pickLatestRegistrationPerDirectory(activeRegs);

    const instances: ActiveOpenCodeInstance[] = [];
    for (const reg of latest) {
      try {
        const identity = await this.getOrCreateIdentity(reg.directory);
        instances.push({
          directory: reg.directory,
          projectName: this.extractProjectName(reg.directory),
          identity,
          registration: reg,
          lastSeen: new Date(reg.lastSeen || Date.now()),
          rooms: reg.rooms || []
        });
      } catch {
        continue;
      }
    }

    return instances;
  }

  /**
   * Find an active instance by project name or directory
   */
  async findActiveInstance(target: string): Promise<ActiveOpenCodeInstance | undefined> {
    const instances = await this.getActiveInstances();
    return instances.find(inst => this.matchTarget(target, inst.directory));
  }

  /**
   * Check if an instance is currently active
   */
  async isInstanceActive(directory: string, thresholdSeconds: number = 120): Promise<boolean> {
    const instances = await this.getActiveInstances(thresholdSeconds);
    return instances.some(inst => inst.directory === directory);
  }

  /**
   * Get identity by target (project name or directory)
   * Used to check if an identity exists even if instance is not active
   */
  async getIdentityByTarget(target: string): Promise<MatrixIdentity | undefined> {
    // If target looks like a full path, use it directly
    if (target.startsWith('/')) {
      return this.getIdentity(target);
    }

    // Otherwise, search through known identities
    const identities = await this.listIdentities();
    const match = identities.find(i => this.matchTarget(target, i.directory));
    if (match) {
      return this.storage.getIdentityAsync(match.identityId);
    }

    return undefined;
  }
}
