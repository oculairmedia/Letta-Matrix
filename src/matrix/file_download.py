import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

import aiohttp
from nio import Event

# Known file types - used for extension mapping and temp file suffixes.
# NOT used for rejection: MarkItDown's PlainTextConverter handles any text-like file.
SUPPORTED_FILE_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "application/json": ".json",
    # Document types (MarkItDown)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/xhtml+xml": ".xhtml",
    "application/epub+zip": ".epub",
    # Image types (for vision-capable models)
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/aac": ".aac",
    "text/calendar": ".ics",
    "application/octet-stream": None,  # Accept but determine by extension
}

# Extension-to-MIME mapping for application/octet-stream resolution (routing to image/audio handlers)
SUPPORTED_EXTENSIONS = {
    # Documents
    ".pdf",
    ".txt",
    ".md",
    ".json",
    ".markdown",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".csv",
    ".html",
    ".htm",
    ".xhtml",
    ".epub",
    ".rtf",
    ".odt",
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".ogg",
    ".mp3",
    ".m4a",
    ".wav",
    ".webm",
    ".flac",
    ".aac",
    ".oga",
    # Calendar
    ".ics",
}

# File size limit (50MB)
MAX_FILE_SIZE = 50 * 1024 * 1024


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


class FileDownloadService:
    def __init__(
        self,
        homeserver_url: str,
        matrix_access_token: Optional[str],
        logger: logging.Logger,
    ):
        self.homeserver_url = homeserver_url
        self.matrix_access_token = matrix_access_token
        self.logger = logger

    def extract_file_metadata(self, event: Event, room_id: str) -> Optional[FileMetadata]:
        """Extract file metadata from Matrix event"""
        try:
            # Check if this is a file message event
            if not hasattr(event, "source") or not isinstance(event.source, dict):
                return None

            content = event.source.get("content", {})
            msgtype = content.get("msgtype")

            if msgtype not in ["m.file", "m.image", "m.audio"]:
                return None

            # Extract file information
            url = content.get("url")  # mxc:// URL
            body = content.get("body", "unnamed_file")  # filename or caption
            info = content.get("info", {})

            if not url:
                self.logger.warning("File event missing URL")
                return None

            content_filename = content.get("filename")
            info_filename = info.get("filename")
            resolved_filename = content_filename or info_filename
            actual_filename = resolved_filename or body

            # Determine if body is a caption (different from filename)
            # If body looks like a filename (has extension), use it as filename
            # Otherwise, treat it as a caption/question from the user
            caption = None
            _, ext = os.path.splitext(body)
            if ext and ext.lower() in SUPPORTED_EXTENSIONS and not resolved_filename:
                actual_filename = body
            elif resolved_filename and body != resolved_filename:
                caption = body
                self.logger.info(f"Detected caption on file '{actual_filename}': {caption[:50]}...")
            elif not ext:
                mimetype = info.get("mimetype", "application/octet-stream")
                mime_ext = SUPPORTED_FILE_TYPES.get(mimetype) or ".bin"
                # Use mxc media_id as a stable filename
                media_id = url.split("/")[-1] if url else "unknown"
                actual_filename = f"{media_id}{mime_ext}"
                caption = body
                self.logger.info(
                    f"Caption detected (no info.filename): '{caption[:50]}...' - "
                    f"derived filename: {actual_filename}"
                )

            return FileMetadata(
                file_url=url,
                file_name=actual_filename,
                file_type=info.get("mimetype", "application/octet-stream"),
                file_size=info.get("size", 0),
                room_id=room_id,
                sender=event.sender,
                timestamp=event.server_timestamp,
                event_id=event.event_id,
                caption=caption,
            )

        except Exception as e:
            self.logger.error(f"Error extracting file metadata: {e}", exc_info=True)
            return None

    def validate_file(self, metadata: FileMetadata) -> Optional[str]:
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

        # Try-first approach: accept any file type. MarkItDown handles text-like files
        # via PlainTextConverter fallback. Only images/audio need special MIME routing.
        # We log unrecognized types but don't reject them.
        if metadata.file_type not in SUPPORTED_FILE_TYPES:
            self.logger.info(
                f"Non-whitelisted MIME type '{metadata.file_type}' for {metadata.file_name} - will try MarkItDown"
            )

        # For application/octet-stream, try to resolve MIME from extension for routing
        if metadata.file_type == "application/octet-stream":
            _, ext = os.path.splitext(metadata.file_name.lower())
            if ext not in SUPPORTED_EXTENSIONS:
                self.logger.info(f"Unknown extension '{ext}' for octet-stream - will try MarkItDown")
            # Update the file_type based on extension for better handling
            if ext in [".md", ".markdown"]:
                metadata.file_type = "text/markdown"
            elif ext == ".txt":
                metadata.file_type = "text/plain"
            elif ext == ".pdf":
                metadata.file_type = "application/pdf"
            elif ext == ".json":
                metadata.file_type = "application/json"
            elif ext in [".jpg", ".jpeg"]:
                metadata.file_type = "image/jpeg"
            elif ext == ".png":
                metadata.file_type = "image/png"
            elif ext == ".gif":
                metadata.file_type = "image/gif"
            elif ext == ".webp":
                metadata.file_type = "image/webp"
            elif ext in [".bmp"]:
                metadata.file_type = "image/bmp"
            elif ext in [".tiff", ".tif"]:
                metadata.file_type = "image/tiff"
            elif ext in [".ogg", ".oga"]:
                metadata.file_type = "audio/ogg"
            elif ext == ".mp3":
                metadata.file_type = "audio/mpeg"
            elif ext == ".m4a":
                metadata.file_type = "audio/mp4"
            elif ext == ".wav":
                metadata.file_type = "audio/wav"
            elif ext == ".webm":
                metadata.file_type = "audio/webm"
            elif ext == ".flac":
                metadata.file_type = "audio/flac"
            elif ext == ".aac":
                metadata.file_type = "audio/aac"
            # Document types
            elif ext == ".docx":
                metadata.file_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            elif ext == ".doc":
                metadata.file_type = "application/msword"
            elif ext == ".pptx":
                metadata.file_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            elif ext == ".ppt":
                metadata.file_type = "application/vnd.ms-powerpoint"
            elif ext == ".xlsx":
                metadata.file_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif ext == ".xls":
                metadata.file_type = "application/vnd.ms-excel"
            elif ext == ".csv":
                metadata.file_type = "text/csv"
            elif ext in [".html", ".htm"]:
                metadata.file_type = "text/html"
            elif ext == ".epub":
                metadata.file_type = "application/epub+zip"
            elif ext == ".ics":
                metadata.file_type = "text/calendar"

        return None

    async def download_file(self, metadata: FileMetadata) -> str:
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
        if not mxc_url.startswith("mxc://"):
            raise FileUploadError(f"Invalid mxc:// URL: {mxc_url}")

        # Parse mxc URL
        parts = mxc_url[6:].split("/", 1)  # Remove "mxc://" prefix
        if len(parts) != 2:
            raise FileUploadError(f"Malformed mxc:// URL: {mxc_url}")

        server_name, media_id = parts
        # Use authenticated media endpoint (MSC3916) - requires auth token
        # The legacy /_matrix/media/v3/download endpoint may be disabled on some servers
        download_url = f"{self.homeserver_url}/_matrix/client/v1/media/download/{server_name}/{media_id}"

        self.logger.debug(f"Downloading file from {download_url}")

        # Create temporary file
        suffix = SUPPORTED_FILE_TYPES.get(metadata.file_type, ".bin")
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = temp_file.name

        # Prepare headers with authentication if available
        headers = {}
        self.logger.debug(f"Download - matrix_access_token present: {bool(self.matrix_access_token)}")
        if self.matrix_access_token:
            headers["Authorization"] = f"Bearer {self.matrix_access_token}"
            self.logger.info(f"Using Matrix auth token for download (length: {len(self.matrix_access_token)})")
        else:
            self.logger.warning("No Matrix access token available for download - may fail with 403")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise FileUploadError(f"Failed to download file: {response.status} - {error_text}")

                    # Write to temporary file
                    with open(temp_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)

            self.logger.info(f"Downloaded file to {temp_path} ({os.path.getsize(temp_path)} bytes)")
            return temp_path

        except Exception as e:
            # Clean up on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise FileUploadError(f"Error downloading file: {e}")
