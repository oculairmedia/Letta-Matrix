"""
Document Parser — MarkItDown integration for extracting text from uploaded files.

Follows the same pattern as src/voice/transcription.py:
  audio_data → transcribe_audio() → TranscriptionResult
  file_data  → parse_document()   → DocumentParseResult

Supported formats: PDF, DOCX, PPTX, XLSX, CSV, HTML, EPUB, images, and more.
OCR fallback for scanned PDFs via PyMuPDF → Tesseract.
"""

import io
import logging
import os
import tempfile
from codecs import BOM_UTF16_BE, BOM_UTF16_LE, BOM_UTF32_BE, BOM_UTF32_LE, BOM_UTF8
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Optional, Any

# Lazy import: retry_async is imported at call site to avoid pulling in
# the heavy src.core.__init__ dependency chain (sqlalchemy, aiohttp, nio)
# which breaks the lightweight temporal-worker container.

logger = logging.getLogger("matrix_client.document_parser")


class DocumentParseRetryError(Exception):
    pass


@dataclass
class DocumentParseResult:
    """Result of document parsing, mirroring TranscriptionResult."""
    text: str
    filename: str = ""
    page_count: Optional[int] = None
    was_ocr: bool = False
    error: Optional[str] = None


@dataclass
class DocumentParseConfig:
    """Configuration for document parsing."""
    enabled: bool = True
    max_file_size_mb: int = 50
    timeout_seconds: float = 120.0
    ocr_enabled: bool = True
    ocr_dpi: int = 200
    max_text_length: int = 50000

    @classmethod
    def from_env(cls) -> "DocumentParseConfig":
        return cls(
            enabled=os.getenv("DOCUMENT_PARSING_ENABLED", "true").lower() == "true",
            max_file_size_mb=int(os.getenv("DOCUMENT_PARSING_MAX_FILE_SIZE_MB", "50")),
            timeout_seconds=float(os.getenv("DOCUMENT_PARSING_TIMEOUT_SECONDS", "120.0")),
            ocr_enabled=os.getenv("DOCUMENT_PARSING_OCR_ENABLED", "true").lower() == "true",
            ocr_dpi=int(os.getenv("DOCUMENT_PARSING_OCR_DPI", "200")),
            max_text_length=int(os.getenv("DOCUMENT_PARSING_MAX_TEXT_LENGTH", "50000")),
        )


# MIME types that MarkItDown can handle (beyond what file_handler already routes)
PARSEABLE_MIME_TYPES = {
    # Documents
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.ms-powerpoint",  # .ppt
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    "text/csv",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/html",
    "text/calendar",
    "application/xhtml+xml",
    "application/json",
    "application/epub+zip",
    # Fallback
    "application/octet-stream",
}

PARSEABLE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".csv",
    ".txt", ".md", ".markdown",
    ".html", ".htm", ".xhtml",
    ".json", ".epub", ".ics",
    ".rtf", ".odt", ".ods", ".odp",
}

TEXT_FALLBACK_EXTENSIONS = {
    ".vcf",
    ".ics",
    ".txt",
    ".csv",
    ".md",
    ".markdown",
    ".json",
    ".html",
    ".htm",
    ".xhtml",
}


def is_parseable_document(mime_type: str, filename: str) -> bool:
    """Check if a file can be parsed by MarkItDown.
    
    With the try-first approach, we accept all non-image/non-audio files.
    MarkItDown's PlainTextConverter handles any text-like file as fallback.
    The whitelist sets (PARSEABLE_MIME_TYPES, PARSEABLE_EXTENSIONS) are kept
    for reference but no longer gate file acceptance.
    """
    # Reject types handled by dedicated pipelines
    if mime_type.startswith('image/') or mime_type.startswith('audio/'):
        return False
    return True


# Dedicated process pool for CPU-bound parsing (avoids GIL contention)
# Use 4 workers for parallel document processing (can handle 4 concurrent files)
_process_pool = ProcessPoolExecutor(max_workers=4)


def _get_process_pool(recreate: bool = False) -> ProcessPoolExecutor:
    """Return a healthy process pool, recreating when requested."""
    global _process_pool
    is_broken = bool(getattr(_process_pool, "_broken", False))
    if recreate or is_broken:
        try:
            _process_pool.shutdown(wait=False, cancel_futures=True)
        except (RuntimeError, OSError) as pool_shutdown_error:
            logger.debug("Process pool shutdown during recreate failed: %s", pool_shutdown_error)
        _process_pool = ProcessPoolExecutor(max_workers=4)
    return _process_pool

# Cached MarkItDown instance per worker process (avoids re-init per file).
# MarkItDown() constructor initializes magika + requests.Session — reuse is safe.
_md_instance: Optional[Any] = None

