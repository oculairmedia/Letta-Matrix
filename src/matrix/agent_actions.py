"""
Agent-as-user Matrix API operations.

All functions that act on Matrix as the room's mapped agent user:
  - Auth (login, token management)
  - Send / edit / delete / react / read-receipt
  - Typing indicators
  - Media upload helpers (audio, image, file, video)

Extracted from client.py as a standalone module.
Re-exported by client.py for backward compatibility.
"""
import asyncio
import logging
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

from src.matrix.config import Config


# ── Timeouts ─────────────────────────────────────────────────────────

_AGENT_LOGIN_TIMEOUT = aiohttp.ClientTimeout(total=10)
_AGENT_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)
_AGENT_SEND_TIMEOUT = aiohttp.ClientTimeout(total=15)


# ── Agent Auth ───────────────────────────────────────────────────────

async def _get_agent_token(
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
                    repaired = await _repair_agent_password(
                        agent_mapping, config, logger, caller
                    )
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
                                    logger.info(f"[{caller}] Password repair succeeded for {agent_username}")
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

# Cache to avoid repeated repair attempts for the same agent within a session
_repair_attempted: set = set()


async def _repair_agent_password(
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
        new_password = 'AgentRepair_' + ''.join(secrets.choice(charset) for _ in range(20))

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
            db.upsert(mapping.agent_id, mapping.agent_name, mapping.matrix_user_id,
                      new_password, room_id=mapping.room_id)
            invalidate_cache()
            logger.info(f"[{caller}] Password repair: reset password and updated DB for {agent_username}")
            return new_password

        logger.error(f"[{caller}] Password repair: mapping not found for {agent_id}")
        return None
    except Exception as e:
        logger.error(f"[{caller}] Password repair failed for {agent_username}: {e}", exc_info=True)
        return None

# ── Send Messages ────────────────────────────────────────────────────

async def send_as_agent_with_event_id(
    room_id: str,
    message: str,
    config: Config,
    logger: logging.Logger,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
    reply_to_body: Optional[str] = None,
) -> Optional[str]:
    """
    Send a message as the agent user for this room and return the event ID.

    Returns the event_id on success, None on failure.
    """
    try:
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
                logger.warning(f"No agent mapping found for room {room_id}")
                return None

        agent_name = agent_mapping.get("agent_name", "Unknown")
        logger.debug(
            f"[SEND_AS_AGENT] Sending as agent: {agent_name} in room {room_id}"
        )

        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="SEND_AS_AGENT"
            )
            if not agent_token:
                return None

            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            message_data: Dict[str, Any] = {"msgtype": "m.notice", "body": message}

            # Convert markdown to HTML for rich rendering
            try:
                import markdown

                html_body = markdown.markdown(
                    message,
                    extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
                )
                if html_body and html_body != f"<p>{message}</p>":
                    message_data["format"] = "org.matrix.custom.html"
                    message_data["formatted_body"] = html_body
            except ImportError:
                pass

            # Add rich reply relationship if replying to a specific message
            if reply_to_event_id:
                message_data["m.relates_to"] = {
                    "m.in_reply_to": {"event_id": reply_to_event_id}
                }
                quoted_sender = reply_to_sender or "user"
                quoted_body = reply_to_body or ""
                if quoted_body:
                    if len(quoted_body) > 200:
                        quoted_body = quoted_body[:200] + "..."
                    message_data[
                        "body"
                    ] = f"> <{quoted_sender}> {quoted_body}\n\n{message}"
                    mx_reply_html = (
                        f'<mx-reply><blockquote>'
                        f'<a href="https://matrix.to/#/{room_id}/{reply_to_event_id}">In reply to</a> '
                        f'<a href="https://matrix.to/#/{quoted_sender}">{quoted_sender}</a><br/>'
                        f"{quoted_body}"
                        f"</blockquote></mx-reply>"
                    )
                    existing_html = message_data.get("formatted_body", message)
                    message_data["format"] = "org.matrix.custom.html"
                    message_data["formatted_body"] = mx_reply_html + existing_html
                if reply_to_sender:
                    message_data["m.mentions"] = {"user_ids": [reply_to_sender]}
                logger.debug(
                    f"[SEND_AS_AGENT] Creating rich reply to event {reply_to_event_id}"
                )

            async with session.put(
                message_url,
                headers=headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    event_id = result.get("event_id")
                    logger.debug(
                        f"[SEND_AS_AGENT] Sent message, event_id: {event_id}"
                        + (
                            f" (reply to {reply_to_event_id})"
                            if reply_to_event_id
                            else ""
                        )
                    )
                    return event_id
                else:
                    response_text = await response.text()
                    logger.error(
                        f"[SEND_AS_AGENT] Failed to send message: {response.status} - {response_text}"
                    )
                    return None

    except Exception as e:
        logger.error(f"[SEND_AS_AGENT] Exception occurred: {e}", exc_info=True)
        return None


async def send_as_agent(
    room_id: str,
    message: str,
    config: Config,
    logger: logging.Logger,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
) -> bool:
    """
    Send a message as the agent user for this room.
    Returns True on success, False on failure.
    """
    event_id = await send_as_agent_with_event_id(
        room_id,
        message,
        config,
        logger,
        reply_to_event_id=reply_to_event_id,
        reply_to_sender=reply_to_sender,
    )
    return event_id is not None


# ── Delete / Edit / React / Receipt ─────────────────────────────────

async def delete_message_as_agent(
    room_id: str, event_id: str, config: Config, logger: logging.Logger
) -> bool:
    """Redact (delete) a message as the agent user for this room."""
    try:
        from src.core.mapping_service import get_mapping_by_room_id

        agent_mapping = get_mapping_by_room_id(room_id)
        if not agent_mapping:
            logger.warning(f"No agent mapping found for room {room_id}")
            return False
        agent_name = agent_mapping.get("agent_name", "Unknown")
        logger.debug(
            f"[DELETE_AS_AGENT] Attempting to delete message as agent: {agent_name} in room {room_id}"
        )

        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="DELETE_AS_AGENT"
            )
            if not agent_token:
                return False

            txn_id = str(uuid.uuid4())
            redact_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            redact_data = {"reason": "Progress message replaced"}

            async with session.put(
                redact_url,
                headers=headers,
                json=redact_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as response:
                if response.status == 200:
                    logger.debug(
                        f"[DELETE_AS_AGENT] Successfully deleted message {event_id}"
                    )
                    return True
                else:
                    response_text = await response.text()
                    logger.warning(
                        f"[DELETE_AS_AGENT] Failed to delete message: {response.status} - {response_text}"
                    )
                    return False

    except Exception as e:
        logger.error(f"[DELETE_AS_AGENT] Exception occurred: {e}", exc_info=True)
        return False


async def edit_message_as_agent(
    room_id: str,
    event_id: str,
    new_body: str,
    config: Config,
    logger: logging.Logger,
) -> bool:
    """Edit a message as the agent user for this room."""
    try:
        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="EDIT_AS_AGENT"
            )
            if not agent_token:
                return False

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            message_data = {
                "msgtype": "m.text",
                "body": f"* {new_body}",
                "m.new_content": {"msgtype": "m.text", "body": new_body},
                "m.relates_to": {"rel_type": "m.replace", "event_id": event_id},
            }

            async with session.put(
                msg_url,
                headers=headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as response:
                if response.status == 200:
                    logger.debug(f"[EDIT_AS_AGENT] Edited message {event_id}")
                    return True
                else:
                    resp_text = await response.text()
                    logger.warning(
                        f"[EDIT_AS_AGENT] Edit failed: {response.status} - {resp_text}"
                    )
                    return False

    except Exception as e:
        logger.error(f"[EDIT_AS_AGENT] Exception: {e}", exc_info=True)
        return False


async def send_reaction_as_agent(
    room_id: str,
    event_id: str,
    emoji: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    """Send a reaction (emoji) to a message as the agent user for this room."""
    try:
        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="REACTION"
            )
            if not agent_token:
                return None

            txn_id = str(uuid.uuid4())
            url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.reaction/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            content = {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": event_id,
                    "key": emoji,
                }
            }

            async with session.put(
                url, headers=headers, json=content, timeout=_AGENT_SEND_TIMEOUT
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    reaction_event_id = result.get("event_id")
                    logger.debug(
                        f"[REACTION] Sent {emoji} to {event_id}, event_id: {reaction_event_id}"
                    )
                    return reaction_event_id
                else:
                    resp_text = await response.text()
                    logger.warning(
                        f"[REACTION] Failed: {response.status} - {resp_text}"
                    )
                    return None

    except Exception as e:
        logger.error(f"[REACTION] Exception: {e}", exc_info=True)
        return None


async def send_read_receipt_as_agent(
    room_id: str,
    event_id: str,
    config: Config,
    logger: logging.Logger,
) -> bool:
    """Send a read receipt for a message as the agent user for this room."""
    try:
        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="READ_RECEIPT"
            )
            if not agent_token:
                return False

            url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/receipt/m.read/{event_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            async with session.post(
                url, headers=headers, json={}, timeout=_AGENT_SEND_TIMEOUT
            ) as response:
                if response.status == 200:
                    logger.debug(
                        f"[READ_RECEIPT] Sent for {event_id} in {room_id}"
                    )
                    return True
                else:
                    logger.debug(f"[READ_RECEIPT] Failed: {response.status}")
                    return False

    except Exception as e:
        logger.debug(f"[READ_RECEIPT] Exception: {e}")
        return False


# ── Typing Indicators ────────────────────────────────────────────────

_TYPING_HEARTBEAT_INTERVAL = 4.0
_TYPING_TIMEOUT_MS = 5000


async def _get_agent_typing_context(
    room_id: str, config: Config, logger: logging.Logger
) -> Optional[Dict[str, str]]:
    """Resolve agent credentials and build reusable typing context for a room."""
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
        return None

    try:
        async with aiohttp.ClientSession() as session:
            token = await _get_agent_token(
                room_id, config, logger, session, caller="TYPING"
            )
            if not token:
                return None
    except Exception as e:
        logger.debug(f"[TYPING] Login failed: {e}")
        return None

    encoded_user_id = quote(agent_mapping["matrix_user_id"], safe="")
    typing_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/typing/{encoded_user_id}"

    return {"token": token, "typing_url": typing_url}


async def _put_typing(
    session: aiohttp.ClientSession,
    typing_url: str,
    token: str,
    typing: bool,
    timeout_ms: int,
    logger: logging.Logger,
) -> bool:
    """Fire a single typing PUT request using a pre-authenticated token."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    typing_data: Dict[str, Any] = {"typing": typing}
    if typing:
        typing_data["timeout"] = timeout_ms

    try:
        async with session.put(
            typing_url,
            headers=headers,
            json=typing_data,
            timeout=_AGENT_SEND_TIMEOUT,
        ) as response:
            if response.status == 200:
                if not typing:
                    # Workaround: force immediate expiry on servers that ignore typing=false
                    expire_data = {"typing": True, "timeout": 1}
                    async with session.put(
                        typing_url,
                        headers=headers,
                        json=expire_data,
                        timeout=_AGENT_SEND_TIMEOUT,
                    ):
                        pass
                return True
            else:
                logger.debug(f"[TYPING] PUT failed: {response.status}")
                return False
    except Exception as e:
        logger.debug(f"[TYPING] PUT exception: {e}")
        return False


async def set_typing_as_agent(
    room_id: str,
    typing: bool,
    config: Config,
    logger: logging.Logger,
    timeout_ms: int = 5000,
) -> bool:
    """Set typing indicator as the agent user (one-shot, re-authenticates each call)."""
    ctx = await _get_agent_typing_context(room_id, config, logger)
    if not ctx:
        return False
    async with aiohttp.ClientSession() as session:
        return await _put_typing(
            session, ctx["typing_url"], ctx["token"], typing, timeout_ms, logger
        )


class TypingIndicatorManager:
    """Typing heartbeat with cached auth. Logs in once, refreshes every 4s."""

    def __init__(self, room_id: str, config: Config, logger: logging.Logger):
        self.room_id = room_id
        self.config = config
        self.logger = logger
        self._typing_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._ctx: Optional[Dict[str, str]] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _typing_loop(self):
        try:
            while not self._stop_event.is_set():
                if self._ctx and self._session:
                    await _put_typing(
                        self._session,
                        self._ctx["typing_url"],
                        self._ctx["token"],
                        True,
                        _TYPING_TIMEOUT_MS,
                        self.logger,
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=_TYPING_HEARTBEAT_INTERVAL,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            if self._ctx and self._session:
                await _put_typing(
                    self._session,
                    self._ctx["typing_url"],
                    self._ctx["token"],
                    False,
                    _TYPING_TIMEOUT_MS,
                    self.logger,
                )

    async def start(self):
        self._stop_event.clear()
        self._ctx = await _get_agent_typing_context(
            self.room_id, self.config, self.logger
        )
        if not self._ctx:
            self.logger.debug(
                f"[TYPING] No agent context for room {self.room_id}, skipping"
            )
            return
        self._session = aiohttp.ClientSession()
        self._typing_task = asyncio.create_task(self._typing_loop())
        self.logger.debug(
            f"[TYPING] Started 4s heartbeat for room {self.room_id}"
        )

    async def stop(self):
        self._stop_event.set()
        if self._typing_task:
            self._typing_task.cancel()
            try:
                await self._typing_task
            except asyncio.CancelledError:
                pass
            self._typing_task = None
        if self._ctx and self._session:
            await _put_typing(
                self._session,
                self._ctx["typing_url"],
                self._ctx["token"],
                False,
                _TYPING_TIMEOUT_MS,
                self.logger,
            )
        if self._session:
            await self._session.close()
            self._session = None
        self._ctx = None
        self.logger.debug(f"[TYPING] Stopped typing for room {self.room_id}")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
        return False


# ── Media Upload Helpers ─────────────────────────────────────────────

async def upload_and_send_audio(
    room_id: str,
    audio_data: bytes,
    filename: str,
    mimetype: str,
    config: Config,
    logger: logging.Logger,
    duration_ms: Optional[int] = None,
) -> Optional[str]:
    """Upload audio bytes to Matrix media repo and send as m.audio."""
    try:
        async with aiohttp.ClientSession() as session:
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="VOICE"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }

            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=audio_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_response:
                if upload_response.status != 200:
                    upload_error = await upload_response.text()
                    logger.error(
                        "[VOICE] Audio upload failed: %s - %s",
                        upload_response.status,
                        upload_error,
                    )
                    return None
                upload_data = await upload_response.json()
                content_uri = upload_data.get("content_uri")
                if not content_uri:
                    logger.error("[VOICE] Upload response missing content_uri")
                    return None

            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            message_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            info = {
                "mimetype": mimetype,
                "size": len(audio_data),
                "duration": duration_ms,
            }
            message_data = {
                "msgtype": "m.audio",
                "url": content_uri,
                "body": filename,
                "info": info,
                "org.matrix.msc1767.audio": {},
                "org.matrix.msc3245.voice": {},
            }

            async with session.put(
                message_url,
                headers=message_headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_response:
                if send_response.status != 200:
                    send_error = await send_response.text()
                    logger.error(
                        "[VOICE] Audio send failed: %s - %s",
                        send_response.status,
                        send_error,
                    )
                    return None
                send_result = await send_response.json()
                event_id = send_result.get("event_id")
                logger.debug("[VOICE] Sent audio event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(
            f"[VOICE] Exception while uploading/sending audio: {e}", exc_info=True
        )
        return None


async def fetch_and_send_image(
    room_id: str,
    image_url: str,
    alt: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    """Fetch an image from a URL, upload to Matrix media repo, and send as m.image."""
    try:
        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            # Step 1: Fetch the image
            try:
                async with session.get(
                    image_url,
                    headers=fetch_headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as img_response:
                    if img_response.status != 200:
                        logger.error(
                            "[IMAGE] Failed to fetch image from %s: %s",
                            image_url,
                            img_response.status,
                        )
                        return None
                    image_data = await img_response.read()
                    content_type = img_response.headers.get("Content-Type", "image/png")
                    mimetype = content_type.split(";")[0].strip()
                    if not mimetype.startswith("image/"):
                        mimetype = "image/png"
            except Exception as fetch_err:
                logger.error(
                    "[IMAGE] Exception fetching image from %s: %s",
                    image_url,
                    fetch_err,
                )
                return None

            if not image_data or len(image_data) < 100:
                logger.warning(
                    "[IMAGE] Fetched image too small (%d bytes) from %s",
                    len(image_data) if image_data else 0,
                    image_url,
                )
                return None

            # Derive filename from URL
            from urllib.parse import urlparse

            url_path = urlparse(image_url).path
            filename = url_path.split("/")[-1] if "/" in url_path else "image.png"
            if not filename or "." not in filename:
                ext = mimetype.split("/")[-1].replace("jpeg", "jpg")
                filename = f"image.{ext}"

            logger.info(
                "[IMAGE] Fetched %s (%d bytes, %s)",
                filename,
                len(image_data),
                mimetype,
            )

            # Step 2: Login as agent
            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="IMAGE"
            )
            if not agent_token:
                return None

            # Step 3: Upload to Matrix media repo
            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }

            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=image_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_response:
                if upload_response.status != 200:
                    upload_error = await upload_response.text()
                    logger.error(
                        "[IMAGE] Upload failed: %s - %s",
                        upload_response.status,
                        upload_error,
                    )
                    return None
                upload_result = await upload_response.json()
                content_uri = upload_result.get("content_uri")
                if not content_uri:
                    logger.error("[IMAGE] Upload response missing content_uri")
                    return None

            # Step 4: Generate thumbnail and upload it
            thumbnail_uri = None
            thumbnail_info = None
            orig_w, orig_h = None, None
            try:
                from PIL import Image as PILImage
                import io

                img = PILImage.open(io.BytesIO(image_data))
                orig_w, orig_h = img.size
                thumb_size = (320, 320)
                img.thumbnail(thumb_size, PILImage.Resampling.LANCZOS)
                thumb_w, thumb_h = img.size
                thumb_buf = io.BytesIO()
                img.save(thumb_buf, format="PNG")
                thumb_data = thumb_buf.getvalue()

                async with session.post(
                    upload_url,
                    headers={**upload_headers, "Content-Type": "image/png"},
                    params={"filename": "thumbnail.png"},
                    data=thumb_data,
                    timeout=_AGENT_UPLOAD_TIMEOUT,
                ) as thumb_resp:
                    if thumb_resp.status == 200:
                        thumb_result = await thumb_resp.json()
                        thumbnail_uri = thumb_result.get("content_uri")
                        thumbnail_info = {
                            "w": thumb_w,
                            "h": thumb_h,
                            "mimetype": "image/png",
                            "size": len(thumb_data),
                        }
                        logger.debug(
                            "[IMAGE] Uploaded thumbnail %dx%d (%d bytes)",
                            thumb_w,
                            thumb_h,
                            len(thumb_data),
                        )
            except ImportError:
                logger.debug("[IMAGE] Pillow not available, skipping thumbnail")
            except Exception as thumb_err:
                logger.debug(
                    "[IMAGE] Thumbnail generation failed: %s", thumb_err
                )

            # Step 5: Send m.image event
            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            message_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            image_info: Dict[str, Any] = {
                "mimetype": mimetype,
                "size": len(image_data),
            }
            if orig_w and orig_h:
                image_info["w"] = orig_w
                image_info["h"] = orig_h
            if thumbnail_uri and thumbnail_info:
                image_info["thumbnail_url"] = thumbnail_uri
                image_info["thumbnail_info"] = thumbnail_info

            message_data = {
                "msgtype": "m.image",
                "url": content_uri,
                "body": alt or filename,
                "info": image_info,
            }

            async with session.put(
                message_url,
                headers=message_headers,
                json=message_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_response:
                if send_response.status != 200:
                    send_error = await send_response.text()
                    logger.error(
                        "[IMAGE] Send failed: %s - %s",
                        send_response.status,
                        send_error,
                    )
                    return None
                send_result = await send_response.json()
                event_id = send_result.get("event_id")
                logger.info("[IMAGE] Sent image event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(
            f"[IMAGE] Exception while fetching/sending image: {e}", exc_info=True
        )
        return None


async def fetch_and_send_file(
    room_id: str,
    file_url: str,
    filename: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    """Fetch a file from a URL, upload to Matrix media repo, and send as m.file."""
    try:
        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    file_url,
                    headers=fetch_headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        logger.error(
                            "[FILE] Failed to fetch file from %s: %s",
                            file_url,
                            resp.status,
                        )
                        return None
                    file_data = await resp.read()
                    content_type = resp.headers.get(
                        "Content-Type", "application/octet-stream"
                    )
                    mimetype = content_type.split(";")[0].strip()
            except Exception as fetch_err:
                logger.error(
                    "[FILE] Exception fetching file from %s: %s",
                    file_url,
                    fetch_err,
                )
                return None

            if not file_data or len(file_data) < 10:
                logger.warning(
                    "[FILE] Fetched file too small (%d bytes)",
                    len(file_data) if file_data else 0,
                )
                return None

            if not filename:
                from urllib.parse import urlparse

                url_path = urlparse(file_url).path
                filename = url_path.split("/")[-1] if "/" in url_path else "file"
                if not filename or "." not in filename:
                    filename = "file.bin"

            logger.info(
                "[FILE] Fetched %s (%d bytes, %s)", filename, len(file_data), mimetype
            )

            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="FILE"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }
            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=file_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_resp:
                if upload_resp.status != 200:
                    logger.error("[FILE] Upload failed: %s", upload_resp.status)
                    return None
                content_uri = (await upload_resp.json()).get("content_uri")
                if not content_uri:
                    return None

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            msg_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            msg_data = {
                "msgtype": "m.file",
                "url": content_uri,
                "body": filename,
                "filename": filename,
                "info": {"mimetype": mimetype, "size": len(file_data)},
            }
            async with session.put(
                msg_url,
                headers=msg_headers,
                json=msg_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_resp:
                if send_resp.status != 200:
                    logger.error("[FILE] Send failed: %s", send_resp.status)
                    return None
                event_id = (await send_resp.json()).get("event_id")
                logger.info("[FILE] Sent file event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(f"[FILE] Exception: {e}", exc_info=True)
        return None


async def fetch_and_send_video(
    room_id: str,
    video_url: str,
    alt: str,
    config: Config,
    logger: logging.Logger,
) -> Optional[str]:
    """Fetch a video from a URL, upload to Matrix media repo, and send as m.video."""
    try:
        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    video_url,
                    headers=fetch_headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        logger.error(
                            "[VIDEO] Failed to fetch from %s: %s",
                            video_url,
                            resp.status,
                        )
                        return None
                    video_data = await resp.read()
                    content_type = resp.headers.get("Content-Type", "video/mp4")
                    mimetype = content_type.split(";")[0].strip()
                    if not mimetype.startswith("video/"):
                        mimetype = "video/mp4"
            except Exception as fetch_err:
                logger.error(
                    "[VIDEO] Exception fetching from %s: %s", video_url, fetch_err
                )
                return None

            if not video_data or len(video_data) < 100:
                logger.warning(
                    "[VIDEO] Fetched video too small (%d bytes)",
                    len(video_data) if video_data else 0,
                )
                return None

            from urllib.parse import urlparse

            url_path = urlparse(video_url).path
            filename = url_path.split("/")[-1] if "/" in url_path else "video.mp4"
            if not filename or "." not in filename:
                ext = mimetype.split("/")[-1]
                filename = f"video.{ext}"

            logger.info(
                "[VIDEO] Fetched %s (%d bytes, %s)",
                filename,
                len(video_data),
                mimetype,
            )

            agent_token = await _get_agent_token(
                room_id, config, logger, session, caller="VIDEO"
            )
            if not agent_token:
                return None

            upload_url = f"{config.homeserver_url}/_matrix/media/v3/upload"
            upload_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": mimetype,
            }
            async with session.post(
                upload_url,
                headers=upload_headers,
                params={"filename": filename},
                data=video_data,
                timeout=_AGENT_UPLOAD_TIMEOUT,
            ) as upload_resp:
                if upload_resp.status != 200:
                    logger.error("[VIDEO] Upload failed: %s", upload_resp.status)
                    return None
                content_uri = (await upload_resp.json()).get("content_uri")
                if not content_uri:
                    return None

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            msg_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }
            msg_data = {
                "msgtype": "m.video",
                "url": content_uri,
                "body": alt or filename,
                "info": {"mimetype": mimetype, "size": len(video_data)},
            }
            async with session.put(
                msg_url,
                headers=msg_headers,
                json=msg_data,
                timeout=_AGENT_SEND_TIMEOUT,
            ) as send_resp:
                if send_resp.status != 200:
                    logger.error("[VIDEO] Send failed: %s", send_resp.status)
                    return None
                event_id = (await send_resp.json()).get("event_id")
                logger.info("[VIDEO] Sent video event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(f"[VIDEO] Exception: {e}", exc_info=True)
        return None
