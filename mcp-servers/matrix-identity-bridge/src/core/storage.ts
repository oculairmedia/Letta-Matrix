/**
 * Storage Layer - JSON-based persistence for identities, DM mappings, and metadata
 */

import fs from 'fs/promises';
import path from 'path';
import type {
  MatrixIdentity,
  DMRoomMapping,
  StorageData,
  StorageMetadata
} from '../types/index.js';

export class Storage {
  private dataDir: string;
  private identitiesFile: string;
  private dmRoomsFile: string;
  private metadataFile: string;
  
  private identities: Map<string, MatrixIdentity> = new Map();
  private dmRooms: Map<string, DMRoomMapping> = new Map();
  private metadata: StorageMetadata = {
    version: 1,
    updatedAt: Date.now()
  };
  
  private initialized = false;

  constructor(dataDir: string = './data') {
    this.dataDir = dataDir;
    this.identitiesFile = path.join(dataDir, 'identities.json');
    this.dmRoomsFile = path.join(dataDir, 'dm_rooms.json');
    this.metadataFile = path.join(dataDir, 'metadata.json');
  }

  /**
   * Initialize storage - create data directory and load existing data
   */
  async initialize(): Promise<void> {
    if (this.initialized) return;

    // Ensure data directory exists
    await fs.mkdir(this.dataDir, { recursive: true });

    // Load existing data
    await this.loadIdentities();
    await this.loadDMRooms();
    await this.loadMetadata();

    this.initialized = true;
    console.log('[Storage] Initialized:', {
      identities: this.identities.size,
      dmRooms: this.dmRooms.size,
      version: this.metadata.version
    });
  }

  /**
   * Load identities from file
   */
  private async loadIdentities(): Promise<void> {
    try {
      const data = await fs.readFile(this.identitiesFile, 'utf-8');
      const parsed = JSON.parse(data) as Record<string, MatrixIdentity>;
      this.identities = new Map(Object.entries(parsed));
    } catch (error: any) {
      if (error.code === 'ENOENT') {
        console.log('[Storage] No existing identities file, starting fresh');
      } else {
        console.error('[Storage] Error loading identities:', error);
      }
    }
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
   * Save identities to file
   */
  private async saveIdentities(): Promise<void> {
    const data = Object.fromEntries(this.identities);
    await fs.writeFile(
      this.identitiesFile,
      JSON.stringify(data, null, 2),
      'utf-8'
    );
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

  /**
   * Get identity by ID
   */
  getIdentity(id: string): MatrixIdentity | undefined {
    return this.identities.get(id);
  }

  /**
   * Get identity by MXID
   */
  getIdentityByMXID(mxid: string): MatrixIdentity | undefined {
    return Array.from(this.identities.values()).find(i => i.mxid === mxid);
  }

  /**
   * Get all identities
   */
  getAllIdentities(): MatrixIdentity[] {
    return Array.from(this.identities.values());
  }

  /**
   * Save or update identity
   */
  async saveIdentity(identity: MatrixIdentity): Promise<void> {
    this.identities.set(identity.id, identity);
    await this.saveIdentities();
    console.log('[Storage] Saved identity:', identity.id, '->', identity.mxid);
  }

  /**
   * Delete identity
   */
  async deleteIdentity(id: string): Promise<boolean> {
    const deleted = this.identities.delete(id);
    if (deleted) {
      await this.saveIdentities();
      console.log('[Storage] Deleted identity:', id);
    }
    return deleted;
  }

  /**
   * Create DM room key from two MXIDs (alphabetically sorted)
   */
  private createDMKey(mxid1: string, mxid2: string): string {
    return [mxid1, mxid2].sort().join('<->');
  }

  /**
   * Get DM room for two users
   */
  getDMRoom(mxid1: string, mxid2: string): DMRoomMapping | undefined {
    const key = this.createDMKey(mxid1, mxid2);
    return this.dmRooms.get(key);
  }

  /**
   * Get all DM rooms
   */
  getAllDMRooms(): DMRoomMapping[] {
    return Array.from(this.dmRooms.values());
  }

  /**
   * Get DM rooms for a specific user
   */
  getDMRoomsForUser(mxid: string): DMRoomMapping[] {
    return this.getAllDMRooms().filter(dm => 
      dm.participants.includes(mxid)
    );
  }

  /**
   * Save or update DM room mapping
   */
  async saveDMRoom(mapping: DMRoomMapping): Promise<void> {
    this.dmRooms.set(mapping.key, mapping);
    await this.saveDMRooms();
    console.log('[Storage] Saved DM room:', mapping.key, '->', mapping.roomId);
  }

  /**
   * Delete DM room mapping
   */
  async deleteDMRoom(mxid1: string, mxid2: string): Promise<boolean> {
    const key = this.createDMKey(mxid1, mxid2);
    const deleted = this.dmRooms.delete(key);
    if (deleted) {
      await this.saveDMRooms();
      console.log('[Storage] Deleted DM room:', key);
    }
    return deleted;
  }

  /**
   * Update last activity timestamp for a DM room
   */
  async updateDMActivity(mxid1: string, mxid2: string): Promise<void> {
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
    return {
      identities: Object.fromEntries(this.identities),
      dmRooms: Object.fromEntries(this.dmRooms),
      metadata: this.metadata
    };
  }

  /**
   * Import data from backup
   */
  async importData(data: StorageData): Promise<void> {
    this.identities = new Map(Object.entries(data.identities));
    this.dmRooms = new Map(Object.entries(data.dmRooms));
    this.metadata = data.metadata;

    await this.saveIdentities();
    await this.saveDMRooms();
    await this.saveMetadata();

    console.log('[Storage] Imported data:', {
      identities: this.identities.size,
      dmRooms: this.dmRooms.size
    });
  }

  /**
   * Clear all data (use with caution!)
   */
  async clearAll(): Promise<void> {
    this.identities.clear();
    this.dmRooms.clear();
    
    await this.saveIdentities();
    await this.saveDMRooms();
    
    console.log('[Storage] Cleared all data');
  }
}
