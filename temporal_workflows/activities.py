"""
Temporal activities for Matrix file processing.

Each activity is an atomic unit of work that interacts with external systems.
Temporal handles retries, timeouts, and failure recording automatically.

Activities:
  1. download_file_from_matrix — download via authenticated media API
  2. parse_with_markitdown — extract text from documents (CPU-bound, process pool)
  3. ingest_to_haystack — POST to Hayhooks ingest_document pipeline
  4. notify_letta_agent — send result message to Letta agent via WS gateway
  5. update_matrix_status — edit/send Matrix room status messages
"""

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
import websockets
from temporalio import activity


# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------

MATRIX_HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://tuwunel:6167")
MATRIX_ACCESS_TOKEN = os.getenv("MATRIX_ACCESS_TOKEN", "")
MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
LETTA_API_URL = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
LETTA_TOKEN = os.getenv("LETTA_TOKEN", "")
LETTA_GATEWAY_URL = os.getenv("LETTA_GATEWAY_URL", "ws://192.168.50.90:8407/api/v1/agent-gateway")
LETTA_GATEWAY_API_KEY = os.getenv("LETTA_GATEWAY_API_KEY", "")
HAYHOOKS_INGEST_URL = os.getenv(
    "HAYHOOKS_INGEST_URL", "http://192.168.50.90:1416/ingest_document/run"
)

# Known file-type → extension mapping (mirrors file_handler.py SUPPORTED_FILE_TYPES)
_EXT_MAP = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "application/json": ".json",
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
    "text/calendar": ".ics",
}


# ---------------------------------------------------------------------------
# Error hierarchy (mirrors Letta temporal_workflows pattern)
# ---------------------------------------------------------------------------

class FileActivityError(Exception):
    """Base error for file processing activities."""
    pass


class DownloadError(FileActivityError):
    """Failed to download file from Matrix. Retryable (network/server issues)."""
    pass


class ParseError(FileActivityError):
    """Failed to parse document. Non-retryable (bad file content)."""
    pass


class IngestError(FileActivityError):
    """Failed to ingest into Haystack. Retryable (service may be temporarily down)."""
    pass


class NotifyError(FileActivityError):
    """Failed to notify Letta agent. Retryable."""
    pass


class MatrixAPIError(FileActivityError):
    """Failed to update Matrix room status. Retryable."""
    pass


# ---------------------------------------------------------------------------
# Dataclasses for typed inputs/outputs
# ---------------------------------------------------------------------------

@dataclass
class DownloadInput:
    """Input for download_file_from_matrix activity."""
    mxc_url: str
    file_type: str  # MIME type
    file_name: str


@dataclass
class DownloadResult:
    """Output of download_file_from_matrix activity."""
    file_path: str  # Path to temporary file on disk
    file_size: int
    duration_ms: int


@dataclass
class ParseInput:
    """Input for parse_with_markitdown activity."""
    file_path: str
    file_name: str


@dataclass
class ParseResult:
    """Output of parse_with_markitdown activity."""
    text: str
    page_count: Optional[int] = None
    was_ocr: bool = False
    char_count: int = 0
    duration_ms: int = 0


@dataclass
class IngestInput:
    """Input for ingest_to_haystack activity."""
    text: str
    filename: str
    room_id: str
    sender: str


@dataclass
class IngestResult:
    """Output of ingest_to_haystack activity."""
    success: bool
    chunks_stored: int = 0
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class NotifyAgentInput:
    """Input for notify_letta_agent activity."""
    agent_id: str
    message: str


@dataclass
class NotifyAgentResult:
    """Output of notify_letta_agent activity."""
    success: bool
    duration_ms: int = 0
    error: Optional[str] = None


@dataclass
class MatrixStatusInput:
    """Input for update_matrix_status activity."""
    room_id: str
    message: str
    agent_id: str  # Agent ID for send-as-agent endpoint
    event_id: Optional[str] = None  # If set, edit this message; otherwise send new

