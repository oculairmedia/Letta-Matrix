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
)

# Import Letta SDK
from letta_client import Letta

logger = logging.getLogger("matrix_client.file_handler")

# Supported file types from Letta Filesystem API
# Note: Some clients send application/octet-stream for unknown types
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
    'application/octet-stream': None,  # Accept but determine by extension
}

# File extensions to accept when MIME type is application/octet-stream
SUPPORTED_EXTENSIONS = {
    # Documents
    '.pdf', '.txt', '.md', '.json', '.markdown',
    '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls',
    '.csv', '.html', '.htm', '.xhtml', '.epub', '.rtf', '.odt',
    # Images
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif',
    '.ogg', '.mp3', '.m4a', '.wav', '.webm', '.flac', '.aac', '.oga'
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
        notify_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
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
        
        # Initialize Letta SDK client
        # SDK v1.x uses api_key instead of token
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
    
    async def _notify(self, room_id: str, message: str):
        """Send notification to Matrix room if callback is configured"""
        if self.notify_callback:
            try:
                await self.notify_callback(room_id, message)
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")
    
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
            
            # For documents (PDF, DOCX, text, etc.) - extract text with MarkItDown
            if is_parseable_document(metadata.file_type, metadata.file_name):
                logger.info(f"Document uploaded: {metadata.file_name}. Extracting text with MarkItDown.")
                return await self._handle_document_upload(metadata, room_id, agent_id)
            
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
                opencode_mxid = metadata.sender
                message_text = f"""[MESSAGE FROM OPENCODE USER]

{message_text}

---
RESPONSE INSTRUCTION (OPENCODE BRIDGE):
This message is from an OpenCode user: {opencode_mxid}
When you respond to this message, you MUST include their @mention ({opencode_mxid}) 
in your response so the OpenCode bridge can route your reply to them.

Example: "{opencode_mxid} Here is my response..."
"""
                logger.info(f"[OPENCODE-IMAGE] Injected @mention instruction for image upload")
            
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
    
    async def _handle_document_upload(self, metadata: FileMetadata, room_id: str, agent_id: Optional[str] = None) -> str:
        """
        Handle document upload by extracting text with MarkItDown.
        
        Follows the same pattern as _handle_audio_upload:
        download → extract text → return formatted string → sent to Letta as message.
        
        Args:
            metadata: File metadata
            room_id: Room ID where document was uploaded
            agent_id: Agent ID to send document to
            
        Returns:
            Formatted string with extracted document text for the agent
        """
        # Notify user that we're processing the document
        await self._notify(room_id, f"📄 Reading document: {metadata.file_name}...")
        
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
                logger.warning(f"Document parsing failed for {metadata.file_name}: {result.error}")
                await self._notify(room_id, f"⚠️ Could not extract text from {metadata.file_name}: {result.error}")
            else:
                page_info = f" ({result.page_count} pages)" if result.page_count else ""
                ocr_info = " (OCR)" if result.was_ocr else ""
                await self._notify(
                    room_id,
                    f"✅ Document processed: {metadata.file_name}{page_info}{ocr_info} — {len(result.text)} chars extracted"
                )
            
            # Format for agent message (includes error handling in format)
            formatted = format_document_for_agent(result, caption=metadata.caption)
            
            # Add OpenCode routing instruction if sender is an OpenCode identity
            if metadata.sender and metadata.sender.startswith("@oc_"):
                opencode_mxid = metadata.sender
                formatted = f"""[MESSAGE FROM OPENCODE USER]

{formatted}

---
RESPONSE INSTRUCTION (OPENCODE BRIDGE):
This message is from an OpenCode user: {opencode_mxid}
When you respond to this message, you MUST include their @mention ({opencode_mxid}) 
in your response so the OpenCode bridge can route your reply to them.

Example: "{opencode_mxid} Here is my response..."
"""
                logger.info(f"[OPENCODE-DOC] Injected @mention instruction for document upload")
            
            logger.info(f"Document extraction complete for {metadata.file_name}, returning {len(formatted)} chars to agent")
            return formatted
            
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
            
            # Get actual filename from info.filename if available (Matrix spec)
            # The 'body' field may contain a user caption instead of filename
            actual_filename = info.get('filename') or body
            
            # Determine if body is a caption (different from filename)
            # If body looks like a filename (has extension), use it as filename
            # Otherwise, treat it as a caption/question from the user
            caption = None
            import os
            _, ext = os.path.splitext(body)
            if ext and ext.lower() in SUPPORTED_EXTENSIONS:
                # body looks like a filename
                actual_filename = body
            elif body != actual_filename:
                # body is different from filename - it's a caption
                caption = body
                logger.info(f"Detected caption for image: {caption[:50]}...")
            
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
        
        # Check file type - also check by extension for application/octet-stream
        if metadata.file_type not in SUPPORTED_FILE_TYPES:
            supported = ", ".join(k for k in SUPPORTED_FILE_TYPES.keys() if k != 'application/octet-stream')
            return f"File type '{metadata.file_type}' is not supported. Supported types: {supported}"
        
        # For application/octet-stream, verify the extension is supported
        if metadata.file_type == 'application/octet-stream':
            _, ext = os.path.splitext(metadata.file_name.lower())
            if ext not in SUPPORTED_EXTENSIONS:
                return f"File extension '{ext}' is not supported for unknown MIME type. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
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
