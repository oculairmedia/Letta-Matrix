import asyncio
import os
import logging
import json
import time
import uuid
import aiohttp
from typing import Optional, Dict, Any
from dataclasses import dataclass
from nio import AsyncClient, RoomMessageText, LoginError, RoomPreset, RoomMessageMedia
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

# Import our authentication manager
from src.matrix.auth import MatrixAuthManager

# Import file handler
from src.matrix.file_handler import LettaFileHandler, FileUploadError

# Import agent user manager
from src.core.agent_user_manager import run_agent_sync
from src.matrix.event_dedupe import is_duplicate_event
from src.matrix.poll_handler import process_agent_response, is_poll_command

# Cross-run tracking webhook URL (TypeScript MCP - deprecated)
CONVERSATION_TRACKER_URL = os.getenv("CONVERSATION_TRACKER_URL", "http://192.168.50.90:3101")

# Python Matrix API for conversation registration
MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")

# Agent Mail MCP server URL for reverse bridge
AGENT_MAIL_URL = os.getenv("AGENT_MAIL_URL", "http://192.168.50.90:8766/mcp/")

async def register_conversation_for_tracking(
    matrix_event_id: str,
    matrix_room_id: str,
    agent_id: str,
    original_query: str,
    logger: logging.Logger
) -> bool:
    """
    Register a conversation with the webhook server for cross-run tracking.
    This enables the system to link responses from subsequent Letta runs
    back to the original Matrix message.
    """
    registered = False
    
    # Register with Python matrix-api (primary - prevents duplicate audit)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MATRIX_API_URL}/conversations/register",
                json={"agent_id": agent_id, "matrix_event_id": matrix_event_id, "matrix_room_id": matrix_room_id},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    logger.info(f"[CROSS-RUN] Registered conversation with matrix-api for agent {agent_id}")
                    registered = True
    except Exception as e:
        logger.debug(f"[CROSS-RUN] Could not register with matrix-api: {e}")
    
    # Also register with TypeScript tracker (for cross-run tool handling - legacy)
    if CONVERSATION_TRACKER_URL:
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "operation": "start_conversation",
                    "matrix_event_id": matrix_event_id,
                    "matrix_room_id": matrix_room_id,
                    "agent_id": agent_id,
                    "original_query": original_query[:500] if original_query else ""
                }
                async with session.post(
                    f"{CONVERSATION_TRACKER_URL}/conversations/start",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        logger.debug(f"[CROSS-RUN] Also registered with TypeScript tracker")
        except Exception as e:
            logger.debug(f"[CROSS-RUN] TypeScript tracker unavailable: {e}")
    
    return registered


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
                letta_api_url=os.getenv("LETTA_API_URL", "https://letta.oculair.ca"),
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
                letta_code_api_url=os.getenv("LETTA_CODE_API_URL", "http://192.168.50.90:3099"),
                letta_code_enabled=os.getenv("LETTA_CODE_ENABLED", "true").lower() == "true"
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
    message_body: str, 
    sender_id: str, 
    config: Config, 
    logger: logging.Logger, 
    room_id: str,
    reply_to_event_id: Optional[str] = None,
    reply_to_sender: Optional[str] = None,
    opencode_sender: Optional[str] = None
) -> str:
    """
    Sends a message to the Letta API using step streaming with progress display.
    Shows tool calls as progress messages that get deleted when replaced.
    
    Args:
        message_body: The message to send to the agent
        sender_id: Matrix sender ID
        config: Application configuration
        logger: Logger instance
        room_id: Matrix room ID
        reply_to_event_id: Optional event ID to reply to for the final response
        reply_to_sender: Optional sender to mention in the reply
        opencode_sender: Optional OpenCode sender MXID to ensure @mention in response
    """
    from src.matrix.streaming import StepStreamReader, StreamingMessageHandler, StreamEventType
    from src.letta.client import get_letta_client, LettaConfig
    
    # Determine which agent to use based on room_id
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
    
    # Create Letta SDK client
    sdk_config = LettaConfig(
        base_url=config.letta_api_url,
        api_key=config.letta_token,
        timeout=config.letta_streaming_timeout,
        max_retries=3
    )
    letta_client = get_letta_client(sdk_config)
    
    # Create stream reader
    stream_reader = StepStreamReader(
        letta_client=letta_client,
        include_reasoning=False,  # Don't show internal reasoning
        include_pings=True,
        timeout=config.letta_streaming_timeout
    )
    
    # Create message handlers for Matrix
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
        """Delete a message"""
        await delete_message_as_agent(rid, event_id, config, logger)
    
    handler = StreamingMessageHandler(
        send_message=send_message,
        delete_message=delete_message,
        room_id=room_id,
        delete_progress=False,  # Keep progress messages visible
        send_final_message=send_final_message  # Use separate handler for final with reply
    )
    
    # Track the final response
    final_response = ""
    
    # NOTE: Typing indicators disabled due to tuwunel/Conduit bug where
    # typing=false doesn't clear the indicator. Can be re-enabled when
    # using a server that properly supports typing notifications.
    # typing_manager = TypingIndicatorManager(room_id, config, logger)
    
    try:
        # Stream the message and handle events
        async for event in stream_reader.stream_message(agent_id_to_use, message_body):
            logger.debug(f"[STREAMING] Event: {event.type.value}")
            
            # Handle the event (sends progress messages to Matrix)
            await handler.handle_event(event)
            
            # Capture final assistant response
            if event.type == StreamEventType.ASSISTANT and event.content:
                final_response = event.content
            
            # Log errors
            if event.type == StreamEventType.ERROR:
                logger.error(f"[STREAMING] Error: {event.content}")
                if not final_response:
                    final_response = f"Error: {event.content}"
        
        # Cleanup any remaining progress messages
        await handler.cleanup()
        
    except Exception as e:
        logger.error(f"[STREAMING] Exception during streaming: {e}", exc_info=True)
        await handler.cleanup()
        raise LettaApiError(f"Streaming error: {e}")
    
    if not final_response:
        final_response = "Agent processed the request (no text response)."
    
    return final_response


