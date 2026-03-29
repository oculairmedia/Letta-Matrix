import fs from 'fs/promises';
import path from 'path';
import type {
  MatrixIdentity,
  DMRoomMapping,
  StorageData,
  StorageMetadata
} from '../types/index.js';
import { IdentityApiClient } from './identity-api-client.js';
import { DMRoomApiClient } from './dm-room-api-client.js';

export class Storage {
  private dataDir: string;
  private dmRoomsFile: string;
  private metadataFile: string;
  
  private dmRooms: Map<string, DMRoomMapping> = new Map();
  private metadata: StorageMetadata = {
    version: 1,
    updatedAt: Date.now()
  };
  
  private initialized = false;
  private useDMRoomApi: boolean;
  private identityApiClient: IdentityApiClient;
  private dmRoomApiClient: DMRoomApiClient | null = null;

  constructor(dataDir: string = './data') {
    this.dataDir = dataDir;
    this.dmRoomsFile = path.join(dataDir, 'dm_rooms.json');
    this.metadataFile = path.join(dataDir, 'metadata.json');

    if (process.env.USE_IDENTITY_API !== 'true') {
      throw new Error('[Storage] USE_IDENTITY_API must be true; identities.json fallback has been removed');
    }

    this.identityApiClient = new IdentityApiClient();
    console.log('[Storage] Using Python Identity API for identity operations');

    this.useDMRoomApi = process.env.USE_DM_ROOM_API === 'true';

    if (this.useDMRoomApi) {
      this.dmRoomApiClient = new DMRoomApiClient();
      console.log('[Storage] Using Python DM Room API for DM room operations');
    }
  }

  /**
   * Initialize storage - create data directory and load existing data
   */
  async initialize(): Promise<void> {
    if (this.initialized) return;

    // Ensure data directory exists
    await fs.mkdir(this.dataDir, { recursive: true });

    // Load existing data
    await this.loadDMRooms();
    await this.loadMetadata();

    const identityCount = (await this.getAllIdentitiesAsync()).length;

    this.initialized = true;
    console.log('[Storage] Initialized:', {
      identities: identityCount,
      dmRooms: this.dmRooms.size,
      version: this.metadata.version
    });
  }

  /**
   * Load DM room mappings from file
   */
  private async loadDMRooms(): Promise<void> {
    try {
      const data = await fs.readFile(this.dmRoomsFile, 'utf-8');
      const parsed = JSON.parse(data) as Record<string, DMRoomMapping>;
      this.dmRooms = new Map(Object.entries(parsed));
    } catch (error: any) {
      if (error.code === 'ENOENT') {
        console.log('[Storage] No existing DM rooms file, starting fresh');
      } else {
        console.error('[Storage] Error loading DM rooms:', error);
      }
    }
  }

  /**
   * Load metadata from file
   */
  private async loadMetadata(): Promise<void> {
    try {
      const data = await fs.readFile(this.metadataFile, 'utf-8');
      this.metadata = JSON.parse(data) as StorageMetadata;
    } catch (error: any) {
      if (error.code === 'ENOENT') {
        console.log('[Storage] No existing metadata file, using defaults');
        await this.saveMetadata();
      } else {
        console.error('[Storage] Error loading metadata:', error);
      }
    }
  }

  /**
   * Save DM room mappings to file
   */
  private async saveDMRooms(): Promise<void> {
    const data = Object.fromEntries(this.dmRooms);
    await fs.writeFile(
      this.dmRoomsFile,
      JSON.stringify(data, null, 2),
      'utf-8'
    );
  }

  /**
   * Save metadata to file
   */
  private async saveMetadata(): Promise<void> {
    this.metadata.updatedAt = Date.now();
    await fs.writeFile(
      this.metadataFile,
      JSON.stringify(this.metadata, null, 2),
      'utf-8'
    );
  }

  getIdentity(id: string): MatrixIdentity | undefined {
    console.warn('[Storage] Sync getIdentity is not supported; use getIdentityAsync');
    return undefined;
  }

  async getIdentityAsync(id: string): Promise<MatrixIdentity | undefined> {
    return await this.identityApiClient.getIdentity(id);
  }

  async getIdentityByMXIDAsync(mxid: string): Promise<MatrixIdentity | undefined> {
    return await this.identityApiClient.getIdentityByMXID(mxid);
  }

  getAllIdentities(): MatrixIdentity[] {
    console.warn('[Storage] Sync getAllIdentities is not supported; use getAllIdentitiesAsync');
    return [];
  }

  async getAllIdentitiesAsync(type?: string): Promise<MatrixIdentity[]> {
    return await this.identityApiClient.getAllIdentities(type);
  }

  async saveIdentity(identity: MatrixIdentity): Promise<void> {
    const saved = await this.identityApiClient.saveIdentity(identity);
    if (!saved) {
      throw new Error(`[Storage] Failed to save identity via API: ${identity.id}`);
    }
    console.log('[Storage] Saved identity via API:', identity.id);
  }

