"""
Unit tests for 5 P2 bug fixes.

Covers:
  - bd-vw6c: TypingIndicatorManager.start() idempotency
  - bd-ix1i: HTML-escape reply metadata
  - bd-fxj0: Reject non-string mode in group_config.py
  - bd-lc4b: Don't persist --path override
  - bd-n87u: HTML-escape plain_text in pill_formatter
"""

import asyncio
import html
import logging
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, Optional

# =============================================================================
# Fix 1: bd-vw6c — TypingIndicatorManager.start() idempotency
# =============================================================================


class TestTypingIndicatorManagerIdempotency:
    """Tests for TypingIndicatorManager.start() idempotency fix."""

    @pytest.mark.asyncio
    async def test_start_twice_stops_first_task(self):
        """Calling start() twice should stop the first task and start a new one."""
        from src.matrix.agent_actions import TypingIndicatorManager
        from src.matrix.config import Config

        # Create mock config
        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        manager = TypingIndicatorManager(room_id, config, logger)

        # Mock _get_agent_typing_context
        mock_ctx = {"typing_url": "http://test/typing", "token": "test_token"}

        with patch(
            "src.matrix.agent_actions._get_agent_typing_context",
            new_callable=AsyncMock,
            return_value=mock_ctx,
        ):
            # Start first time
            await manager.start()
            first_task = manager._typing_task
            assert first_task is not None
            assert not first_task.done()

            # Start second time — should stop first and create new
            await manager.start()
            second_task = manager._typing_task
            assert second_task is not None
            assert second_task != first_task
            # First task should be cancelled
            assert first_task.cancelled() or first_task.done()

            # Cleanup
            await manager.stop()

    @pytest.mark.asyncio
    async def test_start_when_no_task_exists(self):
        """Calling start() when no task exists should work normally."""
        from src.matrix.agent_actions import TypingIndicatorManager
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        manager = TypingIndicatorManager(room_id, config, logger)
        assert manager._typing_task is None

        mock_ctx = {"typing_url": "http://test/typing", "token": "test_token"}

        with patch(
            "src.matrix.agent_actions._get_agent_typing_context",
            new_callable=AsyncMock,
            return_value=mock_ctx,
        ):
            await manager.start()
            assert manager._typing_task is not None
            assert not manager._typing_task.done()

            await manager.stop()

    @pytest.mark.asyncio
    async def test_start_idempotency_no_leaked_tasks(self):
        """Multiple start() calls should not leak tasks."""
        from src.matrix.agent_actions import TypingIndicatorManager
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        manager = TypingIndicatorManager(room_id, config, logger)

        mock_ctx = {"typing_url": "http://test/typing", "token": "test_token"}

        with patch(
            "src.matrix.agent_actions._get_agent_typing_context",
            new_callable=AsyncMock,
            return_value=mock_ctx,
        ):
            # Call start() 3 times
            await manager.start()
            task1 = manager._typing_task

            await manager.start()
            task2 = manager._typing_task

            await manager.start()
            task3 = manager._typing_task

            # All should be different
            assert task1 != task2
            assert task2 != task3

            # Only the last one should be active
            assert not task3.done()

            await manager.stop()


# =============================================================================
# Fix 2: bd-ix1i — HTML-escape reply metadata
# =============================================================================


