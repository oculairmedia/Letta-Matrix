"""
Background Letta task management — dispatch, cancel, and track async tasks.

Includes a per-room message queue so incoming messages received while an
agent is busy are not dropped but processed after the current task finishes.
"""

import asyncio
import collections
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import aiohttp

from src.matrix.agent_actions import send_as_agent
from src.matrix.config import Config, LettaApiError, MatrixClientError
from src.matrix.message_processor import MessageContext, process_letta_message

logger = logging.getLogger("matrix_client")

# Background Letta task tracking — keyed by (room_id, agent_id)
_active_letta_tasks: Dict[Tuple[str, str], asyncio.Task] = {}

# Rate-limit "still processing" notices — keyed by room_id, value is last send time
_still_processing_last_sent: Dict[str, float] = {}
_STILL_PROCESSING_COOLDOWN = 60.0  # seconds between notices per room

# ── Per-room message queue ────────────────────────────────────────────
_MAX_QUEUE_SIZE = int(os.getenv("LETTA_MESSAGE_QUEUE_MAX_SIZE", "5"))


@dataclass
class _QueuedMessage:
    """Snapshot of everything needed to re-dispatch a queued message."""
    room: Any
    event: Any
    config: Any
    logger: Any
    client: Any
    room_agent_id: Optional[str]
    gating_result: Any
    message_text: str
    user_reply_to_event_id: Optional[str]
    auth_manager: Any = None


# Per task_key → bounded deque of _QueuedMessage
_pending_queues: Dict[Tuple[str, str], collections.deque] = {}


def _on_letta_task_done(key: Tuple[str, str], task: asyncio.Task) -> None:
    _active_letta_tasks.pop(key, None)
    if task.cancelled():
        # Clear queue on cancellation (e.g. /stop)
        _pending_queues.pop(key, None)
        return
    exc = task.exception()
    if exc:
        logger.error(
            f'[BG-TASK] Background Letta task failed for {key}: {exc}', exc_info=exc
        )
        try:
            from src.matrix.alerting import alert_letta_error

            room_id, agent_id = key
            asyncio.get_event_loop().create_task(alert_letta_error(agent_id, room_id, str(exc)))
        except (ImportError, RuntimeError, ValueError) as alert_error:
            logger.debug(
                f'[BG-TASK] Failed to enqueue alert for {key}: {alert_error}'
            )

    # Drain the next queued message for this key, if any
    _drain_next_queued_message(key)


def _drain_next_queued_message(key: Tuple[str, str]) -> None:
    """Pop the next queued message and dispatch it as a new background task."""
    queue = _pending_queues.get(key)
    if not queue:
        _pending_queues.pop(key, None)
        return
    msg = queue.popleft()
    if not queue:
        _pending_queues.pop(key, None)

    logger.info(
        f'[BG-TASK] Draining queued message for {key} '
        f'({len(queue) if queue else 0} remaining)'
    )

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        logger.warning(f'[BG-TASK] No event loop to drain queue for {key}')
        return

    loop.create_task(_dispatch_queued_message(key, msg))


async def _dispatch_queued_message(key: Tuple[str, str], msg: _QueuedMessage) -> None:
    """Re-dispatch a previously queued message through the normal path."""
    try:
        await _dispatch_letta_task(
            room=msg.room,
            event=msg.event,
            config=msg.config,
            logger=msg.logger,
            client=msg.client,
            room_agent_id=msg.room_agent_id,
            gating_result=msg.gating_result,
            message_text=msg.message_text,
            user_reply_to_event_id=msg.user_reply_to_event_id,
            auth_manager=msg.auth_manager,
        )
    except Exception as exc:
        logger.error(f'[BG-TASK] Failed to dispatch queued message for {key}: {exc}', exc_info=exc)


async def cancel_all_letta_tasks() -> None:
    if not _active_letta_tasks:
        return
    logger.info(f'[BG-TASK] Cancelling {len(_active_letta_tasks)} active Letta tasks...')
    _pending_queues.clear()
    for task in _active_letta_tasks.values():
        task.cancel()
    await asyncio.gather(*_active_letta_tasks.values(), return_exceptions=True)
    _active_letta_tasks.clear()


async def _handle_stop_command(
    room, config, logger, room_agent_id, room_agent_name, message_text
) -> bool:
    if message_text.strip().lower() != '/stop':
        return False

    stop_key = (room.room_id, room_agent_id or 'unknown')
    existing = _active_letta_tasks.get(stop_key)
    stopped = False

    # Clear queued messages for this room
    queue_cleared = len(_pending_queues.get(stop_key, []))
    _pending_queues.pop(stop_key, None)
    if queue_cleared:
        logger.info(f'[STOP] Cleared {queue_cleared} queued message(s) for {stop_key}')

    if existing and not existing.done():
        existing.cancel()
        _active_letta_tasks.pop(stop_key, None)
        stopped = True
        logger.info(f'[STOP] Cancelled active task for {room_agent_name} in {room.room_id}')

    if room_agent_id:
        try:
            from src.letta.ws_gateway_client import get_gateway_client

            gw = await get_gateway_client(
                gateway_url=config.letta_gateway_url,
                api_key=config.letta_gateway_api_key,
            )
            aborted = await gw.abort(room_agent_id)
            if aborted:
                stopped = True
                logger.info(
                    f'[STOP] Sent gateway abort + evicted WS connection for agent {room_agent_id}'
                )
        except (LettaApiError, MatrixClientError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f'[STOP] Gateway abort failed: {e}')

    if stopped and queue_cleared:
        msg = f'⏹ Stopped. Cleared {queue_cleared} queued message(s).'
    elif stopped:
        msg = '⏹ Stopped.'
    elif queue_cleared:
        msg = f'⏹ Cleared {queue_cleared} queued message(s).'
    else:
        msg = 'Nothing running to stop.'
    await send_as_agent(room.room_id, msg, config, logger)
    return True


