"""
Message content building — construct Matrix message and edit payloads
with markdown rendering, mention pills, and thread/reply formatting.
"""

import logging
import re
from typing import Any, Dict, Optional

from src.matrix.pill_formatter import extract_and_convert_pills

try:
    import markdown as _markdown
except ImportError:
    _markdown = None

_MODULE_LOGGER = logging.getLogger("matrix_client.agent_actions")

_MD_SIGNIFICANT = re.compile(r"[*_`#\[\]|>~]|^-\s", re.MULTILINE)
_HAS_MENTION_CANDIDATE = re.compile(r"(^|\s)@[\w.-]+")
_SIMPLE_MSG_MAX_LEN = 200


def _is_simple_message(text: str) -> bool:
    """Return True for short single-line messages with no markdown syntax."""
    return (
        len(text) <= _SIMPLE_MSG_MAX_LEN
        and "\n" not in text
        and not _MD_SIGNIFICANT.search(text)
    )


def _might_contain_mentions(text: str) -> bool:
    return bool(_HAS_MENTION_CANDIDATE.search(text))


def _build_message_content(
    message: str,
    msgtype: str = "m.text",
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
    reply_to_body: Optional[str] = None,
    room_id: Optional[str] = None,
    thread_event_id: Optional[str] = None,
    thread_latest_event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build Matrix message content dict with markdown, pills, and reply formatting."""
    message_data: Dict[str, Any] = {"msgtype": msgtype, "body": message}
    _pill_mxids: list = []
    _needs_rich_formatting = not _is_simple_message(message)

    if _needs_rich_formatting:
        if _markdown is not None:
            html_body = _markdown.markdown(
                message,
                extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
            )
            if html_body and html_body != f"<p>{message}</p>":
                message_data["format"] = "org.matrix.custom.html"
                message_data["formatted_body"] = html_body

    if _might_contain_mentions(message):
        try:
            pill_html, _pill_mxids = extract_and_convert_pills(
                message, message_data.get("formatted_body")
            )
            if _pill_mxids:
                message_data["formatted_body"] = pill_html
                message_data["format"] = "org.matrix.custom.html"
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            _MODULE_LOGGER.debug(
                "[SEND_AS_AGENT] Mention pill conversion failed: %s", exc
            )

    if thread_event_id:
        fallback_event_id = thread_latest_event_id or reply_to_event_id or thread_event_id
        message_data["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": thread_event_id,
            "is_falling_back": True,
            "m.in_reply_to": {"event_id": fallback_event_id},
        }
    elif reply_to_event_id:
        message_data["m.relates_to"] = {
            "rel_type": "m.thread",
            "event_id": reply_to_event_id,
            "is_falling_back": True,
            "m.in_reply_to": {"event_id": reply_to_event_id},
        }
        if reply_to_sender:
            message_data["m.mentions"] = {"user_ids": [reply_to_sender]}

    if _pill_mxids:
        existing = message_data.get("m.mentions", {}).get("user_ids", [])
        merged = list(dict.fromkeys(existing + _pill_mxids))
        message_data["m.mentions"] = {"user_ids": merged}

    return message_data


def _build_edit_content(
    event_id: str,
    new_body: str,
    msgtype: str = "m.text",
) -> Dict[str, Any]:
    """Build Matrix edit message content dict with markdown and pills."""
    message_data = {
        "msgtype": msgtype,
        "body": f"* {new_body}",
        "m.new_content": {"msgtype": msgtype, "body": new_body},
        "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
    }

    _formatted_body = None
    _pill_mxids: list = []

    if not _is_simple_message(new_body):
        if _markdown is not None:
            _html = _markdown.markdown(
                new_body,
                extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
            )
            if _html and _html != f"<p>{new_body}</p>":
                _formatted_body = _html

    if _might_contain_mentions(new_body):
        try:
            pill_html, _pill_mxids = extract_and_convert_pills(new_body, _formatted_body)
            if _pill_mxids:
                _formatted_body = pill_html
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            _MODULE_LOGGER.debug(
                "[EDIT_AS_AGENT] Mention pill conversion failed: %s", exc
            )

    if _formatted_body:
        message_data["format"] = "org.matrix.custom.html"
        message_data["formatted_body"] = f"* {_formatted_body}"
        message_data["m.new_content"]["format"] = "org.matrix.custom.html"
        message_data["m.new_content"]["formatted_body"] = _formatted_body
    if _pill_mxids:
        mentions = {"user_ids": list(dict.fromkeys(_pill_mxids))}
        message_data["m.new_content"]["m.mentions"] = mentions

    return message_data
