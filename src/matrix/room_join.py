"""
Room creation and joining utilities.
"""

import asyncio
import logging

import aiohttp
from nio import RoomPreset
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

from src.matrix.config import MatrixClientError


async def create_room_if_needed(
    client_instance, logger: logging.Logger, room_name='Letta Bot Room'
):
    """Create a new room and return its ID"""
    logger.info('Creating new room', extra={'room_name': room_name})
    try:
        response = await client_instance.room_create(
            name=room_name,
            topic='Room for Letta bot interactions',
            preset=RoomPreset.public_chat,
            is_direct=False,
        )

        if hasattr(response, 'room_id'):
            logger.info('Successfully created room', extra={'room_id': response.room_id})
            return response.room_id
        else:
            logger.error('Failed to create room', extra={'response': str(response)})
            return None
    except (MatrixClientError, RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
        logger.error('Error creating room', extra={'error': str(e)}, exc_info=True)
        return None


async def join_room_if_needed(client_instance, room_id_or_alias, logger: logging.Logger):
    logger.info('Attempting to join room', extra={'room': room_id_or_alias})
    try:
        response = await client_instance.join(room_id_or_alias)

        if isinstance(response, JoinError):
            error_message = getattr(response, 'message', str(response))
            status_code = getattr(response, 'status_code', None)

            logger.error(
                'Failed to join room',
                extra={
                    'room': room_id_or_alias,
                    'error_message': error_message,
                    'status_code': status_code,
                },
            )

            if status_code == 'M_UNKNOWN' or "Can't join remote room" in error_message:
                logger.error(
                    "Configured room doesn't exist and auto-creation is disabled",
                    extra={
                        'room': room_id_or_alias,
                        'suggestion': 'Please ensure the room exists and the bot is invited, or update MATRIX_ROOM_ID in .env',
                    },
                )
            elif status_code == 'M_UNRECOGNIZED':
                logger.warning(
                    'Server did not recognize the join request',
                    extra={
                        'room': room_id_or_alias,
                        'details': 'This could be due to an invalid room alias or ID, or server-side issues',
                    },
                )
            elif status_code == 'M_FORBIDDEN':
                logger.warning(
                    'Bot not allowed to join room',
                    extra={
                        'room': room_id_or_alias,
                        'details': 'The bot may not be invited or allowed to join. Please check room permissions and invites',
                    },
                )
            return None
        elif hasattr(response, 'room_id') and response.room_id:
            logger.info('Successfully joined room', extra={'room_id': response.room_id})
            return response.room_id
        else:
            logger.error(
                'Unexpected response when joining room',
                extra={'room': room_id_or_alias, 'response': str(response)},
            )
            return None
    except RemoteProtocolError as e:
        if 'M_UNKNOWN_TOKEN' in str(e):
            logger.error(
                'Invalid token when joining room',
                extra={
                    'room': room_id_or_alias,
                    'error': str(e),
                    'details': 'The client might not be logged in correctly or the session is invalid',
                },
            )
        elif 'M_FORBIDDEN' in str(e):
            logger.error(
                'Forbidden when joining room',
                extra={
                    'room': room_id_or_alias,
                    'error': str(e),
                    'details': 'The bot may not be invited or allowed to join',
                },
            )
        else:
            logger.error(
                'Remote protocol error when joining room',
                extra={'room': room_id_or_alias, 'error': str(e)},
            )
        return None
    except (MatrixClientError, RemoteProtocolError, RuntimeError, ValueError, TypeError, asyncio.TimeoutError, aiohttp.ClientError) as e:
        logger.error(
            'Unexpected error when joining room',
            extra={'room': room_id_or_alias, 'error': str(e)},
            exc_info=True,
        )
        return None
