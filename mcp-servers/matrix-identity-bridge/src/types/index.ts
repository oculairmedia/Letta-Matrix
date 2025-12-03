/**
 * Core type definitions for Matrix Messaging MCP
 */

export interface MatrixIdentity {
  /** Unique identifier (agent_id, directory, or custom) */
  id: string;
  /** Full Matrix user ID (@user:domain) */
  mxid: string;
  /** Display name */
  displayName: string;
  /** Avatar URL (mxc://) */
  avatarUrl?: string;
  /** Access token for this user */
  accessToken: string;
  /** Identity type */
  type: 'letta' | 'opencode' | 'custom';
  /** Creation timestamp */
  createdAt: number;
  /** Last used timestamp */
  lastUsedAt: number;
}

export interface DMRoomMapping {
  /** Unique key: "mxid1<->mxid2" (alphabetically sorted) */
  key: string;
  /** Room ID */
  roomId: string;
  /** Participant MXIDs */
  participants: [string, string];
  /** Creation timestamp */
  createdAt: number;
  /** Last message timestamp */
  lastActivityAt: number;
}

export interface StorageMetadata {
  /** Schema version for migrations */
  version: number;
  /** Last updated timestamp */
  updatedAt: number;
}

export interface StorageData {
  identities: Record<string, MatrixIdentity>;
  dmRooms: Record<string, DMRoomMapping>;
  metadata: StorageMetadata;
}

export interface MessageContent {
  /** Message type (m.text, m.image, m.file, etc.) */
  msgtype: string;
  /** Message body */
  body: string;
  /** Optional formatted body (HTML) */
  formatted_body?: string;
  /** Optional format (org.matrix.custom.html) */
  format?: string;
  /** Additional content fields */
  [key: string]: unknown;
}

export interface SendMessageOptions {
  /** Target room ID */
  roomId: string;
  /** Message content */
  content: MessageContent;
  /** Optional event ID to reply to */
  replyToEventId?: string;
}

export interface MatrixEvent {
  /** Event ID */
  event_id: string;
  /** Event type */
  type: string;
  /** Sender MXID */
  sender: string;
  /** Event content */
  content: Record<string, unknown>;
  /** Origin server timestamp */
  origin_server_ts: number;
  /** Room ID */
  room_id: string;
}

export interface RoomInfo {
  /** Room ID */
  roomId: string;
  /** Room name */
  name?: string;
  /** Room topic */
  topic?: string;
  /** Room avatar URL */
  avatarUrl?: string;
  /** Member count */
  memberCount: number;
  /** Whether this is a DM */
  isDirect: boolean;
}

export interface IdentityProvisionRequest {
  /** Unique ID for this identity */
  id: string;
  /** Desired localpart (username without @domain) */
  localpart: string;
  /** Display name */
  displayName: string;
  /** Avatar URL */
  avatarUrl?: string;
  /** Identity type */
  type: 'letta' | 'opencode' | 'custom';
}

export interface LettaContext {
  /** Letta agent ID */
  agentId: string;
  /** Optional workspace ID */
  workspaceId?: string;
}

export interface OpenCodeContext {
  /** OpenCode session directory */
  directory: string;
  /** Optional session ID */
  sessionId?: string;
}

export interface ToolContext {
  /** Optional Letta context */
  letta?: LettaContext;
  /** Optional OpenCode context */
  opencode?: OpenCodeContext;
  /** Custom identity ID */
  identityId?: string;
}
