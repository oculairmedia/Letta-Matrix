"""
Agent authentication and token management helpers.
"""

import asyncio
import logging
import time
from typing import Callable, Dict, Optional

import aiohttp

from src.matrix.config import Config
from src.models.agent_mapping import AgentMappingDB
from src.core.mapping_service import invalidate_cache


_AGENT_LOGIN_TIMEOUT = aiohttp.ClientTimeout(total=10)


# Cooldown tracking: agent_id -> timestamp of last repair attempt
_REPAIR_COOLDOWN_SECONDS = 300  # 5 minutes
_repair_last_attempt: Dict[str, float] = {}




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
    _session_factory: Callable[[], aiohttp.ClientSession] = aiohttp.ClientSession,
) -> Optional[str]:
    """
    Self-healing: reset agent Matrix user password via Tuwunel admin room command.
    Updates the mapping DB so future logins use the new password.
    Returns the new password on success, None on failure.
    """
    agent_id = agent_mapping.get("agent_id", "")
    last_attempt = _repair_last_attempt.get(agent_id, 0)
    if time.monotonic() - last_attempt < _REPAIR_COOLDOWN_SECONDS:
        return None  # Still in cooldown, don't retry yet
    _repair_last_attempt[agent_id] = time.monotonic()

    agent_username = agent_mapping["matrix_user_id"].split(":")[0].replace("@", "")
    agent_name = agent_mapping.get("agent_name", agent_username)

    try:
        import os
        import secrets
        import string

        # Generate a new password
        charset = string.ascii_letters + string.digits
        new_password = "AgentRepair_" + "".join(secrets.choice(charset) for _ in range(20))

        # Admin credentials
        admin_user = os.getenv("MATRIX_ADMIN_USERNAME", os.getenv("MATRIX_USERNAME", "admin"))
        admin_pass = os.getenv("MATRIX_ADMIN_PASSWORD", os.getenv("MATRIX_PASSWORD", ""))
        admin_room = "!jmP5PQ2G13I4VcIcUT:matrix.oculair.ca"

        async with _session_factory() as http:
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

            # Read response to verify success — correlate to this specific agent
            reset_confirmed = False
            messages_url = (
                f"{config.homeserver_url}/_matrix/client/v3/rooms/{admin_room}"
                f"/messages?dir=b&limit=10"
            )
            async with http.get(
                messages_url, headers=headers, timeout=_AGENT_LOGIN_TIMEOUT
            ) as msg_resp:
                if msg_resp.status == 200:
                    data = await msg_resp.json()
                    for msg in data.get("chunk", []):
                        body = msg.get("content", {}).get("body", "")
                        if not body or body == command:
                            continue
                        # Tuwunel responds: "Successfully reset the password for user @X:domain: newpass"
                        # Correlate: body must reference our agent_username
                        body_lower = body.lower()
                        is_success = "successfully" in body_lower and "reset" in body_lower
                        mentions_agent = agent_username.lower() in body_lower
                        if is_success and mentions_agent:
                            reset_confirmed = True
                            logger.info(
                                f"[{caller}] Password repair: Tuwunel confirmed reset for {agent_username}"
                            )
                            break
                        elif is_success and not mentions_agent:
                            # Success message for a different agent — skip it
                            continue
                        else:
                            logger.warning(
                                f"[{caller}] Password repair: unexpected response: {body}"
                            )
                            break

            if not reset_confirmed:
                logger.warning(
                    f"[{caller}] Password repair: no correlated confirmation for {agent_username}, "
                    f"persisting new password optimistically"
                )
        # Update DB with new password (persist even without confirmation —
        # the command was sent successfully, and Tuwunel may just be slow to respond)

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
