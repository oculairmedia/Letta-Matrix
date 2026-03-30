"""
Message callback router — the core message routing logic for Matrix events.
"""

import os
import logging
from typing import Optional, Tuple

from nio import AsyncClient, RoomMessageText

from src.matrix.agent_actions import send_as_agent
from src.matrix.config import Config
from src.matrix.echo_filter import _is_streaming_progress, _is_no_text_fallback_echo
from src.matrix.event_dedupe import is_duplicate_event
from src.matrix.fs_mode_handler import _maybe_handle_fs_mode
from src.matrix.letta_code_service import handle_letta_code_command
from src.matrix.portal_handler import _is_portal_active_request, _handle_passive_portal_message
from src.matrix.task_manager import _dispatch_letta_task, _handle_stop_command

logger = logging.getLogger("matrix_client")


class _MessageCallbackRouter:
    def __init__(self, room, config: Config, logger: logging.Logger, client: Optional[AsyncClient]):
        self.room = room
        self.config = config
        self.logger = logger
        self.client = client
        self.room_agent_mapping = None
        self.sender_mapping = None
        self.portal_link = None

    def _should_skip_message(self, event, room_id) -> Optional[str]:
        event_id = getattr(event, 'event_id', None)
        if event_id and is_duplicate_event(event_id, self.logger):
            return 'duplicate'

        if self.client and event.sender == self.client.user_id:
            return 'self_message'

        source = getattr(event, 'source', None)
        source_content = {}
        if isinstance(source, dict):
            source_content = source.get('content', {})
            if source_content.get('m.letta_historical'):
                self.logger.debug(f'Ignoring historical message from {event.sender}')
                return 'historical'
            if source_content.get('m.bridge_originated'):
                self.logger.debug(f'Ignoring bridge-originated message from {event.sender}')
                return 'bridge_originated'

        body = (getattr(event, 'body', None) or source_content.get('body', '') or '')
        msgtype = source_content.get('msgtype')
        sender_is_machine = event.sender.startswith('@agent_') or event.sender.startswith('@oc_')
        bridge_like = bool(source_content.get('m.forwarded') or source_content.get('m.bridge_originated'))

        if msgtype == 'm.notice' and sender_is_machine:
            self.logger.debug(f'Ignoring bot/system m.notice from {event.sender}')
            return 'bot_notice'

        if _is_no_text_fallback_echo(body) and (sender_is_machine or bridge_like):
            self.logger.debug(f'Ignoring no-text fallback echo from {event.sender}')
            return 'no_text_fallback_echo'

        if _is_streaming_progress(body):
            self.logger.debug(f'Ignoring streaming progress echo from {event.sender}: {body[:80]}')
            return 'streaming_progress'

        disabled_agent_ids = [
            a.strip() for a in os.getenv('DISABLED_AGENT_IDS', '').split(',') if a.strip()
        ]
        if not disabled_agent_ids:
            return None

        from src.core.mapping_service import (
            get_mapping_by_room_id,
            get_mapping_by_agent_id,
            get_portal_link_by_room_id,
        )

        room_agent_mapping = get_mapping_by_room_id(room_id)
        if not room_agent_mapping:
            portal_link = get_portal_link_by_room_id(room_id)
            if portal_link:
                room_agent_mapping = get_mapping_by_agent_id(portal_link['agent_id'])

        if not room_agent_mapping:
            return None

        room_agent_id = room_agent_mapping.get('agent_id')
        room_agent_name = room_agent_mapping.get('agent_name', 'Unknown')
        if room_agent_id and room_agent_id in disabled_agent_ids:
            self.logger.debug(f'Skipping disabled agent {room_agent_id} ({room_agent_name})')
            return 'disabled_agent'
        return None

    async def _resolve_agent_for_room(self, room_id, event) -> Optional[Tuple]:
        from src.core.mapping_service import (
            get_mapping_by_room_id,
            get_mapping_by_matrix_user,
            get_mapping_by_agent_id,
            get_portal_link_by_room_id,
        )

        self.room_agent_mapping = get_mapping_by_room_id(room_id)
        if not self.room_agent_mapping:
            portal_link = get_portal_link_by_room_id(room_id)
            if portal_link:
                self.room_agent_mapping = get_mapping_by_agent_id(portal_link['agent_id'])
                if self.room_agent_mapping:
                    self.portal_link = portal_link
                    self.logger.info(
                        f'Portal link match: room {room_id} → agent {portal_link["agent_id"]}'
                    )

        room_agent_user_id = (
            self.room_agent_mapping.get('matrix_user_id') if self.room_agent_mapping else None
        )
        room_agent_id = self.room_agent_mapping.get('agent_id') if self.room_agent_mapping else None
        room_agent_name = (
            self.room_agent_mapping.get('agent_name', 'Unknown')
            if self.room_agent_mapping
            else None
        )

        self.sender_mapping = get_mapping_by_matrix_user(event.sender)
        if self.sender_mapping and self.sender_mapping.get('agent_id'):
            from src.matrix.mention_routing import handle_agent_mention_routing

            await handle_agent_mention_routing(
                room=self.room,
                event=event,
                sender_mxid=event.sender,
                sender_agent_id=self.sender_mapping['agent_id'],
                sender_agent_name=self.sender_mapping.get('agent_name', 'Unknown'),
                config=self.config,
                logger=self.logger,
                admin_client=self.client,
            )

        if room_agent_user_id and event.sender == room_agent_user_id:
            self.logger.debug(f"Ignoring message from room's own agent {event.sender}")
            return None

        if self.sender_mapping and event.sender != room_agent_user_id:
            self.logger.info(
                f'Received inter-agent message from {event.sender} in {self.room.display_name}'
            )

        if not self.room_agent_mapping:
            self.logger.debug(
                f'No agent mapping for room {room_id}, skipping message processing (relay room)'
            )
            return None

        return room_agent_id, room_agent_name, room_agent_user_id

    def _extract_message_content(self, event) -> Tuple[str, Optional[str]]:
        message_text = getattr(event, 'body', '') or ''
        reply_to_event_id = None
        source = getattr(event, 'source', None)
        if not isinstance(source, dict):
            return message_text, reply_to_event_id

        content = source.get('content', {})
        relates_to = content.get('m.relates_to', {})
        in_reply_to = relates_to.get('m.in_reply_to', {})
        if isinstance(in_reply_to, dict):
            reply_to_event_id = in_reply_to.get('event_id')
        if not reply_to_event_id:
            reply_to_event_id = relates_to.get('event_id')
        return message_text, reply_to_event_id

    def _is_silent_message(self, sender, agent_mappings) -> bool:
        if not sender or not agent_mappings:
            return False
        return any(sender == mapping.get('matrix_user_id') for mapping in agent_mappings if mapping)

    def _apply_group_gating(self, event, room_agent_user_id, message_text):
        if not self.config.matrix_groups:
            return False, None

        from src.matrix.group_gating import apply_group_gating

        event_source = getattr(event, 'source', None)
        gating_result = apply_group_gating(
            room_id=self.room.room_id,
            sender_id=event.sender,
            body=message_text,
            event_source=event_source if isinstance(event_source, dict) else None,
            bot_user_id=room_agent_user_id
            or (self.client.user_id if self.client else self.config.username),
            groups_config=self.config.matrix_groups,
        )
        if gating_result is None:
            self.logger.debug(
                f'[GROUP_GATING] Filtered message in {self.room.room_id} from {event.sender}'
            )
            return True, None

        self.logger.info(
            f'[GROUP_GATING] room={self.room.room_id} mode={gating_result.mode} '
            f'mentioned={gating_result.was_mentioned} method={gating_result.method} '
            f'silent={gating_result.silent}'
        )
        return False, gating_result


