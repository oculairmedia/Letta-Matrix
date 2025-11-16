import asyncio
import os
import logging
import json
import time
import uuid
import aiohttp
from typing import Optional, Dict, Any
from dataclasses import dataclass
from nio import AsyncClient, RoomMessageText, LoginError, RoomPreset
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

# Import our authentication manager
from src.matrix.auth import MatrixAuthManager

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
                log_level=os.getenv("LOG_LEVEL", "INFO")
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
    
    # Check if we have agent mappings to determine the right agent
    if room_id and os.path.exists("/app/data/agent_user_mappings.json"):
        try:
            with open("/app/data/agent_user_mappings.json", 'r') as f:
                mappings = json.load(f)
                for agent_id, mapping in mappings.items():
                    if mapping.get("room_id") == room_id:
                        agent_id_to_use = agent_id
                        agent_name_found = mapping.get('agent_name', 'UNKNOWN')
                        logger.info(f"Found agent mapping for room {room_id}: {mapping.get('agent_name')} ({agent_id})")
                        break
        except Exception as e:
            logger.warning(f"Could not load agent mappings: {e}")
    
    # CRITICAL DEBUG: Log the exact agent ID being used
    logger.warning(f"[DEBUG] AGENT ROUTING: Room {room_id} -> Agent {agent_id_to_use}")
    logger.warning(f"[DEBUG] Agent Name: {agent_name_found}")
    
    logger.info("Sending message to Letta API", extra={
        "message_preview": message_body[:100] + "..." if len(message_body) > 100 else message_body,
        "sender": username,
        "agent_id": agent_id_to_use,
        "room_id": room_id
    })

    async def _send_to_letta():
        """Inner function to handle the actual API call with retry logic - DIRECT HTTP"""
        # Use direct HTTP API instead of SDK to avoid pagination issues
        current_agent_id = agent_id_to_use  # Use the agent ID we determined from room mapping
        
        logger.warning(f"[DEBUG] SENDING TO LETTA API - Agent ID: {current_agent_id}")
        
        # Send message directly via HTTP
        url = f"{config.letta_api_url}/v1/agents/{current_agent_id}/messages"
        headers = {
            "Authorization": f"Bearer {config.letta_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messages": [{
                "role": "user",
                "content": message_body
            }]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise LettaApiError(f"Letta API error: {resp.status} - {error_text[:200]}", resp.status, error_text[:200])
                
                response = await resp.json()
        
        # Response is now a dict from JSON
        logger.debug(f"Received Letta API response: {type(response)}")
        
        return response

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
        logger.error("Letta API HTTP error", extra={"status_code": e.status, "message": str(e.message)[:200]})
        raise LettaApiError(f"Letta API returned error {e.status}", e.status, str(e.message)[:200])
    except Exception as e:
        logger.error("Unexpected error in Letta API call", extra={"error": str(e)}, exc_info=True)
        raise LettaApiError(f"An unexpected error occurred with the Letta SDK: {e}")

async def send_as_agent(room_id: str, message: str, config: Config, logger: logging.Logger) -> bool:
    """Send a message as the agent user for this room"""
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
        logger.info(f"[SEND_AS_AGENT] Attempting to send as agent: {agent_name} in room {room_id}")
        
        # Login as the agent user
        agent_username = agent_mapping["matrix_user_id"].split(':')[0].replace('@', '')
        agent_password = agent_mapping["matrix_password"]
        
        logger.debug(f"[SEND_AS_AGENT] Agent username: {agent_username}")
        
        login_url = f"{config.homeserver_url}/_matrix/client/r0/login"
        login_data = {
            "type": "m.login.password",
            "user": agent_username,
            "password": agent_password
        }
        
        async with aiohttp.ClientSession() as session:
            # Login
            logger.debug(f"[SEND_AS_AGENT] Attempting login to {login_url}")
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
                
                logger.info(f"[SEND_AS_AGENT] Successfully logged in as {agent_username}")
            
            # Send message as the agent
            # Generate a unique transaction ID
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
            
            logger.debug(f"[SEND_AS_AGENT] Sending to {message_url}")
            async with session.put(message_url, headers=headers, json=message_data) as response:
                if response.status == 200:
                    logger.info(f"[SEND_AS_AGENT] ✅ Successfully sent message as {agent_name} ({agent_username})")
                    return True
                else:
                    response_text = await response.text()
                    logger.error(f"[SEND_AS_AGENT] ❌ Failed to send message as agent: {response.status} - {response_text}")
                    return False
                    
    except Exception as e:
        logger.error(f"[SEND_AS_AGENT] Exception occurred: {e}", exc_info=True)
        return False

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
        
        # Check if the sender is THIS room's agent - ignore only self-messages, not other agents
        mappings_file = "/app/data/agent_user_mappings.json"
        if os.path.exists(mappings_file):
            with open(mappings_file, 'r') as f:
                mappings = json.load(f)
                # Find the agent that owns this room
                room_agent_user_id = None
                for agent_id, mapping in mappings.items():
                    if mapping.get("room_id") == room.room_id:
                        room_agent_user_id = mapping.get("matrix_user_id")
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

WHEN TO REPLY BACK TO THE OTHER AGENT:
- If the message clearly contains a question or request addressed to you,
  you SHOULD send a direct reply back to that agent using the
  'matrix_agent_message' tool.
- Your reply should normally be a single, clear answer (not many tiny
  follow-ups).

LOOP-SAFETY RULES (MUST OBEY):
- Never call 'matrix_agent_message' more than once in response to a single
  inter-agent message.
- For any ongoing inter-agent conversation, you may use 'matrix_agent_message'
  at most 3 times in total. After your third such reply, STOP using the tool
  and explain to the human that you are ending the inter-agent exchange to
  avoid loops.
- If the other agent appears to repeat the same question or answer, do NOT
  keep going back and forth. Instead, explain the situation to the human and
  do not call 'matrix_agent_message' again for that topic.

If the message does NOT require a reply (for example, it is just FYI), you can
simply acknowledge it or focus on any actions the HUMAN has asked for in this
room.
"""
                logger.info(f"[INTER-AGENT CONTEXT] Enhanced message for receiving agent:")
                logger.info(f"[INTER-AGENT CONTEXT] Sender: {from_agent_name} ({from_agent_id})")
                logger.info(f"[INTER-AGENT CONTEXT] Full enhanced message:\n{message_to_send}")

            # Send the message to Letta with room context
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

async def periodic_agent_sync(config, logger, interval=0.5):  # Reduced to 0.5 seconds for faster agent detection
    """Periodically sync Letta agents to Matrix users via OpenAI endpoint"""
    while True:
        await asyncio.sleep(interval)
        logger.debug("Running periodic agent sync via OpenAI endpoint...")  # Changed to debug to reduce log noise
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
        space_id = agent_manager.get_space_id()
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

    # Add the callback for text messages with config and logger
    async def callback_wrapper(room, event):
        await message_callback(room, event, config, logger, client)
    
    client.add_event_callback(callback_wrapper, RoomMessageText)

    logger.info("Starting sync loop to listen for messages")
    
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