class TestHTMLEscapeReplyMetadata:
    """Tests for reply metadata safety.

    After the switch from rich-reply (mx-reply blockquote) to m.thread
    (MSC3440), reply_to_sender and reply_to_body are no longer embedded in
    formatted_body at all. This eliminates the XSS vector entirely — the
    tests now verify that malicious content never appears in any HTML output
    and that the m.thread structure is correct.
    """

    @pytest.mark.asyncio
    async def test_escape_sender_with_script_tag(self):
        """Malicious sender must not appear in any HTML output."""
        from src.matrix.agent_actions import send_as_agent_with_event_id
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        mock_mapping = {
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent:matrix.test",
        }

        malicious_sender = "<script>alert(1)</script>"

        with patch(
            "src.core.mapping_service.get_mapping_by_room_id",
            return_value=mock_mapping,
        ), patch(
            "src.matrix.agent_actions.get_agent_token",
            new_callable=AsyncMock,
            return_value="test_token",
        ), patch(
            "aiohttp.ClientSession.put",
            new_callable=AsyncMock,
        ) as mock_put:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$test"})
            mock_put.return_value.__aenter__.return_value = mock_response

            await send_as_agent_with_event_id(
                room_id,
                "Test message",
                config,
                logger,
                reply_to_event_id="$event123",
                reply_to_sender=malicious_sender,
                reply_to_body="Original message",
            )

            assert mock_put.called
            call_args = mock_put.call_args
            json_data = call_args.kwargs.get("json", {})

            # With m.thread, sender/body are never in formatted_body — no XSS vector
            formatted_body = json_data.get("formatted_body", "")
            assert "<script>" not in formatted_body

            # m.thread structure present
            relates_to = json_data.get("m.relates_to", {})
            assert relates_to.get("rel_type") == "m.thread"
            assert relates_to.get("event_id") == "$event123"

            # Sender tracked via m.mentions
            mentions = json_data.get("m.mentions", {})
            assert malicious_sender in mentions.get("user_ids", [])

    @pytest.mark.asyncio
    async def test_escape_body_with_img_onerror(self):
        """Malicious quoted body must not appear in any HTML output."""
        from src.matrix.agent_actions import send_as_agent_with_event_id
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        mock_mapping = {
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent:matrix.test",
        }

        malicious_body = "<img onerror=alert(1)>"

        with patch(
            "src.core.mapping_service.get_mapping_by_room_id",
            return_value=mock_mapping,
        ), patch(
            "src.matrix.agent_actions.get_agent_token",
            new_callable=AsyncMock,
            return_value="test_token",
        ), patch(
            "aiohttp.ClientSession.put",
            new_callable=AsyncMock,
        ) as mock_put:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$test"})
            mock_put.return_value.__aenter__.return_value = mock_response

            await send_as_agent_with_event_id(
                room_id,
                "Test message",
                config,
                logger,
                reply_to_event_id="$event123",
                reply_to_sender="User",
                reply_to_body=malicious_body,
            )

            assert mock_put.called
            call_args = mock_put.call_args
            json_data = call_args.kwargs.get("json", {})

            # With m.thread, quoted body is never in formatted_body
            formatted_body = json_data.get("formatted_body", "")
            assert "<img onerror" not in formatted_body

            # m.thread structure present
            relates_to = json_data.get("m.relates_to", {})
            assert relates_to.get("rel_type") == "m.thread"

    @pytest.mark.asyncio
    async def test_normal_reply_still_works(self):
        """Normal reply should use m.thread with m.mentions."""
        from src.matrix.agent_actions import send_as_agent_with_event_id
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        mock_mapping = {
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent:matrix.test",
        }

        with patch(
            "src.core.mapping_service.get_mapping_by_room_id",
            return_value=mock_mapping,
        ), patch(
            "src.matrix.agent_actions.get_agent_token",
            new_callable=AsyncMock,
            return_value="test_token",
        ), patch(
            "aiohttp.ClientSession.put",
            new_callable=AsyncMock,
        ) as mock_put:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$test"})
            mock_put.return_value.__aenter__.return_value = mock_response

            await send_as_agent_with_event_id(
                room_id,
                "Test message",
                config,
                logger,
                reply_to_event_id="$event123",
                reply_to_sender="Alice",
                reply_to_body="Hello world",
            )

            assert mock_put.called
            call_args = mock_put.call_args
            json_data = call_args.kwargs.get("json", {})

            # m.thread structure
            relates_to = json_data.get("m.relates_to", {})
            assert relates_to.get("rel_type") == "m.thread"
            assert relates_to.get("event_id") == "$event123"
            assert relates_to.get("is_falling_back") is True

            # Sender tracked via m.mentions
            mentions = json_data.get("m.mentions", {})
            assert "Alice" in mentions.get("user_ids", [])


# =============================================================================
# Fix 3: bd-fxj0 — Reject non-string mode in group_config.py
# =============================================================================


