import asyncio
import os
import logging
import json
import time
import uuid
import aiohttp
from typing import Optional, Dict, Any, Tuple, Union
from dataclasses import dataclass
from nio import AsyncClient, RoomMessageText, LoginError, RoomPreset, RoomMessageMedia, RoomMessageAudio, UnknownEvent
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

# Import our authentication manager
from src.matrix.auth import MatrixAuthManager

# Import file handler
from src.matrix.file_handler import LettaFileHandler, FileUploadError
from src.matrix import formatter as matrix_formatter

# Import agent user manager
from src.core.agent_user_manager import run_agent_sync
from src.matrix.event_dedupe import is_duplicate_event
from src.matrix.poll_handler import process_agent_response, is_poll_command, handle_poll_vote, POLL_RESPONSE_TYPE

MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")

# Agent Mail MCP server URL for reverse bridge
AGENT_MAIL_URL = os.getenv("AGENT_MAIL_URL", "http://192.168.50.90:8766/mcp/")

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




async def forward_to_agent_mail(
    sender_code_name: str,
    recipient_code_name: str,
    subject: str,
    body_md: str,
    thread_id: Optional[str],
    original_message_id: Optional[int],
    logger: logging.Logger
) -> bool:
    """
    Forward a Matrix response back to Agent Mail (reverse bridge).
    
    This is called when an agent responds to a message that originated
    from Agent Mail, allowing the original sender to see the response.
    
    Args:
        sender_code_name: Agent Mail code name of the responder (e.g., "WhiteStone")
        recipient_code_name: Agent Mail code name of the original sender (e.g., "BlueCreek")
        subject: Subject line (typically "Re: <original subject>")
        body_md: Response body in Markdown
        thread_id: Original thread ID for reply chain continuity
        original_message_id: ID of the message being replied to
        logger: Logger instance
        
    Returns:
        True if forwarded successfully, False otherwise
    """
    if not AGENT_MAIL_URL:
        logger.warning("[REVERSE-BRIDGE] AGENT_MAIL_URL not configured, skipping forward")
        return False
    
    try:
        # Use reply_message if we have the original message ID, otherwise send_message
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
                        "to": [recipient_code_name]
                    }
                }
            }
        else:
            # Fallback to send_message if no original message ID
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
                        "thread_id": thread_id
                    }
                }
            }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                AGENT_MAIL_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    logger.info(f"[REVERSE-BRIDGE] Forwarded response from {sender_code_name} to {recipient_code_name}")
                    logger.debug(f"[REVERSE-BRIDGE] Response: {result}")
                    return True
                else:
                    response_text = await resp.text()
                    logger.warning(f"[REVERSE-BRIDGE] Failed to forward: {resp.status} - {response_text[:200]}")
                    return False
                    
    except Exception as e:
        logger.error(f"[REVERSE-BRIDGE] Error forwarding to Agent Mail: {e}", exc_info=True)
        return False


def load_agent_mail_mappings(logger: logging.Logger) -> Dict[str, Dict[str, Any]]:
    """
    Load Agent Mail mappings to get code names for agents.
    
    Returns a dict keyed by agent_id with code name and other info.
    """
    mappings_file = "/app/data/agent_mail_mappings.json"
    try:
        if os.path.exists(mappings_file):
            with open(mappings_file, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"[REVERSE-BRIDGE] Could not load agent mail mappings: {e}")
    return {}


def get_agent_code_name(agent_id: str, logger: logging.Logger) -> Optional[str]:
    """
    Get the Agent Mail code name for a Letta agent ID.
    
    Args:
        agent_id: Letta agent UUID
        logger: Logger instance
        
    Returns:
        Agent Mail code name (e.g., "WhiteStone") or None if not found
    """
    mappings = load_agent_mail_mappings(logger)
    agent_info = mappings.get(agent_id)
    if agent_info:
        return agent_info.get('agent_mail_name')
    return None


# Custom exception classes
class LettaApiError(Exception):
    """Raised when Letta API calls fail"""
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body

class MatrixClientError(Exception):
    """Raised when Matrix client operations fail"""
    pass

class ConfigurationError(Exception):
    """Raised when configuration is invalid"""
    pass