async def _dispatch_letta_task(
    room,
    event,
    config,
    logger,
    client,
    room_agent_id,
    gating_result,
    message_text,
    user_reply_to_event_id,
    auth_manager=None,
) -> bool:
    task_key = (room.room_id, room_agent_id or 'unknown')
    thread_event_id: Optional[str] = None
    thread_latest_event_id: Optional[str] = None
    reply_event_id_for_notice: Optional[str] = None
    source = getattr(event, 'source', None)
    if isinstance(source, dict):
        content = source.get('content', {})
        if isinstance(content, dict):
            relates_to = content.get('m.relates_to', {})
            if isinstance(relates_to, dict):
                rel_type = relates_to.get('rel_type')
                thread_root_candidate = relates_to.get('event_id')
                if rel_type == 'm.thread' and isinstance(thread_root_candidate, str):
                    thread_event_id = thread_root_candidate
                    thread_latest_event_id = getattr(event, 'event_id', None) or user_reply_to_event_id
                in_reply_to = relates_to.get('m.in_reply_to', {})
                if isinstance(in_reply_to, dict):
                    reply_event_id_for_notice = in_reply_to.get('event_id')

    existing_task = _active_letta_tasks.get(task_key)
    if existing_task and not existing_task.done():
        # ── Enqueue instead of dropping ────────────────────────────
        queue = _pending_queues.get(task_key)
        if queue is not None and len(queue) >= _MAX_QUEUE_SIZE:
            logger.warning(f'[BG-TASK] Queue full ({_MAX_QUEUE_SIZE}) for {task_key}, dropping message')
            try:
                await send_as_agent(
                    room.room_id,
                    f'⏳ Queue full ({_MAX_QUEUE_SIZE} messages) — please wait for current task to finish.',
                    config,
                    logger,
                    msgtype='m.notice',
                    reply_to_event_id=(
                        None if thread_event_id else (reply_event_id_for_notice or user_reply_to_event_id)
                    ),
                    thread_event_id=thread_event_id,
                    thread_latest_event_id=thread_latest_event_id,
                )
            except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError):
                pass
            return True

        if queue is None:
            queue = collections.deque(maxlen=_MAX_QUEUE_SIZE)
            _pending_queues[task_key] = queue

        queued_msg = _QueuedMessage(
            room=room,
            event=event,
            config=config,
            logger=logger,
            client=client,
            room_agent_id=room_agent_id,
            gating_result=gating_result,
            message_text=message_text,
            user_reply_to_event_id=user_reply_to_event_id,
            auth_manager=auth_manager,
        )
        queue.append(queued_msg)
        position = len(queue)
        logger.info(f'[BG-TASK] Queued message for {task_key} (position {position}/{_MAX_QUEUE_SIZE})')

        try:
            await send_as_agent(
                room.room_id,
                f'⏳ Queued (position {position}) — will process after current task.',
                config,
                logger,
                msgtype='m.notice',
                reply_to_event_id=(
                    None if thread_event_id else (reply_event_id_for_notice or user_reply_to_event_id)
                ),
                thread_event_id=thread_event_id,
                thread_latest_event_id=thread_latest_event_id,
            )
        except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError) as notice_error:
            logger.debug(
                f'[BG-TASK] Failed to send queued notice for {task_key}: {notice_error}'
            )
        return True

    event_source = getattr(event, 'source', None)
    event_source = event_source if isinstance(event_source, dict) else None
    silent_mode = bool(gating_result and gating_result.silent) if gating_result else False
    sender_display_name = room.user_name(event.sender) if hasattr(room, 'user_name') else None
    msg_ctx = MessageContext(
        event_body=message_text,
        event_sender=event.sender,
        event_sender_display_name=sender_display_name,
        event_source=event_source,
        original_event_id=getattr(event, 'event_id', None),
        user_reply_to_event_id=user_reply_to_event_id,
        room_id=room.room_id,
        room_display_name=room.display_name or room.room_id,
        room_agent_id=room_agent_id,
        config=config,
        logger=logger,
        client=client,
        silent_mode=silent_mode,
        auth_manager=auth_manager,
    )
    task = asyncio.create_task(process_letta_message(msg_ctx))
    task.add_done_callback(lambda t: _on_letta_task_done(task_key, t))
    _active_letta_tasks[task_key] = task
    logger.info(f'[BG-TASK] Dispatched background Letta task for {task_key}')
    return True
