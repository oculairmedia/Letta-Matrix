"""Tests for file processing performance optimizations.

Three quick wins:
1. Cached MarkItDown instance per worker (avoid re-init per file)
2. Skip MarkItDown when PyMuPDF extraction is good quality for PDFs
3. Fire-and-forget notifications in _handle_document_upload
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.matrix.document_parser import (
    DocumentParseConfig,
    DocumentParseResult,
    _convert_with_markitdown,
    _is_text_low_quality,
    parse_document,
)


# ---------------------------------------------------------------------------
# 1. Cached MarkItDown instance
# ---------------------------------------------------------------------------

class TestMarkItDownCaching:
    """MarkItDown() should be created once per worker, not per file.
    
    After optimization, document_parser.py should have:
    - A module-level _md_instance = None
    - _convert_with_markitdown() reuses _md_instance instead of creating new MarkItDown()
    """

    def test_convert_reuses_cached_instance(self):
        """_convert_with_markitdown should reuse a module-level MarkItDown instance
        instead of creating a new one on every call."""
        import src.matrix.document_parser as dp
        
        mock_result = MagicMock()
        mock_result.text_content = "Extracted text"
        mock_md = MagicMock()
        mock_md.convert.return_value = mock_result
        
        # Reset cached instance to force fresh initialization
        old_instance = getattr(dp, '_md_instance', None)
        dp._md_instance = None
        
        try:
            with patch('markitdown.MarkItDown', return_value=mock_md) as MockCls:
                text1, _, err1 = _convert_with_markitdown("/fake/file1.txt")
                text2, _, err2 = _convert_with_markitdown("/fake/file2.txt")

                assert err1 is None
                assert err2 is None
                assert text1 == "Extracted text"
                assert text2 == "Extracted text"

                # MarkItDown() constructor should be called at most once (cached)
                assert MockCls.call_count <= 1, (
                    f"MarkItDown() was instantiated {MockCls.call_count} times — "
                    f"expected at most 1 (cached instance)"
                )
                # .convert() should be called for each file
                assert mock_md.convert.call_count == 2
        finally:
            dp._md_instance = old_instance


# ---------------------------------------------------------------------------
# 2. Skip MarkItDown when PyMuPDF extraction is good quality
# ---------------------------------------------------------------------------

class TestSkipRedundantMarkItDown:
    """When PyMuPDF extracts good-quality text from a PDF,
    MarkItDown should NOT be called redundantly."""

    @pytest.fixture
    def pdf_file(self, tmp_path):
        f = tmp_path / "good.pdf"
        f.write_bytes(b"%PDF-1.4 fake content")
        return str(f)

    @pytest.fixture
    def config(self):
        return DocumentParseConfig(
            enabled=True, max_file_size_mb=10, timeout_seconds=10.0
        )

    @pytest.mark.asyncio
    async def test_good_fitz_skips_markitdown(self, pdf_file, config):
        """If PyMuPDF returns high-quality text, MarkItDown should be skipped."""
        good_text = (
            "This is a well-formatted PDF document with plenty of readable content. "
            "It contains multiple paragraphs and well-structured sentences that clearly "
            "indicate this is not a scanned document but rather a digitally created PDF. "
            "The text extraction quality is excellent."
        ) * 5  # ~1000 chars — well above quality thresholds

        with patch("src.matrix.document_parser._extract_pdf_with_fitz") as mock_fitz, \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_mid, \
             patch("src.matrix.document_parser._get_process_pool"):

            mock_fitz.return_value = (good_text, 5, None)
            # MarkItDown should not be called at all
            mock_mid.return_value = ("should not be used", 5, None)

            result = await parse_document(pdf_file, "good.pdf", config=config)

            assert result.error is None
            assert result.text == good_text
            assert result.page_count == 5
            mock_fitz.assert_called_once()
            mock_mid.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_quality_fitz_falls_through_to_markitdown(self, pdf_file, config):
        """If PyMuPDF returns low-quality text, MarkItDown SHOULD run."""
        low_text = "/Type /Page /Encoding"  # metadata-only garbage

        with patch("src.matrix.document_parser._extract_pdf_with_fitz") as mock_fitz, \
             patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_mid:

            mock_fitz.return_value = (low_text, 10, None)
            good_text = "MarkItDown extracted real content " * 20
            mock_mid.return_value = (good_text, 10, None)

            result = await parse_document(pdf_file, "scanned.pdf", config=config)

            assert result.error is None
            assert result.text == good_text
            mock_fitz.assert_called_once()
            # MarkItDown was invoked since fitz returned junk

    @pytest.mark.asyncio
    async def test_fitz_error_falls_through_to_markitdown(self, pdf_file, config):
        """If PyMuPDF fails entirely, MarkItDown should run as fallback."""
        with patch("src.matrix.document_parser._extract_pdf_with_fitz") as mock_fitz, \
             patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_mid:

            mock_fitz.return_value = ("", None, "fitz failed: corrupted PDF")
            recovered = "Recovered by MarkItDown " * 20
            mock_mid.return_value = (recovered, 3, None)

            result = await parse_document(pdf_file, "broken.pdf", config=config)

            assert result.error is None
            assert result.text == recovered
            mock_fitz.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_pdf_always_runs_markitdown(self, tmp_path, config):
        """Non-PDF files should always go through MarkItDown (no fitz shortcut)."""
        docx = tmp_path / "report.docx"
        docx.write_bytes(b"PK\x03\x04 fake docx")

        with patch("src.matrix.document_parser._extract_pdf_with_fitz") as mock_fitz, \
             patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_mid:

            mock_mid.return_value = ("Docx content " * 20, None, None)

            result = await parse_document(str(docx), "report.docx", config=config)

            assert result.error is None
            # PyMuPDF should NOT be called for non-PDF files
            mock_fitz.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Fire-and-forget notifications
# ---------------------------------------------------------------------------

class TestFireAndForgetNotify:
    """_notify() calls in _handle_document_upload should not block the pipeline."""

    @pytest.fixture
    def file_handler(self):
        with patch("src.matrix.file_handler.Letta"):
            from src.matrix.file_handler import LettaFileHandler
            handler = LettaFileHandler(
                homeserver_url="http://test-matrix.local",
                letta_api_url="http://test-letta.local",
                letta_token="test-token",
                max_retries=1,
                retry_delay=0.1,
            )
            handler._temporal_enabled = False
            return handler

    @pytest.fixture
    def metadata(self):
        from src.matrix.file_handler import FileMetadata
        return FileMetadata(
            file_url="mxc://matrix.org/abc123",
            file_name="report.pdf",
            file_type="application/pdf",
            file_size=1024,
            room_id="!test:matrix.org",
            sender="@user:matrix.org",
            timestamp=1234567890,
            event_id="$event123",
        )

    @pytest.mark.asyncio
    async def test_notify_does_not_block_pipeline(self, file_handler, metadata):
        """Notification calls should not add latency to the document processing pipeline.
        
        We verify this by making _notify sleep for a noticeable duration.
        If notifications are fire-and-forget, the pipeline completes much faster
        than it would if notifications were awaited inline.
        """
        notify_delay = 0.5  # 500ms per notification
        notify_tasks = []

        def capture_notify_task(coro):
            task = asyncio.create_task(coro)
            notify_tasks.append(task)
            return task

        async def slow_notify(room_id, message):
            await asyncio.sleep(notify_delay)
            return "$event_id"

        # Mock parse_document to return immediately with good content
        mock_parse_result = DocumentParseResult(
            text="Good content " * 100,
            filename="report.pdf",
            page_count=5,
        )

        with patch.object(file_handler, '_notify', side_effect=slow_notify), \
             patch("src.matrix.file_handler.asyncio.ensure_future", side_effect=capture_notify_task), \
             patch.object(file_handler, '_download_matrix_file', new_callable=AsyncMock, return_value="/tmp/fake.pdf"), \
             patch("src.matrix.file_handler.parse_document", new_callable=AsyncMock, return_value=mock_parse_result), \
             patch.object(file_handler, '_ingest_to_haystack', new_callable=AsyncMock, return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink"):

            import time
            start = time.monotonic()
            result = await file_handler._handle_document_upload(
                metadata, "!test:matrix.org", "agent-123"
            )
            elapsed = time.monotonic() - start

            assert result is not None, "Document upload should succeed"

            # Pipeline should complete in well under 500ms since notifications
            # are fire-and-forget (not blocking on the 500ms sleep each).
            # A blocking pipeline with 2+ notifications would take >= 1.0s.
            assert elapsed < 0.4, (
                f"Pipeline took {elapsed:.2f}s \u2014 expected <0.4s with fire-and-forget notifications. "
                f"If notifications block, this would take >= {notify_delay * 2:.1f}s."
            )

            if notify_tasks:
                await asyncio.gather(*notify_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_notify_failure_does_not_break_pipeline(self, file_handler, metadata):
        """If a fire-and-forget notification fails, document processing should still succeed."""
        notify_tasks = []

        def capture_notify_task(coro):
            task = asyncio.create_task(coro)
            notify_tasks.append(task)
            return task

        async def failing_notify(room_id, message):
            raise ConnectionError("Matrix server unavailable")

        mock_parse_result = DocumentParseResult(
            text="Good content " * 100,
            filename="report.pdf",
            page_count=5,
        )

        with patch.object(file_handler, '_notify', side_effect=failing_notify), \
             patch("src.matrix.file_handler.asyncio.ensure_future", side_effect=capture_notify_task), \
             patch.object(file_handler, '_download_matrix_file', new_callable=AsyncMock, return_value="/tmp/fake.pdf"), \
             patch("src.matrix.file_handler.parse_document", new_callable=AsyncMock, return_value=mock_parse_result), \
             patch.object(file_handler, '_ingest_to_haystack', new_callable=AsyncMock, return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("os.unlink"):

            # Should not raise — notification failures are non-fatal
            result = await file_handler._handle_document_upload(
                metadata, "!test:matrix.org", "agent-123"
            )

            assert result is not None, (
                "Document processing should succeed even when notifications fail"
            )

            if notify_tasks:
                await asyncio.gather(*notify_tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_warmup_embedder_success_is_non_fatal(self, file_handler):
        ingest_response = MagicMock(status=200)
        ingest_response.json = AsyncMock(return_value={"status": "ok", "chunks_stored": 1})

        cleanup_response = MagicMock(status=200)
        cleanup_response.text = AsyncMock(return_value="ok")

        ingest_cm = MagicMock()
        ingest_cm.__aenter__ = AsyncMock(return_value=ingest_response)
        ingest_cm.__aexit__ = AsyncMock(return_value=None)

        cleanup_cm = MagicMock()
        cleanup_cm.__aenter__ = AsyncMock(return_value=cleanup_response)
        cleanup_cm.__aexit__ = AsyncMock(return_value=None)

        session = MagicMock()
        session.post = MagicMock(side_effect=[ingest_cm, cleanup_cm])

        with patch.object(file_handler, '_get_http_session', new_callable=AsyncMock, return_value=session):
            ok = await file_handler.warm_up_ingest_embedder()

        assert ok is True
        assert file_handler._ingest_warmup_succeeded is True
        assert session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_warmup_embedder_failure_does_not_raise(self, file_handler):
        failed_response = MagicMock(status=500)
        failed_response.text = AsyncMock(return_value="failure")

        failed_cm = MagicMock()
        failed_cm.__aenter__ = AsyncMock(return_value=failed_response)
        failed_cm.__aexit__ = AsyncMock(return_value=None)

        session = MagicMock()
        session.post = MagicMock(return_value=failed_cm)

        with patch.object(file_handler, '_get_http_session', new_callable=AsyncMock, return_value=session):
            ok = await file_handler.warm_up_ingest_embedder()

        assert ok is False
        assert file_handler._ingest_warmup_succeeded is False