class LettaCodeApiError(Exception):
    def __init__(self, status_code: int, message: str, details: Optional[Any] = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details

# Configuration dataclass
@dataclass
class Config:
    homeserver_url: str
    username: str
    password: str
    room_id: str
    letta_api_url: str
    letta_token: str
    letta_agent_id: str
    log_level: str = "INFO"
    letta_code_api_url: str = os.getenv("LETTA_CODE_API_URL", "http://192.168.50.90:3099")
    letta_code_enabled: bool = os.getenv("LETTA_CODE_ENABLED", "true").lower() == "true"

    # Embedding configuration for Letta file uploads
    embedding_model: str = "letta/letta-free"
    embedding_endpoint: str = ""  # e.g., http://192.168.50.80:11434/v1 for Ollama
    embedding_endpoint_type: str = "openai"
    embedding_dim: int = 1536
    embedding_chunk_size: int = 300
    # Matrix access token (set after login)
    matrix_token: Optional[str] = None
    # Streaming configuration
    letta_streaming_enabled: bool = False  # Feature flag for step streaming
    letta_streaming_timeout: float = 120.0  # Streaming timeout in seconds
    letta_streaming_idle_timeout: float = 120.0  # Kill stream if no real data (only pings) for this long
    letta_streaming_live_edit: bool = False  # Edit single message in-place instead of sending separate messages
    letta_max_tool_calls: int = 100  # Abort stream if agent exceeds this many tool calls (loop detection)
    # Typing indicators
    letta_typing_enabled: bool = False  # Show typing indicator while agent is processing
    # Conversations API configuration (context isolation per room)
    letta_conversations_enabled: bool = False  # Feature flag for Conversations API
    # Gateway configuration (route messages through lettabot WS gateway)
    letta_gateway_enabled: bool = False  # Feature flag for WS gateway
    letta_gateway_url: str = "ws://192.168.50.90:8407/api/v1/agent-gateway"  # WebSocket gateway URL
    letta_gateway_api_key: str = ""  # API key for gateway auth (X-Api-Key header)
    letta_gateway_idle_timeout: float = 300.0  # Close idle WS connections after 5 minutes
    letta_gateway_max_connections: int = 20  # Max concurrent WS connections
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables"""
        try:
            return cls(
                homeserver_url=os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008"),
                username=os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca"),
                password=os.getenv("MATRIX_PASSWORD", "letta"),
                # MATRIX_ROOM_ID is optional. If unset or empty, we skip joining a base room.
                room_id=os.getenv("MATRIX_ROOM_ID", "") or "",
                letta_api_url=os.getenv("LETTA_API_URL", "http://192.168.50.90:8289"),
                letta_token=os.getenv("LETTA_TOKEN", "lettaSecurePass123"),
                letta_agent_id=os.getenv("LETTA_AGENT_ID", "agent-0e99d1a5-d9ca-43b0-9df9-c09761d01444"),
                log_level=os.getenv("LOG_LEVEL", "INFO"),
                # Embedding configuration
                embedding_model=os.getenv("LETTA_EMBEDDING_MODEL", "letta/letta-free"),
                embedding_endpoint=os.getenv("LETTA_EMBEDDING_ENDPOINT", ""),
                embedding_endpoint_type=os.getenv("LETTA_EMBEDDING_ENDPOINT_TYPE", "openai"),
                embedding_dim=int(os.getenv("LETTA_EMBEDDING_DIM", "1536")),
                embedding_chunk_size=int(os.getenv("LETTA_EMBEDDING_CHUNK_SIZE", "300")),
                # Streaming configuration
                letta_streaming_enabled=os.getenv("LETTA_STREAMING_ENABLED", "false").lower() == "true",
                letta_streaming_timeout=float(os.getenv("LETTA_STREAMING_TIMEOUT", "120.0")),
                letta_streaming_idle_timeout=float(os.getenv("LETTA_STREAMING_IDLE_TIMEOUT", "120.0")),
                letta_streaming_live_edit=os.getenv("LETTA_STREAMING_LIVE_EDIT", "false").lower() == "true",
                letta_code_api_url=os.getenv("LETTA_CODE_API_URL", "http://192.168.50.90:3099"),
                letta_code_enabled=os.getenv("LETTA_CODE_ENABLED", "true").lower() == "true",
                letta_max_tool_calls=int(os.getenv("LETTA_MAX_TOOL_CALLS", "100")),
                letta_conversations_enabled=os.getenv("LETTA_CONVERSATIONS_ENABLED", "false").lower() == "true",
                letta_typing_enabled=os.getenv("LETTA_TYPING_ENABLED", "false").lower() == "true",
                letta_gateway_enabled=os.getenv("LETTA_GATEWAY_ENABLED", "false").lower() == "true",
                letta_gateway_url=os.getenv("LETTA_GATEWAY_URL", "ws://192.168.50.90:8407/api/v1/agent-gateway"),
                letta_gateway_api_key=os.getenv("LETTA_GATEWAY_API_KEY", ""),
                letta_gateway_idle_timeout=float(os.getenv("LETTA_GATEWAY_IDLE_TIMEOUT", "300.0")),
                letta_gateway_max_connections=int(os.getenv("LETTA_GATEWAY_MAX_CONNECTIONS", "20")),
            )
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration: {e}")

LETTACODE_STATE_PATH = os.getenv("LETTA_CODE_STATE_PATH", "/app/data/letta_code_state.json")
_letta_code_state: Dict[str, Dict[str, Any]] = {}


def _load_letta_code_state() -> None:
    global _letta_code_state
    if _letta_code_state:
        return
    try:
        if os.path.exists(LETTACODE_STATE_PATH):
            with open(LETTACODE_STATE_PATH, "r") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    _letta_code_state = data
    except Exception:
        _letta_code_state = {}


def _save_letta_code_state() -> None:
    dir_path = os.path.dirname(LETTACODE_STATE_PATH)
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)
    with open(LETTACODE_STATE_PATH, "w") as fh:
        json.dump(_letta_code_state, fh)


def get_letta_code_room_state(room_id: str) -> Dict[str, Any]:
    _load_letta_code_state()
    room_state = _letta_code_state.get(room_id, {})
    return dict(room_state)


def update_letta_code_room_state(room_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    _load_letta_code_state()
    room_state = _letta_code_state.get(room_id, {})
    room_state.update(updates)
    _letta_code_state[room_id] = room_state
    _save_letta_code_state()
    return dict(room_state)

async def resolve_letta_project_dir(
    room_id: str,
    agent_id: str,
    config: Config,
    logger: logging.Logger,
    override_path: Optional[str] = None,
) -> Optional[str]:
    if override_path:
        update_letta_code_room_state(room_id, {"projectDir": override_path})
        return override_path
    state = get_letta_code_room_state(room_id)
    project_dir = state.get("projectDir")
    if project_dir:
        return project_dir
    try:
        session_info = await call_letta_code_api(config, 'GET', f"/api/letta-code/sessions/{agent_id}")
        if session_info:
            project_dir = session_info.get('projectDir')
            if project_dir:
                update_letta_code_room_state(room_id, {"projectDir": project_dir})
                return project_dir
    except LettaCodeApiError as exc:
        if exc.status_code != 404:
            logger.warning("Failed to resolve Letta Code session", extra={
                "room_id": room_id,
                "agent_id": agent_id,
                "status_code": exc.status_code,
                "error": str(exc),
            })
    except Exception as exc:
        logger.debug(f"Letta Code API unreachable for session resolve: {exc}")
    return None

# Setup structured logging
 
def setup_logging(config: Config) -> logging.Logger:

    """Setup structured JSON logging"""
    logger = logging.getLogger("matrix_client")
    logger.setLevel(getattr(logging, config.log_level.upper()))
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Create console handler with JSON formatter
    handler = logging.StreamHandler()
    handler.setLevel(getattr(logging, config.log_level.upper()))
    
    class JSONFormatter(logging.Formatter):
        def format(self, record):
            log_entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.%fZ", time.gmtime(record.created)),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno
            }
            
            # Add exception info if present
            if record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)
            
            # Add extra fields
            extra_fields = getattr(record, 'extra', None)
            if isinstance(extra_fields, dict):
                log_entry.update(extra_fields)
            return json.dumps(log_entry)
    
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger

async def call_letta_code_api(config: Config, method: str, path: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 600.0) -> Dict[str, Any]:
    base = (config.letta_code_api_url or "").rstrip('/')
    if not base:
        raise LettaCodeApiError(503, "Letta Code API URL not configured")
    url = f"{base}{path}"
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.request(method, url, json=payload) as response:
            text = await response.text()
            data: Optional[Any] = None
            if text:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw": text}
            if response.status >= 400:
                message = ""
                if isinstance(data, dict):
                    message = data.get("error") or data.get("message") or ""
                raise LettaCodeApiError(response.status, message or text or "Request failed", data)
            if data is None:
                return {}
            return data

async def run_letta_code_task(
    *,
    room_id: str,
    agent_id: str,
    agent_name: str,
    project_dir: Optional[str],
    prompt: str,
    config: Config,
    logger: logging.Logger,
    wrap_response: bool = True,
) -> bool:
    if not project_dir:
        await send_as_agent(room_id, "No filesystem session found. Run /fs-link first.", config, logger)
        return False
    payload = {
        "agentId": agent_id,
        "prompt": prompt,
        "projectDir": project_dir,
    }
    try:
        result = await call_letta_code_api(config, 'POST', '/api/letta-code/task', payload, timeout=900.0)
        output = result.get('result') or result.get('message') or ''
        if not output:
            output = 'Task completed with no output.'
        if len(output) > 4000:
            output = output[:4000] + '…'
        success = result.get('success', False)
        if wrap_response:
            status_line = "Task succeeded" if success else "Task failed"
            response_text = f"[Filesystem Task]\n{status_line}\nAgent: {agent_name}\nPath: {project_dir}\n\n{output}"
            if not success:
                error_text = result.get('error') or ''
                if error_text:
                    response_text += f"\nError: {error_text}"
        else:
            if success:
                response_text = output
            else:
                error_text = result.get('error') or ''
                response_text = f"[Filesystem Error]\n{error_text or output}"
        await send_as_agent(room_id, response_text, config, logger)
        return success
    except LettaCodeApiError as exc:
        detail = ""
        if isinstance(exc.details, dict):
            detail = exc.details.get('error') or exc.details.get('message') or ""
        message = f"Filesystem task failed ({exc.status_code}): {detail or str(exc)}"
        await send_as_agent(room_id, message, config, logger)
        return False

async def handle_letta_code_command(
    room,
    event,
    config: Config,
    logger: logging.Logger,
    agent_mapping: Optional[Dict[str, Any]] = None,
    agent_id_hint: Optional[str] = None,
    agent_name_hint: Optional[str] = None,
) -> bool:
    if not config.letta_code_enabled:
        return False
    body = getattr(event, 'body', None)
    if not body:
        return False
    trimmed = body.strip()
    lowered = trimmed.lower()
    if not lowered.startswith('/fs-'):
        return False
    from src.models.agent_mapping import AgentMappingDB
    agent_id = agent_id_hint
    agent_name = agent_name_hint
    if agent_mapping and not agent_name:
        agent_name = agent_mapping.get("agent_name") or agent_mapping.get("agentName")
    mapping_obj = agent_mapping
    if not agent_id or not agent_name:
        db = AgentMappingDB()
        mapping = db.get_by_room_id(room.room_id)
        if not mapping:
            await send_as_agent(room.room_id, "No agent mapping for this room.", config, logger)
            return True
        agent_id = str(mapping.agent_id)
        agent_name = str(mapping.agent_name)
        mapping_obj = {
            "matrix_user_id": mapping.matrix_user_id,
            "room_id": mapping.room_id,
        }
    state = get_letta_code_room_state(room.room_id)
    parts = trimmed.split(' ', 1)
    command = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    if command == '/fs-link':
        project_dir = args if args else None
        
        # Auto-detect path from VibSync if no path provided
        if not project_dir and agent_name:
            try:
                # Fetch all projects from VibSync and match by name
                projects_response = await call_letta_code_api(config, 'GET', '/api/projects')
                projects = projects_response.get('projects', [])
                
                # Extract project name from agent name (e.g., "Huly - Personal Site" -> "Personal Site")
                search_name = agent_name
                if search_name.startswith("Huly - "):
                    search_name = search_name[7:]  # Remove "Huly - " prefix
                
                # Find matching project by name (case-insensitive)
                for proj in projects:
                    if proj.get('name', '').lower() == search_name.lower():
                        project_dir = proj.get('filesystem_path')
                        if project_dir:
                            logger.info(f"Auto-detected filesystem path for {agent_name}: {project_dir}")
                        break
            except Exception as e:
                logger.warning(f"Failed to auto-detect filesystem path: {e}")
        
        if not project_dir:
            await send_as_agent(room.room_id, "Usage: /fs-link /path/to/project\n(Could not auto-detect path for this agent)", config, logger)
            return True
            
        payload = {
            "agentId": agent_id,
            "projectDir": project_dir,
            "agentName": agent_name,
        }
        try:
            response = await call_letta_code_api(config, 'POST', '/api/letta-code/link', payload)
            message = response.get('message') or f"Agent {agent_id} linked to {project_dir}"
            update_letta_code_room_state(room.room_id, {"projectDir": project_dir})
            await send_as_agent(room.room_id, message, config, logger)
        except LettaCodeApiError as exc:
            detail = ""
            if isinstance(exc.details, dict):
                detail = exc.details.get('error') or exc.details.get('message') or ""
            await send_as_agent(room.room_id, f"Link failed ({exc.status_code}): {detail or str(exc)}", config, logger)
        return True
    if command == '/fs-run':
        if not args:
            await send_as_agent(room.room_id, "Usage: /fs-run [--path=/opt/project] prompt", config, logger)
            return True
        prompt_text = args
        path_override = None
        if prompt_text.startswith('--path='):
            first_space = prompt_text.find(' ')
            if first_space == -1:
                path_override = prompt_text[len('--path='):].strip()
                prompt_text = ''
            else:
                path_override = prompt_text[len('--path='):first_space].strip()
                prompt_text = prompt_text[first_space + 1:].strip()
        if not prompt_text:
            await send_as_agent(room.room_id, "Provide a prompt after the path option.", config, logger)
            return True
        project_dir = await resolve_letta_project_dir(room.room_id, agent_id, config, logger, override_path=path_override)
        if not project_dir:
            await send_as_agent(room.room_id, "No filesystem session found. Run /fs-link first.", config, logger)
            return True
        
        fs_run_prompt = prompt_text
        if event.sender.startswith("@oc_"):
            opencode_mxid = event.sender
            fs_run_prompt = f"""[MESSAGE FROM OPENCODE USER]

{prompt_text}

---
RESPONSE INSTRUCTION (OPENCODE BRIDGE):
This message is from an OpenCode user: {opencode_mxid}
When you respond to this message, you MUST include their @mention ({opencode_mxid}) 
in your response so the OpenCode bridge can route your reply to them.

Example: "{opencode_mxid} Here is my response..."
"""
            logger.info(f"[OPENCODE-FS-RUN] Injected @mention instruction for /fs-run command")
        
        await run_letta_code_task(
            room_id=room.room_id,
            agent_id=agent_id,
            agent_name=agent_name,
            project_dir=project_dir,
            prompt=fs_run_prompt,
            config=config,
            logger=logger,
            wrap_response=True,
        )
        return True
    if command == '/fs-task':
        normalized = args.lower()
        state_enabled = bool(state.get("enabled"))
        if not args:
            desired = not state_enabled
        elif normalized in ('on', 'enable', 'start'):
            desired = True
        elif normalized in ('off', 'disable', 'stop'):
            desired = False
        elif normalized in ('status', 'state'):
            status = "ENABLED" if state_enabled else "DISABLED"
            info = state.get("projectDir") or "not set"
            environment = "Letta Code" if state_enabled else "Cloud-only"
            await send_as_agent(
                room.room_id,
                f"Filesystem mode is {status}\nEnvironment: {environment}\nProject path: {info}",
                config,
                logger,
            )
            return True
        else:
            await send_as_agent(room.room_id, "Usage: /fs-task [on|off|status]", config, logger)
            return True
        if desired:
            project_dir = state.get("projectDir")
            if not project_dir:
                project_dir = await resolve_letta_project_dir(room.room_id, agent_id, config, logger)
            if not project_dir:
                await send_as_agent(room.room_id, "Link a project with /fs-link before enabling filesystem mode.", config, logger)
                return True
            update_letta_code_room_state(room.room_id, {"enabled": True, "projectDir": project_dir})
            await send_as_agent(
                room.room_id,
                f"Filesystem mode ENABLED\nEnvironment: Letta Code (path: {project_dir})\nAll new prompts will run inside the project workspace.",
                config,
                logger,
            )
        else:
            update_letta_code_room_state(room.room_id, {"enabled": False})
            await send_as_agent(
                room.room_id,
                "Filesystem mode DISABLED\nEnvironment: Cloud-only (standard Letta API).",
                config,
                logger,
            )
        return True
    return False


# Global variables for backwards compatibility
client = None
auth_manager_global = None

async def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0, logger: Optional[logging.Logger] = None):
    """
    Retry a function with exponential backoff
    """
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                if logger:
                    logger.error("All retry attempts failed", extra={"attempts": max_retries, "error": str(e)})
                raise
            
            delay = min(base_delay * (2 ** attempt), max_delay)
            if logger:
                logger.warning("Retry attempt failed, waiting before next try", 
                             extra={"attempt": attempt + 1, "delay": delay, "error": str(e)})
            await asyncio.sleep(delay)

async def get_agent_from_room_members(room_id: str, config: Config, logger: logging.Logger) -> Optional[tuple]:
    """
    Extract agent ID from room members by finding agent Matrix users.
    Returns (agent_id, agent_name) or None if not found.
    """
    try:
        # Get room members using Matrix API
        admin_token = config.matrix_token
        members_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/members"
        headers = {"Authorization": f"Bearer {admin_token}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(members_url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"Failed to get room members: {resp.status}")
                    return None
                
                members_data = await resp.json()
                members = members_data.get('chunk', [])
                
                # Look for agent users in room members
                # Agent user IDs follow pattern: @agent_<UUID>:matrix.oculair.ca
                from src.models.agent_mapping import AgentMappingDB
                db = AgentMappingDB()
                all_mappings = db.get_all()
                
                for member in members:
                    user_id = member.get('state_key')  # Matrix user ID
                    if not user_id:
                        continue
                    
                    # Check if this user_id matches any agent mapping
                    for mapping in all_mappings:
                        if mapping.matrix_user_id == user_id:
                            logger.info(f"Found agent via room members: {mapping.agent_name} ({mapping.agent_id})")
                            return (mapping.agent_id, mapping.agent_name)
                
                logger.warning(f"No agent users found in room {room_id} members")
                return None
                
    except Exception as e:
        logger.warning(f"Error extracting agent from room members: {e}", exc_info=True)
        return None


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
    from src.matrix.streaming import StepStreamReader, StreamingMessageHandler, StreamEventType
    from src.letta.client import get_letta_client, LettaConfig
    from src.voice.directive_parser import parse_directives, VoiceDirective, ImageDirective
    from src.voice.tts import is_tts_configured, synthesize_speech
    
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
                logger.info(f"[STREAMING] Found agent mapping: {agent_name_found} ({agent_id_to_use})")
            else:
                member_result = await get_agent_from_room_members(room_id, config, logger)
                if member_result:
                    agent_id_to_use, agent_name_found = member_result
        except Exception as e:
            logger.warning(f"[STREAMING] Could not query agent mappings: {e}")
    
    logger.info(f"[STREAMING] Sending message with streaming to agent {agent_name_found}")
    
    sdk_config = LettaConfig(
        base_url=config.letta_api_url,
        api_key=config.letta_token,
        timeout=config.letta_streaming_timeout,
        max_retries=3
    )
    letta_client = get_letta_client(sdk_config)
    
    conversation_id: Optional[str] = None
    if config.letta_conversations_enabled:
        try:
            from src.core.conversation_service import get_conversation_service
            conv_service = get_conversation_service(letta_client)
            conversation_id, created = await conv_service.get_or_create_room_conversation(
                room_id=room_id,
                agent_id=agent_id_to_use,
                room_member_count=room_member_count,
                user_mxid=sender_id if room_member_count == 2 else None,
            )
            logger.info(f"[CONVERSATIONS] Using conversation {conversation_id} (created={created})")
        except Exception as e:
            logger.warning(f"[CONVERSATIONS] Failed to get conversation, falling back to agents API: {e}")
            conversation_id = None
    
    use_gateway = config.letta_gateway_enabled
    gateway_client = None

    if use_gateway:
        try:
            from src.letta.ws_gateway_client import get_gateway_client
            gateway_client = await get_gateway_client(
                gateway_url=config.letta_gateway_url,
                idle_timeout=config.letta_gateway_idle_timeout,
                max_connections=config.letta_gateway_max_connections,
                api_key=config.letta_gateway_api_key or config.letta_token,
            )
            logger.info("[STREAMING] Gateway enabled, will attempt WS path first")
        except Exception as e:
            logger.warning(f"[STREAMING] Gateway client init failed, falling back to direct API: {e}")
            use_gateway = False

    try:
        from src.letta.approval_manager import disable_all_tool_approvals
        disable_all_tool_approvals(letta_client, agent_id_to_use)
    except Exception as e:
        logger.debug(f"[APPROVAL] Pre-message approval disable skipped: {e}")
    
    async def send_message(rid: str, content: str) -> str:
        """Send a progress message and return event_id (no reply context)"""
        event_id = await send_as_agent_with_event_id(rid, content, config, logger)
        return event_id or ""
    
    async def send_final_message(rid: str, content: str) -> str:
        final_content = content
        
        logger.debug(f"[OPENCODE] send_final_message: opencode_sender={opencode_sender}, content_len={len(content) if content else 0}")
        
        if opencode_sender:
            if opencode_sender not in content:
                logger.info(f"[OPENCODE] Agent response missing @mention, prepending {opencode_sender}")
                final_content = f"{opencode_sender} {content}"
            else:
                logger.debug(f"[OPENCODE] Agent response already contains @mention")
        
        poll_handled, remaining_text, poll_event_id = await process_agent_response(
            room_id=rid,
            response_text=final_content,
            config=config,
            logger_instance=logger,
            reply_to_event_id=reply_to_event_id,
            reply_to_sender=reply_to_sender
        )
        
        if poll_handled:
            logger.info(f"[POLL] Poll command handled in streaming, event_id: {poll_event_id}")
            if not remaining_text:
                return poll_event_id or ""
            final_content = remaining_text
        
        event_id = await send_as_agent_with_event_id(
            rid, final_content, config, logger,
            reply_to_event_id=reply_to_event_id,
            reply_to_sender=reply_to_sender
        )
        return event_id or ""
    
    async def delete_message(rid: str, event_id: str) -> None:
        await delete_message_as_agent(rid, event_id, config, logger)

    async def edit_message(rid: str, event_id: str, new_body: str) -> None:
        await edit_message_as_agent(rid, event_id, new_body, config, logger)

    if config.letta_streaming_live_edit:
        from src.matrix.streaming import LiveEditStreamingHandler
        logger.info("[STREAMING] Using live-edit mode (single message, edited in-place)")
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
    typing_manager = TypingIndicatorManager(room_id, config, logger) if config.letta_typing_enabled else None
    voice_logger = logging.getLogger("matrix_client.voice")
    
    try:
        if typing_manager:
            await typing_manager.start()
        
        event_source = None
        if use_gateway and gateway_client and not isinstance(message_body, list):
            from src.letta.gateway_stream_reader import stream_via_gateway
            from src.letta.ws_gateway_client import GatewayUnavailableError
            try:
                event_source = stream_via_gateway(
                    client=gateway_client,
                    agent_id=agent_id_to_use,
                    message=message_body,
                    conversation_id=conversation_id,
                    max_tool_calls=config.letta_max_tool_calls,
                    source={"channel": "matrix", "chatId": room_id},
                )
                logger.info("[STREAMING] Using WS gateway as event source")
            except GatewayUnavailableError as gw_err:
                logger.warning(f"[STREAMING] Gateway unavailable, falling back: {gw_err}")
                event_source = None

        if event_source is None:
            fallback_reader = StepStreamReader(
                letta_client=letta_client,
                include_reasoning=False,
                include_pings=True,
                timeout=config.letta_streaming_timeout,
                idle_data_timeout=config.letta_streaming_idle_timeout,
                max_tool_calls=config.letta_max_tool_calls,
            )
            logger.info("[STREAMING] Using direct Letta API as event source")
            event_source = fallback_reader.stream_message(
                agent_id=agent_id_to_use,
                message=message_body,
                conversation_id=conversation_id,
            )

        async for event in event_source:
            logger.debug(f"[STREAMING] Event: {event.type.value}")

            if event.type == StreamEventType.ASSISTANT and event.content:
                parse_result = parse_directives(event.content)
                voice_logger.debug("[VOICE-DEBUG] Parsed content (%d chars): directives=%d, clean=%r", len(event.content), len(parse_result.directives), event.content[:100])

                if parse_result.directives:
                    # Process each directive (voice or image)
                    transcript_parts = []
                    caption_parts = []

                    for directive in parse_result.directives:
                        if isinstance(directive, VoiceDirective):
                            if not is_tts_configured():
                                voice_logger.info("[VOICE] Voice directive found but TTS is not configured")
                                continue
                            audio_data = await synthesize_speech(directive.text)
                            if not audio_data:
                                voice_logger.warning("[VOICE] TTS synthesis returned no audio")
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
                                voice_logger.info("[VOICE] Sent voice message event %s", audio_event_id)
                                transcript_parts.append(directive.text)
                            else:
                                voice_logger.warning("[VOICE] Failed to upload/send voice message")

                        elif isinstance(directive, ImageDirective):
                            image_event_id = await fetch_and_send_image(
                                room_id=room_id,
                                image_url=directive.url,
                                alt=directive.alt,
                                config=config,
                                logger=voice_logger,
                            )
                            if image_event_id:
                                voice_logger.info("[IMAGE] Sent image event %s", image_event_id)
                                if directive.caption:
                                    caption_parts.append(directive.caption)
                            else:
                                voice_logger.warning("[IMAGE] Failed to fetch/send image from %s", directive.url)

                    # Build the text to display: clean_text + voice transcripts + image captions
                    display_parts = []
                    if parse_result.clean_text.strip():
                        display_parts.append(parse_result.clean_text.strip())
                    if transcript_parts:
                        display_parts.append("\ud83d\udde3\ufe0f " + " ".join(transcript_parts))
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
        logger.error(f"[STREAMING] Exception during streaming: {e}", exc_info=True)
        await handler.cleanup()
        raise LettaApiError(f"Streaming error: {e}")
    finally:
        if typing_manager:
            await typing_manager.stop()
    
    if not final_response:
        final_response = "Agent processed the request (no text response)."
    
    return final_response


async def upload_and_send_audio(
    room_id: str,
    audio_data: bytes,
    filename: str,
    mimetype: str,
    config,
    logger,
    duration_ms: Optional[int] = None,
) -> Optional[str]:
    try:
        from src.core.mapping_service import get_mapping_by_room_id

        agent_mapping = get_mapping_by_room_id(room_id)
        if not agent_mapping:
            logger.warning("[VOICE] No agent mapping found for room %s", room_id)
            return None

        agent_username = agent_mapping["matrix_user_id"].split(":")[0].replace("@", "")
        agent_password = agent_mapping["matrix_password"]

        login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
        login_data = {
            "type": "m.login.password",
            "user": agent_username,
            "password": agent_password,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(login_url, json=login_data) as login_response:
                if login_response.status != 200:
                    error_text = await login_response.text()
                    logger.error(
                        "[VOICE] Failed to login as agent %s: %s - %s",
                        agent_username,
                        login_response.status,
                        error_text,
                    )
                    return None

                auth_data = await login_response.json()
                agent_token = auth_data.get("access_token")
                if not agent_token:
                    logger.error("[VOICE] No token received for agent %s", agent_username)
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

            async with session.put(message_url, headers=message_headers, json=message_data) as send_response:
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
        logger.error(f"[VOICE] Exception while uploading/sending audio: {e}", exc_info=True)
        return None

async def fetch_and_send_image(
    room_id: str,
    image_url: str,
    alt: str,
    config,
    logger,
) -> Optional[str]:
    """Fetch an image from a URL, upload to Matrix media repo, and send as m.image."""
    try:
        from src.core.mapping_service import get_mapping_by_room_id

        agent_mapping = get_mapping_by_room_id(room_id)
        if not agent_mapping:
            logger.warning("[IMAGE] No agent mapping found for room %s", room_id)
            return None

        agent_username = agent_mapping["matrix_user_id"].split(":")[0].replace("@", "")
        agent_password = agent_mapping["matrix_password"]

        fetch_headers = {"User-Agent": "MatrixBridge/1.0"}
        async with aiohttp.ClientSession() as session:
            # Step 1: Fetch the image from the URL
            try:
                async with session.get(image_url, headers=fetch_headers, timeout=aiohttp.ClientTimeout(total=30)) as img_response:
                    if img_response.status != 200:
                        logger.error("[IMAGE] Failed to fetch image from %s: %s", image_url, img_response.status)
                        return None
                    image_data = await img_response.read()
                    content_type = img_response.headers.get("Content-Type", "image/png")
                    # Normalize content type (strip params like charset)
                    mimetype = content_type.split(";")[0].strip()
                    if not mimetype.startswith("image/"):
                        mimetype = "image/png"
            except Exception as fetch_err:
                logger.error("[IMAGE] Exception fetching image from %s: %s", image_url, fetch_err)
                return None

            if not image_data or len(image_data) < 100:
                logger.warning("[IMAGE] Fetched image too small (%d bytes) from %s", len(image_data) if image_data else 0, image_url)
                return None

            # Derive filename from URL
            from urllib.parse import urlparse
            url_path = urlparse(image_url).path
            filename = url_path.split("/")[-1] if "/" in url_path else "image.png"
            if not filename or "." not in filename:
                ext = mimetype.split("/")[-1].replace("jpeg", "jpg")
                filename = f"image.{ext}"

            logger.info("[IMAGE] Fetched %s (%d bytes, %s)", filename, len(image_data), mimetype)

            # Step 2: Login as agent
            login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
            login_data = {
                "type": "m.login.password",
                "user": agent_username,
                "password": agent_password,
            }

            async with session.post(login_url, json=login_data) as login_response:
                if login_response.status != 200:
                    error_text = await login_response.text()
                    logger.error("[IMAGE] Failed to login as agent %s: %s - %s", agent_username, login_response.status, error_text)
                    return None
                auth_data = await login_response.json()
                agent_token = auth_data.get("access_token")
                if not agent_token:
                    logger.error("[IMAGE] No token received for agent %s", agent_username)
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
            ) as upload_response:
                if upload_response.status != 200:
                    upload_error = await upload_response.text()
                    logger.error("[IMAGE] Upload failed: %s - %s", upload_response.status, upload_error)
                    return None
                upload_result = await upload_response.json()
                content_uri = upload_result.get("content_uri")
                if not content_uri:
                    logger.error("[IMAGE] Upload response missing content_uri")
                    return None

            # Step 4: Send m.image event
            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            message_headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json",
            }

            message_data = {
                "msgtype": "m.image",
                "url": content_uri,
                "body": alt or filename,
                "info": {
                    "mimetype": mimetype,
                    "size": len(image_data),
                },
            }

            async with session.put(message_url, headers=message_headers, json=message_data) as send_response:
                if send_response.status != 200:
                    send_error = await send_response.text()
                    logger.error("[IMAGE] Send failed: %s - %s", send_response.status, send_error)
                    return None
                send_result = await send_response.json()
                event_id = send_result.get("event_id")
                logger.info("[IMAGE] Sent image event_id: %s", event_id)
                return event_id

    except Exception as e:
        logger.error(f"[IMAGE] Exception while fetching/sending image: {e}", exc_info=True)
        return None

async def send_to_letta_api(
    message_body: Union[str, list],
    sender_id: str,
    config: Config,
    logger: logging.Logger,
    room_id: Optional[str] = None,
    room_member_count: int = 3,
) -> str:
    if sender_id.startswith('@'):
        username = sender_id[1:].split(':')[0]
    else:
        username = sender_id
    
    agent_id_to_use = config.letta_agent_id
    agent_name_found = "DEFAULT"
    routing_method = "default"

    if room_id:
        try:
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            
            mapping = db.get_by_room_id(room_id)
            if mapping:
                agent_id_to_use = str(mapping.agent_id)
                agent_name_found = str(mapping.agent_name)
                routing_method = "database_room_id"
                logger.info(f"Found agent mapping in DB for room {room_id}: {agent_name_found} ({agent_id_to_use})")
            else:
                logger.info(f"No direct mapping for room {room_id}, checking room members...")
                
                member_result = await get_agent_from_room_members(room_id, config, logger)
                if member_result:
                    agent_id_to_use, agent_name_found = member_result
                    routing_method = "room_members"
                    logger.info(f"Resolved agent via room members: {agent_name_found} ({agent_id_to_use})")
                else:
                    all_mappings = db.get_all()
                    logger.warning(f"No agent mapping found for room {room_id}, using default agent")
                    logger.info(f"Room has no mapping. Total mappings in DB: {len(all_mappings)}")
                
        except Exception as e:
            logger.warning(f"Could not query agent mappings database: {e}")
    
    logger.warning(f"[DEBUG] AGENT ROUTING: Room {room_id} -> Agent {agent_id_to_use}")
    logger.warning(f"[DEBUG] Agent Name: {agent_name_found}")
    logger.warning(f"[DEBUG] Routing Method: {routing_method}")
    
    if isinstance(message_body, str):
        message_preview = message_body[:100] + "..." if len(message_body) > 100 else message_body
    else:
        message_preview = f"[multimodal content: {len(message_body)} parts]"

    logger.info("Sending message to Letta API", extra={
        "message_preview": message_preview,
        "sender": username,
        "agent_id": agent_id_to_use,
        "room_id": room_id
    })

    from src.letta.client import get_letta_client, LettaConfig
    
    sdk_config = LettaConfig(
        base_url=config.letta_api_url,
        api_key=config.letta_token,
        timeout=300.0,
        max_retries=3
    )
    letta_client = get_letta_client(sdk_config)
    
    conversation_id: Optional[str] = None
    if config.letta_conversations_enabled and room_id:
        try:
            from src.core.conversation_service import get_conversation_service
            conv_service = get_conversation_service(letta_client)
            conversation_id, created = await conv_service.get_or_create_room_conversation(
                room_id=room_id,
                agent_id=agent_id_to_use,
                room_member_count=room_member_count,
                user_mxid=sender_id if room_member_count == 2 else None,
            )
            logger.info(f"[CONVERSATIONS] Using conversation {conversation_id} (created={created})")
        except Exception as e:
            logger.warning(f"[CONVERSATIONS] Failed to get conversation, falling back to agents API: {e}")
            conversation_id = None

    async def _send_to_letta():
        current_agent_id = agent_id_to_use
        
        logger.warning(f"[DEBUG] SENDING TO LETTA API (SDK) - Agent ID: {current_agent_id}")
        
        def _sync_send():
            if conversation_id:
                logger.debug(f"[API] Using Conversations API: {conversation_id}")
                from src.core.retry import is_conversation_busy_error, ConversationBusyError
                import time
                
                max_retries = 3
                last_error: Optional[Exception] = None
                
                for attempt in range(max_retries + 1):
                    try:
                        stream = letta_client.conversations.messages.create(
                            conversation_id=conversation_id,
                            input=message_body,
                            streaming=False,
                        )
                        messages = list(stream)
                        return type('Response', (), {'messages': messages})()
                    except Exception as e:
                        if is_conversation_busy_error(e):
                            last_error = e
                            if attempt < max_retries:
                                delay = min(1.0 * (2 ** attempt), 8.0)
                                logger.warning(
                                    f"[API-RETRY] Conversation {conversation_id} is busy, "
                                    f"attempt {attempt + 1}/{max_retries + 1}, "
                                    f"retrying in {delay:.1f}s"
                                )
                                time.sleep(delay)
                                continue
                            else:
                                logger.error(
                                    f"[API-RETRY] Conversation {conversation_id} still busy "
                                    f"after {max_retries + 1} attempts"
                                )
                                raise ConversationBusyError(
                                    conversation_id=conversation_id,
                                    attempts=max_retries + 1,
                                    last_error=e
                                ) from e
                        else:
                            raise
                
                raise ConversationBusyError(
                    conversation_id=conversation_id,
                    attempts=max_retries + 1,
                    last_error=last_error
                )
            else:
                logger.debug(f"[API] Using Agents API: {current_agent_id}")
                if isinstance(message_body, list):
                    return letta_client.agents.messages.create(
                        agent_id=current_agent_id,
                        input=message_body,
                    )
                return letta_client.agents.messages.create(
                    agent_id=current_agent_id,
                    messages=[{"role": "user", "content": message_body}]
                )
        response: Any = await asyncio.to_thread(_sync_send)
        
        if hasattr(response, 'model_dump'):
            result = response.model_dump()
        elif hasattr(response, 'dict'):
            result = response.dict()
        else:
            result = {"messages": []}
            if hasattr(response, 'messages'):
                for msg in response.messages:
                    if hasattr(msg, 'model_dump'):
                        result["messages"].append(msg.model_dump())
                    elif hasattr(msg, 'dict'):
                        result["messages"].append(msg.dict())
                    else:
                        result["messages"].append({"message_type": getattr(msg, 'message_type', 'unknown')})
        
        logger.debug(f"Received Letta API response via SDK: {type(response)}")
        
        return result

    typing_manager = TypingIndicatorManager(room_id, config, logger) if (config.letta_typing_enabled and room_id) else None
    
    try:
        if typing_manager:
            await typing_manager.start()
        
        gateway_result: Optional[str] = None
        if config.letta_gateway_enabled and not isinstance(message_body, list):
            try:
                from src.letta.ws_gateway_client import get_gateway_client, GatewayUnavailableError
                from src.letta.gateway_stream_reader import collect_via_gateway
                gw_client = await get_gateway_client(
                    gateway_url=config.letta_gateway_url,
                    idle_timeout=config.letta_gateway_idle_timeout,
                    max_connections=config.letta_gateway_max_connections,
                    api_key=config.letta_gateway_api_key or config.letta_token,
                )
                gateway_result = await collect_via_gateway(
                    client=gw_client,
                    agent_id=agent_id_to_use,
                    message=message_body,
                    conversation_id=conversation_id,
                    source={"channel": "matrix", "chatId": room_id} if room_id else None,
                )
                if gateway_result:
                    logger.info(f"[API] Got response via WS gateway ({len(gateway_result)} chars)")
            except Exception as gw_err:
                logger.warning(f"[API] Gateway failed, falling back to direct API: {gw_err}")
                gateway_result = None

        if gateway_result:
            return gateway_result

        response = await retry_with_backoff(_send_to_letta, max_retries=3, logger=logger)
        
        if response and 'messages' in response:
            messages = response['messages']
            assistant_messages = []
            
            # Debug: Log the response structure
            logger.debug(f"Response has {len(messages)} messages")
            
            # Look for assistant messages or tool calls in the response
            for message in messages:
                msg_type = message.get('message_type')
                
                # Standard assistant message
                if msg_type == 'assistant_message':
                    content = message.get('content')
                    if content:
                        assistant_messages.append(str(content))
                        logger.warning(f"[DEBUG] LETTA RESPONSE: {str(content)[:100]}")
                
                # Tool call (agent using matrix_agent_message)
                elif msg_type == 'tool_call_message':
                    tool_call = message.get('tool_call', {})
                    if tool_call.get('name') == 'matrix_agent_message':
                        # Agent is sending inter-agent message - extract the message text
                        try:
                            import json as json_lib
                            args = json_lib.loads(tool_call.get('arguments', '{}'))
                            inter_msg = args.get('message', '')
                            if inter_msg:
                                assistant_messages.append(f"[Sent to another agent]: {inter_msg}")
                                logger.warning(f"[DEBUG] INTER-AGENT TOOL CALL: {inter_msg[:100]}")
                        except Exception as e:
                            logger.warning(f"Failed to parse tool call arguments: {e}")
            
            # If we found messages, return them
            if assistant_messages:
                result = " ".join(assistant_messages)
                logger.info("Successfully processed Letta response", extra={
                    "response_length": len(result),
                    "message_count": len(assistant_messages)
                })
                return result
            else:
                logger.warning("No assistant messages or tool calls found in response")
                logger.warning(f"Response structure: {messages[:3] if len(messages) > 0 else 'empty'}")
                return "Letta agent responded (check other agent's room for message)."
        else:
            logger.warning("Empty response from Letta API")
            return "Letta API connection successful, but no response content."

    except aiohttp.ClientResponseError as e:
        # Legacy aiohttp error handling (kept for backward compatibility)
        logger.error("Letta API HTTP error", extra={"status_code": e.status, "message": str(e.message)[:200]})
        raise LettaApiError(f"Letta API returned error {e.status}", e.status, str(e.message)[:200])
    except Exception as e:
        # Handle SDK errors and other exceptions
        error_str = str(e)
        if "Error code:" in error_str:
            # Parse SDK error format: "Error code: 500 - {...}"
            import re
            match = re.search(r"Error code: (\d+)", error_str)
            status_code = int(match.group(1)) if match else 500
            logger.error("Letta SDK API error", extra={"status_code": status_code, "message": error_str[:200]})
            raise LettaApiError(f"Letta API returned error {status_code}", status_code, error_str[:200])
        else:
            logger.error("Unexpected error in Letta API call", extra={"error": error_str}, exc_info=True)
            raise LettaApiError(f"An unexpected error occurred with the Letta SDK: {e}")
    finally:
        if typing_manager:
            await typing_manager.stop()

async def delete_message_as_agent(room_id: str, event_id: str, config: Config, logger: logging.Logger) -> bool:
    """Redact (delete) a message as the agent user for this room"""
    try:
        # Load agent mapping from database
        from src.core.mapping_service import get_mapping_by_room_id
        agent_mapping = get_mapping_by_room_id(room_id)
        
        if not agent_mapping:
            logger.warning(f"No agent mapping found for room {room_id}")
            return False
        
        agent_name = agent_mapping.get("agent_name", "Unknown")
        logger.debug(f"[DELETE_AS_AGENT] Attempting to delete message as agent: {agent_name} in room {room_id}")
        
        # Login as the agent user
        agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
        agent_password = agent_mapping["matrix_password"]
        
        login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
        login_data = {
            "type": "m.login.password",
            "user": agent_username,
            "password": agent_password
        }
        
        async with aiohttp.ClientSession() as session:
            # Login
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Failed to login as agent {agent_username}: {response.status} - {error_text}")
                    return False
                
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                
                if not agent_token:
                    logger.error(f"No token received for agent {agent_username}")
                    return False
            
            # Redact (delete) the message
            txn_id = str(uuid.uuid4())
            redact_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/redact/{event_id}/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json"
            }
            
            # Reason for redaction (optional)
            redact_data = {
                "reason": "Progress message replaced"
            }
            
            async with session.put(redact_url, headers=headers, json=redact_data) as response:
                if response.status == 200:
                    logger.debug(f"[DELETE_AS_AGENT] Successfully deleted message {event_id}")
                    return True
                else:
                    response_text = await response.text()
                    logger.warning(f"[DELETE_AS_AGENT] Failed to delete message: {response.status} - {response_text}")
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
    try:
        from src.core.mapping_service import get_mapping_by_room_id
        agent_mapping = get_mapping_by_room_id(room_id)

        if not agent_mapping:
            logger.warning(f"[EDIT_AS_AGENT] No agent mapping for room {room_id}")
            return False

        agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
        agent_password = agent_mapping["matrix_password"]

        login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
        login_data = {"type": "m.login.password", "user": agent_username, "password": agent_password}

        async with aiohttp.ClientSession() as session:
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    logger.error(f"[EDIT_AS_AGENT] Login failed: {response.status}")
                    return False
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                if not agent_token:
                    return False

            txn_id = str(uuid.uuid4())
            msg_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"}

            message_data = {
                "msgtype": "m.text",
                "body": f"* {new_body}",
                "m.new_content": {
                    "msgtype": "m.text",
                    "body": new_body,
                },
                "m.relates_to": {
                    "rel_type": "m.replace",
                    "event_id": event_id,
                },
            }

            async with session.put(msg_url, headers=headers, json=message_data) as response:
                if response.status == 200:
                    logger.debug(f"[EDIT_AS_AGENT] Edited message {event_id}")
                    return True
                else:
                    resp_text = await response.text()
                    logger.warning(f"[EDIT_AS_AGENT] Edit failed: {response.status} - {resp_text}")
                    return False

    except Exception as e:
        logger.error(f"[EDIT_AS_AGENT] Exception: {e}", exc_info=True)
        return False


async def _get_agent_typing_context(room_id: str, config: Config, logger: logging.Logger) -> Optional[Dict[str, str]]:
    """Resolve agent credentials and build reusable typing context for a room."""
    from src.core.mapping_service import get_mapping_by_room_id
    agent_mapping = get_mapping_by_room_id(room_id)
    if not agent_mapping:
        return None

    agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
    agent_password = agent_mapping["matrix_password"]

    login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
    login_data = {"type": "m.login.password", "user": agent_username, "password": agent_password}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    return None
                auth_data = await response.json()
                token = auth_data.get("access_token")
                if not token:
                    return None
    except Exception as e:
        logger.debug(f"[TYPING] Login failed: {e}")
        return None

    from urllib.parse import quote
    encoded_user_id = quote(agent_mapping['matrix_user_id'], safe='')
    typing_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/typing/{encoded_user_id}"

    return {"token": token, "typing_url": typing_url}


# Matches lettabot pattern: 4s heartbeat < 5s timeout = seamless indicator
_TYPING_HEARTBEAT_INTERVAL = 4.0
_TYPING_TIMEOUT_MS = 5000


async def _put_typing(session: aiohttp.ClientSession, typing_url: str, token: str, typing: bool, timeout_ms: int, logger: logging.Logger) -> bool:
    """Fire a single typing PUT request using a pre-authenticated token."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    typing_data: Dict[str, Any] = {"typing": typing}
    if typing:
        typing_data["timeout"] = timeout_ms

    try:
        async with session.put(typing_url, headers=headers, json=typing_data) as response:
            if response.status == 200:
                if not typing:
                    # Workaround: force immediate expiry on servers that ignore typing=false
                    expire_data = {"typing": True, "timeout": 1}
                    async with session.put(typing_url, headers=headers, json=expire_data):
                        pass
                return True
            else:
                logger.debug(f"[TYPING] PUT failed: {response.status}")
                return False
    except Exception as e:
        logger.debug(f"[TYPING] PUT exception: {e}")
        return False


async def set_typing_as_agent(room_id: str, typing: bool, config: Config, logger: logging.Logger, timeout_ms: int = 5000) -> bool:
    """Set typing indicator as the agent user (one-shot, re-authenticates each call)."""
    ctx = await _get_agent_typing_context(room_id, config, logger)
    if not ctx:
        return False
    async with aiohttp.ClientSession() as session:
        return await _put_typing(session, ctx["typing_url"], ctx["token"], typing, timeout_ms, logger)


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
                    await _put_typing(self._session, self._ctx["typing_url"], self._ctx["token"], True, _TYPING_TIMEOUT_MS, self.logger)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=_TYPING_HEARTBEAT_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            if self._ctx and self._session:
                await _put_typing(self._session, self._ctx["typing_url"], self._ctx["token"], False, _TYPING_TIMEOUT_MS, self.logger)

    async def start(self):
        self._stop_event.clear()
        self._ctx = await _get_agent_typing_context(self.room_id, self.config, self.logger)
        if not self._ctx:
            self.logger.debug(f"[TYPING] No agent context for room {self.room_id}, skipping")
            return
        self._session = aiohttp.ClientSession()
        self._typing_task = asyncio.create_task(self._typing_loop())
        self.logger.debug(f"[TYPING] Started 4s heartbeat for room {self.room_id}")

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
            await _put_typing(self._session, self._ctx["typing_url"], self._ctx["token"], False, _TYPING_TIMEOUT_MS, self.logger)
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


async def send_as_agent_with_event_id(
    room_id: str, 
    message: str, 
    config: Config, 
    logger: logging.Logger,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None
) -> Optional[str]:
    """
    Send a message as the agent user for this room and return the event ID.
    
    Args:
        room_id: The Matrix room ID to send the message to
        message: The message text to send
        config: Application configuration
        logger: Logger instance
        reply_to_event_id: Optional event ID to reply to (creates a rich reply thread)
        reply_to_sender: Optional sender of the original message (for m.mentions)
    
    Returns the event_id on success, None on failure.
    """
    try:
        # Load agent mapping from database
        from src.core.mapping_service import get_mapping_by_room_id
        agent_mapping = get_mapping_by_room_id(room_id)
        
        if not agent_mapping:
            logger.warning(f"No agent mapping found for room {room_id}")
            return None
        
        agent_name = agent_mapping.get("agent_name", "Unknown")
        logger.debug(f"[SEND_AS_AGENT] Sending as agent: {agent_name} in room {room_id}")
        
        # Login as the agent user
        agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
        agent_password = agent_mapping["matrix_password"]
        
        login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
        login_data = {
            "type": "m.login.password",
            "user": agent_username,
            "password": agent_password
        }
        
        async with aiohttp.ClientSession() as session:
            # Login
            async with session.post(login_url, json=login_data) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Failed to login as agent {agent_username}: {response.status} - {error_text}")
                    if "M_FORBIDDEN" in error_text or response.status == 403:
                        try:
                            from src.matrix.alerting import alert_auth_failure
                            asyncio.get_event_loop().create_task(
                                alert_auth_failure(agent_username, room_id)
                            )
                        except Exception:
                            pass
                    return None
                
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                
                if not agent_token:
                    logger.error(f"No token received for agent {agent_username}")
                    return None
            
            txn_id = str(uuid.uuid4())
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message/{txn_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json"
            }
            
            message_data: Dict[str, Any] = {
                "msgtype": "m.text",
                "body": message
            }
            
            # Add rich reply relationship if replying to a specific message
            if reply_to_event_id:
                message_data["m.relates_to"] = {
                    "m.in_reply_to": {
                        "event_id": reply_to_event_id
                    }
                }
                # Optionally mention the original sender
                if reply_to_sender:
                    message_data["m.mentions"] = {
                        "user_ids": [reply_to_sender]
                    }
                logger.debug(f"[SEND_AS_AGENT] Creating rich reply to event {reply_to_event_id}")
            
            async with session.put(message_url, headers=headers, json=message_data) as response:
                if response.status == 200:
                    result = await response.json()
                    event_id = result.get("event_id")
                    logger.debug(f"[SEND_AS_AGENT] Sent message, event_id: {event_id}" + 
                                (f" (reply to {reply_to_event_id})" if reply_to_event_id else ""))
                    return event_id
                else:
                    response_text = await response.text()
                    logger.error(f"[SEND_AS_AGENT] Failed to send message: {response.status} - {response_text}")
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
    reply_to_sender: Optional[str] = None
) -> bool:
    """
    Send a message as the agent user for this room.
    
    Args:
        room_id: The Matrix room ID to send the message to
        message: The message text to send
        config: Application configuration
        logger: Logger instance
        reply_to_event_id: Optional event ID to reply to (creates a rich reply thread)
        reply_to_sender: Optional sender of the original message (for m.mentions)
    
    Returns True on success, False on failure.
    """
    # Use the event_id version and convert to bool
    event_id = await send_as_agent_with_event_id(
        room_id, message, config, logger, 
        reply_to_event_id=reply_to_event_id,
        reply_to_sender=reply_to_sender
    )
    return event_id is not None


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

        if isinstance(file_result, list):
            if config.letta_streaming_enabled:
                await send_to_letta_api_streaming(file_result, event.sender, config, logger, room.room_id)
            else:
                await send_to_letta_api(file_result, event.sender, config, logger, room.room_id)
        elif isinstance(file_result, str):
            if config.letta_streaming_enabled:
                await send_to_letta_api_streaming(file_result, event.sender, config, logger, room.room_id)
            else:
                await send_to_letta_api(file_result, event.sender, config, logger, room.room_id)
    
    except FileUploadError as e:
        logger.error(f"File upload error: {e}")
        # File handler will send notifications
    
    except Exception as e:
        logger.error(f"Unexpected error in file callback: {e}", exc_info=True)

