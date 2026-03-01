"""Tests for src.matrix.formatter — message envelope formatting."""

import pytest
from src.matrix.formatter import (
    format_message_envelope,
    format_inter_agent_envelope,
    format_opencode_envelope,
    wrap_opencode_routing,
    is_no_reply,
    _extract_localpart,
    _build_reply_context_lines,
)

# Fixed timestamp for deterministic tests (2024-03-01 12:00:00 UTC)
FIXED_TS = 1709294400000


class TestExtractLocalpart:
    def test_full_mxid(self):
        assert _extract_localpart("@alice:matrix.org") == "alice"

    def test_localpart_only(self):
        assert _extract_localpart("alice") == "alice"

    def test_empty_string(self):
        assert _extract_localpart("") == ""

    def test_at_prefix_no_colon(self):
        assert _extract_localpart("@alice") == "alice"

    def test_none_input(self):
        assert _extract_localpart(None) == ""


class TestBuildReplyContextLines:
    def test_no_reply(self):
        assert _build_reply_context_lines() == []
        assert _build_reply_context_lines(None, None) == []

    def test_event_only(self):
        lines = _build_reply_context_lines("$orig123")
        assert "## Reply Context" in lines
        assert any("$orig123" in l for l in lines)
        assert not any("Reply-To Sender" in l for l in lines)

    def test_event_and_sender(self):
        lines = _build_reply_context_lines("$orig123", "@alice:matrix.org")
        assert "## Reply Context" in lines
        assert any("$orig123" in l for l in lines)
        assert any("alice" in l for l in lines)

    def test_sender_without_event_returns_empty(self):
        lines = _build_reply_context_lines(None, "@alice:matrix.org")
        assert lines == []


class TestFormatMessageEnvelope:
    def test_basic_structure(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hello"
        )
        assert "<system-reminder>" in result
        assert "</system-reminder>" in result
        assert "## Message Metadata" in result
        assert "## Chat Context" in result
        assert "hello" in result

    def test_sender_extracted(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:matrix.org", "alice", FIXED_TS, "hi"
        )
        assert "- **Sender**: alice" in result

    def test_group_context(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi",
            is_group=True, group_name="Test Room"
        )
        assert "Group chat" in result
        assert "Test Room" in result

    def test_dm_context(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi",
            is_group=False
        )
        assert "Direct message" in result

    def test_mentioned(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi",
            is_mentioned=True
        )
        assert "Mentioned" in result

    def test_no_reply_context_by_default(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi"
        )
        assert "Reply Context" not in result

    def test_with_reply_context(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi",
            reply_to_event_id="$orig123", reply_to_sender="@bob:d"
        )
        assert "## Reply Context" in result
        assert "$orig123" in result
        assert "bob" in result

    def test_reply_event_only(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi",
            reply_to_event_id="$orig123"
        )
        assert "## Reply Context" in result
        assert "$orig123" in result
        assert "Reply-To Sender" not in result

    def test_empty_body(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, ""
        )
        assert result.endswith("</system-reminder>")

    def test_no_reply_hint(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi"
        )
        assert "<no-reply/>" in result


class TestFormatInterAgentEnvelope:
    def test_basic_structure(self):
        result = format_inter_agent_envelope(
            "BMO", "agent-123", "hello", "!room:d", "$ev1", FIXED_TS
        )
        assert "<system-reminder>" in result
        assert "## Inter-Agent Context" in result
        assert "BMO" in result
        assert "agent-123" in result
        assert "MAIN task" in result

    def test_no_reply_by_default(self):
        result = format_inter_agent_envelope(
            "BMO", "agent-123", "hello", "!room:d", "$ev1", FIXED_TS
        )
        assert "Reply Context" not in result

    def test_with_reply(self):
        result = format_inter_agent_envelope(
            "BMO", "agent-123", "hello", "!room:d", "$ev1", FIXED_TS,
            reply_to_event_id="$orig", reply_to_sender="@user:d"
        )
        assert "## Reply Context" in result
        assert "$orig" in result
        # Reply Context should appear before Inter-Agent Context
        reply_pos = result.index("## Reply Context")
        agent_pos = result.index("## Inter-Agent Context")
        assert reply_pos < agent_pos

    def test_body_included(self):
        result = format_inter_agent_envelope(
            "BMO", "agent-123", "test message", "!room:d", "$ev1", FIXED_TS
        )
        assert "test message" in result


