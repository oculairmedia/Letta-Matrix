import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from temporalio import activity

from .common import IngestError

HAYHOOKS_INGEST_URL = os.getenv(
    "HAYHOOKS_INGEST_URL", "http://192.168.50.90:1416/ingest_document/run"
)
HAYHOOKS_DELETE_BY_FILENAME_URL = os.getenv(
    "HAYHOOKS_DELETE_BY_FILENAME_URL",
    HAYHOOKS_INGEST_URL.replace("/ingest_document/run", "/delete_by_filename/run"),
)


def _normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[\t\f\v ]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _presplit_text(text: str) -> list[str]:
    threshold = int(os.getenv("HAYHOOKS_INGEST_PRESPLIT_THRESHOLD_CHARS", "500000"))
    section_chars = int(os.getenv("HAYHOOKS_INGEST_SECTION_CHARS", "200000"))

    if len(text) <= threshold:
        return [text]

    parts: list[str] = []
    paragraphs = text.split("\n\n")
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        para = paragraph.strip()
        if not para:
            continue
        extra_len = len(para) + (2 if current else 0)
        if current and current_len + extra_len > section_chars:
            parts.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += extra_len

    if current:
        parts.append("\n\n".join(current))

    return parts or [text]


@dataclass
class IngestInput:
    text: str
    filename: str
    room_id: str
    sender: str


@dataclass
class IngestResult:
    success: bool
    chunks_stored: int = 0
    duration_ms: int = 0
    error: Optional[str] = None


@activity.defn
async def ingest_to_haystack(input: IngestInput) -> IngestResult:
    start = time.monotonic()
    normalized_text = _normalize_text(input.text)
    sections = _presplit_text(normalized_text)
    activity.logger.info(
        f"Ingesting {input.filename} ({len(normalized_text)} chars) to Haystack "
        f"across {len(sections)} section(s)"
    )

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            delete_enabled = os.getenv("HAYHOOKS_DELETE_BEFORE_INGEST", "true").lower() in (
                "true",
                "1",
                "yes",
            )
            if delete_enabled:
                try:
                    delete_resp = await client.post(
                        HAYHOOKS_DELETE_BY_FILENAME_URL,
                        json={
                            "source_filename": input.filename,
                            "room_id": input.room_id,
                        },
                    )
                    if delete_resp.status_code == 404:
                        activity.logger.debug(
                            f"Delete endpoint not found or no docs to delete for {input.filename}, skipping"
                        )
                    elif delete_resp.status_code != 200:
                        activity.logger.warning(
                            f"Hayhooks delete HTTP {delete_resp.status_code} for {input.filename}: "
                            f"{delete_resp.text[:200]} — continuing with ingest"
                        )
                except httpx.HTTPError as e:
                    activity.logger.warning(
                        f"Hayhooks delete failed for {input.filename}: {e} — continuing with ingest"
                    )

            total_chunks = 0
            total_sections = len(sections)

            for idx, section in enumerate(sections, start=1):
                section_filename = (
                    input.filename
                    if total_sections == 1
                    else f"{input.filename} (part {idx}/{total_sections})"
                )
                payload = {
                    "text": section,
                    "filename": section_filename,
                    "room_id": input.room_id,
                    "sender": input.sender,
                }

                response = await client.post(HAYHOOKS_INGEST_URL, json=payload)

                if response.status_code != 200:
                    raise IngestError(
                        f"Hayhooks HTTP {response.status_code} for {section_filename}: "
                        f"{response.text[:500]}"
                    )

                result = response.json()
                result_data = result
                if isinstance(result.get("result"), str):
                    result_data = json.loads(result["result"])

                status = result_data.get("status", "")
                if status != "ok":
                    detail = result_data.get("detail", "Unknown error")
                    raise IngestError(
                        f"Hayhooks ingest error for {section_filename}: {detail}"
                    )

                total_chunks += int(result_data.get("chunks_stored", 0) or 0)

            elapsed = int((time.monotonic() - start) * 1000)
            activity.logger.info(
                f"Ingested {input.filename}: {total_chunks} chunks stored, {elapsed}ms"
            )
            return IngestResult(success=True, chunks_stored=total_chunks, duration_ms=elapsed)

    except IngestError:
        raise
    except httpx.TimeoutException as e:
        raise IngestError(f"Hayhooks timeout for {input.filename}: {e}") from e
    except Exception as e:
        raise IngestError(f"Unexpected error ingesting {input.filename}: {e}") from e
