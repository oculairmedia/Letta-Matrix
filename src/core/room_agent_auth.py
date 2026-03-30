"""
Agent authentication for rooms — login with recovery, password reset,
auth failure tracking, and service user token management.
"""

import asyncio
import logging
import secrets
import string
import time
from typing import Dict, Optional

import aiohttp

from src.core.password_consistency import sync_agent_password_consistently

logger = logging.getLogger("matrix_client.room_manager")

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)


class RoomAgentAuthMixin:
    """Agent auth methods mixed into MatrixRoomManager."""

    # Known service user passwords - these are reset via admin commands if login fails
    SERVICE_USER_PASSWORDS: Dict[str, str] = {}

    async def _get_service_user_token(self, session: aiohttp.ClientSession, user_id: str) -> Optional[str]:
        """Get access token for a service user, resetting password if needed."""
        username = user_id.split(':')[0].replace('@', '')

        # Try cached password first
        password = self.SERVICE_USER_PASSWORDS.get(user_id)
        if password:
            login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
            login_data = {"type": "m.login.password", "user": username, "password": password}
            async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("access_token")

        # Generate new password and reset via admin
        new_password = f"{username}_" + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12)) + "!"

        admin_token = await self.get_admin_token()
        if not admin_token:
            logger.warning(f"Cannot reset password for {username} - no admin token")
            return None

        from .admin_room import resolve_admin_room_id, AdminRoomResolutionError
        try:
            admin_room = await resolve_admin_room_id(access_token=admin_token, homeserver_url=self.homeserver_url)
        except AdminRoomResolutionError as exc:
            logger.warning("Cannot reset password for %s: %s", username, exc)
            return None
        txn_id = int(time.time() * 1000)
        url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{admin_room}/send/m.room.message/{txn_id}"
        headers = {"Authorization": f"Bearer {admin_token}"}
        command = f"!admin users reset-password {username} {new_password}"

        async with session.put(url, headers=headers, json={"msgtype": "m.text", "body": command}, timeout=DEFAULT_TIMEOUT) as response:
            if response.status != 200:
                logger.warning(f"Failed to send password reset command for {username}")
                return None

        # Wait for command to process
        await asyncio.sleep(0.5)

        # Try login with new password
        login_url = f"{self.homeserver_url}/_matrix/client/r0/login"
        login_data = {"type": "m.login.password", "user": username, "password": new_password}
        async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
            if response.status == 200:
                data = await response.json()
                self.SERVICE_USER_PASSWORDS[user_id] = new_password
                return data.get("access_token")

        logger.warning(f"Failed to login as {username} after password reset")
        return None

    def _current_time(self) -> float:
        return time.monotonic()

    def _agent_login_suppressed(self, agent_id: str, password: str) -> bool:
        if self._agent_auth_last_password.get(agent_id) != password:
            return False
        next_retry = self._agent_auth_next_retry_at.get(agent_id, 0.0)
        return self._current_time() < next_retry

    def _record_agent_auth_failure(
        self,
        agent_id: str,
        password: str,
        reason: str,
        status: int,
        agent_username: str,
    ) -> None:
        failure_count = self._agent_auth_failures.get(agent_id, 0) + 1
        self._agent_auth_failures[agent_id] = failure_count
        self._agent_auth_last_reason[agent_id] = reason
        self._agent_auth_last_status[agent_id] = status
        self._agent_auth_last_password[agent_id] = password
        self._agent_auth_next_retry_at[agent_id] = self._current_time() + self.agent_auth_cooldown_seconds

        logger.warning(
            "agent_auth_failure agent_id=%s agent_username=%s count=%s status=%s reason=%s cooldown_until=%.3f",
            agent_id,
            agent_username,
            failure_count,
            status,
            reason,
            self._agent_auth_next_retry_at[agent_id],
        )

    def _record_agent_auth_success(self, agent_id: str, password: str, agent_username: str) -> None:
        previous_failures = self._agent_auth_failures.get(agent_id, 0)
        self._agent_auth_failures[agent_id] = 0
        self._agent_auth_last_reason[agent_id] = "healthy"
        self._agent_auth_last_status[agent_id] = 200
        self._agent_auth_last_password[agent_id] = password
        self._agent_auth_next_retry_at[agent_id] = 0.0

        if previous_failures > 0:
            logger.info(
                "agent_auth_recovered agent_id=%s agent_username=%s previous_failures=%s",
                agent_id,
                agent_username,
                previous_failures,
            )

    async def _reset_agent_password_via_admin_room(
        self,
        session: aiohttp.ClientSession,
        agent_username: str,
        new_password: str,
    ) -> bool:
        admin_token = await self.get_admin_token()
        if not admin_token:
            return False

        from .admin_room import resolve_admin_room_id, AdminRoomResolutionError
        try:
            admin_room = await resolve_admin_room_id(access_token=admin_token, homeserver_url=self.homeserver_url)
        except AdminRoomResolutionError:
            return False
        txn_id = int(self._current_time() * 1000)
        url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{admin_room}/send/m.room.message/{txn_id}"
        headers = {"Authorization": f"Bearer {admin_token}"}
        command = f"!admin users reset-password {agent_username} {new_password}"

        async with session.put(
            url,
            headers=headers,
            json={"msgtype": "m.text", "body": command},
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status != 200:
                return False

        await asyncio.sleep(self.agent_auth_backoff_seconds)
        return True

    async def _login_agent_with_recovery(
        self,
        session: aiohttp.ClientSession,
        agent_id: str,
        agent_username: str,
        agent_password: str,
    ) -> Optional[str]:
        login_url = f"{self.homeserver_url}/_matrix/client/r0/login"

        if self._agent_login_suppressed(agent_id, agent_password):
            self._record_agent_auth_failure(
                agent_id,
                agent_password,
                "suppressed_by_cooldown",
                429,
                agent_username,
            )
            return None

        async def login_with_password(password: str) -> tuple[Optional[str], int, str]:
            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": password,
            }
            async with session.post(login_url, json=login_data, timeout=DEFAULT_TIMEOUT) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("access_token"), 200, ""
                body = await response.text()
                return None, response.status, body

        token, status, error_text = await login_with_password(agent_password)
        if token:
            self._record_agent_auth_success(agent_id, agent_password, agent_username)
            return token

        if status != 403 and "M_FORBIDDEN" not in error_text:
            self._record_agent_auth_failure(
                agent_id,
                agent_password,
                "login_failed_non_forbidden",
                status,
                agent_username,
            )
            return None

        for attempt in range(1, self.agent_auth_retry_limit + 1):
            new_password = self.user_manager.generate_agent_password(agent_id)
            reset_ok = await self._reset_agent_password_via_admin_room(
                session,
                agent_username,
                new_password,
            )
            if not reset_ok:
                await asyncio.sleep(self.agent_auth_backoff_seconds * attempt)
                continue

            synced = await sync_agent_password_consistently(agent_id, new_password)
            if not synced:
                await asyncio.sleep(self.agent_auth_backoff_seconds * attempt)
                continue

            token, _, _ = await login_with_password(new_password)
            if token:
                self._record_agent_auth_success(agent_id, new_password, agent_username)
                return token
            await asyncio.sleep(self.agent_auth_backoff_seconds * attempt)

        self._record_agent_auth_failure(
            agent_id,
            agent_password,
            "login_failed_after_recovery",
            403,
            agent_username,
        )
        return None
