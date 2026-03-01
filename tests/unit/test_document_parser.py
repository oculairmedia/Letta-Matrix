"""Tests for src/matrix/document_parser module."""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.matrix.document_parser import (
    DocumentParseConfig,
    DocumentParseResult,
    _is_text_low_quality,
    format_document_for_agent,
    is_parseable_document,
    parse_document,
)


# ---------------------------------------------------------------------------
# is_parseable_document
# ---------------------------------------------------------------------------

class TestIsParseableDocument:
    def test_pdf(self):
        assert is_parseable_document("application/pdf", "report.pdf")

    def test_docx(self):
        assert is_parseable_document(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "doc.docx",
        )

    def test_xlsx(self):
        assert is_parseable_document(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "data.xlsx",
        )

    def test_csv(self):
        assert is_parseable_document("text/csv", "data.csv")

    def test_plain_text(self):
        assert is_parseable_document("text/plain", "notes.txt")

    def test_html(self):
        assert is_parseable_document("text/html", "page.html")

    def test_epub(self):
        assert is_parseable_document("application/epub+zip", "book.epub")

    def test_octet_stream_docx(self):
        assert is_parseable_document("application/octet-stream", "doc.docx")

    def test_octet_stream_pdf(self):
        assert is_parseable_document("application/octet-stream", "report.pdf")

    def test_octet_stream_unknown(self):
        assert not is_parseable_document("application/octet-stream", "video.mp4")

    def test_video_not_parseable(self):
        assert not is_parseable_document("video/mp4", "video.mp4")

    def test_extension_fallback(self):
        """Even with unknown MIME, extension match should work."""
        assert is_parseable_document("application/x-custom", "data.csv")

    def test_pptx(self):
        assert is_parseable_document(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "slides.pptx",
        )


# ---------------------------------------------------------------------------
# DocumentParseConfig
# ---------------------------------------------------------------------------

class TestDocumentParseConfig:
    def test_defaults(self):
        config = DocumentParseConfig()
        assert config.enabled is True
        assert config.max_file_size_mb == 50
        assert config.timeout_seconds == 120.0
        assert config.ocr_enabled is True
        assert config.ocr_dpi == 200
        assert config.max_text_length == 50000

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("DOCUMENT_PARSING_ENABLED", "false")
        monkeypatch.setenv("DOCUMENT_PARSING_MAX_FILE_SIZE_MB", "25")
        monkeypatch.setenv("DOCUMENT_PARSING_TIMEOUT_SECONDS", "60.0")
        monkeypatch.setenv("DOCUMENT_PARSING_OCR_ENABLED", "false")

        config = DocumentParseConfig.from_env()
        assert config.enabled is False
        assert config.max_file_size_mb == 25
        assert config.timeout_seconds == 60.0
        assert config.ocr_enabled is False


# ---------------------------------------------------------------------------
# format_document_for_agent
# ---------------------------------------------------------------------------

class TestFormatDocumentForAgent:
    def test_successful_extraction(self):
        result = DocumentParseResult(text="Hello world", filename="test.pdf", page_count=3)
        formatted = format_document_for_agent(result)
        assert "[Document: test.pdf]" in formatted
        assert "(3 pages)" in formatted
        assert "Hello world" in formatted
        assert "--- Document Content ---" in formatted

    def test_with_caption(self):
        result = DocumentParseResult(text="Content here", filename="doc.pdf")
        formatted = format_document_for_agent(result, caption="What does this say?")
        assert "What does this say?" in formatted
        assert "Content here" in formatted

    def test_error_result(self):
        result = DocumentParseResult(text="", filename="bad.pdf", error="Conversion failed")
        formatted = format_document_for_agent(result)
        assert "Conversion failed" in formatted
        assert "⚠️" in formatted

    def test_error_with_caption(self):
        result = DocumentParseResult(text="", filename="bad.pdf", error="Failed")
        formatted = format_document_for_agent(result, caption="Read this")
        assert "Read this" in formatted
        assert "Failed" in formatted

    def test_ocr_tag(self):
        result = DocumentParseResult(text="OCR text", filename="scanned.pdf", was_ocr=True)
        formatted = format_document_for_agent(result)
        assert "(OCR)" in formatted

    def test_no_page_count(self):
        result = DocumentParseResult(text="Some text", filename="notes.txt")
        formatted = format_document_for_agent(result)
        assert "pages" not in formatted


# ---------------------------------------------------------------------------
# parse_document
# ---------------------------------------------------------------------------

