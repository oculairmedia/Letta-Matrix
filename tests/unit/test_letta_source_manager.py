import asyncio
import logging
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from src.matrix.letta_source_manager import LettaSourceManager


def _make_manager(**overrides):
    defaults = {
        "embedding_model": "letta/letta-free",
        "embedding_endpoint": None,
        "embedding_endpoint_type": "openai",
        "embedding_dim": 1536,
        "embedding_chunk_size": 300,
        "max_retries": 1,
        "retry_delay": 0.01,
    }
    defaults.update(overrides)
    client = MagicMock()
    logger = logging.getLogger("test_letta_source_manager")
    return LettaSourceManager(client, defaults, logger)


class TestGetEmbeddingConfig:
    def test_returns_defaults_when_no_agent_id(self):
        mgr = _make_manager()
        config = mgr.get_embedding_config()
        assert config["embedding_model"] == "letta/letta-free"
        assert config["embedding_dim"] == 1536
        assert config["embedding_endpoint_type"] == "openai"
        assert config["embedding_chunk_size"] == 300
        assert "embedding_endpoint" not in config

    def test_returns_defaults_with_endpoint_when_set(self):
        mgr = _make_manager(embedding_endpoint="http://localhost:11434/v1")
        config = mgr.get_embedding_config()
        assert config["embedding_endpoint"] == "http://localhost:11434/v1"

    def test_uses_agent_config_when_available(self):
        mgr = _make_manager()
        mock_ec = MagicMock()
        mock_ec.embedding_model = "text-embedding-3-small"
        mock_ec.embedding_endpoint_type = "openai"
        mock_ec.embedding_dim = 768
        mock_ec.embedding_chunk_size = 512
        mock_ec.embedding_endpoint = "https://api.openai.com/v1"
        mock_agent = MagicMock()
        mock_agent.embedding_config = mock_ec
        mgr.letta_client.agents.retrieve.return_value = mock_agent

        config = mgr.get_embedding_config(agent_id="agent-123")
        assert config["embedding_model"] == "text-embedding-3-small"
        assert config["embedding_dim"] == 768
        assert config["embedding_endpoint"] == "https://api.openai.com/v1"

    def test_falls_back_on_agent_retrieve_failure(self):
        mgr = _make_manager()
        mgr.letta_client.agents.retrieve.side_effect = RuntimeError("network error")

        config = mgr.get_embedding_config(agent_id="agent-broken")
        assert config["embedding_model"] == "letta/letta-free"
        assert config["embedding_dim"] == 1536


class TestGetOrCreateSource:
    @pytest.mark.asyncio
    async def test_caches_results(self):
        mgr = _make_manager()
        mgr._source_cache["!room:test"] = "folder-cached-123"

        result = await mgr.get_or_create_source("!room:test")
        assert result == "folder-cached-123"
        mgr.letta_client.folders.list.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_and_caches_new_folder(self):
        mgr = _make_manager()
        mgr.letta_client.folders.list.return_value = []
        mock_folder = MagicMock()
        mock_folder.id = "folder-new-456"
        mgr.letta_client.folders.create.return_value = mock_folder

        result = await mgr.get_or_create_source("!newroom:test")
        assert result == "folder-new-456"
        assert mgr._source_cache["!newroom:test"] == "folder-new-456"

    @pytest.mark.asyncio
    async def test_returns_existing_folder(self):
        mgr = _make_manager()
        mock_folder = MagicMock()
        mock_folder.id = "folder-existing-789"
        mgr.letta_client.folders.list.return_value = [mock_folder]

        result = await mgr.get_or_create_source("!existing:test")
        assert result == "folder-existing-789"
        mgr.letta_client.folders.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_concurrent_calls_same_room_create_folder_once(self):
        mgr = _make_manager()
        mgr.letta_client.folders.list.return_value = []
        mock_folder = MagicMock()
        mock_folder.id = "folder-once-123"
        mgr.letta_client.folders.create.return_value = mock_folder

        async def fake_run_sync(func, *args, **kwargs):
            await asyncio.sleep(0.01)
            return func(*args, **kwargs)

        mgr._run_sync = fake_run_sync  # type: ignore[assignment]

        results = await asyncio.gather(
            mgr.get_or_create_source("!race:test"),
            mgr.get_or_create_source("!race:test"),
        )

        assert results == ["folder-once-123", "folder-once-123"]
        mgr.letta_client.folders.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_room_lock_cache_lru_evicts_idle_locks(self):
        mgr = _make_manager(room_lock_cache_max=2)
        mgr.letta_client.folders.list.return_value = []

        create_counter = {"i": 0}

        def _make_folder(*args, **kwargs):
            create_counter["i"] += 1
            folder = MagicMock()
            folder.id = f"folder-{create_counter['i']}"
            return folder

        mgr.letta_client.folders.create.side_effect = _make_folder

        await mgr.get_or_create_source("!room1:test")
        await mgr.get_or_create_source("!room2:test")
        await mgr.get_or_create_source("!room3:test")

        assert len(mgr._room_locks) <= 2
        assert "!room1:test" not in mgr._room_locks


