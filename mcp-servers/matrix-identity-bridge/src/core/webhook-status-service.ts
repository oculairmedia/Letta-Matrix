/**
 * Webhook Status Service
 * 
 * Tracks which agents have webhooks enabled and coordinates between
 * webhook-first delivery and polling fallback.
 * 
 * Phase 3: MXSYN-95 - Webhook-first architecture
 */

import type { Letta } from '@letta-ai/letta-client';

export interface AgentWebhookStatus {
  agentId: string;
  webhookEnabled: boolean;
  webhookUrl?: string;
  checkedAt: number;
}

export interface WebhookStatusConfig {
  /** Letta API base URL */
  lettaApiUrl?: string;
  /** Letta API token */
  lettaApiToken?: string;
  /** Cache TTL in milliseconds (default: 5 minutes) */
  cacheTtlMs?: number;
  /** Our webhook URL to match against */
  ourWebhookUrl?: string;
}

export class WebhookStatusService {
  private lettaClient?: Letta;
  private config: Required<WebhookStatusConfig>;
  private statusCache: Map<string, AgentWebhookStatus> = new Map();

  constructor(lettaClient?: Letta, config?: WebhookStatusConfig) {
    this.lettaClient = lettaClient;
    this.config = {
      lettaApiUrl: config?.lettaApiUrl || process.env.LETTA_BASE_URL || 'http://192.168.50.90:8283',
      lettaApiToken: config?.lettaApiToken || process.env.LETTA_API_TOKEN || 'lettaSecurePass123',
      cacheTtlMs: config?.cacheTtlMs ?? 5 * 60 * 1000, // 5 minutes
      ourWebhookUrl: config?.ourWebhookUrl || process.env.LETTA_WEBHOOK_URL || 'http://192.168.50.90:3101/webhooks/letta/agent-response'
    };
  }

  /**
   * Check if an agent has webhooks enabled pointing to our endpoint
   */
  async isWebhookEnabled(agentId: string): Promise<boolean> {
    // Check cache first
    const cached = this.statusCache.get(agentId);
    if (cached && Date.now() - cached.checkedAt < this.config.cacheTtlMs) {
      return cached.webhookEnabled;
    }

    // Query Letta API for webhook config
    try {
      const response = await fetch(
        `${this.config.lettaApiUrl}/v1/agents/${agentId}/webhook`,
        {
          headers: {
            'Authorization': `Bearer ${this.config.lettaApiToken}`,
            'Content-Type': 'application/json'
          }
        }
      );

      if (!response.ok) {
        console.warn(`[WebhookStatus] Failed to get webhook config for ${agentId}: ${response.status}`);
        // Cache negative result briefly
        this.cacheStatus(agentId, false);
        return false;
      }

      const data = await response.json() as {
        url?: string;
        enabled?: boolean;
        events?: string[];
      };

      // Check if webhook is enabled and pointing to our endpoint
      const webhookEnabled = Boolean(
        data.enabled &&
        data.url &&
        data.url === this.config.ourWebhookUrl
      );

      // Cache the result
      this.cacheStatus(agentId, webhookEnabled, data.url);

      console.log(`[WebhookStatus] Agent ${agentId}: webhook ${webhookEnabled ? 'enabled' : 'disabled'}`);
      return webhookEnabled;

    } catch (error) {
      console.error(`[WebhookStatus] Error checking webhook for ${agentId}:`, error);
      // Cache negative result briefly on error
      this.cacheStatus(agentId, false);
      return false;
    }
  }

  /**
   * Get cached status for an agent (without API call)
   */
  getCachedStatus(agentId: string): AgentWebhookStatus | undefined {
    return this.statusCache.get(agentId);
  }

  /**
   * Invalidate cache for an agent (e.g., when webhook config changes)
   */
  invalidateCache(agentId: string): void {
    this.statusCache.delete(agentId);
  }

  /**
   * Clear all cached statuses
   */
  clearCache(): void {
    this.statusCache.clear();
  }

  /**
   * Get statistics about cached statuses
   */
  getStats(): {
    cached: number;
    webhookEnabled: number;
    webhookDisabled: number;
  } {
    let webhookEnabled = 0;
    let webhookDisabled = 0;

    for (const status of this.statusCache.values()) {
      if (status.webhookEnabled) {
        webhookEnabled++;
      } else {
        webhookDisabled++;
      }
    }

    return {
      cached: this.statusCache.size,
      webhookEnabled,
      webhookDisabled
    };
  }

  /**
   * Cache a webhook status
   */
  private cacheStatus(agentId: string, enabled: boolean, url?: string): void {
    this.statusCache.set(agentId, {
      agentId,
      webhookEnabled: enabled,
      webhookUrl: url,
      checkedAt: Date.now()
    });
  }
}

// Singleton instance
let serviceInstance: WebhookStatusService | undefined;

export function getWebhookStatusService(): WebhookStatusService | undefined {
  return serviceInstance;
}

export function initializeWebhookStatusService(
  lettaClient?: Letta,
  config?: WebhookStatusConfig
): WebhookStatusService {
  serviceInstance = new WebhookStatusService(lettaClient, config);
  console.log('[WebhookStatus] Service initialized');
  return serviceInstance;
}
