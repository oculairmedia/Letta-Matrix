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
    ):
        """
        Initialize the stream reader.
        
        Args:
            letta_client: Initialized Letta SDK client
            include_reasoning: Whether to emit reasoning events
            include_pings: Whether to include keepalive pings in stream
            timeout: Maximum stream duration in seconds
        """
        self.client = letta_client
        self.include_reasoning = include_reasoning
        self.include_pings = include_pings
        self.timeout = timeout
        self._last_tool_name: Optional[str] = None
    
    async def stream_message(
        self,
        agent_id: str,
        message: str,
        background: bool = False,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Stream a message to the agent and yield normalized events.
        
        Args:
            agent_id: Letta agent ID
            message: User message to send
            background: Whether to use background mode for long operations
            
        Yields:
            StreamEvent objects for each step
        """
        logger.info(f"Starting step stream for agent {agent_id}")
        
        try:
            # Run streaming in executor since SDK is synchronous
            # Use a queue to bridge sync stream to async iteration
            import queue
            import threading
            
            chunk_queue = queue.Queue()
            error_holder = []
            
            def _consume_stream():
                """Consume stream in background thread and put chunks in queue"""
                try:
                    logger.debug(f"[STREAM] Creating stream for agent {agent_id}")
                    stream = self.client.agents.messages.stream(
                        agent_id=agent_id,
                        input=message,
                        streaming=True,
                        stream_tokens=False,  # Step streaming only
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
            loop = asyncio.get_event_loop()
            start_time = asyncio.get_event_loop().time()
            
            while True:
                # Check timeout
                if loop.time() - start_time > self.timeout:
                    logger.error(f"[STREAM] Timeout after {self.timeout}s")
                    raise asyncio.TimeoutError()
                
                # Try to get chunk with timeout
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
    ):
        """
        Initialize the handler.
        
        Args:
            send_message: Async function(room_id, content) -> event_id
            delete_message: Async function(room_id, event_id) -> None
            room_id: Matrix room ID
            delete_progress: If True, delete progress messages when next event arrives
        """
        self.send_message = send_message
        self.delete_message = delete_message
        self.room_id = room_id
        self.delete_progress = delete_progress
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
            # Send final assistant response (not deleted)
            return await self.send_message(self.room_id, event.content or "")
        
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
        
        return None
    
    async def cleanup(self):
        """Clean up any remaining progress messages (only if delete_progress=True)"""
        if self.delete_progress and self._progress_event_id:
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
            except Exception as e:
                logger.warning(f"Failed to cleanup progress message: {e}")
            self._progress_event_id = None
