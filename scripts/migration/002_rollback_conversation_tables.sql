-- Rollback: Remove conversation tracking tables
-- Author: Sisyphus (OpenCode)
-- Date: 2026-01-17
-- Issue: MXSYN-327

DROP TABLE IF EXISTS inter_agent_conversations;
DROP TABLE IF EXISTS room_conversations;
