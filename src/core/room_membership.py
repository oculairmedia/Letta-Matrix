"""
Room membership management — check, invite, join, and ensure members.
"""

import asyncio
import logging
from typing import Dict, List, Optional

import aiohttp

from .types import AgentUserMapping

logger = logging.getLogger("matrix_client.room_manager")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class RoomMembershipMixin:
    """Membership methods mixed into MatrixRoomManager."""

    # Required members that should be in every agent room
    REQUIRED_ROOM_MEMBERS = [
        "@admin:matrix.oculair.ca",  # Admin user for oversight
        "@letta:matrix.oculair.ca",  # Main Letta bridge bot
    ]

    async def check_admin_in_room(self, room_id: str) -> bool:
        """Check if admin user is a member of the given room"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check room membership")
                return False

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    members = data.get("joined", {})
                    is_member = "@admin:matrix.oculair.ca" in members
                    logger.debug(f"Admin membership check for room {room_id}: {is_member}")
                    return is_member
                elif response.status == 403:
                    logger.debug(f"Admin not in room {room_id} (403 forbidden)")
                    return False
                else:
                    logger.warning(f"Failed to check room members: {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Error checking admin membership in room {room_id}: {e}")
            return False

    async def check_user_in_room(self, room_id: str, user_id: str) -> bool:
        """Check if a specific user is a member of the given room using Matrix API"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check room membership")
                return False

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    members = data.get("joined", {})
                    is_member = user_id in members
                    logger.debug(f"Membership check for {user_id} in room {room_id}: {is_member}")
                    return is_member
                elif response.status == 403:
                    logger.debug(f"User {user_id} not in room {room_id} (403 forbidden)")
                    return False
                else:
                    logger.warning(f"Failed to check room members: {response.status}")
                    return False

        except Exception as e:
            logger.error(f"Error checking membership for {user_id} in room {room_id}: {e}")
            return False

    async def get_room_members(self, room_id: str) -> List[str]:
        """Get list of all members in a room"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot get room members")
                return []

            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/joined_members"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    return list(data.get("joined", {}).keys())
                else:
                    logger.warning(f"Failed to get room members: {response.status}")
                    return []

        except Exception as e:
            logger.error(f"Error getting room members for {room_id}: {e}")
            return []

    async def ensure_required_members(self, room_id: str, agent_id: str) -> Dict[str, str]:
        """
        Ensure all required members are in the room.
        This invites missing members AND has them join the room.
        Returns dict of {user_id: status} where status is 'already_member', 'joined', or 'failed'
        """
        results = {}

        try:
            current_members = await self.get_room_members(room_id)

            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            mapping = db.get_by_agent_id(agent_id)

            if not mapping:
                logger.warning(f"Cannot ensure members for room {room_id} - no agent mapping found for {agent_id}")
                return {user: "failed" for user in self.REQUIRED_ROOM_MEMBERS}

            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')
            agent_password = str(mapping.matrix_password)

            session = await self._get_session()
            agent_token = await self._login_agent_with_recovery(
                session,
                agent_id,
                agent_username,
                agent_password,
            )
            if not agent_token:
                try:
                    from src.matrix.alerting import alert_auth_failure
                    await alert_auth_failure(agent_username, room_id)
                except Exception as alert_error:
                    logger.warning(f"Failed to send auth-failure alert for {agent_username}: {alert_error}")
                return {user: "failed" for user in self.REQUIRED_ROOM_MEMBERS}

            for required_user in self.REQUIRED_ROOM_MEMBERS:
                    if required_user in current_members:
                        results[required_user] = "already_member"
                        logger.debug(f"{required_user} already in room {room_id}")
                        continue

                    logger.info(f"Ensuring {required_user} is in room {room_id}")

                    # Step 1: Send invite from agent
                    invite_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/invite"
                    headers = {
                        "Authorization": f"Bearer {agent_token}",
                        "Content-Type": "application/json"
                    }
                    invite_data = {"user_id": required_user}

                    async with session.post(invite_url, headers=headers, json=invite_data, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status == 200:
                            logger.info(f"Invited {required_user} to room {room_id}")
                        elif response.status == 403:
                            error_text = await response.text()
                            if "already in the room" in error_text.lower():
                                results[required_user] = "already_member"
                                continue
                            # User might already be invited, continue to join attempt

                    # Step 2: Login as the required user and join
                    user_token = await self._get_service_user_token(session, required_user)
                    if not user_token:
                        logger.warning(f"Could not get token for {required_user}")
                        results[required_user] = "failed"
                        continue

                    join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
                    join_headers = {
                        "Authorization": f"Bearer {user_token}",
                        "Content-Type": "application/json"
                    }

                    async with session.post(join_url, headers=join_headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                        if response.status == 200:
                            logger.info(f"{required_user} joined room {room_id}")
                            results[required_user] = "joined"
                        elif response.status == 403:
                            error_text = await response.text()
                            if "already" in error_text.lower():
                                results[required_user] = "already_member"
                            else:
                                logger.warning(f"{required_user} failed to join: {error_text}")
                                results[required_user] = "failed"
                        else:
                            error_text = await response.text()
                            logger.warning(f"{required_user} failed to join: {response.status} - {error_text}")
                            results[required_user] = "failed"

            return results

        except Exception as e:
            logger.error(f"Error ensuring required members for room {room_id}: {e}")
            return {user: "failed" for user in self.REQUIRED_ROOM_MEMBERS}

    async def invite_admin_to_room(self, room_id: str, agent_name: str) -> bool:
        """Invite admin user to a room using the agent's credentials"""
        try:
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            mapping = db.get_by_room_id(room_id)

            if not mapping:
                logger.warning(f"Cannot invite admin to room {room_id} - no agent mapping found")
                return False

            agent_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')

            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": mapping.matrix_password
            }

            session = await self._get_session()
            async with session.post(agent_login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Failed to login as agent {agent_username} to invite admin: {response.status} - {error_text}")
                    return False

                agent_auth = await response.json()
                agent_token = agent_auth.get("access_token")

                if not agent_token:
                    logger.error(f"No token received for agent {agent_username}")
                    return False

                invite_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/invite"
                headers = {
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json"
                }

                invite_data = {
                    "user_id": "@admin:matrix.oculair.ca"
                }

                async with session.post(invite_url, headers=headers, json=invite_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"✅ Successfully invited admin to room {room_id} for agent {agent_name}")

                        await self._accept_invite_as_admin(room_id)
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to invite admin to room {room_id}: {response.status} - {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error inviting admin to room {room_id}: {e}")
            return False

    async def _accept_invite_as_admin(self, room_id: str) -> bool:
        """Accept room invitation as admin user"""
        try:
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot accept invitation")
                return False

            join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            session = await self._get_session()
            async with session.post(join_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    logger.info(f"✅ Admin successfully joined room {room_id}")
                    return True
                else:
                    error_text = await response.text()
                    logger.warning(f"Admin could not join room {room_id}: {response.status} - {error_text}")
                    return False

        except Exception as e:
            logger.error(f"Error accepting invitation for admin: {e}")
            return False

    async def auto_accept_invitations_with_tracking(self, room_id: str, mapping: AgentUserMapping):
        """Auto-accept room invitations for admin and letta users with status tracking"""
        users_to_accept = [
            (self.admin_username, self.user_manager.admin_password),
            (self.config.username, self.config.password)
        ]

        for username, password in users_to_accept:
            if not username or not password:
                continue

            cache_key = (room_id, username)
            if cache_key in self._membership_cache and self._membership_cache[cache_key]:
                logger.debug(f"User {username} already joined room {room_id} (cached)")
                if mapping.invitation_status:
                    mapping.invitation_status[username] = "joined"
                continue

            is_member = await self.check_user_in_room(room_id, username)
            if is_member:
                logger.info(f"User {username} already in room {room_id} (verified via API), updating cache")
                self._membership_cache[cache_key] = True
                if mapping.invitation_status:
                    mapping.invitation_status[username] = "joined"
                continue

            try:
                login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
                user_local = username.split(':')[0].replace('@', '')

                login_data = {
                    "type": "m.login.password",
                    "user": user_local,
                    "password": password
                }

                session = await self._get_session()
                async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status != 200:
                        logger.error(f"Failed to login as {username} to accept invitation")
                        if mapping.invitation_status:
                            mapping.invitation_status[username] = "failed"
                        continue

                    auth_data = await response.json()
                    user_token = auth_data.get("access_token")

                if not user_token:
                    logger.error(f"No token received for {username}")
                    if mapping.invitation_status:
                        mapping.invitation_status[username] = "failed"
                    continue

                join_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/join"
                headers = {
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json"
                }

                async with session.post(join_url, headers=headers, json={}, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"User {username} successfully joined room {room_id}")
                        if mapping.invitation_status:
                            mapping.invitation_status[username] = "joined"
                        self._membership_cache[cache_key] = True
                    elif response.status == 403:
                        error_text = await response.text()
                        if "already in the room" in error_text or "already joined" in error_text:
                            logger.info(f"User {username} is already in room {room_id}")
                            if mapping.invitation_status:
                                mapping.invitation_status[username] = "joined"
                            self._membership_cache[cache_key] = True
                        else:
                            logger.warning(f"User {username} forbidden from joining room {room_id}: {error_text}")
                            if mapping.invitation_status:
                                mapping.invitation_status[username] = "failed"
                    else:
                        error_text = await response.text()
                        logger.warning(f"User {username} could not join room {room_id}: {response.status} - {error_text}")
                        if mapping.invitation_status:
                            mapping.invitation_status[username] = "failed"

            except Exception as e:
                logger.error(f"Error accepting invitation for {username}: {e}")
                if mapping.invitation_status:
                    mapping.invitation_status[username] = "failed"
