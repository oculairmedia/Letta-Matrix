"""Tests for matrix memory block management."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import hashlib

from src.letta.matrix_memory import (
    MATRIX_BLOCK_LABEL,
    MATRIX_CAPABILITIES_CONTENT,
    _content_hash,
    get_or_create_matrix_block,
    ensure_agent_has_block,
    sync_matrix_block_to_agents,
    format_matrix_context,
)


class TestContentHash:
    def test_same_content_same_hash(self):
        content = "test content"
        assert _content_hash(content) == _content_hash(content)
    
    def test_different_content_different_hash(self):
        assert _content_hash("content a") != _content_hash("content b")
    
    def test_hash_length(self):
        assert len(_content_hash("any content")) == 16


class TestFormatMatrixContext:
    def test_with_room_name(self):
        result = format_matrix_context("@user:domain.com", "Test Room")
        assert result == "[Matrix: @user:domain.com in Test Room]"
    
    def test_without_room_name(self):
        result = format_matrix_context("@user:domain.com")
        assert result == "[Matrix: @user:domain.com]"
    
    def test_with_none_room_name(self):
        result = format_matrix_context("@user:domain.com", None)
        assert result == "[Matrix: @user:domain.com]"


class TestGetOrCreateMatrixBlock:
    @pytest.mark.asyncio
    async def test_creates_block_when_not_exists(self):
        mock_client = MagicMock()
        mock_client.blocks.list.return_value = []
        mock_block = MagicMock()
        mock_block.id = "block-new-123"
        mock_client.blocks.create.return_value = mock_block
        
        result = await get_or_create_matrix_block(mock_client)
        
        assert result == "block-new-123"
        mock_client.blocks.create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_reuses_existing_block(self):
        mock_client = MagicMock()
        mock_existing = MagicMock()
        mock_existing.id = "block-existing"
        mock_existing.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_existing]
        
        result = await get_or_create_matrix_block(mock_client)
        
        assert result == "block-existing"
        mock_client.blocks.create.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_updates_outdated_block(self):
        mock_client = MagicMock()
        mock_existing = MagicMock()
        mock_existing.id = "block-outdated"
        mock_existing.value = "old content"
        mock_client.blocks.list.return_value = [mock_existing]
        
        result = await get_or_create_matrix_block(mock_client)
        
        assert result == "block-outdated"
        mock_client.blocks.update.assert_called_once_with(
            block_id="block-outdated",
            value=MATRIX_CAPABILITIES_CONTENT
        )


class TestEnsureAgentHasBlock:
    @pytest.mark.asyncio
    async def test_skips_when_already_attached(self):
        mock_client = MagicMock()
        mock_existing_block = MagicMock()
        mock_existing_block.id = "block-123"
        mock_existing_block.label = MATRIX_BLOCK_LABEL
        mock_client.agents.blocks.list.return_value = [mock_existing_block]
        
        result = await ensure_agent_has_block("agent-abc", "block-123", mock_client)
        
        assert result is True
        mock_client.agents.blocks.attach.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_attaches_when_not_present(self):
        mock_client = MagicMock()
        mock_client.agents.blocks.list.return_value = []
        
        result = await ensure_agent_has_block("agent-abc", "block-123", mock_client)
        
        assert result is True
        mock_client.agents.blocks.attach.assert_called_once_with(
            agent_id="agent-abc",
            block_id="block-123"
        )
    
    @pytest.mark.asyncio
    async def test_replaces_old_block_with_same_label(self):
        mock_client = MagicMock()
        mock_old_block = MagicMock()
        mock_old_block.id = "block-old"
        mock_old_block.label = MATRIX_BLOCK_LABEL
        mock_client.agents.blocks.list.return_value = [mock_old_block]
        
        result = await ensure_agent_has_block("agent-abc", "block-new", mock_client)
        
        assert result is True
        mock_client.agents.blocks.detach.assert_called_once_with(
            agent_id="agent-abc",
            block_id="block-old"
        )
        mock_client.agents.blocks.attach.assert_called_once_with(
            agent_id="agent-abc",
            block_id="block-new"
        )


class TestSyncMatrixBlockToAgents:
    @pytest.mark.asyncio
    async def test_syncs_agents_successfully(self):
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-shared"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_block]
        mock_client.agents.blocks.list.return_value = []
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            result = await sync_matrix_block_to_agents(["agent-1", "agent-2"])
        
        assert result["synced"] == 2
        assert result["failed"] == 0
    
    @pytest.mark.asyncio
    async def test_handles_already_attached(self):
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-shared"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_block]
        
        attached_block = MagicMock()
        attached_block.id = "block-shared"
        attached_block.label = MATRIX_BLOCK_LABEL
        mock_client.agents.blocks.list.return_value = [attached_block]
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            result = await sync_matrix_block_to_agents(["agent-1", "agent-2"])
        
        assert result["synced"] == 2
        mock_client.agents.blocks.attach.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_handles_mixed_agents(self):
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-shared"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_block]
        
        attached_block = MagicMock()
        attached_block.id = "block-shared"
        attached_block.label = MATRIX_BLOCK_LABEL
        
        def mock_list_blocks(agent_id):
            if agent_id == "agent-with-block":
                return [attached_block]
            return []
        
        mock_client.agents.blocks.list.side_effect = mock_list_blocks
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            result = await sync_matrix_block_to_agents(["agent-with-block", "agent-without-block"])
        
        assert result["synced"] == 2
        assert mock_client.agents.blocks.attach.call_count == 1
    
    @pytest.mark.asyncio
    async def test_block_content_update_propagates(self):
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-outdated"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = "old content"
        mock_client.blocks.list.return_value = [mock_block]
        mock_client.agents.blocks.list.return_value = []
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            await sync_matrix_block_to_agents(["agent-1"])
        
        mock_client.blocks.update.assert_called_once_with(
            block_id="block-outdated",
            value=MATRIX_CAPABILITIES_CONTENT
        )
