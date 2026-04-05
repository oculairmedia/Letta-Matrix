"""
StreamingMessageHandler — discrete send/delete streaming pattern for Matrix.

Defers assistant text until STOP and detects agent self-delivery via
``matrix_messaging`` to suppress duplicate messages.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from src.matrix.formatter import is_no_reply
from src.matrix.streaming_types import (
    StreamEvent,
    StreamEventType,
    is_self_delivery_to_room,
)

logger = logging.getLogger("matrix_client.streaming")


class StreamingMessageHandler:
    """
    Handles streaming events and manages Matrix message lifecycle.

    Defers assistant text until STOP and detects agent self-delivery via
    ``matrix_messaging`` to suppress duplicate messages — same semantics as
    ``LiveEditStreamingHandler`` but using discrete send/delete instead of
    in-place edits.
    """

    def __init__(
        self,
        send_message: Callable[..., Any],
        delete_message: Callable[[str, str], Any],
        room_id: str,
        delete_progress: bool = False,
        send_final_message: Optional[Callable[..., Any]] = None,
        thread_root_event_id: Optional[str] = None,
    ):
        self.send_message = send_message
        self.delete_message = delete_message
        self.room_id = room_id
        self.delete_progress = delete_progress
        self.send_final_message = send_final_message or send_message
        self.thread_root_event_id = thread_root_event_id
        self._progress_event_id: Optional[str] = None
        self._latest_thread_event_id: Optional[str] = None
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

    async def _send_final(self, content: str) -> str:
        threaded = bool(self.thread_root_event_id)
        if threaded:
            try:
                event_id = await self.send_final_message(
                    self.room_id,
                    content,
                    thread_event_id=self.thread_root_event_id,
                    thread_latest_event_id=self._latest_thread_event_id,
                )
            except TypeError:
                event_id = await self.send_final_message(self.room_id, content)
            if event_id:
                self._latest_thread_event_id = event_id
            return event_id
        return await self.send_final_message(self.room_id, content)

    async def handle_event(self, event: StreamEvent) -> Optional[str]:
        if self.delete_progress and self._progress_event_id and (event.is_progress or event.is_final or event.is_error):
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
                logger.debug(f"Deleted progress message {self._progress_event_id}")
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to delete progress message: {e}")
            self._progress_event_id = None

        if event.type == StreamEventType.PING:
            return None

        if event.type == StreamEventType.USAGE:
            return None

        if event.type == StreamEventType.REASONING:
            return None

        if event.type == StreamEventType.STOP:
            return await self._handle_stop()

        if event.is_progress:
            if event.type == StreamEventType.TOOL_CALL:
                self._tool_call_count += 1
                if is_self_delivery_to_room(event, self.room_id):
                    logger.info(
                        f"[Streaming] Detected self-delivery tool call "
                        f"({event.metadata.get('tool_name')}) targeting {self.room_id}"
                    )
                    self._self_delivered_to_current_room = True

            progress_text = event.format_progress(tool_call_count=self._tool_call_count)
            if self._progress_event_id:
                try:
                    await self.delete_message(self.room_id, self._progress_event_id)
                except (RuntimeError, ValueError, TypeError, asyncio.TimeoutError) as progress_delete_error:
                    logger.debug(
                        "Failed to replace previous progress message %s: %s",
                        self._progress_event_id,
                        progress_delete_error,
                    )
            eid = await self._send_with_msgtype(progress_text, "m.notice", threaded=True)
            self._progress_event_id = eid
            return eid

        elif event.is_final:
            if is_no_reply(event.content):
                logger.info(f"[Streaming] Agent chose not to reply (no-reply marker) in {self.room_id}")
                self._pending_final_content = None
                return None
            self._pending_final_content = event.content or ""
            return None

        elif event.is_error:
            error_text = f"⚠️ {event.content}"
            if event.metadata.get('detail'):
                error_text += f"\n{event.metadata['detail']}"
            return await self._send_with_msgtype(error_text, "m.notice")

        elif event.is_approval_request:
            approval_text = event.format_progress()
            tool_calls = event.metadata.get('tool_calls', [])

            if tool_calls:
                approval_text += "\n\nTools awaiting approval:"
                for tc in tool_calls:
                    tool_name = tc.get('name', 'unknown')
                    tool_id = tc.get('tool_call_id', '')
                    args = tc.get('arguments', '')
                    if len(args) > 200:
                        args = args[:200] + "..."
                    approval_text += f"\n- **{tool_name}** (`{tool_id[:20]}...`)"
                    if args:
                        approval_text += f"\n  ```\n  {args}\n  ```"

            logger.info(f"[APPROVAL] Sending approval request to Matrix: {len(tool_calls)} tool(s)")
            return await self._send_with_msgtype(approval_text, "m.text")

        return None

    async def _handle_stop(self) -> Optional[str]:
        if self._self_delivered_to_current_room:
            logger.info(
                f"[Streaming] Suppressing duplicate final text — agent self-delivered in {self.room_id}"
            )
            if self.delete_progress and self._progress_event_id:
                try:
                    await self.delete_message(self.room_id, self._progress_event_id)
                except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                    logger.warning(f"Failed to delete progress after self-delivery: {e}")
                self._progress_event_id = None
            self._pending_final_content = None
            return None

        if self._pending_final_content is not None:
            content = self._pending_final_content
            self._pending_final_content = None
            return await self._send_final(content)

        return None

    async def cleanup(self):
        if self._pending_final_content is not None and not self._self_delivered_to_current_room:
            await self._send_final(self._pending_final_content)
            self._pending_final_content = None
            return
        if self.delete_progress and self._progress_event_id:
            try:
                await self.delete_message(self.room_id, self._progress_event_id)
            except (RuntimeError, ValueError, TypeError, AssertionError) as e:
                logger.warning(f"Failed to cleanup progress message: {e}")
            self._progress_event_id = None

    async def show_progress(self, line: str) -> Optional[str]:
        return await self._send_with_msgtype(line, "m.notice", threaded=True)

    async def update_last_progress(self, line: str) -> None:
        """No-op for non-live-edit handler."""
        pass
