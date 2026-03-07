"""
Unit tests for portal passive/active message routing.

Tests verify that:
1. Passive mode: contact messages → Letta (via gateway), response discarded, nothing sent to room
2. Active mode: @mention + mention_enabled → agent responds in room
3. Admin bypass: admin can invoke agent regardless of mention_enabled flag
4. Contact blocked: @mention ignored when mention_enabled=false (non-admin)
5. format_portal_contact_envelope() produces correct system-reminder envelope
"""

import pytest
import asyncio
import os
from unittest.mock import AsyncMock, Mock, MagicMock, patch, PropertyMock
from dataclasses import dataclass


# =============================================================================
# Constants
# =============================================================================

AGENT_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"
AGENT_NAME = "Meridian"
AGENT_MXID = "@agent_597b5756_2915_4560_ba6b_91005f085166:matrix.oculair.ca"
PORTAL_ROOM_ID = "!portal_holly:matrix.oculair.ca"
PORTAL_ROOM_NAME = "Holly (WhatsApp)"
CONTACT_MXID = "@whatsappbot:matrix.oculair.ca"
ADMIN_MXID = "@admin:matrix.oculair.ca"
NON_ADMIN_MXID = "@regularuser:matrix.oculair.ca"
EVENT_ID = "$test_event_123"


# =============================================================================
# Helpers
# =============================================================================


def _make_room(room_id=PORTAL_ROOM_ID, display_name=PORTAL_ROOM_NAME):
    """Create a mock Matrix room."""
    room = Mock()
    room.room_id = room_id
    room.display_name = display_name
    return room


def _make_event(
    sender=CONTACT_MXID,
    body="Hey, are you free for dinner tonight?",
    event_id=EVENT_ID,
    formatted_body=None,
):
    """Create a mock RoomMessageText event."""
    event = Mock()
    event.sender = sender
    event.body = body
    event.event_id = event_id
    source_content = {"msgtype": "m.text", "body": body}
    if formatted_body is not None:
        source_content["formatted_body"] = formatted_body
        source_content["format"] = "org.matrix.custom.html"
    event.source = {
        "type": "m.room.message",
        "sender": sender,
        "event_id": event_id,
        "origin_server_ts": 1704067200000,
        "content": source_content,
    }
    return event


def _make_portal_link(mention_enabled=False, agent_id=AGENT_ID):
    """Create a portal link dict matching the DB schema."""
    return {
        "agent_id": agent_id,
        "room_id": PORTAL_ROOM_ID,
        "enabled": True,
        "relay_mode": True,
        "mention_enabled": mention_enabled,
    }


def _make_agent_mapping(
    agent_id=AGENT_ID,
    agent_name=AGENT_NAME,
    matrix_user_id=AGENT_MXID,
):
    """Create an agent mapping dict matching the DB schema."""
    return {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "matrix_user_id": matrix_user_id,
        "room_id": "!meridian_room:matrix.oculair.ca",
        "room_created": True,
    }


@dataclass
class MockConfig:
    homeserver_url: str = "http://test-synapse:8008"
    username: str = "@test:matrix.test"
    password: str = "test_password"
    room_id: str = "!testroom:matrix.test"
    letta_api_url: str = "http://test-letta:8283"
    letta_token: str = "test_token"
    letta_agent_id: str = "test-agent-id"
    log_level: str = "INFO"
    matrix_api_url: str = "http://test-matrix-api:8000"


# =============================================================================
# Test: format_portal_contact_envelope
# =============================================================================


