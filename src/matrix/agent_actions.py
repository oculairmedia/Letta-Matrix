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
import html
import logging
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote

import aiohttp

from src.matrix.agent_auth import (
    _AGENT_LOGIN_TIMEOUT,
    get_agent_token,
    repair_agent_password,
)
from src.matrix.config import Config


# ── Timeouts ─────────────────────────────────────────────────────────

_AGENT_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)
_AGENT_SEND_TIMEOUT = aiohttp.ClientTimeout(total=15)


# ── Agent Auth ───────────────────────────────────────────────────────

_get_agent_token = get_agent_token
_repair_agent_password = repair_agent_password

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
            agent_token = await get_agent_token(
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

            message_data: Dict[str, Any] = {"msgtype": "m.text", "body": message}

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

            # Convert @mentions to Matrix pills
            _pill_mxids: list = []
            try:
                from src.matrix.pill_formatter import extract_and_convert_pills

                pill_html, _pill_mxids = extract_and_convert_pills(
                    message, message_data.get("formatted_body")
                )
                if _pill_mxids:
                    message_data["formatted_body"] = pill_html
                    message_data["format"] = "org.matrix.custom.html"
            except Exception:
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
                        f'<a href="https://matrix.to/#/{html.escape(quoted_sender)}">{html.escape(quoted_sender)}</a><br/>'
                        f"{html.escape(quoted_body)}"
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

            # Merge pill m.mentions with any reply m.mentions
            if _pill_mxids:
                existing = message_data.get("m.mentions", {}).get("user_ids", [])
                merged = list(dict.fromkeys(existing + _pill_mxids))
                message_data["m.mentions"] = {"user_ids": merged}

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
            agent_token = await get_agent_token(
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
            agent_token = await get_agent_token(
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

            # Add markdown + pill formatting to edit
            _formatted_body = None
            try:
                import markdown

                _html = markdown.markdown(
                    new_body,
                    extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
                )
                if _html and _html != f"<p>{new_body}</p>":
                    _formatted_body = _html
            except ImportError:
                pass

            _pill_mxids: list = []
            try:
                from src.matrix.pill_formatter import extract_and_convert_pills

                pill_html, _pill_mxids = extract_and_convert_pills(
                    new_body, _formatted_body
                )
                if _pill_mxids:
                    _formatted_body = pill_html
            except Exception:
                pass

            if _formatted_body:
                message_data["format"] = "org.matrix.custom.html"
                message_data["formatted_body"] = f"* {_formatted_body}"
                message_data["m.new_content"]["format"] = "org.matrix.custom.html"
                message_data["m.new_content"]["formatted_body"] = _formatted_body
            if _pill_mxids:
                mentions = {"user_ids": list(dict.fromkeys(_pill_mxids))}
                message_data["m.new_content"]["m.mentions"] = mentions

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
            agent_token = await get_agent_token(
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
            agent_token = await get_agent_token(
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
            token = await get_agent_token(
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
        # Idempotent: stop existing task before starting a new one (bd-vw6c)
        if self._typing_task is not None:
            await self.stop()
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

from src.matrix.agent_media import (
    fetch_and_send_file,
    fetch_and_send_image,
    fetch_and_send_video,
    upload_and_send_audio,
)
