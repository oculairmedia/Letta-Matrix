from __future__ import annotations

import asyncio
import logging
import os
import secrets
import string
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import aiohttp

from src.core.identity_storage import IdentityStorageService, get_identity_service
from src.core.password_consistency import sync_agent_password_consistently
from src.core.user_manager import MatrixUserManager
from src.models.identity import Identity


logger = logging.getLogger(__name__)


@dataclass
class IdentityMonitorSummary:
    total: int = 0
    healthy: int = 0
    relogin_recovered: int = 0
    reset_recovered: int = 0
    failed: int = 0
    missing_password: int = 0


class IdentityTokenHealthMonitor:
    def __init__(
        self,
        homeserver_url: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        identity_service: Optional[IdentityStorageService] = None,
        user_manager: Optional[MatrixUserManager] = None,
    ) -> None:
        self.homeserver_url = homeserver_url or os.getenv(
            "MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca"
        )
        self.interval_seconds = interval_seconds or int(
            os.getenv("IDENTITY_TOKEN_HEALTH_INTERVAL_SECONDS", "900")
        )
        self.max_reset_retries = int(os.getenv("IDENTITY_TOKEN_RESET_RETRIES", "3"))
        self.admin_room_id = os.getenv(
            "MATRIX_ADMIN_ROOM_ID", "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca"
        )

        self.identity_service = identity_service or get_identity_service()

        if user_manager is None:
            admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
            admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "")
            user_manager = MatrixUserManager(
                homeserver_url=self.homeserver_url,
                admin_username=admin_username,
                admin_password=admin_password,
            )
        self.user_manager = user_manager

        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Started identity token health monitor (interval=%ss)",
            self.interval_seconds,
        )

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        logger.info("Stopped identity token health monitor")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                summary = await self.check_once()
                logger.info(
                    "Identity token health summary: total=%s healthy=%s relogin=%s reset=%s missing_password=%s failed=%s",
                    summary.total,
                    summary.healthy,
                    summary.relogin_recovered,
                    summary.reset_recovered,
                    summary.missing_password,
                    summary.failed,
                )
            except Exception as exc:
                logger.error("Identity token health check failed: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def check_once(self) -> IdentityMonitorSummary:
        identities = self.identity_service.get_all(active_only=True)
        summary = IdentityMonitorSummary(total=len(identities))

        for identity in identities:
            status = await self._check_identity(identity)
            if status == "healthy":
                summary.healthy += 1
            elif status == "relogin_recovered":
                summary.relogin_recovered += 1
            elif status == "reset_recovered":
                summary.reset_recovered += 1
            elif status == "missing_password":
                summary.missing_password += 1
            else:
                summary.failed += 1

        return summary

    async def ensure_identity_healthy(self, identity_id: str) -> bool:
        identity = self.identity_service.get(identity_id)
        if identity is None:
            logger.warning("Identity not found during health recovery: %s", identity_id)
            return False
        status = await self._check_identity(identity)
        return status in {"healthy", "relogin_recovered", "reset_recovered"}

    async def _check_identity(self, identity: Identity) -> str:
        identity_id = str(identity.id)
        identity_mxid = str(identity.mxid)
        is_valid = await self._validate_identity_token(identity)
        if is_valid:
            return "healthy"

        current_password = (
            str(identity.password_hash) if identity.password_hash is not None else ""
        )
        if not current_password:
            logger.warning(
                "Identity %s has invalid token and no stored password", identity_id
            )
            return "missing_password"

        relogin = await self._login_with_password(identity_mxid, current_password)
        if relogin:
            access_token, device_id = relogin
            self.identity_service.update(
                identity_id,
                access_token=access_token,
                device_id=device_id,
            )
            return "relogin_recovered"

        localpart = identity_mxid.split(":", 1)[0].replace("@", "")
        for attempt in range(1, self.max_reset_retries + 1):
            new_password = self._generate_password(localpart)
            reset_ok = await self._reset_password_via_admin_room(localpart, new_password)
            if not reset_ok:
                logger.warning(
                    "Password reset command failed for %s (attempt %s/%s)",
                    identity_id,
                    attempt,
                    self.max_reset_retries,
                )
                continue

            relogin_after_reset = await self._login_with_password(identity_mxid, new_password)
            if relogin_after_reset:
                access_token, device_id = relogin_after_reset
                updated_identity = self.identity_service.update(
                    identity_id,
                    access_token=access_token,
                    device_id=device_id,
                    password_hash=new_password,
                )
                if updated_identity is None:
                    logger.warning("Failed to update identity state for %s after reset", identity_id)
                    await asyncio.sleep(float(attempt))
                    continue

                if identity_id.startswith("letta_"):
                    agent_id = identity_id[6:]
                    synced = await sync_agent_password_consistently(
                        agent_id,
                        new_password,
                        identity_service=self.identity_service,
                    )
                    if not synced:
                        logger.warning(
                            "Password reset recovered token for %s but failed cross-store sync",
                            identity_id,
                        )
                        await asyncio.sleep(float(attempt))
                        continue
                return "reset_recovered"

            await asyncio.sleep(float(attempt))

        logger.error("Failed to recover identity %s after reset attempts", identity_id)
        return "failed"

    async def _validate_identity_token(self, identity: Identity) -> bool:
        headers = {"Authorization": f"Bearer {identity.access_token}"}
        url = f"{self.homeserver_url}/_matrix/client/v3/account/whoami"

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    if response.status in (401, 403):
                        try:
                            payload = await response.json()
                            errcode = payload.get("errcode")
                            if errcode == "M_UNKNOWN_TOKEN":
                                logger.warning(
                                    "Identity %s token invalid (M_UNKNOWN_TOKEN)",
                                    identity.id,
                                )
                            else:
                                logger.warning(
                                    "Identity %s token rejected: %s",
                                    identity.id,
                                    errcode or response.status,
                                )
                        except Exception:
                            logger.warning(
                                "Identity %s token rejected with HTTP %s",
                                identity.id,
                                response.status,
                            )
                    else:
                        logger.warning(
                            "Identity %s whoami check failed with HTTP %s",
                            identity.id,
                            response.status,
                        )
                    return False

                payload = await response.json()
                user_id = payload.get("user_id")
                if user_id != identity.mxid:
                    logger.warning(
                        "Identity %s token belongs to %s, expected %s",
                        identity.id,
                        user_id,
                        identity.mxid,
                    )
                    return False
                return True

    async def _login_with_password(
        self,
        mxid: str,
        password: str,
    ) -> Optional[Tuple[str, Optional[str]]]:
        url = f"{self.homeserver_url}/_matrix/client/v3/login"
        payload = {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": mxid},
            "password": password,
        }

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                token = data.get("access_token")
                if not token:
                    return None
                device_id = data.get("device_id")
                return token, device_id

    async def _reset_password_via_admin_room(
        self,
        localpart: str,
        new_password: str,
    ) -> bool:
        try:
            admin_token = await self.user_manager.get_admin_token()
        except Exception as exc:
            logger.error("Failed to obtain admin token for reset: %s", exc)
            return False

        if not admin_token:
            logger.error("No admin token available for identity password reset")
            return False

        command = f"!admin users reset-password {localpart} {new_password}"
        txn_id = int(time.time() * 1000)
        url = (
            f"{self.homeserver_url}/_matrix/client/v3/rooms/{self.admin_room_id}"
            f"/send/m.room.message/{txn_id}"
        )
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                url,
                headers=headers,
                json={"msgtype": "m.text", "body": command},
            ) as response:
                return response.status == 200

    @staticmethod
    def _generate_password(localpart: str) -> str:
        charset = string.ascii_letters + string.digits
        random_suffix = "".join(secrets.choice(charset) for _ in range(20))
        return f"IdentityRepair_{localpart}_{random_suffix}"


_monitor: Optional[IdentityTokenHealthMonitor] = None


def get_identity_token_health_monitor() -> IdentityTokenHealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = IdentityTokenHealthMonitor()
    return _monitor
