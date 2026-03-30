import asyncio
import logging
import os
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_ADMIN_ALIAS = "#admins"
_CACHE_TTL_SECONDS = 3600

_cached_room_id: Optional[str] = None
_cached_at: float = 0.0
_lock = asyncio.Lock()


def _server_name() -> str:
    return os.environ.get("MATRIX_SERVER_NAME", "matrix.oculair.ca")


def _homeserver_url() -> str:
    return os.environ.get("MATRIX_HOMESERVER_URL", "https://matrix.oculair.ca")


def _env_fallback() -> Optional[str]:
    return os.environ.get("MATRIX_ADMIN_ROOM_ID") or None


async def resolve_admin_room_id(
    access_token: Optional[str] = None,
    *,
    homeserver_url: Optional[str] = None,
) -> str:
    global _cached_room_id, _cached_at

    now = time.monotonic()
    if _cached_room_id and (now - _cached_at) < _CACHE_TTL_SECONDS:
        return _cached_room_id

    async with _lock:
        if _cached_room_id and (now - _cached_at) < _CACHE_TTL_SECONDS:
            return _cached_room_id

        hs = homeserver_url or _homeserver_url()
        alias = f"{_ADMIN_ALIAS}:{_server_name()}"
        encoded_alias = alias.replace("#", "%23").replace(":", "%3A")
        url = f"{hs}/_matrix/client/v3/directory/room/{encoded_alias}"

        if access_token:
            resolved = await _resolve_via_api(url, access_token)
            if resolved:
                _cached_room_id = resolved
                _cached_at = time.monotonic()
                logger.info("Resolved admin room alias %s -> %s", alias, resolved)
                return resolved

        fallback = _env_fallback()
        if fallback:
            _cached_room_id = fallback
            _cached_at = time.monotonic()
            logger.info(
                "Using MATRIX_ADMIN_ROOM_ID env fallback: %s", fallback
            )
            return fallback

        raise AdminRoomResolutionError(
            f"Cannot resolve admin room: alias {alias} failed and "
            "MATRIX_ADMIN_ROOM_ID not set"
        )


async def _resolve_via_api(url: str, access_token: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    room_id = data.get("room_id")
                    if isinstance(room_id, str) and room_id.startswith("!"):
                        return room_id
                else:
                    text = await resp.text()
                    logger.warning(
                        "Admin room alias resolution failed: %s %s",
                        resp.status,
                        text[:200],
                    )
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        logger.warning("Admin room alias resolution error: %s", exc)
    return None


def invalidate_cache() -> None:
    global _cached_room_id, _cached_at
    _cached_room_id = None
    _cached_at = 0.0


class AdminRoomResolutionError(RuntimeError):
    pass
