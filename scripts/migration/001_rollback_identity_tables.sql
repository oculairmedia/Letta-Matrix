-- Rollback: Remove identity management tables
-- Issue: matrix-synapse-deployment-mybw

DROP INDEX IF EXISTS idx_dm_room_id;
DROP INDEX IF EXISTS idx_dm_participant_2;
DROP INDEX IF EXISTS idx_dm_participant_1;
DROP TABLE IF EXISTS dm_rooms;

DROP INDEX IF EXISTS idx_identities_active;
DROP INDEX IF EXISTS idx_identities_mxid;
DROP INDEX IF EXISTS idx_identities_type;
DROP TABLE IF EXISTS identities;
