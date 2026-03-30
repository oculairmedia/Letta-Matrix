"""
StepStreamReader — Reads step streaming events from Letta and yields normalized StreamEvents.
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Optional, Union

from letta_client import Letta
from src.core.retry import (
    is_conversation_busy_error,
    ConversationBusyError,
    retry_sync,
)
from src.matrix.streaming_types import StreamEvent, StreamEventType

logger = logging.getLogger("matrix_client.streaming")


class StepStreamReader:
    """
    Reads step streaming events from Letta and yields normalized StreamEvents.

    Step streaming returns complete messages after each agent step:
    - reasoning_message: Agent's internal thinking
    - tool_call_message: Request to execute a tool
    - tool_return_message: Result from tool execution
    - assistant_message: Final response to user
    - stop_reason: Why the stream ended
    - usage_statistics: Token usage info
    """

    def __init__(
        self,
        letta_client: Letta,
        include_reasoning: bool = False,
        include_pings: bool = True,
        timeout: float = 120.0,
        idle_data_timeout: float = 120.0,
        max_tool_calls: int = 100,
    ):
        self.client = letta_client
        self.include_reasoning = include_reasoning
        self.include_pings = include_pings
        self.timeout = timeout
        self.idle_data_timeout = idle_data_timeout
        self.max_tool_calls = max_tool_calls
        self._last_tool_name: Optional[str] = None

    def _create_stream_with_retry(
        self,
        conversation_id: str,
        message: Union[str, list],
        background: bool = False,
        max_retries: int = 3,
    ) -> Any:
        total_attempts = max_retries + 1

        def _create_stream() -> Any:
            return self.client.conversations.messages.create(
                conversation_id=conversation_id,
                input=message,
                streaming=True,
                stream_tokens=False,
                include_pings=self.include_pings,
                background=background,
            )

        try:
            return retry_sync(
                _create_stream,
                max_attempts=total_attempts,
                base_delay=1.0,
                max_delay=8.0,
                operation_name=f"[STREAM-RETRY] create stream for {conversation_id}",
                logger=logger,
                retryable_exceptions=(Exception,),
                should_retry=lambda error: isinstance(error, Exception)
                and is_conversation_busy_error(error),
            )
        except (RuntimeError, ValueError, TypeError, AssertionError) as error:
            if is_conversation_busy_error(error):
                raise ConversationBusyError(
                    conversation_id=conversation_id,
                    attempts=total_attempts,
                    last_error=error,
                ) from error
            raise

    async def stream_message(
        self,
        agent_id: str,
        message: Union[str, list],
        background: bool = False,
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream a message to the agent and yield normalized events.

        Args:
            agent_id: Letta agent ID
            message: User message to send
            background: Whether to use background mode for long operations
            conversation_id: Optional conversation ID for Conversations API (context isolation)

        Yields:
            StreamEvent objects for each step
        """
        if conversation_id:
            logger.info(f"Starting step stream via Conversations API: conversation {conversation_id}")
        else:
            logger.info(f"Starting step stream for agent {agent_id}")

        try:
            import threading

            loop = asyncio.get_running_loop()
            chunk_queue: asyncio.Queue = asyncio.Queue()
            tool_call_count = 0

            def _consume_stream():
                try:
                    if conversation_id:
                        logger.debug(f"[STREAM] Using Conversations API: {conversation_id}")
                        stream = self._create_stream_with_retry(
                            conversation_id=conversation_id,
                            message=message,
                            background=background,
                        )
                    else:
                        logger.debug(f"[STREAM] Using Agents API: {agent_id}")
                        stream = self.client.agents.messages.stream(
                            agent_id=agent_id,
                            input=message,
                            streaming=True,
                            stream_tokens=False,
                            include_pings=self.include_pings,
                            background=background,
                        )
                    logger.debug("[STREAM] Stream created, consuming chunks...")
                    chunk_count = 0
                    for chunk in stream:
                        chunk_count += 1
                        logger.debug(f"[STREAM] Got chunk {chunk_count}")
                        loop.call_soon_threadsafe(chunk_queue.put_nowait, ('chunk', chunk))
                    logger.debug(f"[STREAM] Stream complete, {chunk_count} chunks")
                    loop.call_soon_threadsafe(chunk_queue.put_nowait, ('done', None))
                except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                    logger.error(f"[STREAM] Error in stream consumption: {e}", exc_info=True)
                    loop.call_soon_threadsafe(chunk_queue.put_nowait, ('error', e))

            # Start background thread
            thread = threading.Thread(target=_consume_stream, daemon=True)
            thread.start()

            # Consume from queue asynchronously
            start_time = loop.time()
            last_data_time = loop.time()

            while True:
                now = loop.time()

                if now - start_time > self.timeout:
                    logger.error(f"[STREAM] Total timeout after {self.timeout}s")
                    raise asyncio.TimeoutError()

                idle_seconds = now - last_data_time
                if idle_seconds > self.idle_data_timeout:
                    logger.error(f"[STREAM] Idle data timeout: no real data for {idle_seconds:.0f}s (limit: {self.idle_data_timeout}s). Killing stale stream.")
                    raise asyncio.TimeoutError()

                try:
                    item_type, item_data = await asyncio.wait_for(
                        chunk_queue.get(),
                        timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    continue

                if item_type == 'done':
                    logger.debug("[STREAM] Stream completed normally")
                    break
                elif item_type == 'error':
                    raise item_data
                elif item_type == 'chunk':
                    event = self._parse_chunk(item_data)
                    if event:
                        if event.type == StreamEventType.TOOL_CALL:
                            tool_call_count += 1
                            if tool_call_count > self.max_tool_calls:
                                logger.error(
                                    f"[STREAM] Tool loop detected: {tool_call_count} tool calls "
                                    f"(limit={self.max_tool_calls}), aborting"
                                )
                                yield StreamEvent(
                                    type=StreamEventType.ERROR,
                                    content=f"Agent stuck in tool loop ({tool_call_count} calls, limit={self.max_tool_calls}). Stopped.",
                                    metadata={"error_type": "tool_loop"}
                                )
                                break
                        if event.type != StreamEventType.PING:
                            last_data_time = loop.time()
                        logger.debug(f"[STREAM] Yielding event: {event.type.value}")
                        yield event

        except asyncio.TimeoutError:
            logger.error(f"Stream timeout after {self.timeout}s")
            yield StreamEvent(
                type=StreamEventType.ERROR,
                content=f"Request timed out after {self.timeout} seconds",
                metadata={"error_type": "timeout"}
            )
        except (RuntimeError, ValueError, TypeError, AssertionError) as e:
            logger.error(f"Stream error: {e}", exc_info=True)
            yield StreamEvent(
                type=StreamEventType.ERROR,
                content=str(e),
                metadata={"error_type": type(e).__name__}
            )

    def _parse_chunk(self, chunk: Any) -> Optional[StreamEvent]:
        """Parse a raw stream chunk into a StreamEvent"""
        msg_type = getattr(chunk, 'message_type', None)

        if not msg_type:
            return None

        if msg_type == 'ping':
            return StreamEvent(type=StreamEventType.PING)

        elif msg_type == 'reasoning_message':
            if not self.include_reasoning:
                return None
            return StreamEvent(
                type=StreamEventType.REASONING,
                content=getattr(chunk, 'reasoning', ''),
                metadata={
                    'id': getattr(chunk, 'id', None),
                    'run_id': getattr(chunk, 'run_id', None),
                }
            )

        elif msg_type == 'tool_call_message':
            tool_call = getattr(chunk, 'tool_call', None)
            tool_name = tool_call.name if tool_call else 'unknown'
            self._last_tool_name = tool_name  # Track for tool_return

            return StreamEvent(
                type=StreamEventType.TOOL_CALL,
                content=None,
                metadata={
                    'id': getattr(chunk, 'id', None),
                    'run_id': getattr(chunk, 'run_id', None),
                    'tool_name': tool_name,
                    'arguments': tool_call.arguments if tool_call else '',
                }
            )

        elif msg_type == 'tool_return_message':
            return StreamEvent(
                type=StreamEventType.TOOL_RETURN,
                content=getattr(chunk, 'tool_return', ''),
                metadata={
                    'id': getattr(chunk, 'id', None),
                    'run_id': getattr(chunk, 'run_id', None),
                    'tool_name': self._last_tool_name or 'unknown',
                    'status': getattr(chunk, 'status', 'unknown'),
                }
            )

        elif msg_type == 'assistant_message':
            return StreamEvent(
                type=StreamEventType.ASSISTANT,
                content=getattr(chunk, 'content', ''),
                metadata={
                    'id': getattr(chunk, 'id', None),
                    'run_id': getattr(chunk, 'run_id', None),
                }
            )

        elif msg_type == 'stop_reason':
            return StreamEvent(
                type=StreamEventType.STOP,
                content=getattr(chunk, 'stop_reason', 'unknown'),
                metadata={}
            )

        elif msg_type == 'usage_statistics':
            return StreamEvent(
                type=StreamEventType.USAGE,
                content=None,
                metadata={
                    'completion_tokens': getattr(chunk, 'completion_tokens', 0),
                    'prompt_tokens': getattr(chunk, 'prompt_tokens', 0),
                    'total_tokens': getattr(chunk, 'total_tokens', 0),
                    'step_count': getattr(chunk, 'step_count', 0),
                }
            )

        elif msg_type == 'error_message':
            return StreamEvent(
                type=StreamEventType.ERROR,
                content=getattr(chunk, 'message', 'Unknown error'),
                metadata={
                    'error_type': getattr(chunk, 'error_type', 'unknown'),
                    'detail': getattr(chunk, 'detail', ''),
                }
            )

        elif msg_type == 'approval_request_message':
            tool_calls = getattr(chunk, 'tool_calls', [])
            tool_call = getattr(chunk, 'tool_call', None)

            tools_info = []
            if tool_calls:
                for tc in tool_calls:
                    tools_info.append({
                        'name': getattr(tc, 'name', 'unknown'),
                        'tool_call_id': getattr(tc, 'tool_call_id', ''),
                        'arguments': getattr(tc, 'arguments', ''),
                    })
            elif tool_call:
                tools_info.append({
                    'name': getattr(tool_call, 'name', 'unknown'),
                    'tool_call_id': getattr(tool_call, 'tool_call_id', ''),
                    'arguments': getattr(tool_call, 'arguments', ''),
                })

            return StreamEvent(
                type=StreamEventType.APPROVAL_REQUEST,
                content=None,
                metadata={
                    'id': getattr(chunk, 'id', None),
                    'run_id': getattr(chunk, 'run_id', None),
                    'step_id': getattr(chunk, 'step_id', None),
                    'tool_calls': tools_info,
                }
            )

        else:
            logger.debug(f"Unknown message type: {msg_type}")
            return None
