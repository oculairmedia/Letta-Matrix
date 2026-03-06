"""
Unit tests for Matrix pill formatter.

Tests the conversion of @mentions in agent messages to clickable Matrix pills
with m.mentions push notification support.
"""

import pytest
from unittest.mock import patch


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_agent_mappings():
    """Sample agent mappings for testing."""
    return {
        "agent-597b5756-2915-4560-ba6b-91005f085166": {
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
            "agent_name": "Meridian",
            "matrix_user_id": "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca",
            "room_id": "!meridian:matrix.oculair.ca",
            "room_created": True,
        },
        "agent-bmo-1234-5678": {
            "agent_id": "agent-bmo-1234-5678",
            "agent_name": "BMO",
            "matrix_user_id": "@agent_bmo_1234_5678:matrix.oculair.ca",
            "room_id": "!bmo:matrix.oculair.ca",
            "room_created": True,
        },
        "agent-test-html-name": {
            "agent_id": "agent-test-html-name",
            "agent_name": "O'Brien & Co",
            "matrix_user_id": "@agent_test_html:matrix.oculair.ca",
            "room_id": "!test:matrix.oculair.ca",
            "room_created": True,
        },
    }


@pytest.fixture
def mock_mapping_service(sample_agent_mappings):
    """Mock the mapping service functions used by pill_formatter."""
    with patch("src.matrix.pill_formatter.get_mapping_by_matrix_user") as mock_by_user, \
         patch("src.matrix.pill_formatter.get_mapping_by_agent_name") as mock_by_name:

        def by_user(mxid):
            for mapping in sample_agent_mappings.values():
                if mapping["matrix_user_id"] == mxid:
                    return mapping
            return None

        def by_name(name, fuzzy=True):
            name_lower = name.lower()
            for mapping in sample_agent_mappings.values():
                agent_name = mapping["agent_name"].lower()
                if fuzzy:
                    if name_lower == agent_name or name_lower in agent_name:
                        return mapping
                else:
                    if name_lower == agent_name:
                        return mapping
            return None

        mock_by_user.side_effect = by_user
        mock_by_name.side_effect = by_name

        yield {"by_user": mock_by_user, "by_name": mock_by_name}


# =============================================================================
# Tests for _make_pill()
# =============================================================================


class TestMakePill:
    """Tests for pill HTML generation."""

    def test_basic_pill(self):
        from src.matrix.pill_formatter import _make_pill

        result = _make_pill("@user:example.com", "User Name")
        assert result == '<a href="https://matrix.to/#/@user:example.com">User Name</a>'

    def test_html_escape_display_name(self):
        from src.matrix.pill_formatter import _make_pill

        result = _make_pill("@user:example.com", "O'Brien & Co")
        assert "&amp;" in result
        assert '<a href="https://matrix.to/#/@user:example.com">' in result
        # Should not have raw & or unescaped quotes
        assert "& Co" not in result or "&amp; Co" in result

    def test_mxid_in_href(self):
        from src.matrix.pill_formatter import _make_pill

        mxid = "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca"
        result = _make_pill(mxid, "Meridian")
        assert f'href="https://matrix.to/#/{mxid}"' in result
        assert ">Meridian</a>" in result


# =============================================================================
# Tests for _replace_outside_code_blocks()
# =============================================================================


class TestReplaceOutsideCodeBlocks:
    """Tests for code-block-aware text replacement."""

    def test_replaces_in_regular_text(self):
        from src.matrix.pill_formatter import _replace_outside_code_blocks

        result = _replace_outside_code_blocks(
            "<p>Hello @Meridian</p>", "@Meridian", "PILL"
        )
        assert result == "<p>Hello PILL</p>"

    def test_skips_code_tag(self):
        from src.matrix.pill_formatter import _replace_outside_code_blocks

        result = _replace_outside_code_blocks(
            "<p>Hello</p><code>@Meridian</code>", "@Meridian", "PILL"
        )
        assert "<code>@Meridian</code>" in result
        assert "PILL" not in result

    def test_skips_pre_tag(self):
        from src.matrix.pill_formatter import _replace_outside_code_blocks

        result = _replace_outside_code_blocks(
            "<pre>@Meridian</pre><p>@Meridian</p>",
            "@Meridian",
            "PILL",
        )
        assert "<pre>@Meridian</pre>" in result
        assert "<p>PILL</p>" in result

    def test_mixed_code_and_text(self):
        from src.matrix.pill_formatter import _replace_outside_code_blocks

        result = _replace_outside_code_blocks(
            "<p>@Meridian said <code>@Meridian</code> is great @Meridian</p>",
            "@Meridian",
            "PILL",
        )
        assert result == "<p>PILL said <code>@Meridian</code> is great PILL</p>"

    def test_nested_pre_code(self):
        from src.matrix.pill_formatter import _replace_outside_code_blocks

        result = _replace_outside_code_blocks(
            "<p>@Meridian</p><pre><code>@Meridian\n</code></pre>",
            "@Meridian",
            "PILL",
        )
        assert "<p>PILL</p>" in result
        # Content inside pre>code should be preserved
        assert "@Meridian" in result

    def test_no_code_blocks(self):
        from src.matrix.pill_formatter import _replace_outside_code_blocks

        result = _replace_outside_code_blocks(
            "<p>@Meridian and @Meridian</p>",
            "@Meridian",
            "PILL",
        )
        assert result == "<p>PILL and PILL</p>"


