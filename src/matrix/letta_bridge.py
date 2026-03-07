"""
Letta Gateway communication — routes messages through the WS gateway to LettaBot.

Contains:
  - send_to_letta_api_streaming(): token-by-token streaming via WS gateway
  - send_to_letta_api(): non-streaming, collects full response via WS gateway
  - retry_with_backoff: generic async retry helper

Agent routing logic lives in agent_router.py and is re-exported here for
backward compatibility.
"""
import asyncio
import logging
import uuid
from typing import Optional, Tuple, Union

import aiohttp

from src.matrix.config import Config, LettaApiError
from src.matrix.agent_actions import (
    send_as_agent_with_event_id,
    delete_message_as_agent,
    edit_message_as_agent,
    upload_and_send_audio,
    fetch_and_send_image,
    fetch_and_send_file,
    fetch_and_send_video,
    TypingIndicatorManager,
)

# ── Retry Helper ─────────────────────────────────────────────────────

async def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    logger: Optional[logging.Logger] = None,
):
    """Retry an async callable with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                if logger:
                    logger.error(
                        "All retry attempts failed",
                        extra={"attempts": max_retries, "error": str(e)},
                    )
                raise
            delay = min(base_delay * (2**attempt), max_delay)
            if logger:
                logger.warning(
                    "Retry attempt failed, waiting before next try",
                    extra={
                        "attempt": attempt + 1,
                        "delay": delay,
                        "error": str(e),
                    },
                )
            await asyncio.sleep(delay)


# ── Agent Routing (moved to agent_router.py, re-exported for compat) ──

from src.matrix.agent_router import (  # noqa: E402, F401
    get_agent_from_room_members,
    _resolve_agent_for_room,
    _resolve_conversation_id,
)

# ── Gateway Client ───────────────────────────────────────────────────

async def _get_gateway_client(config: Config, logger: logging.Logger):
    """Obtain a WS gateway client (raises on failure)."""
    from src.letta.ws_gateway_client import get_gateway_client

    try:
        gw_client = await get_gateway_client(
            gateway_url=config.letta_gateway_url,
            idle_timeout=config.letta_gateway_idle_timeout,
            max_connections=config.letta_gateway_max_connections,
            api_key=config.letta_gateway_api_key or config.letta_token,
        )
        logger.info("[GATEWAY] Connected")
        return gw_client
    except Exception as e:
        raise LettaApiError(f"Gateway unavailable — cannot process message: {e}") from e


# ── Streaming Send ───────────────────────────────────────────────────

async def send_to_letta_api_streaming(
    message_body: Union[str, list],
    sender_id: str,
    config: Config,
    logger: logging.Logger,
    room_id: str,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
    opencode_sender: Optional[str] = None,
    room_member_count: int = 3,
) -> str:
    """
    Send a message to Letta via the WS gateway with real-time streaming.
    Tokens are delivered to Matrix via live-edit or batched progress messages.
    """
    from src.matrix.streaming import StreamingMessageHandler, StreamEventType
    from src.letta.client import get_letta_client, LettaConfig
    from src.voice.directive_parser import (
        parse_directives,
        VoiceDirective,
        ImageDirective,
        FileDirective,
        VideoDirective,
    )
    from src.voice.tts import is_tts_configured, synthesize_speech
    from src.matrix.poll_handler import process_agent_response

    # Resolve agent
    agent_id_to_use, agent_name_found = await _resolve_agent_for_room(
        room_id, config, logger, tag="STREAMING"
    )
    logger.info(
        f"[STREAMING] Sending message with streaming to agent {agent_name_found}"
    )

    # Resolve conversation
    conversation_id = await _resolve_conversation_id(
        config, room_id, agent_id_to_use, sender_id, room_member_count, logger
    )

    # Gateway is REQUIRED
    gateway_client = await _get_gateway_client(config, logger)

    # ── Matrix message callbacks ──────────────────────────────────

    async def send_message(rid: str, content: str) -> str:
        event_id = await send_as_agent_with_event_id(rid, content, config, logger)
        return event_id or ""

    async def send_final_message(rid: str, content: str) -> str:
        final_content = content

        logger.debug(
            f"[OPENCODE] send_final_message: opencode_sender={opencode_sender}, content_len={len(content) if content else 0}"
        )

        if opencode_sender:
            if opencode_sender not in content:
                logger.info(
                    f"[OPENCODE] Agent response missing @mention, prepending {opencode_sender}"
                )
                final_content = f"{opencode_sender} {content}"
            else:
                logger.debug("[OPENCODE] Agent response already contains @mention")

        poll_handled, remaining_text, poll_event_id = await process_agent_response(
            room_id=rid,
            response_text=final_content,
            config=config,
            logger_instance=logger,
            reply_to_event_id=reply_to_event_id,
            reply_to_sender=reply_to_sender,
        )

        if poll_handled:
            logger.info(
                f"[POLL] Poll command handled in streaming, event_id: {poll_event_id}"
            )
            if not remaining_text:
                return poll_event_id or ""
            final_content = remaining_text

        event_id = await send_as_agent_with_event_id(
            rid,
            final_content,
            config,
            logger,
            reply_to_event_id=reply_to_event_id,
            reply_to_sender=reply_to_sender,
        )
        return event_id or ""

    async def delete_message(rid: str, event_id: str) -> None:
        await delete_message_as_agent(rid, event_id, config, logger)

    async def edit_message(rid: str, event_id: str, new_body: str) -> None:
        await edit_message_as_agent(rid, event_id, new_body, config, logger)

    # ── Build streaming handler ───────────────────────────────────

    if config.letta_streaming_live_edit:
        from src.matrix.streaming import LiveEditStreamingHandler

        logger.info(
            "[STREAMING] Using live-edit mode (single message, edited in-place)"
        )
        handler = LiveEditStreamingHandler(
            send_message=send_message,
            edit_message=edit_message,
            room_id=room_id,
            send_final_message=send_final_message,
            delete_message=delete_message,
        )
    else:
        handler = StreamingMessageHandler(
            send_message=send_message,
            delete_message=delete_message,
            room_id=room_id,
            delete_progress=False,
            send_final_message=send_final_message,
        )

    final_response = ""
    typing_manager = (
        TypingIndicatorManager(room_id, config, logger)
        if config.letta_typing_enabled
        else None
    )
    voice_logger = logging.getLogger("matrix_client.voice")

    try:
        if typing_manager:
            await typing_manager.start()

        from src.letta.gateway_stream_reader import stream_via_gateway

        event_source = stream_via_gateway(
            client=gateway_client,
            agent_id=agent_id_to_use,
            message=message_body,
            conversation_id=conversation_id,
            max_tool_calls=config.letta_max_tool_calls,
            source={"channel": "matrix", "chatId": room_id},
        )
        logger.info("[STREAMING] Using WS gateway as event source")

        async for event in event_source:
            logger.debug(f"[STREAMING] Event: {event.type.value}")

            if event.type == StreamEventType.ASSISTANT and event.content:
                parse_result = parse_directives(event.content)
                voice_logger.debug(
                    "[VOICE-DEBUG] Parsed content (%d chars): directives=%d, clean=%r",
                    len(event.content),
                    len(parse_result.directives),
                    event.content[:100],
                )

                if parse_result.directives:
                    transcript_parts = []
                    caption_parts = []

                    for directive in parse_result.directives:
                        if isinstance(directive, VoiceDirective):
                            if not is_tts_configured():
                                voice_logger.info(
                                    "[VOICE] Voice directive found but TTS is not configured"
                                )
                                continue
                            await handler.show_progress("🎙️ Generating voice...")
                            audio_data = await synthesize_speech(directive.text)
                            if not audio_data:
                                voice_logger.warning(
                                    "[VOICE] TTS synthesis returned no audio"
                                )
                                await handler.update_last_progress(
                                    "❌ Voice synthesis failed"
                                )
                                continue
                            filename = f"voice-{uuid.uuid4().hex}.mp3"
                            audio_event_id = await upload_and_send_audio(
                                room_id=room_id,
                                audio_data=audio_data,
                                filename=filename,
                                mimetype="audio/mpeg",
                                config=config,
                                logger=voice_logger,
                            )
                            if audio_event_id:
                                voice_logger.info(
                                    "[VOICE] Sent voice message event %s",
                                    audio_event_id,
                                )
                                transcript_parts.append(directive.text)
                                await handler.update_last_progress("✅ Voice sent")
                            else:
                                voice_logger.warning(
                                    "[VOICE] Failed to upload/send voice message"
                                )
                                await handler.update_last_progress(
                                    "❌ Voice upload failed"
                                )

                        elif isinstance(directive, ImageDirective):
                            await handler.show_progress("🖼️ Fetching image...")
                            image_event_id = await fetch_and_send_image(
                                room_id=room_id,
                                image_url=directive.url,
                                alt=directive.alt,
                                config=config,
                                logger=voice_logger,
                            )
                            if image_event_id:
                                voice_logger.info(
                                    "[IMAGE] Sent image event %s", image_event_id
                                )
                                if directive.caption:
                                    caption_parts.append(directive.caption)
                                await handler.update_last_progress("✅ Image sent")
                            else:
                                voice_logger.warning(
                                    "[IMAGE] Failed to fetch/send image from %s",
                                    directive.url,
                                )
                                await handler.update_last_progress("❌ Image failed")

                        elif isinstance(directive, FileDirective):
                            await handler.show_progress("📄 Fetching file...")
                            file_event_id = await fetch_and_send_file(
                                room_id=room_id,
                                file_url=directive.url,
                                filename=directive.filename,
                                config=config,
                                logger=voice_logger,
                            )
                            if file_event_id:
                                voice_logger.info(
                                    "[FILE] Sent file event %s", file_event_id
                                )
                                if directive.caption:
                                    caption_parts.append(directive.caption)
                                await handler.update_last_progress("✅ File sent")
                            else:
                                voice_logger.warning(
                                    "[FILE] Failed to fetch/send file from %s",
                                    directive.url,
                                )
                                await handler.update_last_progress("❌ File failed")

                        elif isinstance(directive, VideoDirective):
                            await handler.show_progress("🎬 Fetching video...")
                            video_event_id = await fetch_and_send_video(
                                room_id=room_id,
                                video_url=directive.url,
                                alt=directive.alt,
                                config=config,
                                logger=voice_logger,
                            )
                            if video_event_id:
                                voice_logger.info(
                                    "[VIDEO] Sent video event %s", video_event_id
                                )
                                if directive.caption:
                                    caption_parts.append(directive.caption)
                                await handler.update_last_progress("✅ Video sent")
                            else:
                                voice_logger.warning(
                                    "[VIDEO] Failed to fetch/send video from %s",
                                    directive.url,
                                )
                                await handler.update_last_progress("❌ Video failed")

                    # Build the text to display
                    display_parts = []
                    if parse_result.clean_text.strip():
                        display_parts.append(parse_result.clean_text.strip())
                    if transcript_parts:
                        display_parts.append(
                            "🗣️ " + " ".join(transcript_parts)
                        )
                    if caption_parts:
                        display_parts.append("\n".join(caption_parts))

                    if display_parts:
                        event.content = "\n\n".join(display_parts)
                        final_response = event.content
                    else:
                        final_response = "(media sent)"
                        continue
                elif event.content:
                    final_response = event.content

            await handler.handle_event(event)

            if event.type == StreamEventType.ERROR:
                logger.error(f"[STREAMING] Error: {event.content}")
                if not final_response:
                    final_response = f"Error: {event.content}"

        await handler.cleanup()

    except Exception as e:
        logger.error(
            f"[STREAMING] Exception during streaming: {e}", exc_info=True
        )
        await handler.cleanup()
        raise LettaApiError(f"Streaming error: {e}") from e
    finally:
        if typing_manager:
            await typing_manager.stop()

    if not final_response:
        final_response = "Agent processed the request (no text response)."

    return final_response


# ── Non-Streaming Send ───────────────────────────────────────────────

async def send_to_letta_api(
    message_body: Union[str, list],
    sender_id: str,
    config: Config,
    logger: logging.Logger,
    room_id: Optional[str] = None,
    room_member_count: int = 3,
) -> str:
    """
    Send a message to Letta via the WS gateway (non-streaming).
    Collects the full response and returns it as a string.
    """
    if sender_id.startswith("@"):
        username = sender_id[1:].split(":")[0]
    else:
        username = sender_id

    agent_id_to_use, agent_name_found = await _resolve_agent_for_room(
        room_id or "", config, logger, tag="API"
    )

    logger.debug(f"AGENT ROUTING: Room {room_id} -> Agent {agent_id_to_use}")
    logger.debug(f"Agent Name: {agent_name_found}")

    if isinstance(message_body, str):
        message_preview = (
            message_body[:100] + "..." if len(message_body) > 100 else message_body
        )
    else:
        message_preview = f"[multimodal content: {len(message_body)} parts]"

    logger.info(
        "Sending message to Letta API",
        extra={
            "message_preview": message_preview,
            "sender": username,
            "agent_id": agent_id_to_use,
            "room_id": room_id,
        },
    )

    conversation_id = await _resolve_conversation_id(
        config, room_id or "", agent_id_to_use, sender_id, room_member_count, logger
    )

    typing_manager = (
        TypingIndicatorManager(room_id, config, logger)
        if (config.letta_typing_enabled and room_id)
        else None
    )

    try:
        if typing_manager:
            await typing_manager.start()

        # Gateway is REQUIRED — direct API is deprecated
        gateway_client = await _get_gateway_client(config, logger)
        from src.letta.gateway_stream_reader import collect_via_gateway

        gateway_result = await collect_via_gateway(
            client=gateway_client,
            agent_id=agent_id_to_use,
            message=message_body,
            conversation_id=conversation_id,
            source={"channel": "matrix", "chatId": room_id} if room_id else None,
        )
        if gateway_result:
            logger.info(
                f"[API] Got response via WS gateway ({len(gateway_result)} chars)"
            )
            return gateway_result
        else:
            return "Agent processed the request (no text response)."

    except aiohttp.ClientResponseError as e:
        logger.error(
            "Letta API HTTP error",
            extra={"status_code": e.status, "message": str(e.message)[:200]},
        )
        raise LettaApiError(
            f"Letta API returned error {e.status}", e.status, str(e.message)[:200]
        ) from e
    except Exception as e:
        error_str = str(e)
        if "Error code:" in error_str:
            import re

            match = re.search(r"Error code: (\d+)", error_str)
            status_code = int(match.group(1)) if match else 500
            logger.error(
                "Letta SDK API error",
                extra={"status_code": status_code, "message": error_str[:200]},
            )
            raise LettaApiError(
                f"Letta API returned error {status_code}",
                status_code,
                error_str[:200],
            ) from e
        else:
            logger.error(
                "Unexpected error in Letta API call",
                extra={"error": error_str},
                exc_info=True,
            )
            raise LettaApiError(
                f"An unexpected error occurred with the Letta SDK: {e}"
            ) from e
    finally:
        if typing_manager:
            await typing_manager.stop()


