"""
Unit tests for Matrix Group Gating.

Covers:
- group_config: parsing, loading, resolution
- mention_detection: pills, text, regex
- group_gating: all four modes + allowlist
"""
import re
import pytest

from src.matrix.group_config import (
    GroupConfig,
    GroupsConfig,
    load_groups_config,
    resolve_group_config,
    _parse_mode,
    _parse_config_dict,
)
from src.matrix.mention_detection import (
    MentionResult,
    detect_matrix_mention,
    _check_pill,
    _check_text,
    _check_regex,
)
from src.matrix.group_gating import (
    GatingResult,
    apply_group_gating,
)


# ═══════════════════════════════════════════════════════════════════
# group_config tests
# ═══════════════════════════════════════════════════════════════════

class TestParseMode:
    def test_valid_modes(self):
        assert _parse_mode("open") == "open"
        assert _parse_mode("listen") == "listen"
        assert _parse_mode("mention-only") == "mention-only"
        assert _parse_mode("disabled") == "disabled"

    def test_case_insensitive(self):
        assert _parse_mode("LISTEN") == "listen"
        assert _parse_mode(" Open ") == "open"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid group mode"):
            _parse_mode("foobar")


class TestParseConfigDict:
    def test_minimal(self):
        cfg = _parse_config_dict({})
        assert cfg.mode == "open"
        assert cfg.allowed_users == set()
        assert cfg.mention_patterns == []

    def test_full(self):
        cfg = _parse_config_dict({
            "mode": "listen",
            "allowed_users": ["@alice:example.com", "@bob:example.com"],
            "mention_patterns": [r"\bbot\b"],
        })
        assert cfg.mode == "listen"
        assert "@alice:example.com" in cfg.allowed_users
        assert len(cfg.compiled_patterns) == 1

    def test_invalid_regex_still_loads(self):
        """Bad regex is warned and skipped, not fatal."""
        cfg = _parse_config_dict({
            "mention_patterns": ["(unclosed", r"\bvalid\b"],
        })
        # Only the valid pattern survives
        assert len(cfg.compiled_patterns) == 1


class TestGroupConfig:
    def test_is_user_allowed_empty(self):
        cfg = GroupConfig()
        assert cfg.is_user_allowed("@anyone:example.com") is True

    def test_is_user_allowed_with_list(self):
        cfg = GroupConfig(allowed_users={"@alice:x.com"})
        assert cfg.is_user_allowed("@alice:x.com") is True
        assert cfg.is_user_allowed("@bob:x.com") is False


class TestLoadGroupsConfig:
    def test_empty_returns_empty(self):
        assert load_groups_config("") == {}

    def test_valid_json(self):
        raw = '{"*": {"mode": "listen"}, "!room1:x": {"mode": "open"}}'
        cfg = load_groups_config(raw)
        assert "*" in cfg
        assert cfg["*"].mode == "listen"
        assert cfg["!room1:x"].mode == "open"

    def test_invalid_json_returns_empty(self):
        assert load_groups_config("not json") == {}

    def test_non_dict_returns_empty(self):
        assert load_groups_config("[1, 2, 3]") == {}


class TestResolveGroupConfig:
    def test_exact_match(self):
        groups: GroupsConfig = {
            "!room1:x": GroupConfig(mode="disabled"),
            "*": GroupConfig(mode="listen"),
        }
        assert resolve_group_config("!room1:x", groups).mode == "disabled"

    def test_wildcard_fallback(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="listen")}
        assert resolve_group_config("!unknown:x", groups).mode == "listen"

    def test_default_open(self):
        assert resolve_group_config("!room:x", {}).mode == "open"


# ═══════════════════════════════════════════════════════════════════
# mention_detection tests
# ═══════════════════════════════════════════════════════════════════

