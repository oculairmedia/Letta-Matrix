"""
Letta Streaming Adapter for Matrix Client

Handles step streaming from Letta agents and provides normalized events
for Matrix display with progress-then-delete pattern.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from letta_client import Letta

logger = logging.getLogger("matrix_client.streaming")


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
    ):
        """
        Initialize the stream reader.
        
        Args:
            letta_client: Initialized Letta SDK client
            include_reasoning: Whether to emit reasoning events
            include_pings: Whether to include keepalive pings in stream
            timeout: Maximum stream duration in seconds
            idle_data_timeout: Max seconds to wait for real data (non-ping) before killing stream.
                               Prevents hung streams that only send keepalive pings indefinitely.
        """
        self.client = letta_client
        self.include_reasoning = include_reasoning
        self.include_pings = include_pings
        self.timeout = timeout
        self.idle_data_timeout = idle_data_timeout
        self._last_tool_name: Optional[str] = None
    
    def _create_stream_with_retry(
        self,
        conversation_id: str,
        message: str,
        background: bool = False,
        max_retries: int = 3,
    ) -> Any:
        """
        Create a streaming connection with retry on CONVERSATION_BUSY (409).
        
        Uses synchronous retry since this runs in a background thread.
        """
        import time
        from src.core.retry import is_conversation_busy_error, ConversationBusyError
        
        last_error: Optional[Exception] = None
        
        for attempt in range(max_retries + 1):
            try:
                return self.client.conversations.messages.create(
                    conversation_id=conversation_id,
                    input=message,
                    streaming=True,
                    stream_tokens=False,
                    include_pings=self.include_pings,
                    background=background,
                )
            except Exception as e:
                if is_conversation_busy_error(e):
                    last_error = e
                    if attempt < max_retries:
                        delay = min(1.0 * (2 ** attempt), 8.0)
                        logger.warning(
                            f"[STREAM-RETRY] Conversation {conversation_id} is busy, "
                            f"attempt {attempt + 1}/{max_retries + 1}, "
                            f"retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"[STREAM-RETRY] Conversation {conversation_id} still busy "
                            f"after {max_retries + 1} attempts"
                        )
                        raise ConversationBusyError(
                            conversation_id=conversation_id,
                            attempts=max_retries + 1,
                            last_error=e
                        ) from e
                else:
                    raise
        
        raise ConversationBusyError(
            conversation_id=conversation_id,
            attempts=max_retries + 1,
            last_error=last_error
        )
    
    async def stream_message(
        self,
        agent_id: str,
        message: str,
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
            import queue
            import threading
            
            chunk_queue: queue.Queue = queue.Queue()
            error_holder: list = []
            
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
                        chunk_queue.put(('chunk', chunk))
                    logger.debug(f"[STREAM] Stream complete, {chunk_count} chunks")
                    chunk_queue.put(('done', None))
                except Exception as e:
                    logger.error(f"[STREAM] Error in stream consumption: {e}", exc_info=True)
                    error_holder.append(e)
                    chunk_queue.put(('error', e))
            
            # Start background thread
            thread = threading.Thread(target=_consume_stream, daemon=True)
            thread.start()
            
            # Consume from queue asynchronously
            loop = asyncio.get_running_loop()
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
                    item_type, item_data = await loop.run_in_executor(
                        None,
                        lambda: chunk_queue.get(timeout=1.0)
                    )
                except queue.Empty:
                    continue
                
                if item_type == 'done':
                    logger.debug("[STREAM] Stream completed normally")
                    break
                elif item_type == 'error':
                    raise item_data
                elif item_type == 'chunk':
                    event = self._parse_chunk(item_data)
                    if event:
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
        except Exception as e:
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
    
    Shows progress messages for tool calls that remain visible in the room,
    providing an activity trail of what the agent did.
    """
    
    def __init__(
        self,
        send_message: Callable[[str, str], Any],
        delete_message: Callable[[str, str], Any],
        room_id: str,
        delete_progress: bool = False,
        send_final_message: Optional[Callable[[str, str], Any]] = None,
    ):
        """
        Initialize the handler.
        
        Args:
            send_message: Async function(room_id, content) -> event_id (for progress messages)
            delete_message: Async function(room_id, event_id) -> None
            room_id: Matrix room ID
            delete_progress: If True, delete progress messages when next event arrives
            send_final_message: Optional separate async function for final responses 
                               (e.g., to include rich reply context). If not provided,
                               uses send_message for final messages too.
        """
        self.send_message = send_message
        self.delete_message = delete_message
        self.room_id = room_id
        self.delete_progress = delete_progress
        self.send_final_message = send_final_message or send_message
        self._progress_event_id: Optional[str] = None
    
    async def handle_event(self, event: StreamEvent) -> Optional[str]:
        """
        Handle a streaming event and update Matrix accordingly.
        
        Args:
            event: The streaming event to handle
            
        Returns:
            Event ID of sent message (if any)
        """
        # Optionally delete previous progress message
        if self.delete_progress and self._progress_event_id and (event.is_progress or event.is_final or event.is_error):
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
                logger.debug(f"Deleted progress message {self._progress_event_id}")
            except Exception as e:
                logger.warning(f"Failed to delete progress message: {e}")
            self._progress_event_id = None
        
        # Handle based on event type
        if event.type == StreamEventType.PING:
            # Ignore pings
            return None
        
        elif event.is_progress:
            # Send progress message (kept visible unless delete_progress=True)
            progress_text = event.format_progress()
            event_id = await self.send_message(self.room_id, progress_text)
            if self.delete_progress:
                self._progress_event_id = event_id
            return event_id
        
        elif event.is_final:
            # Send final assistant response (not deleted) - use send_final_message
            # which may include rich reply context
            return await self.send_final_message(self.room_id, event.content or "")
        
        elif event.is_error:
            # Send error message
            error_text = f"⚠️ {event.content}"
            if event.metadata.get('detail'):
                error_text += f"\n{event.metadata['detail']}"
            return await self.send_message(self.room_id, error_text)
        
        elif event.type in (StreamEventType.STOP, StreamEventType.USAGE):
            # Metadata events - don't display
            return None
        
        elif event.type == StreamEventType.REASONING:
            # Reasoning - could optionally display in debug mode
            # For now, skip
            return None
        
        elif event.is_approval_request:
            # Send approval request message to Matrix
            # This allows the user or connected system to approve/deny
            approval_text = event.format_progress()
            tool_calls = event.metadata.get('tool_calls', [])
            
            # Add details about what needs approval
            if tool_calls:
                approval_text += "\n\nTools awaiting approval:"
                for tc in tool_calls:
                    tool_name = tc.get('name', 'unknown')
                    tool_id = tc.get('tool_call_id', '')
                    args = tc.get('arguments', '')
                    # Truncate long arguments
                    if len(args) > 200:
                        args = args[:200] + "..."
                    approval_text += f"\n- **{tool_name}** (`{tool_id[:20]}...`)"
                    if args:
                        approval_text += f"\n  ```\n  {args}\n  ```"
            
            logger.info(f"[APPROVAL] Sending approval request to Matrix: {len(tool_calls)} tool(s)")
            return await self.send_message(self.room_id, approval_text)
        
        return None
    
    async def cleanup(self):
        """Clean up any remaining progress messages (only if delete_progress=True)"""
        if self.delete_progress and self._progress_event_id:
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
            except Exception as e:
                logger.warning(f"Failed to cleanup progress message: {e}")
            self._progress_event_id = None