class TestFormatPortalContactEnvelope:
    """Tests for the system-reminder envelope formatter."""

    def test_basic_envelope_structure(self):
        from src.matrix.formatter import format_portal_contact_envelope

        result = format_portal_contact_envelope(
            contact_sender=CONTACT_MXID,
            room_name=PORTAL_ROOM_NAME,
            chat_id=PORTAL_ROOM_ID,
            message_id=EVENT_ID,
            timestamp=1704067200000,
            text="Hello!",
        )

        assert "<system-reminder>" in result
        assert "</system-reminder>" in result
        assert "Hello!" in result

    def test_envelope_contains_metadata(self):
        from src.matrix.formatter import format_portal_contact_envelope

        result = format_portal_contact_envelope(
            contact_sender=CONTACT_MXID,
            room_name=PORTAL_ROOM_NAME,
            chat_id=PORTAL_ROOM_ID,
            message_id=EVENT_ID,
            timestamp=1704067200000,
            text="Test message",
        )

        assert "Portal (bridged messaging)" in result
        assert PORTAL_ROOM_ID in result
        assert EVENT_ID in result
        assert PORTAL_ROOM_NAME in result

    def test_envelope_marks_not_from_user(self):
        from src.matrix.formatter import format_portal_contact_envelope

        result = format_portal_contact_envelope(
            contact_sender=CONTACT_MXID,
            room_name=PORTAL_ROOM_NAME,
            chat_id=PORTAL_ROOM_ID,
            message_id=EVENT_ID,
            timestamp=1704067200000,
            text="Test",
        )

        assert "NOT from the user" in result
        assert "Do NOT reply" in result
        assert "Observe passively" in result

    def test_envelope_contact_sender_extracted(self):
        from src.matrix.formatter import format_portal_contact_envelope

        result = format_portal_contact_envelope(
            contact_sender="@whatsappbot:matrix.oculair.ca",
            room_name=PORTAL_ROOM_NAME,
            chat_id=PORTAL_ROOM_ID,
            message_id=EVENT_ID,
            timestamp=1704067200000,
            text="Hi",
        )

        # Should extract localpart from MXID
        assert "whatsappbot" in result

    def test_envelope_empty_text(self):
        from src.matrix.formatter import format_portal_contact_envelope

        result = format_portal_contact_envelope(
            contact_sender=CONTACT_MXID,
            room_name=PORTAL_ROOM_NAME,
            chat_id=PORTAL_ROOM_ID,
            message_id=None,
            timestamp=None,
            text="",
        )

        # Should still produce valid envelope without trailing message
        assert "<system-reminder>" in result
        assert "</system-reminder>" in result

    def test_envelope_with_reporting_instruction(self):
        from src.matrix.formatter import format_portal_contact_envelope

        result = format_portal_contact_envelope(
            contact_sender=CONTACT_MXID,
            room_name=PORTAL_ROOM_NAME,
            chat_id=PORTAL_ROOM_ID,
            message_id=EVENT_ID,
            timestamp=1704067200000,
            text="Need action on project X",
        )

        assert "Reporting" in result or "attention" in result


# =============================================================================
# Test: _handle_passive_portal_message
# =============================================================================


