"""Matrix pill formatter — converts @mentions in agent messages to clickable pills.

Converts @AgentName and @user:domain mentions to Matrix pill HTML:
  <a href="https://matrix.to/#/@user:domain">DisplayName</a>

Also collects MXIDs for m.mentions.user_ids (push notifications).

Pure utility — no async, no Matrix API calls.
"""

import html
import logging
import re
from typing import List, Optional, Tuple

from src.core.mapping_service import get_mapping_by_agent_name, get_mapping_by_matrix_user
from src.matrix.mention_routing import (
    FRIENDLY_MENTION_PATTERN,
    MXID_PATTERN,
    OC_MXID_PATTERN,
)

logger = logging.getLogger(__name__)

# Matches <pre>...</pre> and <code>...</code> blocks (including nested)
_CODE_BLOCK_RE = re.compile(
    r"(<pre[^>]*>.*?</pre>|<code[^>]*>.*?</code>)",
    re.DOTALL | re.IGNORECASE,
)


def _make_pill(mxid: str, display_name: str) -> str:
    """Generate Matrix pill HTML for a user mention."""
    escaped_name = html.escape(display_name, quote=True)
    return f'<a href="https://matrix.to/#/{mxid}">{escaped_name}</a>'


def _resolve_mentions(plain_text: str) -> List[Tuple[str, str, str]]:
    """
    Extract and resolve @mentions from plain text.

    Returns list of (matched_text, mxid, display_name) tuples.
    Deduplicates by matched_text to avoid double-replacement.
    """
    seen_texts: set[str] = set()
    results: list[Tuple[str, str, str]] = []
    resolved_spans: list[Tuple[int, int]] = []

    # 1. Full MXIDs (@user:domain) — highest priority
    for match in MXID_PATTERN.finditer(plain_text):
        full_mxid = match.group(0)
        if full_mxid in seen_texts:
            continue

        mapping = get_mapping_by_matrix_user(full_mxid)
        if mapping:
            display_name = mapping.get("agent_name", full_mxid)
            results.append((full_mxid, full_mxid, display_name))
            seen_texts.add(full_mxid)
            resolved_spans.append(match.span())
        elif OC_MXID_PATTERN.match(full_mxid):
            # OpenCode MXIDs — use MXID as display name
            results.append((full_mxid, full_mxid, full_mxid))
            seen_texts.add(full_mxid)
            resolved_spans.append(match.span())

    # 2. Friendly @Name mentions
    for match in FRIENDLY_MENTION_PATTERN.finditer(plain_text):
        name = match.group(1)
        full_match = match.group(0)
        span = match.span()

        # Skip if overlaps with already-resolved MXID match
        if any(s[0] <= span[0] < s[1] for s in resolved_spans):
            continue

        # Skip email-like patterns
        if span[0] > 0 and plain_text[span[0] - 1].isalnum():
            continue

        if full_match in seen_texts:
            continue

        mapping = get_mapping_by_agent_name(name, fuzzy=True)
        if mapping:
            mxid = mapping["matrix_user_id"]
            display_name = mapping.get("agent_name", name)
            results.append((full_match, mxid, display_name))
            seen_texts.add(full_match)
            resolved_spans.append(span)

    return results


def _replace_outside_code_blocks(html_body: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` in HTML, skipping content inside <pre>/<code> tags."""
    segments = _CODE_BLOCK_RE.split(html_body)
    result = []
    for segment in segments:
        if _CODE_BLOCK_RE.match(segment):
            result.append(segment)
        else:
            result.append(segment.replace(old, new))
    return "".join(result)


def extract_and_convert_pills(
    plain_text: str,
    html_body: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Convert @mentions in a message to Matrix pills.

    Args:
        plain_text: Raw message text (used for mention extraction).
        html_body: HTML-formatted body (pills inserted here).
                   If None, HTML is generated from plain_text with pills.

    Returns:
        (html_with_pills, list_of_mentioned_mxids)
        If no mentions found, returns (original html_body, []).
    """
    if not plain_text:
        return (html_body or "", [])

    mentions = _resolve_mentions(plain_text)
    if not mentions:
        return (html_body or plain_text, [])

    # Build working HTML — use provided html_body or create from plain text
    working_html = html_body if html_body else html.escape(plain_text)

    # Build replacement map and collect MXIDs
    replacements: dict[str, str] = {}
    mentioned_mxids: list[str] = []

    for matched_text, mxid, display_name in mentions:
        pill_html = _make_pill(mxid, display_name)
        replacements[matched_text] = pill_html
        if mxid not in mentioned_mxids:
            mentioned_mxids.append(mxid)

    # Apply replacements longest-first (prevents partial matches)
    for old_text in sorted(replacements, key=len, reverse=True):
        working_html = _replace_outside_code_blocks(
            working_html, old_text, replacements[old_text]
        )

    return (working_html, mentioned_mxids)
