export interface MatrixFilters {
  msgtype: string;
  senders: string[];
}

export interface MatrixConfig {
  homeserver: string;
  accessToken: string;
  userId: string;
  rooms: string[];
  filters: MatrixFilters;
  isGlobal?: boolean;
}

export interface MatrixMessage {
  roomId: string;
  sender: string;
  body: string;
  timestamp: number;
}

export interface HookInput {
  user_prompt: string;
  hook_event_name: string;
  session_id?: string;
  cwd?: string;
}

export interface HookOutput {
  systemMessage?: string;
}
