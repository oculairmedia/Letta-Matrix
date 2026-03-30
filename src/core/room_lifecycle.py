"""
Room lifecycle management — rename, leave, forget, and remove rooms from spaces.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("matrix_client.room_manager")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class RoomLifecycleMixin:
    """Lifecycle methods mixed into MatrixRoomManager."""

    async def update_room_name(self, room_id: str, new_name: str) -> bool:
        """Update the name of an existing room"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot update room name")
                return False

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.name"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            room_name_data = {
                "name": f"{new_name} - Letta Agent Chat"
            }

            session = await self._get_session()
            async with session.put(url, headers=headers, json=room_name_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Successfully updated room name for {room_id} to '{new_name} - Letta Agent Chat'")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to update room name: {response.status} - {error_text}")
                    return False

        except Exception as e:
            logger.error(f"Error updating room name for {room_id}: {e}")
            return False

    async def leave_room(self, room_id: str, access_token: str) -> bool:
        """Leave and forget a room using the provided access token."""
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            session = await self._get_session()
            leave_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/leave"
            async with session.post(leave_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Left room {room_id}")
                elif response.status == 403:
                    logger.debug(f"Already not in room {room_id} (403)")
                else:
                    error_text = await response.text()
                    logger.warning(f"Failed to leave room {room_id}: {response.status} - {error_text}")
                    return False

            forget_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/forget"
            async with session.post(forget_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Forgot room {room_id}")
                else:
                    logger.debug(f"Could not forget room {room_id}: {response.status}")

            return True
        except Exception as e:
            logger.error(f"Error leaving room {room_id}: {e}")
            return False

    async def leave_room_as_admin(self, room_id: str) -> bool:
        admin_token = await self.get_admin_token()
        if not admin_token:
            logger.warning("Cannot leave room — no admin token")
            return False
        return await self.leave_room(room_id, admin_token)

    async def leave_room_as_user(self, room_id: str, username: str, password: str) -> bool:
        try:
            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            login_data = {"type": "m.login.password", "user": username, "password": password}
            session = await self._get_session()
            async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    logger.warning(f"Cannot login as {username} to leave room {room_id}")
                    return False
                token = (await response.json()).get("access_token")
            if not token:
                return False
            return await self.leave_room(room_id, token)
        except Exception as e:
            logger.error(f"Error leaving room {room_id} as {username}: {e}")
            return False

    async def remove_room_from_space(self, room_id: str, space_id: str) -> bool:
        """Remove a child room from a space by sending empty m.space.child state."""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                return False
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{space_id}/state/m.space.child/{room_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }
            session = await self._get_session()
            async with session.put(url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"Removed room {room_id} from space {space_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.warning(f"Failed to remove room {room_id} from space: {response.status} - {error_text}")
                    return False
        except Exception as e:
            logger.error(f"Error removing room {room_id} from space {space_id}: {e}")
            return False
