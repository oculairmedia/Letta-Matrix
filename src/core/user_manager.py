#!/usr/bin/env python3
"""
Matrix User Manager - Manages Matrix user accounts
Extracted from agent_user_manager.py as part of Sprint 3 refactoring
"""
import logging
import os
import re
import secrets
import string
import aiohttp
from typing import Literal, Optional

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

    async def check_user_exists(self, username: str) -> Literal["exists_healthy", "exists_auth_failed", "not_found"]:
        """Check Matrix user state (Tuwunel compatible)

        Args:
            username: Matrix username (localpart only, without @domain)

        Returns:
            - exists_healthy: user exists and credentials are valid
            - exists_auth_failed: user exists but auth is failing (e.g., M_FORBIDDEN)
            - not_found: user does not exist
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
                        logger.debug(f"User {username} exists and accepted test password")
                        return "exists_healthy"
                    elif response.status == 403:
                        try:
                            error_data = await response.json()
                            errcode = error_data.get("errcode", "")
                            if errcode == "M_FORBIDDEN":
                                logger.debug(f"User {username} exists but auth failed (M_FORBIDDEN)")
                                return "exists_auth_failed"
                            elif errcode in {"M_UNKNOWN", "M_NOT_FOUND"}:
                                logger.debug(f"User {username} does not exist ({errcode})")
                                return "not_found"
                            else:
                                logger.debug(f"User {username} returned 403 with {errcode}; treating as auth failed")
                                return "exists_auth_failed"
                        except:
                            logger.debug(f"User {username} returned 403; treating as auth failed")
                            return "exists_auth_failed"
                    elif response.status == 404:
                        logger.debug(f"User {username} does not exist (404)")
                        return "not_found"
                    else:
                        logger.warning(f"Unexpected status {response.status} checking user {username}; treating as not_found")
                        return "not_found"

        except Exception as e:
            logger.error(f"Error checking if user {username} exists: {e}")
            return "not_found"

    async def create_matrix_user(self, username: str, password: str, display_name: str) -> bool:
        """Create a new Matrix user via registration API (Tuwunel compatible)

        Uses two-step registration with m.login.registration_token for Tuwunel.
        Falls back to m.login.dummy for Synapse compatibility.

        Args:
            username: Matrix username (localpart only, without @domain)
            password: Password for the new user
            display_name: Display name for the user

        Returns:
            True if user created successfully or already exists, False otherwise
        """
        try:
            url = f"{self.homeserver_url}/_matrix/client/v3/register"
            headers = {"Content-Type": "application/json"}

            # Step 1: Initiate registration to get session and available flows
            initial_data = {
                "username": username,
                "password": password
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=initial_data, timeout=DEFAULT_TIMEOUT) as response:
                    if response.status == 200:
                        # Registration succeeded without auth (unlikely but handle it)
                        logger.info(f"Created Matrix user: @{username}:matrix.oculair.ca")
                        result = await response.json()
                        user_token = result.get("access_token")
                        if user_token:
                            await self.set_user_display_name(f"@{username}:matrix.oculair.ca", display_name, user_token)
                        return True

                    elif response.status == 401:
                        # Expected: need to complete auth flow
                        auth_response = await response.json()
                        session_id = auth_response.get("session")
                        flows = auth_response.get("flows", [])

                        if not session_id:
                            logger.error(f"No session returned for user {username}")
                            return False

                        # Check what auth types are required
                        required_stages = []
                        for flow in flows:
                            required_stages.extend(flow.get("stages", []))

                        # Step 2: Complete registration with appropriate auth type
                        if "m.login.registration_token" in required_stages:
                            # Tuwunel requires registration token
                            registration_token = os.getenv("MATRIX_REGISTRATION_TOKEN")
                            if not registration_token:
                                logger.error(f"MATRIX_REGISTRATION_TOKEN not set, cannot create user {username}")
                                return False

                            complete_data = {
                                "username": username,
                                "password": password,
                                "auth": {
                                    "type": "m.login.registration_token",
                                    "token": registration_token,
                                    "session": session_id
                                }
                            }
                        else:
                            # Fallback to dummy auth (Synapse)
                            complete_data = {
                                "username": username,
                                "password": password,
                                "auth": {
                                    "type": "m.login.dummy",
                                    "session": session_id
                                }
                            }

                        async with session.post(url, headers=headers, json=complete_data, timeout=DEFAULT_TIMEOUT) as complete_response:
                            if complete_response.status == 200:
                                logger.info(f"Created Matrix user: @{username}:matrix.oculair.ca")
                                result = await complete_response.json()
                                user_token = result.get("access_token")
                                if user_token:
                                    await self.set_user_display_name(f"@{username}:matrix.oculair.ca", display_name, user_token)
                                return True
                            else:
                                error_text = await complete_response.text()
                                logger.error(f"Failed to complete registration for {username}: {complete_response.status} - {error_text}")
                                return False

                    elif response.status == 400:
                        error_data = await response.json()
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

    async def update_display_name(self, user_id: str, display_name: str, password: Optional[str] = None) -> bool:
        """Update the display name of a Matrix user by logging in as them

        Matrix doesn't allow admins to change other users' display names,
        so we must login as the user and use their token.

        Args:
            user_id: Full Matrix user ID (@user:domain)
            display_name: New display name
            password: User's password (required for login)

        Returns:
            True if successful, False otherwise
        """
        if not password:
            logger.warning(f"Cannot update display name for {user_id}: no password provided")
            return False

        try:
            async with aiohttp.ClientSession() as session:
                # Login as the user to get their token
                login_url = f"{self.homeserver_url}/_matrix/client/v3/login"
                login_data = {
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": user_id},
                    "password": password
                }

                async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as login_response:
                    if login_response.status != 200:
                        error_text = await login_response.text()
                        logger.error(f"Failed to login as {user_id}: {login_response.status} - {error_text}")
                        return False

                    login_result = await login_response.json()
                    user_token = login_result.get("access_token")
                    if not user_token:
                        logger.error(f"No access token returned for {user_id}")
                        return False

                # Set display name using user's own token
                profile_url = f"{self.homeserver_url}/_matrix/client/v3/profile/{user_id}/displayname"
                headers = {
                    "Authorization": f"Bearer {user_token}",
                    "Content-Type": "application/json"
                }

                async with session.put(profile_url, headers=headers, json={"displayname": display_name}, timeout=DEFAULT_TIMEOUT) as response:
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

    def generate_password(self, length: int = 16) -> str:
        """Generate a secure password for a Matrix user

        Args:
            length: Password length (default 16)
            
        Returns:
            Generated password (alphanumeric)
        """
        # Development override - use simple password if DEV_MODE is set
        if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
            return "password"

        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))

    def generate_agent_password(self, agent_id: str) -> str:
        """Generate a password for an agent user.
        
        Uses a deterministic prefix based on agent_id for easier debugging,
        combined with random characters for security.
        
        Args:
            agent_id: The agent's ID (e.g., "agent-b417b8da-84d2-40dd-97ad-3a35454934f7")
            
        Returns:
            Generated password in format "AgentPass_{short_id}_{random}!"
        """
        if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
            return "password"
        
        # Extract short ID from agent_id
        short_id = agent_id.replace("agent-", "")[:8]
        
        # Generate random suffix
        random_suffix = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        
        return f"AgentPass_{short_id}_{random_suffix}!"

    def generate_service_password(self, service_name: str) -> str:
        """Generate a password for a service user (bridge bots, etc).
        
        Args:
            service_name: Name of the service (e.g., "agent_mail_bridge")
            
        Returns:
            Generated password
        """
        if os.getenv("DEV_MODE", "").lower() in ["true", "1", "yes"]:
            return "password"
        
        # Generate secure random password with special chars for services
        random_part = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
        return f"{service_name}_{random_part}!"

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
                state = await self.check_user_exists(user_local)
                if state == "exists_healthy":
                    logger.info(f"Core user already exists: {full_user_id}")
                    continue

                if state == "exists_auth_failed":
                    # Note: check_user_exists uses a dummy password, so M_FORBIDDEN
                    # is the EXPECTED response for existing users. This is NOT a real
                    # auth failure. Real auth monitoring is handled by the cron-based
                    # health-check-auth.sh which tests with actual credentials.
                    logger.info(f"Core user exists (dummy-password check): {full_user_id}")
                    continue

                logger.info(f"Core user missing, creating: {full_user_id}")
                created = await self.create_matrix_user(user_local, password, display_name)
                if created:
                    logger.info(f"Successfully provisioned core user: {full_user_id}")
                else:
                    logger.error(f"Failed to provision core user: {full_user_id}")
            except Exception as e:
                logger.error(f"Error ensuring core user {full_user_id}: {e}")
