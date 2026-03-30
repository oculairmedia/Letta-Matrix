"""
Haystack document ingestion — POST extracted text to Hayhooks pipeline
for chunking, embedding, and storage in Weaviate.
"""

import asyncio
import logging
import os
import re
import time
import uuid

import aiohttp

logger = logging.getLogger("matrix_client.file_handler")


class HaystackIngestMixin:
    """Haystack ingestion methods mixed into LettaFileHandler."""

    async def _ingest_to_haystack(self, text: str, filename: str, room_id: str, sender: str) -> bool:
        """POST extracted document text to the Hayhooks ingest_document pipeline."""
        if not self._ingest_warmup_attempted:
            await self.warm_up_ingest_embedder()

        ingest_started_at = time.monotonic()

        hayhooks_url = os.getenv(
            "HAYHOOKS_INGEST_URL",
            "http://192.168.50.90:1416/ingest_document/run"
        )
        delete_by_filename_url = os.getenv(
            "HAYHOOKS_DELETE_BY_FILENAME_URL",
            hayhooks_url.replace("/ingest_document/run", "/delete_by_filename/run"),
        )

        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized_text = re.sub(r"[\t\f\v ]+", " ", normalized_text)
        normalized_text = re.sub(r"\n{3,}", "\n\n", normalized_text).strip()

        threshold = int(os.getenv("HAYHOOKS_INGEST_PRESPLIT_THRESHOLD_CHARS", "500000"))
        section_chars = int(os.getenv("HAYHOOKS_INGEST_SECTION_CHARS", "200000"))

        sections: list[str] = []
        if len(normalized_text) <= threshold:
            sections = [normalized_text]
        else:
            current: list[str] = []
            current_len = 0
            for paragraph in normalized_text.split("\n\n"):
                para = paragraph.strip()
                if not para:
                    continue
                extra_len = len(para) + (2 if current else 0)
                if current and current_len + extra_len > section_chars:
                    sections.append("\n\n".join(current))
                    current = [para]
                    current_len = len(para)
                else:
                    current.append(para)
                    current_len += extra_len
            if current:
                sections.append("\n\n".join(current))

            if not sections:
                sections = [normalized_text]

        try:
            session = await self._get_http_session()
            delete_enabled = os.getenv("HAYHOOKS_DELETE_BEFORE_INGEST", "true").lower() in (
                "true", "1", "yes",
            )
            if delete_enabled:
                async with session.post(
                    delete_by_filename_url,
                    json={"source_filename": filename, "room_id": room_id},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as delete_response:
                    if delete_response.status != 200:
                        error_text = await delete_response.text()
                        logger.error(
                            f"Hayhooks delete-by-filename failed for {filename}: "
                            f"HTTP {delete_response.status} - {error_text[:500]}"
                        )
                        return False

            total_chunks = 0
            total_sections = len(sections)

            for idx, section in enumerate(sections, start=1):
                section_filename = (
                    filename
                    if total_sections == 1
                    else f"{filename} (part {idx}/{total_sections})"
                )
                payload = {
                    "text": section,
                    "filename": section_filename,
                    "room_id": room_id,
                    "sender": sender,
                }

                async with session.post(
                    hayhooks_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=600),
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            f"Hayhooks ingest failed for {section_filename}: "
                            f"HTTP {response.status} - {error_text[:500]}"
                        )
                        return False

                    result = await response.json()

                    import json
                    result_data = result
                    if isinstance(result.get("result"), str):
                        result_data = json.loads(result["result"])

                    status = result_data.get("status", "")
                    if status != "ok":
                        detail = result_data.get("detail", "Unknown error")
                        logger.error(
                            f"Hayhooks ingest error for {section_filename}: {detail}"
                        )
                        return False

                    total_chunks += int(result_data.get("chunks_stored", 0) or 0)

            logger.info(
                f"Document '{filename}' ingested successfully: "
                f"{total_chunks} chunks stored in Weaviate "
                f"(ingest_ms={(time.monotonic() - ingest_started_at) * 1000:.1f}, "
                f"first_ingest={not self._first_ingest_logged}, warmup={self._ingest_warmup_succeeded})"
            )
            self._first_ingest_logged = True
            return True

        except asyncio.TimeoutError:
            logger.error(f"Hayhooks ingest timed out for {filename} (120s)")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"Hayhooks connection error for {filename}: {e}")
            return False
        except (ValueError, KeyError, TypeError, RuntimeError) as e:
            logger.error(f"Unexpected error ingesting {filename} to Haystack: {e}", exc_info=True)
            return False

    async def warm_up_ingest_embedder(self) -> bool:
        if self._ingest_warmup_attempted:
            return self._ingest_warmup_succeeded

        self._ingest_warmup_attempted = True

        enabled = os.getenv("HAYHOOKS_EMBEDDER_WARMUP_ENABLED", "true").lower() in (
            "true", "1", "yes",
        )
        if not enabled:
            logger.info("[HAYHOOKS-WARMUP] Warm-up disabled")
            return False

        hayhooks_url = os.getenv(
            "HAYHOOKS_INGEST_URL",
            "http://192.168.50.90:1416/ingest_document/run",
        )
        delete_by_filename_url = os.getenv(
            "HAYHOOKS_DELETE_BY_FILENAME_URL",
            hayhooks_url.replace("/ingest_document/run", "/delete_by_filename/run"),
        )
        warmup_filename = os.getenv(
            "HAYHOOKS_EMBEDDER_WARMUP_FILENAME",
            f"__embedder_warmup__{uuid.uuid4().hex}.txt",
        )
        warmup_text = os.getenv("HAYHOOKS_EMBEDDER_WARMUP_TEXT", "warmup")
        warmup_room_id = os.getenv("HAYHOOKS_EMBEDDER_WARMUP_ROOM_ID", "!warmup:matrix.local")
        warmup_sender = os.getenv("HAYHOOKS_EMBEDDER_WARMUP_SENDER", "@warmup:matrix.local")

        started_at = time.monotonic()
        try:
            session = await self._get_http_session()
            payload = {
                "text": warmup_text,
                "filename": warmup_filename,
                "room_id": warmup_room_id,
                "sender": warmup_sender,
            }

            async with session.post(
                hayhooks_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(
                        f"[HAYHOOKS-WARMUP] Warm-up ingest failed: HTTP {response.status} - {error_text[:300]}"
                    )
                    return False

                result = await response.json()
                result_data = result
                if isinstance(result.get("result"), str):
                    import json
                    result_data = json.loads(result["result"])

                if result_data.get("status") != "ok":
                    logger.warning(
                        f"[HAYHOOKS-WARMUP] Warm-up ingest returned non-ok status: {result_data}"
                    )
                    return False

            self._ingest_warmup_succeeded = True
            logger.info(
                f"[HAYHOOKS-WARMUP] Warm-up completed in {(time.monotonic() - started_at) * 1000:.1f}ms"
            )

            cleanup_enabled = os.getenv("HAYHOOKS_EMBEDDER_WARMUP_CLEANUP", "true").lower() in (
                "true", "1", "yes",
            )
            if cleanup_enabled:
                async with session.post(
                    delete_by_filename_url,
                    json={"source_filename": warmup_filename, "room_id": warmup_room_id},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as delete_response:
                    if delete_response.status != 200:
                        delete_error = await delete_response.text()
                        logger.warning(
                            f"[HAYHOOKS-WARMUP] Cleanup failed: HTTP {delete_response.status} - {delete_error[:300]}"
                        )

            return True
        except (asyncio.TimeoutError, aiohttp.ClientError, ValueError, KeyError, TypeError, RuntimeError) as e:
            logger.warning(f"[HAYHOOKS-WARMUP] Warm-up failed (non-fatal): {e}")
            return False
