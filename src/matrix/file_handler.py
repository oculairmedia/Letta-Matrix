"""
Matrix File Upload Handler — slim orchestrator.

Domain logic lives in focused mixin modules:
  - file_image_handler: image upload → base64 multimodal
  - file_audio_handler: audio upload → voice transcription
  - file_document_handler: document parsing, multimodal messaging
  - haystack_ingest: Hayhooks/Weaviate document ingestion
  - temporal_file_workflow: Temporal async file processing
"""

import asyncio
import functools
import logging
import os
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, Awaitable, Union
import aiohttp
from nio import Event
from src.matrix.file_download import (
    FileMetadata,
    FileUploadError,
    FileDownloadService,
    SUPPORTED_FILE_TYPES,
    SUPPORTED_EXTENSIONS,
    MAX_FILE_SIZE,
)
from src.matrix.document_parser import DocumentParseConfig
from src.matrix.letta_source_manager import LettaSourceManager  # pyright: ignore[reportMissingImports]
from src.matrix.file_image_handler import FileImageHandlerMixin
from src.matrix.file_audio_handler import FileAudioHandlerMixin
from src.matrix.file_document_handler import FileDocumentHandlerMixin
from src.matrix.haystack_ingest import HaystackIngestMixin
from src.matrix.temporal_file_workflow import TemporalFileWorkflowMixin

from letta_client import Letta

logger = logging.getLogger("matrix_client.file_handler")

DEFAULT_EMBEDDING_MODEL = "letta/letta-free"

_executor = ThreadPoolExecutor(max_workers=4)
_executor_semaphore = asyncio.Semaphore(4)


