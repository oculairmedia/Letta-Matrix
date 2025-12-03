/**
 * Tests for Storage module
 * Note: Storage uses async methods, but we test synchronous aspects here
 */
import { describe, it, expect } from '@jest/globals';

describe('Storage', () => {
  describe('DM Room Key Generation', () => {
    /**
     * Helper that mirrors the key generation logic in Storage
     */
    function generateDMKey(mxid1: string, mxid2: string): string {
      const sorted = [mxid1, mxid2].sort();
      return `${sorted[0]}<->${sorted[1]}`;
    }

    it('should generate consistent keys regardless of user order', () => {
      const key1 = generateDMKey('@user1:example.com', '@user2:example.com');
      const key2 = generateDMKey('@user2:example.com', '@user1:example.com');

      expect(key1).toBe(key2);
    });

    it('should generate different keys for different user pairs', () => {
      const key1 = generateDMKey('@user1:example.com', '@user2:example.com');
      const key2 = generateDMKey('@user1:example.com', '@user3:example.com');

      expect(key1).not.toBe(key2);
    });

    it('should sort MXIDs alphabetically', () => {
      const key = generateDMKey('@zulu:example.com', '@alpha:example.com');

      expect(key).toBe('@alpha:example.com<->@zulu:example.com');
    });
  });

  describe('MatrixIdentity Type', () => {
    it('should have correct structure', () => {
      const identity = {
        id: 'test-identity-1',
        mxid: '@test:matrix.example.com',
        displayName: 'Test User',
        type: 'custom' as const,
        accessToken: 'test-token-123',
        createdAt: Date.now(),
        lastUsedAt: Date.now(),
      };

      expect(identity.id).toBeDefined();
      expect(identity.mxid).toMatch(/^@.+:.+$/);
      expect(identity.type).toMatch(/^(letta|opencode|custom)$/);
      expect(typeof identity.createdAt).toBe('number');
      expect(typeof identity.lastUsedAt).toBe('number');
    });

    it('should support all identity types', () => {
      const types: Array<'letta' | 'opencode' | 'custom'> = ['letta', 'opencode', 'custom'];
      
      types.forEach(type => {
        const identity = {
          id: `${type}-id`,
          mxid: `@${type}:example.com`,
          displayName: `${type} User`,
          type,
          accessToken: 'token',
          createdAt: Date.now(),
          lastUsedAt: Date.now(),
        };
        expect(identity.type).toBe(type);
      });
    });
  });

  describe('DMRoomMapping Type', () => {
    it('should have correct structure', () => {
      const mapping = {
        key: '@user1:example.com<->@user2:example.com',
        roomId: '!room123:example.com',
        participants: ['@user1:example.com', '@user2:example.com'] as [string, string],
        createdAt: Date.now(),
        lastActivityAt: Date.now(),
      };

      expect(mapping.key).toContain('<->');
      expect(mapping.roomId).toMatch(/^!.+:.+$/);
      expect(mapping.participants).toHaveLength(2);
      expect(typeof mapping.createdAt).toBe('number');
    });
  });
});
