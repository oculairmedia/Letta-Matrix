"""
File and poll response callbacks for Matrix events.
"""

import asyncio
import logging
from typing import Optional

import aiohttp
from nio import RoomMessageAudio, RoomMessageMedia
from sqlalchemy.exc import SQLAlchemyError

from src.matrix.agent_actions import edit_message_as_agent, send_as_agent
from src.matrix.config import Config, LettaApiError, MatrixClientError
from src.matrix.event_dedupe import is_duplicate_event
from src.matrix.file_handler import LettaFileHandler, FileUploadError
from src.matrix.letta_bridge import send_to_letta_api, send_to_letta_api_streaming
from src.matrix.poll_handler import handle_poll_vote, POLL_RESPONSE_TYPE


async def poll_response_callback(room, event, config: Config, logger: logging.Logger):
    if not hasattr(event, 'source') or not isinstance(event.source, dict):
        return

    content = event.source.get('content', {})
    event_type = event.source.get('type', '')

    if event_type != POLL_RESPONSE_TYPE:
        return

    sender = event.source.get('sender', '')
    poll_response = content.get('org.matrix.msc3381.poll.response', {})
    answers = poll_response.get('answers', [])
    relates_to = content.get('m.relates_to', {})
    poll_event_id = relates_to.get('event_id')

    if not poll_event_id or not answers:
        logger.debug(f'[POLL] Invalid poll response: missing event_id or answers')
        return

    logger.info(f'[POLL] Vote received from {sender} for poll {poll_event_id}: {answers}')

    from src.models.agent_mapping import AgentMappingDB

    db = AgentMappingDB()
    mapping = db.get_by_room_id(room.room_id)
    if not mapping:
        logger.debug(f'[POLL] No agent mapping for room {room.room_id}, ignoring poll vote')
        return

    vote_message = await handle_poll_vote(
        room_id=room.room_id,
        sender=sender,
        poll_event_id=poll_event_id,
        selected_option_ids=answers,
        config=config,
        logger_instance=logger,
    )

    if vote_message:
        await send_to_letta_api(vote_message, sender, config, logger, room.room_id)


async def file_callback(
    room,
    event,
    config: Config,
    logger: logging.Logger,
    file_handler: Optional[LettaFileHandler] = None,
):
    """Callback function for handling file uploads."""
    if not file_handler:
        logger.warning('File handler not initialized, skipping file event')
        return

    try:
        event_id = getattr(event, 'event_id', None)
        if event_id and is_duplicate_event(event_id, logger):
            return

        logger.info(f'File upload detected in room {room.room_id}')

        event_sender = getattr(event, 'sender', '')
        if event_sender.startswith('@agent_') and isinstance(
            event, (RoomMessageAudio, RoomMessageMedia)
        ):
            logger.debug(
                f"[MEDIA] Skipping agent's own media upload from {event_sender} (feedback loop prevention)"
            )
            return

        agent_id = None
        try:
            from src.models.agent_mapping import AgentMappingDB

            db = AgentMappingDB()
            mapping = db.get_by_room_id(room.room_id)
            if mapping:
                agent_id = str(mapping.agent_id)
                logger.info(
                    f'Using agent {mapping.agent_name} ({agent_id}) for room {room.room_id}'
                )
            else:
                logger.debug(
                    f'No agent mapping for room {room.room_id}, skipping file processing (relay room)'
                )
                return
        except (ImportError, RuntimeError, ValueError, SQLAlchemyError) as e:
            logger.warning(f'Could not query agent mappings: {e}, skipping file processing')
            return

        file_result = await file_handler.handle_file_event(event, room.room_id, agent_id)
        cleanup_event_ids, status_summary = file_handler.pop_cleanup_event_ids()

        if file_result is None:
            logger.info(f'[FILE] Document dispatched to Temporal workflow, nothing more to do')
            return

        if agent_id:
            try:
                await file_handler.ensure_search_tool_attached(agent_id)
                logger.info(f'[FILE] search_documents tool verified for agent {agent_id}')
            except (RuntimeError, ValueError, TypeError, LettaApiError, MatrixClientError, asyncio.TimeoutError, aiohttp.ClientError) as attach_err:
                logger.error(f'[FILE] Failed to attach search_documents tool: {attach_err}')

        if isinstance(file_result, (list, str)):
            if config.letta_streaming_enabled:
                await send_to_letta_api_streaming(
                    file_result, event.sender, config, logger, room.room_id
                )
            else:
                await send_to_letta_api(file_result, event.sender, config, logger, room.room_id)

            for i, eid in enumerate(cleanup_event_ids):
                try:
                    if i == 0 and status_summary:
                        await edit_message_as_agent(
                            room.room_id, eid, status_summary, config, logger
                        )
                    else:
                        await edit_message_as_agent(room.room_id, eid, '\u200b', config, logger)
                except (RuntimeError, ValueError, TypeError, MatrixClientError, LettaApiError, asyncio.TimeoutError, aiohttp.ClientError) as cleanup_err:
                    logger.debug(
                        f'[FILE-CLEANUP] Failed to clean up status message {eid}: {cleanup_err}'
                    )

    except FileUploadError as e:
        logger.error(f'File upload error: {e}')

    except (MatrixClientError, LettaApiError, asyncio.TimeoutError, RuntimeError, ValueError) as e:
        logger.error(f'Unexpected error in file callback: {e}', exc_info=True)