class TestParseModeMustBeString:
    """Tests for _parse_mode() type checking."""

    def test_parse_mode_rejects_int(self):
        """Passing an int to _parse_mode should raise ValueError."""
        from src.matrix.group_config import _parse_mode

        with pytest.raises(ValueError, match="must be a string"):
            _parse_mode(123)

    def test_parse_mode_rejects_none(self):
        """Passing None to _parse_mode should raise ValueError."""
        from src.matrix.group_config import _parse_mode

        with pytest.raises(ValueError, match="must be a string"):
            _parse_mode(None)

    def test_parse_mode_rejects_list(self):
        """Passing a list to _parse_mode should raise ValueError."""
        from src.matrix.group_config import _parse_mode

        with pytest.raises(ValueError, match="must be a string"):
            _parse_mode(["open"])

    def test_parse_mode_rejects_dict(self):
        """Passing a dict to _parse_mode should raise ValueError."""
        from src.matrix.group_config import _parse_mode

        with pytest.raises(ValueError, match="must be a string"):
            _parse_mode({"mode": "open"})

    def test_parse_mode_accepts_valid_string(self):
        """Valid string modes should be accepted."""
        from src.matrix.group_config import _parse_mode

        assert _parse_mode("open") == "open"
        assert _parse_mode("listen") == "listen"
        assert _parse_mode("mention-only") == "mention-only"
        assert _parse_mode("disabled") == "disabled"

    def test_parse_mode_case_insensitive(self):
        """Mode parsing should be case-insensitive."""
        from src.matrix.group_config import _parse_mode

        assert _parse_mode("OPEN") == "open"
        assert _parse_mode("Listen") == "listen"
        assert _parse_mode("MENTION-ONLY") == "mention-only"

    def test_parse_mode_rejects_invalid_string(self):
        """Invalid string modes should raise ValueError."""
        from src.matrix.group_config import _parse_mode

        with pytest.raises(ValueError, match="Invalid group mode"):
            _parse_mode("invalid_mode")


# =============================================================================
# Fix 4: bd-lc4b — Don't persist --path override
# =============================================================================


class TestLettaCodePathOverride:
    """Tests for resolve_letta_project_dir() not persisting override_path."""

    @pytest.mark.asyncio
    async def test_override_path_not_persisted(self):
        """Calling with override_path should return it but NOT persist."""
        from src.matrix.letta_code_service import resolve_letta_project_dir
        from src.matrix.config import Config

        config = Mock(spec=Config)
        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"
        agent_id = "agent-123"
        override_path = "/custom/path"

        with patch(
            "src.matrix.letta_code_service.update_letta_code_room_state"
        ) as mock_update, patch(
            "src.matrix.letta_code_service.get_letta_code_room_state",
            return_value={},
        ):
            result = await resolve_letta_project_dir(
                room_id, agent_id, config, logger, override_path=override_path
            )

            # Should return the override path
            assert result == override_path

            # Should NOT call update_letta_code_room_state
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_override_path_persists(self):
        """Calling without override_path should persist from API response."""
        from src.matrix.letta_code_service import resolve_letta_project_dir
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.letta_code_api_url = "http://test:8000"
        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"
        agent_id = "agent-123"

        mock_session_info = {"projectDir": "/api/project/path"}

        with patch(
            "src.matrix.letta_code_service.get_letta_code_room_state",
            return_value={},
        ), patch(
            "src.matrix.letta_code_service.call_letta_code_api",
            new_callable=AsyncMock,
            return_value=mock_session_info,
        ), patch(
            "src.matrix.letta_code_service.update_letta_code_room_state"
        ) as mock_update:
            result = await resolve_letta_project_dir(
                room_id, agent_id, config, logger
            )

            # Should return the API path
            assert result == "/api/project/path"

            # Should call update_letta_code_room_state to persist
            mock_update.assert_called_once()
            call_args = mock_update.call_args
            assert call_args[0][0] == room_id
            assert call_args[0][1].get("projectDir") == "/api/project/path"

    @pytest.mark.asyncio
    async def test_override_path_returns_immediately(self):
        """override_path should return without calling API."""
        from src.matrix.letta_code_service import resolve_letta_project_dir
        from src.matrix.config import Config

        config = Mock(spec=Config)
        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"
        agent_id = "agent-123"
        override_path = "/override"

        with patch(
            "src.matrix.letta_code_service.call_letta_code_api"
        ) as mock_api, patch(
            "src.matrix.letta_code_service.get_letta_code_room_state",
            return_value={},
        ):
            result = await resolve_letta_project_dir(
                room_id, agent_id, config, logger, override_path=override_path
            )

            # Should return override path
            assert result == override_path

            # Should NOT call API
            mock_api.assert_not_called()

    @pytest.mark.asyncio
    async def test_cached_path_returned_without_api_call(self):
        """If path is cached in room state, should return without API call."""
        from src.matrix.letta_code_service import resolve_letta_project_dir
        from src.matrix.config import Config

        config = Mock(spec=Config)
        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"
        agent_id = "agent-123"

        cached_state = {"projectDir": "/cached/path"}

        with patch(
            "src.matrix.letta_code_service.get_letta_code_room_state",
            return_value=cached_state,
        ), patch(
            "src.matrix.letta_code_service.call_letta_code_api"
        ) as mock_api:
            result = await resolve_letta_project_dir(
                room_id, agent_id, config, logger
            )

            # Should return cached path
            assert result == "/cached/path"

            # Should NOT call API
            mock_api.assert_not_called()


