-- Dead letter table for failed message deliveries (MXSYN-547)
-- Run against: postgresql://letta:letta@192.168.50.90:5432/matrix_letta

CREATE TABLE IF NOT EXISTS dead_letter_messages (
    id SERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    room_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    message_body TEXT NOT NULL,
    sender TEXT NOT NULL,
    error TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    failed_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for querying by room or agent
CREATE INDEX IF NOT EXISTS idx_dead_letter_room_id ON dead_letter_messages(room_id);
CREATE INDEX IF NOT EXISTS idx_dead_letter_agent_id ON dead_letter_messages(agent_id);
CREATE INDEX IF NOT EXISTS idx_dead_letter_created_at ON dead_letter_messages(created_at);

-- Comment
COMMENT ON TABLE dead_letter_messages IS 'Messages that failed delivery to Letta agents after exhausting retries';