class TestFormatOpenCodeEnvelope:
    def test_basic_structure(self):
        result = format_opencode_envelope(
            "@oc_test:d", "hello", "!room:d", "$ev1", FIXED_TS
        )
        assert "<system-reminder>" in result
        assert "## OpenCode Context" in result
        assert "@oc_test:d" in result
        assert "Response Routing" in result

    def test_sender_extracted(self):
        result = format_opencode_envelope(
            "@oc_myproject_v2:matrix.oculair.ca", "hi", "!room:d", "$ev1", FIXED_TS
        )
        assert "oc_myproject_v2" in result

    def test_no_reply_by_default(self):
        result = format_opencode_envelope(
            "@oc_test:d", "hello", "!room:d", "$ev1", FIXED_TS
        )
        assert "Reply Context" not in result

    def test_with_reply(self):
        result = format_opencode_envelope(
            "@oc_test:d", "hello", "!room:d", "$ev1", FIXED_TS,
            reply_to_event_id="$orig", reply_to_sender="@alice:d"
        )
        assert "## Reply Context" in result
        assert "$orig" in result
        # Reply Context should appear before OpenCode Context
        reply_pos = result.index("## Reply Context")
        oc_pos = result.index("## OpenCode Context")
        assert reply_pos < oc_pos


class TestWrapOpenCodeRouting:
    def test_basic_wrap(self):
        result = wrap_opencode_routing("test content", "@oc_test:d")
        assert "[MESSAGE FROM OPENCODE USER]" in result
        assert "test content" in result
        assert "@oc_test:d" in result
        assert "RESPONSE INSTRUCTION (OPENCODE BRIDGE)" in result

    def test_mxid_in_example(self):
        result = wrap_opencode_routing("hello", "@oc_myproject:matrix.org")
        assert '@oc_myproject:matrix.org Here is my response' in result

    def test_multiline_content(self):
        content = "Line 1\nLine 2\nLine 3"
        result = wrap_opencode_routing(content, "@oc_test:d")
        assert "Line 1\nLine 2\nLine 3" in result

    def test_preserves_original_content(self):
        content = "[Image Upload: photo.jpg]\n\nPlease analyze this image."
        result = wrap_opencode_routing(content, "@oc_test:d")
        assert content in result


class TestEnvelopeSectionOrder:
    """Verify that sections appear in the correct order in all envelope types."""

    def test_standard_sections_order(self):
        result = format_message_envelope(
            "Matrix", "!room:d", "$ev1", "@alice:d", "alice", FIXED_TS, "hi",
            reply_to_event_id="$orig"
        )
        meta_pos = result.index("## Message Metadata")
        ctx_pos = result.index("## Chat Context")
        reply_pos = result.index("## Reply Context")
        assert meta_pos < ctx_pos < reply_pos

    def test_inter_agent_sections_order(self):
        result = format_inter_agent_envelope(
            "BMO", "agent-123", "hi", "!room:d", "$ev1", FIXED_TS,
            reply_to_event_id="$orig"
        )
        meta_pos = result.index("## Message Metadata")
        ctx_pos = result.index("## Chat Context")
        reply_pos = result.index("## Reply Context")
        agent_pos = result.index("## Inter-Agent Context")
        assert meta_pos < ctx_pos < reply_pos < agent_pos

    def test_opencode_sections_order(self):
        result = format_opencode_envelope(
            "@oc_test:d", "hi", "!room:d", "$ev1", FIXED_TS,
            reply_to_event_id="$orig"
        )
        meta_pos = result.index("## Message Metadata")
        ctx_pos = result.index("## Chat Context")
        reply_pos = result.index("## Reply Context")
        oc_pos = result.index("## OpenCode Context")
        assert meta_pos < ctx_pos < reply_pos < oc_pos


class TestIsNoReply:
    def test_exact_match(self):
        assert is_no_reply("<no-reply/>") is True

    def test_with_space(self):
        assert is_no_reply("<no-reply />") is True

    def test_with_whitespace(self):
        assert is_no_reply("  <no-reply/>  ") is True
        assert is_no_reply("\n<no-reply/>\n") is True

    def test_empty(self):
        assert is_no_reply("") is False
        assert is_no_reply(None) is False

    def test_normal_text(self):
        assert is_no_reply("Hello world") is False

    def test_embedded_not_matched(self):
        assert is_no_reply("Some text <no-reply/> more text") is False

    def test_case_sensitive(self):
        assert is_no_reply("<NO-REPLY/>") is False
