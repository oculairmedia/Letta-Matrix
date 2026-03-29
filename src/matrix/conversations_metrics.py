import threading
from typing import Dict


_lock = threading.Lock()
letta_conversations_api_fallback_total = 0
letta_api_mode = 0
_fallback_reasons: Dict[str, int] = {}


def increment_fallback(reason: str) -> None:
    global letta_conversations_api_fallback_total
    with _lock:
        letta_conversations_api_fallback_total += 1
        _fallback_reasons[reason] = _fallback_reasons.get(reason, 0) + 1


def set_api_mode(mode: int) -> None:
    global letta_api_mode
    with _lock:
        letta_api_mode = mode


def snapshot() -> Dict[str, object]:
    with _lock:
        return {
            "letta_conversations_api_fallback_total": letta_conversations_api_fallback_total,
            "letta_api_mode": letta_api_mode,
            "fallback_reasons": dict(_fallback_reasons),
        }


def reset() -> None:
    global letta_conversations_api_fallback_total, letta_api_mode
    with _lock:
        letta_conversations_api_fallback_total = 0
        letta_api_mode = 0
        _fallback_reasons.clear()