class LiveEditStreamingHandler:
    """
    Streams agent activity into a single Matrix message that is edited in-place.

    First meaningful event creates the message.  Subsequent events append to a
    running log and edit the same message (debounced to avoid rate-limits).
    The final assistant response replaces the entire message body.
    """

    EDIT_DEBOUNCE_S = 0.5

    def __init__(
        self,
        send_message: Callable[[str, str], Any],
        edit_message: Callable[[str, str, str], Any],
        room_id: str,
        send_final_message: Optional[Callable[[str, str], Any]] = None,
    ):
        self.send_message = send_message
        self.edit_message = edit_message
        self.room_id = room_id
        self.send_final_message = send_final_message or send_message

        self._event_id: Optional[str] = None
        self._lines: List[str] = []
        self._last_edit_time: float = 0

    async def handle_event(self, event: StreamEvent) -> Optional[str]:
        import time

        if event.type == StreamEventType.PING:
            return None

        if event.type in (StreamEventType.STOP, StreamEventType.USAGE):
            return None

        if event.type == StreamEventType.REASONING:
            return None

        if event.is_final:
            return await self._send_final(event.content or "")

        if event.is_error:
            error_text = f"⚠️ {event.content}"
            if self._event_id:
                self._lines.append(error_text)
                await self._do_edit()
                return self._event_id
            return await self.send_message(self.room_id, error_text)

        if event.is_progress or event.is_approval_request:
            line = event.format_progress()
            self._lines.append(line)

            if self._event_id is None:
                body = self._build_body()
                eid = await self.send_message(self.room_id, body)
                self._event_id = eid
                self._last_edit_time = time.monotonic()
                return eid

            now = time.monotonic()
            if now - self._last_edit_time >= self.EDIT_DEBOUNCE_S:
                await self._do_edit()
            return self._event_id

        return None

    async def _send_final(self, content: str) -> str:
        if self._event_id and self._lines:
            final_body = "\n".join(self._lines) + "\n\n" + content
            await self.edit_message(self.room_id, self._event_id, final_body)
            self._event_id = None
            self._lines.clear()
            return ""

        eid = await self.send_final_message(self.room_id, content)
        self._event_id = None
        self._lines.clear()
        return eid

    async def _do_edit(self) -> None:
        import time
        if not self._event_id:
            return
        body = self._build_body()
        await self.edit_message(self.room_id, self._event_id, body)
        self._last_edit_time = time.monotonic()

    def _build_body(self) -> str:
        return "\n".join(self._lines)

    async def cleanup(self) -> None:
        if self._event_id and self._lines:
            await self._do_edit()
