"""
Retry utilities for Letta API calls.

Handles transient errors like CONVERSATION_BUSY (409) with exponential backoff.
"""

import asyncio
import logging
from typing import TypeVar, Callable, Any, Optional

try:
    from letta_client import ConflictError
except ImportError:
    ConflictError = None  # type: ignore

logger = logging.getLogger("matrix_client.retry")

T = TypeVar("T")


class ConversationBusyError(Exception):
    """
    Raised when a conversation is busy processing another message 
    and all retry attempts have been exhausted.
    """
    def __init__(self, conversation_id: str, attempts: int, last_error: Optional[Exception] = None):
        self.conversation_id = conversation_id
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Conversation {conversation_id} is busy after {attempts} retry attempts"
        )


def is_conversation_busy_error(error: Exception) -> bool:
    """
    Check if an error indicates the conversation is busy processing another message.
    
    Letta returns 409 Conflict with "CONVERSATION_BUSY" when a conversation
    is already processing a message.
    
    Args:
        error: The exception to check
        
    Returns:
        True if this is a CONVERSATION_BUSY error
    """
    if ConflictError is not None and isinstance(error, ConflictError):
        error_str = str(error).lower()
        if "conversation_busy" in error_str or "busy" in error_str:
            return True
        
        body = getattr(error, "body", None)
        if body:
            body_str = str(body).lower()
            if "conversation_busy" in body_str or "busy" in body_str:
                return True
        
        return True
    
    error_str = str(error).lower()
    if "409" in error_str and ("conversation" in error_str or "busy" in error_str):
        return True
    
    return False


async def retry_on_conversation_busy(
    func: Callable[[], T],
    conversation_id: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 8.0,
    logger_instance: Optional[logging.Logger] = None,
) -> T:
    """
    Execute a function with retry logic for CONVERSATION_BUSY errors.
    
    Uses exponential backoff: 1s, 2s, 4s (capped at max_delay).
    
    Args:
        func: The async or sync function to execute (will be awaited if async)
        conversation_id: The conversation ID (for logging/error messages)
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 8.0)
        logger_instance: Optional logger (uses module logger if not provided)
        
    Returns:
        The result of the function call
        
    Raises:
        ConversationBusyError: If all retries are exhausted due to busy state
        Exception: Any other exception from the function
    """
    log = logger_instance or logger
    last_error: Optional[Exception] = None
    
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            return result  # type: ignore[return-value]
            
        except Exception as e:
            if is_conversation_busy_error(e):
                last_error = e
                
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    
                    log.warning(
                        f"[RETRY] Conversation {conversation_id} is busy, "
                        f"attempt {attempt + 1}/{max_retries + 1}, "
                        f"retrying in {delay:.1f}s"
                    )
                    
                    await asyncio.sleep(delay)
                    continue
                else:
                    log.error(
                        f"[RETRY] Conversation {conversation_id} still busy after "
                        f"{max_retries + 1} attempts, giving up"
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


async def send_message_with_retry(
    letta_client: Any,
    conversation_id: str,
    message: str,
    streaming: bool = True,
    max_retries: int = 3,
    logger_instance: Optional[logging.Logger] = None,
    **kwargs: Any,
) -> Any:
    """
    Send a message to a Letta conversation with automatic retry on CONVERSATION_BUSY.
    
    This is a convenience wrapper around retry_on_conversation_busy specifically
    for the conversations.messages.create endpoint.
    
    Args:
        letta_client: Initialized Letta SDK client
        conversation_id: The conversation to send to
        message: The message content
        streaming: Whether to use streaming mode (default: True)
        max_retries: Maximum retry attempts (default: 3)
        logger_instance: Optional logger
        **kwargs: Additional arguments to pass to messages.create
        
    Returns:
        The stream or response from the API
        
    Raises:
        ConversationBusyError: If conversation remains busy after retries
        Exception: Any other API error
    """
    log = logger_instance or logger
    
    def _create_message():
        log.debug(f"[RETRY] Sending message to conversation {conversation_id}")
        return letta_client.conversations.messages.create(
            conversation_id=conversation_id,
            input=message,
            streaming=streaming,
            **kwargs
        )
    
    return await retry_on_conversation_busy(
        func=_create_message,
        conversation_id=conversation_id,
        max_retries=max_retries,
        logger_instance=log,
    )
