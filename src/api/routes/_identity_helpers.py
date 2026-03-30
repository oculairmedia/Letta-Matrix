"""
Shared helpers for identity routes — HTTP session, profile sync,
provisioning auth, and validation utilities.
"""

import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import aiohttp
from fastapi import HTTPException

from src.core.identity_health_monitor import get_identity_token_health_monitor
from src.core.identity_storage import get_identity_service
from src.core.user_manager import MatrixUserManager

logger = logging.getLogger(__name__)

_IDENTITY_HTTP_SESSION: Optional[aiohttp.ClientSession] = None
_IDENTITY_HTTP_SESSION_LOCK = asyncio.Lock()


async def _get_identity_http_session() -> aiohttp.ClientSession:
    global _IDENTITY_HTTP_SESSION
    if _IDENTITY_HTTP_SESSION is not None and not _IDENTITY_HTTP_SESSION.closed:
        return _IDENTITY_HTTP_SESSION

    async with _IDENTITY_HTTP_SESSION_LOCK:
        if _IDENTITY_HTTP_SESSION is not None and not _IDENTITY_HTTP_SESSION.closed:
            return _IDENTITY_HTTP_SESSION
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=50,
            ttl_dns_cache=300,
            keepalive_timeout=30,
        )
        _IDENTITY_HTTP_SESSION = aiohttp.ClientSession(connector=connector)
        return _IDENTITY_HTTP_SESSION


@asynccontextmanager
async def _identity_http_session_scope(
    session: Optional[aiohttp.ClientSession] = None,
) -> AsyncIterator[aiohttp.ClientSession]:
    if session is not None:
        yield session
        return
    pooled_session = await _get_identity_http_session()
    yield pooled_session


async def _sync_identity_profile(identity_id: str, display_name: Optional[str]) -> None:
    if display_name is None:
        return

    service = get_identity_service()
    identity = service.get(identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found")

    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    admin_username = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("MATRIX_ADMIN_PASSWORD", "")
    user_manager = MatrixUserManager(homeserver_url, admin_username, admin_password)

    monitor = get_identity_token_health_monitor()
    token_ready = await monitor.ensure_identity_healthy(identity_id)

    refreshed_identity = service.get(identity_id)
    if not refreshed_identity:
        raise HTTPException(status_code=404, detail=f"Identity {identity_id} not found after refresh")

    sync_success = False
    if token_ready and refreshed_identity.access_token is not None:
        sync_success = await user_manager.set_user_display_name(
            str(refreshed_identity.mxid),
            display_name,
            str(refreshed_identity.access_token),
        )

    if not sync_success and refreshed_identity.password_hash is not None:
        sync_success = await user_manager.update_display_name(
            str(refreshed_identity.mxid),
            display_name,
            str(refreshed_identity.password_hash),
        )
        if sync_success:
            await monitor.ensure_identity_healthy(identity_id)

    if not sync_success:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync Matrix profile display name for {identity_id}",
        )


def _sanitize_letta_name(name: str, remove_legacy_huly_prefix: bool) -> str:
    if remove_legacy_huly_prefix and name.startswith("Huly - "):
        cleaned = name[7:].strip()
        return cleaned if cleaned else name
    return name


async def _get_matrix_display_name(
    mxid: str,
    access_token: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    url = f"{homeserver_url}/_matrix/client/v3/profile/{mxid}/displayname"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with _identity_http_session_scope(session) as active_session:
            async with active_session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json()
                value = payload.get("displayname")
                return str(value) if value is not None else None
    except Exception:
        return None


async def _get_room_name(
    room_id: str,
    access_token: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")
    url = f"{homeserver_url}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with _identity_http_session_scope(session) as active_session:
            async with active_session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json()
                value = payload.get("name")
                return str(value) if value is not None else None
    except Exception:
        return None


async def _provision_login(
    homeserver_url: str,
    localpart: str,
    password: str,
    retries: int = 1,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    login_url = f"{homeserver_url}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": localpart},
        "password": password,
    }

    for attempt in range(1, retries + 1):
        try:
            async with _identity_http_session_scope(session) as active_session:
                async with active_session.post(login_url, json=login_data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        token = result.get("access_token")
                        return str(token) if token is not None else None
        except Exception as exc:
            logger.debug("Provision login failed (attempt %s/%s): %s", attempt, retries, exc)
        if attempt < retries:
            await asyncio.sleep(float(attempt))

    return None


async def _send_admin_password_reset_command(
    user_manager: MatrixUserManager,
    homeserver_url: str,
    localpart: str,
    password: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> bool:
    command = f"!admin users reset-password {localpart} {password}"

    for token_attempt in range(1, 3):
        try:
            admin_token = await user_manager.get_admin_token()
        except Exception as exc:
            logger.warning("Failed to obtain admin token for provisioning reset (attempt %s): %s", token_attempt, exc)
            user_manager.clear_admin_token_cache()
            continue
        if not admin_token:
            user_manager.clear_admin_token_cache()
            continue

        from src.core.admin_room import resolve_admin_room_id, AdminRoomResolutionError
        try:
            admin_room_id = await resolve_admin_room_id(
                access_token=admin_token, homeserver_url=homeserver_url
            )
        except AdminRoomResolutionError as exc:
            logger.warning("Cannot resolve admin room for provisioning reset: %s", exc)
            return False
        url = f"{homeserver_url}/_matrix/client/v3/rooms/{admin_room_id}/send/m.room.message/{int(time.time() * 1000)}"

        headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        }
        try:
            async with _identity_http_session_scope(session) as active_session:
                async with active_session.put(
                    url,
                    headers=headers,
                    json={"msgtype": "m.text", "body": command},
                ) as response:
                    if response.status == 200:
                        return True
                    if response.status in (401, 403):
                        user_manager.clear_admin_token_cache()
                        continue
                    return False
        except Exception as exc:
            logger.warning("Provisioning reset command failed (attempt %s): %s", token_attempt, exc)
            user_manager.clear_admin_token_cache()

    return False


async def _reset_password_and_verify_login(
    user_manager: MatrixUserManager,
    homeserver_url: str,
    localpart: str,
    password: str,
    max_attempts: int = 3,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[str]:
    for attempt in range(1, max_attempts + 1):
        reset_ok = await _send_admin_password_reset_command(
            user_manager,
            homeserver_url,
            localpart,
            password,
            session=session,
        )
        if not reset_ok:
            continue

        access_token = await _provision_login(
            homeserver_url,
            localpart,
            password,
            retries=1,
            session=session,
        )
        if access_token:
            return access_token

        if attempt < max_attempts:
            await asyncio.sleep(float(attempt))

    return None


def _is_valid_mxid(mxid: str) -> bool:
    return bool(re.match(r"^@[^:]+:[^:]+$", mxid))
