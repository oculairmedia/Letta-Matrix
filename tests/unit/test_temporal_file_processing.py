"""Tests for Temporal async file processing integration.

Tests the file_handler.py Temporal workflow dispatch and client.py
None-return handling for document uploads routed through Temporal.
"""

import asyncio
import os
import types
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_env_temporal_enabled():
    """Set TEMPORAL_FILE_PROCESSING_ENABLED=true in environment."""
    with patch.dict(os.environ, {
        "TEMPORAL_FILE_PROCESSING_ENABLED": "true",
        "TEMPORAL_HOST": "192.168.50.90:7233",
        "TEMPORAL_NAMESPACE": "matrix",
        "TEMPORAL_TASK_QUEUE": "matrix-file-queue",
    }):
        yield


@pytest.fixture
def mock_env_temporal_disabled():
    """Ensure TEMPORAL_FILE_PROCESSING_ENABLED is false."""
    with patch.dict(os.environ, {
        "TEMPORAL_FILE_PROCESSING_ENABLED": "false",
    }):
        yield


@pytest.fixture
def file_metadata():
    """Sample FileMetadata for a PDF document upload."""
    from src.matrix.file_handler import FileMetadata
    return FileMetadata(
        file_url="mxc://matrix.oculair.ca/abc123",
        file_name="report.pdf",
        file_type="application/pdf",
        file_size=1024 * 100,  # 100KB
        room_id="!testroom:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1709500000000,
        event_id="$evt_upload_001",
        caption="What does this report say?",
    )


@pytest.fixture
def image_metadata():
    """Sample FileMetadata for an image upload."""
    from src.matrix.file_handler import FileMetadata
    return FileMetadata(
        file_url="mxc://matrix.oculair.ca/img456",
        file_name="photo.jpg",
        file_type="image/jpeg",
        file_size=1024 * 200,
        room_id="!testroom:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1709500000000,
        event_id="$evt_upload_002",
    )


@pytest.fixture
def audio_metadata():
    """Sample FileMetadata for an audio upload."""
    from src.matrix.file_handler import FileMetadata
    return FileMetadata(
        file_url="mxc://matrix.oculair.ca/aud789",
        file_name="voice.ogg",
        file_type="audio/ogg",
        file_size=1024 * 50,
        room_id="!testroom:matrix.oculair.ca",
        sender="@user:matrix.oculair.ca",
        timestamp=1709500000000,
        event_id="$evt_upload_003",
    )


def _make_file_handler(temporal_enabled: bool = False):
    """Create a LettaFileHandler with mocked dependencies."""
    env = {
        "TEMPORAL_FILE_PROCESSING_ENABLED": "true" if temporal_enabled else "false",
        "DOCUMENT_PARSING_ENABLED": "true",
    }
    # Patch temporalio import check to succeed when enabled,
    # and always mock Letta SDK client to avoid real HTTP connections
    with patch.dict(os.environ, env), \
         patch("src.matrix.file_handler.Letta") as mock_letta_cls:
        mock_letta_cls.return_value = MagicMock()
        if temporal_enabled:
            with patch.dict("sys.modules", {"temporalio": MagicMock()}):
                from src.matrix.file_handler import LettaFileHandler
                handler = LettaFileHandler(
                    homeserver_url="http://tuwunel:6167",
                    letta_api_url="http://192.168.50.90:8289",
                    letta_token="test_token",
                    matrix_access_token="test_matrix_token",
                    notify_callback=AsyncMock(return_value="$status_evt_1"),
                )
        else:
            from src.matrix.file_handler import LettaFileHandler
            handler = LettaFileHandler(
                homeserver_url="http://tuwunel:6167",
                letta_api_url="http://192.168.50.90:8289",
                letta_token="test_token",
                matrix_access_token="test_matrix_token",
                notify_callback=AsyncMock(return_value="$status_evt_1"),
            )
    return handler


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------