class TestCheckPill:
    def test_mentioned(self):
        source = {"content": {"m.mentions": {"user_ids": ["@bot:x.com"]}}}
        hit, text = _check_pill(source, "@bot:x.com")
        assert hit is True
        assert text == "@bot:x.com"

    def test_not_mentioned(self):
        source = {"content": {"m.mentions": {"user_ids": ["@other:x.com"]}}}
        hit, _ = _check_pill(source, "@bot:x.com")
        assert hit is False

    def test_no_source(self):
        hit, _ = _check_pill(None, "@bot:x.com")
        assert hit is False

    def test_no_mentions_field(self):
        source = {"content": {}}
        hit, _ = _check_pill(source, "@bot:x.com")
        assert hit is False

    def test_user_ids_is_string_not_list(self):
        """Malformed m.mentions.user_ids should not crash."""
        source = {"content": {"m.mentions": {"user_ids": "@bot:x.com"}}}
        hit, _ = _check_pill(source, "@bot:x.com")
        assert hit is False

    def test_user_ids_is_int(self):
        source = {"content": {"m.mentions": {"user_ids": 42}}}
        hit, _ = _check_pill(source, "@bot:x.com")
        assert hit is False

    def test_mentions_is_list_not_dict(self):
        source = {"content": {"m.mentions": ["@bot:x.com"]}}
        hit, _ = _check_pill(source, "@bot:x.com")
        assert hit is False


class TestCheckText:
    def test_at_mention(self):
        hit, text = _check_text("hello @meridian how are you?", "meridian")
        assert hit is True
        assert text == "@meridian"

    def test_case_insensitive(self):
        hit, _ = _check_text("hey @MERIDIAN", "meridian")
        assert hit is True

    def test_no_mention(self):
        hit, _ = _check_text("just a normal message", "meridian")
        assert hit is False

    def test_quoted_mention_ignored(self):
        """Reply fallback content should be stripped before matching."""
        body = "> <@someone:x.com> @meridian said hi\n\nactual message"
        hit, _ = _check_text(body, "meridian")
        assert hit is False

    def test_empty_body(self):
        hit, _ = _check_text("", "meridian")
        assert hit is False


class TestCheckRegex:
    def test_pattern_match(self):
        patterns = [re.compile(r"\bmeridian\b", re.IGNORECASE)]
        hit, text = _check_regex("hey meridian, help", patterns)
        assert hit is True
        assert text == "meridian"

    def test_no_match(self):
        patterns = [re.compile(r"\bmeridian\b", re.IGNORECASE)]
        hit, _ = _check_regex("unrelated message", patterns)
        assert hit is False

    def test_empty_patterns(self):
        hit, _ = _check_regex("meridian is here", [])
        assert hit is False


class TestDetectMatrixMention:
    BOT = "@meridian:matrix.oculair.ca"

    def test_pill_takes_priority(self):
        source = {"content": {"m.mentions": {"user_ids": [self.BOT]}}}
        result = detect_matrix_mention("hello @meridian", source, self.BOT)
        assert result.was_mentioned is True
        assert result.method == "pill"

    def test_text_fallback(self):
        result = detect_matrix_mention("hey @meridian", None, self.BOT)
        assert result.was_mentioned is True
        assert result.method == "text"

    def test_regex_fallback(self):
        patterns = [re.compile(r"\bhey bot\b", re.IGNORECASE)]
        result = detect_matrix_mention("hey bot, do something", None, self.BOT, patterns)
        assert result.was_mentioned is True
        assert result.method == "regex"

    def test_no_mention(self):
        result = detect_matrix_mention("normal chat message", None, self.BOT)
        assert result.was_mentioned is False
        assert result.method is None


# ═══════════════════════════════════════════════════════════════════
# group_gating tests
# ═══════════════════════════════════════════════════════════════════

BOT_ID = "@meridian:matrix.oculair.ca"
ROOM_ID = "!test:x.com"
SENDER = "@alice:x.com"


class TestApplyGroupGatingOpen:
    def test_processes_all_messages(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="open")}
        result = apply_group_gating(ROOM_ID, SENDER, "hello", None, BOT_ID, groups)
        assert result is not None
        assert result.mode == "open"
        assert result.silent is False

    def test_mention_status_tracked(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="open")}
        result = apply_group_gating(ROOM_ID, SENDER, "hey @meridian", None, BOT_ID, groups)
        assert result is not None
        assert result.was_mentioned is True