def _convert_with_markitdown(file_path: str) -> tuple[str, Optional[int], Optional[str]]:
    """
    Synchronous MarkItDown conversion (runs in process pool).

    Uses a cached module-level MarkItDown instance to avoid re-initialization
    overhead (~100-500ms) on every file.

    Returns (text_content, page_count, error_message).
    Exceptions are caught and returned as strings to avoid pickle issues
    with traceback objects across process boundaries.
    """
    global _md_instance
    try:
        if _md_instance is None:
            from markitdown import MarkItDown
            _md_instance = MarkItDown()

        md_instance = _md_instance
        if md_instance is None:
            raise RuntimeError("MarkItDown initialization failed")

        result = md_instance.convert(file_path)
        text = (result.text_content or "").strip()

        # Try to get page count for PDFs
        page_count = None
        if file_path.lower().endswith(".pdf"):
            try:
                import fitz
                doc = fitz.open(file_path)
                page_count = len(doc)
                doc.close()
            except (ImportError, RuntimeError, ValueError, OSError) as pdf_meta_error:
                logger.debug("Failed to compute PDF page count for %s: %s", file_path, pdf_meta_error)

        return text, page_count, None
    except (ImportError, RuntimeError, ValueError, TypeError, OSError) as e:
        return "", None, f"{type(e).__name__}: {e}"

def _extract_pdf_with_fitz(file_path: str) -> tuple[str, Optional[int], Optional[str]]:
    """Extract PDF text page-by-page with PyMuPDF in a memory-efficient way."""
    try:
        import fitz

        page_texts = []
        doc = fitz.open(file_path)
        try:
            page_count = len(doc)
            for page in doc:
                page_texts.append(page.get_text() or "")
        finally:
            doc.close()

        text = "\n".join(page_texts).strip()
        return text, page_count, None
    except (ImportError, RuntimeError, ValueError, TypeError, OSError, AssertionError) as e:
        return "", None, f"{type(e).__name__}: {e}"


def _should_attempt_text_decode_fallback(filename: str, conversion_error: str) -> bool:
    """Return True when we should bypass MarkItDown and decode file bytes directly."""
    _, ext = os.path.splitext((filename or "").lower())
    if ext not in TEXT_FALLBACK_EXTENSIONS:
        return False

    lowered = (conversion_error or "").lower()
    return (
        "unicodedecodeerror" in lowered
        or "codec can't decode" in lowered
        or "ascii" in lowered
    )


def _decode_text_file_with_fallbacks(file_path: str) -> tuple[str, Optional[str]]:
    """Decode text-like files with resilient encoding fallbacks.

    This is used when MarkItDown's PlainTextConverter fails due to strict ASCII
    decoding on valid UTF-8/UTF-16 content.
    """
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return "", f"Could not read file bytes: {e}"

    if not raw:
        return "", "File is empty"

    # BOM-guided first pass
    if raw.startswith(BOM_UTF8):
        return raw.decode("utf-8-sig"), None
    if raw.startswith(BOM_UTF16_LE) or raw.startswith(BOM_UTF16_BE):
        return raw.decode("utf-16"), None
    if raw.startswith(BOM_UTF32_LE) or raw.startswith(BOM_UTF32_BE):
        return raw.decode("utf-32"), None

    errors: list[str] = []
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), None
        except UnicodeDecodeError as e:
            errors.append(f"{encoding}: {e}")

    # Last resort: decode lossy but do not fail hard.
    return raw.decode("utf-8", errors="replace"), "; ".join(errors) if errors else None


def _ocr_pdf_pages(file_path: str, dpi: int = 200) -> str:
    """
    OCR fallback: render PDF pages to images, then OCR with Tesseract.

    Returns extracted text or empty string.
    """
    try:
        import fitz
        from PIL import Image
        pytesseract = __import__("pytesseract")
    except ImportError as e:
        logger.warning(f"OCR dependencies not available: {e}")
        return ""

    texts = []
    try:
        doc = fitz.open(file_path)
        zoom = dpi / 72.0  # 72 DPI is default
        matrix = fitz.Matrix(zoom, zoom)

        for page_num in range(min(len(doc), 50)):  # Cap at 50 pages for safety
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=matrix)
            img_bytes = pix.tobytes("png")
            img = Image.open(io.BytesIO(img_bytes))
            page_text = pytesseract.image_to_string(img)
            if page_text.strip():
                texts.append(f"--- Page {page_num + 1} ---\n{page_text.strip()}")

        doc.close()
    except (RuntimeError, ValueError, TypeError, OSError) as e:
        logger.error(f"OCR processing failed: {e}", exc_info=True)
        return ""

    return "\n\n".join(texts)

