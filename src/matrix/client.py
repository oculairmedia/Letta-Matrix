import asyncio
import os
import logging
import time
from typing import Optional, Dict, Any, Tuple, Union
from nio import AsyncClient, RoomMessageText, LoginError, RoomPreset, RoomMessageMedia, RoomMessageAudio, UnknownEvent
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

# Import our authentication manager
from src.matrix.auth import MatrixAuthManager

# Import file handler
from src.matrix.file_handler import LettaFileHandler, FileUploadError
from src.matrix.document_parser import DocumentParseConfig
from src.matrix import formatter as matrix_formatter

# Import agent user manager
from src.core.agent_user_manager import run_agent_sync
from src.matrix.event_dedupe import is_duplicate_event
from src.matrix.poll_handler import process_agent_response, is_poll_command, handle_poll_vote, POLL_RESPONSE_TYPE

# ── Extracted modules ────────────────────────────────────────────────
from src.matrix.config import (
    Config,
    LettaApiError,
    MatrixClientError,
    ConfigurationError,
    LettaCodeApiError,
    setup_logging,
)
from src.matrix.letta_code_service import (
    get_letta_code_room_state,
    update_letta_code_room_state,
    call_letta_code_api,
    resolve_letta_project_dir,
    run_letta_code_task,
    handle_letta_code_command,
)
from src.matrix.agent_actions import (
    send_as_agent,
    send_as_agent_with_event_id,
    delete_message_as_agent,
    edit_message_as_agent,
    send_reaction_as_agent,
    send_read_receipt_as_agent,
    set_typing_as_agent,
    TypingIndicatorManager,
    upload_and_send_audio,
    fetch_and_send_image,
    fetch_and_send_file,
    fetch_and_send_video,
)
from src.matrix.letta_bridge import (
    send_to_letta_api,
    send_to_letta_api_streaming,
    retry_with_backoff,
)
from src.matrix.message_processor import process_letta_message, MessageContext

MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")

# Background Letta task tracking — keyed by (room_id, agent_id)
# Prevents sync loop blocking when streaming calls hang
_active_letta_tasks: Dict[Tuple[str, str], asyncio.Task] = {}

def _on_letta_task_done(key: Tuple[str, str], task: asyncio.Task) -> None:
    _active_letta_tasks.pop(key, None)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logging.getLogger("matrix_client").error(
            f"[BG-TASK] Background Letta task failed for {key}: {exc}",
            exc_info=exc
        )
        try:
            from src.matrix.alerting import alert_letta_error
            room_id, agent_id = key
            asyncio.get_event_loop().create_task(
                alert_letta_error(agent_id, room_id, str(exc))
            )
        except Exception:
            pass

async def cancel_all_letta_tasks() -> None:
    if not _active_letta_tasks:
        return
    logger = logging.getLogger("matrix_client")
    logger.info(f"[BG-TASK] Cancelling {len(_active_letta_tasks)} active Letta tasks...")
    for task in _active_letta_tasks.values():
        task.cancel()
    await asyncio.gather(*_active_letta_tasks.values(), return_exceptions=True)
    _active_letta_tasks.clear()

# Global variables for backwards compatibility
client = None
auth_manager_global = None


async def poll_response_callback(room, event, config: Config, logger: logging.Logger):
    if not hasattr(event, 'source') or not isinstance(event.source, dict):
        return
    
    content = event.source.get('content', {})
    event_type = event.source.get('type', '')
    
    if event_type != POLL_RESPONSE_TYPE:
        return
    
    sender = event.source.get('sender', '')
    poll_response = content.get('org.matrix.msc3381.poll.response', {})
    answers = poll_response.get('answers', [])
    relates_to = content.get('m.relates_to', {})
    poll_event_id = relates_to.get('event_id')
    
    if not poll_event_id or not answers:
        logger.debug(f"[POLL] Invalid poll response: missing event_id or answers")
        return
    
    logger.info(f"[POLL] Vote received from {sender} for poll {poll_event_id}: {answers}")
    
    from src.models.agent_mapping import AgentMappingDB
    db = AgentMappingDB()
    mapping = db.get_by_room_id(room.room_id)
    if not mapping:
        logger.debug(f"[POLL] No agent mapping for room {room.room_id}, ignoring poll vote")
        return
    
    vote_message = await handle_poll_vote(
        room_id=room.room_id,
        sender=sender,
        poll_event_id=poll_event_id,
        selected_option_ids=answers,
        config=config,
        logger_instance=logger
    )
    
    if vote_message:
        await send_to_letta_api(vote_message, sender, config, logger, room.room_id)


