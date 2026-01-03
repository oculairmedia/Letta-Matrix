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
        call_kwargs = mock_client.blocks.create.call_args[1]
        assert call_kwargs["label"] == MATRIX_BLOCK_LABEL
        assert call_kwargs["value"] == MATRIX_CAPABILITIES_CONTENT
    
    @pytest.mark.asyncio
    async def test_returns_existing_block_unchanged(self):
        mock_client = MagicMock()
        mock_block = MagicMock()
        mock_block.id = "block-existing-456"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_block]
        
        result = await get_or_create_matrix_block(mock_client)
        
        assert result == "block-existing-456"
        mock_client.blocks.create.assert_not_called()
        mock_client.blocks.update.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_updates_block_when_content_changed(self):
        mock_client = MagicMock()
        mock_block = MagicMock()
        mock_block.id = "block-outdated-789"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = "old content that differs"
        mock_client.blocks.list.return_value = [mock_block]
        
        result = await get_or_create_matrix_block(mock_client)
        
        assert result == "block-outdated-789"
        mock_client.blocks.update.assert_called_once_with(
            block_id="block-outdated-789",
            value=MATRIX_CAPABILITIES_CONTENT
        )


class TestEnsureAgentHasBlock:
    @pytest.mark.asyncio
    async def test_skips_when_already_attached(self):
        mock_client = MagicMock()
        mock_agent = MagicMock()
        mock_existing_block = MagicMock()
        mock_existing_block.id = "block-123"
        mock_agent.blocks = [mock_existing_block]
        mock_client.agents.retrieve.return_value = mock_agent
        
        result = await ensure_agent_has_block("agent-abc", "block-123", mock_client)
        
        assert result is True
        mock_client.agents.blocks.attach.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_attaches_when_not_present(self):
        mock_client = MagicMock()
        mock_agent = MagicMock()
        mock_agent.blocks = []
        mock_client.agents.retrieve.return_value = mock_agent
        
        result = await ensure_agent_has_block("agent-abc", "block-123", mock_client)
        
        assert result is True
        mock_client.agents.blocks.attach.assert_called_once_with(
            agent_id="agent-abc",
            block_id="block-123"
        )


class TestSyncMatrixBlockToAgents:
    @pytest.mark.asyncio
    async def test_idempotency_no_duplicates(self):
        """Running sync twice should not create duplicate attachments."""
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-shared"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_block]
        
        mock_agent = MagicMock()
        mock_agent.blocks = []
        mock_client.agents.retrieve.return_value = mock_agent
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            result1 = await sync_matrix_block_to_agents(["agent-1", "agent-2"])
        
        assert result1["synced"] == 2
        assert result1["skipped"] == 0
        
        attached_block = MagicMock()
        attached_block.id = "block-shared"
        mock_agent.blocks = [attached_block]
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            result2 = await sync_matrix_block_to_agents(["agent-1", "agent-2"])
        
        assert result2["synced"] == 0
        assert result2["skipped"] == 2
    
    @pytest.mark.asyncio
    async def test_handles_mixed_agents(self):
        """Some agents already have block, some don't."""
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-shared"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = MATRIX_CAPABILITIES_CONTENT
        mock_client.blocks.list.return_value = [mock_block]
        
        attached_block = MagicMock()
        attached_block.id = "block-shared"
        
        def mock_retrieve(agent_id):
            agent = MagicMock()
            if agent_id == "agent-with-block":
                agent.blocks = [attached_block]
            else:
                agent.blocks = []
            return agent
        
        mock_client.agents.retrieve.side_effect = mock_retrieve
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            result = await sync_matrix_block_to_agents(["agent-with-block", "agent-without-block"])
        
        assert result["synced"] == 1
        assert result["skipped"] == 1
    
    @pytest.mark.asyncio
    async def test_block_content_update_propagates(self):
        """When block content changes, it should be updated."""
        mock_client = MagicMock()
        
        mock_block = MagicMock()
        mock_block.id = "block-outdated"
        mock_block.label = MATRIX_BLOCK_LABEL
        mock_block.value = "old content"
        mock_client.blocks.list.return_value = [mock_block]
        
        mock_agent = MagicMock()
        mock_agent.blocks = []
        mock_client.agents.retrieve.return_value = mock_agent
        
        with patch('src.letta.matrix_memory.get_letta_client', return_value=mock_client):
            await sync_matrix_block_to_agents(["agent-1"])
        
        mock_client.blocks.update.assert_called_once_with(
            block_id="block-outdated",
            value=MATRIX_CAPABILITIES_CONTENT
        )