class LettaFileHandler(
    FileImageHandlerMixin,
    FileAudioHandlerMixin,
    FileDocumentHandlerMixin,
    HaystackIngestMixin,
    TemporalFileWorkflowMixin,
):
    """Handles Matrix file uploads and Letta integration using Letta SDK"""

    def __init__(
        self,
        homeserver_url: str,
        letta_api_url: str,
        letta_token: str,
        matrix_access_token: Optional[str] = None,
        notify_callback: Optional[Callable[[str, str], Awaitable[Optional[str]]]] = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_endpoint: Optional[str] = None,
        embedding_endpoint_type: str = "openai",
        embedding_dim: int = 1536,
        embedding_chunk_size: int = 300,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        document_parsing_config: Optional[DocumentParseConfig] = None
    ):
        self.homeserver_url = homeserver_url
        self.letta_api_url = letta_api_url
        self.letta_token = letta_token
        self.matrix_access_token = matrix_access_token
        self._download_service = FileDownloadService(homeserver_url, matrix_access_token, logger)
        self.notify_callback = notify_callback
        self.embedding_model = embedding_model
        self.embedding_endpoint = embedding_endpoint
        self.embedding_endpoint_type = embedding_endpoint_type
        self.embedding_dim = embedding_dim
        self.embedding_chunk_size = embedding_chunk_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._pending_cleanup_event_ids: list = []
        self._status_summary: Optional[str] = None
        self._ingest_warmup_attempted = False
        self._ingest_warmup_succeeded = False
        self._first_ingest_logged = False

        self._temporal_client = None
        self._temporal_lock = asyncio.Lock()
        self._temporal_enabled = os.environ.get('TEMPORAL_FILE_PROCESSING_ENABLED', 'false').lower() in ('true', '1', 'yes')
        if self._temporal_enabled:
            try:
                import temporalio  # noqa: F401
                logger.info("Temporal async file processing: ENABLED")
            except ImportError:
                logger.warning("TEMPORAL_FILE_PROCESSING_ENABLED=true but temporalio not installed — falling back to inline")
                self._temporal_enabled = False
        else:
            logger.info("Temporal async file processing: disabled (set TEMPORAL_FILE_PROCESSING_ENABLED=true to enable)")

        self.letta_client = Letta(base_url=letta_api_url, api_key=letta_token)

        config_defaults = {
            "embedding_model": embedding_model,
            "embedding_endpoint": embedding_endpoint,
            "embedding_endpoint_type": embedding_endpoint_type,
            "embedding_dim": embedding_dim,
            "embedding_chunk_size": embedding_chunk_size,
            "max_retries": max_retries,
            "retry_delay": retry_delay,
        }
        self._source_manager = LettaSourceManager(
            self.letta_client,
            config_defaults,
            logger,
        )
        self._source_cache = self._source_manager._source_cache
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._http_session_lock = asyncio.Lock()

        logger.info(f"LettaFileHandler initialized - matrix_access_token present: {bool(self.matrix_access_token)}, length: {len(self.matrix_access_token) if self.matrix_access_token else 0}")
        logger.info(f"Embedding config: model={embedding_model}, endpoint={embedding_endpoint}, dim={embedding_dim}")
        logger.info(f"Using Letta SDK for API calls")
        self.document_parsing_config: DocumentParseConfig = (
            document_parsing_config or DocumentParseConfig.from_env()
        )
        logger.info(f"Document parsing: enabled={self.document_parsing_config.enabled}, ocr={self.document_parsing_config.ocr_enabled}")

    @property
    def source_manager(self) -> LettaSourceManager:
        return self._source_manager

    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        async with _executor_semaphore:
            return await loop.run_in_executor(
                _executor,
                functools.partial(func, *args, **kwargs)
            )

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is not None and not self._http_session.closed:
            return self._http_session

        async with self._http_session_lock:
            if self._http_session is not None and not self._http_session.closed:
                return self._http_session
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=50,
                ttl_dns_cache=300,
                keepalive_timeout=30,
            )
            self._http_session = aiohttp.ClientSession(connector=connector)
            return self._http_session

    async def _notify(self, room_id: str, message: str) -> Optional[str]:
        if self.notify_callback:
            try:
                return await self.notify_callback(room_id, message)
            except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
                logger.error(f"Failed to send notification: {e}")
        return None

    def _notify_bg(self, room_id: str, message: str) -> None:
        async def _do_notify():
            try:
                eid = await self._notify(room_id, message)
                if eid:
                    self._pending_cleanup_event_ids.append(eid)
            except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
                logger.debug(f"Background notification failed (non-fatal): {e}")

        asyncio.ensure_future(_do_notify())

    def pop_cleanup_event_ids(self) -> tuple[list, Optional[str]]:
        ids = self._pending_cleanup_event_ids.copy()
        summary = self._status_summary
        self._pending_cleanup_event_ids.clear()
        self._status_summary = None
        return ids, summary

    async def handle_file_event(self, event: Event, room_id: str, agent_id: Optional[str] = None) -> Union[bool, list, str, None]:
        """Handle a file upload event from Matrix."""
        try:
            metadata = self._extract_file_metadata(event, room_id)
            if not metadata:
                logger.warning(f"Could not extract file metadata from event {event.event_id}")
                return False

            validation_error = self._validate_file(metadata)
            if validation_error:
                logger.info(f"File {metadata.file_name} rejected: {validation_error}")
                await self._notify(room_id, f"⚠️ {validation_error}")
                return False

            logger.info(f"Processing file upload: {metadata.file_name} from {metadata.sender} in room {room_id}")

            is_audio = metadata.file_type.startswith('audio/')
            if is_audio:
                logger.info(f"Audio uploaded: {metadata.file_name}. Transcribing voice message.")
                return await self._handle_audio_upload(metadata)

            is_image = metadata.file_type.startswith('image/')
            if is_image:
                logger.info(f"Image uploaded: {metadata.file_name}. Building multimodal message content.")
                return await self._handle_image_upload(metadata, room_id, agent_id)

            logger.info(f"File uploaded: {metadata.file_name} ({metadata.file_type}).")

            if self._temporal_enabled and agent_id:
                logger.info(f"Temporal enabled — starting async workflow for {metadata.file_name}")
                return await self._start_temporal_workflow(metadata, room_id, agent_id)

            logger.info(f"Processing {metadata.file_name} inline with MarkItDown.")
            result = await self._handle_document_upload(metadata, room_id, agent_id)
            if result:
                return result
            logger.info(f"MarkItDown returned empty for {metadata.file_name}, falling back to Letta source upload")
            await self._notify(room_id, f"📄 Processing file: {metadata.file_name}")

            async with self._downloaded_file(metadata) as file_path:
                source_id = await self._source_manager.get_or_create_source(room_id, agent_id)
                file_id = await self._source_manager.upload_to_letta(file_path, source_id, metadata)
                if agent_id:
                    await self._source_manager.attach_source_to_agent(source_id, agent_id)
                success = await self._source_manager.poll_file_status(source_id, file_id)
                if success:
                    logger.info(f"Successfully processed file {metadata.file_name} in Letta")
                    await self._notify(room_id, f"✅ File {metadata.file_name} uploaded successfully and indexed")
                else:
                    logger.warning(f"File processing for {file_id} did not complete successfully")
                    await self._notify(room_id, f"⚠️ File processing timed out for {metadata.file_name}")
                return success
        except (
            FileUploadError,
            aiohttp.ClientError,
            asyncio.TimeoutError,
            OSError,
            ValueError,
            RuntimeError,
        ) as e:
            logger.error(f"Error handling file event: {e}", exc_info=True)
            raise FileUploadError(f"Failed to process file upload: {e}")

    @asynccontextmanager
    async def _downloaded_file(self, metadata: FileMetadata):
        file_path = await self._download_matrix_file(metadata)
        try:
            yield file_path
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.debug(f"Cleaned up temporary file {file_path}")
                except OSError:
                    pass

    # ── Delegation to sub-services ──────────────────────────────────

    def _extract_file_metadata(self, event: Event, room_id: str) -> Optional[FileMetadata]:
        return self._download_service.extract_file_metadata(event, room_id)

    def _validate_file(self, metadata: FileMetadata) -> Optional[str]:
        return self._download_service.validate_file(metadata)

    async def _download_matrix_file(self, metadata: FileMetadata) -> str:
        return await self._download_service.download_file(metadata)

    def _get_embedding_config(self, agent_id: Optional[str] = None) -> dict:
        return self._source_manager.get_embedding_config(agent_id)

    async def _get_or_create_source(self, room_id: str, agent_id: Optional[str] = None) -> str:
        return await self._source_manager.get_or_create_source(room_id, agent_id)

    async def _attach_source_to_agent(self, source_id: str, agent_id: str):
        return await self._source_manager.attach_source_to_agent(source_id, agent_id)

    async def _upload_to_letta(self, file_path: str, source_id: str, metadata: FileMetadata) -> str:
        return await self._source_manager.upload_to_letta(file_path, source_id, metadata)

    async def _poll_file_status(self, source_id: str, file_id: str, timeout: int = 300, interval: int = 2) -> bool:
        return await self._source_manager.poll_file_status(source_id, file_id, timeout=timeout, interval=interval)

    async def _get_or_create_folder(self, room_id: str, agent_id: Optional[str] = None) -> str:
        return await self._source_manager.get_or_create_folder(room_id, agent_id)

    async def ensure_search_tool_attached(self, agent_id: str) -> None:
        await self._source_manager.ensure_search_tool_attached(agent_id)