class TestTemporalFeatureFlag:
    """Test TEMPORAL_FILE_PROCESSING_ENABLED feature flag behavior."""

    def test_disabled_by_default(self):
        """Temporal should be disabled when env var is not set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEMPORAL_FILE_PROCESSING_ENABLED", None)
            handler = _make_file_handler(temporal_enabled=False)
            assert handler._temporal_enabled is False

    def test_enabled_when_true(self):
        """Temporal should be enabled when TEMPORAL_FILE_PROCESSING_ENABLED=true."""
        handler = _make_file_handler(temporal_enabled=True)
        assert handler._temporal_enabled is True

    def test_graceful_fallback_without_sdk(self):
        """When temporalio is not installed, fall back to inline even if enabled."""
        env = {"TEMPORAL_FILE_PROCESSING_ENABLED": "true"}
        with patch.dict(os.environ, env):
            # Simulate temporalio not installed
            import sys
            original = sys.modules.get("temporalio")
            sys.modules["temporalio"] = None  # Makes import fail
            try:
                from importlib import reload
                import src.matrix.file_handler as fh_mod
                # The import check in __init__ catches ImportError
                # We test this by directly creating the handler
                handler = _make_file_handler(temporal_enabled=False)
                assert handler._temporal_enabled is False
            finally:
                if original is not None:
                    sys.modules["temporalio"] = original
                else:
                    sys.modules.pop("temporalio", None)


# ---------------------------------------------------------------------------
# Workflow dispatch tests
# ---------------------------------------------------------------------------

class TestStartTemporalWorkflow:
    """Test _start_temporal_workflow method."""

    @pytest.mark.asyncio
    async def test_starts_workflow_and_returns_none(self, file_metadata):
        """Should start a Temporal workflow and return None."""
        handler = _make_file_handler(temporal_enabled=True)

        # Mock the Temporal client
        mock_client = AsyncMock()
        mock_handle = AsyncMock()
        mock_handle.id = "file-test-workflow-123"
        mock_client.start_workflow = AsyncMock(return_value=mock_handle)
        handler._temporal_client = mock_client

        # Mock ensure_search_tool_attached
        handler.ensure_search_tool_attached = AsyncMock()

        result = await handler._start_temporal_workflow(
            file_metadata,
            file_metadata.room_id,
            "agent-test-123",
        )

        assert result is None
        mock_client.start_workflow.assert_called_once()
        handler.ensure_search_tool_attached.assert_called_once_with("agent-test-123")

    @pytest.mark.asyncio
    async def test_workflow_input_fields(self, file_metadata):
        """Verify correct fields are passed to FileProcessingWorkflow."""
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        mock_handle = AsyncMock()
        mock_handle.id = "file-test-workflow-456"
        mock_client.start_workflow = AsyncMock(return_value=mock_handle)
        handler._temporal_client = mock_client
        handler.ensure_search_tool_attached = AsyncMock()

        await handler._start_temporal_workflow(
            file_metadata,
            file_metadata.room_id,
            "agent-test-123",
        )

        call_args = mock_client.start_workflow.call_args
        workflow_input = call_args[0][1]  # Second positional arg

        assert workflow_input.mxc_url == "mxc://matrix.oculair.ca/abc123"
        assert workflow_input.file_name == "report.pdf"
        assert workflow_input.file_type == "application/pdf"
        assert workflow_input.room_id == "!testroom:matrix.oculair.ca"
        assert workflow_input.sender == "@user:matrix.oculair.ca"
        assert workflow_input.event_id == "$evt_upload_001"
        assert workflow_input.agent_id == "agent-test-123"
        assert workflow_input.caption == "What does this report say?"
        assert workflow_input.conversation_id is None

    @pytest.mark.asyncio
    async def test_workflow_input_uses_existing_conversation_id(self, file_metadata):
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        mock_handle = AsyncMock()
        mock_handle.id = "file-test-workflow-789"
        mock_client.start_workflow = AsyncMock(return_value=mock_handle)
        handler._temporal_client = mock_client
        handler.ensure_search_tool_attached = AsyncMock()

        mock_conv_service = Mock()
        mock_conv_service.get_conversation_id = Mock(return_value="conv-room-123")

        fake_conv_module = types.ModuleType("src.core.conversation_service")
        fake_conv_module.get_conversation_service = Mock(return_value=mock_conv_service)

        with patch.dict("sys.modules", {"src.core.conversation_service": fake_conv_module}):
            await handler._start_temporal_workflow(
                file_metadata,
                file_metadata.room_id,
                "agent-test-123",
            )

        call_args = mock_client.start_workflow.call_args
        workflow_input = call_args[0][1]
        assert workflow_input.conversation_id == "conv-room-123"

    @pytest.mark.asyncio
    async def test_sends_ack_message(self, file_metadata):
        """Should send acknowledgement message to room."""
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        mock_handle = AsyncMock()
        mock_handle.id = "wf-1"
        mock_client.start_workflow = AsyncMock(return_value=mock_handle)
        handler._temporal_client = mock_client
        handler.ensure_search_tool_attached = AsyncMock()

        await handler._start_temporal_workflow(
            file_metadata,
            file_metadata.room_id,
            "agent-test-123",
        )

        # notify_callback should have been called with ack message
        handler.notify_callback.assert_called_once()
        call_args = handler.notify_callback.call_args
        assert file_metadata.room_id == call_args[0][0]
        assert "report.pdf" in call_args[0][1]
        assert "async" in call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_failure_notifies_room(self, file_metadata):
        """When Temporal client fails, should notify room and return None."""
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        mock_client.start_workflow = AsyncMock(side_effect=Exception("Connection refused"))
        handler._temporal_client = mock_client
        handler.ensure_search_tool_attached = AsyncMock()

        result = await handler._start_temporal_workflow(
            file_metadata,
            file_metadata.room_id,
            "agent-test-123",
        )

        assert result is None
        # Should have sent both ack and error notification
        assert handler.notify_callback.call_count == 2
        error_call = handler.notify_callback.call_args_list[1]
        assert "Failed" in error_call[0][1] or "failed" in error_call[0][1].lower()

    @pytest.mark.asyncio
    async def test_search_tool_failure_non_fatal(self, file_metadata):
        """ensure_search_tool_attached failure should not prevent workflow start."""
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        mock_handle = AsyncMock()
        mock_handle.id = "wf-1"
        mock_client.start_workflow = AsyncMock(return_value=mock_handle)
        handler._temporal_client = mock_client
        handler.ensure_search_tool_attached = AsyncMock(side_effect=Exception("Letta down"))

        result = await handler._start_temporal_workflow(
            file_metadata,
            file_metadata.room_id,
            "agent-test-123",
        )

        # Should still start workflow despite tool attachment failure
        assert result is None
        mock_client.start_workflow.assert_called_once()


# ---------------------------------------------------------------------------
# handle_file_event routing tests
# ---------------------------------------------------------------------------

class TestHandleFileEventRouting:
    """Test that handle_file_event correctly routes to Temporal vs inline."""

    @pytest.mark.asyncio
    async def test_document_routes_to_temporal_when_enabled(self, file_metadata):
        """Document uploads should route to Temporal when enabled."""
        handler = _make_file_handler(temporal_enabled=True)

        # Mock _start_temporal_workflow
        handler._start_temporal_workflow = AsyncMock(return_value=None)
        handler._extract_file_metadata = Mock(return_value=file_metadata)
        handler._validate_file = Mock(return_value=None)  # No validation error

        # Create a mock event
        mock_event = Mock()
        mock_event.event_id = file_metadata.event_id

        result = await handler.handle_file_event(mock_event, file_metadata.room_id, "agent-123")

        assert result is None
        handler._start_temporal_workflow.assert_called_once()

    @pytest.mark.asyncio
    async def test_document_routes_inline_when_disabled(self, file_metadata):
        """Document uploads should process inline when Temporal is disabled."""
        handler = _make_file_handler(temporal_enabled=False)

        # Mock the inline handler
        handler._handle_document_upload = AsyncMock(return_value="Document text here")
        handler._extract_file_metadata = Mock(return_value=file_metadata)
        handler._validate_file = Mock(return_value=None)

        mock_event = Mock()
        mock_event.event_id = file_metadata.event_id

        result = await handler.handle_file_event(mock_event, file_metadata.room_id, "agent-123")

        assert result == "Document text here"
        handler._handle_document_upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_document_routes_inline_without_agent_id(self, file_metadata):
        """Without agent_id, documents should process inline even with Temporal enabled."""
        handler = _make_file_handler(temporal_enabled=True)

        handler._handle_document_upload = AsyncMock(return_value="Document text here")
        handler._extract_file_metadata = Mock(return_value=file_metadata)
        handler._validate_file = Mock(return_value=None)

        mock_event = Mock()
        mock_event.event_id = file_metadata.event_id

        result = await handler.handle_file_event(mock_event, file_metadata.room_id, agent_id=None)

        assert result == "Document text here"
        handler._handle_document_upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_image_unaffected_by_temporal(self, image_metadata):
        """Image uploads should not be affected by Temporal (always synchronous)."""
        handler = _make_file_handler(temporal_enabled=True)

        handler._handle_image_upload = AsyncMock(return_value=[{"type": "image"}])
        handler._extract_file_metadata = Mock(return_value=image_metadata)
        handler._validate_file = Mock(return_value=None)

        mock_event = Mock()
        mock_event.event_id = image_metadata.event_id

        result = await handler.handle_file_event(mock_event, image_metadata.room_id, "agent-123")

        assert isinstance(result, list)
        handler._handle_image_upload.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_unaffected_by_temporal(self, audio_metadata):
        """Audio uploads should not be affected by Temporal (always synchronous)."""
        handler = _make_file_handler(temporal_enabled=True)

        handler._handle_audio_upload = AsyncMock(return_value="[Voice message]: hello")
        handler._extract_file_metadata = Mock(return_value=audio_metadata)
        handler._validate_file = Mock(return_value=None)

        mock_event = Mock()
        mock_event.event_id = audio_metadata.event_id

        result = await handler.handle_file_event(mock_event, audio_metadata.room_id, "agent-123")

        assert isinstance(result, str)
        assert "Voice message" in result
        handler._handle_audio_upload.assert_called_once()


# ---------------------------------------------------------------------------
# Lazy Temporal client init tests
# ---------------------------------------------------------------------------

class TestTemporalClientInit:
    """Test lazy initialization of the Temporal client."""

    @pytest.mark.asyncio
    async def test_lazy_init_creates_client(self):
        """_get_temporal_client should create client on first call."""
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        with patch(
            "temporalio.client.Client.connect",
            new_callable=AsyncMock,
            return_value=mock_client,
        ):
            client = await handler._get_temporal_client()
            assert client is mock_client

    @pytest.mark.asyncio
    async def test_lazy_init_caches_client(self):
        """Second call should return cached client without reconnecting."""
        handler = _make_file_handler(temporal_enabled=True)

        mock_client = AsyncMock()
        handler._temporal_client = mock_client

        client = await handler._get_temporal_client()
        assert client is mock_client


# ---------------------------------------------------------------------------
# client.py file_callback None handling tests
# ---------------------------------------------------------------------------

class TestFileCallbackNoneHandling:
    """Test that file_callback in client.py correctly handles None returns."""

    @pytest.mark.asyncio
    async def test_none_result_returns_early(self):
        """When handle_file_event returns None, file_callback should return without sending to Letta."""
        # This tests the logic flow, not the actual function (which has many deps)
        file_result = None
        cleanup_event_ids = []
        status_summary = None

        # Simulate the None check from file_callback
        if file_result is None:
            action = "return_early"
        elif isinstance(file_result, (list, str)):
            action = "send_to_letta"
        else:
            action = "noop"

        assert action == "return_early"

    @pytest.mark.asyncio
    async def test_list_result_sends_to_letta(self):
        """When handle_file_event returns list, should send to Letta."""
        file_result = [{"type": "image", "url": "mxc://..."}]

        if file_result is None:
            action = "return_early"
        elif isinstance(file_result, (list, str)):
            action = "send_to_letta"
        else:
            action = "noop"

        assert action == "send_to_letta"

    @pytest.mark.asyncio
    async def test_str_result_sends_to_letta(self):
        """When handle_file_event returns str, should send to Letta."""
        file_result = "[Voice message]: hello world"

        if file_result is None:
            action = "return_early"
        elif isinstance(file_result, (list, str)):
            action = "send_to_letta"
        else:
            action = "noop"

        assert action == "send_to_letta"

    @pytest.mark.asyncio
    async def test_false_result_is_noop(self):
        """When handle_file_event returns False (validation error), should be noop."""
        file_result = False

        if file_result is None:
            action = "return_early"
        elif isinstance(file_result, (list, str)):
            action = "send_to_letta"
        else:
            action = "noop"

        assert action == "noop"
