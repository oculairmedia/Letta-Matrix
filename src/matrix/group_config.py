"""
Per-room group gating configuration for Matrix bot response modes.

Modes:
  - open:         Respond to all messages (current default behavior)
  - listen:       Ingest all messages for memory, only respond when mentioned
  - mention-only: Only process messages where the bot is mentioned
  - disabled:     Ignore all messages in the room

Usage:
  Set MATRIX_GROUPS_JSON env var with a JSON object mapping room IDs to config.
  Use "*" as a wildcard/default key.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Set

GroupMode = Literal["open", "listen", "mention-only", "disabled"]
_VALID_MODES: List[str] = ["open", "listen", "mention-only", "disabled"]

logger = logging.getLogger(__name__)


@dataclass
class GroupConfig:
    """Per-room configuration for group gating."""

    mode: GroupMode = "open"
    allowed_users: Set[str] = field(default_factory=set)
    mention_patterns: List[str] = field(default_factory=list)

    # Compiled regex cache (not serialised)
    _compiled_patterns: List[re.Pattern] = field(
        default_factory=list, repr=False, compare=False, init=False
    )

    def __post_init__(self) -> None:
        self._compiled_patterns = []
        for pattern in self.mention_patterns:
            try:
                self._compiled_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.warning(f"Invalid mention pattern '{pattern}': {exc}")

    @property
    def compiled_patterns(self) -> List[re.Pattern]:
        return self._compiled_patterns

    def is_user_allowed(self, user_id: str) -> bool:
        """Empty allowlist ⇒ everyone allowed."""
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users


# room_id → config; "*" = wildcard default
GroupsConfig = Dict[str, GroupConfig]


def _parse_mode(value: str) -> GroupMode:
    normalised = value.strip().lower()
    if normalised in _VALID_MODES:
        return normalised  # type: ignore[return-value]
    raise ValueError(
        f"Invalid group mode '{value}'. Must be one of: {', '.join(_VALID_MODES)}"
    )


def _parse_config_dict(data: dict) -> GroupConfig:
    mode: GroupMode = "open"
    if "mode" in data:
        mode = _parse_mode(data["mode"])

    allowed_users: Set[str] = set()
    if "allowed_users" in data and isinstance(data["allowed_users"], list):
        allowed_users = {str(u).strip() for u in data["allowed_users"] if u}

    mention_patterns: List[str] = []
    if "mention_patterns" in data and isinstance(data["mention_patterns"], list):
        mention_patterns = [str(p).strip() for p in data["mention_patterns"] if p]

    return GroupConfig(
        mode=mode,
        allowed_users=allowed_users,
        mention_patterns=mention_patterns,
    )


def load_groups_config(raw_json: str = "") -> GroupsConfig:
    """Parse a JSON string into a GroupsConfig dict.

    If *raw_json* is empty, reads from the ``MATRIX_GROUPS_JSON`` env var.
    Returns an empty dict when nothing is configured (all rooms default to
    ``open`` mode via :func:`resolve_group_config`).
    """
    if not raw_json:
        raw_json = os.getenv("MATRIX_GROUPS_JSON", "").strip()
    if not raw_json:
        return {}

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse MATRIX_GROUPS_JSON: {exc}")
        return {}

    if not isinstance(data, dict):
        logger.error(f"MATRIX_GROUPS_JSON must be a JSON object, got {type(data).__name__}")
        return {}

    config: GroupsConfig = {}
    for room_id, room_cfg in data.items():
        if not isinstance(room_id, str) or not isinstance(room_cfg, dict):
            logger.warning(f"Skipping invalid entry for room '{room_id}'")
            continue
        try:
            config[room_id] = _parse_config_dict(room_cfg)
            logger.debug(f"[GROUP_CONFIG] {room_id}: mode={config[room_id].mode}")
        except ValueError as exc:
            logger.warning(f"Invalid config for room {room_id}: {exc}")

    return config


def resolve_group_config(room_id: str, groups: GroupsConfig) -> GroupConfig:
    """Resolve effective config: exact match → wildcard → default open."""
    if room_id in groups:
        return groups[room_id]
    if "*" in groups:
        return groups["*"]
    return GroupConfig(mode="open")
