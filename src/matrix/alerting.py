"""
Push notification alerts via self-hosted ntfy.
Deduplicates repeated alerts within a configurable window.
"""

import asyncio
import logging
import os
import time
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger("matrix_client.alerting")

_DEDUP_WINDOW_S = 300  # 5 minutes
_last_alert_times: Dict[str, float] = {}


def _should_send(alert_key: str) -> bool:
    now = time.monotonic()
    last = _last_alert_times.get(alert_key, 0)
    if now - last < _DEDUP_WINDOW_S:
        return False
    _last_alert_times[alert_key] = now
    return True


async def send_alert(
    message: str,
    *,
    title: str = "Matrix Synapse Alert",
    priority: str = "default",
    tags: str = "warning",
    alert_key: Optional[str] = None,
) -> bool:
    ntfy_url = os.getenv("NTFY_URL", "http://127.0.0.1:2586")
    ntfy_topic = os.getenv("NTFY_TOPIC", "mxsyn-alerts")

    if not ntfy_topic:
        return False

    dedup_key = alert_key or message[:80]
    if not _should_send(dedup_key):
        logger.debug(f"[ALERT] Deduped (within {_DEDUP_WINDOW_S}s): {dedup_key}")
        return False

    url = f"{ntfy_url}/{ntfy_topic}"
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tags,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=message, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    logger.info(f"[ALERT] Sent: {title} — {message[:100]}")
                    return True
                else:
                    logger.warning(f"[ALERT] ntfy returned {resp.status}")
                    return False
    except Exception as e:
        logger.warning(f"[ALERT] Failed to send ntfy alert: {e}")
        return False


async def alert_auth_failure(username: str, room_id: str) -> bool:
    return await send_alert(
        f"Auth failure for {username} in room {room_id}. Possible RocksDB corruption from OOM.",
        title="Auth Failure",
        priority="high",
        tags="rotating_light,lock",
        alert_key=f"auth_fail:{username}",
    )


async def alert_streaming_timeout(agent_id: str, room_id: str, timeout_type: str, seconds: float) -> bool:
    return await send_alert(
        f"{timeout_type} timeout after {seconds:.0f}s for agent {agent_id} in {room_id}. Task killed, room unlocked.",
        title=f"Streaming {timeout_type} Timeout",
        priority="default",
        tags="hourglass,warning",
        alert_key=f"stream_timeout:{room_id}:{agent_id}",
    )


async def alert_letta_error(agent_id: str, room_id: str, error: str) -> bool:
    return await send_alert(
        f"Letta API error for agent {agent_id} in {room_id}: {error[:200]}",
        title="Letta API Error",
        priority="default",
        tags="x,warning",
        alert_key=f"letta_error:{agent_id}",
    )


async def alert_health_check_failed(failed_users: list) -> bool:
    return await send_alert(
        f"Health check failed for {len(failed_users)} users: {', '.join(failed_users)}. See OOM Recovery Runbook in AGENTS.md.",
        title="Health Check FAILED",
        priority="urgent",
        tags="rotating_light,skull",
        alert_key="health_check_fail",
    )
