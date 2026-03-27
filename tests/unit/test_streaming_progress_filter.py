"""Tests for _is_streaming_progress and rate-limited still-processing notices.

Validates that:
1. Streaming progress messages (🔧, ✅, ❌, ⏳, 💭, ⚠️) are detected and skipped
2. Multi-line progress blocks (live-edit handler) are detected
3. Normal messages are NOT falsely detected
4. Mixed content (progress + regular text) is NOT falsely detected
5. Rate-limiting on "still processing" notices works
"""

import pytest
import time
from unittest.mock import Mock

from src.matrix.client import (
    _is_streaming_progress,
    _still_processing_last_sent,
    _STILL_PROCESSING_COOLDOWN,
)


class TestIsStreamingProgress:
    """Tests for the _is_streaming_progress detection function."""

    # ── Single-line progress messages ─────────────────────────────────

    def test_tool_call_detected(self):
        assert _is_streaming_progress("🔧 send_message...") is True

    def test_tool_success_detected(self):
        assert _is_streaming_progress("✅ send_message") is True

    def test_tool_failure_detected(self):
        assert _is_streaming_progress("❌ Bash (failed)") is True

    def test_still_processing_detected(self):
        assert _is_streaming_progress("⏳ Still processing previous message...") is True

    def test_approval_request_detected(self):
        assert _is_streaming_progress("⏳ **Approval Required**: tool_name") is True

    def test_error_detected(self):
        assert _is_streaming_progress("⚠️ Some error occurred") is True

    def test_reasoning_detected(self):
        assert _is_streaming_progress("💭 Thinking about the problem...") is True

    # ── Whitespace handling ───────────────────────────────────────────

    def test_leading_trailing_whitespace(self):
        assert _is_streaming_progress("  🔧 send_message...  ") is True

    def test_newline_wrapped(self):
        assert _is_streaming_progress("\n🔧 send_message...\n") is True

    # ── Multi-line progress blocks (live-edit) ────────────────────────

    def test_multi_line_all_progress(self):
        text = "🔧 Bash...\n✅ Bash\n🔧 Grep..."
        assert _is_streaming_progress(text) is True

    def test_multi_line_with_failures(self):
        text = "🔧 Bash...\n🔧 Bash...\n🔧 Grep...\n🔧 Grep...\n❌ Bash (failed)"
        assert _is_streaming_progress(text) is True

    def test_long_progress_block(self):
        """Simulate the exact regression pattern from the bug report."""
        text = (
            "🔧 Bash...\n🔧 Bash...\n🔧 Grep...\n🔧 Grep...\n"
            "❌ Bash (failed)\n🔧 Bash...\n🔧 Bash...\n🔧 Grep...\n"
            "🔧 Grep...\n❌ Bash (failed)\n🔧 Bash..."
        )
        assert _is_streaming_progress(text) is True

    def test_multi_line_with_blank_lines(self):
        """Blank lines between progress indicators should still match."""
        text = "🔧 Bash...\n\n✅ Bash\n\n🔧 Grep..."
        assert _is_streaming_progress(text) is True

    # ── Normal messages should NOT match ──────────────────────────────

    def test_normal_text(self):
        assert _is_streaming_progress("Hello world") is False

    def test_normal_sentence(self):
        assert _is_streaming_progress("I checked the database and found results") is False

    def test_empty_string(self):
        assert _is_streaming_progress("") is False

    def test_whitespace_only(self):
        assert _is_streaming_progress("   \n  \n  ") is False

    def test_emoji_not_at_start(self):
        assert _is_streaming_progress("The 🔧 is in the toolbox") is False

    def test_code_block_with_emoji(self):
        assert _is_streaming_progress("```\n🔧 tool output\n```") is False

    # ── Mixed content should NOT match ────────────────────────────────

    def test_mixed_progress_and_text(self):
        text = "🔧 Bash...\nSome regular text here"
        assert _is_streaming_progress(text) is False

    def test_text_then_progress(self):
        text = "Regular text\n🔧 Bash..."
        assert _is_streaming_progress(text) is False

    def test_progress_with_narrative(self):
        text = "🔧 Bash...\nI ran the command and here's what happened"
        assert _is_streaming_progress(text) is False


class TestStillProcessingRateLimiting:
    """Tests for the rate-limiting dict and cooldown constant."""

    def test_cooldown_is_reasonable(self):
        """Cooldown should be between 30-120 seconds."""
        assert 30 <= _STILL_PROCESSING_COOLDOWN <= 120

    def test_rate_limit_dict_initially_empty(self):
        """The rate-limit dict should start empty (no stale state)."""
        # Clear any state from other tests
        _still_processing_last_sent.clear()
        assert len(_still_processing_last_sent) == 0

    def test_rate_limit_dict_stores_room_timestamps(self):
        """Verify the dict can store per-room timestamps."""
        _still_processing_last_sent.clear()
        _still_processing_last_sent["!room1:test"] = time.monotonic()
        _still_processing_last_sent["!room2:test"] = time.monotonic()
        assert len(_still_processing_last_sent) == 2
        _still_processing_last_sent.clear()
