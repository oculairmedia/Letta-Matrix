"""
Matrix File Upload Handler for Letta Integration

This module handles file uploads from Matrix rooms and integrates them with Letta's
filesystem API for document processing and embedding.

Flow:
1. Detect file upload events (m.room.message with msgtype=m.file)
2. Download file from Matrix media repository
3. Get or create Letta source for the room
4. Upload file to Letta source
5. Attach source to agent
6. Poll for processing completion
7. Notify user in Matrix room
"""

import asyncio
import functools
import logging
import os
import uuid
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any, Callable, Awaitable, Union
from dataclasses import dataclass
import aiohttp
from nio import Event
from src.voice.transcription import transcribe_audio
from src.matrix.document_parser import (
    parse_document, format_document_for_agent,
    is_parseable_document, DocumentParseConfig,
    DocumentParseResult,
)
from src.matrix.formatter import wrap_opencode_routing

# Import Letta SDK
from letta_client import Letta

logger = logging.getLogger("matrix_client.file_handler")

# Known file types - used for extension mapping and temp file suffixes.
# NOT used for rejection: MarkItDown's PlainTextConverter handles any text-like file.
SUPPORTED_FILE_TYPES = {
    'application/pdf': '.pdf',
    'text/plain': '.txt',
    'text/markdown': '.md',
    'text/x-markdown': '.md',
    'application/json': '.json',
    # Document types (MarkItDown)
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.ms-excel': '.xls',
    'text/csv': '.csv',
    'text/html': '.html',
    'application/xhtml+xml': '.xhtml',
    'application/epub+zip': '.epub',
    # Image types (for vision-capable models)
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
    'image/tiff': '.tiff',
    'audio/ogg': '.ogg',
    'audio/mpeg': '.mp3',
    'audio/mp4': '.m4a',
    'audio/wav': '.wav',
    'audio/x-wav': '.wav',
    'audio/webm': '.webm',
    'audio/flac': '.flac',
    'audio/aac': '.aac',
    'text/calendar': '.ics',
    'application/octet-stream': None,  # Accept but determine by extension
}

# Extension-to-MIME mapping for application/octet-stream resolution (routing to image/audio handlers)
SUPPORTED_EXTENSIONS = {
    # Documents
    '.pdf', '.txt', '.md', '.json', '.markdown',
    '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.csv', '.html', '.htm', '.xhtml', '.epub', '.rtf', '.odt',
    # Images
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif',
    '.ogg', '.mp3', '.m4a', '.wav', '.webm', '.flac', '.aac', '.oga',
    # Calendar
    '.ics'
}

# File size limit (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Default embedding model
DEFAULT_EMBEDDING_MODEL = "letta/letta-free"

# Thread pool for running sync SDK calls
_executor = ThreadPoolExecutor(max_workers=4)


@dataclass
class FileMetadata:
    """Metadata for uploaded files"""
    file_url: str  # mxc:// URL
    file_name: str
    file_type: str  # MIME type
    file_size: int
    room_id: str
    sender: str
    timestamp: int
    event_id: str
    caption: Optional[str] = None  # User's caption/question about the file


class FileUploadError(Exception):
    """Raised when file upload operations fail"""
    pass