def _is_text_low_quality(text: str, page_count: Optional[int] = None) -> bool:
    """
    Determine if extracted text is too low-quality to be useful.
    
    Goes beyond a simple length check to detect garbled metadata strings
    that MarkItDown may extract from scanned PDFs.
    
    Returns True if OCR fallback should be attempted.
    """
    stripped = text.strip()
    
    # No text at all
    if not stripped:
        return True
    
    # Very short text (likely just metadata headers)
    if len(stripped) < 50:
        return True
    
    # Low chars-per-page ratio — a readable page typically has 1500+ chars.
    # A scanned PDF with only embedded metadata will have very few.
    if page_count and page_count > 0:
        chars_per_page = len(stripped) / page_count
        if chars_per_page < 500:
            return True
    
    # Check text quality: ratio of alphanumeric chars to total
    # Scanned PDFs often produce garbled sequences like "%$#@\x00\x01"
    alnum_count = sum(1 for c in stripped if c.isalnum() or c.isspace())
    total = len(stripped)
    alnum_ratio = alnum_count / total if total > 0 else 0
    
    if alnum_ratio < 0.5:
        # Less than half the characters are readable — likely garbage
        return True
    
    # Check for excessive whitespace/control characters
    # Normal text has words; garbled output is often one long string or all spaces
    words = stripped.split()
    if len(words) < 5 and len(stripped) > 100:
        # Long string with very few word boundaries — probably binary noise
        return True
    
    return False

async def parse_document(
    file_path: str,
    filename: str = "document",
    config: Optional[DocumentParseConfig] = None,
) -> DocumentParseResult:
    """
    Parse a document file and extract text content.

    This is the main entry point, analogous to transcribe_audio().

    Args:
        file_path: Path to the downloaded file on disk.
        filename: Original filename (for display and extension detection).
        config: Optional parsing configuration.

    Returns:
        DocumentParseResult with extracted text or error.
    """
    import asyncio

    if config is None:
        config = DocumentParseConfig.from_env()

    if not config.enabled:
        return DocumentParseResult(
            text="", filename=filename,
            error="Document parsing is disabled",
        )

    # Check file size
    try:
        file_size = os.path.getsize(file_path)
        max_bytes = config.max_file_size_mb * 1024 * 1024
        if file_size > max_bytes:
            return DocumentParseResult(
                text="", filename=filename,
                error=f"File too large ({file_size / 1024 / 1024:.1f}MB > {config.max_file_size_mb}MB limit)",
            )
    except OSError as e:
        return DocumentParseResult(
            text="", filename=filename,
            error=f"Cannot read file: {e}",
        )

    is_pdf = file_path.lower().endswith(".pdf")

    text = ""
    page_count = None

    # Use fitz as primary extractor for PDFs (runs in main process)
    if is_pdf:
        fitz_text, fitz_page_count, fitz_error = _extract_pdf_with_fitz(file_path)
        if fitz_error:
            logger.warning(f"PyMuPDF extraction failed for {filename}: {fitz_error}")
        else:
            text = fitz_text
            page_count = fitz_page_count
            if not _is_text_low_quality(text, fitz_page_count):
                logger.info(f"PyMuPDF extraction succeeded for {filename} ({len(text)} chars, pages={page_count})")
            else:
                logger.info(
                    f"PyMuPDF returned low-quality text for {filename} ({len(text)} chars), "
                    "falling back to MarkItDown"
                )

    # Run MarkItDown conversion in a process pool with timeout + retry
    loop = asyncio.get_event_loop()
    max_attempts = 3
    last_error = None

    should_run_markitdown = (not is_pdf) or _is_text_low_quality(text, page_count)
    if should_run_markitdown:
        async def _run_markitdown_once() -> tuple[str, Optional[int]]:
            try:
                parsed_text, parsed_page_count, conv_error = await asyncio.wait_for(
                    loop.run_in_executor(_get_process_pool(), _convert_with_markitdown, file_path),
                    timeout=config.timeout_seconds,
                )
                if conv_error:
                    if _should_attempt_text_decode_fallback(filename, conv_error):
                        fallback_text, fallback_error = _decode_text_file_with_fallbacks(file_path)
                        fallback_text = (fallback_text or "").strip()
                        if fallback_text:
                            logger.info(
                                f"Direct text decode fallback succeeded for {filename} ({len(fallback_text)} chars)"
                            )
                            return fallback_text, None
                        if fallback_error:
                            logger.warning(
                                f"Direct text decode fallback failed for {filename}: {fallback_error}"
                            )

                    if "not usable anymore" in conv_error.lower():
                        _get_process_pool(recreate=True)
                    raise DocumentParseRetryError(f"Conversion failed: {conv_error}")
                else:
                    return parsed_text, parsed_page_count
            except asyncio.TimeoutError:
                raise DocumentParseRetryError(f"Parsing timed out after {config.timeout_seconds}s")
            except BrokenProcessPool as e:
                _get_process_pool(recreate=True)
                raise DocumentParseRetryError(f"Conversion failed: {e}") from e
            except (RuntimeError, ValueError, TypeError, OSError, AssertionError) as e:
                if "not usable anymore" in str(e).lower():
                    _get_process_pool(recreate=True)
                raise DocumentParseRetryError(f"Conversion failed: {e}") from e

        try:
            from src.core.retry import retry_async
            text, page_count = await retry_async(
                _run_markitdown_once,
                max_attempts=max_attempts,
                base_delay=1.0,
                operation_name=f"Document parsing for {filename}",
                logger=logger,
                retryable_exceptions=(DocumentParseRetryError,),
            )
        except DocumentParseRetryError as e:
            last_error = str(e)
            if not text:
                logger.error(f"Document parsing failed after {max_attempts} attempts for {filename}: {last_error}")
                return DocumentParseResult(
                    text="", filename=filename,
                    error=last_error or "Parsing failed after retries",
                )

    # OCR fallback for PDFs with no/useful text
    was_ocr = False
    if (
        config.ocr_enabled
        and is_pdf
        and _is_text_low_quality(text, page_count)
    ):
        logger.info(f"MarkItDown returned low-quality text for {filename} ({len(text)} chars), attempting OCR fallback")
        try:
            ocr_text = await asyncio.wait_for(
                loop.run_in_executor(_get_process_pool(), _ocr_pdf_pages, file_path, config.ocr_dpi),
                timeout=config.timeout_seconds,
            )
            if ocr_text.strip():
                text = ocr_text
                was_ocr = True
                logger.info(f"OCR fallback succeeded for {filename} ({len(text)} chars)")
            else:
                logger.warning(f"OCR fallback returned no text for {filename}")
        except asyncio.TimeoutError:
            logger.warning(f"OCR fallback timed out for {filename}")
        except (RuntimeError, ValueError, TypeError, OSError, AssertionError) as e:
            logger.warning(f"OCR fallback failed for {filename}: {e}")

    if not text:
        return DocumentParseResult(
            text="", filename=filename, page_count=page_count,
            error="No text could be extracted from the document",
        )

    # Note: No truncation applied here. Documents are chunked + embedded
    # by the Hayhooks ingest pipeline, so full text is needed for complete indexing.

    result = DocumentParseResult(
        text=text,
        filename=filename,
        page_count=page_count,
        was_ocr=was_ocr,
    )

    logger.info(
        f"Document parsed successfully: {filename} "
        f"({len(result.text)} chars, pages={page_count}, ocr={was_ocr})"
    )
    return result