async def send_to_letta_api(message_body: str, sender_id: str, config: Config, logger: logging.Logger, room_id: Optional[str] = None) -> str:
    """
    Sends a message to the Letta API using the letta-client SDK and returns the response.
    """
    # Extract just the username from the Matrix user ID (remove @ and domain)
    if sender_id.startswith('@'):
        username = sender_id[1:].split(':')[0]  # Remove @ and take part before :
    else:
        username = sender_id
    
    # Determine which agent to use based on room_id
    agent_id_to_use = config.letta_agent_id  # Default to configured agent
    agent_name_found = "DEFAULT"
    routing_method = "default"

    # Multi-strategy routing: Try multiple methods to find the correct agent
    if room_id:
        try:
            from src.models.agent_mapping import AgentMappingDB
            db = AgentMappingDB()
            
            # Strategy 1: Direct room_id lookup in database
            mapping = db.get_by_room_id(room_id)
            if mapping:
                agent_id_to_use = str(mapping.agent_id)
                agent_name_found = str(mapping.agent_name)
                routing_method = "database_room_id"
                logger.info(f"Found agent mapping in DB for room {room_id}: {agent_name_found} ({agent_id_to_use})")
            else:
                # Strategy 2: Extract agent ID from room members (self-healing fallback)
                logger.info(f"No direct mapping for room {room_id}, checking room members...")
                
                member_result = await get_agent_from_room_members(room_id, config, logger)
                if member_result:
                    agent_id_to_use, agent_name_found = member_result
                    routing_method = "room_members"
                    logger.info(f"Resolved agent via room members: {agent_name_found} ({agent_id_to_use})")
                else:
                    # Get all agent mappings for debugging
                    all_mappings = db.get_all()
                    logger.warning(f"No agent mapping found for room {room_id}, using default agent")
                    logger.info(f"Room has no mapping. Total mappings in DB: {len(all_mappings)}")
                
        except Exception as e:
            logger.warning(f"Could not query agent mappings database: {e}")
    
    # CRITICAL DEBUG: Log the exact agent ID being used
    logger.warning(f"[DEBUG] AGENT ROUTING: Room {room_id} -> Agent {agent_id_to_use}")
    logger.warning(f"[DEBUG] Agent Name: {agent_name_found}")
    logger.warning(f"[DEBUG] Routing Method: {routing_method}")
    
    logger.info("Sending message to Letta API", extra={
        "message_preview": message_body[:100] + "..." if len(message_body) > 100 else message_body,
        "sender": username,
        "agent_id": agent_id_to_use,
        "room_id": room_id
    })

    async def _send_to_letta():
        """Inner function to handle the actual API call with retry logic - Using SDK"""
        from src.letta.client import get_letta_client, LettaConfig
        from concurrent.futures import ThreadPoolExecutor
        import asyncio
        
        current_agent_id = agent_id_to_use  # Use the agent ID we determined from room mapping
        
        logger.warning(f"[DEBUG] SENDING TO LETTA API (SDK) - Agent ID: {current_agent_id}")
        
        # Configure SDK client with extended timeout for long-running agents
        sdk_config = LettaConfig(
            base_url=config.letta_api_url,
            api_key=config.letta_token,
            timeout=300.0,  # 5 minutes (increased from 180s for agents doing work)
            max_retries=3
        )
        client = get_letta_client(sdk_config)
        
        # Run sync SDK call in thread (SDK is synchronous)
        # Use asyncio.to_thread which properly manages the thread pool
        def _sync_send():
            return client.agents.messages.create(
                agent_id=current_agent_id,
                messages=[{"role": "user", "content": message_body}]
            )
        response = await asyncio.to_thread(_sync_send)
        
        # Convert SDK response to dict for compatibility with existing code
        if hasattr(response, 'model_dump'):
            result = response.model_dump()
        elif hasattr(response, 'dict'):
            result = response.dict()
        else:
            # Fallback: manually extract messages
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

    try:
        # Use retry logic for the API call
        response = await retry_with_backoff(_send_to_letta, max_retries=3, logger=logger)
        
        # Extract assistant messages from the response (now a dict)
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


