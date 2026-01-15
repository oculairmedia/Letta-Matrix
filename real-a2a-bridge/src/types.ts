export interface BridgeConfig {
  matrix: {
    homeserver: string;
    serverName: string;
    registrationToken: string;
    roomId: string;
    localpart: string;
    displayName: string;
    adminUsername?: string;
    adminPassword?: string;
  };
  p2p: {
    room: string;
    identity: string;
    ticket?: string;
  };
  bridge: {
    logMessages: boolean;
    healthCheckIntervalMs: number;
    opencodeBridgeUrl?: string;
    opencodeDirectory?: string;
  };
}

export interface P2PMessage {
  timestamp: string;
  fromName: string;
  fromId: string;
  content: string;
  messageId: string; // For deduplication
}

export interface MatrixMessage {
  sender: string;
  displayName: string;
  body: string;
  timestamp: number;
  eventId: string;
  roomId: string;
  messageId: string;
}

export interface ProcessedMessage {
  id: string; // Unique message ID
  origin: 'matrix' | 'p2p'; // Where it came from
  timestamp: number;
  processed: number; // When we processed it
}

export type MessageOrigin = 'matrix' | 'p2p';
