"""
Message processing pipeline — formats envelopes, routes to Letta, delivers responses.

Contains _process_letta_message which was the core processing loop in client.py.
Orchestrates:
  1. Token refresh
  2. Inter-agent / OpenCode detection
  3. Message envelope formatting
  4. Read receipts
  5. Streaming vs non-streaming Letta send
  6. Silent mode suppression (group gating)
  7. Gateway-down retry buffering
  8. Error alerting

Extracted from client.py as a standalone module.
Re-exported by client.py for backward compatibility.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from nio import AsyncClient

from src.matrix.config import Config, LettaApiError
from src.matrix.agent_actions import (
    send_as_agent,
    send_as_agent_with_event_id,
    send_read_receipt_as_agent,
)
from src.matrix.letta_bridge import (
    send_to_letta_api,
    send_to_letta_api_streaming,
)
from src.matrix import formatter as matrix_formatter
from src.matrix.poll_handler import process_agent_response

@dataclass
class MessageContext:
    """All data needed to process a single inbound Matrix message."""

    event_body: str
    event_sender: str
    event_sender_display_name: Optional[str]
    event_source: Optional[Dict]
    original_event_id: Optional[str]
    room_id: str
    room_display_name: str
    room_agent_id: Optional[str]
    config: Config
    logger: logging.Logger
    client: Optional[AsyncClient] = None
    silent_mode: bool = False
    auth_manager: Any = None

async def process_letta_message(ctx: MessageContext) -> None:
    """
    Process a single inbound Matrix message through the Letta pipeline.

    *ctx.auth_manager* is the global MatrixAuthManager (passed in to avoid globals).
    """
    # Unpack context for local use
    event_body = ctx.event_body
    event_sender = ctx.event_sender
    event_sender_display_name = ctx.event_sender_display_name
    event_source = ctx.event_source
    original_event_id = ctx.original_event_id
    room_id = ctx.room_id
    room_display_name = ctx.room_display_name
    room_agent_id = ctx.room_agent_id
    config = ctx.config
    logger = ctx.logger
    client = ctx.client
    silent_mode = ctx.silent_mode
    auth_manager = ctx.auth_manager

    message_to_send: Union[str, list] = event_body
    try:
        from src.core.mapping_service import get_mapping_by_matrix_user

        # Refresh Matrix token if needed
        if client and auth_manager is not None:
            await auth_manager.ensure_valid_token(client)

        event_timestamp = None
        if event_source and isinstance(event_source, dict):
            event_timestamp = event_source.get("origin_server_ts")
        if event_timestamp is None:
            event_timestamp = int(time.time() * 1000)

        is_inter_agent_message = False
        from_agent_id = None
        from_agent_name = None

        reply_to_event_id_for_envelope = None
        reply_to_sender_for_envelope = None

        if event_source and isinstance(event_source, dict):
            content = event_source.get("content", {})
            from_agent_id = content.get("m.letta.from_agent_id")
            from_agent_name = content.get("m.letta.from_agent_name")

            if from_agent_id and from_agent_name:
                is_inter_agent_message = True
                logger.info(
                    f"Detected inter-agent message (via metadata) from {from_agent_name} ({from_agent_id})"
                )

            # Extract reply threading context
            relates_to = content.get("m.relates_to", {})
            in_reply_to = (
                relates_to.get("m.in_reply_to", {})
                if isinstance(relates_to, dict)
                else {}
            )
            reply_to_event_id_for_envelope = (
                in_reply_to.get("event_id")
                if isinstance(in_reply_to, dict)
                else None
            )
            reply_to_sender_for_envelope = content.get("m.letta.reply_to_sender")
            if not reply_to_sender_for_envelope and reply_to_event_id_for_envelope:
                body_text = content.get("body", "")
                if body_text.startswith("> <@"):
                    try:
                        reply_to_sender_for_envelope = (
                            body_text.split(">", 2)[1].strip().strip("<>")
                        )
                    except (IndexError, ValueError):
                        pass

        if not is_inter_agent_message:
            sender_agent_mapping = get_mapping_by_matrix_user(event_sender)
            if sender_agent_mapping:
                from_agent_id = sender_agent_mapping.get("agent_id")
                from_agent_name = sender_agent_mapping.get(
                    "agent_name", "Unknown Agent"
                )
                is_inter_agent_message = True
                logger.info(
                    f"Detected inter-agent message (via sender check) from {from_agent_name} ({from_agent_id})"
                )

        if is_inter_agent_message and from_agent_id and from_agent_name:
            raw_body = event_body or ""
            payload_lines = raw_body.splitlines()
            if payload_lines and payload_lines[0].startswith(
                "[Inter-Agent Message from"
            ):
                payload = "\n".join(payload_lines[1:]).lstrip("\n")
            else:
                payload = raw_body

            message_to_send = matrix_formatter.format_inter_agent_envelope(
                sender_agent_name=from_agent_name,
                sender_agent_id=from_agent_id,
                text=payload,
                chat_id=room_id,
                message_id=original_event_id,
                timestamp=event_timestamp,
                reply_to_event_id=reply_to_event_id_for_envelope,
                reply_to_sender=reply_to_sender_for_envelope,
            )
            logger.info("[INTER-AGENT CONTEXT] Enhanced message for receiving agent:")
            logger.info(
                f"[INTER-AGENT CONTEXT] Sender: {from_agent_name} ({from_agent_id})"
            )
            logger.info(
                f"[INTER-AGENT CONTEXT] Full enhanced message:\n{message_to_send}"
            )

        is_opencode_sender = event_sender.startswith("@oc_")
        opencode_mxid: Optional[str] = None
        if is_opencode_sender and not is_inter_agent_message:
            opencode_mxid = event_sender
            message_to_send = matrix_formatter.format_opencode_envelope(
                opencode_mxid=opencode_mxid,
                text=event_body,
                chat_id=room_id,
                message_id=original_event_id,
                timestamp=event_timestamp,
                reply_to_event_id=reply_to_event_id_for_envelope,
                reply_to_sender=reply_to_sender_for_envelope,
            )
            logger.info(
                f"[OPENCODE] Detected message from OpenCode identity: {opencode_mxid}"
            )
            logger.info(
                "[OPENCODE] Injected @mention instruction for response routing"
            )
        elif not is_inter_agent_message:
            room_display = room_display_name or room_id
            message_to_send = matrix_formatter.format_message_envelope(
                channel="Matrix",
                chat_id=room_id,
                message_id=original_event_id,
                sender=event_sender,
                sender_name=event_sender_display_name or event_sender,
                timestamp=event_timestamp,
                text=event_body,
                is_group=True,
                group_name=room_display,
                is_mentioned=not silent_mode,
                reply_to_event_id=reply_to_event_id_for_envelope,
                reply_to_sender=reply_to_sender_for_envelope,
            )
            logger.debug(
                f"[MATRIX-CONTEXT] Added context for sender {event_sender}"
            )

        # Send read receipt
        if original_event_id:
            asyncio.create_task(
                send_read_receipt_as_agent(room_id, original_event_id, config, logger)
            )

        # ── Streaming path ────────────────────────────────────────
        if config.letta_streaming_enabled:
            logger.info("[STREAMING] Using streaming mode for Letta API call")
            letta_response = await send_to_letta_api_streaming(
                message_to_send,
                event_sender,
                config,
                logger,
                room_id,
                reply_to_event_id=None,
                reply_to_sender=None,
                opencode_sender=opencode_mxid,
            )
            if silent_mode:
                logger.info(
                    f"[GROUP_GATING] Silent mode: suppressing streaming response in {room_id}"
                )
                return
            logger.info(
                "Successfully processed streaming response",
                extra={
                    "response_length": len(letta_response),
                    "room_id": room_id,
                    "streaming": True,
                    "reply_to": original_event_id,
                },
            )

        # ── Non-streaming path ────────────────────────────────────
        else:
            letta_response = await send_to_letta_api(
                message_to_send, event_sender, config, logger, room_id
            )
            if silent_mode:
                logger.info(
                    f"[GROUP_GATING] Silent mode: suppressing direct-API response in {room_id}"
                )
                return

            if matrix_formatter.is_no_reply(letta_response):
                logger.info(
                    f"[DIRECT-API] Agent chose not to reply (no-reply marker) in {room_id}"
                )
            else:
                if opencode_mxid and opencode_mxid not in letta_response:
                    logger.info(
                        f"[OPENCODE] Agent response missing @mention, prepending {opencode_mxid}"
                    )
                    letta_response = f"{opencode_mxid} {letta_response}"

                sent_as_agent = False

                poll_handled, remaining_text, poll_event_id = (
                    await process_agent_response(
                        room_id=room_id,
                        response_text=letta_response,
                        config=config,
                        logger_instance=logger,
                        reply_to_event_id=None,
                        reply_to_sender=None,
                    )
                )

                if poll_handled:
                    logger.info(
                        f"[POLL] Poll command handled, event_id: {poll_event_id}"
                    )
                    if remaining_text:
                        letta_response = remaining_text
                    else:
                        sent_as_agent = True
                        letta_response = ""

                if not poll_handled or remaining_text:
                    sent_as_agent = await send_as_agent(
                        room_id,
                        letta_response,
                        config,
                        logger,
                        reply_to_event_id=None,
                        reply_to_sender=None,
                    )

                if not sent_as_agent:
                    if client:
                        logger.warning(
                            "Failed to send as agent, falling back to main client"
                        )
                        message_content: Dict[str, Any] = {
                            "msgtype": "m.text",
                            "body": letta_response,
                        }
                        await client.room_send(
                            room_id, "m.room.message", message_content
                        )
                    else:
                        logger.error(
                            "No client available and agent send failed"
                        )

                logger.info(
                    "Successfully sent response to Matrix",
                    extra={
                        "response_length": len(letta_response),
                        "room_id": room_id,
                        "sent_as_agent": sent_as_agent,
                        "reply_to": original_event_id,
                    },
                )

    except LettaApiError as e:
        await _handle_letta_api_error(
            e,
            room_id=room_id,
            room_agent_id=room_agent_id,
            event_sender=event_sender,
            message_to_send=event_body,
            config=config,
            logger=logger,
            client=client,
        )

    except Exception as e:
        logger.error(
            "Unexpected error in background Letta task",
            extra={"error": str(e), "sender": event_sender},
            exc_info=True,
        )
        try:
            error_msg = f"Sorry, I encountered an unexpected error: {str(e)[:100]}"
            sent_as_agent = await send_as_agent(
                room_id,
                error_msg,
                config,
                logger,
                reply_to_event_id=None,
                reply_to_sender=None,
            )
            if not sent_as_agent and client:
                error_content: Dict[str, Any] = {
                    "msgtype": "m.text",
                    "body": error_msg,
                }
                await client.room_send(
                    room_id, "m.room.message", error_content
                )
        except Exception as send_error:
            logger.error(
                "Failed to send error message",
                extra={"error": str(send_error)},
            )


# ── Error Handling Helpers ───────────────────────────────────────────

async def _handle_letta_api_error(
    e: LettaApiError,
    *,
    room_id: str,
    room_agent_id: Optional[str],
    event_sender: str,
    message_to_send: Union[str, list],
    config: Config,
    logger: logging.Logger,
    client: Optional[AsyncClient],
) -> None:
    """Handle LettaApiError with gateway-down buffering and alerting."""
    from src.letta.ws_gateway_client import GatewayUnavailableError

    is_gateway_down = (
        "Gateway unavailable" in str(e)
        or "Cannot connect to gateway" in str(e)
        or isinstance(e.__cause__, GatewayUnavailableError)
    )

    if is_gateway_down:
        from src.letta.message_retry_buffer import get_retry_buffer, PendingMessage

        buffer = get_retry_buffer()

        stash_conversation_id: Optional[str] = None
        try:
            from src.letta.client import get_letta_client, LettaConfig
            from src.core.conversation_service import get_conversation_service

            sdk_cfg = LettaConfig(
                base_url=config.letta_api_url,
                api_key=config.letta_token,
                timeout=10.0,
                max_retries=1,
            )
            conv_svc = get_conversation_service(get_letta_client(sdk_cfg))
            stash_conversation_id, _ = (
                await conv_svc.get_or_create_room_conversation(
                    room_id=room_id,
                    agent_id=room_agent_id or config.letta_agent_id,
                    room_member_count=3,
                )
            )
        except Exception:
            pass

        async def _reply_cb(rid, text, cfg, log):
            await send_as_agent(rid, text, cfg, log)

        async def _error_cb(rid, text, cfg, log):
            await send_as_agent(rid, text, cfg, log)

        pending = PendingMessage(
            room_id=room_id,
            agent_id=room_agent_id or config.letta_agent_id,
            message_body=message_to_send,
            conversation_id=stash_conversation_id,
            sender=event_sender,
            config=config,
            is_streaming=config.letta_streaming_enabled,
            reply_callback=_reply_cb,
            error_callback=_error_cb,
        )
        count = await buffer.stash(pending)
        logger.warning(
            f"[RETRY-BUFFER] Gateway down, stashed message for room {room_id} "
            f"(buffer={count}). Will retry automatically."
        )
        try:
            await send_as_agent(
                room_id,
                "I'm having a temporary connection issue. Your message has been "
                "queued and I'll process it automatically when I reconnect.",
                config,
                logger,
            )
        except Exception:
            pass
        return

    logger.error(
        "Letta API error in background task",
        extra={
            "error": str(e),
            "status_code": e.status_code,
            "sender": event_sender,
        },
    )
    try:
        from src.matrix.alerting import alert_streaming_timeout, alert_letta_error

        if "timeout" in str(e).lower() or "Timeout" in str(e):
            await alert_streaming_timeout(
                room_agent_id or "unknown",
                room_id,
                "streaming",
                config.letta_streaming_timeout,
            )
        else:
            await alert_letta_error(
                room_agent_id or "unknown", room_id, str(e)
            )
    except Exception:
        pass

    error_message = (
        f"Sorry, I encountered an error while processing your message: {str(e)[:100]}"
    )
    try:
        sent_as_agent = await send_as_agent(
            room_id,
            error_message,
            config,
            logger,
            reply_to_event_id=None,
            reply_to_sender=None,
        )
        if not sent_as_agent and client:
            error_content: Dict[str, Any] = {
                "msgtype": "m.text",
                "body": error_message,
            }
            await client.room_send(room_id, "m.room.message", error_content)
    except Exception as send_error:
        logger.error(
            "Failed to send error message",
            extra={"error": str(send_error)},
        )