@dataclass
class MatrixStatusResult:
    """Output of update_matrix_status activity."""
    event_id: Optional[str] = None  # Event ID of sent/edited message
    success: bool = True
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Activity 1: Download file from Matrix
# ---------------------------------------------------------------------------

@activity.defn
async def download_file_from_matrix(input: DownloadInput) -> DownloadResult:
    """
    Download a file from the Matrix authenticated media API.

    Parses an mxc:// URL and fetches the file via the homeserver's
    /_matrix/client/v1/media/download endpoint with Bearer auth.
    Writes to a temporary file and returns the path.

    Raises:
        DownloadError: On network/HTTP failures (retryable by Temporal).
    """
    start = time.monotonic()
    activity.logger.info(f"Downloading {input.file_name} from {input.mxc_url}")

    # Parse mxc:// URL → server_name / media_id
    if not input.mxc_url.startswith("mxc://"):
        raise DownloadError(f"Invalid mxc:// URL: {input.mxc_url}")

    parts = input.mxc_url[6:].split("/", 1)
    if len(parts) != 2:
        raise DownloadError(f"Malformed mxc:// URL: {input.mxc_url}")

    server_name, media_id = parts
    download_url = (
        f"{MATRIX_HOMESERVER_URL}/_matrix/client/v1/media/download"
        f"/{server_name}/{media_id}"
    )

    suffix = _EXT_MAP.get(input.file_type, ".bin")
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temp_path = temp.name
    temp.close()

    headers = {}
    if MATRIX_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {MATRIX_ACCESS_TOKEN}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(download_url, headers=headers)
            if response.status_code != 200:
                raise DownloadError(
                    f"HTTP {response.status_code} downloading {input.file_name}: "
                    f"{response.text[:500]}"
                )
            with open(temp_path, "wb") as f:
                f.write(response.content)

        file_size = os.path.getsize(temp_path)
        elapsed = int((time.monotonic() - start) * 1000)
        activity.logger.info(
            f"Downloaded {input.file_name} → {temp_path} ({file_size} bytes, {elapsed}ms)"
        )
        return DownloadResult(file_path=temp_path, file_size=file_size, duration_ms=elapsed)

    except DownloadError:
        # Clean up temp file on our own errors
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    except httpx.TimeoutException as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise DownloadError(f"Timeout downloading {input.file_name}: {e}") from e
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise DownloadError(f"Error downloading {input.file_name}: {e}") from e


# ---------------------------------------------------------------------------
# Activity 2: Parse document with MarkItDown
# ---------------------------------------------------------------------------

