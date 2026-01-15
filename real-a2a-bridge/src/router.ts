import { readFileSync, watchFile, existsSync } from "fs";
import { parse as parseYaml } from "yaml";
import { EventEmitter } from "events";

interface RoutesConfig {
  topics: Record<string, string>;
  agents: Record<string, string>;
  defaults: {
    topic: string;
  };
}

interface RouteResult {
  primaryRoom: string | null;
  mentionRooms: string[];
  mentionedAgents: string[];
}

const MENTION_REGEX = /@([a-zA-Z][a-zA-Z0-9_-]*)/g;

export class Router extends EventEmitter {
  private configPath: string;
  private config: RoutesConfig;
  private agentAliases: Map<string, string>;

  constructor(configPath: string) {
    super();
    this.configPath = configPath;
    this.agentAliases = new Map();
    this.config = this.loadConfig();
    this.buildAliases();
    this.watchConfig();
  }

  private loadConfig(): RoutesConfig {
    if (!existsSync(this.configPath)) {
      console.warn(`[Router] Config not found: ${this.configPath}, using defaults`);
      return {
        topics: {},
        agents: {},
        defaults: { topic: "agent-swarm-lobby" },
      };
    }

    const content = readFileSync(this.configPath, "utf-8");
    const parsed = parseYaml(content) as RoutesConfig;
    
    console.log(`[Router] Loaded config: ${Object.keys(parsed.topics || {}).length} topics, ${Object.keys(parsed.agents || {}).length} agents`);
    
    return {
      topics: parsed.topics || {},
      agents: parsed.agents || {},
      defaults: parsed.defaults || { topic: "agent-swarm-lobby" },
    };
  }

  private buildAliases(): void {
    this.agentAliases.clear();
    
    for (const [name, roomId] of Object.entries(this.config.agents)) {
      const normalized = name.toLowerCase();
      this.agentAliases.set(normalized, roomId);
      this.agentAliases.set(`@${normalized}`, roomId);
    }
  }

  private watchConfig(): void {
    watchFile(this.configPath, { interval: 1000 }, () => {
      console.log("[Router] Config file changed, reloading...");
      try {
        this.config = this.loadConfig();
        this.buildAliases();
        this.emit("reload", this.config);
      } catch (err) {
        console.error("[Router] Failed to reload config:", err);
      }
    });
  }

  routeP2PMessage(topic: string, messageBody: string): RouteResult {
    const primaryRoom = this.config.topics[topic] || 
                        this.config.topics[this.config.defaults.topic] || 
                        null;

    const mentionRooms: string[] = [];
    const mentionedAgents: string[] = [];
    
    const mentions = messageBody.match(MENTION_REGEX) || [];
    for (const mention of mentions) {
      const normalized = mention.toLowerCase();
      const roomId = this.agentAliases.get(normalized);
      if (roomId && !mentionRooms.includes(roomId)) {
        mentionRooms.push(roomId);
        mentionedAgents.push(mention.slice(1));
      }
    }

    return { primaryRoom, mentionRooms, mentionedAgents };
  }

  routeMatrixMessage(roomId: string): string | null {
    for (const [topic, room] of Object.entries(this.config.topics)) {
      if (room === roomId) {
        return topic;
      }
    }
    
    for (const room of Object.values(this.config.agents)) {
      if (room === roomId) {
        return this.config.defaults.topic;
      }
    }
    
    return null;
  }
  
  getRoomAgent(roomId: string): string | null {
    for (const [agent, room] of Object.entries(this.config.agents)) {
      if (room === roomId) {
        return agent;
      }
    }
    return null;
  }

  getAgentRoom(agentName: string): string | null {
    const normalized = agentName.toLowerCase().replace(/^@/, "");
    return this.agentAliases.get(normalized) || this.agentAliases.get(`@${normalized}`) || null;
  }

  getTopicRoom(topic: string): string | null {
    return this.config.topics[topic] || null;
  }

  getDefaultTopic(): string {
    return this.config.defaults.topic;
  }

  getAllTopics(): string[] {
    return Object.keys(this.config.topics);
  }

  getAllAgents(): string[] {
    return Object.keys(this.config.agents);
  }

  getAllRooms(): string[] {
    const rooms = new Set<string>();
    for (const room of Object.values(this.config.topics)) {
      rooms.add(room);
    }
    for (const room of Object.values(this.config.agents)) {
      rooms.add(room);
    }
    return Array.from(rooms);
  }
}