async def file_callback(room, event, config: Config, logger: logging.Logger, file_handler: Optional[LettaFileHandler] = None):
    """Callback function for handling file uploads."""
    if not file_handler:
        logger.warning("File handler not initialized, skipping file event")
        return
    
    try:
        # Check for duplicate events
        event_id = getattr(event, 'event_id', None)
        if event_id and is_duplicate_event(event_id, logger):
            return
        
        logger.info(f"File upload detected in room {room.room_id}")

        # Skip media files sent by agent users to prevent feedback loops
        # (agent sends voice/image -> bridge picks up -> re-processes -> sends back to agent)
        event_sender = getattr(event, 'sender', '')
        if event_sender.startswith('@agent_') and isinstance(event, (RoomMessageAudio, RoomMessageMedia)):
            logger.debug(f"[MEDIA] Skipping agent's own media upload from {event_sender} (feedback loop prevention)")
            return
        
        # Only process files in rooms that have an agent mapping
        # This prevents processing files in relay/bridge rooms where letta
        # is just a relay participant, not the designated responder
        agent_id = None
        try:
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            mapping = db.get_by_room_id(room.room_id)
            if mapping:
                agent_id = str(mapping.agent_id)
                logger.info(f"Using agent {mapping.agent_name} ({agent_id}) for room {room.room_id}")
            else:
                # No agent mapping for this room - it's likely a relay/bridge room
                # Skip file processing to avoid spamming relay rooms with processing messages
                logger.debug(f"No agent mapping for room {room.room_id}, skipping file processing (relay room)")
                return
        except Exception as e:
            logger.warning(f"Could not query agent mappings: {e}, skipping file processing")
            return
        
        # Handle the file upload (notifications are sent by file_handler)
        file_result = await file_handler.handle_file_event(event, room.room_id, agent_id)
        cleanup_event_ids, status_summary = file_handler.pop_cleanup_event_ids()

        # None = document sent to Temporal for async processing (workflow handles notification + status)
        if file_result is None:
            logger.info(f"[FILE] Document dispatched to Temporal workflow, nothing more to do")
            return

        # Ensure search_documents tool is attached BEFORE the agent run
        # (For Temporal documents, this is done inside _start_temporal_workflow)
        if agent_id:
            try:
                await file_handler.ensure_search_tool_attached(agent_id)
                logger.info(f"[FILE] search_documents tool verified for agent {agent_id}")
            except Exception as attach_err:
                logger.error(f"[FILE] Failed to attach search_documents tool: {attach_err}")

        if isinstance(file_result, (list, str)):
            if config.letta_streaming_enabled:
                await send_to_letta_api_streaming(file_result, event.sender, config, logger, room.room_id)
            else:
                await send_to_letta_api(file_result, event.sender, config, logger, room.room_id)
            
            # Rewrite status messages: edit first to summary, edit rest to blank
            for i, eid in enumerate(cleanup_event_ids):
                try:
                    if i == 0 and status_summary:
                        # Edit the first status message into a compact summary
                        await edit_message_as_agent(room.room_id, eid, status_summary, config, logger)
                    else:
                        # Edit subsequent status messages to blank (avoids "Message deleted")
                        await edit_message_as_agent(room.room_id, eid, "\u200b", config, logger)
                except Exception as cleanup_err:
                    logger.debug(f"[FILE-CLEANUP] Failed to clean up status message {eid}: {cleanup_err}")
    
    except FileUploadError as e:
        logger.error(f"File upload error: {e}")
        # File handler will send notifications
    
    except Exception as e:
        logger.error(f"Unexpected error in file callback: {e}", exc_info=True)