# =============================================================================
# Tests for _resolve_mentions()
# =============================================================================


class TestResolveMentions:
    """Tests for mention extraction and resolution."""

    def test_resolve_friendly_name(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        results = _resolve_mentions("Hey @Meridian, check this")
        assert len(results) == 1
        matched_text, mxid, display_name = results[0]
        assert matched_text == "@Meridian"
        assert "agent_597b5756" in mxid
        assert display_name == "Meridian"

    def test_resolve_full_mxid(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        mxid = "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca"
        results = _resolve_mentions(f"Hello {mxid}")
        assert len(results) == 1
        assert results[0][1] == mxid
        assert results[0][2] == "Meridian"

    def test_resolve_opencode_mxid(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        oc_mxid = "@oc_matrix_synapse_deployment_v2:matrix.oculair.ca"
        results = _resolve_mentions(f"Check with {oc_mxid}")
        assert len(results) == 1
        assert results[0][1] == oc_mxid
        # OC MXIDs use the MXID itself as display name
        assert results[0][2] == oc_mxid

    def test_skip_email_pattern(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        results = _resolve_mentions("Contact user@Meridian.com")
        # Email-like patterns should be skipped
        assert len(results) == 0

    def test_skip_unknown_names(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        results = _resolve_mentions("@NonExistent please help")
        assert len(results) == 0

    def test_mxid_takes_priority_over_friendly(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        mxid = "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca"
        results = _resolve_mentions(f"{mxid} and @Meridian")
        # MXID match resolves first; @Meridian should also resolve (different matched_text)
        assert len(results) == 2
        matched_texts = {r[0] for r in results}
        assert mxid in matched_texts
        assert "@Meridian" in matched_texts

    def test_friendly_inside_mxid_skipped(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        # The @agent part of a full MXID should not match as a friendly mention
        mxid = "@agent_bmo_1234_5678:matrix.oculair.ca"
        results = _resolve_mentions(f"Hello {mxid}")
        assert len(results) == 1
        assert results[0][0] == mxid  # Should be the full MXID match, not @agent

    def test_multiple_different_mentions(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        results = _resolve_mentions("@Meridian and @BMO please help")
        assert len(results) == 2
        names = {r[2] for r in results}
        assert "Meridian" in names
        assert "BMO" in names

    def test_duplicate_mentions_deduplicated(self, mock_mapping_service):
        from src.matrix.pill_formatter import _resolve_mentions

        results = _resolve_mentions("@Meridian and @Meridian again")
        # Same matched_text should appear only once
        assert len(results) == 1


# =============================================================================
# Tests for extract_and_convert_pills()
# =============================================================================


class TestExtractAndConvertPills:
    """Tests for the full pill conversion pipeline."""

    def test_friendly_name_pill(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "Hey @Meridian, check this",
            "<p>Hey @Meridian, check this</p>",
        )

        assert "https://matrix.to/#/@agent_597b5756" in html
        assert ">Meridian</a>" in html
        assert "@Meridian" not in html.replace("matrix.to/#/@agent", "")  # Original text replaced
        assert len(mxids) == 1
        assert mxids[0] == "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca"

    def test_mxid_pill(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        mxid = "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca"
        html, mxids = extract_and_convert_pills(
            f"Hello {mxid}",
            f"<p>Hello {mxid}</p>",
        )

        assert f'href="https://matrix.to/#/{mxid}"' in html
        assert ">Meridian</a>" in html
        assert mxid in mxids

    def test_opencode_mxid_pill(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        oc_mxid = "@oc_matrix_synapse_deployment_v2:matrix.oculair.ca"
        html, mxids = extract_and_convert_pills(
            f"Check with {oc_mxid}",
            f"<p>Check with {oc_mxid}</p>",
        )

        assert f'href="https://matrix.to/#/{oc_mxid}"' in html
        assert oc_mxid in mxids

    def test_no_mentions_returns_original(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        original_html = "<p>No mentions here</p>"
        html, mxids = extract_and_convert_pills("No mentions here", original_html)

        assert html == original_html
        assert mxids == []

    def test_empty_text_returns_html_body(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills("", "<p></p>")
        assert html == "<p></p>"
        assert mxids == []

    def test_empty_text_no_html_returns_empty(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills("", None)
        assert html == ""
        assert mxids == []

    def test_code_block_skipped(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "@Meridian in code: `@Meridian`",
            "<p>@Meridian in code: <code>@Meridian</code></p>",
        )

        # First @Meridian (outside code) should be converted
        assert ">Meridian</a>" in html
        # Second @Meridian (inside code) should NOT be converted
        assert "<code>@Meridian</code>" in html
        assert len(mxids) == 1

    def test_pre_block_skipped(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "@Meridian\n```\n@Meridian\n```",
            '<p>@Meridian</p>\n<pre><code>@Meridian\n</code></pre>',
        )

        # Outside pre block should be converted
        assert "matrix.to" in html
        # Inside pre block should not
        assert "<pre><code>@Meridian" in html
        assert len(mxids) == 1

    def test_multiple_mentions(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "@Meridian and @BMO please help",
            "<p>@Meridian and @BMO please help</p>",
        )

        assert ">Meridian</a>" in html
        assert ">BMO</a>" in html
        assert len(mxids) == 2

    def test_duplicate_mentions_mxids_deduplicated(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "@Meridian and @Meridian again",
            "<p>@Meridian and @Meridian again</p>",
        )

        # Both occurrences should be pills (str.replace gets both)
        assert html.count("matrix.to") == 2
        # MXIDs should be deduplicated
        assert len(mxids) == 1

    def test_unresolvable_mention_left_as_text(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "@NonExistent and @Meridian",
            "<p>@NonExistent and @Meridian</p>",
        )

        assert "@NonExistent" in html
        assert ">Meridian</a>" in html
        assert len(mxids) == 1

    def test_no_html_body_generates_html(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills("Hey @Meridian", None)

        assert "matrix.to" in html
        assert ">Meridian</a>" in html
        assert len(mxids) == 1
        # Original @Meridian text should be replaced
        assert "Hey " in html

    def test_self_mention_gets_pill(self, mock_mapping_service):
        """Self-mentions should still get pills (agent can mention itself)."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "I am @Meridian",
            "<p>I am @Meridian</p>",
        )

        assert ">Meridian</a>" in html
        assert len(mxids) == 1

    def test_email_not_converted(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "Contact user@Meridian.com",
            "<p>Contact user@Meridian.com</p>",
        )

        assert mxids == []
        assert "matrix.to" not in html

    def test_html_special_chars_in_display_name(self, mock_mapping_service):
        """Display names with HTML special characters should be escaped in pills."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        # Use the full MXID of the agent with HTML-special chars in name
        mxid = "@agent_test_html:matrix.oculair.ca"
        html, mxids = extract_and_convert_pills(
            f"Ask {mxid}",
            f"<p>Ask {mxid}</p>",
        )

        assert len(mxids) == 1
        # Display name "O'Brien & Co" should be HTML-escaped
        assert "&amp;" in html  # & should be escaped
        assert f'href="https://matrix.to/#/{mxid}"' in html

    def test_plain_text_no_mentions_returns_plain(self, mock_mapping_service):
        """When no html_body and no mentions, return plain text."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills("Just plain text", None)
        assert html == "Just plain text"
        assert mxids == []

    def test_mention_at_start_of_message(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "@Meridian check this",
            "<p>@Meridian check this</p>",
        )

        assert ">Meridian</a>" in html
        assert len(mxids) == 1

    def test_mention_at_end_of_message(self, mock_mapping_service):
        from src.matrix.pill_formatter import extract_and_convert_pills

        html, mxids = extract_and_convert_pills(
            "Check this @Meridian",
            "<p>Check this @Meridian</p>",
        )

        assert ">Meridian</a>" in html
        assert len(mxids) == 1