async def _process_letta_message(
    event_body: str,
    event_sender: str,
    event_source: Optional[Dict],
    original_event_id: Optional[str],
    room_id: str,
    room_display_name: str,
    room_agent_id: Optional[str],
    config: Config,
    logger: logging.Logger,
    client: Optional[AsyncClient] = None,
) -> None:
    try:
        from src.core.mapping_service import get_mapping_by_matrix_user
        
        if client and 'auth_manager_global' in globals() and auth_manager_global is not None:
            await auth_manager_global.ensure_valid_token(client)
        
        message_to_send = event_body
        event_timestamp = None
        if event_source and isinstance(event_source, dict):
            event_timestamp = event_source.get("origin_server_ts")
        if event_timestamp is None:
            event_timestamp = int(time.time() * 1000)
        is_inter_agent_message = False
        from_agent_id = None
        from_agent_name = None
        
        is_agent_mail_message = False
        agent_mail_metadata = None
        
        if event_source and isinstance(event_source, dict):
            content = event_source.get("content", {})
            from_agent_id = content.get("m.letta.from_agent_id")
            from_agent_name = content.get("m.letta.from_agent_name")
            
            if from_agent_id and from_agent_name:
                is_inter_agent_message = True
                logger.info(f"Detected inter-agent message (via metadata) from {from_agent_name} ({from_agent_id})")
            
            agent_mail_metadata = content.get("m.agent_mail")
            if agent_mail_metadata:
                is_agent_mail_message = True
                logger.info(f"[REVERSE-BRIDGE] Detected Agent Mail message from {agent_mail_metadata.get('sender_friendly_name', 'Unknown')}")
        
        if not is_inter_agent_message:
            sender_agent_mapping = get_mapping_by_matrix_user(event_sender)
            if sender_agent_mapping:
                from_agent_id = sender_agent_mapping.get("agent_id")
                from_agent_name = sender_agent_mapping.get("agent_name", "Unknown Agent")
                is_inter_agent_message = True
                logger.info(f"Detected inter-agent message (via sender check) from {from_agent_name} ({from_agent_id})")
        
        if is_inter_agent_message and from_agent_id and from_agent_name:
            raw_body = event_body or ""
            payload_lines = raw_body.splitlines()
            if payload_lines and payload_lines[0].startswith("[Inter-Agent Message from"):
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
            )
            logger.info(f"[INTER-AGENT CONTEXT] Enhanced message for receiving agent:")
            logger.info(f"[INTER-AGENT CONTEXT] Sender: {from_agent_name} ({from_agent_id})")
            logger.info(f"[INTER-AGENT CONTEXT] Full enhanced message:\n{message_to_send}")

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
            )
            logger.info(f"[OPENCODE] Detected message from OpenCode identity: {opencode_mxid}")
            logger.info(f"[OPENCODE] Injected @mention instruction for response routing")
        elif not is_inter_agent_message:
            room_display = room_display_name or room_id
            message_to_send = matrix_formatter.format_message_envelope(
                channel="Matrix",
                chat_id=room_id,
                message_id=original_event_id,
                sender=event_sender,
                sender_name=event_sender,
                timestamp=event_timestamp,
                text=event_body,
                is_group=True,
                group_name=room_display,
                is_mentioned=False,
            )
            logger.debug(f"[MATRIX-CONTEXT] Added context for sender {event_sender}")

        if config.letta_streaming_enabled:
            logger.info("[STREAMING] Using streaming mode for Letta API call")
            letta_response = await send_to_letta_api_streaming(
                message_to_send, event_sender, config, logger, room_id,
                reply_to_event_id=None,
                reply_to_sender=None,
                opencode_sender=opencode_mxid
            )
            logger.info("Successfully processed streaming response", extra={
                "response_length": len(letta_response),
                "room_id": room_id,
                "streaming": True,
                "reply_to": original_event_id
            })
            
            if is_agent_mail_message and agent_mail_metadata and room_agent_id:
                try:
                    responder_code_name = get_agent_code_name(room_agent_id, logger)
                    sender_code_name = agent_mail_metadata.get('sender_code_name')
                    
                    if responder_code_name and sender_code_name:
                        original_subject = agent_mail_metadata.get('subject', 'No subject')
                        original_msg_id = agent_mail_metadata.get('message_id')
                        thread_id = agent_mail_metadata.get('thread_id')
                        
                        await forward_to_agent_mail(
                            sender_code_name=responder_code_name,
                            recipient_code_name=sender_code_name,
                            subject=f"Re: {original_subject}",
                            body_md=letta_response,
                            thread_id=thread_id,
                            original_message_id=original_msg_id,
                            logger=logger
                        )
                    else:
                        logger.warning(f"[REVERSE-BRIDGE] Could not get code names: responder={responder_code_name}, sender={sender_code_name}")
                except Exception as bridge_error:
                    logger.error(f"[REVERSE-BRIDGE] Error forwarding to Agent Mail: {bridge_error}", exc_info=True)
        else:
            letta_response = await send_to_letta_api(message_to_send, event_sender, config, logger, room_id)
            
            if opencode_mxid and opencode_mxid not in letta_response:
                logger.info(f"[OPENCODE] Agent response missing @mention, prepending {opencode_mxid}")
                letta_response = f"{opencode_mxid} {letta_response}"
            
            sent_as_agent = False
            
            poll_handled, remaining_text, poll_event_id = await process_agent_response(
                room_id=room_id,
                response_text=letta_response,
                config=config,
                logger_instance=logger,
                reply_to_event_id=None,
                reply_to_sender=None
            )
            
            if poll_handled:
                logger.info(f"[POLL] Poll command handled, event_id: {poll_event_id}")
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
                    reply_to_sender=None
                )
            
            if not sent_as_agent:
                if client:
                    logger.warning("Failed to send as agent, falling back to main client")
                    message_content: Dict[str, Any] = {"msgtype": "m.text", "body": letta_response}
                    await client.room_send(
                        room_id,
                        "m.room.message",
                        message_content
                    )
                else:
                    logger.error("No client available and agent send failed")
            
            logger.info("Successfully sent response to Matrix", extra={
                "response_length": len(letta_response),
                "room_id": room_id,
                "sent_as_agent": sent_as_agent,
                "reply_to": original_event_id
            })
        
        if is_agent_mail_message and agent_mail_metadata and room_agent_id:
            try:
                responder_code_name = get_agent_code_name(room_agent_id, logger)
                sender_code_name = agent_mail_metadata.get('sender_code_name')
                
                if responder_code_name and sender_code_name:
                    original_subject = agent_mail_metadata.get('subject', 'No subject')
                    original_msg_id = agent_mail_metadata.get('message_id')
                    thread_id = agent_mail_metadata.get('thread_id')
                    
                    await forward_to_agent_mail(
                        sender_code_name=responder_code_name,
                        recipient_code_name=sender_code_name,
                        subject=f"Re: {original_subject}",
                        body_md=letta_response,
                        thread_id=thread_id,
                        original_message_id=original_msg_id,
                        logger=logger
                    )
                else:
                    logger.warning(f"[REVERSE-BRIDGE] Could not get code names: responder={responder_code_name}, sender={sender_code_name}")
            except Exception as bridge_error:
                logger.error(f"[REVERSE-BRIDGE] Error forwarding to Agent Mail: {bridge_error}", exc_info=True)
        
    except LettaApiError as e:
        logger.error("Letta API error in background task", extra={
            "error": str(e),
            "status_code": e.status_code,
            "sender": event_sender
        })
        try:
            from src.matrix.alerting import alert_streaming_timeout, alert_letta_error
            if "timeout" in str(e).lower() or "Timeout" in str(e):
                await alert_streaming_timeout(room_agent_id or "unknown", room_id, "streaming", config.letta_streaming_timeout)
            else:
                await alert_letta_error(room_agent_id or "unknown", room_id, str(e))
        except Exception:
            pass
        error_message = f"Sorry, I encountered an error while processing your message: {str(e)[:100]}"
        try:
            sent_as_agent = await send_as_agent(
                room_id, error_message, config, logger,
                reply_to_event_id=None,
                reply_to_sender=None
            )
            if not sent_as_agent and client:
                error_content: Dict[str, Any] = {"msgtype": "m.text", "body": error_message}
                await client.room_send(
                    room_id,
                    "m.room.message",
                    error_content
                )
        except Exception as send_error:
            logger.error("Failed to send error message", extra={"error": str(send_error)})
            
    except Exception as e:
        logger.error("Unexpected error in background Letta task", extra={
            "error": str(e),
            "sender": event_sender
        }, exc_info=True)
        
        try:
            error_msg = f"Sorry, I encountered an unexpected error: {str(e)[:100]}"
            sent_as_agent = await send_as_agent(
                room_id, error_msg, config, logger,
                reply_to_event_id=None,
                reply_to_sender=None
            )
            if not sent_as_agent and client:
                error_content: Dict[str, Any] = {"msgtype": "m.text", "body": error_msg}
                await client.room_send(
                    room_id,
                    "m.room.message",
                    error_content
                )
        except Exception as send_error:
            logger.error("Failed to send error message", extra={"error": str(send_error)})

