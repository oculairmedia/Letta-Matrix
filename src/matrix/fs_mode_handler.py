"""
Filesystem mode handler — routes messages through letta-code when fs-mode is active.
"""

import asyncio
import logging
import os
import time
from typing import Optional

import aiohttp

from src.matrix import formatter as matrix_formatter
from src.matrix.agent_actions import send_as_agent
from src.matrix.config import LettaApiError, LettaCodeApiError, MatrixClientError
from src.matrix.letta_code_service import (
    call_letta_code_api,
    get_letta_code_room_state,
    resolve_letta_project_dir,
    run_letta_code_task,
    update_letta_code_room_state,
)


async def _maybe_handle_fs_mode(
    room, event, config, logger, room_agent_id, room_agent_name
) -> bool:
    fs_state = get_letta_code_room_state(room.room_id)
    fs_enabled = fs_state.get('enabled')
    is_huly_agent = room_agent_name and (
        room_agent_name.startswith('Huly - ') or room_agent_name == 'Huly-PM-Control'
    )
    fs_mode_agents = [
        a.strip() for a in os.getenv('FS_MODE_AGENTS', 'Meridian').split(',') if a.strip()
    ]
    is_fs_mode_agent = room_agent_name and room_agent_name in fs_mode_agents
    use_fs_mode = fs_enabled is True or (fs_enabled is None and (is_huly_agent or is_fs_mode_agent))
    if not use_fs_mode:
        return False

    agent_id = room_agent_id
    agent_name = room_agent_name or 'Filesystem Agent'
    if not agent_id or not agent_name:
        from src.models.agent_mapping import AgentMappingDB

        db = AgentMappingDB()
        mapping = db.get_by_room_id(room.room_id)
        if mapping:
            agent_id = str(mapping.agent_id)
            agent_name = str(mapping.agent_name)
    if not agent_id:
        await send_as_agent(
            room.room_id, 'No agent configured for filesystem mode.', config, logger
        )
        return True

    project_dir = fs_state.get('projectDir')
    if not project_dir:
        project_dir = await resolve_letta_project_dir(room.room_id, agent_id, config, logger)

    if not project_dir and is_huly_agent and agent_name:
        try:
            projects_response = await call_letta_code_api(config, 'GET', '/api/projects')
            projects = projects_response.get('projects', [])
            search_name = agent_name[7:] if agent_name.startswith('Huly - ') else agent_name
            for proj in projects:
                if proj.get('name', '').lower() == search_name.lower():
                    project_dir = proj.get('filesystem_path')
                    if project_dir:
                        update_letta_code_room_state(room.room_id, {'projectDir': project_dir})
                        logger.info(f'[HULY-FS] Auto-linked {agent_name} to {project_dir}')
                    break
        except (LettaCodeApiError, MatrixClientError, asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f'[HULY-FS] Auto-link failed for {agent_name}: {e}')

    if not project_dir:
        await send_as_agent(
            room.room_id,
            'Filesystem mode enabled but no project linked. Run /fs-link.',
            config,
            logger,
        )
        return True

    fs_prompt = event.body
    fs_event_timestamp = getattr(event, 'server_timestamp', None)
    event_source_for_fs = getattr(event, 'source', None)
    if fs_event_timestamp is None and isinstance(event_source_for_fs, dict):
        fs_event_timestamp = event_source_for_fs.get('origin_server_ts')
    if fs_event_timestamp is None:
        fs_event_timestamp = int(time.time() * 1000)

    if event.sender.startswith('@oc_'):
        opencode_mxid = event.sender
        fs_prompt = matrix_formatter.format_opencode_envelope(
            opencode_mxid=opencode_mxid,
            text=event.body,
            chat_id=room.room_id,
            message_id=getattr(event, 'event_id', None),
            timestamp=fs_event_timestamp,
        )
        logger.info(f'[OPENCODE-FS] Detected message from OpenCode identity: {opencode_mxid}')
    else:
        room_display = room.display_name or room.room_id
        fs_sender_display = room.user_name(event.sender) if hasattr(room, 'user_name') else None
        fs_prompt = matrix_formatter.format_message_envelope(
            channel='Matrix',
            chat_id=room.room_id,
            message_id=getattr(event, 'event_id', None),
            sender=event.sender,
            sender_name=fs_sender_display or event.sender,
            timestamp=fs_event_timestamp,
            text=event.body,
            is_group=True,
            group_name=room_display,
            is_mentioned=False,
        )
        logger.debug(f'[MATRIX-FS] Added context for sender {event.sender}')

    if config.letta_code_enabled:
        try:
            await run_letta_code_task(
                room_id=room.room_id,
                agent_id=agent_id,
                agent_name=agent_name,
                project_dir=project_dir,
                prompt=fs_prompt,
                config=config,
                logger=logger,
                send_fn=send_as_agent,
                wrap_response=False,
            )
            return True
        except (
            LettaCodeApiError,
            LettaApiError,
            MatrixClientError,
            asyncio.TimeoutError,
            aiohttp.ClientError,
            RuntimeError,
            ValueError,
        ) as fs_err:
            logger.warning(
                f'[FS-FALLBACK] letta-code task failed ({fs_err}), falling back to streaming Letta API'
            )
    else:
        logger.info(
            '[FS-SKIP] letta_code_enabled=false, using streaming Letta API for fs-mode room'
        )

    return False
