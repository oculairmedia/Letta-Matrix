/**
 * Matrix Client Pool - Manage per-identity Matrix clients
 */

import { MatrixClient, SimpleFsStorageProvider, AutojoinRoomsMixin } from '@vector-im/matrix-bot-sdk';
import type { MatrixIdentity } from '../types/index.js';
import type { Storage } from './storage.js';
import path from 'path';
import fs from 'fs/promises';

export class MatrixClientPool {
  private storage: Storage;
  private homeserverUrl: string;
  private clients: Map<string, MatrixClient> = new Map();
  private storageDir: string;

  constructor(storage: Storage, homeserverUrl: string, storageDir: string = './data/clients') {
    this.storage = storage;
    this.homeserverUrl = homeserverUrl;
    this.storageDir = storageDir;
  }

  /**
   * Initialize client pool
   */
  async initialize(): Promise<void> {
    await fs.mkdir(this.storageDir, { recursive: true });
    console.log('[ClientPool] Initialized');
  }

  /**
   * Get or create Matrix client for identity
   */
  async getClient(identity: MatrixIdentity): Promise<MatrixClient> {
    // Return existing client if already created
    if (this.clients.has(identity.id)) {
      return this.clients.get(identity.id)!;
    }

    // Create new client
    const client = await this.createClient(identity);
    this.clients.set(identity.id, client);

    console.log('[ClientPool] Created client for:', identity.mxid);
    return client;
  }

  /**
   * Get client by identity ID
   */
  async getClientById(identityId: string): Promise<MatrixClient | undefined> {
    const identity = await this.storage.getIdentityAsync(identityId);
    if (!identity) {
      return undefined;
    }
    return await this.getClient(identity);
  }

  /**
   * Create Matrix client for identity
   */
  private async createClient(identity: MatrixIdentity): Promise<MatrixClient> {
    // Create storage provider for this client
    const storageProvider = new SimpleFsStorageProvider(
      path.join(this.storageDir, `${identity.id}.json`)
    );

    // Create client
    const client = new MatrixClient(
      this.homeserverUrl,
      identity.accessToken,
      storageProvider
    );

    // Enable auto-join for invited rooms
    AutojoinRoomsMixin.setupOnClient(client);

    // Start client
    await client.start();

    // Set display name and avatar if not already set
    try {
      const profile = await client.getUserProfile(identity.mxid);
      
      if (!profile.displayname || profile.displayname !== identity.displayName) {
        await client.setDisplayName(identity.displayName);
      }
      
      if (identity.avatarUrl && (!profile.avatar_url || profile.avatar_url !== identity.avatarUrl)) {
        await client.setAvatarUrl(identity.avatarUrl);
      }
    } catch (error) {
      console.error('[ClientPool] Error setting profile:', error);
    }

    return client;
  }

  /**
   * Remove client from pool (e.g., when identity is deleted)
   */
  async removeClient(identityId: string): Promise<void> {
    const client = this.clients.get(identityId);
    if (client) {
      try {
        await client.stop();
      } catch (error) {
        console.error('[ClientPool] Error stopping client:', error);
      }
      this.clients.delete(identityId);
      console.log('[ClientPool] Removed client:', identityId);
    }
  }

  /**
   * Get all active clients
   */
  getActiveClients(): Map<string, MatrixClient> {
    return new Map(this.clients);
  }

  /**
   * Stop all clients
   */
  async stopAll(): Promise<void> {
    console.log('[ClientPool] Stopping all clients...');
    const promises = Array.from(this.clients.values()).map(async (client) => {
      try {
        await client.stop();
      } catch (error) {
        console.error('[ClientPool] Error stopping client:', error);
      }
    });
    await Promise.all(promises);
    this.clients.clear();
    console.log('[ClientPool] All clients stopped');
  }

  /**
   * Restart client (e.g., after token refresh)
   */
  async restartClient(identityId: string): Promise<MatrixClient | undefined> {
    await this.removeClient(identityId);
    const identity = await this.storage.getIdentityAsync(identityId);
    if (!identity) {
      return undefined;
    }
    return await this.getClient(identity);
  }
}