class TestParseDocument:
    @pytest.fixture
    def text_file(self, tmp_path):
        """Create a temporary text file for testing."""
        f = tmp_path / "test.txt"
        f.write_text("Hello, this is test content for document parsing.")
        return str(f)

    @pytest.fixture
    def disabled_config(self):
        return DocumentParseConfig(enabled=False)

    @pytest.fixture
    def default_config(self):
        return DocumentParseConfig(enabled=True, max_file_size_mb=1, timeout_seconds=10.0)

    @pytest.mark.asyncio
    async def test_disabled(self, text_file, disabled_config):
        result = await parse_document(text_file, "test.txt", config=disabled_config)
        assert result.error == "Document parsing is disabled"
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_file_too_large(self, tmp_path):
        """File exceeding max_file_size_mb should fail."""
        f = tmp_path / "big.txt"
        # Create a file > 1KB
        f.write_text("x" * 2000)
        config = DocumentParseConfig(enabled=True, max_file_size_mb=0)  # 0 MB = reject all
        result = await parse_document(str(f), "big.txt", config=config)
        assert result.error is not None
        assert "too large" in result.error

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, default_config):
        result = await parse_document("/nonexistent/path.pdf", "missing.pdf", config=default_config)
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_successful_text_file(self, text_file, default_config):
        """Test parsing a plain text file with mocked MarkItDown."""
        with patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_convert:
            mock_convert.return_value = ("Extracted text content", None)
            result = await parse_document(text_file, "test.txt", config=default_config)

            assert result.error is None
            assert "Extracted text content" in result.text
            assert result.filename == "test.txt"

    @pytest.mark.asyncio
    async def test_empty_extraction_triggers_ocr_for_pdf(self, tmp_path):
        """When MarkItDown returns empty text for PDF, OCR should be attempted."""
        f = tmp_path / "scanned.pdf"
        f.write_bytes(b"%PDF-1.4 fake content")

        config = DocumentParseConfig(enabled=True, ocr_enabled=True, timeout_seconds=10.0, max_file_size_mb=10)

        with patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_convert, \
             patch("src.matrix.document_parser._ocr_pdf_pages") as mock_ocr:
            mock_convert.return_value = ("", 2)  # Empty text, 2 pages
            mock_ocr.return_value = "OCR extracted text"

            result = await parse_document(str(f), "scanned.pdf", config=config)

            assert result.was_ocr is True
            assert "OCR extracted text" in result.text
            mock_ocr.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncation(self, text_file):
        """Text exceeding max_text_length should be truncated."""
        config = DocumentParseConfig(enabled=True, max_text_length=20, timeout_seconds=10.0, max_file_size_mb=10)

        with patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown") as mock_convert:
            mock_convert.return_value = ("A" * 100, None)
            result = await parse_document(text_file, "test.txt", config=config)

            assert len(result.text) < 100
            assert "truncated" in result.text

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, text_file, default_config):
        """Should retry on transient failures."""
        call_count = 0

        def flaky_convert(path):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("Transient error")
            return ("Success after retry", None)

        with patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown", side_effect=flaky_convert):
            result = await parse_document(text_file, "test.txt", config=default_config)

            assert result.error is None
            assert "Success after retry" in result.text
            assert call_count == 3  # Failed twice, succeeded third time

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self, text_file, default_config):
        """When all retries fail, should return error."""
        with patch("src.matrix.document_parser._process_pool", None), \
             patch("src.matrix.document_parser._convert_with_markitdown",
                    side_effect=RuntimeError("Persistent failure")):
            result = await parse_document(text_file, "test.txt", config=default_config)

            assert result.error is not None
            assert "Persistent failure" in result.error


# ---------------------------------------------------------------------------
# _is_text_low_quality
# ---------------------------------------------------------------------------

class TestIsTextLowQuality:
    def test_empty_string(self):
        assert _is_text_low_quality("") is True

    def test_whitespace_only(self):
        assert _is_text_low_quality("   \n\t  ") is True

    def test_short_text(self):
        assert _is_text_low_quality("PDF-1.4 obj") is True

    def test_normal_text(self):
        text = "This is a normal document with readable sentences and paragraphs of content."
        assert _is_text_low_quality(text) is False

    def test_garbled_characters(self):
        text = "%$#@!^&*(){}[]|\\//" * 10  # 200 chars of garbage
        assert _is_text_low_quality(text) is True

    def test_binary_noise(self):
        # Long string with no word boundaries
        text = "x" * 200
        assert _is_text_low_quality(text) is True

    def test_borderline_quality(self):
        # Just above threshold: mostly readable with some noise
        text = "This document contains some readable text that has a few issues here and there."
        assert _is_text_low_quality(text) is False

    def test_metadata_only(self):
        # Typical PDF metadata extraction
        text = "/Type /Page /Resources /Font /F1 /Encoding"
        assert _is_text_low_quality(text) is True
