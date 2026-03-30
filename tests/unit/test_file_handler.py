"""Unit tests for src/matrix/file_handler.py - image and document upload handling."""

import base64
import os
import tempfile
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch, MagicMock

import pytest

from src.matrix.file_download import FileMetadata
from src.matrix.document_parser import DocumentParseResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_notify_callback():
    """Async mock for notify callback."""
    return AsyncMock(return_value="$event-123")


@pytest.fixture
def file_metadata_factory():
    """Factory to create FileMetadata instances."""
    def _create(
        file_url="mxc://test.com/file",
        file_name="test.png",
        file_type="image/png",
        file_size=1024,
        room_id="!room:test.com",
        sender="@user:test.com",
        timestamp=1700000000,
        event_id="$evt-1",
        caption=None,
    ):
        return FileMetadata(
            file_url=file_url,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            room_id=room_id,
            sender=sender,
            timestamp=timestamp,
            event_id=event_id,
            caption=caption,
        )
    return _create


# ---------------------------------------------------------------------------
# _handle_image_upload tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_image_upload_encodes_base64_and_returns_multimodal(
    mock_notify_callback, file_metadata_factory
):
    """Test that image upload encodes file as base64 and returns multimodal content."""
    from src.matrix.file_handler import LettaFileHandler
    
    # Create a temporary image file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"\x89PNG\r\n\x1a\nfake-png-data")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="test.png",
            file_type="image/png",
        )
        
        # Mock the _downloaded_file context manager
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file):
            result = await handler._handle_image_upload(
                metadata=metadata,
                room_id="!room:test.com",
                agent_id="agent-123",
            )
        
        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 2
        
        # Check text part
        assert result[0]["type"] == "text"
        assert "Image Upload: test.png" in result[0]["text"]
        
        # Check image part
        assert result[1]["type"] == "image"
        assert result[1]["source"]["type"] == "base64"
        assert result[1]["source"]["media_type"] == "image/png"
        
        # Verify base64 encoding is correct
        expected_b64 = base64.standard_b64encode(b"\x89PNG\r\n\x1a\nfake-png-data").decode("utf-8")
        assert result[1]["source"]["data"] == expected_b64
        
        # Verify notification was sent
        mock_notify_callback.assert_awaited_once()
        call_args = mock_notify_callback.await_args
        assert "!room:test.com" in str(call_args)
        assert "test.png" in str(call_args)
        
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_handle_image_upload_includes_caption_in_message(
    mock_notify_callback, file_metadata_factory
):
    """Test that user caption is included in the multimodal message text."""
    from src.matrix.file_handler import LettaFileHandler
    
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(b"fake-jpg-data")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="photo.jpg",
            file_type="image/jpeg",
            caption="What is in this image?",
        )
        
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file):
            result = await handler._handle_image_upload(
                metadata=metadata,
                room_id="!room:test.com",
            )
        
        assert result is not None
        message_text = result[0]["text"]
        assert "What is in this image?" in message_text
        assert "asked:" in message_text.lower() or "question" in message_text.lower()
        
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_handle_image_upload_wraps_opencode_sender_routing(
    mock_notify_callback, file_metadata_factory
):
    """Test that OpenCode sender gets @mention routing instruction injected."""
    from src.matrix.file_handler import LettaFileHandler
    
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(b"fake-image")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="screenshot.png",
            file_type="image/png",
            sender="@oc_myproject:test.com",  # OpenCode sender
        )
        
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file):
            result = await handler._handle_image_upload(
                metadata=metadata,
                room_id="!room:test.com",
            )
        
        assert result is not None
        message_text = result[0]["text"]
        # OpenCode routing should include @mention instruction
        assert "@oc_myproject" in message_text or "@oc_" in message_text or "mention" in message_text.lower()
        
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# _handle_document_upload tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_document_upload_parses_and_returns_summary(
    mock_notify_callback, file_metadata_factory
):
    """Test successful document parsing returns agent notification."""
    from src.matrix.file_handler import LettaFileHandler
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"fake-pdf-content-for-testing")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="report.pdf",
            file_type="application/pdf",
        )
        
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        parse_result = DocumentParseResult(
            text="This is the extracted document content. " * 50,  # Long enough to pass length check
            filename="report.pdf",
            page_count=5,
            was_ocr=False,
            error=None,
        )
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file), \
             patch("src.matrix.file_handler.parse_document", new_callable=AsyncMock) as mock_parse, \
             patch("src.matrix.file_handler.build_outline_record") as mock_build_outline, \
             patch("src.matrix.file_handler.upsert_outline_record") as mock_upsert_outline, \
             patch.object(handler, "_ingest_to_haystack", new_callable=AsyncMock, return_value=True):
            
            mock_parse.return_value = parse_result
            mock_build_outline.return_value = Mock()
            
            result = await handler._handle_document_upload(
                metadata=metadata,
                room_id="!room:test.com",
                agent_id="agent-456",
            )
        
        assert result is not None
        assert "Document Indexed: report.pdf" in result
        assert "search_documents" in result
        mock_parse.assert_awaited_once()
        
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_handle_document_upload_returns_none_on_parse_error(
    mock_notify_callback, file_metadata_factory
):
    """Test that parsing failure returns None for Letta source fallback."""
    from src.matrix.file_handler import LettaFileHandler
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"corrupted-pdf")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="broken.pdf",
            file_type="application/pdf",
        )
        
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        parse_result = DocumentParseResult(
            text="",
            filename="broken.pdf",
            error="Failed to parse PDF: corrupted data",
        )
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file), \
             patch("src.matrix.file_handler.parse_document", new_callable=AsyncMock) as mock_parse:
            
            mock_parse.return_value = parse_result
            
            result = await handler._handle_document_upload(
                metadata=metadata,
                room_id="!room:test.com",
                agent_id="agent-456",
            )
        
        # Should return None on parse failure so caller can fall back
        assert result is None
        
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_handle_document_upload_returns_none_on_short_content(
    mock_notify_callback, file_metadata_factory
):
    """Test that very short extracted content returns None."""
    from src.matrix.file_handler import LettaFileHandler
    
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(b"short")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="tiny.txt",
            file_type="text/plain",
        )
        
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        # Content too short (< 10 chars)
        parse_result = DocumentParseResult(
            text="short",
            filename="tiny.txt",
            error=None,
        )
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file), \
             patch("src.matrix.file_handler.parse_document", new_callable=AsyncMock) as mock_parse:
            
            mock_parse.return_value = parse_result
            
            result = await handler._handle_document_upload(
                metadata=metadata,
                room_id="!room:test.com",
                agent_id="agent-456",
            )
        
        assert result is None
        
    finally:
        os.unlink(tmp_path)


