#!/usr/bin/env python3
"""
Matrix Space Manager - Manages Matrix Spaces for organizing Letta agent rooms
"""
import asyncio
import logging
import os
import json
import time
import aiohttp
from typing import Dict, List, Optional

logger = logging.getLogger("matrix_client.space_manager")

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class MatrixSpaceManager:
    """Manages Matrix Spaces for Letta agents

    This class handles:
    - Creating the "Letta Agents" space
    - Adding agent rooms to the space
    - Migrating existing rooms to the space
    - Persisting space configuration
    """

    def __init__(
        self,
        homeserver_url: str,
        admin_username: str,
        admin_password: str,
        main_bot_username: str,
        space_config_file: str = "/app/data/letta_space_config.json"
    ):
        """Initialize the Matrix Space Manager

        Args:
            homeserver_url: Matrix homeserver URL (e.g., "https://matrix.example.com")
            admin_username: Admin user ID (e.g., "@admin:matrix.example.com")
            admin_password: Admin user password
            main_bot_username: Main Letta bot user ID (e.g., "@letta:matrix.example.com")
            space_config_file: Path to space configuration file
        """
        self.homeserver_url = homeserver_url
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.main_bot_username = main_bot_username
        self.space_config_file = space_config_file
        self.space_id: Optional[str] = None

        # Cache for admin token (to avoid repeated logins)
        self._admin_token: Optional[str] = None

        # Ensure data directory exists
        os.makedirs(os.path.dirname(self.space_config_file), exist_ok=True)

    async def get_admin_token(self) -> Optional[str]:
        """Get an admin access token by logging in as the admin user

        Returns:
            Admin access token or None if login failed
        """
        if self._admin_token:
            logger.debug("Using cached admin token")
            return self._admin_token

        try:
            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            username = self.admin_username.split(':')[0].replace('@', '')  # Extract just username

            login_data = {
                "type": "m.login.password",
                "user": username,
                "password": self.admin_password
            }

            logger.info(f"Attempting to get admin token for user: {username}")

            async with aiohttp.ClientSession() as session:
                async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._admin_token = data.get("access_token")
                        logger.info(f"Successfully obtained admin access token for user {username}")
                        return self._admin_token
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get admin token for {username}: {response.status} - {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error getting admin token: {e}")
            return None

    async def check_room_exists(self, room_id: str) -> bool:
        """Check if a room exists on the server

        Args:
            room_id: Matrix room ID to check

        Returns:
            True if room exists, False otherwise
        """
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot check room existence")
                return False

            # Use the room state API to check if room exists
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Room {room_id} exists")
                        return True
                    elif response.status == 404:
                        logger.info(f"Room {room_id} does not exist")
                        return False
                    elif response.status == 403:
                        # Room exists but we don't have access - still counts as existing
                        logger.info(f"Room {room_id} exists but access denied")
                        return True
                    else:
                        logger.warning(f"Unexpected response checking room {room_id}: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Error checking if room {room_id} exists: {e}")
            return False

    async def load_space_config(self):
        """Load the Letta Agents space configuration from file"""
        try:
            if os.path.exists(self.space_config_file):
                with open(self.space_config_file, 'r') as f:
                    data = json.load(f)
                    self.space_id = data.get("space_id")
                    logger.info(f"Loaded space configuration: {self.space_id}")
            else:
                logger.info("No existing space configuration found")
        except Exception as e:
            logger.error(f"Error loading space config: {e}")

    async def save_space_config(self):
        """Save the Letta Agents space configuration to file"""
        try:
            data = {
                "space_id": self.space_id,
                "created_at": time.time(),
                "name": "Letta Agents"
            }
            with open(self.space_config_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved space configuration: {self.space_id}")
        except Exception as e:
            logger.error(f"Error saving space config: {e}")

    async def create_letta_agents_space(self) -> Optional[str]:
        """Create the Letta Agents space if it doesn't exist

        Returns:
            Space ID if successful, None otherwise
        """
        try:
            # Check if we already have a space
            if self.space_id:
                # Verify it still exists
                exists = await self.check_room_exists(self.space_id)
                if exists:
                    logger.info(f"Letta Agents space already exists: {self.space_id}")
                    return self.space_id
                else:
                    logger.warning(f"Stored space {self.space_id} doesn't exist, creating new one")
                    self.space_id = None

            # Login as admin to create the space
            admin_login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            admin_username_local = self.admin_username.split(':')[0].replace('@', '')

            login_data = {
                "type": "m.login.password",
                "user": admin_username_local,
                "password": self.admin_password
            }

            async with aiohttp.ClientSession() as session:
                # Login
                async with session.post(admin_login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to login as admin to create space: {response.status} - {error_text}")
                        return None

                    auth_data = await response.json()
                    admin_token = auth_data.get("access_token")

                if not admin_token:
                    logger.error("No token received for admin user")
                    return None

                # Create the space
                space_url = f"{self.homeserver_url}/_matrix/client/r0/createRoom"

                # Invite key users to the space
                invites = [
                    "@admin:matrix.oculair.ca",
                    self.main_bot_username  # Main Letta bot
                ]

                space_data = {
                    "name": "Letta Agents",
                    "topic": "All Letta AI agents - organized by the Letta Matrix bridge",
                    "preset": "private_chat",
                    "invite": invites,
                    "power_level_content_override": {
                        "events": {
                            "m.space.child": 50  # Allow room moderators to add children
                        }
                    },
                    "creation_content": {
                        "type": "m.space"
                    },
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
                    "Authorization": f"Bearer {admin_token}",
                    "Content-Type": "application/json"
                }

                logger.info("Creating Letta Agents space")
                async with session.post(space_url, headers=headers, json=space_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        data = await response.json()
                        space_id = data.get("room_id")
                        logger.info(f"Created Letta Agents space: {space_id}")

                        # Store the space ID
                        self.space_id = space_id
                        await self.save_space_config()

                        return space_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create space: {response.status} - {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error creating Letta Agents space: {e}")
            return None

    async def add_room_to_space(self, room_id: str, room_name: str) -> bool:
        """Add a room as a child of the Letta Agents space

        Args:
            room_id: Matrix room ID to add to the space
            room_name: Display name of the room

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.space_id:
                logger.warning("No space ID available, cannot add room to space")
                return False

            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot add room to space")
                return False

            # Add the room as a child of the space
            url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{self.space_id}/state/m.space.child/{room_id}"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            child_data = {
                "via": ["matrix.oculair.ca"],
                "suggested": True,
                "order": room_name  # Use room name for alphabetical ordering
            }

            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=child_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Added room {room_id} ({room_name}) to Letta Agents space")

                        # Also add the space as a parent of the room (bidirectional relationship)
                        parent_url = f"{self.homeserver_url}/_matrix/client/r0/rooms/{room_id}/state/m.space.parent/{self.space_id}"
                        parent_data = {
                            "via": ["matrix.oculair.ca"],
                            "canonical": True
                        }

                        async with session.put(parent_url, headers=headers, json=parent_data, timeout=DEFAULT_TIMEOUT) as parent_response:
                            if parent_response.status == 200:
                                logger.info(f"Set space as parent of room {room_id}")
                            else:
                                logger.warning(f"Failed to set space as parent: {parent_response.status}")

                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to add room to space: {response.status} - {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error adding room {room_id} to space: {e}")
            return False

    async def migrate_existing_rooms_to_space(self, agent_mappings: Dict) -> int:
        """Migrate all existing agent rooms to the Letta Agents space

        Args:
            agent_mappings: Dictionary of agent ID to AgentUserMapping objects

        Returns:
            Number of rooms successfully migrated
        """
        if not self.space_id:
            logger.warning("No space ID available, cannot migrate rooms")
            return 0

        migrated_count = 0
        for agent_id, mapping in agent_mappings.items():
            if mapping.room_id and mapping.room_created:
                logger.info(f"Migrating room for agent {mapping.agent_name} to space")
                success = await self.add_room_to_space(mapping.room_id, mapping.agent_name)
                if success:
                    migrated_count += 1
                    logger.info(f"Successfully migrated room for {mapping.agent_name}")
                else:
                    logger.warning(f"Failed to migrate room for {mapping.agent_name}")

        logger.info(f"Migrated {migrated_count} existing rooms to space")
        return migrated_count

    def get_space_id(self) -> Optional[str]:
        """Get the current Letta Agents space ID

        Returns:
            Space ID or None if not set
        """
        return self.space_id