async def set_typing_as_agent(room_id: str, typing: bool, config: Config, logger: logging.Logger, timeout_ms: int = 30000) -> bool:
    """
    Set typing indicator as the agent user for this room.
    
    Args:
        room_id: Matrix room ID
        typing: True to start typing, False to stop
        config: Configuration object
        logger: Logger instance
        timeout_ms: How long the typing indicator should last (default 30s)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Load agent mapping from database
        from src.core.mapping_service import get_mapping_by_room_id
        agent_mapping = get_mapping_by_room_id(room_id)
        
        if not agent_mapping:
            return False
        
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
                    return False
                
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                
                if not agent_token:
                    return False
            
            # Set typing indicator
            # URL-encode the user ID since it contains @ and :
            from urllib.parse import quote
            encoded_user_id = quote(agent_mapping['matrix_user_id'], safe='')
            typing_url = f"{config.homeserver_url}/_matrix/client/v3/rooms/{room_id}/typing/{encoded_user_id}"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json"
            }
            
            # Per Matrix spec: timeout should only be included when typing=true
            if typing:
                typing_data = {
                    "typing": True,
                    "timeout": timeout_ms
                }
            else:
                typing_data = {
                    "typing": False
                }
            
            logger.debug(f"[TYPING] PUT {typing_url} with {typing_data}")
            async with session.put(typing_url, headers=headers, json=typing_data) as response:
                response_text = await response.text()
                if response.status == 200:
                    logger.debug(f"[TYPING] Set typing={typing} for room {room_id}, response: {response_text}")
                    
                    # Workaround for servers that don't properly handle typing=false:
                    # Also send typing=true with timeout=1 to force immediate expiry
                    if not typing:
                        expire_data = {"typing": True, "timeout": 1}
                        async with session.put(typing_url, headers=headers, json=expire_data) as expire_response:
                            logger.debug(f"[TYPING] Sent expire workaround, status: {expire_response.status}")
                    
                    return True
                else:
                    logger.warning(f"[TYPING] Failed to set typing: {response.status} - {response_text}")
                    return False
                    
    except Exception as e:
        logger.debug(f"[TYPING] Exception: {e}")
        return False


class TypingIndicatorManager:
    """
    Manages typing indicators with automatic refresh.
    
    Typing indicators timeout after ~30 seconds, so this manager
    periodically refreshes them while the agent is processing.
    """
    
    def __init__(self, room_id: str, config: Config, logger: logging.Logger):
        self.room_id = room_id
        self.config = config
        self.logger = logger
        self._typing_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
    
    async def _typing_loop(self):
        """Keep typing indicator active until stopped"""
        try:
            while not self._stop_event.is_set():
                await set_typing_as_agent(self.room_id, True, self.config, self.logger, timeout_ms=30000)
                # Refresh every 25 seconds (before the 30s timeout)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=25.0)
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    continue  # Refresh typing
        except asyncio.CancelledError:
            pass
        finally:
            # Always stop typing when done
            await set_typing_as_agent(self.room_id, False, self.config, self.logger)
    
    async def start(self):
        """Start showing typing indicator"""
        self._stop_event.clear()
        self._typing_task = asyncio.create_task(self._typing_loop())
        self.logger.debug(f"[TYPING] Started typing indicator for room {self.room_id}")
    
    async def stop(self):
        """Stop showing typing indicator"""
        self._stop_event.set()
        if self._typing_task:
            self._typing_task.cancel()
            try:
                await self._typing_task
            except asyncio.CancelledError:
                pass
            self._typing_task = None
        # Explicitly stop typing
        await set_typing_as_agent(self.room_id, False, self.config, self.logger)
        self.logger.debug(f"[TYPING] Stopped typing indicator for room {self.room_id}")
    
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
                    return None
                
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                
                if not agent_token:
                    logger.error(f"No token received for agent {agent_username}")
                    return None
            
            # Send message as the agent
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
        await file_handler.handle_file_event(event, room.room_id, agent_id)
    
    except FileUploadError as e:
        logger.error(f"File upload error: {e}")
        # File handler will send notifications
    
    except Exception as e:
        logger.error(f"Unexpected error in file callback: {e}", exc_info=True)

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

        # Only ignore messages from THIS room's own agent (prevent self-loops)
        if room_agent_user_id and event.sender == room_agent_user_id:
            logger.debug(f"Ignoring message from room's own agent {event.sender}")
            return

        # Allow messages from OTHER agents (inter-agent communication)
        if room_agent_user_id:
            sender_mapping = get_mapping_by_matrix_user(event.sender)
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
        if fs_state.get("enabled"):
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
                if not project_dir:
                    await send_as_agent(room.room_id, "Filesystem mode enabled but no project linked. Run /fs-link.", config, logger)
                    return
            
            # Check if sender is an OpenCode identity (@oc_*) and inject metaprompt
            fs_prompt = event.body
            if event.sender.startswith("@oc_"):
                opencode_mxid = event.sender
                fs_prompt = f"""[MESSAGE FROM OPENCODE USER]

