from unittest.mock import patch

import pytest

from src.matrix.agent_actions import _build_message_content
from src.matrix.formatter import (
    format_inter_agent_envelope,
    format_message_envelope,
    format_opencode_envelope,
    format_reaction_route_envelope,
)
from src.matrix.pill_formatter import extract_and_convert_pills


FIXED_TS = 1709294400000


@pytest.fixture
def mock_pill_mapping_service():
    mappings = {
        "Meridian": {
            "agent_id": "agent-1",
            "agent_name": "Meridian",
            "matrix_user_id": "@agent_meridian:matrix.oculair.ca",
        },
        "BMO": {
            "agent_id": "agent-2",
            "agent_name": "BMO",
            "matrix_user_id": "@agent_bmo:matrix.oculair.ca",
        },
    }

    def by_user(mxid):
        for value in mappings.values():
            if value["matrix_user_id"] == mxid:
                return value
        return None

    def by_name(name, fuzzy=True):
        needle = (name or "").lower()
        for value in mappings.values():
            candidate = value["agent_name"].lower()
            if needle == candidate:
                return value
            if fuzzy and needle in candidate:
                return value
        return None

    with patch("src.matrix.pill_formatter.get_mapping_by_matrix_user", side_effect=by_user), patch(
        "src.matrix.pill_formatter.get_mapping_by_agent_name", side_effect=by_name
    ):
        yield


@pytest.mark.parametrize(
    "name,renderer",
    [
        (
            "group_basic",
            lambda: format_message_envelope(
                "Matrix",
                "!room:matrix.oculair.ca",
                "$event1",
                "@alice:matrix.oculair.ca",
                "Alice",
                FIXED_TS,
                "Hello team",
                is_group=True,
                group_name="Ops Room",
                is_mentioned=True,
            ),
        ),
        (
            "group_with_reply",
            lambda: format_message_envelope(
                "Matrix",
                "!room:matrix.oculair.ca",
                "$event2",
                "@alice:matrix.oculair.ca",
                "Alice",
                FIXED_TS,
                "Following up on your note",
                is_group=True,
                group_name="Ops Room",
                reply_to_event_id="$orig1",
                reply_to_sender="@bob:matrix.oculair.ca",
            ),
        ),
        (
            "inter_agent",
            lambda: format_inter_agent_envelope(
                "Meridian",
                "agent-xyz",
                "Escalate this to infra.",
                "!room:matrix.oculair.ca",
                "$event3",
                FIXED_TS,
                reply_to_event_id="$orig2",
                reply_to_sender="@charlie:matrix.oculair.ca",
            ),
        ),
        (
            "opencode",
            lambda: format_opencode_envelope(
                "@oc_matrix_synapse_deployment_v2:matrix.oculair.ca",
                "Please run tests and summarize failures.",
                "!room:matrix.oculair.ca",
                "$event4",
                FIXED_TS,
            ),
        ),
        (
            "reaction_routing",
            lambda: format_reaction_route_envelope(
                reactor_mxid="@alice:matrix.oculair.ca",
                emoji="✅",
                target_agent_name="Meridian",
                target_agent_id="agent-xyz",
                chat_id="!room:matrix.oculair.ca",
                reaction_event_id="$reaction",
                reacted_event_id="$original",
                timestamp=FIXED_TS,
                original_sender="@bob:matrix.oculair.ca",
                original_body="Can you summarize this thread and propose next steps?",
            ),
        ),
    ],
)
def test_envelope_rendering_snapshots(name, renderer, snapshot):
    assert renderer() == snapshot


@pytest.mark.parametrize(
    "name,plain_text,html_body",
    [
        (
            "friendly_mentions",
            "Hey @Meridian and @BMO",
            "<p>Hey @Meridian and @BMO</p>",
        ),
        (
            "mxid_mention",
            "Hello @agent_meridian:matrix.oculair.ca",
            "<p>Hello @agent_meridian:matrix.oculair.ca</p>",
        ),
        (
            "code_block_protection",
            "Use @Meridian in text but not in code",
            "<p>Use @Meridian in text</p><pre><code>@Meridian</code></pre>",
        ),
    ],
)
def test_pill_html_snapshots(name, plain_text, html_body, snapshot, mock_pill_mapping_service):
    rendered = extract_and_convert_pills(plain_text, html_body)
    assert rendered == snapshot


@pytest.mark.parametrize(
    "name,message,reply_to_event_id,reply_to_sender,reply_to_body,room_id",
    [
        ("plain_single_line", "Quick status update", None, None, None, None),
        (
            "markdown_with_mention",
            "**Update**: check @Meridian\n\n- item 1\n- item 2",
            None,
            None,
            None,
            "!room:matrix.oculair.ca",
        ),
        (
            "reply_with_quote",
            "Acknowledged ✅",
            "$orig-reply",
            "@alice:matrix.oculair.ca",
            "Please confirm deployment window",
            "!room:matrix.oculair.ca",
        ),
    ],
)
def test_agent_actions_rendering_snapshots(
    name,
    message,
    reply_to_event_id,
    reply_to_sender,
    reply_to_body,
    room_id,
    snapshot,
    mock_pill_mapping_service,
):
    payload = _build_message_content(
        message,
        reply_to_event_id=reply_to_event_id,
        reply_to_sender=reply_to_sender,
        reply_to_body=reply_to_body,
        room_id=room_id,
    )
    assert payload == snapshot
