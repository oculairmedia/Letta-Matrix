"""
Matrix File Upload Handler for Letta Integration

This module handles file uploads from Matrix rooms and integrates them with Letta's
filesystem API for document processing and embedding.

Flow:
1. Detect file upload events (m.room.message with msgtype=m.file)
2. Download file from Matrix media repository
3. Get or create Letta folder for the room
4. Upload file to Letta folder
5. Poll for processing completion
6. Notify user in Matrix room
"""

import asyncio
import json
import logging
import os
import tempfile
from typing import Optional, Dict, Any, Callable, Awaitable
from dataclasses import dataclass
import aiohttp
from nio import Event

logger = logging.getLogger(__name__)

# Supported file types from Letta Filesystem API
SUPPORTED_FILE_TYPES = {
    'application/pdf': '.pdf',
    'text/plain': '.txt',
    'text/markdown': '.md',
    'application/json': '.json',
}

# File size limit (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# Default embedding model
DEFAULT_EMBEDDING_MODEL = "letta/letta-free"


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


class FileUploadError(Exception):
    """Raised when file upload operations fail"""
    pass


class LettaFileHandler:
    """Handles Matrix file uploads and Letta integration"""
    
    def __init__(
        self,
        homeserver_url: str,
        letta_api_url: str,
        letta_token: str,
        matrix_access_token: Optional[str] = None,
        notify_callback: Optional[Callable[[str, str], Awaitable[None]]] = None,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ):
        """
        Initialize the file handler
        
        Args:
            homeserver_url: Matrix homeserver URL
            letta_api_url: Letta API base URL
            letta_token: Letta API authentication token
            matrix_access_token: Matrix access token for downloading authenticated media
            notify_callback: Optional callback function to send notifications to Matrix
            embedding_model: Embedding model to use for folders (default: letta/letta-free)
            max_retries: Maximum number of retries for API calls
            retry_delay: Base delay between retries (exponential backoff)
        """
        self.homeserver_url = homeserver_url
        self.letta_api_url = letta_api_url
        self.letta_token = letta_token
        self.matrix_access_token = matrix_access_token
        self.notify_callback = notify_callback
        self.embedding_model = embedding_model
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._folder_cache: Dict[str, str] = {}  # room_id -> folder_id cache
        self._cache_lock = asyncio.Lock()  # Protect cache from race conditions
    
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
        
    async def handle_file_event(self, event: Event, room_id: str, agent_id: Optional[str] = None) -> bool:
        """
        Handle a file upload event from Matrix
        
        Args:
            event: Matrix event object
            room_id: Room ID where file was uploaded
            agent_id: Optional agent ID to attach folder to
            
        Returns:
            True if file was processed successfully
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
            
            # Notify user that processing has started
            await self._notify(room_id, f"📄 Processing file: {metadata.file_name}")
            
            # Download file from Matrix
            file_path = await self._download_matrix_file(metadata)
            
            try:
                # Get or create Letta folder for this room
                folder_id = await self._get_or_create_folder(room_id, agent_id)
                
                # Upload to Letta
                job_id = await self._upload_to_letta(file_path, folder_id, metadata)
                
                # Poll for completion
                success = await self._poll_job_completion(job_id)
                
                if success:
                    logger.info(f"Successfully processed file {metadata.file_name} in Letta")
                    await self._notify(room_id, f"✅ File {metadata.file_name} uploaded successfully and indexed")
                else:
                    logger.warning(f"File processing job {job_id} did not complete successfully")
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
    
    def _extract_file_metadata(self, event: Event, room_id: str) -> Optional[FileMetadata]:
        """Extract file metadata from Matrix event"""
        try:
            # Check if this is a file message event
            if not hasattr(event, 'source') or not isinstance(event.source, dict):
                return None
            
            content = event.source.get('content', {})
            msgtype = content.get('msgtype')
            
            if msgtype != 'm.file':
                return None
            
            # Extract file information
            url = content.get('url')  # mxc:// URL
            body = content.get('body', 'unnamed_file')  # filename
            info = content.get('info', {})
            
            if not url:
                logger.warning("File event missing URL")
                return None
            
            return FileMetadata(
                file_url=url,
                file_name=body,
                file_type=info.get('mimetype', 'application/octet-stream'),
                file_size=info.get('size', 0),
                room_id=room_id,
                sender=event.sender,
                timestamp=event.server_timestamp,
                event_id=event.event_id
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
        # Check file size
        if metadata.file_size > MAX_FILE_SIZE:
            size_mb = metadata.file_size / (1024 * 1024)
            max_mb = MAX_FILE_SIZE / (1024 * 1024)
            return f"File '{metadata.file_name}' is too large ({size_mb:.1f}MB). Maximum size is {max_mb:.0f}MB."
        
        # Check file type
        if metadata.file_type not in SUPPORTED_FILE_TYPES:
            supported = ", ".join(SUPPORTED_FILE_TYPES.keys())
            return f"File type '{metadata.file_type}' is not supported. Supported types: {supported}"
        
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
        download_url = f"{self.homeserver_url}/_matrix/media/v3/download/{server_name}/{media_id}"
        
        logger.debug(f"Downloading file from {download_url}")
        
        # Create temporary file
        suffix = SUPPORTED_FILE_TYPES.get(metadata.file_type, '.bin')
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp_file.name
        
        # Prepare headers with authentication if available
        headers = {}
        if self.matrix_access_token:
            headers["Authorization"] = f"Bearer {self.matrix_access_token}"
        
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
    
    async def _get_or_create_folder(self, room_id: str, agent_id: Optional[str] = None) -> str:
        """
        Get or create Letta folder (source) for Matrix room
        
        Args:
            room_id: Matrix room ID
            agent_id: Optional agent ID to attach folder to
            
        Returns:
            Letta source/folder ID
        """
        # Check cache first (with lock for thread safety)
        async with self._cache_lock:
            if room_id in self._folder_cache:
                return self._folder_cache[room_id]
        
        async def _do_get_or_create() -> str:
            # Sanitize room_id for use in folder name (remove special chars)
            safe_room_id = room_id.replace("!", "").replace(":", "-")
            folder_name = f"matrix-{safe_room_id}"
            
            headers = {
                "Authorization": f"Bearer {self.letta_token}",
                "Content-Type": "application/json"
            }
            
            async with aiohttp.ClientSession() as session:
                # List sources to find existing one
                # Letta uses "sources" for file storage (folders are an alias)
                list_url = f"{self.letta_api_url}/v1/sources/"
                async with session.get(list_url, headers=headers) as response:
                    if response.status == 200:
                        sources = await response.json()
                        for source in sources:
                            if source.get('name') == folder_name:
                                folder_id = source['id']
                                logger.info(f"Found existing source {folder_name}: {folder_id}")
                                return folder_id
                
                # Create new source
                create_url = f"{self.letta_api_url}/v1/sources/"
                source_data = {
                    "name": folder_name,
                    "description": f"Documents from Matrix room {room_id}",
                    "embedding_config": {
                        "embedding_model": self.embedding_model,
                        "embedding_endpoint_type": "openai",
                        "embedding_dim": 1536,
                        "embedding_chunk_size": 300
                    }
                }
                
                async with session.post(create_url, headers=headers, json=source_data) as response:
                    if response.status not in [200, 201]:
                        error_text = await response.text()
                        raise FileUploadError(f"Failed to create source: {response.status} - {error_text}")
                    
                    source_response = await response.json()
                    folder_id = source_response['id']
                    logger.info(f"Created new source {folder_name}: {folder_id}")
                
                # Attach to agent if provided
                if agent_id:
                    attach_url = f"{self.letta_api_url}/v1/agents/{agent_id}/sources/{folder_id}"
                    
                    async with session.post(attach_url, headers=headers) as response:
                        if response.status in [200, 201, 204]:
                            logger.info(f"Attached source {folder_id} to agent {agent_id}")
                        else:
                            error_text = await response.text()
                            logger.warning(f"Failed to attach source to agent: {response.status} - {error_text}")
                
                return folder_id
        
        folder_id = await self._retry_async(_do_get_or_create, "Get/create Letta source")
        
        # Cache the folder ID
        async with self._cache_lock:
            self._folder_cache[room_id] = folder_id
        
        return folder_id
    
    async def _upload_to_letta(self, file_path: str, folder_id: str, metadata: FileMetadata) -> str:
        """
        Upload file to Letta folder
        
        Args:
            file_path: Local path to file
            folder_id: Letta folder ID
            metadata: File metadata
            
        Returns:
            Job ID for tracking upload progress
        """
        async def _do_upload() -> str:
            headers = {
                "Authorization": f"Bearer {self.letta_token}"
            }
            
            # Prepare metadata as JSON string
            file_metadata = json.dumps({
                "source": "matrix",
                "room_id": metadata.room_id,
                "sender": metadata.sender,
                "timestamp": str(metadata.timestamp),
                "original_filename": metadata.file_name,
                "event_id": metadata.event_id
            })
            
            upload_url = f"{self.letta_api_url}/v1/sources/{folder_id}/upload"
            
            async with aiohttp.ClientSession() as session:
                # Read file content and properly close handle
                with open(file_path, 'rb') as f:
                    file_content = f.read()
                
                # Create multipart form data
                data = aiohttp.FormData()
                data.add_field(
                    'file',
                    file_content,
                    filename=metadata.file_name,
                    content_type=metadata.file_type
                )
                
                async with session.post(upload_url, headers=headers, data=data) as response:
                    if response.status not in [200, 201, 202]:
                        error_text = await response.text()
                        raise FileUploadError(f"Failed to upload file to Letta: {response.status} - {error_text}")
                    
                    result = await response.json()
                    # Letta returns job info for async processing
                    job_id = result.get('job_id') or result.get('id')
                    
                    if not job_id:
                        # If no job_id, file was processed synchronously
                        logger.info(f"File uploaded to Letta (sync): {metadata.file_name}")
                        return "sync-complete"
                    
                    logger.info(f"File uploaded to Letta, job ID: {job_id}")
                    return job_id
        
        return await self._retry_async(_do_upload, "Letta file upload")
    
    async def _poll_job_completion(self, job_id: str, timeout: int = 300, interval: int = 2) -> bool:
        """
        Poll Letta job until completion
        
        Args:
            job_id: Job ID to poll
            timeout: Maximum time to wait (seconds)
            interval: Polling interval (seconds)
            
        Returns:
            True if job completed successfully
        """
        # Handle sync-complete case (file was processed immediately)
        if job_id == "sync-complete":
            return True
        
        headers = {
            "Authorization": f"Bearer {self.letta_token}"
        }
        
        job_url = f"{self.letta_api_url}/v1/jobs/{job_id}"
        elapsed = 0
        consecutive_errors = 0
        max_consecutive_errors = 3
        
        async with aiohttp.ClientSession() as session:
            while elapsed < timeout:
                try:
                    async with session.get(job_url, headers=headers) as response:
                        if response.status == 404:
                            # Job might not exist yet or was processed sync
                            logger.info(f"Job {job_id} not found, assuming completed")
                            return True
                        
                        if response.status != 200:
                            consecutive_errors += 1
                            if consecutive_errors >= max_consecutive_errors:
                                logger.error(f"Failed to get job status after {max_consecutive_errors} attempts")
                                return False
                            logger.warning(f"Failed to get job status: {response.status}")
                            await asyncio.sleep(interval)
                            elapsed += interval
                            continue
                        
                        consecutive_errors = 0  # Reset on success
                        job_data = await response.json()
                        status = job_data.get('status', '').lower()
                        
                        logger.debug(f"Job {job_id} status: {status}")
                        
                        if status in ['completed', 'success', 'done']:
                            logger.info(f"Job {job_id} completed successfully")
                            return True
                        elif status in ['failed', 'cancelled', 'error']:
                            error_msg = job_data.get('error', 'Unknown error')
                            logger.error(f"Job {job_id} {status}: {error_msg}")
                            return False
                        
                        # Still processing
                        await asyncio.sleep(interval)
                        elapsed += interval
                        
                except aiohttp.ClientError as e:
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(f"Network error polling job after {max_consecutive_errors} attempts: {e}")
                        return False
                    logger.warning(f"Network error polling job: {e}")
                    await asyncio.sleep(interval)
                    elapsed += interval
        
        logger.warning(f"Job {job_id} polling timed out after {timeout}s")
        return False