class _MessageCallbackRouter:
    def __init__(self, room, config: Config, logger: logging.Logger, client: Optional[AsyncClient]):
        self.room = room
        self.config = config
        self.logger = logger
        self.client = client
        self.room_agent_mapping = None
        self.sender_mapping = None
        self.portal_link = None  # Set when room is matched via portal link

    def _should_skip_message(self, event, room_id) -> Optional[str]:
        event_id = getattr(event, "event_id", None)
        if event_id and is_duplicate_event(event_id, self.logger):
            return "duplicate"

        if self.client and event.sender == self.client.user_id:
            return "self_message"

        source = getattr(event, "source", None)
        if isinstance(source, dict):
            content = source.get("content", {})
            if content.get("m.letta_historical"):
                self.logger.debug(f"Ignoring historical message from {event.sender}")
                return "historical"
            if content.get("m.bridge_originated"):
                self.logger.debug(f"Ignoring bridge-originated message from {event.sender}")
                return "bridge_originated"

        disabled_agent_ids = [a.strip() for a in os.getenv("DISABLED_AGENT_IDS", "").split(",") if a.strip()]
        if not disabled_agent_ids:
            return None

        from src.core.mapping_service import get_mapping_by_room_id, get_mapping_by_agent_id, get_portal_link_by_room_id

        room_agent_mapping = get_mapping_by_room_id(room_id)
        if not room_agent_mapping:
            portal_link = get_portal_link_by_room_id(room_id)
            if portal_link:
                room_agent_mapping = get_mapping_by_agent_id(portal_link["agent_id"])

        if not room_agent_mapping:
            return None

        room_agent_id = room_agent_mapping.get("agent_id")
        room_agent_name = room_agent_mapping.get("agent_name", "Unknown")
        if room_agent_id and room_agent_id in disabled_agent_ids:
            self.logger.debug(f"Skipping disabled agent {room_agent_id} ({room_agent_name})")
            return "disabled_agent"
        return None

    async def _resolve_agent_for_room(self, room_id, event) -> Optional[Tuple]:
        from src.core.mapping_service import (
            get_mapping_by_room_id,
            get_mapping_by_matrix_user,
            get_mapping_by_agent_id,
            get_portal_link_by_room_id,
        )

        self.room_agent_mapping = get_mapping_by_room_id(room_id)
        if not self.room_agent_mapping:
            portal_link = get_portal_link_by_room_id(room_id)
            if portal_link:
                self.room_agent_mapping = get_mapping_by_agent_id(portal_link["agent_id"])
                if self.room_agent_mapping:
                    self.portal_link = portal_link
                    self.logger.info(f"Portal link match: room {room_id} → agent {portal_link['agent_id']}")

        room_agent_user_id = self.room_agent_mapping.get("matrix_user_id") if self.room_agent_mapping else None
        room_agent_id = self.room_agent_mapping.get("agent_id") if self.room_agent_mapping else None
        room_agent_name = self.room_agent_mapping.get("agent_name", "Unknown") if self.room_agent_mapping else None

        self.sender_mapping = get_mapping_by_matrix_user(event.sender)
        if self.sender_mapping and self.sender_mapping.get("agent_id"):
            from src.matrix.mention_routing import handle_agent_mention_routing

            await handle_agent_mention_routing(
                room=self.room,
                event=event,
                sender_mxid=event.sender,
                sender_agent_id=self.sender_mapping["agent_id"],
                sender_agent_name=self.sender_mapping.get("agent_name", "Unknown"),
                config=self.config,
                logger=self.logger,
                admin_client=self.client,
            )

        if room_agent_user_id and event.sender == room_agent_user_id:
            self.logger.debug(f"Ignoring message from room's own agent {event.sender}")
            return None

        if self.sender_mapping and event.sender != room_agent_user_id:
            self.logger.info(f"Received inter-agent message from {event.sender} in {self.room.display_name}")

        if not self.room_agent_mapping:
            self.logger.debug(f"No agent mapping for room {room_id}, skipping message processing (relay room)")
            return None

        return room_agent_id, room_agent_name, room_agent_user_id

    def _extract_message_content(self, event) -> Tuple[str, Optional[str]]:
        message_text = getattr(event, "body", "") or ""
        reply_to_event_id = None
        source = getattr(event, "source", None)
        if not isinstance(source, dict):
            return message_text, reply_to_event_id

        content = source.get("content", {})
        relates_to = content.get("m.relates_to", {})
        in_reply_to = relates_to.get("m.in_reply_to", {})
        if isinstance(in_reply_to, dict):
            reply_to_event_id = in_reply_to.get("event_id")
        if not reply_to_event_id:
            reply_to_event_id = relates_to.get("event_id")
        return message_text, reply_to_event_id

    def _is_silent_message(self, sender, agent_mappings) -> bool:
        if not sender or not agent_mappings:
            return False
        return any(sender == mapping.get("matrix_user_id") for mapping in agent_mappings if mapping)

    def _apply_group_gating(self, event, room_agent_user_id, message_text):
        if not self.config.matrix_groups:
            return False, None

        from src.matrix.group_gating import apply_group_gating

        event_source = getattr(event, "source", None)
        gating_result = apply_group_gating(
            room_id=self.room.room_id,
            sender_id=event.sender,
            body=message_text,
            event_source=event_source if isinstance(event_source, dict) else None,
            bot_user_id=room_agent_user_id or (self.client.user_id if self.client else self.config.username),
            groups_config=self.config.matrix_groups,
        )
        if gating_result is None:
            self.logger.debug(f"[GROUP_GATING] Filtered message in {self.room.room_id} from {event.sender}")
            return True, None

        self.logger.info(
            f"[GROUP_GATING] room={self.room.room_id} mode={gating_result.mode} "
            f"mentioned={gating_result.was_mentioned} method={gating_result.method} "
            f"silent={gating_result.silent}"
        )
        return False, gating_result


