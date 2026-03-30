"""
Matrix Client — slim orchestrator and main() entrypoint.

Domain logic lives in focused modules:
  - echo_filter: streaming progress / fallback echo detection
  - portal_handler: portal room passive observation / active request
  - task_manager: background Letta task dispatch, cancel, /stop
  - file_callback: file upload and poll response callbacks
  - fs_mode_handler: filesystem mode routing
  - message_router: _MessageCallbackRouter, message_callback
  - room_join: create_room_if_needed, join_room_if_needed
"""
import asyncio
import os
import logging
from typing import Optional, Dict, Any

import aiohttp
from nio import (
    AsyncClient,
    RoomMessageText,
    RoomMessageMedia,
    RoomMessageAudio,
    UnknownEvent,
)
from nio.exceptions import RemoteProtocolError
from sqlalchemy.exc import SQLAlchemyError

from src.matrix.auth import MatrixAuthManager
from src.matrix.file_handler import LettaFileHandler, FileUploadError
from src.matrix.document_parser import DocumentParseConfig
from src.core.agent_user_manager import run_agent_sync

# ── Re-exports (backward compatibility) ─────────────────────────────
from src.matrix.config import (  # noqa: F401
    Config,
    LettaApiError,
    MatrixClientError,
    ConfigurationError,
    LettaCodeApiError,
    setup_logging,
)
from src.matrix.agent_actions import (  # noqa: F401
    send_as_agent,
    send_as_agent_with_event_id,
    delete_message_as_agent,
    edit_message_as_agent,
    send_reaction_as_agent,
    send_read_receipt_as_agent,
    set_typing_as_agent,
    TypingIndicatorManager,
    upload_and_send_audio,
    fetch_and_send_image,
    fetch_and_send_file,
    fetch_and_send_video,
)
from src.matrix.letta_bridge import (  # noqa: F401
    send_to_letta_api,
    send_to_letta_api_streaming,
    retry_with_backoff,
    NO_TEXT_RESPONSE_FALLBACK,
)
from src.matrix.echo_filter import (  # noqa: F401
    _is_streaming_progress,
    _is_no_text_fallback_echo,
    _strip_leading_mxid_prefix,
    _STREAMING_PROGRESS_PREFIXES,
)
from src.matrix.portal_handler import (  # noqa: F401
    _is_portal_active_request,
    _handle_passive_portal_message,
)
from src.matrix.task_manager import (  # noqa: F401
    _active_letta_tasks,
    _on_letta_task_done,
    cancel_all_letta_tasks,
    _dispatch_letta_task,
    _handle_stop_command,
    _still_processing_last_sent,
    _STILL_PROCESSING_COOLDOWN,
)
from src.matrix.file_callback import (  # noqa: F401
    file_callback,
    poll_response_callback,
)
from src.matrix.fs_mode_handler import _maybe_handle_fs_mode  # noqa: F401
from src.matrix.letta_code_service import handle_letta_code_command  # noqa: F401
from src.matrix.message_router import (  # noqa: F401
    _MessageCallbackRouter,
    message_callback,
)
from src.matrix.event_dedupe import is_duplicate_event  # noqa: F401
from src.matrix.poll_handler import (  # noqa: F401
    process_agent_response,
    is_poll_command,
    handle_poll_vote,
    POLL_RESPONSE_TYPE,
)
from src.matrix.room_join import (  # noqa: F401
    create_room_if_needed,
    join_room_if_needed,
)
from src.matrix.message_processor import process_letta_message, MessageContext  # noqa: F401

MATRIX_API_URL = os.getenv('MATRIX_API_URL', 'http://matrix-api:8000')

# Global variables for backwards compatibility
client = None
auth_manager_global = None