# =============================================================================
# Fix 5: bd-n87u — HTML-escape plain_text in pill_formatter
# =============================================================================


class TestPillFormatterHTMLEscape:
    """Tests for HTML-escaping plain_text in pill_formatter."""

    def test_plain_text_escaped_when_no_mentions(self):
        """plain_text with <script> should be escaped when no mentions found."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        plain_text = "Hello <script>alert(1)</script>"

        with patch(
            "src.matrix.pill_formatter._resolve_mentions", return_value=[]
        ):
            html_result, mxids = extract_and_convert_pills(plain_text)

            # Should be escaped
            assert html.escape(plain_text) in html_result
            assert "<script>" not in html_result
            assert mxids == []

    def test_plain_text_escaped_no_html_body_provided(self):
        """When html_body is None, plain_text should be escaped."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        plain_text = "Test <img onerror=x>"

        with patch(
            "src.matrix.pill_formatter._resolve_mentions", return_value=[]
        ):
            html_result, mxids = extract_and_convert_pills(plain_text, html_body=None)

            # Should be escaped
            assert html.escape(plain_text) in html_result
            assert "<img onerror" not in html_result

    def test_html_body_not_double_escaped(self):
        """When html_body is provided, it should be returned as-is."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        plain_text = "Hello"
        html_body = "<p>Already escaped &lt;script&gt;</p>"

        with patch(
            "src.matrix.pill_formatter._resolve_mentions", return_value=[]
        ):
            html_result, mxids = extract_and_convert_pills(plain_text, html_body)

            # Should return html_body as-is (no double-escaping)
            assert html_result == html_body
            assert mxids == []

    def test_normal_text_passes_through(self):
        """Normal text without HTML should pass through normally."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        plain_text = "Hello world"

        with patch(
            "src.matrix.pill_formatter._resolve_mentions", return_value=[]
        ):
            html_result, mxids = extract_and_convert_pills(plain_text)

            # Should be escaped (safe version)
            assert "Hello world" in html_result
            assert mxids == []

    def test_mentions_with_escaped_plain_text(self):
        """When mentions are found, plain_text should still be escaped."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        plain_text = "Hello @Agent <script>alert(1)</script>"

        # Mock mentions found
        mock_mentions = [("@Agent", "@agent:matrix.test", "Agent")]

        with patch(
            "src.matrix.pill_formatter._resolve_mentions", return_value=mock_mentions
        ):
            html_result, mxids = extract_and_convert_pills(plain_text)

            # The plain text part should be escaped
            assert html.escape("<script>alert(1)</script>") in html_result
            assert "<script>" not in html_result
            # Mention should be converted to pill
            assert "@agent:matrix.test" in html_result

    def test_empty_plain_text(self):
        """Empty plain_text should return empty HTML."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        html_result, mxids = extract_and_convert_pills("")

        assert html_result == ""
        assert mxids == []

    def test_plain_text_with_ampersand(self):
        """Ampersands should be properly escaped."""
        from src.matrix.pill_formatter import extract_and_convert_pills

        plain_text = "Tom & Jerry <script>"

        with patch(
            "src.matrix.pill_formatter._resolve_mentions", return_value=[]
        ):
            html_result, mxids = extract_and_convert_pills(plain_text)

            # Ampersand should be escaped
            assert "&amp;" in html_result
            assert "Tom & Jerry" not in html_result
            assert "<script>" not in html_result