async def _maybe_handle_fs_mode(room, event, config, logger, room_agent_id, room_agent_name) -> bool:
    fs_state = get_letta_code_room_state(room.room_id)
    fs_enabled = fs_state.get("enabled")
    is_huly_agent = room_agent_name and (room_agent_name.startswith("Huly - ") or room_agent_name == "Huly-PM-Control")
    fs_mode_agents = [a.strip() for a in os.getenv("FS_MODE_AGENTS", "Meridian").split(",") if a.strip()]
    is_fs_mode_agent = room_agent_name and room_agent_name in fs_mode_agents
    use_fs_mode = fs_enabled is True or (fs_enabled is None and (is_huly_agent or is_fs_mode_agent))
    if not use_fs_mode:
        return False

    agent_id = room_agent_id
    agent_name = room_agent_name or "Filesystem Agent"
    if not agent_id or not agent_name:
        from src.models.agent_mapping import AgentMappingDB

        db = AgentMappingDB()
        mapping = db.get_by_room_id(room.room_id)
        if mapping:
            agent_id = str(mapping.agent_id)
            agent_name = str(mapping.agent_name)
    if not agent_id:
        await send_as_agent(room.room_id, "No agent configured for filesystem mode.", config, logger)
        return True

    project_dir = fs_state.get("projectDir")
    if not project_dir:
        project_dir = await resolve_letta_project_dir(room.room_id, agent_id, config, logger)

    if not project_dir and is_huly_agent and agent_name:
        try:
            projects_response = await call_letta_code_api(config, "GET", "/api/projects")
            projects = projects_response.get("projects", [])
            search_name = agent_name[7:] if agent_name.startswith("Huly - ") else agent_name
            for proj in projects:
                if proj.get("name", "").lower() == search_name.lower():
                    project_dir = proj.get("filesystem_path")
                    if project_dir:
                        update_letta_code_room_state(room.room_id, {"projectDir": project_dir})
                        logger.info(f"[HULY-FS] Auto-linked {agent_name} to {project_dir}")
                    break
        except Exception as e:
            logger.warning(f"[HULY-FS] Auto-link failed for {agent_name}: {e}")

    if not project_dir:
        await send_as_agent(room.room_id, "Filesystem mode enabled but no project linked. Run /fs-link.", config, logger)
        return True

    fs_prompt = event.body
    fs_event_timestamp = getattr(event, "server_timestamp", None)
    event_source_for_fs = getattr(event, "source", None)
    if fs_event_timestamp is None and isinstance(event_source_for_fs, dict):
        fs_event_timestamp = event_source_for_fs.get("origin_server_ts")
    if fs_event_timestamp is None:
        fs_event_timestamp = int(time.time() * 1000)

    if event.sender.startswith("@oc_"):
        opencode_mxid = event.sender
        fs_prompt = matrix_formatter.format_opencode_envelope(
            opencode_mxid=opencode_mxid,
            text=event.body,
            chat_id=room.room_id,
            message_id=getattr(event, "event_id", None),
            timestamp=fs_event_timestamp,
        )
        logger.info(f"[OPENCODE-FS] Detected message from OpenCode identity: {opencode_mxid}")
    else:
        room_display = room.display_name or room.room_id
        fs_sender_display = room.user_name(event.sender) if hasattr(room, 'user_name') else None
        fs_prompt = matrix_formatter.format_message_envelope(
            channel="Matrix",
            chat_id=room.room_id,
            message_id=getattr(event, "event_id", None),
            sender=event.sender,
            sender_name=fs_sender_display or event.sender,
            timestamp=fs_event_timestamp,
            text=event.body,
            is_group=True,
            group_name=room_display,
            is_mentioned=False,
        )
        logger.debug(f"[MATRIX-FS] Added context for sender {event.sender}")

    if config.letta_code_enabled:
        try:
            await run_letta_code_task(
                room_id=room.room_id,
                agent_id=agent_id,
                agent_name=agent_name,
                project_dir=project_dir,
                prompt=fs_prompt,
                config=config,
                logger=logger,
                send_fn=send_as_agent,
                wrap_response=False,
            )
            return True
        except Exception as fs_err:
            logger.warning(f"[FS-FALLBACK] letta-code task failed ({fs_err}), falling back to streaming Letta API")
    else:
        logger.info("[FS-SKIP] letta_code_enabled=false, using streaming Letta API for fs-mode room")

    return False


async def _handle_stop_command(room, config, logger, room_agent_id, room_agent_name, message_text) -> bool:
    if message_text.strip().lower() != "/stop":
        return False

    existing = _active_letta_tasks.get((room.room_id, room_agent_id or "unknown"))
    stopped = False
    if existing and not existing.done():
        existing.cancel()
        _active_letta_tasks.pop((room.room_id, room_agent_id or "unknown"), None)
        stopped = True
        logger.info(f"[STOP] Cancelled active task for {room_agent_name} in {room.room_id}")

    if room_agent_id:
        try:
            from src.letta.ws_gateway_client import get_gateway_client

            gw = await get_gateway_client(
                gateway_url=config.letta_gateway_url,
                api_key=config.letta_gateway_api_key,
            )
            aborted = await gw.abort(room_agent_id)
            if aborted:
                stopped = True
                logger.info(f"[STOP] Sent gateway abort + evicted WS connection for agent {room_agent_id}")
        except Exception as e:
            logger.warning(f"[STOP] Gateway abort failed: {e}")

    msg = "⏹ Stopped." if stopped else "Nothing running to stop."
    await send_as_agent(room.room_id, msg, config, logger)
    return True