class TestHandlePassivePortalMessage:
    """Tests for the passive portal message handler.

    Note: _handle_passive_portal_message uses lazy imports inside the function body:
    - from src.matrix.letta_bridge import _get_gateway_client
    - from src.letta.gateway_stream_reader import collect_via_gateway
    So we patch at the SOURCE module, not at src.matrix.client.
    """

    @pytest.mark.asyncio
    async def test_passive_sends_to_letta_via_gateway(self):
        """Verify that passive mode sends the envelope to Letta."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event()
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        mock_collect = AsyncMock(return_value="Agent response text (should be discarded)")

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", mock_collect):

            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Hey, are you free for dinner tonight?",
            )

            # Give the fire-and-forget task a moment to run
            await asyncio.sleep(0.1)

            # Verify collect_via_gateway was called with agent_id and an envelope
            mock_collect.assert_called_once()
            call_kwargs = mock_collect.call_args
            assert call_kwargs.kwargs.get("agent_id") == AGENT_ID

    @pytest.mark.asyncio
    async def test_passive_does_not_call_send_as_agent(self):
        """Verify that passive mode does NOT send any message to the Matrix room."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event()
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        mock_collect = AsyncMock(return_value="Some agent response")

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", mock_collect), \
             patch("src.matrix.agent_actions.send_as_agent_with_event_id") as mock_send, \
             patch("src.matrix.agent_actions.send_as_agent") as mock_send_plain:

            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Hello from contact",
            )

            await asyncio.sleep(0.1)

            # Neither send function should be called
            mock_send.assert_not_called()
            mock_send_plain.assert_not_called()

    @pytest.mark.asyncio
    async def test_passive_logs_portal_passive_prefix(self):
        """Verify that passive handler uses [PORTAL-PASSIVE] log prefix."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event()
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        mock_collect = AsyncMock(return_value="response")

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", mock_collect):

            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Contact message",
            )

            await asyncio.sleep(0.1)

            # Check that at least one info log contains [PORTAL-PASSIVE]
            info_calls = [str(c) for c in logger.info.call_args_list]
            assert any("[PORTAL-PASSIVE]" in c for c in info_calls), \
                f"Expected [PORTAL-PASSIVE] in log calls: {info_calls}"

    @pytest.mark.asyncio
    async def test_passive_gateway_error_is_noncritical(self):
        """Verify that gateway errors in passive mode don't raise."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event()
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        mock_collect = AsyncMock(side_effect=Exception("Gateway connection failed"))

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", mock_collect):

            # Should not raise
            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Message that causes error",
            )

            await asyncio.sleep(0.1)

            # Should log warning, not crash
            warning_calls = [str(c) for c in logger.warning.call_args_list]
            assert any("[PORTAL-PASSIVE]" in c for c in warning_calls), \
                f"Expected [PORTAL-PASSIVE] warning log: {warning_calls}"

    @pytest.mark.asyncio
    async def test_passive_formats_envelope_correctly(self):
        """Verify the envelope passed to gateway contains system-reminder."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event(body="Dinner at 7pm?")
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        captured_messages = []

        async def capture_collect(**kwargs):
            captured_messages.append(kwargs.get("message", ""))
            return "discarded"

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", AsyncMock(side_effect=capture_collect)):

            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Dinner at 7pm?",
            )

            await asyncio.sleep(0.1)

            assert len(captured_messages) == 1
            envelope = captured_messages[0]
            assert "<system-reminder>" in envelope
            assert "Dinner at 7pm?" in envelope
            assert "Do NOT reply" in envelope


# =============================================================================
# Test: Portal Routing in message_callback
# =============================================================================


class TestPortalRoutingMessageCallback:
    """Tests for the portal routing logic inside message_callback.

    These tests verify the routing DECISION logic extracted from message_callback,
    not the full callback (which requires nio.RoomMessageText isinstance checks).
    """

    @pytest.mark.asyncio
    async def test_passive_route_contact_no_mention(self):
        """Verify contact message without @mention routes to passive handler."""
        portal_link = _make_portal_link(mention_enabled=False)
        message_text = "Just saying hi"
        sender = CONTACT_MXID
        agent_name = AGENT_NAME

        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or portal_link.get("mention_enabled", False)

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
        )

        assert not agent_mentioned, "Contact message without @mention should not trigger active mode"

    @pytest.mark.asyncio
    async def test_active_route_with_mention_enabled(self):
        """Verify @mention + mention_enabled=True routes to active handler."""
        portal_link = _make_portal_link(mention_enabled=True)
        message_text = "@Meridian can you check my calendar?"
        sender = CONTACT_MXID
        agent_name = AGENT_NAME

        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or portal_link.get("mention_enabled", True)

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
        )

        assert agent_mentioned, "@Meridian with mention_enabled=True should trigger active mode"

    @pytest.mark.asyncio
    async def test_contact_mention_blocked_without_flag(self):
        """Contact @mention with mention_enabled=False → passive mode (blocked)."""
        portal_link = _make_portal_link(mention_enabled=False)
        message_text = "@Meridian what's up?"
        sender = CONTACT_MXID
        agent_name = AGENT_NAME

        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or portal_link.get("mention_enabled", False)

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
        )

        assert not agent_mentioned, "Contact @mention with mention_enabled=False should be blocked"

    @pytest.mark.asyncio
    async def test_admin_bypass_mention_disabled(self):
        """Admin can invoke agent even with mention_enabled=False."""
        portal_link = _make_portal_link(mention_enabled=False)
        message_text = "@Meridian add a calendar entry for tomorrow"
        sender = ADMIN_MXID
        agent_name = AGENT_NAME

        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or portal_link.get("mention_enabled", False)

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
        )

        assert agent_mentioned, "Admin should be able to invoke agent regardless of mention_enabled"

    @pytest.mark.asyncio
    async def test_admin_without_mention_still_passive(self):
        """Admin message without @mention → passive mode (admin doesn't auto-activate)."""
        portal_link = _make_portal_link(mention_enabled=False)
        message_text = "Just checking in on this thread"
        sender = ADMIN_MXID
        agent_name = AGENT_NAME

        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or portal_link.get("mention_enabled", False)

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
        )

        assert not agent_mentioned, "Admin message without @mention should still go passive"

    @pytest.mark.asyncio
    async def test_mention_detection_case_insensitive(self):
        """@mention detection should be case-insensitive."""
        portal_link = _make_portal_link(mention_enabled=True)
        agent_name = AGENT_NAME  # "Meridian"

        for msg in ["@meridian help", "@MERIDIAN help", "@Meridian help", "hey meridian help"]:
            mention_allowed = True  # mention_enabled=True

            agent_mentioned = mention_allowed and agent_name and (
                f"@{agent_name.lower()}" in msg.lower()
                or agent_name.lower() in msg.lower()
            )

            assert agent_mentioned, f"Should detect mention in: {msg}"

    @pytest.mark.asyncio
    async def test_mention_detection_in_formatted_body(self):
        """@mention in formatted_body (HTML pills) should be detected."""
        portal_link = _make_portal_link(mention_enabled=True)
        message_text = "help me"
        agent_name = AGENT_NAME
        formatted_body = '<a href="https://matrix.to/#/@agent:matrix.oculair.ca">Meridian</a> help me'

        is_admin = False
        mention_allowed = True  # mention_enabled=True

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
            or agent_name.lower() in formatted_body.lower()
        )

        assert agent_mentioned, "Should detect agent name in formatted_body HTML"


