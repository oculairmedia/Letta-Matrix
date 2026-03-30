"""
Letta Streaming Adapter for Matrix Client

Handles step streaming from Letta agents and provides normalized events
for Matrix display with progress-then-delete pattern.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Union

from letta_client import Letta
from src.core.retry import (
    is_conversation_busy_error,
    ConversationBusyError,
    retry_sync,
)
from src.matrix.formatter import is_no_reply

logger = logging.getLogger("matrix_client.streaming")

SELF_DELIVERY_TOOL_NAMES = frozenset({
    "matrix_messaging",
    "send_message",
    "matrix-identity-bridge_matrix_messaging",
})


class StreamEventType(Enum):
    """Types of streaming events"""
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RETURN = "tool_return"
    ASSISTANT = "assistant"
    STOP = "stop"
    USAGE = "usage"
    ERROR = "error"
    PING = "ping"
    APPROVAL_REQUEST = "approval_request"


@dataclass
class StreamEvent:
    """Normalized streaming event"""
    type: StreamEventType
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_progress(self) -> bool:
        """Whether this event should show as progress (deletable)"""
        return self.type in (StreamEventType.TOOL_CALL, StreamEventType.TOOL_RETURN)
    
    @property
    def is_approval_request(self) -> bool:
        """Whether this is an approval request that needs user action"""
        return self.type == StreamEventType.APPROVAL_REQUEST
    
    @property
    def is_final(self) -> bool:
        """Whether this is a final response"""
        return self.type == StreamEventType.ASSISTANT
    
    @property
    def is_error(self) -> bool:
        """Whether this is an error"""
        return self.type == StreamEventType.ERROR
    
    def format_progress(self) -> str:
        """Format event as progress message for Matrix"""
        if self.type == StreamEventType.TOOL_CALL:
            tool_name = self.metadata.get('tool_name', 'unknown')
            return f"🔧 {tool_name}..."
        elif self.type == StreamEventType.TOOL_RETURN:
            tool_name = self.metadata.get('tool_name', 'unknown')
            status = self.metadata.get('status', 'unknown')
            if status == 'success':
                return f"✅ {tool_name}"
            else:
                return f"❌ {tool_name} (failed)"
        elif self.type == StreamEventType.REASONING:
            # Truncate reasoning for progress display
            text = self.content or ""
            if len(text) > 50:
                text = text[:50] + "..."
            return f"💭 {text}"
        elif self.type == StreamEventType.APPROVAL_REQUEST:
            tool_calls = self.metadata.get('tool_calls', [])
            if tool_calls:
                tool_names = [tc.get('name', 'unknown') for tc in tool_calls]
                return f"⏳ **Approval Required**: {', '.join(tool_names)}"
            return "⏳ **Approval Required**"
        return str(self.content or "")


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
            # Extract tool call information for approval
            tool_calls = getattr(chunk, 'tool_calls', [])
            tool_call = getattr(chunk, 'tool_call', None)
            
            # Build list of tools needing approval
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


class StreamingMessageHandler:
    """
    Handles streaming events and manages Matrix message lifecycle.

    Defers assistant text until STOP and detects agent self-delivery via
    ``matrix_messaging`` to suppress duplicate messages — same semantics as
    ``LiveEditStreamingHandler`` but using discrete send/delete instead of
    in-place edits.
    """

    def __init__(
        self,
        send_message: Callable[..., Any],
        delete_message: Callable[[str, str], Any],
        room_id: str,
        delete_progress: bool = False,
        send_final_message: Optional[Callable[..., Any]] = None,
        thread_root_event_id: Optional[str] = None,
    ):
        self.send_message = send_message
        self.delete_message = delete_message
        self.room_id = room_id
        self.delete_progress = delete_progress
        self.send_final_message = send_final_message or send_message
        self.thread_root_event_id = thread_root_event_id
        self._progress_event_id: Optional[str] = None
        self._latest_thread_event_id: Optional[str] = None
        self._tool_call_count = 0

        self._pending_final_content: Optional[str] = None
        self._self_delivered_to_current_room: bool = False

    @property
    def self_delivered(self) -> bool:
        return self._self_delivered_to_current_room

    def _is_self_delivery_to_current_room(self, event: StreamEvent) -> bool:
        if event.metadata.get("tool_name") not in SELF_DELIVERY_TOOL_NAMES:
            return False
        args_raw = event.metadata.get("arguments", "")
        if not args_raw:
            return False
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except (json.JSONDecodeError, TypeError):
            return False
        target_room = args.get("room_id") or args.get("chatId") or ""
        if target_room and target_room == self.room_id:
            return True
        target_to = args.get("to_mxid") or ""
        if target_to:
            return False
        op = args.get("operation", "")
        if op in ("send", "talk_to_agent", "talk_to_opencode"):
            return not target_room
        return False

    async def _send_with_msgtype(self, text: str, msgtype: str, *, threaded: bool = False) -> str:
        thread_kwargs: Dict[str, Any] = {}
        if threaded and self.thread_root_event_id:
            thread_kwargs = {
                "thread_event_id": self.thread_root_event_id,
                "thread_latest_event_id": self._latest_thread_event_id,
            }
        if msgtype == "m.text":
            try:
                event_id = await self.send_message(self.room_id, text, **thread_kwargs)
            except TypeError:
                event_id = await self.send_message(self.room_id, text)
            if thread_kwargs and event_id:
                self._latest_thread_event_id = event_id
            return event_id
        try:
            event_id = await self.send_message(self.room_id, text, msgtype=msgtype, **thread_kwargs)
        except TypeError:
            event_id = await self.send_message(self.room_id, text)
        if thread_kwargs and event_id:
            self._latest_thread_event_id = event_id
        return event_id

    async def _send_final(self, content: str) -> str:
        threaded = bool(self.thread_root_event_id)
        if threaded:
            try:
                event_id = await self.send_final_message(
                    self.room_id,
                    content,
                    thread_event_id=self.thread_root_event_id,
                    thread_latest_event_id=self._latest_thread_event_id,
                )
            except TypeError:
                event_id = await self.send_final_message(self.room_id, content)
            if event_id:
                self._latest_thread_event_id = event_id
            return event_id
        return await self.send_final_message(self.room_id, content)

    async def handle_event(self, event: StreamEvent) -> Optional[str]:
        if self.delete_progress and self._progress_event_id and (event.is_progress or event.is_final or event.is_error):
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
                logger.debug(f"Deleted progress message {self._progress_event_id}")
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to delete progress message: {e}")
            self._progress_event_id = None

        if event.type == StreamEventType.PING:
            return None

        if event.type == StreamEventType.USAGE:
            return None

        if event.type == StreamEventType.REASONING:
            return None

        if event.type == StreamEventType.STOP:
            return await self._handle_stop()

        if event.is_progress:
            if event.type == StreamEventType.TOOL_CALL:
                self._tool_call_count += 1
                if self._is_self_delivery_to_current_room(event):
                    logger.info(
                        f"[Streaming] Detected self-delivery tool call "
                        f"({event.metadata.get('tool_name')}) targeting {self.room_id}"
                    )
                    self._self_delivered_to_current_room = True

            progress_text = event.format_progress()
            if self._progress_event_id:
                try:
                    await self.delete_message(self.room_id, self._progress_event_id)
                except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError) as progress_delete_error:
                    logger.debug(
                        "Failed to replace previous progress message %s: %s",
                        self._progress_event_id,
                        progress_delete_error,
                    )
            eid = await self._send_with_msgtype(progress_text, "m.notice", threaded=True)
            self._progress_event_id = eid
            return eid

        elif event.is_final:
            if is_no_reply(event.content):
                logger.info(f"[Streaming] Agent chose not to reply (no-reply marker) in {self.room_id}")
                self._pending_final_content = None
                return None
            self._pending_final_content = event.content or ""
            return None

        elif event.is_error:
            error_text = f"⚠️ {event.content}"
            if event.metadata.get('detail'):
                error_text += f"\n{event.metadata['detail']}"
            return await self._send_with_msgtype(error_text, "m.notice")

        elif event.is_approval_request:
            approval_text = event.format_progress()
            tool_calls = event.metadata.get('tool_calls', [])

            if tool_calls:
                approval_text += "\n\nTools awaiting approval:"
                for tc in tool_calls:
                    tool_name = tc.get('name', 'unknown')
                    tool_id = tc.get('tool_call_id', '')
                    args = tc.get('arguments', '')
                    if len(args) > 200:
                        args = args[:200] + "..."
                    approval_text += f"\n- **{tool_name}** (`{tool_id[:20]}...`)"
                    if args:
                        approval_text += f"\n  ```\n  {args}\n  ```"

            logger.info(f"[APPROVAL] Sending approval request to Matrix: {len(tool_calls)} tool(s)")
            return await self._send_with_msgtype(approval_text, "m.text")

        return None

    async def _handle_stop(self) -> Optional[str]:
        if self._self_delivered_to_current_room:
            logger.info(
                f"[Streaming] Suppressing duplicate final text — agent self-delivered in {self.room_id}"
            )
            if self.delete_progress and self._progress_event_id:
                try:
                    await self.delete_message(self.room_id, self._progress_event_id)
                except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                    logger.warning(f"Failed to delete progress after self-delivery: {e}")
                self._progress_event_id = None
            self._pending_final_content = None
            return None

        if self._pending_final_content is not None:
            content = self._pending_final_content
            self._pending_final_content = None
            return await self._send_final(content)

        return None

    async def cleanup(self):
        if self._pending_final_content is not None and not self._self_delivered_to_current_room:
            await self._send_final(self._pending_final_content)
            self._pending_final_content = None
            return
        if self.delete_progress and self._progress_event_id:
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to cleanup progress message: {e}")
            self._progress_event_id = None

    async def show_progress(self, line: str) -> Optional[str]:
        return await self._send_with_msgtype(line, "m.notice", threaded=True)

    async def update_last_progress(self, line: str) -> None:
        """No-op for non-live-edit handler."""
        pass


class LiveEditStreamingHandler:
    """
    Streams agent activity into a single Matrix message that is edited in-place.

    First meaningful event creates the message.  Subsequent events append to a
    running log and edit the same message (debounced to avoid rate-limits).

    Assistant text is deferred until STOP so that mid-chain assistant events
    (emitted between tool calls) don't prematurely replace the progress log.
    If the agent self-delivers via ``matrix_messaging`` targeting the current
    room, the duplicate final text is suppressed and the progress message is
    cleaned up instead.
    """

    EDIT_DEBOUNCE_S = 0.2

    def __init__(
        self,
        send_message: Callable[..., Any],
        edit_message: Callable[..., Any],
        room_id: str,
        send_final_message: Optional[Callable[..., Any]] = None,
        delete_message: Optional[Callable[[str, str], Any]] = None,
        thread_root_event_id: Optional[str] = None,
    ):
        self.send_message = send_message
        self.edit_message = edit_message
        self.delete_message = delete_message
        self.room_id = room_id
        self.send_final_message = send_final_message or send_message
        self.thread_root_event_id = thread_root_event_id

        self._event_id: Optional[str] = None
        self._latest_thread_event_id: Optional[str] = None
        self._lines: List[str] = []
        self._last_edit_time: float = 0
        self._tool_call_count = 0

        self._pending_final_content: Optional[str] = None
        self._self_delivered_to_current_room: bool = False

    @property
    def self_delivered(self) -> bool:
        return self._self_delivered_to_current_room

    def _is_self_delivery_to_current_room(self, event: StreamEvent) -> bool:
        if event.metadata.get("tool_name") not in SELF_DELIVERY_TOOL_NAMES:
            return False
        args_raw = event.metadata.get("arguments", "")
        if not args_raw:
            return False
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except (json.JSONDecodeError, TypeError):
            return False
        target_room = args.get("room_id") or args.get("chatId") or ""
        if target_room and target_room == self.room_id:
            return True
        target_to = args.get("to_mxid") or ""
        if target_to:
            return False
        op = args.get("operation", "")
        if op in ("send", "talk_to_agent", "talk_to_opencode"):
            return not target_room
        return False

    async def _send_with_msgtype(self, text: str, msgtype: str, *, threaded: bool = False) -> str:
        thread_kwargs: Dict[str, Any] = {}
        if threaded and self.thread_root_event_id:
            thread_kwargs = {
                "thread_event_id": self.thread_root_event_id,
                "thread_latest_event_id": self._latest_thread_event_id,
            }
        if msgtype == "m.text":
            try:
                event_id = await self.send_message(self.room_id, text, **thread_kwargs)
            except TypeError:
                event_id = await self.send_message(self.room_id, text)
            if thread_kwargs and event_id:
                self._latest_thread_event_id = event_id
            return event_id
        try:
            event_id = await self.send_message(self.room_id, text, msgtype=msgtype, **thread_kwargs)
        except TypeError:
            event_id = await self.send_message(self.room_id, text)
        if thread_kwargs and event_id:
            self._latest_thread_event_id = event_id
        return event_id

    async def _edit_with_msgtype(self, event_id: str, text: str, msgtype: str) -> None:
        if msgtype == "m.text":
            await self.edit_message(self.room_id, event_id, text)
            return
        try:
            await self.edit_message(self.room_id, event_id, text, msgtype=msgtype)
        except TypeError:
            await self.edit_message(self.room_id, event_id, text)

    async def handle_event(self, event: StreamEvent) -> Optional[str]:
        if event.type == StreamEventType.PING:
            return None

        if event.type == StreamEventType.USAGE:
            return None

        if event.type == StreamEventType.REASONING:
            return None

        if event.type == StreamEventType.STOP:
            return await self._handle_stop()

        if event.is_final:
            if is_no_reply(event.content):
                logger.info(f"[LiveEdit] Agent chose not to reply (no-reply marker) in {self.room_id}")
                self._pending_final_content = None
                await self._cleanup_no_reply()
                return None
            self._pending_final_content = event.content or ""
            return None

        if event.is_error:
            error_text = f"⚠️ {event.content}"
            if self._event_id:
                self._lines.append(error_text)
                await self._do_edit()
                return self._event_id
            return await self._send_with_msgtype(error_text, "m.notice")

        if event.is_approval_request:
            line = event.format_progress()
            self._append_progress_line(event, line)

            if self._event_id is None:
                body = self._build_body()
                eid = await self._send_with_msgtype(body, "m.text")
                self._event_id = eid
                self._last_edit_time = time.monotonic()
                return eid

            now = time.monotonic()
            if now - self._last_edit_time >= self.EDIT_DEBOUNCE_S:
                await self._do_edit()
            return self._event_id

        if event.is_progress:
            if event.type == StreamEventType.TOOL_CALL:
                self._tool_call_count += 1
                if self._is_self_delivery_to_current_room(event):
                    logger.info(
                        f"[LiveEdit] Detected self-delivery tool call "
                        f"({event.metadata.get('tool_name')}) targeting {self.room_id}"
                    )
                    self._self_delivered_to_current_room = True

            line = event.format_progress()
            self._append_progress_line(event, line)

            if self._event_id is None:
                body = self._build_body()
                eid = await self._send_with_msgtype(body, "m.notice", threaded=True)
                self._event_id = eid
                self._last_edit_time = time.monotonic()
                return eid

            now = time.monotonic()
            if now - self._last_edit_time >= self.EDIT_DEBOUNCE_S:
                await self._do_edit()
            return self._event_id

        return None

    async def _handle_stop(self) -> Optional[str]:
        if self._self_delivered_to_current_room:
            logger.info(
                f"[LiveEdit] Suppressing duplicate final text — agent self-delivered in {self.room_id}"
            )
            await self._cleanup_self_delivered()
            return None

        if self._pending_final_content is not None:
            content = self._pending_final_content
            self._pending_final_content = None
            return await self._send_final(content)

        return None

    async def _send_final(self, content: str) -> str:
        # When threading is active, an in-place edit cannot add m.relates_to
        # to a progress message that was born without it.  Delete the progress
        # message and send a fresh threaded response instead.
        if self._event_id and self.thread_root_event_id and self.delete_message:
            try:
                await self.delete_message(self.room_id, self._event_id)
            except (RuntimeError, ValueError, TypeError, AssertionError):
                pass  # best-effort cleanup
            self._event_id = None
            self._lines.clear()
            # fall through to fresh-send below

        # Replace progress message with final response (non-threaded only)
        if self._event_id:
            await self._edit_with_msgtype(self._event_id, content, "m.text")
            eid = self._event_id
            self._event_id = None
            self._lines.clear()
            return eid

        # Send fresh — with threading if applicable
        if self.thread_root_event_id:
            try:
                eid = await self.send_final_message(
                    self.room_id,
                    content,
                    thread_event_id=self.thread_root_event_id,
                    thread_latest_event_id=self._latest_thread_event_id,
                )
            except TypeError:
                eid = await self.send_final_message(self.room_id, content)
            self._latest_thread_event_id = eid
            return eid
        eid = await self.send_final_message(self.room_id, content)
        return eid

    async def _do_edit(self) -> None:
        if not self._event_id:
            return
        body = self._build_body()
        await self._edit_with_msgtype(self._event_id, body, "m.notice")
        self._last_edit_time = time.monotonic()

    def _build_body(self) -> str:
        return "\n".join(self._lines)

    def _append_progress_line(self, event: StreamEvent, line: str) -> None:
        if event.type == StreamEventType.TOOL_RETURN and self._lines:
            tool_name = event.metadata.get("tool_name", "unknown")
            if self._lines[-1] == f"🔧 {tool_name}...":
                self._lines[-1] = line
                return
        self._lines.append(line)

    async def show_progress(self, line: str) -> Optional[str]:
        """Add a progress line and update the live-edit message."""
        self._lines.append(line)
        if self._event_id is None:
            body = self._build_body()
            eid = await self._send_with_msgtype(body, "m.notice", threaded=True)
            self._event_id = eid
            self._last_edit_time = time.monotonic()
            return eid
        await self._do_edit()
        return self._event_id

    async def update_last_progress(self, line: str) -> None:
        """Replace the last progress line and edit the message."""
        if self._lines:
            self._lines[-1] = line
        else:
            self._lines.append(line)
        if self._event_id:
            await self._do_edit()

    async def _cleanup_no_reply(self) -> None:
        if self._event_id and self.delete_message:
            try:
                await self.delete_message(self.room_id, self._event_id)
                logger.debug(f"Deleted live-edit progress message {self._event_id} for no-reply")
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to delete live-edit progress for no-reply: {e}")
        self._event_id = None
        self._lines.clear()

    async def _cleanup_self_delivered(self) -> None:
        if self._event_id and self.delete_message:
            try:
                await self.delete_message(self.room_id, self._event_id)
                logger.debug(f"Deleted progress message {self._event_id} after self-delivery")
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to delete progress after self-delivery: {e}")
        self._event_id = None
        self._lines.clear()
        self._pending_final_content = None

    async def cleanup(self) -> None:
        if self._pending_final_content is not None and not self._self_delivered_to_current_room:
            await self._send_final(self._pending_final_content)
            self._pending_final_content = None
            return
        if self._event_id and self._lines:
            await self._do_edit()
