"""
Letta Gateway communication — routes messages through the WS gateway to LettaBot.

Contains:
  - send_to_letta_api_streaming(): token-by-token streaming via WS gateway
  - send_to_letta_api(): non-streaming, collects full response via WS gateway
  - Agent routing: resolve room → agent mapping (DB, portal links, room members)
  - Agent Mail: reverse bridge for inter-agent communication
  - retry_with_backoff: generic async retry helper

Extracted from client.py as a standalone module.
Re-exported by client.py for backward compatibility.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple, Union

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

# Agent Mail MCP server URL for reverse bridge
AGENT_MAIL_URL = os.getenv("AGENT_MAIL_URL", "http://192.168.50.90:8766/mcp/")


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


# ── Agent Routing ────────────────────────────────────────────────────

async def get_agent_from_room_members(
    room_id: str, config: Config, logger: logging.Logger
) -> Optional[tuple]:
    """
    Extract agent ID from room members by finding agent Matrix users.
    Returns (agent_id, agent_name) or None if not found.
    """
    try:
        admin_token = config.matrix_token
        members_url = (
            f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/members"
        )
        headers = {"Authorization": f"Bearer {admin_token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(members_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to get room members: {resp.status}")
                    return None

                members_data = await resp.json()
                members = members_data.get("chunk", [])

                from src.models.agent_mapping import AgentMappingDB

                db = AgentMappingDB()
                all_mappings = db.get_all()

                for member in members:
                    user_id = member.get("state_key")
                    if not user_id:
                        continue
                    for mapping in all_mappings:
                        if mapping.matrix_user_id == user_id:
                            logger.info(
                                f"Found agent via room members: {mapping.agent_name} ({mapping.agent_id})"
                            )
                            return (mapping.agent_id, mapping.agent_name)

                logger.warning(
                    f"No agent users found in room {room_id} members"
                )
                return None

    except Exception as e:
        logger.warning(
            f"Error extracting agent from room members: {e}", exc_info=True
        )
        return None


# ── Agent Routing Resolution (shared by both send paths) ─────────────

async def _resolve_agent_for_room(
    room_id: str, config: Config, logger: logging.Logger, tag: str = ""
) -> Tuple[str, str]:
    """
    Resolve (agent_id, agent_name) for a room.
    Falls back through: DB mapping → portal links → room members → default.
    """
    agent_id_to_use = config.letta_agent_id
    agent_name_found = "DEFAULT"

    if room_id:
        try:
            from src.models.agent_mapping import AgentMappingDB

            db = AgentMappingDB()
            mapping = db.get_by_room_id(room_id)
            if mapping:
                agent_id_to_use = str(mapping.agent_id)
                agent_name_found = str(mapping.agent_name)
                if tag:
                    logger.info(
                        f"[{tag}] Found agent mapping: {agent_name_found} ({agent_id_to_use})"
                    )
            else:
                portal_link = db.get_portal_link_by_room_id(room_id)
                if portal_link:
                    portal_mapping = db.get_by_agent_id(portal_link["agent_id"])
                    if portal_mapping:
                        agent_id_to_use = str(portal_mapping.agent_id)
                        agent_name_found = str(portal_mapping.agent_name)
                        if tag:
                            logger.info(
                                f"[{tag}] Portal link match: {agent_name_found} ({agent_id_to_use})"
                            )
                    else:
                        member_result = await get_agent_from_room_members(
                            room_id, config, logger
                        )
                        if member_result:
                            agent_id_to_use, agent_name_found = member_result
                else:
                    member_result = await get_agent_from_room_members(
                        room_id, config, logger
                    )
                    if member_result:
                        agent_id_to_use, agent_name_found = member_result
        except Exception as e:
            logger.warning(f"[{tag}] Could not query agent mappings: {e}")

    return agent_id_to_use, agent_name_found


# ── Conversation ID Resolution ───────────────────────────────────────

async def _resolve_conversation_id(
    config: Config,
    room_id: str,
    agent_id: str,
    sender_id: str,
    room_member_count: int,
    logger: logging.Logger,
) -> Optional[str]:
    """Resolve or create a conversation_id for context isolation."""
    if not config.letta_conversations_enabled:
        return None
    try:
        from src.letta.client import get_letta_client, LettaConfig

        sdk_config = LettaConfig(
            base_url=config.letta_api_url,
            api_key=config.letta_token,
            timeout=config.letta_streaming_timeout,
            max_retries=3,
        )
        letta_client = get_letta_client(sdk_config)
        from src.core.conversation_service import get_conversation_service

        conv_service = get_conversation_service(letta_client)
        conversation_id, created = await conv_service.get_or_create_room_conversation(
            room_id=room_id,
            agent_id=agent_id,
            room_member_count=room_member_count,
            user_mxid=sender_id if room_member_count == 2 else None,
        )
        logger.info(
            f"[CONVERSATIONS] Using conversation {conversation_id} (created={created})"
        )
        return conversation_id
    except Exception as e:
        logger.warning(
            f"[CONVERSATIONS] Failed to get conversation, falling back to agents API: {e}"
        )
        return None


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
        raise LettaApiError(f"Gateway unavailable — cannot process message: {e}")


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
        raise LettaApiError(f"Streaming error: {e}")
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

    logger.warning(f"[DEBUG] AGENT ROUTING: Room {room_id} -> Agent {agent_id_to_use}")
    logger.warning(f"[DEBUG] Agent Name: {agent_name_found}")

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
        )
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
            )
        else:
            logger.error(
                "Unexpected error in Letta API call",
                extra={"error": error_str},
                exc_info=True,
            )
            raise LettaApiError(
                f"An unexpected error occurred with the Letta SDK: {e}"
            )
    finally:
        if typing_manager:
            await typing_manager.stop()


# ── Agent Mail (Reverse Bridge) ──────────────────────────────────────

async def forward_to_agent_mail(
    sender_code_name: str,
    recipient_code_name: str,
    subject: str,
    body_md: str,
    thread_id: Optional[str],
    original_message_id: Optional[int],
    logger: logging.Logger,
) -> bool:
    """
    Forward a Matrix response back to Agent Mail (reverse bridge).

    Called when an agent responds to a message that originated
    from Agent Mail, allowing the original sender to see the response.
    """
    if not AGENT_MAIL_URL:
        logger.warning(
            "[REVERSE-BRIDGE] AGENT_MAIL_URL not configured, skipping forward"
        )
        return False

    try:
        if original_message_id:
            payload = {
                "jsonrpc": "2.0",
                "id": f"reverse-bridge-{time.time()}",
                "method": "tools/call",
                "params": {
                    "name": "reply_message",
                    "arguments": {
                        "project_key": "/opt/stacks/matrix-synapse-deployment",
                        "message_id": original_message_id,
                        "sender_name": sender_code_name,
                        "body_md": body_md,
                        "to": [recipient_code_name],
                    },
                },
            }
        else:
            payload = {
                "jsonrpc": "2.0",
                "id": f"reverse-bridge-{time.time()}",
                "method": "tools/call",
                "params": {
                    "name": "send_message",
                    "arguments": {
                        "project_key": "/opt/stacks/matrix-synapse-deployment",
                        "sender_name": sender_code_name,
                        "to": [recipient_code_name],
                        "subject": subject,
                        "body_md": body_md,
                        "thread_id": thread_id,
                    },
                },
            }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                AGENT_MAIL_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(
                        f"[REVERSE-BRIDGE] Forwarded response from {sender_code_name} to {recipient_code_name}"
                    )
                    logger.debug(f"[REVERSE-BRIDGE] Response: {result}")
                    return True
                else:
                    response_text = await resp.text()
                    logger.warning(
                        f"[REVERSE-BRIDGE] Failed to forward: {resp.status} - {response_text[:200]}"
                    )
                    return False

    except Exception as e:
        logger.error(
            f"[REVERSE-BRIDGE] Error forwarding to Agent Mail: {e}", exc_info=True
        )
        return False


def load_agent_mail_mappings(
    logger: logging.Logger,
) -> Dict[str, Dict[str, Any]]:
    """Load Agent Mail mappings to get code names for agents."""
    mappings_file = "/app/data/agent_mail_mappings.json"
    try:
        if os.path.exists(mappings_file):
            with open(mappings_file, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(
            f"[REVERSE-BRIDGE] Could not load agent mail mappings: {e}"
        )
    return {}


def get_agent_code_name(
    agent_id: str, logger: logging.Logger
) -> Optional[str]:
    """Get the Agent Mail code name for a Letta agent ID."""
    mappings = load_agent_mail_mappings(logger)
    agent_info = mappings.get(agent_id)
    if agent_info:
        return agent_info.get("agent_mail_name")
    return None