# =============================================================================
# Test: Portal Link mention_enabled flag
# =============================================================================


class TestMentionEnabledFlag:
    """Tests for the mention_enabled column on portal_agent_links."""

    def test_portal_link_default_mention_disabled(self):
        """Default portal link should have mention_enabled=False."""
        link = _make_portal_link()
        assert link["mention_enabled"] is False

    def test_portal_link_mention_enabled_true(self):
        """Portal link with mention_enabled=True."""
        link = _make_portal_link(mention_enabled=True)
        assert link["mention_enabled"] is True

    def test_mention_allowed_logic_admin(self):
        """Admin is always mention_allowed regardless of flag."""
        link = _make_portal_link(mention_enabled=False)
        sender = ADMIN_MXID
        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or link.get("mention_enabled", False)
        assert mention_allowed is True

    def test_mention_allowed_logic_contact_disabled(self):
        """Contact with mention_enabled=False → not allowed."""
        link = _make_portal_link(mention_enabled=False)
        sender = CONTACT_MXID
        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or link.get("mention_enabled", False)
        assert mention_allowed is False

    def test_mention_allowed_logic_contact_enabled(self):
        """Contact with mention_enabled=True → allowed."""
        link = _make_portal_link(mention_enabled=True)
        sender = CONTACT_MXID
        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or link.get("mention_enabled", False)
        assert mention_allowed is True


# =============================================================================
# Test: End-to-end passive flow (integration-style with mocks)
# =============================================================================


