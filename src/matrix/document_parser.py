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
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("matrix_client.document_parser")


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
    ".json", ".epub",
    ".rtf", ".odt", ".ods", ".odp",
}


def is_parseable_document(mime_type: str, filename: str) -> bool:
    """Check if a file can be parsed by MarkItDown."""
    if mime_type in PARSEABLE_MIME_TYPES:
        if mime_type == "application/octet-stream":
            _, ext = os.path.splitext(filename.lower())
            return ext in PARSEABLE_EXTENSIONS
        return True
    # Also check by extension as a fallback
    _, ext = os.path.splitext(filename.lower())
    return ext in PARSEABLE_EXTENSIONS


# Dedicated process pool for CPU-bound parsing (avoids GIL contention)
_process_pool = ProcessPoolExecutor(max_workers=2)


def _convert_with_markitdown(file_path: str) -> tuple[str, Optional[int]]:
    """
    Synchronous MarkItDown conversion (runs in process pool).

    Returns (text_content, page_count).
    """
    from markitdown import MarkItDown

    md = MarkItDown()
    result = md.convert(file_path)
    text = (result.text_content or "").strip()

    # Try to get page count for PDFs
    page_count = None
    if file_path.lower().endswith(".pdf"):
        try:
            import fitz
            doc = fitz.open(file_path)
            page_count = len(doc)
            doc.close()
        except Exception:
            pass

    return text, page_count


def _ocr_pdf_pages(file_path: str, dpi: int = 200) -> str:
    """
    OCR fallback: render PDF pages to images, then OCR with Tesseract.

    Returns extracted text or empty string.
    """
    try:
        import fitz
        import pytesseract
        from PIL import Image
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
    except Exception as e:
        logger.error(f"OCR processing failed: {e}", exc_info=True)
        return ""

    return "\n\n".join(texts)

def _is_text_low_quality(text: str) -> bool:
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

    # Run MarkItDown conversion in a thread pool with timeout + retry
    loop = asyncio.get_event_loop()
    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
            text, page_count = await asyncio.wait_for(
                loop.run_in_executor(_process_pool, _convert_with_markitdown, file_path),
                timeout=config.timeout_seconds,
            )
            break  # Success
        except asyncio.TimeoutError:
            last_error = f"Parsing timed out after {config.timeout_seconds}s"
            logger.warning(f"Document parsing attempt {attempt + 1}/{max_retries} timed out for {filename}")
        except Exception as e:
            last_error = f"Conversion failed: {e}"
            logger.warning(f"Document parsing attempt {attempt + 1}/{max_retries} failed for {filename}: {e}")
        
        if attempt < max_retries - 1:
            delay = 1.0 * (2 ** attempt)  # 1s, 2s backoff
            logger.info(f"Retrying document parsing in {delay}s...")
            await asyncio.sleep(delay)
    else:
        # All retries exhausted
        logger.error(f"Document parsing failed after {max_retries} attempts for {filename}: {last_error}")
        return DocumentParseResult(
            text="", filename=filename,
            error=last_error or "Parsing failed after retries",
        )

    # OCR fallback for PDFs with no/useful text
    was_ocr = False
    if (
        config.ocr_enabled
        and file_path.lower().endswith(".pdf")
        and _is_text_low_quality(text)
    ):
        logger.info(f"MarkItDown returned low-quality text for {filename} ({len(text)} chars), attempting OCR fallback")
        try:
            ocr_text = await asyncio.wait_for(
                loop.run_in_executor(_process_pool, _ocr_pdf_pages, file_path, config.ocr_dpi),
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
        except Exception as e:
            logger.warning(f"OCR fallback failed for {filename}: {e}")

    if not text:
        return DocumentParseResult(
            text="", filename=filename, page_count=page_count,
            error="No text could be extracted from the document",
        )

    # Truncate if needed
    truncated = False
    if len(text) > config.max_text_length:
        text = text[:config.max_text_length]
        truncated = True
        logger.info(f"Truncated extracted text for {filename} to {config.max_text_length} chars")

    # Build final result
    result = DocumentParseResult(
        text=text,
        filename=filename,
        page_count=page_count,
        was_ocr=was_ocr,
    )

    # Add truncation note to text
    if truncated:
        result.text += f"\n\n[... truncated at {config.max_text_length} characters]"

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
