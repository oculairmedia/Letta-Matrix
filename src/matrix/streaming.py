"""
Letta Streaming Adapter for Matrix Client — re-export hub.

All public names are re-exported from their focused modules for
backward compatibility. New code should import directly from:
  - streaming_types: StreamEventType, StreamEvent, SELF_DELIVERY_TOOL_NAMES
  - stream_reader: StepStreamReader
  - streaming_handler: StreamingMessageHandler
  - streaming_live_edit: LiveEditStreamingHandler
"""

from src.matrix.streaming_types import (  # noqa: F401
    SELF_DELIVERY_TOOL_NAMES,
    StreamEvent,
    StreamEventType,
    is_self_delivery_to_room,
)
from src.matrix.stream_reader import StepStreamReader  # noqa: F401
from src.matrix.streaming_handler import StreamingMessageHandler  # noqa: F401
from src.matrix.streaming_live_edit import LiveEditStreamingHandler  # noqa: F401

__all__ = [
    "SELF_DELIVERY_TOOL_NAMES",
    "StreamEvent",
    "StreamEventType",
    "StepStreamReader",
    "StreamingMessageHandler",
    "LiveEditStreamingHandler",
    "is_self_delivery_to_room",
]