async def message_callback(room, event, config: Config, logger: logging.Logger, client: Optional[AsyncClient] = None):
    """Callback function for handling new text messages."""
    if isinstance(event, RoomMessageText):
        # Check for duplicate events via shared dedupe store
        event_id = getattr(event, 'event_id', None)
        if event_id and is_duplicate_event(event_id, logger):
            return
        
        # Ignore messages from ourselves to prevent loops
        if client and event.sender == client.user_id:
            return

        # Ignore historical messages imported from Letta (to prevent re-processing)
        if hasattr(event, 'source') and isinstance(event.source, dict):
            content = event.source.get("content", {})
            if content.get("m.letta_historical"):
                logger.debug(f"Ignoring historical message from {event.sender}")
                return
            
            # Ignore messages posted by the webhook bridge (prevent CLI→webhook→Matrix→Letta loop)
            if content.get("m.bridge_originated"):
                logger.debug(f"Ignoring bridge-originated message from {event.sender}")
                return
        
        # Only process messages in rooms that have a dedicated agent mapping
        # This prevents auto-forwarding content in relay/bridge rooms
        from src.core.mapping_service import get_mapping_by_room_id, get_mapping_by_matrix_user, get_all_mappings
        room_agent_user_id = None
        room_has_agent = False
        room_agent_id = None
        room_agent_name = None
        room_agent_mapping = None
        
        # Find the agent that owns this room
        room_agent_mapping = get_mapping_by_room_id(room.room_id)
        if room_agent_mapping:
            room_agent_user_id = room_agent_mapping.get("matrix_user_id")
            room_has_agent = True
            room_agent_id = room_agent_mapping.get("agent_id")
            room_agent_name = room_agent_mapping.get("agent_name", "Unknown")

        disabled_agent_ids = [a.strip() for a in os.getenv("DISABLED_AGENT_IDS", "").split(",") if a.strip()]
        if room_agent_id and room_agent_id in disabled_agent_ids:
            logger.debug(f"Skipping disabled agent {room_agent_id} ({room_agent_name})")
            return

        # Check if sender is an agent (for @mention routing)
        sender_mapping = get_mapping_by_matrix_user(event.sender)
        
        # Handle @mention-based routing for ALL agent messages (including own agent)
        # This allows agents to forward messages to other agents via @mentions
        if sender_mapping and sender_mapping.get("agent_id"):
            from src.matrix.mention_routing import handle_agent_mention_routing
            await handle_agent_mention_routing(
                room=room,
                event=event,
                sender_mxid=event.sender,
                sender_agent_id=sender_mapping["agent_id"],
                sender_agent_name=sender_mapping.get("agent_name", "Unknown"),
                config=config,
                logger=logger,
                admin_client=client,
            )

        # Only ignore messages from THIS room's own agent (prevent self-loops)
        # This comes AFTER @mention routing so agents can still forward via mentions
        if room_agent_user_id and event.sender == room_agent_user_id:
            logger.debug(f"Ignoring message from room's own agent {event.sender}")
            return

        # Log inter-agent communication
        if sender_mapping and event.sender != room_agent_user_id:
            logger.info(f"Received inter-agent message from {event.sender} in {room.display_name}")
        
        # Skip processing for rooms without a dedicated agent (relay/bridge rooms)
        # Letta can still write to these rooms via MCP tools, but won't auto-respond
        if not room_has_agent:
            logger.debug(f"No agent mapping for room {room.room_id}, skipping message processing (relay room)")
            return

        if await handle_letta_code_command(room, event, config, logger, room_agent_mapping, room_agent_id, room_agent_name):
            return

        fs_state = get_letta_code_room_state(room.room_id)
        fs_enabled = fs_state.get("enabled")
        is_huly_agent = room_agent_name and (room_agent_name.startswith("Huly - ") or room_agent_name == "Huly-PM-Control")
        # FS_MODE_AGENTS: comma-separated agent names that auto-enable fs-task mode (routes to Letta Code CLI)
        fs_mode_agents = [a.strip() for a in os.getenv("FS_MODE_AGENTS", "Meridian").split(",") if a.strip()]
        is_fs_mode_agent = room_agent_name and room_agent_name in fs_mode_agents
        use_fs_mode = fs_enabled is True or (fs_enabled is None and (is_huly_agent or is_fs_mode_agent))
        
        if use_fs_mode:
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
                return
            project_dir = fs_state.get("projectDir")
            if not project_dir:
                project_dir = await resolve_letta_project_dir(room.room_id, agent_id, config, logger)
            
            if not project_dir and is_huly_agent and agent_name:
                try:
                    projects_response = await call_letta_code_api(config, 'GET', '/api/projects')
                    projects = projects_response.get('projects', [])
                    search_name = agent_name[7:] if agent_name.startswith("Huly - ") else agent_name
                    for proj in projects:
                        if proj.get('name', '').lower() == search_name.lower():
                            project_dir = proj.get('filesystem_path')
                            if project_dir:
                                update_letta_code_room_state(room.room_id, {"projectDir": project_dir})
                                logger.info(f"[HULY-FS] Auto-linked {agent_name} to {project_dir}")
                            break
                except Exception as e:
                    logger.warning(f"[HULY-FS] Auto-link failed for {agent_name}: {e}")
            
            if not project_dir:
                await send_as_agent(room.room_id, "Filesystem mode enabled but no project linked. Run /fs-link.", config, logger)
                return
            
            # Add Matrix context or OpenCode metaprompt
            fs_prompt = event.body
            fs_event_timestamp = getattr(event, 'server_timestamp', None)
            if fs_event_timestamp is None and hasattr(event, 'source') and isinstance(event.source, dict):
                fs_event_timestamp = event.source.get('origin_server_ts')
            if fs_event_timestamp is None:
                fs_event_timestamp = int(time.time() * 1000)
            if event.sender.startswith("@oc_"):
                opencode_mxid = event.sender
                fs_prompt = matrix_formatter.format_opencode_envelope(
                    opencode_mxid=opencode_mxid,
                    text=event.body,
                    chat_id=room.room_id,
                    message_id=getattr(event, 'event_id', None),
                    timestamp=fs_event_timestamp,
                )
                logger.info(f"[OPENCODE-FS] Detected message from OpenCode identity: {opencode_mxid}")
            else:
                room_display = room.display_name or room.room_id
                fs_prompt = matrix_formatter.format_message_envelope(
                    channel="Matrix",
                    chat_id=room.room_id,
                    message_id=getattr(event, 'event_id', None),
                    sender=event.sender,
                    sender_name=event.sender,
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
                        wrap_response=False,
                    )
                    return
                except Exception as fs_err:
                    logger.warning(f"[FS-FALLBACK] letta-code task failed ({fs_err}), falling back to streaming Letta API")
                    # Fall through to the normal streaming path below
            else:
                logger.info(f"[FS-SKIP] letta_code_enabled=false, using streaming Letta API for fs-mode room")
                # Fall through to the normal streaming path below
 
        logger.info("Received message from user", extra={
            "sender": event.sender,
            "room_name": room.display_name,
            "room_id": room.room_id,
            "message_preview": event.body[:100] + "..." if len(event.body) > 100 else event.body
        })

        task_key = (room.room_id, room_agent_id or "unknown")
        existing_task = _active_letta_tasks.get(task_key)
        if existing_task and not existing_task.done():
            logger.warning(f"[BG-TASK] Agent still processing previous message for {task_key}, sending notice")
            try:
                await send_as_agent(
                    room.room_id, "⏳ Still processing previous message...", config, logger
                )
            except Exception:
                pass
            return

        event_source = None
        if hasattr(event, 'source') and isinstance(event.source, dict):
            event_source = event.source

        task = asyncio.create_task(
            _process_letta_message(
                event_body=event.body,
                event_sender=event.sender,
                event_source=event_source,
                original_event_id=getattr(event, 'event_id', None),
                room_id=room.room_id,
                room_display_name=room.display_name or room.room_id,
                room_agent_id=room_agent_id,
                config=config,
                logger=logger,
                client=client,
            )
        )
        task.add_done_callback(lambda t: _on_letta_task_done(task_key, t))
        _active_letta_tasks[task_key] = task
        logger.info(f"[BG-TASK] Dispatched background Letta task for {task_key}")

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
    async def notify_room(room_id: str, message: str):
        """Send notification to room"""
        sent_as_agent = await send_as_agent(room_id, message, config, logger)
        if not sent_as_agent and client is not None:
            await client.room_send(
                room_id,
                "m.room.message",
                {"msgtype": "m.text", "body": message}
            )

    # Initialize file handler with Matrix access token
    matrix_token = client.access_token
    logger.info(f"Matrix access token available: {bool(matrix_token)}, length: {len(matrix_token) if matrix_token else 0}")
    
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
        embedding_chunk_size=config.embedding_chunk_size
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
