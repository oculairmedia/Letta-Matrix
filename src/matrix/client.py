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
                letta_streaming_timeout=float(os.getenv("LETTA_STREAMING_TIMEOUT", "120.0"))
            )
        except Exception as e:
            raise ConfigurationError(f"Failed to load configuration: {e}")

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
            for key, value in record.__dict__.items():
                if key not in ["name", "msg", "args", "levelname", "levelno", "pathname", "filename", 
                              "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName", 
                              "created", "msecs", "relativeCreated", "thread", "threadName", 
                              "processName", "process", "getMessage"]:
                    log_entry[key] = value
            
            return json.dumps(log_entry)
    
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    
    return logger

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
    room_id: str
) -> str:
    """
    Sends a message to the Letta API using step streaming with progress display.
    Shows tool calls as progress messages that get deleted when replaced.
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
        """Send a message and return event_id"""
        event_id = await send_as_agent_with_event_id(rid, content, config, logger)
        return event_id or ""
    
    async def delete_message(rid: str, event_id: str) -> None:
        """Delete a message"""
        await delete_message_as_agent(rid, event_id, config, logger)
    
    handler = StreamingMessageHandler(
        send_message=send_message,
        delete_message=delete_message,
        room_id=room_id,
        delete_progress=False  # Keep progress messages visible
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
        
        # Run sync SDK call in thread pool (SDK is synchronous)
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = await loop.run_in_executor(
                executor,
                lambda: client.agents.messages.create(
                    agent_id=current_agent_id,
                    messages=[{"role": "user", "content": message_body}]
                )
            )
        
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
        # Load agent mappings to find which agent owns this room
        mappings_file = "/app/data/agent_user_mappings.json"
        if not os.path.exists(mappings_file):
            logger.warning("No agent mappings file found")
            return False
            
        with open(mappings_file, 'r') as f:
            mappings = json.load(f)
        
        # Find the agent for this room
        agent_mapping = None
        for agent_id, mapping in mappings.items():
            if mapping.get("room_id") == room_id:
                agent_mapping = mapping
                break
        
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
        # Load agent mappings to find which agent owns this room
        mappings_file = "/app/data/agent_user_mappings.json"
        if not os.path.exists(mappings_file):
            return False
            
        with open(mappings_file, 'r') as f:
            mappings = json.load(f)
        
        # Find the agent for this room
        agent_mapping = None
        for agent_id, mapping in mappings.items():
            if mapping.get("room_id") == room_id:
                agent_mapping = mapping
                break
        
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


async def send_as_agent_with_event_id(room_id: str, message: str, config: Config, logger: logging.Logger) -> Optional[str]:
    """
    Send a message as the agent user for this room and return the event ID.
    Returns the event_id on success, None on failure.
    """
    try:
        # Load agent mappings to find which agent owns this room
        mappings_file = "/app/data/agent_user_mappings.json"
        if not os.path.exists(mappings_file):
            logger.warning("No agent mappings file found")
            return None
            
        with open(mappings_file, 'r') as f:
            mappings = json.load(f)
        
        # Find the agent for this room
        agent_mapping = None
        for agent_id, mapping in mappings.items():
            if mapping.get("room_id") == room_id:
                agent_mapping = mapping
                break
        
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
            
            message_data = {
                "msgtype": "m.text",
                "body": message
            }
            
            async with session.put(message_url, headers=headers, json=message_data) as response:
                if response.status == 200:
                    result = await response.json()
                    event_id = result.get("event_id")
                    logger.debug(f"[SEND_AS_AGENT] Sent message, event_id: {event_id}")
                    return event_id
                else:
                    response_text = await response.text()
                    logger.error(f"[SEND_AS_AGENT] Failed to send message: {response.status} - {response_text}")
                    return None
                    
    except Exception as e:
        logger.error(f"[SEND_AS_AGENT] Exception occurred: {e}", exc_info=True)
        return None


async def send_as_agent(room_id: str, message: str, config: Config, logger: logging.Logger) -> bool:
    """Send a message as the agent user for this room"""
    # Use the event_id version and convert to bool
    event_id = await send_as_agent_with_event_id(room_id, message, config, logger)
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
                logger.debug("Ignoring historical message imported from Letta", extra={
                    "sender": event.sender,
                    "message": event.body[:50]
                })
                return
        
        # Only process messages in rooms that have a dedicated agent mapping
        # This prevents auto-forwarding content in relay/bridge rooms
        mappings_file = "/app/data/agent_user_mappings.json"
        room_agent_user_id = None
        room_has_agent = False
        
        if os.path.exists(mappings_file):
            with open(mappings_file, 'r') as f:
                mappings = json.load(f)
                # Find the agent that owns this room
                for agent_id, mapping in mappings.items():
                    if mapping.get("room_id") == room.room_id:
                        room_agent_user_id = mapping.get("matrix_user_id")
                        room_has_agent = True
                        break

                # Only ignore messages from THIS room's own agent (prevent self-loops)
                if room_agent_user_id and event.sender == room_agent_user_id:
                    logger.debug(f"Ignoring message from room's own agent {event.sender}")
                    return

                # Allow messages from OTHER agents (inter-agent communication)
                for agent_id, mapping in mappings.items():
                    if mapping.get("matrix_user_id") == event.sender and event.sender != room_agent_user_id:
                        logger.info(f"Received inter-agent message from {event.sender} in {room.display_name}")
                        break
        
        # Skip processing for rooms without a dedicated agent (relay/bridge rooms)
        # Letta can still write to these rooms via MCP tools, but won't auto-respond
        if not room_has_agent:
            logger.debug(f"No agent mapping for room {room.room_id}, skipping message processing (relay room)")
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
            
            # Method 1: Check for metadata (from MCP tool)
            if hasattr(event, 'source') and isinstance(event.source, dict):
                content = event.source.get("content", {})
                from_agent_id = content.get("m.letta.from_agent_id")
                from_agent_name = content.get("m.letta.from_agent_name")
                
                if from_agent_id and from_agent_name:
                    is_inter_agent_message = True
                    logger.info(f"Detected inter-agent message (via metadata) from {from_agent_name} ({from_agent_id})")
            
            # Method 2: Check if sender is an agent user (even without metadata)
            if not is_inter_agent_message and os.path.exists(mappings_file):
                with open(mappings_file, 'r') as f:
                    mappings = json.load(f)
                    
                    # Check if sender is an agent user
                    for agent_id, mapping in mappings.items():
                        if mapping.get("matrix_user_id") == event.sender:
                            # This is an agent user sending to another agent's room
                            # (We already filtered out self-messages above)
                            from_agent_id = agent_id
                            from_agent_name = mapping.get("agent_name", "Unknown Agent")
                            is_inter_agent_message = True
                            logger.info(f"Detected inter-agent message (via sender check) from {from_agent_name} ({from_agent_id})")
                            break
            
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

            # Check if sender is an OpenCode identity (@oc_*)
            # If so, inject @mention instruction so agent knows how to respond
            is_opencode_sender = event.sender.startswith("@oc_")
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

            # Send the message to Letta with room context
            # Use streaming mode if enabled (shows progress messages for tool calls)
            if config.letta_streaming_enabled:
                logger.info("[STREAMING] Using streaming mode for Letta API call")
                # Streaming mode handles sending messages directly (with progress updates)
                letta_response = await send_to_letta_api_streaming(
                    message_to_send, event.sender, config, logger, room.room_id
                )
                # In streaming mode, the final message is already sent by the handler
                # We don't need to send it again, just log the result
                logger.info("Successfully processed streaming response", extra={
                    "response_length": len(letta_response),
                    "room_id": room.room_id,
                    "streaming": True
                })
            else:
                # Non-streaming mode (original behavior)
                letta_response = await send_to_letta_api(message_to_send, event.sender, config, logger, room.room_id)
                
                # Try to send as the agent user first
                sent_as_agent = await send_as_agent(room.room_id, letta_response, config, logger)
                
                if not sent_as_agent:
                    # Fallback to sending as the main letta client if agent send fails
                    if client:
                        logger.warning("Failed to send as agent, falling back to main client")
                        await client.room_send(
                            room.room_id,
                            "m.room.message",
                            {"msgtype": "m.text", "body": letta_response}
                        )
                    else:
                        logger.error("No client available and agent send failed")
                
                logger.info("Successfully sent response to Matrix", extra={
                    "response_length": len(letta_response),
                    "room_id": room.room_id,
                    "sent_as_agent": sent_as_agent
                })
            
        except LettaApiError as e:
            logger.error("Letta API error in message callback", extra={
                "error": str(e),
                "status_code": e.status_code,
                "sender": event.sender
            })
            error_message = f"Sorry, I encountered an error while processing your message: {str(e)[:100]}"
            try:
                # Try to send error as agent first
                sent_as_agent = await send_as_agent(room.room_id, error_message, config, logger)
                if not sent_as_agent and client:
                    await client.room_send(
                        room.room_id,
                        "m.room.message",
                        {"msgtype": "m.text", "body": error_message}
                    )
            except Exception as send_error:
                logger.error("Failed to send error message", extra={"error": str(send_error)})
                
        except Exception as e:
            logger.error("Unexpected error in message callback", extra={
                "error": str(e),
                "sender": event.sender
            }, exc_info=True)
            
            # Try to send an error message if possible
            try:
                error_msg = f"Sorry, I encountered an unexpected error: {str(e)[:100]}"
                sent_as_agent = await send_as_agent(room.room_id, error_msg, config, logger)
                if not sent_as_agent and client:
                    await client.room_send(
                        room.room_id,
                        "m.room.message",
                        {"msgtype": "m.text", "body": error_msg}
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
    if os.path.exists("/app/data/agent_user_mappings.json"):
        try:
            with open("/app/data/agent_user_mappings.json", 'r') as f:
                mappings = json.load(f)
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
    async def callback_wrapper(room, event):
        await message_callback(room, event, config, logger, client)
    
    client.add_event_callback(callback_wrapper, RoomMessageText)
    
    # Add the callback for file messages
    async def file_callback_wrapper(room, event):
        await file_callback(room, event, config, logger, file_handler)
    
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