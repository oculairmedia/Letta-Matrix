/**
 * Tests for Identity Manager static methods
 */
import { describe, it, expect } from '@jest/globals';
import { IdentityManager } from '../src/core/identity-manager.js';

describe('IdentityManager', () => {
  describe('Letta ID Generation', () => {
    it('should generate consistent IDs from agent IDs', () => {
      const agentId = '597b5756-2915-4560-ba6b-91005f085166';
      
      const id1 = IdentityManager.generateLettaId(agentId);
      const id2 = IdentityManager.generateLettaId(agentId);
      
      expect(id1).toBe(id2);
    });

    it('should generate IDs with letta prefix', () => {
      const id = IdentityManager.generateLettaId('agent-123');
      expect(id).toContain('letta_');
    });

    it('should generate different IDs for different agents', () => {
      const id1 = IdentityManager.generateLettaId('agent-1');
      const id2 = IdentityManager.generateLettaId('agent-2');
      
      expect(id1).not.toBe(id2);
    });
  });

  describe('OpenCode ID Generation', () => {
    it('should generate consistent IDs from directories', () => {
      const directory = '/opt/stacks/matrix-synapse-deployment';
      
      const id1 = IdentityManager.generateOpenCodeId(directory);
      const id2 = IdentityManager.generateOpenCodeId(directory);
      
      expect(id1).toBe(id2);
    });

    it('should generate IDs with opencode prefix', () => {
      const id = IdentityManager.generateOpenCodeId('/opt/project');
      expect(id).toContain('opencode_');
    });

    it('should generate different IDs for different directories', () => {
      const id1 = IdentityManager.generateOpenCodeId('/opt/project-a');
      const id2 = IdentityManager.generateOpenCodeId('/opt/project-b');
      
      expect(id1).not.toBe(id2);
    });
  });

  describe('Letta Localpart Generation', () => {
    it('should generate valid Matrix localparts', () => {
      const agentId = '597b5756-2915-4560-ba6b-91005f085166';
      const localpart = IdentityManager.generateLettaLocalpart(agentId);
      
      // Matrix localparts must match [a-z0-9._=-]+
      expect(localpart).toMatch(/^[a-z0-9._=-]+$/);
    });

    it('should use agent_ prefix to match matrix-client format', () => {
      // CRITICAL: This format MUST match what matrix-client's user_manager.py generates
      // The Python code uses: f"agent_{clean_id}" where clean_id has hyphens replaced with underscores
      const localpart = IdentityManager.generateLettaLocalpart('agent-test-123');
      expect(localpart).toMatch(/^agent_/);
      expect(localpart).not.toContain('letta_'); // Must NOT use letta_ prefix
    });

    it('should be consistent', () => {
      const agentId = 'test-agent-123';
      const lp1 = IdentityManager.generateLettaLocalpart(agentId);
      const lp2 = IdentityManager.generateLettaLocalpart(agentId);
      expect(lp1).toBe(lp2);
    });

    it('should match matrix-client format exactly for real agent IDs', () => {
      // This test ensures identity-bridge creates the SAME Matrix username
      // as matrix-client (Python), preventing duplicate user creation.
      // 
      // Python user_manager.py logic:
      //   1. Remove 'agent-' prefix if present
      //   2. Replace hyphens with underscores
      //   3. Prefix with 'agent_'
      
      const testCases = [
        { agentId: 'agent-597b5756-2915-4560-ba6b-91005f085166', expected: 'agent_597b5756_2915_4560_ba6b_91005f085166' },
        { agentId: 'agent-0a0867cb-09a4-4a9d-ad97-884773b7cbbc', expected: 'agent_0a0867cb_09a4_4a9d_ad97_884773b7cbbc' },
        { agentId: '12345678-abcd-efgh-ijkl-mnopqrstuvwx', expected: 'agent_12345678_abcd_efgh_ijkl_mnopqrstuvwx' },
      ];

      for (const { agentId, expected } of testCases) {
        const localpart = IdentityManager.generateLettaLocalpart(agentId);
        expect(localpart).toBe(expected);
      }
    });

    it('should strip agent- prefix before processing', () => {
      // With agent- prefix
      const withPrefix = IdentityManager.generateLettaLocalpart('agent-abc-123');
      // Without agent- prefix (should produce same result)
      const withoutPrefix = IdentityManager.generateLettaLocalpart('abc-123');
      
      expect(withPrefix).toBe('agent_abc_123');
      expect(withoutPrefix).toBe('agent_abc_123');
    });
  });

  describe('OpenCode Localpart Generation', () => {
    it('should generate valid Matrix localparts', () => {
      const directory = '/opt/stacks/matrix-synapse-deployment';
      const localpart = IdentityManager.generateOpenCodeLocalpart(directory);
      
      // Matrix localparts must match [a-z0-9._=-]+
      expect(localpart).toMatch(/^[a-z0-9._=-]+$/);
    });

    it('should include oc prefix', () => {
      const localpart = IdentityManager.generateOpenCodeLocalpart('/opt/test');
      expect(localpart).toContain('oc_');
    });

    it('should be consistent', () => {
      const directory = '/opt/test-project';
      const lp1 = IdentityManager.generateOpenCodeLocalpart(directory);
      const lp2 = IdentityManager.generateOpenCodeLocalpart(directory);
      expect(lp1).toBe(lp2);
    });

    it('should handle directories with special characters', () => {
      const directory = '/opt/stacks/my-project_v2.0';
      const localpart = IdentityManager.generateOpenCodeLocalpart(directory);
      
      // Should still produce valid localpart
      expect(localpart).toMatch(/^[a-z0-9._=-]+$/);
    });
  });
});