async def _handle_passive_portal_message(
    room, event, config: Config, logger: logging.Logger,
    room_agent_id: str, room_agent_name: str, message_text: str,
    triage_agent_id: Optional[str] = None,
) -> None:
    """Handle portal room messages in passive observation mode.

    Sends the contact message to Letta for processing (memory updates,
    actionable item detection) but does NOT send any response to the
    Matrix room. The agent observes silently.

    When triage_agent_id is set, passive messages are routed to the triage
    agent instead of the primary agent. The triage agent can then filter
    and escalate actionable items.

    Follows the same pattern as LettaBot's heartbeat: wrap the message in
    a system envelope, send through the gateway, discard the response.
    """
    import time as _time
    from src.matrix.config import LettaApiError
    from src.matrix.letta_bridge import _get_gateway_client
    from src.matrix import formatter as matrix_formatter

    target_agent_id = triage_agent_id or room_agent_id

    event_source = getattr(event, "source", None)
    event_timestamp = None
    if isinstance(event_source, dict):
        event_timestamp = event_source.get("origin_server_ts")
    if event_timestamp is None:
        event_timestamp = int(_time.time() * 1000)

    contact_display_name = room.user_name(event.sender) if hasattr(room, 'user_name') else None

    envelope = matrix_formatter.format_portal_contact_envelope(
        contact_sender=event.sender,
        room_name=room.display_name or room.room_id,
        chat_id=room.room_id,
        message_id=getattr(event, "event_id", None),
        timestamp=event_timestamp,
        text=message_text,
        contact_display_name=contact_display_name,
    )

    if triage_agent_id:
        logger.info(
            f"[PORTAL-TRIAGE] Routing contact message from {event.sender} "
            f"in {room.display_name or room.room_id} to triage agent {triage_agent_id} "
            f"(primary agent: {room_agent_name})"
        )
    else:
        logger.info(
            f"[PORTAL-PASSIVE] Ingesting contact message from {event.sender} "
            f"in {room.display_name or room.room_id} for agent {room_agent_name}"
        )

    async def _send_to_letta():
        try:
            gateway_client = await _get_gateway_client(config, logger)
            from src.letta.gateway_stream_reader import collect_via_gateway

            result = await collect_via_gateway(
                client=gateway_client,
                agent_id=target_agent_id,
                message=envelope,
                source={"channel": "portal", "chatId": room.room_id},
            )
            resp_len = len(result) if result else 0
            log_prefix = "[PORTAL-TRIAGE]" if triage_agent_id else "[PORTAL-PASSIVE]"
            logger.info(
                f"{log_prefix} Agent processed contact message "
                f"({resp_len} chars response discarded)"
            )
        except LettaApiError as e:
            logger.warning(f"[PORTAL-PASSIVE] Letta API error (non-critical): {e}")
        except Exception as e:
            logger.warning(f"[PORTAL-PASSIVE] Failed to send to Letta (non-critical): {e}")

    asyncio.create_task(_send_to_letta())

async def _dispatch_letta_task(room, event, config, logger, client, room_agent_id, gating_result, message_text) -> bool:
    task_key = (room.room_id, room_agent_id or "unknown")
    existing_task = _active_letta_tasks.get(task_key)
    if existing_task and not existing_task.done():
        logger.warning(f"[BG-TASK] Agent still processing previous message for {task_key}, sending notice")
        try:
            await send_as_agent(room.room_id, "⏳ Still processing previous message...", config, logger)
        except Exception:
            pass
        return True

    event_source = getattr(event, "source", None)
    event_source = event_source if isinstance(event_source, dict) else None
    silent_mode = bool(gating_result and gating_result.silent) if gating_result else False
    # Resolve sender display name from room membership
    sender_display_name = room.user_name(event.sender) if hasattr(room, 'user_name') else None
    msg_ctx = MessageContext(
        event_body=message_text,
        event_sender=event.sender,
        event_sender_display_name=sender_display_name,
        event_source=event_source,
        original_event_id=getattr(event, "event_id", None),
        room_id=room.room_id,
        room_display_name=room.display_name or room.room_id,
        room_agent_id=room_agent_id,
        config=config,
        logger=logger,
        client=client,
        silent_mode=silent_mode,
        auth_manager=auth_manager_global,
    )
    task = asyncio.create_task(process_letta_message(msg_ctx))
    task.add_done_callback(lambda t: _on_letta_task_done(task_key, t))
    _active_letta_tasks[task_key] = task
    logger.info(f"[BG-TASK] Dispatched background Letta task for {task_key}")
    return True


