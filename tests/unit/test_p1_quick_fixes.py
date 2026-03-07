"""
Unit tests for three P1 bug fixes:
1. EditAsAgentRequest msgtype default (m.text, not m.notice)
2. Mention detection with full MXID and case-insensitive matching
3. Group gating fail-closed on unknown mode
"""
import pytest
import logging

from src.api.schemas.identity import EditAsAgentRequest, SendAsAgentRequest
from src.matrix.mention_detection import _check_text, detect_matrix_mention, MentionResult
from src.matrix.group_gating import apply_group_gating, GatingResult
from src.matrix.group_config import GroupConfig



# ═══════════════════════════════════════════════════════════════════
# TestEditAsAgentRequestMsgtype
# ═══════════════════════════════════════════════════════════════════

class TestEditAsAgentRequestMsgtype:
    """Test that EditAsAgentRequest defaults to m.text, not m.notice."""

    def test_default_msgtype_is_m_text(self):
        """Default msgtype should be 'm.text'."""
        req = EditAsAgentRequest(
            agent_id="agent-123",
            room_id="!room:example.com",
            event_id="$event123",
            message="Updated message",
        )
        assert req.msgtype == "m.text"

    def test_explicit_msgtype_m_notice_works(self):
        """Explicit msgtype 'm.notice' should still work when specified."""
        req = EditAsAgentRequest(
            agent_id="agent-123",
            room_id="!room:example.com",
            event_id="$event123",
            message="Updated message",
            msgtype="m.notice"
        )
        assert req.msgtype == "m.notice"

    def test_matches_send_as_agent_request_default(self):
        """EditAsAgentRequest and SendAsAgentRequest should both default to m.text."""
        edit_req = EditAsAgentRequest(
            agent_id="agent-123",
            room_id="!room:example.com",
            event_id="$event123",
            message="Updated message",
        )
        send_req = SendAsAgentRequest(
            agent_id="agent-123",
            room_id="!room:example.com",
            message="New message",
        )
        assert edit_req.msgtype == send_req.msgtype == "m.text"


# ═══════════════════════════════════════════════════════════════════
# TestMentionDetectionMXID
# ═══════════════════════════════════════════════════════════════════

class TestMentionDetectionMXID:
    """Test mention detection with full MXID and case-insensitive matching."""

    def test_bare_mention_matches_full_mxid(self):
        """Bare @mention should match when bot_user_id is full MXID."""
        hit, text = _check_text("hello @bot", "@bot:server.com")
        assert hit is True
        assert text == "@bot"

    def test_full_mxid_mention_matches(self):
        """Full MXID mention should match."""
        hit, text = _check_text("hello @bot:server.com", "@bot:server.com")
        assert hit is True
        assert text == "@bot:server.com"

    def test_different_server_does_not_match(self):
        """Mention with different server should NOT match."""
        hit, text = _check_text("hello @bot:other.com", "@bot:server.com")
        assert hit is False
        assert text is None

    def test_different_localpart_does_not_match(self):
        """Mention with different localpart should NOT match."""
        hit, text = _check_text("hello @bot_helper", "@bot:server.com")
        assert hit is False
        assert text is None

    def test_case_insensitive_matching(self):
        """Mention matching should be case-insensitive."""
        hit, text = _check_text("hello @BOT", "@bot:server.com")
        assert hit is True
        assert text is not None and text.lower() == "@bot"

    def test_detect_matrix_mention_with_different_server(self):
        """detect_matrix_mention should return was_mentioned=False for different server."""
        result = detect_matrix_mention(
            body="hello @bot:other.com",
            event_source=None,
            bot_user_id="@bot:server.com"
        )
        assert result.was_mentioned is False
        assert result.method is None


# ═══════════════════════════════════════════════════════════════════
# TestGatingFailClosed
# ═══════════════════════════════════════════════════════════════════

class TestGatingFailClosed:
    """Test that group gating fails closed on unknown mode."""

    def test_unknown_mode_returns_none(self, caplog):
        """Unknown mode should return None (deny), not a permissive GatingResult."""
        # Create a GroupConfig with an unknown mode
        # We'll need to bypass validation, so we'll create it directly
        cfg = GroupConfig()
        cfg.mode = "unknown_mode"  # type: ignore
        
        # Create a minimal groups config dict
        groups_config = {"!room:example.com": cfg}
        
        with caplog.at_level(logging.WARNING):
            result = apply_group_gating(
                room_id="!room:example.com",
                sender_id="@user:example.com",
                body="hello @bot:example.com",
                event_source=None,
                bot_user_id="@bot:example.com",
                groups_config=groups_config
            )
        
        assert result is None
        assert "Unknown mode" in caplog.text

    def test_valid_mode_always_works(self):
        """Valid mode 'always' should still work (if it exists)."""
        # Note: 'always' might not be a real mode, but 'open' is
        cfg = GroupConfig(mode="open")
        groups_config = {"!room:example.com": cfg}
        
        result = apply_group_gating(
            room_id="!room:example.com",
            sender_id="@user:example.com",
            body="hello",
            event_source=None,
            bot_user_id="@bot:example.com",
            groups_config=groups_config
        )
        
        assert result is not None
        assert isinstance(result, GatingResult)
        assert result.mode == "open"

    def test_valid_mode_mention_only_with_mention(self):
        """Valid mode 'mention-only' with mention should work."""
        cfg = GroupConfig(mode="mention-only")
        groups_config = {"!room:example.com": cfg}
        
        result = apply_group_gating(
            room_id="!room:example.com",
            sender_id="@user:example.com",
            body="hello @bot:example.com",
            event_source=None,
            bot_user_id="@bot:example.com",
            groups_config=groups_config
        )
        
        assert result is not None
        assert isinstance(result, GatingResult)
        assert result.mode == "mention-only"
        assert result.was_mentioned is True

    def test_warning_logged_on_unknown_mode(self, caplog):
        """Warning should be logged when unknown mode is encountered."""
        cfg = GroupConfig()
        cfg.mode = "invalid_mode_xyz"  # type: ignore
        groups_config = {"!room:example.com": cfg}
        
        with caplog.at_level(logging.WARNING):
            apply_group_gating(
                room_id="!room:example.com",
                sender_id="@user:example.com",
                body="test",
                event_source=None,
                bot_user_id="@bot:example.com",
                groups_config=groups_config
            )
        
        assert any("Unknown mode" in record.message for record in caplog.records)
