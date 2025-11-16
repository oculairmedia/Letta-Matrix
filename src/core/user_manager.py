#!/usr/bin/env python3
"""
Matrix User Manager - Manages Matrix user accounts
Extracted from agent_user_manager.py as part of Sprint 3 refactoring
"""
import logging
import os
import re
import aiohttp
from typing import Optional

logger = logging.getLogger("matrix_client.user_manager")

# Default timeout for all requests
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class MatrixUserManager:
    """Manages Matrix user accounts - creation, authentication, and profile management"""

    def __init__(self, homeserver_url: str, admin_username: str, admin_password: str):
        """Initialize the user manager

        Args:
            homeserver_url: Matrix homeserver URL
            admin_username: Admin user for privileged operations (format: @user:domain or just user)
            admin_password: Admin user password
        """
        self.homeserver_url = homeserver_url
        self.admin_username = admin_username
        self.admin_password = admin_password
        self.admin_token = None  # Cached admin token

        logger.info(f"Initialized MatrixUserManager with homeserver: {homeserver_url}")
        logger.info(f"Using admin account: {admin_username}")

    async def get_admin_token(self) -> Optional[str]:
        """Get an admin access token by logging in as the admin user

        Returns:
            Admin access token if successful, None otherwise
        """
        if self.admin_token:
            logger.debug("Using cached admin token")
            return self.admin_token

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
                        self.admin_token = data.get("access_token")
                        logger.info(f"Successfully obtained admin access token for user {username}")
                        return self.admin_token
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to get admin token for {username}: {response.status} - {error_text}")
                        return None

        except Exception as e:
            logger.error(f"Error getting admin token: {e}")
            return None

    async def check_user_exists(self, username: str) -> bool:
        """Check if a Matrix user exists (Tuwunel compatible)

        Args:
            username: Matrix username (localpart only, without @domain)

        Returns:
            True if user exists, False otherwise
        """
        try:
            # Try to login - if it fails with wrong password, user exists
            # If it fails with unknown user, user doesn't exist
            url = f"{self.homeserver_url}/_matrix/client/v3/login"

            headers = {"Content-Type": "application/json"}

            # Use a dummy password - we're just checking existence
            data = {
                "type": "m.login.password",
                "identifier": {
                    "type": "m.id.user",
                    "user": f"@{username}:matrix.oculair.ca"
                },
                "password": "dummy_check_password_12345"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        # Somehow logged in with dummy password (shouldn't happen)
                        return True
                    elif response.status == 403:
                        # Wrong password = user exists
                        return True
                    elif response.status == 404:
                        # User not found
                        return False
                    else:
                        # Assume user doesn't exist for other errors
                        return False

        except Exception as e:
            logger.error(f"Error checking if user {username} exists: {e}")
            return False

    async def create_matrix_user(self, username: str, password: str, display_name: str) -> bool:
        """Create a new Matrix user via registration API (Tuwunel compatible)

        Args:
            username: Matrix username (localpart only, without @domain)
            password: Password for the new user
            display_name: Display name for the user

        Returns:
            True if user created successfully or already exists, False otherwise
        """
        try:
            # Use standard Matrix registration API (works with both Synapse and Tuwunel)
            url = f"{self.homeserver_url}/_matrix/client/v3/register"

            headers = {
                "Content-Type": "application/json"
            }

            data = {
                "username": username,
                "password": password,
                "auth": {"type": "m.login.dummy"}
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Created Matrix user: @{username}:matrix.oculair.ca")

                        # Set display name after registration
                        result = await response.json()
                        user_token = result.get("access_token")
                        if user_token:
                            await self.set_user_display_name(f"@{username}:matrix.oculair.ca", display_name, user_token)

                        return True
                    elif response.status == 400:
                        error_data = await response.json()
                        # User already exists
                        if error_data.get("errcode") == "M_USER_IN_USE":
                            logger.info(f"Matrix user already exists: @{username}:matrix.oculair.ca")
                            return True
                        else:
                            error_text = await response.text()
                            logger.error(f"Failed to create user {username}: {response.status} - {error_text}")
                            return False
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to create user {username}: {response.status} - {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error creating Matrix user {username}: {e}")
            return False

    async def set_user_display_name(self, user_id: str, display_name: str, access_token: str) -> bool:
        """Set display name for a user using their own access token

        Args:
            user_id: Full Matrix user ID (@user:domain)
            display_name: Display name to set
            access_token: User's access token

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.homeserver_url}/_matrix/client/v3/profile/{user_id}/displayname"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            data = {"displayname": display_name}

            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Set display name for {user_id}: {display_name}")
                        return True
                    else:
                        logger.warning(f"Failed to set display name: {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Error setting display name: {e}")
            return False

    async def update_display_name(self, user_id: str, display_name: str) -> bool:
        """Update the display name of a Matrix user using admin privileges

        Args:
            user_id: Full Matrix user ID (@user:domain)
            display_name: New display name

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get admin token
            admin_token = await self.get_admin_token()
            if not admin_token:
                logger.warning("Failed to get admin token, cannot update display name")
                return False

            # Use the profile API to update display name
            url = f"{self.homeserver_url}/_matrix/client/r0/profile/{user_id}/displayname"
            headers = {
                "Authorization": f"Bearer {admin_token}",
                "Content-Type": "application/json"
            }

            display_name_data = {
                "displayname": display_name
            }

            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=headers, json=display_name_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        logger.info(f"Successfully updated display name for {user_id} to '{display_name}'")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to update display name: {response.status} - {error_text}")
                        return False

        except Exception as e:
            logger.error(f"Error updating display name for {user_id}: {e}")
            return False

    def generate_username(self, agent_name: str, agent_id: str) -> str:
        """Generate a safe Matrix username from agent ID

        Args:
            agent_name: Agent display name (not used for generation, kept for signature compatibility)
            agent_id: Agent ID (used as base for username)

        Returns:
            Safe Matrix username (localpart only)
        """
        # Use the agent ID as the base for the username
        # This ensures the username is stable even if the agent is renamed
        # Format: agent-{uuid} -> agent_{uuid with underscores}

        # Remove 'agent-' prefix if present and replace hyphens with underscores
        if agent_id.startswith("agent-"):
            clean_id = agent_id[6:]  # Remove 'agent-' prefix
        else:
            clean_id = agent_id

        # Replace hyphens with underscores for Matrix compatibility
        clean_id = clean_id.replace('-', '_')

        # Ensure it only contains valid characters
        clean_id = re.sub(r'[^a-zA-Z0-9_]', '', clean_id)

        # Create username as 'agent_{id}'
        username = f"agent_{clean_id}"

        return username

    def generate_password(self) -> str:
        """Generate a secure password for a Matrix user

        Returns:
            Generated password (16 characters, alphanumeric)
        """
        # Development override - use simple password if DEV_MODE is set
        if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
            return "password"

        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(16))

    async def ensure_core_users_exist(self, core_users: list):
        """Ensure required core Matrix users exist (idempotent bootstrap)

        Args:
            core_users: List of tuples (full_user_id, password, display_name)
                       e.g., [("@admin:matrix.oculair.ca", "pass", "Admin")]
        """
        for full_user_id, password, display_name in core_users:
            try:
                # Extract localpart from '@user:domain'
                user_local = full_user_id.split(":")[0].replace("@", "")
                exists = await self.check_user_exists(user_local)
                if exists:
                    logger.info(f"Core user already exists: {full_user_id}")
                    continue

                logger.info(f"Core user missing, creating: {full_user_id}")
                created = await self.create_matrix_user(user_local, password, display_name)
                if created:
                    logger.info(f"Successfully provisioned core user: {full_user_id}")
                else:
                    logger.error(f"Failed to provision core user: {full_user_id}")
            except Exception as e:
                logger.error(f"Error ensuring core user {full_user_id}: {e}")