class TestApplyGroupGatingListen:
    def test_silent_when_not_mentioned(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="listen")}
        result = apply_group_gating(ROOM_ID, SENDER, "normal chat", None, BOT_ID, groups)
        assert result is not None
        assert result.mode == "listen"
        assert result.silent is True

    def test_active_when_mentioned(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="listen")}
        result = apply_group_gating(ROOM_ID, SENDER, "hey @meridian", None, BOT_ID, groups)
        assert result is not None
        assert result.silent is False
        assert result.was_mentioned is True

    def test_active_when_mentioned_via_pill(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="listen")}
        source = {"content": {"m.mentions": {"user_ids": [BOT_ID]}}}
        result = apply_group_gating(ROOM_ID, SENDER, "hello", source, BOT_ID, groups)
        assert result is not None
        assert result.silent is False
        assert result.method == "pill"

    def test_active_when_custom_regex_matches(self):
        groups: GroupsConfig = {
            "*": GroupConfig(mode="listen", mention_patterns=[r"\bmeridian\b"]),
        }
        result = apply_group_gating(ROOM_ID, SENDER, "hey meridian", None, BOT_ID, groups)
        assert result is not None
        assert result.silent is False
        assert result.method == "regex"


class TestApplyGroupGatingMentionOnly:
    def test_drops_without_mention(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="mention-only")}
        result = apply_group_gating(ROOM_ID, SENDER, "normal chat", None, BOT_ID, groups)
        assert result is None

    def test_processes_with_mention(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="mention-only")}
        result = apply_group_gating(ROOM_ID, SENDER, "hey @meridian", None, BOT_ID, groups)
        assert result is not None
        assert result.mode == "mention-only"
        assert result.silent is False


class TestApplyGroupGatingDisabled:
    def test_drops_all(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="disabled")}
        result = apply_group_gating(ROOM_ID, SENDER, "hello", None, BOT_ID, groups)
        assert result is None

    def test_drops_even_with_mention(self):
        groups: GroupsConfig = {"*": GroupConfig(mode="disabled")}
        result = apply_group_gating(ROOM_ID, SENDER, "hey @meridian", None, BOT_ID, groups)
        assert result is None


class TestApplyGroupGatingAllowlist:
    def test_allowed_user_passes(self):
        groups: GroupsConfig = {
            "*": GroupConfig(mode="open", allowed_users={SENDER}),
        }
        result = apply_group_gating(ROOM_ID, SENDER, "hello", None, BOT_ID, groups)
        assert result is not None

    def test_blocked_user_dropped(self):
        groups: GroupsConfig = {
            "*": GroupConfig(mode="open", allowed_users={"@other:x.com"}),
        }
        result = apply_group_gating(ROOM_ID, SENDER, "hello", None, BOT_ID, groups)
        assert result is None


class TestApplyGroupGatingNoConfig:
    """When MATRIX_GROUPS_JSON is not set, groups dict is empty."""

    def test_empty_config_defaults_open(self):
        result = apply_group_gating(ROOM_ID, SENDER, "hello", None, BOT_ID, {})
        assert result is not None
        assert result.mode == "open"
        assert result.silent is False


class TestRoomSpecificOverride:
    def test_specific_room_overrides_wildcard(self):
        groups: GroupsConfig = {
            "*": GroupConfig(mode="listen"),
            ROOM_ID: GroupConfig(mode="open"),
        }
        result = apply_group_gating(ROOM_ID, SENDER, "hello", None, BOT_ID, groups)
        assert result is not None
        assert result.mode == "open"
        assert result.silent is False

    def test_other_rooms_use_wildcard(self):
        groups: GroupsConfig = {
            "*": GroupConfig(mode="listen"),
            ROOM_ID: GroupConfig(mode="open"),
        }
        result = apply_group_gating("!other:x.com", SENDER, "hello", None, BOT_ID, groups)
        assert result is not None
        assert result.mode == "listen"
        assert result.silent is True
