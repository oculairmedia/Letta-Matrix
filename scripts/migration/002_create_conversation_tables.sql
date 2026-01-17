-- Migration: Add conversation tracking tables for Letta 0.16.2 Conversations API
-- Author: Sisyphus (OpenCode)
-- Date: 2026-01-17
-- Issue: MXSYN-327

-- Room conversations: maps Matrix rooms to Letta conversation IDs per agent
CREATE TABLE IF NOT EXISTS room_conversations (
    id SERIAL PRIMARY KEY,
    room_id VARCHAR(255) NOT NULL,
    agent_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) NOT NULL,
    strategy VARCHAR(50) NOT NULL DEFAULT 'per-room',
    user_mxid VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    last_message_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_room_agent_user UNIQUE (room_id, agent_id, user_mxid)
);

CREATE INDEX IF NOT EXISTS idx_room_conv_room_id ON room_conversations(room_id);
CREATE INDEX IF NOT EXISTS idx_room_conv_agent_id ON room_conversations(agent_id);
CREATE INDEX IF NOT EXISTS idx_room_conv_last_msg ON room_conversations(last_message_at);
CREATE INDEX IF NOT EXISTS idx_room_conv_conversation_id ON room_conversations(conversation_id);

-- Inter-agent conversations: tracks conversations between agents for @mention routing
CREATE TABLE IF NOT EXISTS inter_agent_conversations (
    id SERIAL PRIMARY KEY,
    source_agent_id VARCHAR(255) NOT NULL,
    target_agent_id VARCHAR(255) NOT NULL,
    room_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255) NOT NULL,
    user_mxid VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    last_message_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_inter_agent_conv UNIQUE (source_agent_id, target_agent_id, room_id, user_mxid)
);

CREATE INDEX IF NOT EXISTS idx_inter_agent_source ON inter_agent_conversations(source_agent_id);
CREATE INDEX IF NOT EXISTS idx_inter_agent_target ON inter_agent_conversations(target_agent_id);
CREATE INDEX IF NOT EXISTS idx_inter_agent_room ON inter_agent_conversations(room_id);
CREATE INDEX IF NOT EXISTS idx_inter_agent_last_msg ON inter_agent_conversations(last_message_at);

COMMENT ON TABLE room_conversations IS 'Maps Matrix rooms to Letta conversation IDs for context isolation';
COMMENT ON TABLE inter_agent_conversations IS 'Tracks conversations between agents for @mention routing';
COMMENT ON COLUMN room_conversations.strategy IS 'Conversation strategy: per-room (default) or per-user (for DMs)';
COMMENT ON COLUMN room_conversations.user_mxid IS 'User MXID for per-user strategy in DMs';
COMMENT ON COLUMN inter_agent_conversations.source_agent_id IS 'Agent initiating the conversation';
COMMENT ON COLUMN inter_agent_conversations.target_agent_id IS 'Agent being mentioned/contacted';
