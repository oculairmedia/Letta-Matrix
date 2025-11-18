-- Create database for Matrix-Letta agent mappings
-- This should be run once to initialize the database

-- Create the database (run as postgres superuser)
-- CREATE DATABASE matrix_letta;

-- Connect to the database
\c matrix_letta;

-- Main agent mappings table
CREATE TABLE IF NOT EXISTS agent_mappings (
    agent_id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    matrix_user_id TEXT NOT NULL UNIQUE,
    matrix_password TEXT NOT NULL,
    room_id TEXT UNIQUE,
    room_created BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW() NOT NULL
);

-- Invitation status table (normalized)
CREATE TABLE IF NOT EXISTS invitation_status (
    agent_id TEXT NOT NULL REFERENCES agent_mappings(agent_id) ON DELETE CASCADE,
    invitee TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (agent_id, invitee)
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_room_id ON agent_mappings(room_id);
CREATE INDEX IF NOT EXISTS idx_agent_name ON agent_mappings(agent_name);
CREATE INDEX IF NOT EXISTS idx_matrix_user ON agent_mappings(matrix_user_id);

-- Grant permissions (adjust username as needed)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO postgres;

-- Add update trigger for updated_at column
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_agent_mappings_updated_at
BEFORE UPDATE ON agent_mappings
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
