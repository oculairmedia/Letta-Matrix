"""
Streaming types, enums, and constants for the Letta streaming adapter.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("matrix_client.streaming")

SELF_DELIVERY_TOOL_NAMES = frozenset({
    "matrix_messaging",
    "send_message",
    "matrix-identity-bridge_matrix_messaging",
})


_MAX_DESC_LEN = 60

# Map tool names to the argument field that best describes what it does.
_TOOL_DESC_FIELDS: dict[str, tuple[str, ...]] = {
    "Bash": ("description", "command"),
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "Grep": ("pattern",),
    "Glob": ("pattern",),
    "search_documents": ("query",),
    "Task": ("description", "prompt"),
    "matrix_messaging": ("operation", "message"),
}


def _extract_tool_description(tool_name: str, metadata: dict) -> str:
    """Extract a human-readable description from tool call arguments.

    Returns a truncated string suitable for inline progress display,
    or empty string if nothing useful can be extracted.
    """
    args_raw = metadata.get("arguments", "")
    if not args_raw:
        return ""
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(args, dict):
        return ""

    # Try tool-specific fields first
    fields = _TOOL_DESC_FIELDS.get(tool_name, ())
    for field_name in fields:
        val = args.get(field_name)
        if val and isinstance(val, str):
            val = val.strip()
            if field_name == "file_path":
                # Show basename only
                val = val.rsplit("/", 1)[-1]
            if field_name == "command":
                # Strip leading whitespace/newlines from commands
                val = val.split("\n")[0].strip()
            if len(val) > _MAX_DESC_LEN:
                val = val[:_MAX_DESC_LEN - 1] + "…"
            return val

    # Generic fallback: first short string value
    for v in args.values():
        if isinstance(v, str) and 3 <= len(v.strip()) <= _MAX_DESC_LEN:
            return v.strip()

    return ""


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

    def format_progress(self, tool_call_count: int | None = None) -> str:
        """Format event as progress message for Matrix.

        Args:
            tool_call_count: Running count of tool calls in this stream session.
        """
        if self.type == StreamEventType.TOOL_CALL:
            tool_name = self.metadata.get('tool_name', 'unknown')
            count_tag = f" [{tool_call_count}]" if tool_call_count else ""
            desc = _extract_tool_description(tool_name, self.metadata)
            desc_tag = f" — {desc}" if desc else "..."
            return f"🔧 {tool_name}{count_tag}{desc_tag}"
        elif self.type == StreamEventType.TOOL_RETURN:
            tool_name = self.metadata.get('tool_name', 'unknown')
            count_tag = f" [{tool_call_count}]" if tool_call_count else ""
            status = self.metadata.get('status', 'unknown')
            if status == 'success':
                return f"✅ {tool_name}{count_tag}"
            else:
                return f"❌ {tool_name}{count_tag} (failed)"
        elif self.type == StreamEventType.REASONING:
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


def is_self_delivery_to_room(event: StreamEvent, room_id: str) -> bool:
    """Check if a tool call event is a self-delivery targeting the given room."""
    tool_name = event.metadata.get("tool_name")
    if tool_name not in SELF_DELIVERY_TOOL_NAMES:
        return False
    args_raw = event.metadata.get("arguments", "")
    if not args_raw:
        # Gateway stream events may not include arguments — if the tool name
        # matches a self-delivery tool, assume it targets the current room
        # (conservative: better to suppress a duplicate than to double-post)
        return True
    try:
        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
    except (json.JSONDecodeError, TypeError):
        return True  # Tool matched but args unparseable — assume self-delivery
    target_room = args.get("room_id") or args.get("chatId") or ""
    if target_room and target_room == room_id:
        return True
    target_to = args.get("to_mxid") or ""
    if target_to:
        return False
    op = args.get("operation", "")
    if op in ("send", "talk_to_agent", "talk_to_opencode"):
        return not target_room
    return False
