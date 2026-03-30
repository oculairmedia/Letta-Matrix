"""
Background Letta task management — dispatch, cancel, and track async tasks.
"""

import asyncio
import logging
import time
from typing import Dict, Optional, Tuple

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


def _on_letta_task_done(key: Tuple[str, str], task: asyncio.Task) -> None:
    _active_letta_tasks.pop(key, None)
    if task.cancelled():
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


async def cancel_all_letta_tasks() -> None:
    if not _active_letta_tasks:
        return
    logger.info(f'[BG-TASK] Cancelling {len(_active_letta_tasks)} active Letta tasks...')
    for task in _active_letta_tasks.values():
        task.cancel()
    await asyncio.gather(*_active_letta_tasks.values(), return_exceptions=True)
    _active_letta_tasks.clear()


async def _handle_stop_command(
    room, config, logger, room_agent_id, room_agent_name, message_text
) -> bool:
    if message_text.strip().lower() != '/stop':
        return False

    existing = _active_letta_tasks.get((room.room_id, room_agent_id or 'unknown'))
    stopped = False
    if existing and not existing.done():
        existing.cancel()
        _active_letta_tasks.pop((room.room_id, room_agent_id or 'unknown'), None)
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

    msg = '⏹ Stopped.' if stopped else 'Nothing running to stop.'
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
        now = time.monotonic()
        last_sent = _still_processing_last_sent.get(room.room_id, 0.0)
        if now - last_sent >= _STILL_PROCESSING_COOLDOWN:
            logger.warning(
                f'[BG-TASK] Agent still processing previous message for {task_key}, sending notice'
            )
            try:
                await send_as_agent(
                    room.room_id,
                    '⏳ Still processing previous message...',
                    config,
                    logger,
                    msgtype='m.notice',
                    reply_to_event_id=(
                        None if thread_event_id else (reply_event_id_for_notice or user_reply_to_event_id)
                    ),
                    thread_event_id=thread_event_id,
                    thread_latest_event_id=thread_latest_event_id,
                )
                _still_processing_last_sent[room.room_id] = now
            except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError) as notice_error:
                logger.debug(
                    f'[BG-TASK] Failed to send still-processing notice for {task_key}: {notice_error}'
                )
        else:
            logger.debug(
                f'[BG-TASK] Suppressed "still processing" notice for {task_key} '
                f'(cooldown: {_STILL_PROCESSING_COOLDOWN - (now - last_sent):.0f}s remaining)'
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