def format_document_for_agent(result: DocumentParseResult, caption: Optional[str] = None) -> str:
    """
    Format parsed document text for injection into a Letta agent message.

    Analogous to how voice transcription returns "[Voice message]: {text}".

    Args:
        result: The parse result from parse_document().
        caption: Optional user caption/question about the document.

    Returns:
        Formatted string to send as the agent message.
    """
    header_parts = [f"[Document: {result.filename}]"]

    if result.page_count is not None:
        header_parts.append(f"({result.page_count} pages)")
    if result.was_ocr:
        header_parts.append("(OCR)")

    header = " ".join(header_parts)

    if result.error:
        if caption:
            return (
                f"{header}\n\n"
                f"The user uploaded a document and said: \"{caption}\"\n\n"
                f"⚠️ Document extraction failed: {result.error}\n"
                f"The document could not be read automatically. "
                f"Please acknowledge the upload and let the user know."
            )
        return (
            f"{header}\n\n"
            f"⚠️ Document extraction failed: {result.error}\n"
            f"The document could not be read automatically."
        )

    if caption:
        return (
            f"{header}\n\n"
            f"The user uploaded this document and asked: \"{caption}\"\n\n"
            f"--- Document Content ---\n"
            f"{result.text}\n"
            f"--- End Document Content ---\n\n"
            f"Please analyze the document and respond to the user's question."
        )

    return (
        f"{header}\n\n"
        f"The user has shared a document with you. Here is the extracted content:\n\n"
        f"--- Document Content ---\n"
        f"{result.text}\n"
        f"--- End Document Content ---\n\n"
        f"Please acknowledge receiving the document and summarize its key points."
    )