async def message_callback(room, event, config: Config, logger: logging.Logger, client: Optional[AsyncClient] = None):
    """Callback function for handling new text messages."""
    if not isinstance(event, RoomMessageText):
        return

    router = _MessageCallbackRouter(room=room, config=config, logger=logger, client=client)
    if router._should_skip_message(event, room.room_id):
        return

    resolved_agent = await router._resolve_agent_for_room(room.room_id, event)
    if not resolved_agent:
        return
    room_agent_id, room_agent_name, room_agent_user_id = resolved_agent

    # Portal rooms: check for @agent mention to activate, otherwise passive observation
    if router.portal_link:
        message_text, _ = router._extract_message_content(event)
        # Admin can always invoke; contacts need mention_enabled
        is_admin = event.sender == os.getenv("MATRIX_ADMIN_USERNAME", "@admin:matrix.oculair.ca")
        mention_allowed = is_admin or router.portal_link.get("mention_enabled", False)
        # If the user @mentions the agent by name AND mention is enabled,
        # treat as active request — let it fall through to normal processing
        # Also check the formatted_body for Matrix pills (HTML mentions)
        formatted_body = (
            event.source.get("content", {}).get("formatted_body", "")
            if getattr(event, "source", None) else ""
        )
        agent_mentioned = mention_allowed and room_agent_name and (
            f"@{room_agent_name.lower()}" in message_text.lower()
            or room_agent_name.lower() in message_text.lower()
            or room_agent_name.lower() in formatted_body.lower()
        )
        if not agent_mentioned:
            triage_id = router.portal_link.get("triage_agent_id")
            await _handle_passive_portal_message(
                room, event, config, logger, room_agent_id, room_agent_name, message_text,
                triage_agent_id=triage_id,
            )
            return
        # Agent was @mentioned and mention_enabled — continue to normal dispatch below
        logger.info(
            f"[PORTAL-ACTIVE] @{room_agent_name} mentioned in portal room "
            f"{room.display_name or room.room_id}, processing as active request"
        )

    message_text, _ = router._extract_message_content(event)
    _ = router._is_silent_message(event.sender, [router.sender_mapping])
    filtered, gating_result = router._apply_group_gating(event, room_agent_user_id, message_text)
    if filtered:
        return

    if await handle_letta_code_command(
        room,
        event,
        config,
        logger,
        send_fn=send_as_agent,
        agent_mapping=router.room_agent_mapping,
        agent_id_hint=room_agent_id,
        agent_name_hint=room_agent_name,
    ):
        return

    if await _maybe_handle_fs_mode(room, event, config, logger, room_agent_id, room_agent_name):
        return

    logger.info("Received message from user", extra={
        "sender": event.sender,
        "room_name": room.display_name,
        "room_id": room.room_id,
        "message_preview": message_text[:100] + "..." if len(message_text) > 100 else message_text,
    })

    if await _handle_stop_command(room, config, logger, room_agent_id, room_agent_name, message_text):
        return

    await _dispatch_letta_task(room, event, config, logger, client, room_agent_id, gating_result, message_text)

async def create_room_if_needed(client_instance, logger: logging.Logger, room_name="Letta Bot Room"):
    """Create a new room and return its ID"""
    logger.info("Creating new room", extra={"room_name": room_name})
    try:
        # Create a public room that anyone can join
        response = await client_instance.room_create(
            name=room_name,
            topic="Room for Letta bot interactions",
            preset=RoomPreset.public_chat,  # Makes the room public
            is_direct=False
        )
        
        if hasattr(response, 'room_id'):
            logger.info("Successfully created room", extra={"room_id": response.room_id})
            return response.room_id
        else:
            logger.error("Failed to create room", extra={"response": str(response)})
            return None
    except Exception as e:
        logger.error("Error creating room", extra={"error": str(e)}, exc_info=True)
        return None

async def join_room_if_needed(client_instance, room_id_or_alias, logger: logging.Logger):
    logger.info("Attempting to join room", extra={"room": room_id_or_alias})
    try:
        response = await client_instance.join(room_id_or_alias)

        if isinstance(response, JoinError):
            error_message = getattr(response, 'message', str(response)) # Human-readable message
            status_code = getattr(response, 'status_code', None) # Matrix error code like M_UNRECOGNIZED

            logger.error("Failed to join room", extra={
                "room": room_id_or_alias,
                "error_message": error_message,
                "status_code": status_code
            })

            # If room doesn't exist, log error but don't create a new one
            if status_code == "M_UNKNOWN" or "Can't join remote room" in error_message:
                logger.error("Configured room doesn't exist and auto-creation is disabled", extra={
                    "room": room_id_or_alias,
                    "suggestion": "Please ensure the room exists and the bot is invited, or update MATRIX_ROOM_ID in .env"
                })
            elif status_code == "M_UNRECOGNIZED":
                logger.warning("Server did not recognize the join request", extra={
                    "room": room_id_or_alias,
                    "details": "This could be due to an invalid room alias or ID, or server-side issues"
                })
            elif status_code == "M_FORBIDDEN":
                logger.warning("Bot not allowed to join room", extra={
                    "room": room_id_or_alias,
                    "details": "The bot may not be invited or allowed to join. Please check room permissions and invites"
                })
            return None
        elif hasattr(response, 'room_id') and response.room_id: # Successful join
            logger.info("Successfully joined room", extra={"room_id": response.room_id})
            return response.room_id
        else: # Other unexpected response type
            logger.error("Unexpected response when joining room", extra={
                "room": room_id_or_alias,
                "response": str(response)
            })
            return None
    except RemoteProtocolError as e: # Catches exceptions raised during the API call
        if "M_UNKNOWN_TOKEN" in str(e):
            logger.error("Invalid token when joining room", extra={
                "room": room_id_or_alias,
                "error": str(e),
                "details": "The client might not be logged in correctly or the session is invalid"
            })
        elif "M_FORBIDDEN" in str(e):
            logger.error("Forbidden when joining room", extra={
                "room": room_id_or_alias,
                "error": str(e),
                "details": "The bot may not be invited or allowed to join"
            })
        else:
            logger.error("Remote protocol error when joining room", extra={
                "room": room_id_or_alias,
                "error": str(e)
            })
        return None
    except Exception as e:
        logger.error("Unexpected error when joining room", extra={
            "room": room_id_or_alias,
            "error": str(e)
        }, exc_info=True)
        return None

