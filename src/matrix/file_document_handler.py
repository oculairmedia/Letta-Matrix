"""
Document upload handling — parsing, Haystack ingestion, multimodal messaging.
"""

import asyncio
import logging
from typing import Any, Optional

import aiohttp

from src.core.document_outline_index import build_outline_record, upsert_outline_record
from src.core.retry import retry_async
from src.matrix.document_parser import (
    DocumentParseResult,
    format_document_for_agent,
    parse_document,
)
from src.matrix.file_download import FileMetadata
from src.matrix.formatter import wrap_opencode_routing

logger = logging.getLogger("matrix_client.file_handler")


class FileDocumentHandlerMixin:
    """Document upload methods mixed into LettaFileHandler."""

    async def _handle_document_upload(self, metadata: FileMetadata, room_id: str, agent_id: Optional[str] = None) -> Optional[str]:
        """Handle document upload by extracting text with MarkItDown,
        then ingesting into the shared Haystack document store."""
        self._notify_bg(room_id, f"📄 Reading document: {metadata.file_name}...")

        async with self._downloaded_file(metadata) as file_path:
            result = await parse_document(
                file_path=file_path,
                filename=metadata.file_name,
                config=self.document_parsing_config,
            )

            if result.error:
                self._status_summary = f"⚠️ {metadata.file_name} — extraction failed"
                logger.warning(f"Document parsing failed for {metadata.file_name}: {result.error}")
                self._notify_bg(room_id, f"⚠️ Could not extract text from {metadata.file_name}: {result.error}")
                return None

            text_len = len((result.text or "").strip())
            if text_len < 10:
                logger.info(f"MarkItDown returned insufficient content for {metadata.file_name} ({text_len} chars)")
                return None

            page_info = f" ({result.page_count} pages)" if result.page_count else ""
            ocr_info = " (OCR)" if result.was_ocr else ""
            char_count = len(result.text)

            outline_record = build_outline_record(
                document_id=f"{room_id}:{metadata.event_id}",
                filename=metadata.file_name,
                room_id=room_id,
                sender=metadata.sender or "",
                event_id=metadata.event_id,
                text=result.text,
                page_count=result.page_count,
                was_ocr=result.was_ocr,
            )
            try:
                upsert_outline_record(outline_record)
            except (RuntimeError, ValueError, TypeError, OSError) as outline_error:
                logger.warning(f"Failed to persist document outline for {metadata.file_name}: {outline_error}")

            ingest_success = await self._ingest_to_haystack(
                text=result.text,
                filename=metadata.file_name,
                room_id=room_id,
                sender=metadata.sender or "",
            )

            if ingest_success:
                self._notify_bg(
                    room_id,
                    f"✅ Document indexed: {metadata.file_name}{page_info}{ocr_info} — {char_count} chars stored in shared document library"
                )
                self._status_summary = f"📄 {metadata.file_name}{page_info}{ocr_info} — {char_count:,} chars indexed ✓"
                caption_note = ""
                if metadata.caption:
                    caption_note = f"\n\nThe user asked: \"{metadata.caption}\"\nUse the search_documents tool to find relevant content and answer their question."

                agent_msg = (
                    f"[Document Indexed: {metadata.file_name}]{page_info}{ocr_info}\n\n"
                    f"This document ({char_count} chars) has been indexed into the shared document library. "
                    f"Use the **search_documents** tool with a relevant query to find specific content from this document."
                    f"{caption_note}"
                )
            else:
                logger.warning(f"Haystack ingest failed for {metadata.file_name}, falling back to direct text")
                self._notify_bg(
                    room_id,
                    f"⚠️ Document store unavailable, sending text directly: {metadata.file_name}{page_info}{ocr_info}"
                )
                self._status_summary = f"⚠️ {metadata.file_name}{page_info}{ocr_info} — sent directly (document store unavailable)"
                truncated_text = result.text[:8000]
                if len(result.text) > 8000:
                    truncated_text += f"\n\n[... truncated from {char_count} chars. Document store was unavailable for full indexing.]"
                agent_msg = format_document_for_agent(
                    DocumentParseResult(text=truncated_text, filename=result.filename, page_count=result.page_count, was_ocr=result.was_ocr),
                    caption=metadata.caption,
                )

            if metadata.sender and metadata.sender.startswith("@oc_"):
                agent_msg = wrap_opencode_routing(agent_msg, metadata.sender)
                logger.info("[OPENCODE-DOC] Injected @mention instruction for document upload")

            logger.info(f"Document handling complete for {metadata.file_name}, returning {len(agent_msg)} chars to agent")
            return agent_msg

    async def _send_multimodal_message(self, agent_id: str, content: list) -> Optional[Any]:
        """Send a multimodal message (with images) to a Letta agent."""
        try:
            async def _do_send():
                return await self._run_sync(
                    self.letta_client.agents.messages.create,
                    agent_id=agent_id,
                    messages=[{
                        "role": "user",
                        "content": content
                    }]
                )

            response = await retry_async(
                _do_send,
                operation_name="Multimodal message send",
                max_attempts=self.max_retries,
                base_delay=self.retry_delay,
                logger=logger,
            )
            logger.debug(f"Multimodal message response: {type(response)}")
            return response

        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError, OSError) as e:
            logger.error(f"Error sending multimodal message: {e}", exc_info=True)
            return None

    def _extract_assistant_response(self, response: Any) -> Optional[str]:
        """Extract assistant message text from Letta response."""
        try:
            if hasattr(response, 'messages'):
                messages = response.messages
            elif hasattr(response, 'model_dump'):
                data = response.model_dump()
                messages = data.get('messages', [])
            elif isinstance(response, dict):
                messages = response.get('messages', [])
            else:
                return None

            assistant_texts = []
            for msg in messages:
                if hasattr(msg, 'message_type'):
                    msg_type = msg.message_type
                    if msg_type == 'assistant_message' and hasattr(msg, 'content'):
                        assistant_texts.append(str(msg.content))
                elif isinstance(msg, dict):
                    msg_type = msg.get('message_type')
                    if msg_type == 'assistant_message':
                        content = msg.get('content')
                        if content:
                            assistant_texts.append(str(content))

            if assistant_texts:
                return '\n'.join(assistant_texts)
            return None

        except (AttributeError, KeyError, ValueError, TypeError) as e:
            logger.error(f"Error extracting assistant response: {e}")
            return None