async def periodic_agent_sync(config, logger, interval=None):
    """Periodically sync Letta agents to Matrix users via OpenAI endpoint"""
    if interval is None:
        interval = int(os.getenv('MATRIX_AGENT_SYNC_INTERVAL', '60'))

    logger.info(f'Starting periodic agent sync with interval: {interval}s')

    while True:
        await asyncio.sleep(interval)
        logger.debug('Running periodic agent sync via OpenAI endpoint...')
        try:
            await run_agent_sync(config)
            logger.debug('Periodic agent sync completed successfully')
        except (MatrixClientError, RemoteProtocolError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.error('Periodic agent sync failed', extra={'error': str(e)})


async def _set_all_agents_online(logger: logging.Logger) -> None:
    try:
        from src.core.identity_storage import get_identity_service
        from src.matrix.presence_manager import get_presence_manager

        svc = get_identity_service()
        identities = svc.get_by_type("letta")
        pairs: dict[str, str] = {}
        for ident in identities:
            mxid = str(ident.mxid) if ident.mxid is not None else ""
            token = str(ident.access_token) if ident.access_token is not None else ""
            if mxid and token:
                pairs[mxid] = token
        if pairs:
            mgr = get_presence_manager()
            count = await mgr.set_all_online(pairs)
            logger.info("Set %d/%d agent identities to online", count, len(pairs))
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        logger.debug("Presence startup skipped: %s", exc)


async def _set_all_agents_offline(logger: logging.Logger) -> None:
    try:
        from src.core.identity_storage import get_identity_service
        from src.matrix.presence_manager import get_presence_manager

        svc = get_identity_service()
        identities = svc.get_by_type("letta")
        pairs: dict[str, str] = {}
        for ident in identities:
            mxid = str(ident.mxid) if ident.mxid is not None else ""
            token = str(ident.access_token) if ident.access_token is not None else ""
            if mxid and token:
                pairs[mxid] = token
        if pairs:
            mgr = get_presence_manager()
            count = await mgr.set_all_offline(pairs)
            logger.info("Set %d/%d agent identities to offline", count, len(pairs))
    except (ImportError, RuntimeError, ValueError, OSError) as exc:
        logger.debug("Presence shutdown skipped: %s", exc)


async def main():
    global client
    global auth_manager_global

    try:
        config = Config.from_env()
    except ConfigurationError as e:
        print(f'Configuration error: {e}')
        return

    logger = setup_logging(config)
    logger.info(
        'Matrix client starting up',
        extra={
            'config': {
                'homeserver_url': config.homeserver_url,
                'username': config.username,
                'room_id': config.room_id,
                'letta_api_url': config.letta_api_url,
                'agent_id': config.letta_agent_id,
                'log_level': config.log_level,
            }
        },
    )

    auth_manager = MatrixAuthManager(
        config.homeserver_url, config.username, config.password, 'CustomNioClientToken'
    )

    logger.info('Running agent sync to create rooms for any new agents...')
    agent_manager = None
    try:
        agent_manager = await run_agent_sync(config)
        logger.info('Agent-to-user sync completed successfully')
    except (MatrixClientError, RemoteProtocolError, asyncio.TimeoutError, aiohttp.ClientError) as e:
        logger.error('Agent sync failed', extra={'error': str(e)})

    sync_task = asyncio.create_task(periodic_agent_sync(config, logger))

    auth_retry_delay = float(os.getenv('MATRIX_AUTH_RETRY_DELAY', '5.0'))
    while True:
        client = await auth_manager.get_authenticated_client()
        if client:
            break
        logger.error('Failed to authenticate with Matrix server, retrying')
        await asyncio.sleep(auth_retry_delay)

    logger.info(
        'Client authenticated successfully',
        extra={'user_id': client.user_id, 'device_id': client.device_id},
    )

    config.matrix_token = client.access_token

    joined_room_id = None
    if config.room_id:
        joined_room_id = await join_room_if_needed(client, config.room_id, logger)
        if not joined_room_id:
            logger.warning(
                'Configured MATRIX_ROOM_ID could not be joined; continuing without a base room',
                extra={'room_id': config.room_id},
            )
    else:
        logger.info('No MATRIX_ROOM_ID configured; skipping base room join')

    if joined_room_id:
        logger.info('Ready to interact in room', extra={'room_id': joined_room_id})
    else:
        logger.info('Proceeding without a dedicated base room; will listen in agent rooms only')

    if joined_room_id and joined_room_id != config.room_id:
        logger.warning(
            'New room created, please update configuration',
            extra={'new_room_id': joined_room_id, 'original_room_id': config.room_id},
        )

    if agent_manager:
        space_id = agent_manager.space_manager.get_space_id()
        if space_id:
            logger.info(f'Attempting to join Letta Agents space: {space_id}')
            space_joined = await join_room_if_needed(client, space_id, logger)
            if space_joined:
                logger.info(f'Successfully joined Letta Agents space')
            else:
                logger.warning(f'Failed to join Letta Agents space')
        else:
            logger.info('No Letta Agents space found, skipping space join')

    logger.info('Joining agent rooms...')
    agent_rooms_joined = 0
    try:
        from src.core.mapping_service import get_all_mappings

        mappings = get_all_mappings()
        for agent_id, mapping in mappings.items():
            room_id = mapping.get('room_id')
            agent_name = mapping.get('agent_name')
            if room_id:
                logger.info(f'Attempting to join room for agent {agent_name}')
                joined = await join_room_if_needed(client, room_id, logger)
                if joined:
                    agent_rooms_joined += 1
                    logger.info(f'Successfully joined room for agent {agent_name}: {room_id}')
                else:
                    logger.warning(f'Failed to join room for agent {agent_name}: {room_id}')
    except (RuntimeError, ValueError, SQLAlchemyError) as e:
        logger.error(f'Error loading agent mappings: {e}')

    logger.info(f'Joined {agent_rooms_joined} agent rooms')

    async def notify_room(room_id: str, message: str) -> Optional[str]:
        event_id = await send_as_agent_with_event_id(room_id, message, config, logger)
        if not event_id and client is not None:
            await client.room_send(
                room_id, 'm.room.message', {'msgtype': 'm.text', 'body': message}
            )
        return event_id

    matrix_token = client.access_token
    logger.info(
        f'Matrix access token available: {bool(matrix_token)}, length: {len(matrix_token) if matrix_token else 0}'
    )

    doc_parse_config = DocumentParseConfig(
        enabled=config.document_parsing_enabled,
        max_file_size_mb=config.document_parsing_max_file_size_mb,
        timeout_seconds=config.document_parsing_timeout,
        ocr_enabled=config.document_parsing_ocr_enabled,
        ocr_dpi=config.document_parsing_ocr_dpi,
        max_text_length=config.document_parsing_max_text_length,
    )

    file_handler = LettaFileHandler(
        homeserver_url=config.homeserver_url,
        letta_api_url=config.letta_api_url,
        letta_token=config.letta_token,
        matrix_access_token=matrix_token,
        notify_callback=notify_room,
        embedding_model=config.embedding_model,
        embedding_endpoint=config.embedding_endpoint or None,
        embedding_endpoint_type=config.embedding_endpoint_type,
        embedding_dim=config.embedding_dim,
        embedding_chunk_size=config.embedding_chunk_size,
        document_parsing_config=doc_parse_config,
    )
    logger.info(
        f'File handler initialized with embedding: model={config.embedding_model}, endpoint={config.embedding_endpoint or "default"}, dim={config.embedding_dim}'
    )

    warmup_ok = await file_handler.warm_up_ingest_embedder()
    logger.info(f'[HAYHOOKS-WARMUP] startup warm-up success={warmup_ok}')

    async def callback_wrapper(room, event):
        try:
            await message_callback(room, event, config, logger, client, auth_manager=auth_manager_global)
        except (MatrixClientError, LettaApiError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.error(f'Error in message callback: {e}', exc_info=True)

    client.add_event_callback(callback_wrapper, RoomMessageText)

    async def file_callback_wrapper(room, event):
        try:
            await file_callback(room, event, config, logger, file_handler)
        except (FileUploadError, MatrixClientError, LettaApiError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.error(f'Error in file callback: {e}', exc_info=True)

    client.add_event_callback(file_callback_wrapper, RoomMessageMedia)
    client.add_event_callback(file_callback_wrapper, RoomMessageAudio)

    async def poll_response_wrapper(room, event):
        try:
            await poll_response_callback(room, event, config, logger)
        except (MatrixClientError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.error(f'Error in poll response callback: {e}', exc_info=True)

    client.add_event_callback(poll_response_wrapper, UnknownEvent)

    logger.info('Starting sync loop to listen for messages, file uploads, and poll votes')

    initial_sync_filter = {
        'room': {
            'timeline': {'limit': 0},
            'state': {'lazy_load_members': True},
        },
        'presence': {'enabled': False},
        'account_data': {'enabled': False},
    }

    sync_filter = {
        'room': {
            'timeline': {'limit': 50},
            'state': {'lazy_load_members': True},
        },
        'presence': {'enabled': False},
        'account_data': {'enabled': False},
    }

    try:
        auth_manager_global = auth_manager

        logger.info('Performing initial sync to skip historical messages')
        await client.sync(timeout=30000, full_state=False, sync_filter=initial_sync_filter)
        logger.info('Initial sync complete, now listening for new messages')

        await _set_all_agents_online(logger)

        await client.sync_forever(timeout=5000, full_state=False, sync_filter=sync_filter)
    except (MatrixClientError, RemoteProtocolError, asyncio.TimeoutError, aiohttp.ClientError) as e:
        logger.error('Error during sync', extra={'error': str(e)}, exc_info=True)
    finally:
        await _set_all_agents_offline(logger)
        await cancel_all_letta_tasks()
        logger.info('Closing client session')
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
