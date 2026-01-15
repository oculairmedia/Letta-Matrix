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
  private agentNameIndex: Map<string, string> = new Map(); // lowercase name -> agent_id
  private lastIndexRefresh: number = 0;
  private readonly INDEX_TTL = 60000; // Refresh name index every 60 seconds

  constructor(
    config: LettaConfig,
    storage: Storage,
    identityManager: IdentityManager
  ) {
    this.client = new Letta({
      baseURL: config.baseUrl,
      apiKey: config.apiKey || null,
      timeout: 3600000 // 1 hour timeout for long-running agent requests
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
        // Update name index
        this.agentNameIndex.set(agent.name.toLowerCase(), agent.id);
      }
      
      this.lastIndexRefresh = Date.now();
      return agents;
    } catch (error) {
      console.error('[LettaService] Failed to list agents:', error);
      return [];
    }
  }

  /**
   * Resolve agent name to agent_id with fuzzy matching
   * Supports exact match, case-insensitive match, and partial match
   */
  async resolveAgentName(nameOrId: string): Promise<{
    agent_id: string;
    agent_name: string;
    match_type: 'exact_id' | 'exact_name' | 'case_insensitive' | 'partial' | 'fuzzy';
    confidence: number;
  } | null> {
    // First check if it's already a valid UUID (agent_id)
    const uuidRegex = /^agent-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (uuidRegex.test(nameOrId)) {
      const agent = await this.getAgent(nameOrId);
      if (agent) {
        return { agent_id: agent.id, agent_name: agent.name, match_type: 'exact_id', confidence: 1.0 };
      }
    }

    // Refresh name index if stale
    if (Date.now() - this.lastIndexRefresh > this.INDEX_TTL) {
      await this.listAgents();
    }

    const searchLower = nameOrId.toLowerCase().trim();
    const agents = Array.from(this.agentCache.values());

    // 1. Exact name match (case-sensitive)
    for (const agent of agents) {
      if (agent.name === nameOrId) {
        return { agent_id: agent.id, agent_name: agent.name, match_type: 'exact_name', confidence: 1.0 };
      }
    }

    // 2. Case-insensitive exact match
    for (const agent of agents) {
      if (agent.name.toLowerCase() === searchLower) {
        return { agent_id: agent.id, agent_name: agent.name, match_type: 'case_insensitive', confidence: 0.95 };
      }
    }

    // 3. Partial match (name contains search or search contains name)
    const partialMatches: Array<{ agent: LettaAgentInfo; score: number }> = [];
    for (const agent of agents) {
      const nameLower = agent.name.toLowerCase();
      if (nameLower.includes(searchLower) || searchLower.includes(nameLower)) {
        // Score based on how much of the name matches
        const score = Math.min(searchLower.length, nameLower.length) / Math.max(searchLower.length, nameLower.length);
        partialMatches.push({ agent, score });
      }
    }
    if (partialMatches.length > 0) {
      partialMatches.sort((a, b) => b.score - a.score);
      const best = partialMatches[0];
      if (best.score > 0.5) {
        return { agent_id: best.agent.id, agent_name: best.agent.name, match_type: 'partial', confidence: best.score * 0.9 };
      }
    }

    // 4. Fuzzy match using Levenshtein distance
    const fuzzyMatches: Array<{ agent: LettaAgentInfo; distance: number }> = [];
    for (const agent of agents) {
      const distance = this.levenshteinDistance(searchLower, agent.name.toLowerCase());
      const maxLen = Math.max(searchLower.length, agent.name.length);
      const similarity = 1 - (distance / maxLen);
      if (similarity > 0.6) { // At least 60% similar
        fuzzyMatches.push({ agent, distance });
      }
    }
    if (fuzzyMatches.length > 0) {
      fuzzyMatches.sort((a, b) => a.distance - b.distance);
      const best = fuzzyMatches[0];
      const maxLen = Math.max(searchLower.length, best.agent.name.length);
      const confidence = (1 - (best.distance / maxLen)) * 0.8;
      return { agent_id: best.agent.id, agent_name: best.agent.name, match_type: 'fuzzy', confidence };
    }

    return null;
  }

  /**
   * Get suggestions for similar agent names (for error messages)
   */
  async getSuggestions(searchTerm: string, maxSuggestions: number = 3): Promise<string[]> {
    // Refresh name index if stale
    if (Date.now() - this.lastIndexRefresh > this.INDEX_TTL) {
      await this.listAgents();
    }

    const searchLower = searchTerm.toLowerCase();
    const agents = Array.from(this.agentCache.values());
    
    const scored = agents.map(agent => {
      const nameLower = agent.name.toLowerCase();
      const distance = this.levenshteinDistance(searchLower, nameLower);
      const maxLen = Math.max(searchLower.length, nameLower.length);
      return { name: agent.name, id: agent.id, similarity: 1 - (distance / maxLen) };
    });

    scored.sort((a, b) => b.similarity - a.similarity);
    return scored.slice(0, maxSuggestions).map(s => `${s.name} (${s.id})`);
  }

  /**
   * Levenshtein distance for fuzzy matching
   */
  private levenshteinDistance(a: string, b: string): number {
    const matrix: number[][] = [];
    for (let i = 0; i <= b.length; i++) {
      matrix[i] = [i];
    }
    for (let j = 0; j <= a.length; j++) {
      matrix[0][j] = j;
    }
    for (let i = 1; i <= b.length; i++) {
      for (let j = 1; j <= a.length; j++) {
        if (b.charAt(i - 1) === a.charAt(j - 1)) {
          matrix[i][j] = matrix[i - 1][j - 1];
        } else {
          matrix[i][j] = Math.min(
            matrix[i - 1][j - 1] + 1,
            matrix[i][j - 1] + 1,
            matrix[i - 1][j] + 1
          );
        }
      }
    }
    return matrix[b.length][a.length];
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
    const existing = await this.storage.getIdentityAsync(identityId);
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

  /**
   * Ensure an agent has the matrix_messaging tool attached.
   * This enables the agent to respond via Matrix.
   */
  async ensureMatrixToolAttached(agentId: string): Promise<{
    attached: boolean;
    alreadyHad: boolean;
    toolId?: string;
    error?: string;
  }> {
    const MATRIX_MESSAGING_TOOL_ID = 'tool-e2b73220-6df7-44cb-bb37-d791e6ac6208';
    const MATRIX_MESSAGING_TOOL_NAME = 'matrix_messaging';

    try {
      // First, check if the agent already has the tool
      // The SDK returns a paginated result, need to iterate through it
      const agentToolsPage = this.client.agents.tools.list(agentId);
      let hasMatrixTool = false;
      
      for await (const tool of agentToolsPage) {
        if (tool.id === MATRIX_MESSAGING_TOOL_ID || tool.name === MATRIX_MESSAGING_TOOL_NAME) {
          hasMatrixTool = true;
          break;
        }
      }

      if (hasMatrixTool) {
        console.log(`[LettaService] Agent ${agentId} already has matrix_messaging tool`);
        return { attached: true, alreadyHad: true, toolId: MATRIX_MESSAGING_TOOL_ID };
      }

      // Attach the tool - SDK signature is attach(toolId, {agent_id})
      console.log(`[LettaService] Attaching matrix_messaging tool to agent ${agentId}`);
      await this.client.agents.tools.attach(MATRIX_MESSAGING_TOOL_ID, { agent_id: agentId });
      
      console.log(`[LettaService] Successfully attached matrix_messaging tool to agent ${agentId}`);
      return { attached: true, alreadyHad: false, toolId: MATRIX_MESSAGING_TOOL_ID };

    } catch (error) {
      const errorMsg = error instanceof Error ? error.message : String(error);
      console.error(`[LettaService] Failed to attach matrix_messaging tool to agent ${agentId}:`, errorMsg);
      return { attached: false, alreadyHad: false, error: errorMsg };
    }
  }
}
