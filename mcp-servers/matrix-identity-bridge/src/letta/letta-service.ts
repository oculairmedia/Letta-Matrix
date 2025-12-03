/**
 * Letta Integration Service
 * Uses the official @letta-ai/letta-client SDK
 */

import { Letta } from '@letta-ai/letta-client';
import type { AgentState } from '@letta-ai/letta-client/resources/agents/agents.js';
import type { Storage } from '../core/storage.js';
import { IdentityManager } from '../core/identity-manager.js';

export interface LettaConfig {
  baseUrl: string;
  apiKey?: string;
}

export interface LettaAgentInfo {
  id: string;
  name: string;
  description?: string;
  createdAt?: string;
  model?: string;
}

export class LettaService {
  private client: Letta;
  private storage: Storage;
  private identityManager: IdentityManager;
  private agentCache: Map<string, LettaAgentInfo> = new Map();

  constructor(
    config: LettaConfig,
    storage: Storage,
    identityManager: IdentityManager
  ) {
    this.client = new Letta({
      baseURL: config.baseUrl,
      apiKey: config.apiKey || null
    });
    this.storage = storage;
    this.identityManager = identityManager;
  }

  /**
   * Get agent information from Letta API
   */
  async getAgent(agentId: string): Promise<LettaAgentInfo | undefined> {
    // Check cache first
    if (this.agentCache.has(agentId)) {
      return this.agentCache.get(agentId);
    }

    try {
      const agent = await this.client.agents.retrieve(agentId);
      const info: LettaAgentInfo = {
        id: agent.id,
        name: agent.name,
        description: agent.description || undefined,
        createdAt: agent.created_at || undefined,
        model: agent.llm_config?.model || undefined
      };
      this.agentCache.set(agentId, info);
      return info;
    } catch (error) {
      console.error('[LettaService] Failed to get agent:', error);
      return undefined;
    }
  }

  /**
   * List all agents
   */
  async listAgents(): Promise<LettaAgentInfo[]> {
    try {
      const response = await this.client.agents.list();
      const agents: LettaAgentInfo[] = [];
      
      for await (const agent of response) {
        const info: LettaAgentInfo = {
          id: agent.id,
          name: agent.name,
          description: agent.description || undefined,
          createdAt: agent.created_at || undefined,
          model: agent.llm_config?.model || undefined
        };
        agents.push(info);
        this.agentCache.set(agent.id, info);
      }
      
      return agents;
    } catch (error) {
      console.error('[LettaService] Failed to list agents:', error);
      return [];
    }
  }

  /**
   * Send a message to a Letta agent
   */
  async sendMessage(agentId: string, message: string): Promise<{
    messages: Array<{ role: string; content: string }>;
    usage?: { total_tokens?: number };
  }> {
    try {
      const response = await this.client.agents.messages.create(agentId, {
        messages: [{ role: 'user', content: message }]
      });

      // Extract assistant messages from response
      const messages: Array<{ role: string; content: string }> = [];
      
      if (response && typeof response === 'object' && 'messages' in response) {
        const respMessages = (response as { messages: Array<{ message_type?: string; content?: string }> }).messages;
        for (const msg of respMessages) {
          if (msg.message_type === 'assistant_message' && msg.content) {
            messages.push({ role: 'assistant', content: msg.content });
          }
        }
      }

      return { messages };
    } catch (error) {
      console.error('[LettaService] Failed to send message:', error);
      throw error;
    }
  }

  /**
   * Get or create Matrix identity for a Letta agent
   */
  async getOrCreateAgentIdentity(agentId: string): Promise<string> {
    const identityId = IdentityManager.generateLettaId(agentId);
    
    // Check if identity already exists
    const existing = this.storage.getIdentity(identityId);
    if (existing) {
      return identityId;
    }

    // Get agent info from Letta
    const agent = await this.getAgent(agentId);
    if (!agent) {
      throw new Error(`Letta agent not found: ${agentId}`);
    }

    // Provision Matrix identity
    const localpart = IdentityManager.generateLettaLocalpart(agentId);
    await this.identityManager.getOrCreateIdentity({
      id: identityId,
      localpart,
      displayName: agent.name,
      type: 'letta'
    });

    console.log('[LettaService] Created Matrix identity for agent:', agentId, '->', identityId);
    return identityId;
  }

  /**
   * Get all Letta agent Matrix identities
   */
  async getLettaIdentities(): Promise<Array<{
    identityId: string;
    agentId: string;
    mxid: string;
    agentName?: string;
  }>> {
    const identities = await this.identityManager.listIdentities('letta');
    const result: Array<{
      identityId: string;
      agentId: string;
      mxid: string;
      agentName?: string;
    }> = [];

    for (const identity of identities) {
      // Extract agent ID from identity ID (letta_<agent_id>)
      const agentId = identity.id.replace(/^letta_/, '');
      const agent = await this.getAgent(agentId);
      
      result.push({
        identityId: identity.id,
        agentId,
        mxid: identity.mxid,
        agentName: agent?.name
      });
    }

    return result;
  }

  /**
   * Get the Letta client for direct API access
   */
  getClient(): Letta {
    return this.client;
  }
}