async def message_callback(
    room, event, config: Config, logger: logging.Logger, client: Optional[AsyncClient] = None,
    auth_manager=None,
):
    """Callback function for handling new text messages."""
    if not isinstance(event, RoomMessageText):
        return

    router = _MessageCallbackRouter(room=room, config=config, logger=logger, client=client)
    if router._should_skip_message(event, room.room_id):
        return

    resolved_agent = await router._resolve_agent_for_room(room.room_id, event)
    if not resolved_agent:
        return
    room_agent_id, room_agent_name, room_agent_user_id = resolved_agent

    # Portal rooms: check for @agent mention to activate, otherwise passive observation
    if router.portal_link:
        message_text, _ = router._extract_message_content(event)
        formatted_body = (
            event.source.get('content', {}).get('formatted_body', '')
            if getattr(event, 'source', None)
            else ''
        )
        active_request = _is_portal_active_request(
            sender=event.sender,
            message_text=message_text,
            formatted_body=formatted_body,
            room_agent_name=room_agent_name,
            portal_link=router.portal_link,
            admin_username=os.getenv('MATRIX_ADMIN_USERNAME', '@admin:matrix.oculair.ca'),
        )
        if not active_request:
            triage_id = router.portal_link.get('triage_agent_id') or None
            await _handle_passive_portal_message(
                room,
                event,
                config,
                logger,
                room_agent_id,
                room_agent_name,
                message_text,
                triage_agent_id=triage_id,
            )
            return
        logger.info(
            f'[PORTAL-ACTIVE] @{room_agent_name} mentioned in portal room '
            f'{room.display_name or room.room_id}, processing as active request'
        )

    message_text, reply_to_event_id = router._extract_message_content(event)
    _ = router._is_silent_message(event.sender, [router.sender_mapping])
    filtered, gating_result = router._apply_group_gating(event, room_agent_user_id, message_text)
    if filtered:
        return

    if await handle_letta_code_command(
        room,
        event,
        config,
        logger,
        send_fn=send_as_agent,
        agent_mapping=router.room_agent_mapping,
        agent_id_hint=room_agent_id,
        agent_name_hint=room_agent_name,
    ):
        return

    if await _maybe_handle_fs_mode(room, event, config, logger, room_agent_id, room_agent_name):
        return

    logger.info(
        'Received message from user',
        extra={
            'sender': event.sender,
            'room_name': room.display_name,
            'room_id': room.room_id,
            'message_preview': message_text[:100] + '...'
            if len(message_text) > 100
            else message_text,
        },
    )

    if await _handle_stop_command(
        room, config, logger, room_agent_id, room_agent_name, message_text
    ):
        return

    await _dispatch_letta_task(
        room,
        event,
        config,
        logger,
        client,
        room_agent_id,
        gating_result,
        message_text,
        reply_to_event_id,
        auth_manager=auth_manager,
    )