{event.body}

---
RESPONSE INSTRUCTION (OPENCODE BRIDGE):
This message is from an OpenCode user: {opencode_mxid}
When you respond to this message, you MUST include their @mention ({opencode_mxid}) 
in your response so the OpenCode bridge can route your reply to them.

Example: "{opencode_mxid} Here is my response..."
"""
                logger.info(f"[OPENCODE-FS] Detected message from OpenCode identity: {opencode_mxid}")
                logger.info(f"[OPENCODE-FS] Injected @mention instruction for filesystem mode")
            
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
 
        logger.info("Received message from user", extra={

            "sender": event.sender,
            "room_name": room.display_name,
            "room_id": room.room_id,
            "message_preview": event.body[:100] + "..." if len(event.body) > 100 else event.body
        })

        try:
            # Ensure we have a valid token before making API calls
            if client and 'auth_manager_global' in globals() and auth_manager_global is not None:
                await auth_manager_global.ensure_valid_token(client)
            
            # Check if this is an inter-agent message
            message_to_send = event.body
            is_inter_agent_message = False
            from_agent_id = None
            from_agent_name = None
            
            # Check if this is a message from Agent Mail bridge (for reverse bridge)
            is_agent_mail_message = False
            agent_mail_metadata = None
            
            # Method 1: Check for metadata (from MCP tool or Agent Mail bridge)
            if hasattr(event, 'source') and isinstance(event.source, dict):
                content = event.source.get("content", {})
                from_agent_id = content.get("m.letta.from_agent_id")
                from_agent_name = content.get("m.letta.from_agent_name")
                
                if from_agent_id and from_agent_name:
                    is_inter_agent_message = True
                    logger.info(f"Detected inter-agent message (via metadata) from {from_agent_name} ({from_agent_id})")
                
                # Check for Agent Mail bridge metadata
                agent_mail_metadata = content.get("m.agent_mail")
                if agent_mail_metadata:
                    is_agent_mail_message = True
                    logger.info(f"[REVERSE-BRIDGE] Detected Agent Mail message from {agent_mail_metadata.get('sender_friendly_name', 'Unknown')}")
            
            # Method 2: Check if sender is an agent user (even without metadata)
            if not is_inter_agent_message:
                sender_agent_mapping = get_mapping_by_matrix_user(event.sender)
                if sender_agent_mapping:
                    # This is an agent user sending to another agent's room
                    # (We already filtered out self-messages above)
                    from_agent_id = sender_agent_mapping.get("agent_id")
                    from_agent_name = sender_agent_mapping.get("agent_name", "Unknown Agent")
                    is_inter_agent_message = True
                    logger.info(f"Detected inter-agent message (via sender check) from {from_agent_name} ({from_agent_id})")
            
            # If this is an inter-agent message, enhance it with context
            if is_inter_agent_message and from_agent_id and from_agent_name:
                # Format message with context for the receiving agent.
                # IMPORTANT: The receiving agent MAY reply back using the
                # 'matrix_agent_message' tool, but must avoid open-ended loops.

                # The original Matrix event body already includes a prefix like
                # "[Inter-Agent Message from X]". Strip that inner prefix so we
                # don't show duplicate headers to the receiving agent.
                raw_body = event.body or ""
                payload_lines = raw_body.splitlines()
                if payload_lines and payload_lines[0].startswith("[Inter-Agent Message from"):
                    # Drop the first line (the original prefix) and keep the rest
                    payload = "\n".join(payload_lines[1:]).lstrip("\n")
                else:
                    payload = raw_body

                message_to_send = f"""[INTER-AGENT MESSAGE from {from_agent_name}]

