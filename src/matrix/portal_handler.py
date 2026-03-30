"""
Portal room handling — passive observation and active request detection
for portal-linked rooms.
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from src.matrix.config import LettaApiError

logger = logging.getLogger("matrix_client")


def _is_portal_active_request(
    *,
    sender: str,
    message_text: str,
    formatted_body: str,
    room_agent_name: Optional[str],
    portal_link: Dict[str, Any],
    admin_username: str,
) -> bool:
    is_admin = sender == admin_username
    mention_enabled = portal_link.get('mention_enabled', False)
    is_relay = portal_link.get('relay_mode', False)

    contact_mentioned = bool(
        mention_enabled
        and room_agent_name
        and (
            f'@{room_agent_name.lower()}' in message_text.lower()
            or room_agent_name.lower() in message_text.lower()
            or room_agent_name.lower() in formatted_body.lower()
        )
    )

    return (is_admin and not is_relay) or contact_mentioned


async def _handle_passive_portal_message(
    room,
    event,
    config,
    logger: logging.Logger,
    room_agent_id: str,
    room_agent_name: str,
    message_text: str,
    triage_agent_id: Optional[str] = None,
) -> None:
    """Handle portal room messages in passive observation mode.

    Sends the contact message to Letta for processing (memory updates,
    actionable item detection) but does NOT send any response to the
    Matrix room. The agent observes silently.
    """
    import time as _time
    from src.matrix.letta_bridge import _get_gateway_client
    from src.matrix import formatter as matrix_formatter

    target_agent_id = triage_agent_id or room_agent_id

    event_source = getattr(event, 'source', None)
    event_timestamp = None
    if isinstance(event_source, dict):
        event_timestamp = event_source.get('origin_server_ts')
    if event_timestamp is None:
        event_timestamp = int(_time.time() * 1000)

    contact_display_name = room.user_name(event.sender) if hasattr(room, 'user_name') else None

    envelope = matrix_formatter.format_portal_contact_envelope(
        contact_sender=event.sender,
        room_name=room.display_name or room.room_id,
        chat_id=room.room_id,
        message_id=getattr(event, 'event_id', None),
        timestamp=event_timestamp,
        text=message_text,
        contact_display_name=contact_display_name,
    )

    if triage_agent_id:
        logger.info(
            f'[PORTAL-TRIAGE] Routing contact message from {event.sender} '
            f'in {room.display_name or room.room_id} to triage agent {triage_agent_id} '
            f'(primary agent: {room_agent_name})'
        )
    else:
        logger.info(
            f'[PORTAL-PASSIVE] Ingesting contact message from {event.sender} '
            f'in {room.display_name or room.room_id} for agent {room_agent_name}'
        )

    async def _send_to_letta():
        try:
            gateway_client = await _get_gateway_client(config, logger)
            from src.letta.gateway_stream_reader import collect_via_gateway

            result = await collect_via_gateway(
                client=gateway_client,
                agent_id=target_agent_id,
                message=envelope,
                source={'channel': 'portal', 'chatId': room.room_id},
            )
            resp_len = len(result) if result else 0
            log_prefix = '[PORTAL-TRIAGE]' if triage_agent_id else '[PORTAL-PASSIVE]'
            logger.info(
                f'{log_prefix} Agent processed contact message '
                f'({resp_len} chars response discarded)'
            )
        except LettaApiError as e:
            logger.warning(f'[PORTAL-PASSIVE] Letta API error (non-critical): {e}')
        except (LettaApiError, asyncio.TimeoutError, RuntimeError, ValueError, TypeError) as e:
            logger.warning(f'[PORTAL-PASSIVE] Failed to send to Letta (non-critical): {e}')

    asyncio.create_task(_send_to_letta())