@pytest.mark.asyncio
async def test_handle_document_upload_includes_caption_in_agent_message(
    mock_notify_callback, file_metadata_factory
):
    """Test that user caption is included in agent notification."""
    from src.matrix.file_handler import LettaFileHandler
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"document content here for testing")
        tmp_path = tmp.name
    
    try:
        handler = LettaFileHandler(
            homeserver_url="https://test.com",
            letta_api_url="https://letta.test.com",
            letta_token="test-token",
            notify_callback=mock_notify_callback,
        )
        
        metadata = file_metadata_factory(
            file_name="contract.pdf",
            file_type="application/pdf",
            caption="Can you find the termination clause?",
        )
        
        @asynccontextmanager
        async def mock_downloaded_file(meta):
            yield tmp_path
        
        parse_result = DocumentParseResult(
            text="This is a long contract document with many clauses. " * 20,
            filename="contract.pdf",
            page_count=10,
            error=None,
        )
        
        with patch.object(handler, "_downloaded_file", mock_downloaded_file), \
             patch("src.matrix.file_handler.parse_document", new_callable=AsyncMock) as mock_parse, \
             patch("src.matrix.file_handler.build_outline_record") as mock_build_outline, \
             patch("src.matrix.file_handler.upsert_outline_record") as mock_upsert_outline, \
             patch.object(handler, "_ingest_to_haystack", new_callable=AsyncMock, return_value=True):
            
            mock_parse.return_value = parse_result
            mock_build_outline.return_value = Mock()
            
            result = await handler._handle_document_upload(
                metadata=metadata,
                room_id="!room:test.com",
                agent_id="agent-456",
            )
        
        assert result is not None
        assert "termination clause" in result
        assert "search_documents" in result
        
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# _notify tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_calls_callback_with_room_and_message():
    """Test _notify invokes callback with correct args."""
    from src.matrix.file_handler import LettaFileHandler
    
    callback = AsyncMock(return_value="$evt-notify-1")
    handler = LettaFileHandler(
        homeserver_url="https://test.com",
        letta_api_url="https://letta.test.com",
        letta_token="test-token",
        notify_callback=callback,
    )
    
    result = await handler._notify("!room:server", "Processing file...")
    
    assert result == "$evt-notify-1"
    callback.assert_awaited_once_with("!room:server", "Processing file...")


@pytest.mark.asyncio
async def test_notify_returns_none_when_no_callback():
    """Test _notify returns None gracefully when no callback configured."""
    from src.matrix.file_handler import LettaFileHandler
    
    handler = LettaFileHandler(
        homeserver_url="https://test.com",
        letta_api_url="https://letta.test.com",
        letta_token="test-token",
        notify_callback=None,
    )
    
    result = await handler._notify("!room:server", "Test message")
    
    assert result is None


@pytest.mark.asyncio
async def test_notify_handles_callback_exception_gracefully():
    """Test _notify catches and logs callback exceptions."""
    from src.matrix.file_handler import LettaFileHandler
    
    failing_callback = AsyncMock(side_effect=RuntimeError("Network error"))
    handler = LettaFileHandler(
        homeserver_url="https://test.com",
        letta_api_url="https://letta.test.com",
        letta_token="test-token",
        notify_callback=failing_callback,
    )
    
    result = await handler._notify("!room:server", "Test message")
    
    # Should not raise, should return None on error
    assert result is None
    failing_callback.assert_awaited_once()