class TestPassiveFlowEndToEnd:
    """Integration-style tests that exercise _handle_passive_portal_message
    with fully mocked dependencies to verify the complete flow.
    """

    @pytest.mark.asyncio
    async def test_full_passive_flow_message_to_letta_response_discarded(self):
        """Full flow: contact message → envelope → gateway → response discarded."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event(
            sender=CONTACT_MXID,
            body="Can we reschedule the meeting to 3pm?",
        )
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        collected_calls = []

        async def track_collect(**kwargs):
            collected_calls.append(kwargs)
            return "I'll note that the meeting should be rescheduled to 3pm."

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", AsyncMock(side_effect=track_collect)), \
             patch("src.matrix.agent_actions.send_as_agent", new_callable=AsyncMock) as mock_send, \
             patch("src.matrix.agent_actions.send_as_agent_with_event_id", new_callable=AsyncMock) as mock_send_eid:

            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Can we reschedule the meeting to 3pm?",
            )

            await asyncio.sleep(0.15)

            # Gateway was called
            assert len(collected_calls) == 1

            # Message envelope contains the contact message
            envelope = collected_calls[0].get("message", "")
            assert "reschedule" in envelope.lower() or "3pm" in envelope.lower()

            # Source contains portal channel info
            source = collected_calls[0].get("source", {})
            assert source.get("channel") == "portal"
            assert source.get("chatId") == PORTAL_ROOM_ID

            # No message sent to Matrix room
            mock_send.assert_not_called()
            mock_send_eid.assert_not_called()

    @pytest.mark.asyncio
    async def test_passive_preserves_agent_id_in_gateway_call(self):
        """Verify the correct agent_id is passed to the gateway."""
        from src.matrix.client import _handle_passive_portal_message

        room = _make_room()
        event = _make_event()
        config = MockConfig()
        logger = Mock()
        logger.info = Mock()
        logger.warning = Mock()

        mock_gateway = AsyncMock()
        collected_kwargs = []

        async def capture(**kwargs):
            collected_kwargs.append(kwargs)
            return None

        with patch("src.matrix.letta_bridge._get_gateway_client", new_callable=AsyncMock, return_value=mock_gateway), \
             patch("src.letta.gateway_stream_reader.collect_via_gateway", AsyncMock(side_effect=capture)):

            await _handle_passive_portal_message(
                room, event, config, logger,
                room_agent_id=AGENT_ID,
                room_agent_name=AGENT_NAME,
                message_text="Test",
            )

            await asyncio.sleep(0.1)

            assert len(collected_kwargs) == 1
            assert collected_kwargs[0]["agent_id"] == AGENT_ID


# =============================================================================
# Test: Routing decision matrix
# =============================================================================


class TestPortalRoutingDecisionMatrix:
    """Exhaustive test of every combination of sender type × mention × flag."""

    @pytest.mark.parametrize(
        "sender,mention_enabled,has_mention,expected_active",
        [
            # Contact scenarios
            (CONTACT_MXID, False, False, False),   # Contact, disabled, no mention → passive
            (CONTACT_MXID, False, True, False),     # Contact, disabled, mention → passive (blocked)
            (CONTACT_MXID, True, False, False),     # Contact, enabled, no mention → passive
            (CONTACT_MXID, True, True, True),       # Contact, enabled, mention → ACTIVE

            # Admin scenarios
            (ADMIN_MXID, False, False, False),      # Admin, disabled, no mention → passive
            (ADMIN_MXID, False, True, True),        # Admin, disabled, mention → ACTIVE (bypass)
            (ADMIN_MXID, True, False, False),       # Admin, enabled, no mention → passive
            (ADMIN_MXID, True, True, True),         # Admin, enabled, mention → ACTIVE
        ],
        ids=[
            "contact-disabled-no_mention",
            "contact-disabled-with_mention",
            "contact-enabled-no_mention",
            "contact-enabled-with_mention",
            "admin-disabled-no_mention",
            "admin-disabled-with_mention",
            "admin-enabled-no_mention",
            "admin-enabled-with_mention",
        ],
    )
    def test_routing_decision(self, sender, mention_enabled, has_mention, expected_active):
        """Parametric test covering all sender × flag × mention combinations."""
        portal_link = _make_portal_link(mention_enabled=mention_enabled)
        agent_name = AGENT_NAME
        message_text = f"@{agent_name} do something" if has_mention else "Regular message"

        is_admin = sender == ADMIN_MXID
        mention_allowed = is_admin or portal_link.get("mention_enabled", False)

        agent_mentioned = mention_allowed and agent_name and (
            f"@{agent_name.lower()}" in message_text.lower()
            or agent_name.lower() in message_text.lower()
        )

        assert agent_mentioned == expected_active, (
            f"sender={sender}, mention_enabled={mention_enabled}, "
            f"has_mention={has_mention} → expected_active={expected_active}, "
            f"got agent_mentioned={agent_mentioned}"
        )
