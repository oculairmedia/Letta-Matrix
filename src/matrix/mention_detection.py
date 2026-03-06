"""
Mention detection for Matrix group gating.

Three detection methods in priority order:
  1. Matrix pills  – m.mentions.user_ids (MSC 3952)
  2. @username text – case-insensitive @localpart word-boundary match
  3. Custom regex   – patterns from GroupConfig.mention_patterns
"""
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.matrix.mention_routing import strip_reply_fallback

logger = logging.getLogger(__name__)


@dataclass
class MentionResult:
    was_mentioned: bool
    method: Optional[str] = None       # "pill" | "text" | "regex" | None
    matched_text: Optional[str] = None


# ── internal helpers ─────────────────────────────────────────────────

def _check_pill(
    event_source: Optional[Dict],
    bot_user_id: str,
) -> Tuple[bool, Optional[str]]:
    """Check MSC 3952 m.mentions.user_ids."""
    if not event_source or not isinstance(event_source, dict):
        return False, None
    content = event_source.get("content", {})
    if not isinstance(content, dict):
        return False, None
    mentions = content.get("m.mentions", {})
    if not isinstance(mentions, dict):
        return False, None
    user_ids = mentions.get("user_ids", [])
    if isinstance(user_ids, list) and bot_user_id in user_ids:
        return True, bot_user_id
    return False, None


def _check_text(body: str, bot_localpart: str) -> Tuple[bool, Optional[str]]:
    """Case-insensitive @localpart word-boundary match."""
    if not body or not bot_localpart:
        return False, None
    clean = strip_reply_fallback(body)
    pat = re.compile(rf"@{re.escape(bot_localpart)}\b", re.IGNORECASE)
    m = pat.search(clean)
    if m:
        return True, m.group(0)
    return False, None


def _check_regex(
    body: str,
    compiled_patterns: List[re.Pattern],
) -> Tuple[bool, Optional[str]]:
    """Custom regex patterns from config."""
    if not body or not compiled_patterns:
        return False, None
    clean = strip_reply_fallback(body)
    for pat in compiled_patterns:
        m = pat.search(clean)
        if m:
            return True, m.group(0)
    return False, None


# ── public API ───────────────────────────────────────────────────────

def detect_matrix_mention(
    body: str,
    event_source: Optional[Dict],
    bot_user_id: str,
    compiled_patterns: Optional[List[re.Pattern]] = None,
) -> MentionResult:
    """Return a MentionResult indicating whether *bot_user_id* was mentioned.

    Checks pills → @text → custom regex, returning on the first hit.
    """
    # 1. pills
    hit, text = _check_pill(event_source, bot_user_id)
    if hit:
        return MentionResult(True, "pill", text)

    # 2. @localpart
    localpart = ""
    if bot_user_id and bot_user_id.startswith("@"):
        localpart = bot_user_id[1:].split(":")[0]
    hit, text = _check_text(body, localpart)
    if hit:
        return MentionResult(True, "text", text)

    # 3. regex
    hit, text = _check_regex(body, compiled_patterns or [])
    if hit:
        return MentionResult(True, "regex", text)

    return MentionResult(False)
