"""
LiveEditStreamingHandler — in-place edit streaming pattern for Matrix.

Streams agent activity into a single Matrix message that is edited in-place.
First meaningful event creates the message. Subsequent events append to a
running log and edit the same message (debounced to avoid rate-limits).
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from src.matrix.formatter import is_no_reply
from src.matrix.streaming_types import (
    StreamEvent,
    StreamEventType,
    is_self_delivery_to_room,
)

logger = logging.getLogger("matrix_client.streaming")


class LiveEditStreamingHandler:
    """
    Streams agent activity into a single Matrix message that is edited in-place.

    First meaningful event creates the message.  Subsequent events append to a
    running log and edit the same message (debounced to avoid rate-limits).

    Assistant text is deferred until STOP so that mid-chain assistant events
    (emitted between tool calls) don't prematurely replace the progress log.
    If the agent self-delivers via ``matrix_messaging`` targeting the current
    room, the duplicate final text is suppressed and the progress message is
    cleaned up instead.
    """

    EDIT_DEBOUNCE_S = 0.2

    def __init__(
        self,
        send_message: Callable[..., Any],
        edit_message: Callable[..., Any],
        room_id: str,
        send_final_message: Optional[Callable[..., Any]] = None,
        delete_message: Optional[Callable[[str, str], Any]] = None,
        thread_root_event_id: Optional[str] = None,
        reply_to_event_id: Optional[str] = None,
    ):
        self.send_message = send_message
        self.edit_message = edit_message
        self.delete_message = delete_message
        self.room_id = room_id
        self.send_final_message = send_final_message or send_message
        self.thread_root_event_id = thread_root_event_id
        self.reply_to_event_id = reply_to_event_id

        self._event_id: Optional[str] = None
        self._latest_thread_event_id: Optional[str] = None
        self._lines: List[str] = []
        self._last_edit_time: float = 0
        self._tool_call_count = 0

        self._pending_final_content: Optional[str] = None
        self._self_delivered_to_current_room: bool = False

    @property
    def self_delivered(self) -> bool:
        return self._self_delivered_to_current_room

    async def _send_with_msgtype(self, text: str, msgtype: str, *, threaded: bool = False) -> str:
        thread_kwargs: Dict[str, Any] = {}
        if threaded and self.thread_root_event_id:
            thread_kwargs = {
                "thread_event_id": self.thread_root_event_id,
                "thread_latest_event_id": self._latest_thread_event_id,
            }
        if msgtype == "m.text":
            try:
                event_id = await self.send_message(self.room_id, text, **thread_kwargs)
            except TypeError:
                event_id = await self.send_message(self.room_id, text)
            if thread_kwargs and event_id:
                self._latest_thread_event_id = event_id
            return event_id
        try:
            event_id = await self.send_message(self.room_id, text, msgtype=msgtype, **thread_kwargs)
        except TypeError:
            event_id = await self.send_message(self.room_id, text)
        if thread_kwargs and event_id:
            self._latest_thread_event_id = event_id
        return event_id

    async def _edit_with_msgtype(self, event_id: str, text: str, msgtype: str) -> None:
        if msgtype == "m.text":
            await self.edit_message(self.room_id, event_id, text)
            return
        try:
            await self.edit_message(self.room_id, event_id, text, msgtype=msgtype)
        except TypeError:
            await self.edit_message(self.room_id, event_id, text)

    async def handle_event(self, event: StreamEvent) -> Optional[str]:
        if event.type == StreamEventType.PING:
            return None

        if event.type == StreamEventType.USAGE:
            return None

        if event.type == StreamEventType.REASONING:
            return None

        if event.type == StreamEventType.STOP:
            return await self._handle_stop()

        if event.is_final:
            if is_no_reply(event.content):
                logger.info(f"[LiveEdit] Agent chose not to reply (no-reply marker) in {self.room_id}")
                self._pending_final_content = None
                await self._cleanup_no_reply()
                return None
            self._pending_final_content = event.content or ""
            return None

        if event.is_error:
            error_text = f"⚠️ {event.content}"
            if self._event_id:
                self._lines.append(error_text)
                await self._do_edit()
                return self._event_id
            return await self._send_with_msgtype(error_text, "m.notice")

        if event.is_approval_request:
            line = event.format_progress()
            self._append_progress_line(event, line)

            if self._event_id is None:
                body = self._build_body()
                eid = await self._send_with_msgtype(body, "m.text")
                self._event_id = eid
                self._last_edit_time = time.monotonic()
                return eid

            now = time.monotonic()
            if now - self._last_edit_time >= self.EDIT_DEBOUNCE_S:
                await self._do_edit()
            return self._event_id

        if event.is_progress:
            if event.type == StreamEventType.TOOL_CALL:
                self._tool_call_count += 1
                if is_self_delivery_to_room(event, self.room_id):
                    logger.info(
                        f"[LiveEdit] Detected self-delivery tool call "
                        f"({event.metadata.get('tool_name')}) targeting {self.room_id}"
                    )
                    self._self_delivered_to_current_room = True

            line = event.format_progress(tool_call_count=self._tool_call_count)
            self._append_progress_line(event, line)

            if self._event_id is None:
                body = self._build_body()
                eid = await self._send_with_msgtype(body, "m.notice", threaded=True)
                self._event_id = eid
                self._last_edit_time = time.monotonic()
                return eid

            now = time.monotonic()
            if now - self._last_edit_time >= self.EDIT_DEBOUNCE_S:
                await self._do_edit()
            return self._event_id

        return None

    async def _handle_stop(self) -> Optional[str]:
        if self._self_delivered_to_current_room:
            logger.info(
                f"[LiveEdit] Suppressing duplicate final text — agent self-delivered in {self.room_id}"
            )
            await self._cleanup_self_delivered()
            return None

        if self._pending_final_content is not None:
            content = self._pending_final_content
            self._pending_final_content = None
            return await self._send_final(content)

        return None

    async def _send_final(self, content: str) -> str:
        if self._event_id and (self.thread_root_event_id or self.reply_to_event_id) and self.delete_message:
            try:
                await self.delete_message(self.room_id, self._event_id)
            except (RuntimeError, ValueError, TypeError, AssertionError):
                pass  # best-effort cleanup
            self._event_id = None
            self._lines.clear()

        if self._event_id:
            await self._edit_with_msgtype(self._event_id, content, "m.text")
            eid = self._event_id
            self._event_id = None
            self._lines.clear()
            return eid

        if self.thread_root_event_id:
            try:
                eid = await self.send_final_message(
                    self.room_id,
                    content,
                    thread_event_id=self.thread_root_event_id,
                    thread_latest_event_id=self._latest_thread_event_id,
                )
            except TypeError:
                eid = await self.send_final_message(self.room_id, content)
            self._latest_thread_event_id = eid
            return eid
        eid = await self.send_final_message(self.room_id, content)
        return eid

    async def _do_edit(self) -> None:
        if not self._event_id:
            return
        body = self._build_body()
        await self._edit_with_msgtype(self._event_id, body, "m.notice")
        self._last_edit_time = time.monotonic()

    def _build_body(self) -> str:
        return "\n".join(self._lines)

    def _append_progress_line(self, event: StreamEvent, line: str) -> None:
        if event.type == StreamEventType.TOOL_RETURN and self._lines:
            tool_name = event.metadata.get("tool_name", "unknown")
            if self._lines[-1] == f"🔧 {tool_name}...":
                self._lines[-1] = line
                return
        self._lines.append(line)

    async def show_progress(self, line: str) -> Optional[str]:
        """Add a progress line and update the live-edit message."""
        self._lines.append(line)
        if self._event_id is None:
            body = self._build_body()
            eid = await self._send_with_msgtype(body, "m.notice", threaded=True)
            self._event_id = eid
            self._last_edit_time = time.monotonic()
            return eid
        await self._do_edit()
        return self._event_id

    async def update_last_progress(self, line: str) -> None:
        """Replace the last progress line and edit the message."""
        if self._lines:
            self._lines[-1] = line
        else:
            self._lines.append(line)
        if self._event_id:
            await self._do_edit()

    async def _cleanup_no_reply(self) -> None:
        if self._event_id and self.delete_message:
            try:
                await self.delete_message(self.room_id, self._event_id)
                logger.debug(f"Deleted live-edit progress message {self._event_id} for no-reply")
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to delete live-edit progress for no-reply: {e}")
        self._event_id = None
        self._lines.clear()

    async def _cleanup_self_delivered(self) -> None:
        if self._event_id and self.delete_message:
            try:
                await self.delete_message(self.room_id, self._event_id)
                logger.debug(f"Deleted progress message {self._event_id} after self-delivery")
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to delete progress after self-delivery: {e}")
        self._event_id = None
        self._lines.clear()
        self._pending_final_content = None

    async def cleanup(self) -> None:
        if self._pending_final_content is not None and not self._self_delivered_to_current_room:
            await self._send_final(self._pending_final_content)
            self._pending_final_content = None
            return
        if self._event_id and self._lines:
            await self._do_edit()
