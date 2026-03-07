"""
Group gating orchestration layer.

Combines user-allowlist checking, mode evaluation and mention detection
into a single ``apply_group_gating()`` call that returns either:

* ``None``         – message should be dropped
* ``GatingResult`` – message should be processed (with metadata)
"""
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from src.matrix.group_config import GroupConfig, GroupMode, GroupsConfig, resolve_group_config
from src.matrix.mention_detection import detect_matrix_mention, MentionResult

logger = logging.getLogger(__name__)


@dataclass
class GatingResult:
    was_mentioned: bool
    mode: GroupMode
    method: Optional[str] = None   # "pill" | "text" | "regex"
    silent: bool = False           # ingest for memory but suppress response
    reason: str = ""


def apply_group_gating(
    room_id: str,
    sender_id: str,
    body: str,
    event_source: Optional[Dict],
    bot_user_id: str,
    groups_config: GroupsConfig,
) -> Optional[GatingResult]:
    """Evaluate group gating for a single incoming message.

    Returns ``None`` when the message should be silently dropped (disabled,
    mention-only without mention, blocked user).  Otherwise returns a
    ``GatingResult`` that tells the caller whether to respond or stay silent.
    """
    cfg = resolve_group_config(room_id, groups_config)

    # 1. user allowlist
    if not cfg.is_user_allowed(sender_id):
        logger.info(
            f"[GROUP_GATING] Dropped: {sender_id} not in allowlist for {room_id}"
        )
        return None

    # 2. disabled → drop everything
    if cfg.mode == "disabled":
        logger.debug(f"[GROUP_GATING] Room {room_id} disabled, dropping message")
        return None

    # 3. mention detection
    mention = detect_matrix_mention(
        body=body,
        event_source=event_source,
        bot_user_id=bot_user_id,
        compiled_patterns=cfg.compiled_patterns,
    )

    # 4. mode-specific logic
    if cfg.mode == "open":
        return GatingResult(
            was_mentioned=mention.was_mentioned,
            mode="open",
            method=mention.method,
            silent=False,
            reason="open: respond normally",
        )

    if cfg.mode == "listen":
        silent = not mention.was_mentioned
        return GatingResult(
            was_mentioned=mention.was_mentioned,
            mode="listen",
            method=mention.method,
            silent=silent,
            reason="listen: silent ingest" if silent else "listen: mentioned, respond",
        )

    if cfg.mode == "mention-only":
        if not mention.was_mentioned:
            logger.debug(
                f"[GROUP_GATING] Dropped: mention-only room {room_id}, not mentioned"
            )
            return None
        return GatingResult(
            was_mentioned=True,
            mode="mention-only",
            method=mention.method,
            silent=False,
            reason="mention-only: mentioned, respond",
        )

    # fail closed on unknown mode
    logger.warning(f"[GROUP_GATING] Unknown mode '{cfg.mode}' for {room_id}, denying")
    return None