class TestAttachSourceToAgent:
    @pytest.mark.asyncio
    async def test_skips_if_already_attached(self):
        mgr = _make_manager()
        mock_folder = MagicMock()
        mock_folder.id = "folder-abc"
        mgr.letta_client.agents.folders.list.return_value = [mock_folder]

        await mgr.attach_source_to_agent("folder-abc", "agent-xyz")
        mgr.letta_client.agents.folders.attach.assert_not_called()

    @pytest.mark.asyncio
    async def test_attaches_when_not_present(self):
        mgr = _make_manager()
        mgr.letta_client.agents.folders.list.return_value = []

        await mgr.attach_source_to_agent("folder-abc", "agent-xyz")
        mgr.letta_client.agents.folders.attach.assert_called_once_with(
            "folder-abc", agent_id="agent-xyz"
        )


class TestEnsureSearchToolAttached:
    @pytest.mark.asyncio
    async def test_finds_and_attaches_tool(self):
        mgr = _make_manager()
        search_tool = MagicMock()
        search_tool.id = "tool-search-001"
        mgr.letta_client.tools.list.return_value = [search_tool]
        mgr.letta_client.agents.tools.list.return_value = []

        await mgr.ensure_search_tool_attached("agent-xyz")
        mgr.letta_client.agents.tools.attach.assert_called_once_with(
            "tool-search-001", agent_id="agent-xyz"
        )

    @pytest.mark.asyncio
    async def test_skips_when_already_attached(self):
        mgr = _make_manager()
        search_tool = MagicMock()
        search_tool.id = "tool-search-001"
        mgr.letta_client.tools.list.return_value = [search_tool]
        existing = MagicMock()
        existing.id = "tool-search-001"
        mgr.letta_client.agents.tools.list.return_value = [existing]

        await mgr.ensure_search_tool_attached("agent-xyz")
        mgr.letta_client.agents.tools.attach.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_tool_not_found(self):
        mgr = _make_manager()
        mgr.letta_client.tools.list.return_value = []

        await mgr.ensure_search_tool_attached("agent-xyz")
        mgr.letta_client.agents.tools.attach.assert_not_called()


class TestPollFileStatus:
    @pytest.mark.asyncio
    async def test_sync_complete_returns_true(self):
        mgr = _make_manager()
        result = await mgr.poll_file_status("folder-1", "sync-complete")
        assert result is True

    @pytest.mark.asyncio
    async def test_completed_status(self):
        mgr = _make_manager()
        mock_file = MagicMock()
        mock_file.id = "file-123"
        mock_file.processing_status = "completed"
        mgr.letta_client.folders.files.list.return_value = [mock_file]

        result = await mgr.poll_file_status("folder-1", "file-123", timeout=5, interval=1)
        assert result is True


class TestUploadToLetta:
    @pytest.mark.asyncio
    async def test_upload_to_letta_reads_file_via_to_thread(self, tmp_path):
        mgr = _make_manager()

        file_path = tmp_path / "doc.txt"
        file_path.write_bytes(b"hello world")

        metadata = MagicMock()
        metadata.file_name = "doc.txt"
        metadata.file_type = "text/plain"

        upload_result = MagicMock()
        upload_result.id = "file-xyz"
        mgr.letta_client.folders.files.upload.return_value = upload_result

        async def _fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("src.matrix.letta_source_manager.asyncio.to_thread", side_effect=_fake_to_thread) as mock_to_thread:
            file_id = await mgr.upload_to_letta(str(file_path), "folder-1", metadata)

        assert file_id == "file-xyz"
        mock_to_thread.assert_awaited_once()
        mgr.letta_client.folders.files.upload.assert_called_once()
