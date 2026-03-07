import os
import time
from dataclasses import dataclass
from typing import Optional

from temporalio import activity

from .common import ParseError


@dataclass
class ParseInput:
    file_path: str
    file_name: str


@dataclass
class ParseResult:
    text: str
    page_count: Optional[int] = None
    was_ocr: bool = False
    char_count: int = 0
    duration_ms: int = 0


@activity.defn
async def parse_with_markitdown(input: ParseInput) -> ParseResult:
    start = time.monotonic()
    activity.logger.info(f"Parsing document: {input.file_name}")

    try:
        from src.matrix.document_parser import DocumentParseConfig, parse_document

        config = DocumentParseConfig.from_env()
        result = await parse_document(
            file_path=input.file_path,
            filename=input.file_name,
            config=config,
        )

        if result.error:
            raise ParseError(f"MarkItDown failed for {input.file_name}: {result.error}")

        text = (result.text or "").strip()
        if len(text) < 10:
            raise ParseError(
                f"Insufficient content extracted from {input.file_name} ({len(text)} chars)"
            )

        elapsed = int((time.monotonic() - start) * 1000)
        activity.logger.info(
            f"Parsed {input.file_name}: {len(text)} chars, "
            f"{result.page_count or '?'} pages, OCR={result.was_ocr}, {elapsed}ms"
        )
        return ParseResult(
            text=text,
            page_count=result.page_count,
            was_ocr=result.was_ocr,
            char_count=len(text),
            duration_ms=elapsed,
        )

    finally:
        if input.file_path and os.path.exists(input.file_path):
            os.unlink(input.file_path)
            activity.logger.debug(f"Cleaned up temp file: {input.file_path}")
