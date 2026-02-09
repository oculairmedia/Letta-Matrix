import { BridgeApiClient } from '../helpers/bridge-api';
import { loadConfig } from '../helpers/config';

describe('Bridge health contract', () => {
  const config = loadConfig();
  const bridge = new BridgeApiClient(config.bridgeApiUrl);

  it('returns status=healthy and includes authenticated,timestamp,agent_sync_available on GET /health', async () => {
    const result = await bridge.health();

    expect(result.status).toBe('healthy');
    expect(typeof result.authenticated).toBe('boolean');
    expect(typeof result.agent_sync_available).toBe('boolean');
    expect(new Date(result.timestamp).toString()).not.toBe('Invalid Date');
  });

  it('returns provisioning metrics and status threshold alignment on GET /health/agent-provisioning', async () => {
    const result = await bridge.agentProvisioningHealth();

    expect(['healthy', 'degraded', 'unhealthy', 'error', 'unavailable']).toContain(result.status);
    expect(new Date(result.timestamp).toString()).not.toBe('Invalid Date');

    if (result.status === 'healthy' || result.status === 'degraded' || result.status === 'unhealthy') {
      expect(typeof result.total_agents).toBe('number');
      expect(typeof result.agents_with_rooms).toBe('number');
      expect(typeof result.missing_count).toBe('number');
      expect(Array.isArray(result.agents_missing_rooms)).toBe(true);

      if (result.status === 'healthy') {
        expect(result.missing_count).toBe(0);
      }
      if (result.status === 'degraded') {
        expect(result.missing_count).toBeGreaterThanOrEqual(1);
        expect(result.missing_count).toBeLessThanOrEqual(3);
      }
      if (result.status === 'unhealthy') {
        expect(result.missing_count).toBeGreaterThanOrEqual(4);
      }
    }
  });

  it('returns success=true and non-empty mapping set on GET /agents/mappings', async () => {
    const result = await bridge.getMappings();

    expect(result.success).toBe(true);
    expect(typeof result.message).toBe('string');
    expect(Object.keys(result.mappings).length).toBeGreaterThan(0);
  });
});
