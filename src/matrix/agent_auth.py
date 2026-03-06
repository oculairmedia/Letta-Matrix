"""
Agent authentication and token management helpers.
"""

import asyncio
import logging
from typing import Optional

import aiohttp

from src.matrix.config import Config


_AGENT_LOGIN_TIMEOUT = aiohttp.ClientTimeout(total=10)


# Cache to avoid repeated repair attempts for the same agent within a session
_repair_attempted: set = set()


async def get_agent_token(
    room_id: str,
    config: Config,
    logger: logging.Logger,
    session: aiohttp.ClientSession,
    caller: str = "",
) -> Optional[str]:
    """
    Look up agent mapping for a room, login as the agent user, return access token.
    Returns None on any failure (logs the issue).
    """
    from src.core.mapping_service import (
        get_mapping_by_room_id,
        get_mapping_by_agent_id,
        get_portal_link_by_room_id,
    )

    agent_mapping = get_mapping_by_room_id(room_id)
    if not agent_mapping:
        portal_link = get_portal_link_by_room_id(room_id)
        if portal_link:
            agent_mapping = get_mapping_by_agent_id(portal_link["agent_id"])
    if not agent_mapping:
        logger.warning(f"[{caller}] No agent mapping for room {room_id}")
        return None

    agent_username = agent_mapping["matrix_user_id"].split(":")[0].replace("@", "")
    agent_password = agent_mapping["matrix_password"]

    login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
    login_data = {
        "type": "m.login.password",
        "user": agent_username,
        "password": agent_password,
    }

    try:
        async with session.post(
            login_url, json=login_data, timeout=_AGENT_LOGIN_TIMEOUT
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(
                    f"[{caller}] Login failed for {agent_username}: {resp.status} - {error_text}"
                )
                # Self-healing: if password is wrong, re-register with a new one
                if resp.status == 403 and "M_FORBIDDEN" in error_text:
                    repaired = await repair_agent_password(agent_mapping, config, logger, caller)
                    if repaired:
                        # Retry login with repaired password
                        login_data["password"] = repaired
                        async with session.post(
                            login_url, json=login_data, timeout=_AGENT_LOGIN_TIMEOUT
                        ) as retry_resp:
                            if retry_resp.status == 200:
                                retry_data = await retry_resp.json()
                                token = retry_data.get("access_token")
                                if token:
                                    logger.info(
                                        f"[{caller}] Password repair succeeded for {agent_username}"
                                    )
                                    return token
                return None
            auth_data = await resp.json()
            token = auth_data.get("access_token")
            if not token:
                logger.error(
                    f"[{caller}] No access_token in login response for {agent_username}"
                )
                return None
            return token
    except asyncio.TimeoutError:
        logger.error(f"[{caller}] Login timed out for {agent_username}")
        return None
    except Exception as e:
        logger.error(f"[{caller}] Login exception for {agent_username}: {e}")
        return None


async def repair_agent_password(
    agent_mapping: dict,
    config: Config,
    logger: logging.Logger,
    caller: str = "",
) -> Optional[str]:
    """
    Self-healing: reset agent Matrix user password via Tuwunel admin room command.
    Updates the mapping DB so future logins use the new password.
    Returns the new password on success, None on failure.
    """
    agent_id = agent_mapping.get("agent_id", "")
    if agent_id in _repair_attempted:
        return None  # Already tried this session, don't loop
    _repair_attempted.add(agent_id)

    agent_username = agent_mapping["matrix_user_id"].split(":")[0].replace("@", "")
    agent_name = agent_mapping.get("agent_name", agent_username)

    try:
        import os
        import secrets
        import string
        import time

        # Generate a new password
        charset = string.ascii_letters + string.digits
        new_password = "AgentRepair_" + "".join(secrets.choice(charset) for _ in range(20))

        # Admin credentials
        admin_user = os.getenv("MATRIX_ADMIN_USERNAME", os.getenv("MATRIX_USERNAME", "admin"))
        admin_pass = os.getenv("MATRIX_ADMIN_PASSWORD", os.getenv("MATRIX_PASSWORD", ""))
        admin_room = "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca"

        async with aiohttp.ClientSession() as http:
            # Login as admin to get token
            admin_username_short = admin_user.split(":")[0].replace("@", "")
            login_resp = await http.post(
                f"{config.homeserver_url}/_matrix/client/r0/login",
                json={"type": "m.login.password", "user": admin_username_short, "password": admin_pass},
                timeout=_AGENT_LOGIN_TIMEOUT,
            )
            if login_resp.status != 200:
                logger.error(f"[{caller}] Password repair: admin login failed ({login_resp.status})")
                return None
            admin_token = (await login_resp.json()).get("access_token")
            if not admin_token:
                return None

            # Reset password via Tuwunel admin room command
            command = f"!admin users reset-password {agent_username} {new_password}"
            txn_id = int(time.time() * 1000)
            cmd_url = (
                f"{config.homeserver_url}/_matrix/client/v3/rooms/{admin_room}"
                f"/send/m.room.message/{txn_id}"
            )
            headers = {"Authorization": f"Bearer {admin_token}"}

            async with http.put(
                cmd_url,
                headers=headers,
                json={"msgtype": "m.text", "body": command},
                timeout=_AGENT_LOGIN_TIMEOUT,
            ) as cmd_resp:
                if cmd_resp.status != 200:
                    error_text = await cmd_resp.text()
                    logger.error(
                        f"[{caller}] Password repair: admin command failed "
                        f"({cmd_resp.status}): {error_text}"
                    )
                    return None

            # Wait for Tuwunel to process the command
            await asyncio.sleep(0.5)

            # Read response to verify success
            messages_url = (
                f"{config.homeserver_url}/_matrix/client/v3/rooms/{admin_room}"
                f"/messages?dir=b&limit=2"
            )
            async with http.get(
                messages_url, headers=headers, timeout=_AGENT_LOGIN_TIMEOUT
            ) as msg_resp:
                if msg_resp.status == 200:
                    data = await msg_resp.json()
                    for msg in data.get("chunk", []):
                        body = msg.get("content", {}).get("body", "")
                        if body and body != command:
                            if "Successfully" in body or (
                                "password" in body.lower() and "reset" in body.lower()
                            ):
                                logger.info(
                                    f"[{caller}] Password repair: Tuwunel confirmed reset for {agent_username}"
                                )
                            else:
                                logger.warning(
                                    f"[{caller}] Password repair: unexpected response: {body}"
                                )
                            break

        # Update DB with new password
        from src.models.agent_mapping import AgentMappingDB
        from src.core.mapping_service import invalidate_cache

        db = AgentMappingDB()
        mapping = db.get_by_agent_id(agent_id)
        if mapping:
            room_id = str(mapping.room_id) if mapping.room_id is not None else None
            db.upsert(
                str(mapping.agent_id),
                str(mapping.agent_name),
                str(mapping.matrix_user_id),
                new_password,
                room_id=room_id,
            )
            invalidate_cache()
            logger.info(
                f"[{caller}] Password repair: reset password and updated DB for {agent_name} ({agent_username})"
            )
            return new_password

        logger.error(f"[{caller}] Password repair: mapping not found for {agent_id}")
        return None
    except Exception as e:
        logger.error(f"[{caller}] Password repair failed for {agent_username}: {e}", exc_info=True)
        return None
