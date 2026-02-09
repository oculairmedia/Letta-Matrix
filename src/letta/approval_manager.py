import logging
import time
from typing import Optional

from letta_client import Letta

logger = logging.getLogger(__name__)

THROTTLE_INTERVAL_S = 300  # 5 minutes

_last_disable_times: dict[str, float] = {}


def disable_all_tool_approvals(
    client: Letta,
    agent_id: str,
    force: bool = False,
) -> int:
    now = time.monotonic()
    last = _last_disable_times.get(agent_id, 0)
    if not force and (now - last) < THROTTLE_INTERVAL_S:
        return 0

    tools = client.agents.tools.list(agent_id=agent_id)
    disabled_count = 0

    for tool in tools:
        name = getattr(tool, "name", None)
        if not name:
            continue
        try:
            client.agents.tools.update_approval(
                tool_name=name,
                agent_id=agent_id,
                body_requires_approval=False,
            )
            disabled_count += 1
        except Exception as e:
            logger.warning(f"[APPROVAL] Failed to disable approval for {name}: {e}")

    _last_disable_times[agent_id] = time.monotonic()
    if disabled_count:
        logger.info(f"[APPROVAL] Disabled approvals for {disabled_count} tools on agent {agent_id}")
    return disabled_count


def is_approval_conflict_error(error: Exception) -> bool:
    msg = str(error).lower()
    if "409" in msg or getattr(error, "status_code", None) == 409:
        return True
    return "waiting for approval" in msg or ("conflict" in msg and "approval" in msg)


def recover_orphaned_approval(
    client: Letta,
    agent_id: str,
    conversation_id: Optional[str] = None,
) -> bool:
    try:
        runs = client.runs.list(agent_id=agent_id, limit=5)
        for run in runs:
            status = getattr(run, "status", None)
            run_id = getattr(run, "id", None)
            if not run_id:
                continue

            if status in ("failed", "cancelled", "completed"):
                continue

            logger.warning(f"[APPROVAL] Found stuck run {run_id} (status={status}), cancelling")
            try:
                client.agents.messages.cancel(agent_id=agent_id)
                logger.info(f"[APPROVAL] Cancelled active message processing for agent {agent_id}")
                return True
            except Exception as e:
                logger.error(f"[APPROVAL] Failed to cancel stuck run: {e}")

        logger.debug("[APPROVAL] No stuck runs found")
        return False
    except Exception as e:
        logger.error(f"[APPROVAL] Error during orphan recovery: {e}", exc_info=True)
        return False
