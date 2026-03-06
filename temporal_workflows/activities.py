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
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

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

# Persistent storage directory for downloaded files
PERSISTENT_DOCUMENTS_DIR = os.getenv("PERSISTENT_DOCUMENTS_DIR", "/app/documents")
_HASH_INDEX_FILENAME = ".hashes.json"
_HASH_LOCK_FILENAME = ".hashes.lock"

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


def _sha256(path: str) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_index_paths() -> tuple[str, str]:
    """Return (index_path, lock_path) for the persistent hash index."""
    idx_path = os.path.join(PERSISTENT_DOCUMENTS_DIR, _HASH_INDEX_FILENAME)
    lock_path = os.path.join(PERSISTENT_DOCUMENTS_DIR, _HASH_LOCK_FILENAME)
    return idx_path, lock_path


def _load_hash_index() -> dict:
    """Load the hash index from disk. Returns {sha256: {filename, mxc_url, persistent_path, ts}}."""
    idx_path, _ = _hash_index_paths()
    if os.path.exists(idx_path):
        try:
            with open(idx_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_hash_index(index: dict) -> None:
    """Atomically write the hash index to disk."""
    idx_path, _ = _hash_index_paths()
    tmp_path = idx_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(index, f, indent=2)
    os.replace(tmp_path, idx_path)


def _mutate_hash_index_locked(mutate_fn):
    """Apply index mutation under an inter-process file lock."""
    os.makedirs(PERSISTENT_DOCUMENTS_DIR, exist_ok=True)
    idx_path, lock_path = _hash_index_paths()

    with open(lock_path, "a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            if os.path.exists(idx_path):
                try:
                    with open(idx_path, "r") as f:
                        index = json.load(f)
                except (json.JSONDecodeError, OSError):
                    index = {}
            else:
                index = {}

            result = mutate_fn(index)

            tmp_path = idx_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(index, f, indent=2)
            os.replace(tmp_path, idx_path)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _persist_file(
    temp_path: str, file_name: str, mxc_url: str
) -> tuple[str, str, bool]:
    """
    Compute SHA-256, check for duplicates, and persist to durable storage.

    Layout: {PERSISTENT_DOCUMENTS_DIR}/{media_id}/{original_filename}

    Returns:
        (persistent_path, file_hash, is_duplicate)
        - persistent_path: durable path on disk ("" on failure)
        - file_hash: SHA-256 hex digest
        - is_duplicate: True if this exact file was already processed
    """
    file_hash = ""
    try:
        file_hash = _sha256(temp_path)

        def _persist_under_lock(index: dict) -> tuple[str, str, bool]:
            if file_hash in index:
                existing = index[file_hash]
                import logging
                logging.getLogger(__name__).info(
                    f"Duplicate detected: {file_name} matches existing "
                    f"{existing.get('filename')} (hash={file_hash[:12]}...)"
                )
                return existing.get("persistent_path", ""), file_hash, True

            media_id = mxc_url.split("/")[-1] if mxc_url else "unknown"
            dest_dir = os.path.join(PERSISTENT_DOCUMENTS_DIR, media_id)
            os.makedirs(dest_dir, exist_ok=True)

            dest_path = os.path.join(dest_dir, file_name)
            shutil.copy2(temp_path, dest_path)

            index[file_hash] = {
                "filename": file_name,
                "mxc_url": mxc_url,
                "persistent_path": dest_path,
                "ts": time.time(),
            }
            return dest_path, file_hash, False

        return _mutate_hash_index_locked(_persist_under_lock)

    except Exception as exc:
        # Persistence is best-effort — don't block the pipeline
        import logging
        logging.getLogger(__name__).warning(
            f"Failed to persist file {file_name}: {exc}"
        )
        return "", file_hash, False

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
    persistent_path: str = ""  # Durable copy for long-term storage
    file_hash: str = ""  # SHA-256 hex digest
    is_duplicate: bool = False  # True if identical content was already processed


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
    room_id: str = ""
    conversation_id: str = ""


@dataclass
class NotifyAgentResult:
    """Output of notify_letta_agent activity."""
    success: bool
    duration_ms: int = 0
    error: Optional[str] = None
    response_text: Optional[str] = None


@dataclass
class MatrixStatusInput:
    """Input for update_matrix_status activity."""
    room_id: str
    message: str
    agent_id: str  # Agent ID for send-as-agent endpoint
    event_id: Optional[str] = None  # If set, edit this message; otherwise send new
    msgtype: str = "m.notice"  # Message type: m.notice for status, m.text for agent replies

@dataclass
class MatrixStatusResult:
    """Output of update_matrix_status activity."""
    event_id: Optional[str] = None  # Event ID of sent/edited message
    success: bool = True
    duration_ms: int = 0


@dataclass
class CleanupArtifactsInput:
    """Input for cleanup_file_artifacts activity."""
    temp_file_path: str = ""
    persistent_path: str = ""
    file_hash: str = ""
    remove_persistent: bool = False


@dataclass
class CleanupArtifactsResult:
    """Output of cleanup_file_artifacts activity."""
    temp_removed: bool = False
    persistent_removed: bool = False
    hash_entry_removed: bool = False
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

        # Persist a durable copy and check for duplicates via SHA-256
        persistent_path, file_hash, is_duplicate = _persist_file(
            temp_path, input.file_name, input.mxc_url
        )

        elapsed = int((time.monotonic() - start) * 1000)
        dup_tag = " [DUPLICATE]" if is_duplicate else ""
        activity.logger.info(
            f"Downloaded {input.file_name} → {temp_path} ({file_size} bytes, {elapsed}ms), "
            f"hash={file_hash[:12]}..., persisted → {persistent_path}{dup_tag}"
        )
        return DownloadResult(
            file_path=temp_path, file_size=file_size,
            duration_ms=elapsed, persistent_path=persistent_path,
            file_hash=file_hash, is_duplicate=is_duplicate,
        )

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
        session_payload = {
            "type": "session_start",
            "agent_id": input.agent_id,
        }
        if input.conversation_id:
            session_payload["conversation_id"] = input.conversation_id

        session_start = json.dumps(session_payload)
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
        msg_payload_body: dict[str, Any] = {
            "type": "message",
            "content": input.message,
            "request_id": f"temporal-notify-{uuid.uuid4()}",
        }
        if input.room_id:
            msg_payload_body["source"] = {
                "channel": "matrix",
                "chatId": input.room_id,
            }

        msg_payload = json.dumps(msg_payload_body)
        await ws.send(msg_payload)

        # Step 3: Consume events until we get a result
        response_chunks: list[str] = []
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

            if event_type == "stream" and event.get("event") == "assistant":
                chunk = event.get("content")
                if chunk:
                    response_chunks.append(chunk)

            if event_type == "result":
                elapsed = int((time.monotonic() - start) * 1000)
                response_text = "".join(response_chunks).strip() or None
                activity.logger.info(
                    f"Notified agent {input.agent_id} via WS gateway, {elapsed}ms, "
                    f"response_len={len(response_text) if response_text else 0}"
                )
                return NotifyAgentResult(
                    success=True,
                    duration_ms=elapsed,
                    response_text=response_text,
                )

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
                activity.logger.info(
                    f"Editing status message {input.event_id} in {input.room_id}"
                )
                response = await client.post(
                    f"{MATRIX_API_URL}/api/v1/messages/edit-as-agent",
                    json={
                        "agent_id": input.agent_id,
                        "room_id": input.room_id,
                        "event_id": input.event_id,
                        "message": input.message,
                        "msgtype": input.msgtype,
                    },
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
                        "msgtype": input.msgtype,
                    },
                )

            if response.status_code >= 400:
                raise MatrixAPIError(
                    f"Matrix API {response.status_code}: {response.text[:500]}"
                )

            result = response.json()
            if result.get("success") is False:
                raise MatrixAPIError(
                    f"Matrix API logical failure: {result.get('error', 'unknown error')}"
                )

            event_id = result.get("event_id") or input.event_id
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


@activity.defn
async def cleanup_file_artifacts(input: CleanupArtifactsInput) -> CleanupArtifactsResult:
    """Cleanup temporary/persistent artifacts and hash index entries."""
    start = time.monotonic()
    temp_removed = False
    persistent_removed = False
    hash_entry_removed = False

    try:
        if input.temp_file_path and os.path.exists(input.temp_file_path):
            os.unlink(input.temp_file_path)
            temp_removed = True

        if input.remove_persistent and input.file_hash:
            def _remove_hash(index: dict) -> bool:
                entry = index.get(input.file_hash)
                if not entry:
                    return False
                if input.persistent_path and entry.get("persistent_path") != input.persistent_path:
                    return False
                index.pop(input.file_hash, None)
                return True

            hash_entry_removed = _mutate_hash_index_locked(_remove_hash)

        if input.remove_persistent and input.persistent_path and os.path.exists(input.persistent_path):
            os.unlink(input.persistent_path)
            persistent_removed = True
            parent = os.path.dirname(input.persistent_path)
            if parent and os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)

        elapsed = int((time.monotonic() - start) * 1000)
        activity.logger.info(
            "Cleanup completed: "
            f"temp_removed={temp_removed}, persistent_removed={persistent_removed}, "
            f"hash_entry_removed={hash_entry_removed}, {elapsed}ms"
        )
        return CleanupArtifactsResult(
            temp_removed=temp_removed,
            persistent_removed=persistent_removed,
            hash_entry_removed=hash_entry_removed,
            duration_ms=elapsed,
        )
    except Exception as e:
        raise FileActivityError(f"Failed cleanup artifacts: {e}") from e
