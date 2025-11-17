#!/usr/bin/env python3
"""
Matrix Room Manager - Handles room creation and management
"""
import asyncio
import logging
import aiohttp
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger("matrix_client.room_manager")

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass
class AgentUserMapping:
    """Data class for agent-to-user mappings (shared with AgentUserManager)"""
    agent_id: str
    agent_name: str
    matrix_user_id: str
    matrix_password: str
    created: bool = False
    room_id: Optional[str] = None
    room_created: bool = False
    invitation_status: Optional[Dict[str, str]] = None


class MatrixRoomManager:
    """Manages Matrix rooms for Letta agents"""

    def __init__(
        self,
        homeserver_url: str,
        space_manager,
        user_manager,
        config,
        admin_username: str,
        get_admin_token_callback,
        save_mappings_callback
    ):
        """
        Initialize the room manager

        Args:
            homeserver_url: Matrix homeserver URL
            space_manager: MatrixSpaceManager instance
            user_manager: MatrixUserManager instance
            config: Configuration object with username/password
            admin_username: Admin username for invitations
            get_admin_token_callback: Async callback to get admin token
            save_mappings_callback: Async callback to save mappings
        """
        self.homeserver_url = homeserver_url
        self.space_manager = space_manager
        self.user_manager = user_manager
        self.config = config
        self.admin_username = admin_username
        self.get_admin_token = get_admin_token_callback
        self.save_mappings = save_mappings_callback

    async def update_room_name(self, room_id: str, new_name: str) -> bool:
        """Update the name of an existing room"""
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot update room name")
                return False

            # Use the room state API to update room name
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.name"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            room_name_data = {
                "name": f"{new_name} - Letta Agent Chat"
            }

            async with aiohttp.ClientSession() as session:
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

    async def find_existing_agent_room(self, agent_name: str) -> Optional[str]:
        """Find an existing room for an agent by searching room names"""
        # TEMPORARY: Always return None to force creation of new rooms
        return None

        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot search rooms")
                return None

            # Get list of rooms
            url = f"{self.homeserver_url}/_matrix/client/r0/joined_rooms"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status != 200:
                        logger.error(f"Failed to get joined rooms: {response.status}")
                        return None

                    data = await response.json()
                    room_ids = data.get("joined_rooms", [])

                # Check each room to see if it matches our agent
                expected_name = f"{agent_name} - Letta Agent Chat"
                for room_id in room_ids:
                    # Get room state to check name
                    state_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.room.name"
                    async with session.get(state_url, headers=headers) as state_response:
                        if state_response.status == 200:
                            state_data = await state_response.json()
                            room_name = state_data.get("name", "")
                            if room_name == expected_name:
                                logger.info(f"Found existing room for agent {agent_name}: {room_id}")
                                return room_id

                logger.info(f"No existing room found for agent {agent_name}")
                return None

        except Exception as e:
            logger.error(f"Error searching for agent room: {e}")
            return None

    async def create_or_update_agent_room(self, agent_id: str, mapping: AgentUserMapping):
        """Create or update a Matrix room for agent communication"""
        if not mapping or not mapping.created:
            logger.error(f"Cannot create room for agent {agent_id} - user not created")
            return None


        # Check if room already exists in our mapping and on the server
        if mapping.room_id and mapping.room_created:
            # Verify the room actually exists on the server
            room_exists = await self.space_manager.check_room_exists(mapping.room_id)
            if room_exists:
                logger.info(f"Room already exists for agent {mapping.agent_name}: {mapping.room_id}")
                # Ensure invitations are accepted
                await self.auto_accept_invitations_with_tracking(mapping.room_id, mapping)
                return
            else:
                logger.warning(f"Room {mapping.room_id} in mapping doesn't exist on server, checking for existing rooms")
                # Clear the invalid room info
                mapping.room_id = None
                mapping.room_created = False

        # Check if a room already exists for this agent on the server
        existing_room_id = await self.find_existing_agent_room(mapping.agent_name)
        if existing_room_id:
            logger.info(f"Found existing room for agent {mapping.agent_name}: {existing_room_id}")
            mapping.room_id = existing_room_id
            mapping.room_created = True
            await self.save_mappings()
            # Ensure invitations are accepted
            await self.auto_accept_invitations_with_tracking(existing_room_id, mapping)
            return

        try:
            # First, we need to login as the agent user to create the room
            agent_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            agent_username = mapping.matrix_user_id.split(':')[0].replace('@', '')

            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": mapping.matrix_password
            }

            # Login as the agent user
            async with aiohttp.ClientSession() as session:
                async with session.post(agent_login_url, json=login_data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to login as agent user {agent_username}: {response.status} - {error_text}")
                        return None

                    agent_auth = await response.json()
                    agent_token = agent_auth.get("access_token")

                if not agent_token:
                    logger.error(f"No access token received for agent user {agent_username}")
                    return None

                # Now create the room as the agent user (inside the session)
                room_url = f"{self.homeserver_url}/_matrix/client/r0/createRoom"

                # Define the users to invite: admin users and main letta bot
                invites = [
                    "@admin:matrix.oculair.ca",  # Your actual admin account
                    self.admin_username,  # Admin user (matrixadmin)
                    self.config.username  # Main Letta bot (@letta)
                ]

                room_data = {
                    "name": f"{mapping.agent_name} - Letta Agent Chat",
                    "topic": f"Private chat with Letta agent: {mapping.agent_name}",
                    "preset": "trusted_private_chat",  # Allows invited users to see history
                    "invite": invites,
                    "is_direct": False,
                    "initial_state": [
                        {
                            "type": "m.room.guest_access",
                            "state_key": "",
                            "content": {"guest_access": "forbidden"}
                        },
                        {
                            "type": "m.room.history_visibility",
                            "state_key": "",
                            "content": {"history_visibility": "shared"}
                        }
                    ]
                }

                headers = {
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json"
                }

                logger.info(f"Creating room as agent {agent_username} for {mapping.agent_name} with invites: {invites}")

                async with session.post(room_url, headers=headers, json=room_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        room_id = data.get("room_id")
                        logger.info(f"Created room {room_id} for agent {mapping.agent_name}")

                        # Update mapping with room info
                        mapping.room_id = room_id
                        mapping.room_created = True

                        # Initialize invitation status tracking
                        mapping.invitation_status = {user_id: "invited" for user_id in invites}

                        # Save updated mappings
                        await self.save_mappings()

                        # Add the room to the Letta Agents space
                        if self.space_manager.get_space_id():
                            logger.info(f"Adding room {room_id} to Letta Agents space")
                            space_success = await self.space_manager.add_room_to_space(room_id, mapping.agent_name)
                            if space_success:
                                logger.info(f"Successfully added room to space")
                            else:
                                logger.warning(f"Failed to add room to space")

                        # Now auto-accept the invitations for admin and letta users
                        await self.auto_accept_invitations_with_tracking(room_id, mapping)

                        # Import recent conversation history for UI continuity
                        logger.info(f"Importing recent history for agent {mapping.agent_name}")
                        await self.import_recent_history(
                            agent_id=agent_id,
                            agent_username=mapping.matrix_user_id,
                            agent_password=mapping.matrix_password,
                            room_id=room_id
                        )

                        return room_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create room for agent {mapping.agent_name}: {response.status} - {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error creating room for agent {agent_id}: {e}")
            return None

    async def auto_accept_invitations_with_tracking(self, room_id: str, mapping: AgentUserMapping):
        """Auto-accept room invitations for admin and letta users with status tracking"""
        users_to_accept = [
            (self.admin_username, self.user_manager.admin_password),
            (self.config.username, self.config.password)
        ]

        for username, password in users_to_accept:
            if not username or not password:
                continue

            try:
                # Login as the user
                login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
                user_local = username.split(':')[0].replace('@', '')

                login_data = {
                    "type": "m.login.password",
                    "user": user_local,
                    "password": password
                }

                async with aiohttp.ClientSession() as session:
                    # Login
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

                    # Accept the invitation
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
                        elif response.status == 403:
                            error_text = await response.text()
                            if "already in the room" in error_text or "already joined" in error_text:
                                logger.info(f"User {username} is already in room {room_id}")
                                if mapping.invitation_status:
                                    mapping.invitation_status[username] = "joined"
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

        # Save updated invitation status
        await self.save_mappings()

    async def import_recent_history(
        self,
        agent_id: str,
        agent_username: str,
        agent_password: str,
        room_id: str,
        limit: int = 15
    ):
        """Import recent Letta conversation history for UI continuity

        Args:
            agent_id: The Letta agent ID
            agent_username: Matrix username for the agent
            agent_password: Matrix password for the agent
            room_id: Matrix room ID to import messages into
            limit: Number of recent messages to import (default: 15, like letta-code)
        """
        try:
            # 1. Fetch recent messages from Letta proxy
            messages_url = f"http://192.168.50.90:8289/v1/agents/{agent_id}/messages"

            async with aiohttp.ClientSession() as session:
                async with session.get(messages_url, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status != 200:
                        logger.warning(f"Could not fetch history for agent {agent_id}: {response.status}")
                        return

                    data = await response.json()
                    # Handle both array and object responses
                    if isinstance(data, dict):
                        messages = data.get("items", [])
                    else:
                        messages = data

            if not messages:
                logger.info(f"No history to import for agent {agent_id}")
                return

            # 2. Take only last N messages (like letta-code does)
            recent_messages = messages[-limit:] if len(messages) > limit else messages

            # 3. Skip if starts with orphaned tool_return (incomplete turn)
            if recent_messages and recent_messages[0].get("message_type") == "tool_return_message":
                recent_messages = recent_messages[1:]

            if not recent_messages:
                logger.info(f"No valid history to import for agent {agent_id}")
                return

            # 4. Login as the agent to send historical messages
            from nio import AsyncClient, LoginResponse
            agent_client = AsyncClient(self.homeserver_url, agent_username)

            try:
                login_response = await agent_client.login(agent_password)

                if not isinstance(login_response, LoginResponse):
                    logger.error(f"Failed to login as {agent_username} for history import")
                    await agent_client.close()
                    return

                # 5. Send each message with historical flag
                imported_count = 0
                for msg in recent_messages:
                    msg_type = msg.get("message_type")

                    # Only import user and assistant messages (skip tool calls, reasoning, etc.)
                    if msg_type == "user_message":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Handle content array format
                            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                            content = " ".join(text_parts)

                        await agent_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={
                                "msgtype": "m.text",
                                "body": f"[History] {content}",
                                "m.letta_historical": True,  # Flag to prevent processing
                                "m.relates_to": {
                                    "rel_type": "m.annotation"  # Mark as annotation
                                }
                            }
                        )
                        imported_count += 1

                    elif msg_type == "assistant_message":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # Handle content array format
                            text_parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                            content = " ".join(text_parts)

                        await agent_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content={
                                "msgtype": "m.text",
                                "body": content,
                                "m.letta_historical": True,
                                "m.relates_to": {
                                    "rel_type": "m.annotation"
                                }
                            }
                        )
                        imported_count += 1

                logger.info(f"Imported {imported_count} historical messages for agent {agent_id}")

            finally:
                await agent_client.close()

        except Exception as e:
            logger.error(f"Error importing history for agent {agent_id}: {e}")