  async deleteIdentity(id: string): Promise<boolean> {
    const deleted = await this.identityApiClient.deleteIdentity(id);
    if (deleted) {
      console.log('[Storage] Deleted identity via API:', id);
    }
    return deleted;
  }

  /**
   * Create DM room key from two MXIDs (alphabetically sorted)
   */
  private createDMKey(mxid1: string, mxid2: string): string {
    return [mxid1, mxid2].sort().join('<->');
  }

  getDMRoom(mxid1: string, mxid2: string): DMRoomMapping | undefined {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      console.warn('[Storage] Sync getDMRoom called with API mode - use getDMRoomAsync');
      return undefined;
    }
    const key = this.createDMKey(mxid1, mxid2);
    return this.dmRooms.get(key);
  }

  async getDMRoomAsync(mxid1: string, mxid2: string): Promise<DMRoomMapping | undefined> {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      return await this.dmRoomApiClient.getDMRoom(mxid1, mxid2);
    }
    const key = this.createDMKey(mxid1, mxid2);
    return this.dmRooms.get(key);
  }

  getAllDMRooms(): DMRoomMapping[] {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      console.warn('[Storage] Sync getAllDMRooms called with API mode - use getAllDMRoomsAsync');
      return [];
    }
    return Array.from(this.dmRooms.values());
  }

  async getAllDMRoomsAsync(): Promise<DMRoomMapping[]> {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      return await this.dmRoomApiClient.getAllDMRooms();
    }
    return Array.from(this.dmRooms.values());
  }

  getDMRoomsForUser(mxid: string): DMRoomMapping[] {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      console.warn('[Storage] Sync getDMRoomsForUser called with API mode - use getDMRoomsForUserAsync');
      return [];
    }
    return this.getAllDMRooms().filter(dm => 
      dm.participants.includes(mxid)
    );
  }

  async getDMRoomsForUserAsync(mxid: string): Promise<DMRoomMapping[]> {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      return await this.dmRoomApiClient.getDMRoomsForUser(mxid);
    }
    return this.getAllDMRooms().filter(dm => 
      dm.participants.includes(mxid)
    );
  }

  async saveDMRoom(mapping: DMRoomMapping): Promise<void> {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      await this.dmRoomApiClient.saveDMRoom(mapping);
      console.log('[Storage] Saved DM room via API:', mapping.key, '->', mapping.roomId);
      return;
    }
    this.dmRooms.set(mapping.key, mapping);
    await this.saveDMRooms();
    console.log('[Storage] Saved DM room:', mapping.key, '->', mapping.roomId);
  }

  async deleteDMRoom(mxid1: string, mxid2: string): Promise<boolean> {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      console.log('[Storage] Delete DM room via API not implemented');
      return false;
    }
    const key = this.createDMKey(mxid1, mxid2);
    const deleted = this.dmRooms.delete(key);
    if (deleted) {
      await this.saveDMRooms();
      console.log('[Storage] Deleted DM room:', key);
    }
    return deleted;
  }

  async updateDMActivity(mxid1: string, mxid2: string): Promise<void> {
    if (this.useDMRoomApi && this.dmRoomApiClient) {
      await this.dmRoomApiClient.updateActivity(mxid1, mxid2);
      return;
    }
    const key = this.createDMKey(mxid1, mxid2);
    const mapping = this.dmRooms.get(key);
    if (mapping) {
      mapping.lastActivityAt = Date.now();
      await this.saveDMRooms();
    }
  }

  /**
   * Get storage metadata
   */
  getMetadata(): StorageMetadata {
    return { ...this.metadata };
  }

  /**
   * Export all data for backup
   */
  async exportData(): Promise<StorageData> {
    const identities = await this.getAllIdentitiesAsync();
    return {
      identities: Object.fromEntries(identities.map(identity => [identity.id, identity])),
      dmRooms: Object.fromEntries(this.dmRooms),
      metadata: this.metadata
    };
  }

  /**
   * Import data from backup
   */
  async importData(data: StorageData): Promise<void> {
    const identityEntries = Object.values(data.identities);
    for (const identity of identityEntries) {
      await this.saveIdentity(identity);
    }

    this.dmRooms = new Map(Object.entries(data.dmRooms));
    this.metadata = data.metadata;

    await this.saveDMRooms();
    await this.saveMetadata();

    console.log('[Storage] Imported data:', {
      identities: identityEntries.length,
      dmRooms: this.dmRooms.size
    });
  }

  /**
   * Clear all data (use with caution!)
   */
  async clearAll(): Promise<void> {
    const identities = await this.getAllIdentitiesAsync();
    for (const identity of identities) {
      await this.deleteIdentity(identity.id);
    }

    this.dmRooms.clear();

    await this.saveDMRooms();
    
    console.log('[Storage] Cleared all data');
  }
}
