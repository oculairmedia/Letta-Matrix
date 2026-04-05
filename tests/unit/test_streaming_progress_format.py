"""Tests for enriched tool call progress display (GitHub #29)."""

import json

import pytest

from src.matrix.streaming_types import (
    StreamEvent,
    StreamEventType,
    _extract_tool_description,
)


# ---------------------------------------------------------------------------
# _extract_tool_description
# ---------------------------------------------------------------------------

class TestExtractToolDescription:
    def test_bash_description_field(self):
        meta = {"arguments": json.dumps({"description": "List recent CI runs", "command": "gh run list"})}
        assert _extract_tool_description("Bash", meta) == "List recent CI runs"

    def test_bash_falls_back_to_command(self):
        meta = {"arguments": json.dumps({"command": "git status && git diff --stat"})}
        assert _extract_tool_description("Bash", meta) == "git status && git diff --stat"

    def test_bash_command_multiline_takes_first(self):
        meta = {"arguments": json.dumps({"command": "echo hello\necho world"})}
        assert _extract_tool_description("Bash", meta) == "echo hello"

    def test_bash_command_truncated(self):
        long_cmd = "a" * 100
        meta = {"arguments": json.dumps({"command": long_cmd})}
        desc = _extract_tool_description("Bash", meta)
        assert len(desc) <= 60
        assert desc.endswith("…")

    def test_read_file_path_basename(self):
        meta = {"arguments": json.dumps({"file_path": "/opt/stacks/matrix-tuwunel-deploy/src/matrix/streaming_types.py"})}
        assert _extract_tool_description("Read", meta) == "streaming_types.py"

    def test_grep_pattern(self):
        meta = {"arguments": json.dumps({"pattern": "async def handle_event", "path": "/app/src"})}
        assert _extract_tool_description("Grep", meta) == "async def handle_event"

    def test_glob_pattern(self):
        meta = {"arguments": json.dumps({"pattern": "**/*.py"})}
        assert _extract_tool_description("Glob", meta) == "**/*.py"

    def test_search_documents_query(self):
        meta = {"arguments": json.dumps({"query": "How does the streaming handler work?"})}
        assert _extract_tool_description("search_documents", meta) == "How does the streaming handler work?"

    def test_task_description(self):
        meta = {"arguments": json.dumps({"description": "Find auth code", "prompt": "Search for all auth-related code"})}
        assert _extract_tool_description("Task", meta) == "Find auth code"

    def test_unknown_tool_generic_fallback(self):
        meta = {"arguments": json.dumps({"query": "hello world", "limit": 10})}
        assert _extract_tool_description("some_custom_tool", meta) == "hello world"

    def test_empty_arguments(self):
        assert _extract_tool_description("Bash", {"arguments": ""}) == ""
        assert _extract_tool_description("Bash", {}) == ""

    def test_malformed_json(self):
        assert _extract_tool_description("Bash", {"arguments": "not json"}) == ""

    def test_arguments_as_dict(self):
        """Arguments can be pre-parsed dict instead of JSON string."""
        meta = {"arguments": {"description": "Install deps", "command": "npm install"}}
        assert _extract_tool_description("Bash", meta) == "Install deps"

    def test_no_useful_string_values(self):
        meta = {"arguments": json.dumps({"limit": 10, "offset": 0})}
        assert _extract_tool_description("some_tool", meta) == ""

    def test_matrix_messaging_operation(self):
        meta = {"arguments": json.dumps({"operation": "send", "message": "hello there"})}
        assert _extract_tool_description("matrix_messaging", meta) == "send"


# ---------------------------------------------------------------------------
# format_progress with tool_call_count
# ---------------------------------------------------------------------------

class TestFormatProgressEnriched:
    def test_tool_call_with_count_and_description(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={
                "tool_name": "Bash",
                "arguments": json.dumps({"description": "List files", "command": "ls -la"}),
            },
        )
        result = event.format_progress(tool_call_count=3)
        assert result == "🔧 Bash [3] — List files"

    def test_tool_call_without_count(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={
                "tool_name": "Bash",
                "arguments": json.dumps({"description": "List files"}),
            },
        )
        result = event.format_progress()
        assert result == "🔧 Bash — List files"

    def test_tool_call_no_description_shows_ellipsis(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "custom_tool"},
        )
        result = event.format_progress(tool_call_count=1)
        assert result == "🔧 custom_tool [1]..."

    def test_tool_return_success_with_count(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_RETURN,
            metadata={"tool_name": "Grep", "status": "success"},
        )
        result = event.format_progress(tool_call_count=5)
        assert result == "✅ Grep [5]"

    def test_tool_return_failure_with_count(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_RETURN,
            metadata={"tool_name": "Bash", "status": "error"},
        )
        result = event.format_progress(tool_call_count=2)
        assert result == "❌ Bash [2] (failed)"

    def test_tool_return_without_count(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_RETURN,
            metadata={"tool_name": "Read", "status": "success"},
        )
        result = event.format_progress()
        assert result == "✅ Read"

    def test_reasoning_unchanged(self):
        event = StreamEvent(type=StreamEventType.REASONING, content="Thinking about it")
        assert event.format_progress(tool_call_count=10) == "💭 Thinking about it"

    def test_approval_unchanged(self):
        event = StreamEvent(
            type=StreamEventType.APPROVAL_REQUEST,
            metadata={"tool_calls": [{"name": "Bash"}]},
        )
        result = event.format_progress(tool_call_count=3)
        assert "Approval Required" in result

    def test_backward_compatible_no_args(self):
        """Calling format_progress() with no args should still work."""
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={"tool_name": "Read"},
        )
        result = event.format_progress()
        assert "Read" in result

    def test_file_path_shows_basename(self):
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            metadata={
                "tool_name": "Read",
                "arguments": json.dumps({"file_path": "/very/long/path/to/file.py"}),
            },
        )
        result = event.format_progress(tool_call_count=7)
        assert result == "🔧 Read [7] — file.py"