class LettaFileHandler:
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
        """
        Initialize the file handler
        
        Args:
            homeserver_url: Matrix homeserver URL
            letta_api_url: Letta API base URL
            letta_token: Letta API authentication token
            matrix_access_token: Matrix access token for downloading authenticated media
            notify_callback: Optional callback function to send notifications to Matrix
            embedding_model: Embedding model to use for sources (default: letta/letta-free)
            embedding_endpoint: Embedding API endpoint (e.g., http://localhost:11434/v1 for Ollama)
            embedding_endpoint_type: Type of embedding endpoint (openai, huggingface, etc.)
            embedding_dim: Embedding dimension (1536 for OpenAI, 2560 for some Ollama models)
            embedding_chunk_size: Chunk size for text splitting
            max_retries: Maximum number of retries for API calls
            retry_delay: Base delay between retries (exponential backoff)
        """
        self.homeserver_url = homeserver_url
        self.letta_api_url = letta_api_url
        self.letta_token = letta_token
        self.matrix_access_token = matrix_access_token
        self.notify_callback = notify_callback
        self.embedding_model = embedding_model
        self.embedding_endpoint = embedding_endpoint
        self.embedding_endpoint_type = embedding_endpoint_type
        self.embedding_dim = embedding_dim
        self.embedding_chunk_size = embedding_chunk_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._source_cache: Dict[str, str] = {}  # room_id -> source_id cache
        self._folder_cache = self._source_cache  # Alias for backward compatibility
        self._cache_lock = asyncio.Lock()  # Protect cache from race conditions
        self._pending_cleanup_event_ids: list = []  # Status message event_ids to edit/delete after agent responds
        self._status_summary: Optional[str] = None  # Final compact summary to replace status messages

        # Temporal async file processing
        self._temporal_client = None  # Lazy-initialized
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

        # Initialize Letta SDK client
        self.letta_client = Letta(base_url=letta_api_url, api_key=letta_token)
        
        # Log token status at init
        logger.info(f"LettaFileHandler initialized - matrix_access_token present: {bool(self.matrix_access_token)}, length: {len(self.matrix_access_token) if self.matrix_access_token else 0}")
        logger.info(f"Embedding config: model={embedding_model}, endpoint={embedding_endpoint}, dim={embedding_dim}")
        logger.info(f"Using Letta SDK for API calls")
        # Document parsing config (MarkItDown)
        self.document_parsing_config = document_parsing_config or DocumentParseConfig.from_env()
        logger.info(f"Document parsing: enabled={self.document_parsing_config.enabled}, ocr={self.document_parsing_config.ocr_enabled}")
    
    async def _run_sync(self, func, *args, **kwargs):
        """Run a synchronous function in a thread pool"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor,
            functools.partial(func, *args, **kwargs)
        )
    
    async def _notify(self, room_id: str, message: str) -> Optional[str]:
        """Send notification to Matrix room if callback is configured. Returns event_id."""
        if self.notify_callback:
            try:
                return await self.notify_callback(room_id, message)
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")
        return None

    def pop_cleanup_event_ids(self) -> tuple[list, Optional[str]]:
        """Return and clear pending status message event_ids and summary for cleanup after agent responds.
        
        Returns:
            Tuple of (event_ids_list, summary_string_or_none).
            The first event_id should be EDITED to show the summary.
            Remaining event_ids should be DELETED.
        """
        ids = self._pending_cleanup_event_ids.copy()
        summary = self._status_summary
        self._pending_cleanup_event_ids.clear()
        self._status_summary = None
        return ids, summary

    async def _retry_async(self, func: Callable[[], Awaitable[Any]], operation_name: str) -> Any:
        """
        Retry an async operation with exponential backoff
        
        Args:
            func: Async function to retry
            operation_name: Name of operation for logging
            
        Returns:
            Result of the function
            
        Raises:
            Last exception if all retries fail
        """
        last_exception: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                return await func()
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"{operation_name} failed (attempt {attempt + 1}/{self.max_retries}), "
                        f"retrying in {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"{operation_name} failed after {self.max_retries} attempts: {e}")
        if last_exception is not None:
            raise last_exception
        raise FileUploadError(f"{operation_name} failed with no exception")
        
    async def handle_file_event(self, event: Event, room_id: str, agent_id: Optional[str] = None) -> Union[bool, list, str, None]:
        """
        Handle a file upload event from Matrix
        
        Args:
            event: Matrix event object
            room_id: Room ID where file was uploaded
            agent_id: Optional agent ID to attach source to
            
        Returns:
            list: Multimodal content for image uploads (to be sent via normal message pipeline)
            str: Transcribed text wrapper for voice uploads, or extracted document text
            bool: Success/failure for unsupported uploads handled internally
            None: Nothing to do
        """
        try:
            # Extract file metadata
            metadata = self._extract_file_metadata(event, room_id)
            if not metadata:
                logger.warning(f"Could not extract file metadata from event {event.event_id}")
                return False
            
            # Validate file type and size
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

            # Check if this is an image - handle differently via multimodal message
            is_image = metadata.file_type.startswith('image/')
            if is_image:
                logger.info(f"Image uploaded: {metadata.file_name}. Building multimodal message content.")
                return await self._handle_image_upload(metadata, room_id, agent_id)
            
            # For all non-image, non-audio files - try document processing
            # If Temporal is enabled, start async workflow; otherwise process inline
            logger.info(f"File uploaded: {metadata.file_name} ({metadata.file_type}).")

            if self._temporal_enabled and agent_id:
                logger.info(f"Temporal enabled — starting async workflow for {metadata.file_name}")
                return await self._start_temporal_workflow(metadata, room_id, agent_id)

            # Fallback: inline processing (Temporal disabled or no agent_id)
            logger.info(f"Processing {metadata.file_name} inline with MarkItDown.")
            result = await self._handle_document_upload(metadata, room_id, agent_id)
            if result:
                return result
            logger.info(f"MarkItDown returned empty for {metadata.file_name}, falling back to Letta source upload")
            # Fallback for unknown file types - use Letta source upload flow
            await self._notify(room_id, f"📄 Processing file: {metadata.file_name}")
            
            # Download file from Matrix
            file_path = await self._download_matrix_file(metadata)
            
            try:
                # Get or create Letta source for this room
                source_id = await self._get_or_create_source(room_id, agent_id)
                
                # Upload to Letta
                file_id = await self._upload_to_letta(file_path, source_id, metadata)
                
                # Attach source to agent (idempotent)
                if agent_id:
                    await self._attach_source_to_agent(source_id, agent_id)
                
                # Poll for completion (using file status endpoint)
                success = await self._poll_file_status(source_id, file_id)
                
                if success:
                    logger.info(f"Successfully processed file {metadata.file_name} in Letta")
                    await self._notify(room_id, f"✅ File {metadata.file_name} uploaded successfully and indexed")
                else:
                    logger.warning(f"File processing for {file_id} did not complete successfully")
                    await self._notify(room_id, f"⚠️ File processing timed out for {metadata.file_name}")
                
                return success
                
            finally:
                # Clean up temporary file
                if file_path and os.path.exists(file_path):
                    os.unlink(file_path)
                    logger.debug(f"Cleaned up temporary file {file_path}")
        except Exception as e:
            logger.error(f"Error handling file event: {e}", exc_info=True)
            raise FileUploadError(f"Failed to process file upload: {e}")
    
    async def _handle_image_upload(self, metadata: FileMetadata, room_id: str, agent_id: Optional[str] = None) -> Optional[list]:
        """
        Handle image upload by sending as multimodal message to agent.
        
        Images are sent as base64-encoded data in a multipart message format
        that vision-capable models (GPT-4o, Claude 3.5, Gemini) can process.
        
        Args:
            metadata: File metadata
            room_id: Room ID where image was uploaded
            agent_id: Agent ID to send image to
            
        Returns:
            Multimodal input content for Letta API, or None on failure
        """
        import base64
        
        # Notify user that we're processing the image
        await self._notify(room_id, f"🖼️ Processing image: {metadata.file_name}")
        
        # Download image from Matrix
        file_path = await self._download_matrix_file(metadata)
        
        try:
            # Read and encode image as base64
            with open(file_path, 'rb') as f:
                image_data = base64.standard_b64encode(f.read()).decode('utf-8')
            
            logger.info(f"Encoded image {metadata.file_name} as base64 ({len(image_data)} chars)")
            
            # Build message text based on whether user provided a caption/question
            if metadata.caption:
                message_text = (
                    f"[Image Upload: {metadata.file_name}]\n\n"
                    f"The user shared an image and asked: \"{metadata.caption}\"\n\n"
                    f"Please analyze the image and respond to the user's question."
                )
                logger.info(f"Including user caption in image message: {metadata.caption[:50]}...")
            else:
                message_text = (
                    f"[Image Upload: {metadata.file_name}]\n\n"
                    f"The user has shared an image with you. Please analyze the image and describe what you see."
                )
            
            # Add OpenCode routing instruction if sender is an OpenCode identity
            if metadata.sender and metadata.sender.startswith("@oc_"):
                message_text = wrap_opencode_routing(message_text, metadata.sender)
                logger.info("[OPENCODE-IMAGE] Injected @mention instruction for image upload")
            
            input_content = [
                {
                    "type": "text",
                    "text": message_text
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": metadata.file_type,
                        "data": image_data,
                    }
                }
            ]
            logger.info(f"Built multimodal content for image {metadata.file_name}")
            return input_content
                 
        finally:
            # Clean up temporary file
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"Cleaned up temporary file {file_path}")

    async def _handle_audio_upload(self, metadata: FileMetadata) -> str:
        file_path = await self._download_matrix_file(metadata)

        try:
            with open(file_path, 'rb') as audio_file:
                audio_data = audio_file.read()

            result = await transcribe_audio(audio_data, filename=metadata.file_name or "voice.ogg")
            if result.error:
                logger.warning(f"Voice transcription failed for {metadata.file_name}: {result.error}")
                return f"[Voice message - transcription failed: {result.error}]"

            transcribed_text = (result.text or "").strip()
            if not transcribed_text:
                transcribed_text = "(no speech detected)"

            logger.info(f"Voice transcription succeeded for {metadata.file_name}")
            return f"[Voice message]: {transcribed_text}"
        finally:
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"Cleaned up temporary file {file_path}")
    
    # ------------------------------------------------------------------
    # Temporal async file processing
    # ------------------------------------------------------------------

    async def _get_temporal_client(self):
        """Lazy-initialize the Temporal client (async, thread-safe via asyncio.Lock)."""
        if self._temporal_client is not None:
            return self._temporal_client

        async with self._temporal_lock:
            # Double-check after acquiring lock
            if self._temporal_client is not None:
                return self._temporal_client

            from temporalio.client import Client as TemporalClient

            host = os.environ.get('TEMPORAL_HOST', '192.168.50.90:7233')
            namespace = os.environ.get('TEMPORAL_NAMESPACE', 'matrix')

            logger.info(f"Connecting Temporal client to {host}, namespace={namespace}")
            self._temporal_client = await TemporalClient.connect(
                host,
                namespace=namespace,
            )
            logger.info("Temporal client connected")
            return self._temporal_client

    async def _start_temporal_workflow(
        self, metadata: FileMetadata, room_id: str, agent_id: str
    ) -> None:
        """
        Start a Temporal FileProcessingWorkflow for a document upload.

        Sends an acknowledgement to the Matrix room, then fires the workflow
        and returns None immediately (async processing).
        """
        from temporal_workflows.workflows.file_processing import (
            FileProcessingWorkflow,
            FileProcessingInput,
        )

        # Pre-attach search tool so it's available by the time the document is indexed
        try:
            await self.ensure_search_tool_attached(agent_id)
        except Exception as e:
            logger.warning(f"Failed to pre-attach search tool for {agent_id}: {e}")

        # Send immediate acknowledgement to user
        eid = await self._notify(
            room_id,
            f"\U0001f4c4 Processing document: {metadata.file_name} (async)..."
        )
        if eid:
            self._pending_cleanup_event_ids.append(eid)
        self._status_summary = f"\U0001f4c4 {metadata.file_name} — processing asynchronously"

        conversation_id: Optional[str] = None
        try:
            from src.core.conversation_service import get_conversation_service

            conv_service = get_conversation_service(self.letta_client)
            conversation_id = conv_service.get_conversation_id(
                room_id=room_id,
                agent_id=agent_id,
            )
            if conversation_id:
                logger.info(
                    f"[CONVERSATIONS] Reusing conversation {conversation_id} for temporal workflow"
                )
        except Exception as conv_err:
            logger.debug(
                f"[CONVERSATIONS] Could not resolve conversation for temporal workflow: {conv_err}"
            )

        # Build workflow input
        task_queue = os.environ.get('TEMPORAL_TASK_QUEUE', 'matrix-file-queue')
        workflow_input = FileProcessingInput(
            mxc_url=metadata.file_url,
            file_name=metadata.file_name,
            file_type=metadata.file_type,
            room_id=room_id,
            sender=metadata.sender,
            event_id=metadata.event_id,
            agent_id=agent_id,
            caption=metadata.caption,
            status_event_id=eid,
            file_size=metadata.file_size,
            conversation_id=conversation_id,
        )

        workflow_id = f"file-{room_id}-{metadata.event_id}-{uuid.uuid4().hex[:8]}"

        try:
            client = await self._get_temporal_client()
            handle = await client.start_workflow(
                FileProcessingWorkflow.run,
                workflow_input,
                id=workflow_id,
                task_queue=task_queue,
            )
            logger.info(
                f"Started Temporal workflow {handle.id} for {metadata.file_name} "
                f"(queue={task_queue})"
            )
        except Exception as e:
            logger.error(f"Failed to start Temporal workflow for {metadata.file_name}: {e}", exc_info=True)
            # Notify room of failure, fall through to return None
            await self._notify(room_id, f"\u26a0\ufe0f Failed to queue document processing: {e}")

        # Return None — document processing is async via Temporal
        return None

    async def _handle_document_upload(self, metadata: FileMetadata, room_id: str, agent_id: Optional[str] = None) -> Optional[str]:
        """
        Handle document upload by extracting text with MarkItDown,
        then ingesting into the shared Haystack document store (Weaviate)
        instead of dumping the full text into agent context.
        
        Flow:
        1. Download file from Matrix
        2. Extract text with MarkItDown (OCR fallback for scanned PDFs)
        3. POST extracted text to Hayhooks ingest_document pipeline
        4. Return a brief notification to the agent (NOT the full text)
        
        Args:
            metadata: File metadata
            room_id: Room ID where document was uploaded
            agent_id: Agent ID to send document to
            
        Returns:
            Formatted string with document summary for the agent, or None if
            extraction failed (caller should fall back to Letta source upload).
        """
        # Notify user that we're processing the document
        eid = await self._notify(room_id, f"📄 Reading document: {metadata.file_name}...")
        if eid:
            self._pending_cleanup_event_ids.append(eid)
        
        # Download file from Matrix
        file_path = await self._download_matrix_file(metadata)
        
        try:
            # Parse document with MarkItDown
            result = await parse_document(
                file_path=file_path,
                filename=metadata.file_name,
                config=self.document_parsing_config,
            )
            
            if result.error:
                self._status_summary = f"⚠️ {metadata.file_name} — extraction failed"
                logger.warning(f"Document parsing failed for {metadata.file_name}: {result.error}")
                await self._notify(room_id, f"⚠️ Could not extract text from {metadata.file_name}: {result.error}")
                # Return None so the caller can fall back to Letta source upload
                return None
            
            # Check if extracted content is too short or empty to be useful
            text_len = len((result.text or "").strip())
            if text_len < 10:
                logger.info(f"MarkItDown returned insufficient content for {metadata.file_name} ({text_len} chars)")
                return None
            
            page_info = f" ({result.page_count} pages)" if result.page_count else ""
            ocr_info = " (OCR)" if result.was_ocr else ""
            char_count = len(result.text)
            
            # Ingest into shared Haystack document store via Hayhooks
            ingest_success = await self._ingest_to_haystack(
                text=result.text,
                filename=metadata.file_name,
                room_id=room_id,
                sender=metadata.sender or "",
            )
            
            if ingest_success:
                eid = await self._notify(
                    room_id,
                    f"✅ Document indexed: {metadata.file_name}{page_info}{ocr_info} — {char_count} chars stored in shared document library"
                )
                if eid:
                    self._pending_cleanup_event_ids.append(eid)
                self._status_summary = f"📄 {metadata.file_name}{page_info}{ocr_info} — {char_count:,} chars indexed ✓"
                # Return a brief notification to the agent — NOT the full text
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
                # Fallback: ingest failed, send truncated text directly
                logger.warning(f"Haystack ingest failed for {metadata.file_name}, falling back to direct text")
                eid = await self._notify(
                    room_id,
                    f"⚠️ Document store unavailable, sending text directly: {metadata.file_name}{page_info}{ocr_info}"
                )
                if eid:
                    self._pending_cleanup_event_ids.append(eid)
                self._status_summary = f"⚠️ {metadata.file_name}{page_info}{ocr_info} — sent directly (document store unavailable)"
                # Truncate to a safe size for context (max ~8000 chars)
                truncated_text = result.text[:8000]
                if len(result.text) > 8000:
                    truncated_text += f"\n\n[... truncated from {char_count} chars. Document store was unavailable for full indexing.]"
                agent_msg = format_document_for_agent(
                    DocumentParseResult(text=truncated_text, filename=result.filename, page_count=result.page_count, was_ocr=result.was_ocr),
                    caption=metadata.caption,
                )
            
            # Add OpenCode routing instruction if sender is an OpenCode identity
            if metadata.sender and metadata.sender.startswith("@oc_"):
                agent_msg = wrap_opencode_routing(agent_msg, metadata.sender)
                logger.info("[OPENCODE-DOC] Injected @mention instruction for document upload")
            
            logger.info(f"Document handling complete for {metadata.file_name}, returning {len(agent_msg)} chars to agent")
            return agent_msg
            
        finally:
            # Clean up temporary file
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
                logger.debug(f"Cleaned up temporary file {file_path}")

    async def _send_multimodal_message(self, agent_id: str, content: list) -> Optional[Any]:
        """
        Send a multimodal message (with images) to a Letta agent.
        
        Args:
            agent_id: Agent ID to send message to
            content: Message content array with text and image parts
            
        Returns:
            Agent response or None on error
        """
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
            
            response = await self._retry_async(_do_send, "Multimodal message send")
            logger.debug(f"Multimodal message response: {type(response)}")
            return response
            
        except Exception as e:
            logger.error(f"Error sending multimodal message: {e}", exc_info=True)
            return None
    
    def _extract_assistant_response(self, response: Any) -> Optional[str]:
        """
        Extract assistant message text from Letta response.
        
        Args:
            response: Letta API response object
            
        Returns:
            Assistant message text or None
        """
        try:
            # Handle SDK response object
            if hasattr(response, 'messages'):
                messages = response.messages
            elif hasattr(response, 'model_dump'):
                data = response.model_dump()
                messages = data.get('messages', [])
            elif isinstance(response, dict):
                messages = response.get('messages', [])
            else:
                return None
            
            # Look for assistant messages
            assistant_texts = []
            for msg in messages:
                # Handle SDK message objects
                if hasattr(msg, 'message_type'):
                    msg_type = msg.message_type
                    if msg_type == 'assistant_message' and hasattr(msg, 'content'):
                        assistant_texts.append(str(msg.content))
                # Handle dict messages
                elif isinstance(msg, dict):
                    msg_type = msg.get('message_type')
                    if msg_type == 'assistant_message':
                        content = msg.get('content')
                        if content:
                            assistant_texts.append(str(content))
            
            if assistant_texts:
                return '\n'.join(assistant_texts)
            return None
            
        except Exception as e:
            logger.error(f"Error extracting assistant response: {e}")
            return None
    
    def _extract_file_metadata(self, event: Event, room_id: str) -> Optional[FileMetadata]:
        """Extract file metadata from Matrix event"""
        try:
            # Check if this is a file message event
            if not hasattr(event, 'source') or not isinstance(event.source, dict):
                return None
            
            content = event.source.get('content', {})
            msgtype = content.get('msgtype')
            
            if msgtype not in ['m.file', 'm.image', 'm.audio']:
                return None
            
            # Extract file information
            url = content.get('url')  # mxc:// URL
            body = content.get('body', 'unnamed_file')  # filename or caption
            info = content.get('info', {})
            
            if not url:
                logger.warning("File event missing URL")
                return None
            
            content_filename = content.get('filename')
            info_filename = info.get('filename')
            resolved_filename = content_filename or info_filename
            actual_filename = resolved_filename or body
            
            # Determine if body is a caption (different from filename)
            # If body looks like a filename (has extension), use it as filename
            # Otherwise, treat it as a caption/question from the user
            caption = None
            import os
            _, ext = os.path.splitext(body)
            if ext and ext.lower() in SUPPORTED_EXTENSIONS and not resolved_filename:
                actual_filename = body
            elif resolved_filename and body != resolved_filename:
                caption = body
                logger.info(f"Detected caption on file '{actual_filename}': {caption[:50]}...")
            elif not ext:
                mimetype = info.get('mimetype', 'application/octet-stream')
                mime_ext = SUPPORTED_FILE_TYPES.get(mimetype) or '.bin'
                # Use mxc media_id as a stable filename
                media_id = url.split('/')[-1] if url else 'unknown'
                actual_filename = f"{media_id}{mime_ext}"
                caption = body
                logger.info(
                    f"Caption detected (no info.filename): '{caption[:50]}...' — "
                    f"derived filename: {actual_filename}"
                )
            
            return FileMetadata(
                file_url=url,
                file_name=actual_filename,
                file_type=info.get('mimetype', 'application/octet-stream'),
                file_size=info.get('size', 0),
                room_id=room_id,
                sender=event.sender,
                timestamp=event.server_timestamp,
                event_id=event.event_id,
                caption=caption
            )
            
        except Exception as e:
            logger.error(f"Error extracting file metadata: {e}", exc_info=True)
            return None
    
    def _validate_file(self, metadata: FileMetadata) -> Optional[str]:
        """
        Validate file type and size
        
        Args:
            metadata: File metadata to validate
            
        Returns:
            Error message if validation fails, None if valid
        """
        import os
        
        # Check file size
        if metadata.file_size > MAX_FILE_SIZE:
            size_mb = metadata.file_size / (1024 * 1024)
            max_mb = MAX_FILE_SIZE / (1024 * 1024)
            return f"File '{metadata.file_name}' is too large ({size_mb:.1f}MB). Maximum size is {max_mb:.0f}MB."
        
        # Try-first approach: accept any file type. MarkItDown handles text-like files
        # via PlainTextConverter fallback. Only images/audio need special MIME routing.
        # We log unrecognized types but don't reject them.
        if metadata.file_type not in SUPPORTED_FILE_TYPES:
            logger.info(f"Non-whitelisted MIME type '{metadata.file_type}' for {metadata.file_name} - will try MarkItDown")
        
        # For application/octet-stream, try to resolve MIME from extension for routing
        if metadata.file_type == 'application/octet-stream':
            _, ext = os.path.splitext(metadata.file_name.lower())
            if ext not in SUPPORTED_EXTENSIONS:
                logger.info(f"Unknown extension '{ext}' for octet-stream - will try MarkItDown")
            # Update the file_type based on extension for better handling
            if ext in ['.md', '.markdown']:
                metadata.file_type = 'text/markdown'
            elif ext == '.txt':
                metadata.file_type = 'text/plain'
            elif ext == '.pdf':
                metadata.file_type = 'application/pdf'
            elif ext == '.json':
                metadata.file_type = 'application/json'
            elif ext in ['.jpg', '.jpeg']:
                metadata.file_type = 'image/jpeg'
            elif ext == '.png':
                metadata.file_type = 'image/png'
            elif ext == '.gif':
                metadata.file_type = 'image/gif'
            elif ext == '.webp':
                metadata.file_type = 'image/webp'
            elif ext in ['.bmp']:
                metadata.file_type = 'image/bmp'
            elif ext in ['.tiff', '.tif']:
                metadata.file_type = 'image/tiff'
            elif ext in ['.ogg', '.oga']:
                metadata.file_type = 'audio/ogg'
            elif ext == '.mp3':
                metadata.file_type = 'audio/mpeg'
            elif ext == '.m4a':
                metadata.file_type = 'audio/mp4'
            elif ext == '.wav':
                metadata.file_type = 'audio/wav'
            elif ext == '.webm':
                metadata.file_type = 'audio/webm'
            elif ext == '.flac':
                metadata.file_type = 'audio/flac'
            elif ext == '.aac':
                metadata.file_type = 'audio/aac'
            # Document types
            elif ext == '.docx':
                metadata.file_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif ext == '.doc':
                metadata.file_type = 'application/msword'
            elif ext == '.pptx':
                metadata.file_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
            elif ext == '.ppt':
                metadata.file_type = 'application/vnd.ms-powerpoint'
            elif ext == '.xlsx':
                metadata.file_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            elif ext == '.xls':
                metadata.file_type = 'application/vnd.ms-excel'
            elif ext == '.csv':
                metadata.file_type = 'text/csv'
            elif ext in ['.html', '.htm']:
                metadata.file_type = 'text/html'
            elif ext == '.epub':
                metadata.file_type = 'application/epub+zip'
            elif ext == '.ics':
                metadata.file_type = 'text/calendar'

        return None
    
    async def _download_matrix_file(self, metadata: FileMetadata) -> str:
        """
        Download file from Matrix media repository
        
        Args:
            metadata: File metadata containing mxc:// URL
            
        Returns:
            Path to downloaded temporary file
        """
        # Convert mxc:// URL to HTTP download URL
        # mxc://server.name/mediaId -> http://homeserver/_matrix/media/v3/download/server.name/mediaId
        mxc_url = metadata.file_url
        if not mxc_url.startswith('mxc://'):
            raise FileUploadError(f"Invalid mxc:// URL: {mxc_url}")
        
        # Parse mxc URL
        parts = mxc_url[6:].split('/', 1)  # Remove "mxc://" prefix
        if len(parts) != 2:
            raise FileUploadError(f"Malformed mxc:// URL: {mxc_url}")
        
        server_name, media_id = parts
        # Use authenticated media endpoint (MSC3916) - requires auth token
        # The legacy /_matrix/media/v3/download endpoint may be disabled on some servers
        download_url = f"{self.homeserver_url}/_matrix/client/v1/media/download/{server_name}/{media_id}"
        
        logger.debug(f"Downloading file from {download_url}")
        
        # Create temporary file
        suffix = SUPPORTED_FILE_TYPES.get(metadata.file_type, '.bin')
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp_file.name
        
        # Prepare headers with authentication if available
        headers = {}
        logger.debug(f"Download - matrix_access_token present: {bool(self.matrix_access_token)}")
        if self.matrix_access_token:
            headers["Authorization"] = f"Bearer {self.matrix_access_token}"
            logger.info(f"Using Matrix auth token for download (length: {len(self.matrix_access_token)})")
        else:
            logger.warning("No Matrix access token available for download - may fail with 403")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise FileUploadError(f"Failed to download file: {response.status} - {error_text}")
                    
                    # Write to temporary file
                    with open(temp_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)
            
            logger.info(f"Downloaded file to {temp_path} ({os.path.getsize(temp_path)} bytes)")
            return temp_path
            
        except Exception as e:
            # Clean up on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise FileUploadError(f"Error downloading file: {e}")
    
    def _get_embedding_config(self, agent_id: Optional[str] = None) -> dict:
        """
        Get embedding configuration, optionally from agent
        
        Args:
            agent_id: Optional agent ID to fetch config from
            
        Returns:
            Embedding config dict
        """
        # Try to get agent's embedding config
        if agent_id:
            try:
                agent = self.letta_client.agents.retrieve(agent_id)
                if agent and agent.embedding_config:
                    ec = agent.embedding_config
                    config = {
                        "embedding_model": ec.embedding_model,
                        "embedding_endpoint_type": ec.embedding_endpoint_type or "openai",
                        "embedding_dim": ec.embedding_dim,
                        "embedding_chunk_size": ec.embedding_chunk_size or 300
                    }
                    if ec.embedding_endpoint:
                        config["embedding_endpoint"] = ec.embedding_endpoint
                    logger.info(f"Using agent's embedding config: model={config['embedding_model']}, dim={config['embedding_dim']}")
                    return config
            except Exception as e:
                logger.warning(f"Failed to fetch agent embedding config: {e}")
        
        # Fall back to instance defaults
        config = {
            "embedding_model": self.embedding_model,
            "embedding_endpoint_type": self.embedding_endpoint_type,
            "embedding_dim": self.embedding_dim,
            "embedding_chunk_size": self.embedding_chunk_size
        }
        if self.embedding_endpoint:
            config["embedding_endpoint"] = self.embedding_endpoint
        logger.info(f"Using fallback embedding config: model={self.embedding_model}, dim={self.embedding_dim}")
        return config
    
    async def _get_or_create_source(self, room_id: str, agent_id: Optional[str] = None) -> str:
        """
        Get or create Letta folder (source) for Matrix room using SDK v1.x
        
        Note: In SDK v1.x, "sources" are now called "folders"
        
        Args:
            room_id: Matrix room ID
            agent_id: Optional agent ID to get embedding config from
            
        Returns:
            Letta folder ID
        """
        # Check cache first (with lock for thread safety)
        async with self._cache_lock:
            if room_id in self._source_cache:
                return self._source_cache[room_id]
        
        # Sanitize room_id for use in folder name (remove special chars)
        safe_room_id = room_id.replace("!", "").replace(":", "-")
        folder_name = f"matrix-{safe_room_id}"
        
        async def _do_get_or_create() -> str:
            # Try to get existing folder by name (SDK v1.x: use folders.list with name filter)
            try:
                folders_page = await self._run_sync(
                    self.letta_client.folders.list,
                    name=folder_name
                )
                # SDK v1.x returns paginated result with .items
                folders = folders_page.items if hasattr(folders_page, 'items') else folders_page
                if folders and len(folders) > 0:
                    folder_id = folders[0].id
                    logger.info(f"Found existing folder by name: {folder_id}")
                    return folder_id
            except Exception as e:
                logger.debug(f"Folder not found by name: {e}")
            
            # Create new folder
            logger.info(f"Creating new folder: {folder_name}")
            embedding_config = await self._run_sync(self._get_embedding_config, agent_id)
            
            try:
                folder = await self._run_sync(
                    self.letta_client.folders.create,
                    name=folder_name,
                    description=f"Documents from Matrix room {room_id}"
                )
                logger.info(f"Created new folder {folder_name}: {folder.id}")
                return folder.id
            except Exception as e:
                error_str = str(e)
                if "409" in error_str or "already exists" in error_str.lower() or "unique" in error_str.lower():
                    # Folder exists but wasn't found - try list again
                    logger.info(f"Folder {folder_name} already exists (conflict), fetching...")
                    try:
                        folders_page = await self._run_sync(
                            self.letta_client.folders.list,
                            name=folder_name
                        )
                        folders = folders_page.items if hasattr(folders_page, 'items') else folders_page
                        if folders and len(folders) > 0:
                            folder_id = folders[0].id
                            logger.info(f"Found folder after conflict: {folder_id}")
                            return folder_id
                    except Exception as e2:
                        logger.error(f"Failed to get folder after 409: {e2}")
                raise FileUploadError(f"Failed to create folder: {e}")
        
        folder_id = await self._retry_async(_do_get_or_create, "Get/create Letta folder")
        
        # Cache the folder ID
        async with self._cache_lock:
            self._source_cache[room_id] = folder_id
        
        return folder_id
    
    async def _attach_source_to_agent(self, source_id: str, agent_id: str):
        """
        Attach a folder to an agent using SDK v1.x (idempotent)
        
        Note: In SDK v1.x, use agents.folders instead of agents.sources
        
        Args:
            source_id: Letta folder ID
            agent_id: Letta agent ID
        """
        try:
            # Check if already attached (SDK v1.x returns paginated result)
            attached_page = await self._run_sync(
                self.letta_client.agents.folders.list,
                agent_id
            )
            attached_folders = attached_page.items if hasattr(attached_page, 'items') else attached_page
            
            for folder in attached_folders:
                if folder.id == source_id:
                    logger.info(f"Folder {source_id} already attached to agent {agent_id}")
                    return
            
            # Attach folder to agent (SDK v1.x: folder_id positional, agent_id keyword)
            await self._run_sync(
                lambda: self.letta_client.agents.folders.attach(source_id, agent_id=agent_id)
            )
            logger.info(f"Attached folder {source_id} to agent {agent_id}")
            
        except Exception as e:
            logger.warning(f"Failed to attach folder to agent: {e}")
            # Don't raise - attachment failure shouldn't block the upload
    
    async def _upload_to_letta(self, file_path: str, source_id: str, metadata: FileMetadata) -> str:
        """
        Upload file to Letta folder using SDK v1.x
        
        Note: In SDK v1.x, use folders.files instead of sources.files
        
        Args:
            file_path: Local path to file
            source_id: Letta folder ID
            metadata: File metadata
            
        Returns:
            File ID for tracking upload progress
        """
        async def _do_upload() -> str:
            # Read file content
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            # Upload file using SDK v1.x - file param expects tuple (filename, content, content_type)
            result = await self._run_sync(
                self.letta_client.folders.files.upload,
                source_id,
                file=(metadata.file_name, file_content, metadata.file_type)
            )
            
            # Get file ID from result (returns Job object)
            file_id = result.id if hasattr(result, 'id') else str(result)
            
            logger.info(f"File uploaded to Letta, file ID: {file_id}")
            return file_id
        
        return await self._retry_async(_do_upload, "Letta file upload")
    
    async def _poll_file_status(self, source_id: str, file_id: str, timeout: int = 300, interval: int = 2) -> bool:
        """
        Poll Letta file status until processing completes
        
        Note: In SDK v1.x, use folders.files instead of sources.files
        
        Args:
            source_id: Folder ID containing the file
            file_id: File ID to poll
            timeout: Maximum time to wait (seconds)
            interval: Polling interval (seconds)
            
        Returns:
            True if file processed successfully
        """
        # Handle sync-complete case (file was processed immediately)
        if file_id == "sync-complete":
            return True
        
        elapsed = 0
        consecutive_errors = 0
        max_consecutive_errors = 3
        
        while elapsed < timeout:
            try:
                # Get file status using SDK v1.x (returns paginated result)
                files_page = await self._run_sync(
                    self.letta_client.folders.files.list,
                    source_id
                )
                files = files_page.items if hasattr(files_page, 'items') else files_page
                
                # Find our file
                file_data = None
                for f in files:
                    if f.id == file_id:
                        file_data = f
                        break
                
                if not file_data:
                    logger.warning(f"File {file_id} not found in folder {source_id}")
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        return False
                    await asyncio.sleep(interval)
                    elapsed += interval
                    continue
                
                consecutive_errors = 0  # Reset on success
                status = (file_data.processing_status or '').lower()
                
                logger.debug(f"File {file_id} processing_status: {status}")
                
                if status in ['completed', 'success', 'done', 'embedded']:
                    logger.info(f"File {file_id} processed successfully")
                    return True
                elif status in ['error', 'failed']:
                    error_msg = getattr(file_data, 'error_message', 'Unknown error')
                    logger.error(f"File {file_id} processing failed: {error_msg}")
                    return False
                
                # Still processing (parsing, embedding, etc.)
                await asyncio.sleep(interval)
                elapsed += interval
                
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Error polling file status after {max_consecutive_errors} attempts: {e}")
                    return False
                logger.warning(f"Error polling file status: {e}")
                await asyncio.sleep(interval)
                elapsed += interval
        
        logger.warning(f"File {file_id} polling timed out after {timeout}s")
        return False
    
    # Alias for backward compatibility with tests
    async def _get_or_create_folder(self, room_id: str, agent_id: Optional[str] = None) -> str:
        """Alias for _get_or_create_source for backward compatibility"""
        return await self._get_or_create_source(room_id, agent_id)

    async def _ingest_to_haystack(self, text: str, filename: str, room_id: str, sender: str) -> bool:
        """
        POST extracted document text to the Hayhooks ingest_document pipeline.
        
        The pipeline chunks the text, embeds it via LiteLLM, and writes
        the chunks to the shared Weaviate document store.
        
        Args:
            text: Full extracted text content of the document
            filename: Original filename of the document
            room_id: Matrix room ID where the document was uploaded
            sender: Matrix user ID of the uploader
            
        Returns:
            True on successful ingestion, False on failure
        """
        hayhooks_url = os.getenv(
            "HAYHOOKS_INGEST_URL",
            "http://192.168.50.90:1416/ingest_document/run"
        )
        
        payload = {
            "text": text,
            "filename": filename,
            "room_id": room_id,
            "sender": sender,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    hayhooks_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=600),  # Large docs (400+ pages) need time for chunking + embedding
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            f"Hayhooks ingest failed for {filename}: "
                            f"HTTP {response.status} - {error_text[:500]}"
                        )
                        return False
                    
                    result = await response.json()
                    
                    # The pipeline returns JSON-encoded string in 'result' key
                    import json
                    result_data = result
                    if isinstance(result.get("result"), str):
                        result_data = json.loads(result["result"])
                    
                    status = result_data.get("status", "")
                    if status == "ok":
                        chunks = result_data.get("chunks_stored", 0)
                        logger.info(
                            f"Document '{filename}' ingested successfully: "
                            f"{chunks} chunks stored in Weaviate"
                        )
                        return True
                    else:
                        detail = result_data.get("detail", "Unknown error")
                        logger.error(f"Hayhooks ingest error for {filename}: {detail}")
                        return False
                        
        except asyncio.TimeoutError:
            logger.error(f"Hayhooks ingest timed out for {filename} (120s)")
            return False
        except aiohttp.ClientError as e:
            logger.error(f"Hayhooks connection error for {filename}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error ingesting {filename} to Haystack: {e}", exc_info=True)
            return False

    async def ensure_search_tool_attached(self, agent_id: str) -> None:
        """
        Ensure the search_documents tool is attached to the given agent.
        
        Called as an explicit prerequisite in file_callback() BEFORE the agent
        run starts. Looks up the tool by name, checks if the agent already has
        it, and attaches it if missing.
        
        Raises on failure so the caller can decide whether to proceed.
"""
        try:
            # Find the search_documents tool by name
            tools_page = await self._run_sync(
                self.letta_client.tools.list, name="search_documents"
            )
            tools_list = list(tools_page)  # SyncArrayPage → list
            if not tools_list:
                logger.warning("search_documents tool not found in Letta — cannot auto-attach")
                return
            
            search_tool = tools_list[0]
            search_tool_id = search_tool.id
            
            # Check if agent already has this tool
            agent_tools_page = await self._run_sync(
                self.letta_client.agents.tools.list, agent_id
            )
            agent_tools = list(agent_tools_page)  # SyncArrayPage → list
            for t in agent_tools:
                if t.id == search_tool_id:
                    logger.debug(f"search_documents already attached to agent {agent_id}")
                    return
            
            # Attach it
            await self._run_sync(
                self.letta_client.agents.tools.attach, search_tool_id, agent_id=agent_id
            )
            logger.info(f"Auto-attached search_documents tool to agent {agent_id}")
            
        except Exception as e:
            logger.warning(f"Failed to auto-attach search_documents to agent {agent_id}: {e}")