# =============================================================================
# Integration Tests
# =============================================================================


class TestP2FixesIntegration:
    """Integration tests combining multiple fixes."""

    @pytest.mark.asyncio
    async def test_typing_manager_with_escaped_context(self):
        """TypingIndicatorManager should work with escaped context data."""
        from src.matrix.agent_actions import TypingIndicatorManager
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"

        manager = TypingIndicatorManager(room_id, config, logger)

        # Context with special characters (should be handled safely)
        mock_ctx = {
            "typing_url": "http://test/typing?user=%3Cscript%3E",
            "token": "test_token",
        }

        with patch(
            "src.matrix.agent_actions._get_agent_typing_context",
            new_callable=AsyncMock,
            return_value=mock_ctx,
        ):
            await manager.start()
            assert manager._typing_task is not None

            # Start again (idempotency test)
            await manager.start()
            assert manager._typing_task is not None

            await manager.stop()

    def test_group_config_with_escaped_mode(self):
        """GroupConfig should reject non-string modes even with escaping."""
        from src.matrix.group_config import _parse_config_dict

        # Valid config
        valid_config = {"mode": "open", "allowed_users": ["@user:matrix.test"]}
        result = _parse_config_dict(valid_config)
        assert result.mode == "open"

        # Invalid config with non-string mode
        invalid_config = {"mode": 123}
        with pytest.raises(ValueError):
            _parse_config_dict(invalid_config)

    @pytest.mark.asyncio
    async def test_reply_with_escaped_sender_and_path_override(self):
        """Reply with escaped sender should work with path override."""
        from src.matrix.agent_actions import send_as_agent_with_event_id
        from src.matrix.letta_code_service import resolve_letta_project_dir
        from src.matrix.config import Config

        config = Mock(spec=Config)
        config.homeserver_url = "http://test:8008"

        logger = Mock(spec=logging.Logger)
        room_id = "!test:matrix.test"
        agent_id = "agent-123"

        # Test path override doesn't persist
        with patch(
            "src.matrix.letta_code_service.update_letta_code_room_state"
        ) as mock_update, patch(
            "src.matrix.letta_code_service.get_letta_code_room_state",
            return_value={},
        ):
            result = await resolve_letta_project_dir(
                room_id, agent_id, config, logger, override_path="/override"
            )
            assert result == "/override"
            mock_update.assert_not_called()

        # Test reply with escaped sender
        mock_mapping = {
            "agent_name": "TestAgent",
            "matrix_user_id": "@agent:matrix.test",
        }

        with patch(
            "src.core.mapping_service.get_mapping_by_room_id",
            return_value=mock_mapping,
        ), patch(
            "src.matrix.agent_actions.get_agent_token",
            new_callable=AsyncMock,
            return_value="test_token",
        ), patch(
            "aiohttp.ClientSession.put",
            new_callable=AsyncMock,
        ) as mock_put:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"event_id": "$test"})
            mock_put.return_value.__aenter__.return_value = mock_response

            await send_as_agent_with_event_id(
                room_id,
                "Test message",
                config,
                logger,
                reply_to_event_id="$event123",
                reply_to_sender="<script>alert(1)</script>",
                reply_to_body="Original",
            )

            assert mock_put.called
            json_data = mock_put.call_args.kwargs.get("json", {})
            formatted_body = json_data.get("formatted_body", "")
            assert "<script>" not in formatted_body
