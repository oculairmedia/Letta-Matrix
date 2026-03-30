"""
MessageDeliveryWorkflow — Durable delivery of Matrix messages to Letta agents via Temporal.

Pipeline:
  1. deliver_to_letta → send message via WS gateway with retry
  2. On success → send_delivery_ack → Matrix read receipt/reaction
  3. On permanent failure → dead_letter_message → PostgreSQL + ntfy alert

Supports signals (cancel) and queries (get_status, get_attempt_count).
"""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_workflows.activities.deliver import (
        deliver_to_letta,
        dead_letter_message,
        send_delivery_ack,
        DeliverToLettaInput,
        DeliverToLettaResult,
        DeadLetterInput,
        DeliveryAckInput,
    )


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    DEAD_LETTERED = "dead_lettered"
    CANCELLED = "cancelled"


@dataclass
class MessageDeliveryInput:
    """Input for the message delivery workflow."""
    room_id: str
    agent_id: str
    message_body: str  # The formatted message/envelope to send
    sender: str  # Matrix user who sent the message
    event_id: str  # Original Matrix event ID (for dedup + ack)
    conversation_id: str = ""
    is_streaming: bool = False
    max_retries: int = 5
    # Source metadata for Letta
    source_channel: str = "matrix"
    # Reply / thread context for Matrix responses
    reply_to_event_id: Optional[str] = None
    thread_root_event_id: Optional[str] = None
    thread_latest_event_id: Optional[str] = None


@dataclass
class MessageDeliveryResult:
    """Result of the message delivery workflow."""
    status: str  # DeliveryStatus value
    agent_id: str = ""
    event_id: str = ""
    response_text: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    total_ms: int = 0


# ---------------------------------------------------------------------------
# Retry policies
# ---------------------------------------------------------------------------

_DELIVER_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=5,
    backoff_coefficient=2.0,
    # Don't retry on client errors (bad request, agent not found)
    non_retryable_error_types=["ClientError", "AgentNotFoundError"],
)

_ACK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=2,
    backoff_coefficient=2.0,
)

_DEAD_LETTER_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    backoff_coefficient=2.0,
)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

@workflow.defn
class MessageDeliveryWorkflow:
    """
    Durable workflow for delivering a Matrix message to a Letta agent.

    Chains deliver → ack on success, or dead-letter on permanent failure.
    """

    def __init__(self) -> None:
        self._status = DeliveryStatus.PENDING
        self._cancelled = False
        self._attempts = 0

    @workflow.run
    async def run(self, input: MessageDeliveryInput) -> MessageDeliveryResult:
        """Main workflow execution."""
        workflow_start = workflow.now()

        result = MessageDeliveryResult(
            status=DeliveryStatus.PENDING.value,
            agent_id=input.agent_id,
            event_id=input.event_id,
        )

        workflow.logger.info(
            f"Starting message delivery: event={input.event_id} "
            f"room={input.room_id} agent={input.agent_id}"
        )

        try:
            if self._cancelled:
                return self._finalize(result, DeliveryStatus.CANCELLED, workflow_start)

            # Step 1: Deliver to Letta
            self._status = DeliveryStatus.DELIVERING

            deliver_result = await workflow.execute_activity(
                deliver_to_letta,
                DeliverToLettaInput(
                    agent_id=input.agent_id,
                    message_body=input.message_body,
                    conversation_id=input.conversation_id,
                    room_id=input.room_id,
                    source_channel=input.source_channel,
                ),
                start_to_close_timeout=timedelta(seconds=300),
                retry_policy=_DELIVER_RETRY,
            )

            self._attempts = deliver_result.attempts
            result.attempts = deliver_result.attempts
            result.response_text = deliver_result.response_text

            if not deliver_result.success:
                # Permanent failure — dead letter it
                raise RuntimeError(deliver_result.error or "Delivery failed")

            # Step 2: Acknowledge delivery (best-effort)
            self._status = DeliveryStatus.DELIVERED
            result.status = DeliveryStatus.DELIVERED.value

            try:
                await workflow.execute_activity(
                    send_delivery_ack,
                    DeliveryAckInput(
                        room_id=input.room_id,
                        event_id=input.event_id,
                        agent_id=input.agent_id,
                    ),
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=_ACK_RETRY,
                )
            except Exception as ack_err:
                workflow.logger.warning(f"Delivery ack failed (best-effort): {ack_err}")

            workflow.logger.info(
                f"Message delivered: event={input.event_id} agent={input.agent_id} "
                f"attempts={deliver_result.attempts}"
            )

            return self._finalize(result, DeliveryStatus.DELIVERED, workflow_start)

        except Exception as e:
            # Dead letter the message
            self._status = DeliveryStatus.DEAD_LETTERED
            result.status = DeliveryStatus.DEAD_LETTERED.value
            result.error = str(e)

            workflow.logger.error(
                f"Message delivery failed, dead-lettering: "
                f"event={input.event_id} agent={input.agent_id} error={e}"
            )

            try:
                await workflow.execute_activity(
                    dead_letter_message,
                    DeadLetterInput(
                        event_id=input.event_id,
                        room_id=input.room_id,
                        agent_id=input.agent_id,
                        message_body=input.message_body,
                        sender=input.sender,
                        error=str(e),
                        attempts=result.attempts,
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=_DEAD_LETTER_RETRY,
                )
            except Exception as dl_err:
                workflow.logger.error(f"Dead letter also failed: {dl_err}")

            return self._finalize(result, DeliveryStatus.DEAD_LETTERED, workflow_start)

    # -------------------------------------------------------------------
    # Signals
    # -------------------------------------------------------------------

    @workflow.signal
    async def cancel(self) -> None:
        """Cancel the workflow."""
        workflow.logger.info("Received cancel signal")
        self._cancelled = True

    # -------------------------------------------------------------------
    # Queries
    # -------------------------------------------------------------------

    @workflow.query
    def get_status(self) -> str:
        """Query the current delivery status."""
        return self._status.value

    @workflow.query
    def get_attempt_count(self) -> int:
        """Query the number of delivery attempts so far."""
        return self._attempts

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _finalize(
        self,
        result: MessageDeliveryResult,
        status: DeliveryStatus,
        workflow_start,
    ) -> MessageDeliveryResult:
        """Set final status and compute elapsed time."""
        result.status = status.value
        elapsed = int((workflow.now() - workflow_start).total_seconds() * 1000)
        result.total_ms = elapsed
        return result
