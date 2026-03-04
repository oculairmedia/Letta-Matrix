"""
FileProcessingWorkflow — Orchestrates async document processing via Temporal.

Pipeline:
  1. update_matrix_status  → "Processing document..."
  2. download_file_from_matrix → temp file on disk
  3. parse_with_markitdown → extracted text
  4. ingest_to_haystack → chunks stored in Weaviate
  5. notify_letta_agent → agent knows document is searchable
  6. update_matrix_status → final status in room

Supports signals (pause/resume/cancel) and queries (get_status) for
observability via Temporal UI.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import activities through sandbox passthrough (same pattern as Letta/Graphiti)
with workflow.unsafe.imports_passed_through():
    from temporal_workflows.activities import (
        download_file_from_matrix,
        parse_with_markitdown,
        ingest_to_haystack,
        notify_letta_agent,
        update_matrix_status,
        DownloadInput,
        ParseInput,
        IngestInput,
        NotifyAgentInput,
        MatrixStatusInput,
        DownloadResult,
        ParseResult,
        IngestResult,
        NotifyAgentResult,
        MatrixStatusResult,
        ParseError,
    )


# ---------------------------------------------------------------------------
# Workflow status
# ---------------------------------------------------------------------------

class WorkflowStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PARSING = "parsing"
    INGESTING = "ingesting"
    NOTIFYING = "notifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


# ---------------------------------------------------------------------------
# Workflow input/output
# ---------------------------------------------------------------------------

@dataclass
class FileProcessingInput:
    """Input parameters for the file processing workflow."""
    # Matrix file info
    mxc_url: str
    file_name: str
    file_type: str  # MIME type
    room_id: str
    sender: str
    event_id: str  # Original upload event ID

    # Letta agent to notify
    agent_id: str

    # Optional user caption/question about the document
    caption: Optional[str] = None

    # Retry config (per-activity overrides use their own defaults)
    max_retries: int = 3
    retry_backoff_seconds: int = 2


@dataclass
class FileProcessingResult:
    """Result of the file processing workflow."""
    status: str  # WorkflowStatus value
    file_name: str = ""
    char_count: int = 0
    page_count: Optional[int] = None
    was_ocr: bool = False
    chunks_stored: int = 0
    error: Optional[str] = None

    # Duration tracking per stage (ms)
    download_ms: int = 0
    parse_ms: int = 0
    ingest_ms: int = 0
    notify_ms: int = 0
    total_ms: int = 0


# ---------------------------------------------------------------------------
# Retry policies (per-activity, different timeouts)
# ---------------------------------------------------------------------------

# Network I/O: short timeout, moderate retries
_DOWNLOAD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

# CPU-bound: longer timeout, fewer retries (bad content won't get better)
_PARSE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=2,
    backoff_coefficient=2.0,
    non_retryable_error_types=["ParseError"],
)

# External service: long timeout, moderate retries
_INGEST_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

# API call: short timeout, moderate retries
_NOTIFY_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)

# Status messages: best-effort, quick timeout
_STATUS_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=2,
    backoff_coefficient=2.0,
)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

@workflow.defn
class FileProcessingWorkflow:
    """
    Durable workflow for processing document uploads from Matrix.

    Chains 5 activities with per-activity retry policies and timeouts.
    Supports pause/resume/cancel signals and status queries.
    """

    def __init__(self) -> None:
        self._status = WorkflowStatus.PENDING
        self._paused = False
        self._cancelled = False
        self._status_event_id: Optional[str] = None  # Track room status message

    @workflow.run
    async def run(self, input: FileProcessingInput) -> FileProcessingResult:
        """Main workflow execution — chains all 5 activities."""

        import time

        workflow.logger.info(
            f"Starting file processing: {input.file_name} in {input.room_id} "
            f"for agent {input.agent_id}"
        )

        result = FileProcessingResult(
            status=WorkflowStatus.PENDING.value,
            file_name=input.file_name,
        )
        workflow_start = workflow.now()

        try:
            # ---------------------------------------------------------------
            # Step 0: Send initial status message to room
            # ---------------------------------------------------------------
            try:
                status_result: MatrixStatusResult = await workflow.execute_activity(
                    update_matrix_status,
                    MatrixStatusInput(
                        room_id=input.room_id,
                        message=f"📄 Processing document: {input.file_name}...",
                        agent_id=input.agent_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_STATUS_RETRY,
                )
                self._status_event_id = status_result.event_id
            except Exception as e:
                # Status messages are best-effort — don't fail the workflow
                workflow.logger.warning(f"Failed to send initial status: {e}")

            # ---------------------------------------------------------------
            # Step 1: Download file from Matrix
            # ---------------------------------------------------------------
            await self._check_paused()
            if self._cancelled:
                result.status = WorkflowStatus.CANCELLED.value
                return result

            self._status = WorkflowStatus.DOWNLOADING
            download_result: DownloadResult = await workflow.execute_activity(
                download_file_from_matrix,
                DownloadInput(
                    mxc_url=input.mxc_url,
                    file_type=input.file_type,
                    file_name=input.file_name,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_DOWNLOAD_RETRY,
            )
            result.download_ms = download_result.duration_ms

            # ---------------------------------------------------------------
            # Step 2: Parse document with MarkItDown
            # ---------------------------------------------------------------
            await self._check_paused()
            if self._cancelled:
                result.status = WorkflowStatus.CANCELLED.value
                return result

            self._status = WorkflowStatus.PARSING

            # Update status in room
            try:
                if self._status_event_id:
                    await workflow.execute_activity(
                        update_matrix_status,
                        MatrixStatusInput(
                            room_id=input.room_id,
                            message=f"📄 Extracting text from {input.file_name}...",
                            agent_id=input.agent_id,
                            event_id=self._status_event_id,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=_STATUS_RETRY,
                    )
            except Exception:
                pass  # Best-effort

            parse_result: ParseResult = await workflow.execute_activity(
                parse_with_markitdown,
                ParseInput(
                    file_path=download_result.file_path,
                    file_name=input.file_name,
                ),
                start_to_close_timeout=timedelta(seconds=300),
                retry_policy=_PARSE_RETRY,
            )
            result.char_count = parse_result.char_count
            result.page_count = parse_result.page_count
            result.was_ocr = parse_result.was_ocr
            result.parse_ms = parse_result.duration_ms

            # ---------------------------------------------------------------
            # Step 3: Ingest into Haystack/Weaviate
            # ---------------------------------------------------------------
            await self._check_paused()
            if self._cancelled:
                result.status = WorkflowStatus.CANCELLED.value
                return result

            self._status = WorkflowStatus.INGESTING

            try:
                if self._status_event_id:
                    await workflow.execute_activity(
                        update_matrix_status,
                        MatrixStatusInput(
                            room_id=input.room_id,
                            message=f"📄 Indexing {input.file_name} ({parse_result.char_count:,} chars)...",
                            agent_id=input.agent_id,
                            event_id=self._status_event_id,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=_STATUS_RETRY,
                    )
            except Exception:
                pass

            ingest_result: IngestResult = await workflow.execute_activity(
                ingest_to_haystack,
                IngestInput(
                    text=parse_result.text,
                    filename=input.file_name,
                    room_id=input.room_id,
                    sender=input.sender,
                ),
                start_to_close_timeout=timedelta(seconds=600),
                retry_policy=_INGEST_RETRY,
            )
            result.chunks_stored = ingest_result.chunks_stored
            result.ingest_ms = ingest_result.duration_ms

            # ---------------------------------------------------------------
            # Step 4: Notify Letta agent
            # ---------------------------------------------------------------
            await self._check_paused()
            if self._cancelled:
                result.status = WorkflowStatus.CANCELLED.value
                return result

            self._status = WorkflowStatus.NOTIFYING

            # Build agent notification message (matches file_handler.py format)
            page_info = f" ({parse_result.page_count} pages)" if parse_result.page_count else ""
            ocr_info = " (OCR)" if parse_result.was_ocr else ""
            caption_note = ""
            if input.caption:
                caption_note = (
                    f'\n\nThe user asked: "{input.caption}"\n'
                    f"Use the search_documents tool to find relevant content "
                    f"and answer their question."
                )

            agent_msg = (
                f"[Document Indexed: {input.file_name}]{page_info}{ocr_info}\n\n"
                f"This document ({parse_result.char_count:,} chars) has been indexed "
                f"into the shared document library. "
                f"Use the **search_documents** tool with a relevant query to find "
                f"specific content from this document."
                f"{caption_note}"
            )

            notify_result: NotifyAgentResult = await workflow.execute_activity(
                notify_letta_agent,
                NotifyAgentInput(
                    agent_id=input.agent_id,
                    message=agent_msg,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_NOTIFY_RETRY,
            )
            result.notify_ms = notify_result.duration_ms

            # ---------------------------------------------------------------
            # Step 5: Update final status in room
            # ---------------------------------------------------------------
            self._status = WorkflowStatus.COMPLETED
            result.status = WorkflowStatus.COMPLETED.value

            try:
                final_msg = (
                    f"✅ {input.file_name}{page_info}{ocr_info} — "
                    f"{parse_result.char_count:,} chars indexed "
                    f"({ingest_result.chunks_stored} chunks) ✓"
                )
                await workflow.execute_activity(
                    update_matrix_status,
                    MatrixStatusInput(
                        room_id=input.room_id,
                        message=final_msg,
                        agent_id=input.agent_id,
                        event_id=self._status_event_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_STATUS_RETRY,
                )
            except Exception:
                pass  # Best-effort

            elapsed = int((workflow.now() - workflow_start).total_seconds() * 1000)
            result.total_ms = elapsed

            workflow.logger.info(
                f"File processing completed: {input.file_name} — "
                f"{parse_result.char_count:,} chars, {ingest_result.chunks_stored} chunks, "
                f"{elapsed}ms total"
            )

        except Exception as e:
            self._status = WorkflowStatus.FAILED
            result.status = WorkflowStatus.FAILED.value
            result.error = str(e)

            workflow.logger.error(
                f"File processing failed for {input.file_name}: {e}"
            )

            # Best-effort: update room status with error
            try:
                error_msg = f"❌ Failed to process {input.file_name}: {str(e)[:200]}"
                await workflow.execute_activity(
                    update_matrix_status,
                    MatrixStatusInput(
                        room_id=input.room_id,
                        message=error_msg,
                        agent_id=input.agent_id,
                        event_id=self._status_event_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_STATUS_RETRY,
                )
            except Exception:
                pass

            elapsed = int((workflow.now() - workflow_start).total_seconds() * 1000)
            result.total_ms = elapsed

        return result

    # -------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------

    @workflow.signal
    async def pause(self) -> None:
        """Pause the workflow at the next activity boundary."""
        workflow.logger.info("Received pause signal")
        self._paused = True
        self._status = WorkflowStatus.PAUSED

    @workflow.signal
    async def resume(self) -> None:
        """Resume a paused workflow."""
        workflow.logger.info("Received resume signal")
        self._paused = False
        # Status will be set by the next activity step

    @workflow.signal
    async def cancel(self) -> None:
        """Cancel the workflow at the next activity boundary."""
        workflow.logger.info("Received cancel signal")
        self._cancelled = True
        self._status = WorkflowStatus.CANCELLED

    # -------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------

    @workflow.query
    def get_status(self) -> str:
        """Query the current workflow status."""
        return self._status.value

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _check_paused(self) -> None:
        """Block if workflow is paused, waiting for resume or cancel."""
        while self._paused:
            workflow.logger.info("Workflow paused, waiting for resume/cancel signal")
            await workflow.wait_condition(
                lambda: not self._paused or self._cancelled
            )
            if self._cancelled:
                workflow.logger.info("Workflow cancelled while paused")
                return