{payload}

---
SYSTEM NOTE (INTER-AGENT COMMUNICATION)
The message above is from another Letta agent: {from_agent_name} (ID: {from_agent_id}).
Treat this as your MAIN task for this turn; the other agent is trying to
collaborate with you.
"""
                logger.info(f"[INTER-AGENT CONTEXT] Enhanced message for receiving agent:")
                logger.info(f"[INTER-AGENT CONTEXT] Sender: {from_agent_name} ({from_agent_id})")
                logger.info(f"[INTER-AGENT CONTEXT] Full enhanced message:\n{message_to_send}")

            is_opencode_sender = event.sender.startswith("@oc_")
            opencode_mxid: Optional[str] = None
            if is_opencode_sender and not is_inter_agent_message:
                opencode_mxid = event.sender
                message_to_send = f"""[MESSAGE FROM OPENCODE USER]

{event.body}

---
RESPONSE INSTRUCTION (OPENCODE BRIDGE):
This message is from an OpenCode user: {opencode_mxid}
When you respond to this message, you MUST include their @mention ({opencode_mxid}) 
in your response so the OpenCode bridge can route your reply to them.

Example: "@oc_matrix_synapse_deployment:matrix.oculair.ca Here is my response..."
"""
                logger.info(f"[OPENCODE] Detected message from OpenCode identity: {opencode_mxid}")
                logger.info(f"[OPENCODE] Injected @mention instruction for response routing")
            elif not is_inter_agent_message:
                room_display = room.display_name or room.room_id
                message_to_send = f"[Matrix: {event.sender} in {room_display}]\n\n{event.body}"
                logger.debug(f"[MATRIX-CONTEXT] Added context for sender {event.sender}")

            # Register this conversation for cross-run tracking
            # This allows the webhook server to link responses from subsequent runs
            # back to this original Matrix message
            original_event_id = getattr(event, 'event_id', None)
            if original_event_id and room_agent_id:
                await register_conversation_for_tracking(
                    matrix_event_id=original_event_id,
                    matrix_room_id=room.room_id,
                    agent_id=room_agent_id,
                    original_query=message_to_send,
                    logger=logger
                )

            # Send the message to Letta with room context
            # Use streaming mode if enabled (shows progress messages for tool calls)
            if config.letta_streaming_enabled:
                logger.info("[STREAMING] Using streaming mode for Letta API call")
                # Streaming mode handles sending messages directly (with progress updates)
                letta_response = await send_to_letta_api_streaming(
                    message_to_send, event.sender, config, logger, room.room_id,
                    reply_to_event_id=original_event_id,
                    reply_to_sender=event.sender,
                    opencode_sender=opencode_mxid  # Pass OpenCode sender for @mention fallback
                )
                # In streaming mode, the final message is already sent by the handler
                # We don't need to send it again, just log the result
                logger.info("Successfully processed streaming response", extra={
                    "response_length": len(letta_response),
                    "room_id": room.room_id,
                    "streaming": True,
                    "reply_to": original_event_id
                })
                
                # REVERSE BRIDGE (streaming mode): Forward response back to Agent Mail if original was from bridge
                if is_agent_mail_message and agent_mail_metadata and room_agent_id:
                    try:
                        # Get the responding agent's code name
                        responder_code_name = get_agent_code_name(room_agent_id, logger)
                        sender_code_name = agent_mail_metadata.get('sender_code_name')
                        
                        if responder_code_name and sender_code_name:
                            original_subject = agent_mail_metadata.get('subject', 'No subject')
                            original_msg_id = agent_mail_metadata.get('message_id')
                            thread_id = agent_mail_metadata.get('thread_id')
                            
                            # Forward the response back to Agent Mail
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
                # Non-streaming mode (original behavior)
                letta_response = await send_to_letta_api(message_to_send, event.sender, config, logger, room.room_id)
                
                # If this is a response to an OpenCode user, ensure @mention is included
                if opencode_mxid and opencode_mxid not in letta_response:
                    logger.info(f"[OPENCODE] Agent response missing @mention, prepending {opencode_mxid}")
                    letta_response = f"{opencode_mxid} {letta_response}"
                
                original_event_id = getattr(event, 'event_id', None)
                sent_as_agent = False
                
                poll_handled, remaining_text, poll_event_id = await process_agent_response(
                    room_id=room.room_id,
                    response_text=letta_response,
                    config=config,
                    logger_instance=logger,
                    reply_to_event_id=original_event_id,
                    reply_to_sender=event.sender
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
                        room.room_id, 
                        letta_response, 
                        config, 
                        logger,
                        reply_to_event_id=original_event_id,
                        reply_to_sender=event.sender
                    )
                
                if not sent_as_agent:
                    # Fallback to sending as the main letta client if agent send fails
                    # Include rich reply in fallback too
                    if client:
                        logger.warning("Failed to send as agent, falling back to main client")
                        message_content: Dict[str, Any] = {"msgtype": "m.text", "body": letta_response}
                        if original_event_id:
                            message_content["m.relates_to"] = {
                                "m.in_reply_to": {"event_id": original_event_id}
                            }
                            message_content["m.mentions"] = {"user_ids": [event.sender]}
                        await client.room_send(
                            room.room_id,
                            "m.room.message",
                            message_content
                        )
                    else:
                        logger.error("No client available and agent send failed")
                
                logger.info("Successfully sent response to Matrix", extra={
                    "response_length": len(letta_response),
                    "room_id": room.room_id,
                    "sent_as_agent": sent_as_agent,
                    "reply_to": original_event_id
                })
            
            # REVERSE BRIDGE: Forward response back to Agent Mail if original was from bridge
            if is_agent_mail_message and agent_mail_metadata and room_agent_id:
                try:
                    # Get the responding agent's code name
                    responder_code_name = get_agent_code_name(room_agent_id, logger)
                    sender_code_name = agent_mail_metadata.get('sender_code_name')
                    
                    if responder_code_name and sender_code_name:
                        original_subject = agent_mail_metadata.get('subject', 'No subject')
                        original_msg_id = agent_mail_metadata.get('message_id')
                        thread_id = agent_mail_metadata.get('thread_id')
                        
                        # Forward the response back to Agent Mail
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
            logger.error("Letta API error in message callback", extra={
                "error": str(e),
                "status_code": e.status_code,
                "sender": event.sender
            })
            error_message = f"Sorry, I encountered an error while processing your message: {str(e)[:100]}"
            original_event_id = getattr(event, 'event_id', None)
            try:
                # Try to send error as agent first - use rich reply
                sent_as_agent = await send_as_agent(
                    room.room_id, error_message, config, logger,
                    reply_to_event_id=original_event_id,
                    reply_to_sender=event.sender
                )
                if not sent_as_agent and client:
                    error_content: Dict[str, Any] = {"msgtype": "m.text", "body": error_message}
                    if original_event_id:
                        error_content["m.relates_to"] = {"m.in_reply_to": {"event_id": original_event_id}}
                    await client.room_send(
                        room.room_id,
                        "m.room.message",
                        error_content
                    )
            except Exception as send_error:
                logger.error("Failed to send error message", extra={"error": str(send_error)})
                
        except Exception as e:
            logger.error("Unexpected error in message callback", extra={
                "error": str(e),
                "sender": event.sender
            }, exc_info=True)
            
            # Try to send an error message if possible
            original_event_id = getattr(event, 'event_id', None)
            try:
                error_msg = f"Sorry, I encountered an unexpected error: {str(e)[:100]}"
                sent_as_agent = await send_as_agent(
                    room.room_id, error_msg, config, logger,
                    reply_to_event_id=original_event_id,
                    reply_to_sender=event.sender
                )
                if not sent_as_agent and client:
                    error_content: Dict[str, Any] = {"msgtype": "m.text", "body": error_msg}
                    if original_event_id:
                        error_content["m.relates_to"] = {"m.in_reply_to": {"event_id": original_event_id}}
                    await client.room_send(
                        room.room_id,
                        "m.room.message",
                        error_content
                    )
            except Exception as send_error:
                logger.error("Failed to send error message", extra={"error": str(send_error)})

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

    logger.info("Starting sync loop to listen for messages and file uploads")
    
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
        logger.info("Closing client session")
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())