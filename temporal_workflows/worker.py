"""
Temporal Worker — Registers file processing workflows and activities, polls for tasks.

Connects to the Temporal server and runs the matrix-file-queue worker.
Follows the same structure as /opt/stacks/letta/temporal_workflows/worker.py.

Environment variables:
  TEMPORAL_HOST          — Temporal server address (default: 192.168.50.90:7233)
  TEMPORAL_NAMESPACE     — Temporal namespace (default: matrix)
  TEMPORAL_TASK_QUEUE    — Task queue name (default: matrix-file-queue)
"""

import asyncio
import logging
import os
import signal
import sys

from temporalio.client import Client
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

# Import workflows and activities
from temporal_workflows.workflows import file_processing
from temporal_workflows import activities

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "192.168.50.90:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "matrix")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "matrix-file-queue")
MAX_CONCURRENT_ACTIVITIES = int(os.getenv("MAX_CONCURRENT_ACTIVITIES", "5"))
MAX_CONCURRENT_WORKFLOWS = int(os.getenv("MAX_CONCURRENT_WORKFLOWS", "10"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("temporal_worker")


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def run_worker() -> None:
    """Connect to Temporal and run the file processing worker."""

    logger.info(f"Connecting to Temporal at {TEMPORAL_HOST} (namespace={TEMPORAL_NAMESPACE})")

    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
    )

    logger.info(
        f"Connected. Starting worker on queue={TEMPORAL_TASK_QUEUE}, "
        f"max_activities={MAX_CONCURRENT_ACTIVITIES}, "
        f"max_workflows={MAX_CONCURRENT_WORKFLOWS}"
    )

    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[
            file_processing.FileProcessingWorkflow,
        ],
        activities=[
            activities.download_file_from_matrix,
            activities.parse_with_markitdown,
            activities.ingest_to_haystack,
            activities.notify_letta_agent,
            activities.update_matrix_status,
        ],
        max_concurrent_activities=MAX_CONCURRENT_ACTIVITIES,
        max_concurrent_workflow_tasks=MAX_CONCURRENT_WORKFLOWS,
        # Use unsandboxed runner because parse_with_markitdown imports
        # document_parser which uses ProcessPoolExecutor and MarkItDown
        workflow_runner=UnsandboxedWorkflowRunner(),
    )

    logger.info(
        f"Worker started. Polling queue={TEMPORAL_TASK_QUEUE}. "
        f"Registered: FileProcessingWorkflow + 5 activities"
    )

    # Graceful shutdown on SIGINT/SIGTERM
    shutdown_event = asyncio.Event()

    def _signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Run worker until shutdown signal
    async with worker:
        await shutdown_event.wait()

    logger.info("Worker shutdown complete")


def main() -> None:
    """Main entry point for the Temporal file processing worker."""
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