async def periodic_agent_sync(config, logger, interval=None):
    """Periodically sync Letta agents to Matrix users via OpenAI endpoint"""
    # Allow override via environment variable, default to 60 seconds
    if interval is None:
        interval = int(os.getenv("MATRIX_AGENT_SYNC_INTERVAL", "60"))
    
    logger.info(f"Starting periodic agent sync with interval: {interval}s")
    
    while True:
        await asyncio.sleep(interval)
        logger.debug("Running periodic agent sync via OpenAI endpoint...")
        try:
            await run_agent_sync(config)
            logger.debug("Periodic agent sync completed successfully")
        except Exception as e:
            logger.error("Periodic agent sync failed", extra={"error": str(e)})

async def main():
    global client  # Make client global

    # Load configuration
    try:
        config = Config.from_env()
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return
    
    # Setup logging
    logger = setup_logging(config)
    logger.info("Matrix client starting up", extra={"config": {
        "homeserver_url": config.homeserver_url,
        "username": config.username,
        "room_id": config.room_id,
        "letta_api_url": config.letta_api_url,
        "agent_id": config.letta_agent_id,
        "log_level": config.log_level
    }})
    
    # Initialize Matrix authentication manager
    auth_manager = MatrixAuthManager(config.homeserver_url, config.username, config.password, "CustomNioClientToken")
    
    # Run agent sync to create rooms for new agents
    logger.info("Running agent sync to create rooms for any new agents...")
    agent_manager = None
    try:
        agent_manager = await run_agent_sync(config)
        logger.info("Agent-to-user sync completed successfully")
    except Exception as e:
        logger.error("Agent sync failed", extra={"error": str(e)})
        # Continue with main client setup even if agent sync fails

    # Enable periodic agent sync to detect new agents and create rooms
    sync_task = asyncio.create_task(periodic_agent_sync(config, logger))
    
    # Get authenticated client
    client = await auth_manager.get_authenticated_client()
    if not client:
        logger.error("Failed to authenticate with Matrix server")
        return

    logger.info("Client authenticated successfully", extra={
        "user_id": client.user_id,
        "device_id": client.device_id
    })
    
    # Store the access token in config for functions that need Matrix API access
    config.matrix_token = client.access_token

    # Join the optional base room, but do not treat failures as fatal
    joined_room_id = None
    if config.room_id:
        joined_room_id = await join_room_if_needed(client, config.room_id, logger)
        if not joined_room_id:
            logger.warning(
                "Configured MATRIX_ROOM_ID could not be joined; continuing without a base room",
                extra={"room_id": config.room_id}
            )
    else:
        logger.info("No MATRIX_ROOM_ID configured; skipping base room join")

    if joined_room_id:
        logger.info("Ready to interact in room", extra={"room_id": joined_room_id})
    else:
        logger.info("Proceeding without a dedicated base room; will listen in agent rooms only")

    # If we created a new room, save its ID for future reference
    if joined_room_id and joined_room_id != config.room_id:
        logger.warning("New room created, please update configuration", extra={
            "new_room_id": joined_room_id,
            "original_room_id": config.room_id
        })

    # Join the Letta Agents space if it exists
    if agent_manager:
        space_id = agent_manager.space_manager.get_space_id()
        if space_id:
            logger.info(f"Attempting to join Letta Agents space: {space_id}")
            space_joined = await join_room_if_needed(client, space_id, logger)
            if space_joined:
                logger.info(f"Successfully joined Letta Agents space")
            else:
                logger.warning(f"Failed to join Letta Agents space")
        else:
            logger.info("No Letta Agents space found, skipping space join")

    # Join all agent rooms
    logger.info("Joining agent rooms...")
    agent_rooms_joined = 0
    try:
        from src.core.mapping_service import get_all_mappings
        mappings = get_all_mappings()
        for agent_id, mapping in mappings.items():
            room_id = mapping.get("room_id")
            agent_name = mapping.get("agent_name")
            if room_id:
                logger.info(f"Attempting to join room for agent {agent_name}")
                joined = await join_room_if_needed(client, room_id, logger)
                if joined:
                    agent_rooms_joined += 1
                    logger.info(f"Successfully joined room for agent {agent_name}: {room_id}")
                else:
                    logger.warning(f"Failed to join room for agent {agent_name}: {room_id}")
    except Exception as e:
        logger.error(f"Error loading agent mappings: {e}")
    
    logger.info(f"Joined {agent_rooms_joined} agent rooms")

    # Create notification callback for file handler
    async def notify_room(room_id: str, message: str) -> Optional[str]:
        """Send notification to room and return event_id for later cleanup"""
        event_id = await send_as_agent_with_event_id(room_id, message, config, logger)
        if not event_id and client is not None:
            await client.room_send(
                room_id,
                "m.room.message",
                {"msgtype": "m.text", "body": message}
            )
        return event_id

    # Initialize file handler with Matrix access token
    matrix_token = client.access_token
    logger.info(f"Matrix access token available: {bool(matrix_token)}, length: {len(matrix_token) if matrix_token else 0}")
    
    # Build document parsing config from env
    doc_parse_config = DocumentParseConfig(
        enabled=config.document_parsing_enabled,
        max_file_size_mb=config.document_parsing_max_file_size_mb,
        timeout_seconds=config.document_parsing_timeout,
        ocr_enabled=config.document_parsing_ocr_enabled,
        ocr_dpi=config.document_parsing_ocr_dpi,
        max_text_length=config.document_parsing_max_text_length,
    )
    
    file_handler = LettaFileHandler(
        homeserver_url=config.homeserver_url,
        letta_api_url=config.letta_api_url,
        letta_token=config.letta_token,
        matrix_access_token=matrix_token,
        notify_callback=notify_room,
        embedding_model=config.embedding_model,
        embedding_endpoint=config.embedding_endpoint or None,
        embedding_endpoint_type=config.embedding_endpoint_type,
        embedding_dim=config.embedding_dim,
        embedding_chunk_size=config.embedding_chunk_size,
        document_parsing_config=doc_parse_config,
    )
    logger.info(f"File handler initialized with embedding: model={config.embedding_model}, endpoint={config.embedding_endpoint or 'default'}, dim={config.embedding_dim}")

    # Add the callback for text messages with config and logger
    # Wrap in try/except to prevent callback errors from breaking the sync loop
    async def callback_wrapper(room, event):
        try:
            await message_callback(room, event, config, logger, client)
        except Exception as e:
            logger.error(f"Error in message callback: {e}", exc_info=True)
    
    client.add_event_callback(callback_wrapper, RoomMessageText)
    
    # Add the callback for file messages
    async def file_callback_wrapper(room, event):
        try:
            await file_callback(room, event, config, logger, file_handler)
        except Exception as e:
            logger.error(f"Error in file callback: {e}", exc_info=True)
    
    client.add_event_callback(file_callback_wrapper, RoomMessageMedia)
    client.add_event_callback(file_callback_wrapper, RoomMessageAudio)
    
    async def poll_response_wrapper(room, event):
        try:
            await poll_response_callback(room, event, config, logger)
        except Exception as e:
            logger.error(f"Error in poll response callback: {e}", exc_info=True)
    
    client.add_event_callback(poll_response_wrapper, UnknownEvent)

    logger.info("Starting sync loop to listen for messages, file uploads, and poll votes")
    
    # Do an initial sync with limit=0 to skip historical messages
    initial_sync_filter = {
        "room": {
            "timeline": {"limit": 0},  # Don't fetch historical messages on initial sync
            "state": {
                "lazy_load_members": True
            }
        },
        "presence": {"enabled": False},
        "account_data": {"enabled": False}
    }
    
    # Regular sync filter for ongoing syncs - MUST include timeline messages!
    sync_filter = {
        "room": {
            "timeline": {"limit": 50},  # Fetch up to 50 messages per sync
            "state": {
                "lazy_load_members": True
            }
        },
        "presence": {"enabled": False},
        "account_data": {"enabled": False}
    }
    
    try:
        # Store auth manager globally so we can refresh tokens during sync
        global auth_manager_global
        auth_manager_global = auth_manager
        
        # Do initial sync to skip old messages
        logger.info("Performing initial sync to skip historical messages")
        await client.sync(timeout=30000, full_state=False, sync_filter=initial_sync_filter)
        logger.info("Initial sync complete, now listening for new messages")
        
        # Now start the main sync loop with regular filter
        await client.sync_forever(timeout=5000, full_state=False, sync_filter=sync_filter)
    except Exception as e:
        logger.error("Error during sync", extra={"error": str(e)}, exc_info=True)
    finally:
        await cancel_all_letta_tasks()
        logger.info("Closing client session")
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())