@activity.defn
async def parse_with_markitdown(input: ParseInput) -> ParseResult:
    """
    Extract text from a document using MarkItDown.

    This is CPU-bound work (especially OCR). The document_parser module
    already runs MarkItDown in a ProcessPoolExecutor internally, so we
    call it directly.

    The temporary file is cleaned up after parsing.

    Raises:
        ParseError: On extraction failure (non-retryable — bad file content).
    """
    start = time.monotonic()
    activity.logger.info(f"Parsing document: {input.file_name}")

    try:
        # Import here to avoid sandbox issues (same pattern as Letta/Graphiti workers)
        from src.matrix.document_parser import parse_document, DocumentParseConfig

        config = DocumentParseConfig.from_env()
        result = await parse_document(
            file_path=input.file_path,
            filename=input.file_name,
            config=config,
        )

        if result.error:
            raise ParseError(
                f"MarkItDown failed for {input.file_name}: {result.error}"
            )

        text = (result.text or "").strip()
        if len(text) < 10:
            raise ParseError(
                f"Insufficient content extracted from {input.file_name} "
                f"({len(text)} chars)"
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
        # Always clean up the downloaded temp file
        if input.file_path and os.path.exists(input.file_path):
            os.unlink(input.file_path)
            activity.logger.debug(f"Cleaned up temp file: {input.file_path}")


# ---------------------------------------------------------------------------
# Activity 3: Ingest into Haystack/Weaviate
# ---------------------------------------------------------------------------

@activity.defn
async def ingest_to_haystack(input: IngestInput) -> IngestResult:
    """
    POST extracted text to the Hayhooks ingest_document pipeline.

    The pipeline chunks the text, embeds via LiteLLM, and writes to
    the shared Weaviate document store.

    Raises:
        IngestError: On HTTP/service failures (retryable).
    """
    start = time.monotonic()
    activity.logger.info(
        f"Ingesting {input.filename} ({len(input.text)} chars) to Haystack"
    )

    payload = {
        "text": input.text,
        "filename": input.filename,
        "room_id": input.room_id,
        "sender": input.sender,
    }

    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(HAYHOOKS_INGEST_URL, json=payload)

            if response.status_code != 200:
                raise IngestError(
                    f"Hayhooks HTTP {response.status_code} for {input.filename}: "
                    f"{response.text[:500]}"
                )

            result = response.json()

            # Pipeline returns JSON-encoded string in 'result' key
            result_data = result
            if isinstance(result.get("result"), str):
                result_data = json.loads(result["result"])

            status = result_data.get("status", "")
            if status == "ok":
                chunks = result_data.get("chunks_stored", 0)
                elapsed = int((time.monotonic() - start) * 1000)
                activity.logger.info(
                    f"Ingested {input.filename}: {chunks} chunks stored, {elapsed}ms"
                )
                return IngestResult(
                    success=True, chunks_stored=chunks, duration_ms=elapsed
                )
            else:
                detail = result_data.get("detail", "Unknown error")
                raise IngestError(
                    f"Hayhooks ingest error for {input.filename}: {detail}"
                )

    except IngestError:
        raise
    except httpx.TimeoutException as e:
        raise IngestError(
            f"Hayhooks timeout for {input.filename}: {e}"
        ) from e
    except Exception as e:
        raise IngestError(
            f"Unexpected error ingesting {input.filename}: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Activity 4: Notify Letta agent
# ---------------------------------------------------------------------------

@activity.defn
async def notify_letta_agent(input: NotifyAgentInput) -> NotifyAgentResult:
    """
    Send a user message to a Letta agent via the lettabot WS gateway.

    Opens a WebSocket connection to the agent-gateway, sends a session_start
    followed by the message, and waits for a result event.

    Raises:
        NotifyError: On gateway/connection failures (retryable).
    """
    start = time.monotonic()
    activity.logger.info(f"Notifying agent {input.agent_id} via WS gateway")

    extra_headers = {}
    if LETTA_GATEWAY_API_KEY:
        extra_headers["X-Api-Key"] = LETTA_GATEWAY_API_KEY

    ws = None
    try:
        ws = await websockets.connect(
            LETTA_GATEWAY_URL,
            additional_headers=extra_headers,
            max_size=2**22,  # 4 MB frames
            close_timeout=5,
            open_timeout=10,
        )

        # Step 1: Start session for this agent
        session_start = json.dumps({
            "type": "session_start",
            "agent_id": input.agent_id,
        })
        await ws.send(session_start)

        # Wait for session_init acknowledgement
        raw_init = await asyncio.wait_for(ws.recv(), timeout=15.0)
        init_event = json.loads(raw_init)

        if init_event.get("type") == "error":
            raise NotifyError(
                f"Gateway session error for agent {input.agent_id}: "
                f"{init_event.get('message', 'Unknown error')}"
            )
        if init_event.get("type") != "session_init":
            raise NotifyError(
                f"Expected session_init from gateway, got {init_event.get('type')}"
            )

        activity.logger.info(
            f"Gateway session established for agent {input.agent_id}, "
            f"session={init_event.get('session_id')}"
        )

        # Step 2: Send the message
        msg_payload = json.dumps({
            "type": "message",
            "content": input.message,
            "request_id": f"temporal-notify-{input.agent_id[:8]}",
        })
        await ws.send(msg_payload)

        # Step 3: Consume events until we get a result
        async for raw in ws:
            try:
                event = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            event_type = event.get("type")

            if event_type == "error":
                raise NotifyError(
                    f"Gateway error for agent {input.agent_id}: "
                    f"{event.get('message', 'Unknown error')}"
                )

            if event_type == "result":
                elapsed = int((time.monotonic() - start) * 1000)
                activity.logger.info(
                    f"Notified agent {input.agent_id} via WS gateway, {elapsed}ms"
                )
                return NotifyAgentResult(success=True, duration_ms=elapsed)

            # stream, session_init, etc — just consume and continue

        # Stream ended without result
        raise NotifyError(
            f"Gateway stream ended without result for agent {input.agent_id}"
        )

    except NotifyError:
        raise
    except asyncio.TimeoutError as e:
        raise NotifyError(
            f"Timeout connecting to gateway for agent {input.agent_id}: {e}"
        ) from e
    except websockets.ConnectionClosed as e:
        raise NotifyError(
            f"Gateway connection closed for agent {input.agent_id}: {e}"
        ) from e
    except Exception as e:
        raise NotifyError(
            f"Error notifying agent {input.agent_id}: {e}"
        ) from e
    finally:
        if ws:
            try:
                await ws.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Activity 5: Update Matrix room status
# ---------------------------------------------------------------------------

@activity.defn
async def update_matrix_status(input: MatrixStatusInput) -> MatrixStatusResult:
    """
    Send or edit a status message in a Matrix room via the Matrix API service.

    Uses the matrix-api /api/v1/messages/send-as-agent endpoint to send
    messages as the agent's Matrix identity.

    Raises:
        MatrixAPIError: On failures (retryable).
    """
    start = time.monotonic()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if input.event_id:
                # Edit existing message — use Tuwunel C-S API directly
                # since matrix-api doesn't have an edit endpoint
                activity.logger.info(
                    f"Editing status message {input.event_id} in {input.room_id}"
                )
                # For edits, fall back to the C-S API with admin token
                edit_body = {
                    "msgtype": "m.notice",
                    "body": f"* {input.message}",
                    "m.new_content": {
                        "msgtype": "m.notice",
                        "body": input.message,
                    },
                    "m.relates_to": {
                        "rel_type": "m.replace",
                        "event_id": input.event_id,
                    },
                }
                import uuid
                txn_id = str(uuid.uuid4())
                headers = {}
                if MATRIX_ACCESS_TOKEN:
                    headers["Authorization"] = f"Bearer {MATRIX_ACCESS_TOKEN}"
                response = await client.put(
                    f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/rooms/{input.room_id}/send/m.room.message/{txn_id}",
                    json=edit_body,
                    headers=headers,
                )
            else:
                # Send new message via matrix-api send-as-agent
                activity.logger.info(
                    f"Sending status message to {input.room_id} as agent {input.agent_id}"
                )
                response = await client.post(
                    f"{MATRIX_API_URL}/api/v1/messages/send-as-agent",
                    json={
                        "agent_id": input.agent_id,
                        "room_id": input.room_id,
                        "message": input.message,
                        "msgtype": "m.notice",
                    },
                )

            if response.status_code >= 400:
                raise MatrixAPIError(
                    f"Matrix API {response.status_code}: {response.text[:500]}"
                )

            result = response.json()
            event_id = result.get("event_id", input.event_id)
            elapsed = int((time.monotonic() - start) * 1000)

            activity.logger.info(
                f"Matrix status updated in {input.room_id}, event={event_id}, {elapsed}ms"
            )
            return MatrixStatusResult(
                event_id=event_id, success=True, duration_ms=elapsed
            )

    except MatrixAPIError:
        raise
    except httpx.TimeoutException as e:
        raise MatrixAPIError(
            f"Timeout updating Matrix status in {input.room_id}: {e}"
        ) from e
    except Exception as e:
        raise MatrixAPIError(
            f"Error updating Matrix status in {input.room_id}: {e}"
        ) from e
