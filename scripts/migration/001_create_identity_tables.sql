-- Migration: Add identity management tables
-- Author: Sisyphus (OpenCode)
-- Date: 2026-01-04
-- Issue: matrix-synapse-deployment-mybw

-- Identity table: stores Matrix user credentials and metadata
CREATE TABLE IF NOT EXISTS identities (
    id VARCHAR(255) PRIMARY KEY,
    identity_type VARCHAR(50) NOT NULL,
    mxid VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(255),
    avatar_url VARCHAR(500),
    access_token TEXT NOT NULL,
    password_hash TEXT,
    device_id VARCHAR(255),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    last_used_at TIMESTAMP WITHOUT TIME ZONE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_identities_type ON identities(identity_type);
CREATE INDEX IF NOT EXISTS idx_identities_mxid ON identities(mxid);
CREATE INDEX IF NOT EXISTS idx_identities_active ON identities(is_active);

-- DM room mappings: stores direct message room associations
CREATE TABLE IF NOT EXISTS dm_rooms (
    id SERIAL PRIMARY KEY,
    room_id VARCHAR(255) UNIQUE NOT NULL,
    participant_1 VARCHAR(255) NOT NULL,
    participant_2 VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    last_activity_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CONSTRAINT uq_participants UNIQUE (participant_1, participant_2)
);

CREATE INDEX IF NOT EXISTS idx_dm_participant_1 ON dm_rooms(participant_1);
CREATE INDEX IF NOT EXISTS idx_dm_participant_2 ON dm_rooms(participant_2);
CREATE INDEX IF NOT EXISTS idx_dm_room_id ON dm_rooms(room_id);

-- Add comments for documentation
COMMENT ON TABLE identities IS 'Matrix identities for agents and OpenCode sessions';
COMMENT ON TABLE dm_rooms IS 'Direct message room mappings between Matrix users';
COMMENT ON COLUMN identities.id IS 'Unique identifier (e.g., letta_agent-xxx, opencode_xxx)';
COMMENT ON COLUMN identities.identity_type IS 'Type of identity: letta, opencode, or custom';
COMMENT ON COLUMN identities.mxid IS 'Full Matrix user ID (@user:domain)';
COMMENT ON COLUMN dm_rooms.participant_1 IS 'First participant MXID (alphabetically sorted)';
COMMENT ON COLUMN dm_rooms.participant_2 IS 'Second participant MXID (alphabetically sorted)';
