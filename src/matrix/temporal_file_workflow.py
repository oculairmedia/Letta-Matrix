"""
Temporal workflow integration for async file processing.
"""

import asyncio
import logging
import os
import uuid
from typing import Optional

import aiohttp

from src.matrix.config import LettaApiError
from src.matrix.file_download import FileMetadata

logger = logging.getLogger("matrix_client.file_handler")


class TemporalFileWorkflowMixin:
    """Temporal workflow methods mixed into LettaFileHandler."""

    async def _get_temporal_client(self):
        """Lazy-initialize the Temporal client (async, thread-safe via asyncio.Lock)."""
        if self._temporal_client is not None:
            return self._temporal_client

        async with self._temporal_lock:
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
        """Start a Temporal FileProcessingWorkflow for a document upload."""
        from temporal_workflows.workflows.file_processing import (
            FileProcessingWorkflow,
            FileProcessingInput,
        )

        try:
            await self.ensure_search_tool_attached(agent_id)
        except (LettaApiError, RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"Failed to pre-attach search tool for {agent_id}: {e}")

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
        except (ImportError, RuntimeError, ValueError) as conv_err:
            logger.debug(
                f"[CONVERSATIONS] Could not resolve conversation for temporal workflow: {conv_err}"
            )

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
        except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.error(f"Failed to start Temporal workflow for {metadata.file_name}: {e}", exc_info=True)
            await self._notify(room_id, f"\u26a0\ufe0f Failed to queue document processing: {e}")

        return None
