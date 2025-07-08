import asyncio
import os
import logging
import json
import time
import aiohttp
from typing import Optional, Dict, Any
from dataclasses import dataclass
from nio import AsyncClient, RoomMessageText, LoginError, RoomPreset
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

# Imports for Letta SDK
from letta_client import AsyncLetta # MessageCreate and TextContent removed
from letta_client.core import ApiError # Corrected import for ApiError

# Import our authentication manager
from matrix_auth import MatrixAuthManager

# Import agent user manager
from agent_user_manager import run_agent_sync

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
                room_id=os.getenv("MATRIX_ROOM_ID", "!LWmNEJcwPwVWlbmNqe:matrix.oculair.ca"),
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
startup_time = None  # Track when the bot started to ignore old messages

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

async def send_to_letta_api(message_body: str, sender_id: str, config: Config, logger: logging.Logger, room_id: str = None) -> str:
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
    
    # Check if we have agent mappings to determine the right agent
    if room_id and os.path.exists("/app/data/agent_user_mappings.json"):
        try:
            with open("/app/data/agent_user_mappings.json", 'r') as f:
                mappings = json.load(f)
                for agent_id, mapping in mappings.items():
                    if mapping.get("room_id") == room_id:
                        agent_id_to_use = agent_id
                        logger.info(f"Found agent mapping for room {room_id}: {mapping.get('agent_name')} ({agent_id})")
                        break
        except Exception as e:
            logger.warning(f"Could not load agent mappings: {e}")
    
    logger.info("Sending message to Letta API", extra={
        "message_preview": message_body[:100] + "..." if len(message_body) > 100 else message_body,
        "sender": username,
        "agent_id": agent_id_to_use,
        "room_id": room_id
    })

    async def _send_to_letta():
        """Inner function to handle the actual API call with retry logic"""
        # Configure client with 3-minute timeout
        letta_sdk_client = AsyncLetta(
            token=config.letta_token, 
            base_url=config.letta_api_url,
            timeout=180.0  # 3 minutes timeout
        )
        
        # First, let's try to list available agents to see if our agent exists
        try:
            agents = await letta_sdk_client.agents.list()
            agent_ids = [agent.id for agent in agents]
            logger.debug("Listed available agents", extra={"agent_ids": agent_ids})
            
            # Check if our agent exists
            agent_exists = any(agent.id == agent_id_to_use for agent in agents)
            current_agent_id = agent_id_to_use
            
            if not agent_exists:
                logger.warning("Configured agent not found, using first available", 
                             extra={"configured_agent": agent_id_to_use, "available_agents": agent_ids})
                if agents:
                    current_agent_id = agents[0].id
                    logger.info("Using fallback agent", extra={"agent_id": current_agent_id})
                else:
                    raise LettaApiError("No agents available in Letta")
            
        except Exception as e:
            logger.error("Error listing agents", extra={"error": str(e)})
            current_agent_id = agent_id_to_use  # Use determined agent anyway
        
        # Send message to Letta agent
        response = await letta_sdk_client.agents.messages.create(
            agent_id=current_agent_id,
            messages=[{
                "role": "user",
                "content": message_body
            }]
        )
        
        logger.debug("Received Letta API response", extra={
            "response_type": type(response).__name__,
            "has_messages": bool(response and response.messages)
        })
        
        return response

    try:
        # Use retry logic for the API call
        response = await retry_with_backoff(_send_to_letta, max_retries=3, logger=logger)
        
        # Extract assistant messages from the response
        if response and response.messages:
            assistant_messages = []
            
            # Debug: Log the response structure
            logger.debug(f"Response has {len(response.messages)} messages")
            for i, message in enumerate(response.messages):
                logger.debug(f"Message {i}: type={getattr(message, 'message_type', 'unknown')}, "
                           f"role={getattr(message, 'role', 'unknown')}, "
                           f"content={getattr(message, 'content', 'none')[:100]}")
            
            # Look for assistant messages in the response - check multiple possible formats
            for message in response.messages:
                message_content = None
                
                # Try different ways to identify assistant messages
                if hasattr(message, 'message_type') and message.message_type == 'assistant_message':
                    message_content = getattr(message, 'content', None)
                elif hasattr(message, 'role') and message.role == 'assistant':
                    message_content = getattr(message, 'content', None)
                elif hasattr(message, 'content') and message.content:
                    # If it has content but no clear role, assume it's an assistant message
                    message_content = message.content
                
                if message_content:
                    assistant_messages.append(str(message_content))
            
            # If we found assistant messages, return them
            if assistant_messages:
                result = " ".join(assistant_messages)
                logger.info("Successfully processed Letta response", extra={
                    "response_length": len(result),
                    "message_count": len(assistant_messages)
                })
                return result
            else:
                logger.warning("No assistant messages found in response")
                logger.warning(f"Response structure: {[{k: getattr(msg, k, None) for k in ['message_type', 'role', 'content']} for msg in response.messages[:3]]}")
                return "Letta responded but no clear message content found."
        else:
            logger.warning("Empty response from Letta API")
            return "Letta SDK connection successful, but no response content."

    except ApiError as e:
        logger.error("Letta API error", extra={"status_code": e.status_code, "body": str(e.body)[:200]})
        raise LettaApiError(f"Letta API returned error {e.status_code}", e.status_code, str(e.body)[:200])
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
                    logger.error(f"Failed to login as agent {agent_username}")
                    return False
                
                auth_data = await response.json()
                agent_token = auth_data.get("access_token")
                
                if not agent_token:
                    logger.error(f"No token received for agent {agent_username}")
                    return False
            
            # Send message as the agent
            message_url = f"{config.homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message"
            headers = {
                "Authorization": f"Bearer {agent_token}",
                "Content-Type": "application/json"
            }
            
            message_data = {
                "msgtype": "m.text",
                "body": message
            }
            
            async with session.post(message_url, headers=headers, json=message_data) as response:
                if response.status == 200:
                    logger.info(f"Successfully sent message as agent {agent_username}")
                    return True
                else:
                    logger.error(f"Failed to send message as agent: {response.status}")
                    return False
                    
    except Exception as e:
        logger.error(f"Error sending message as agent: {e}")
        return False

async def message_callback(room, event, config: Config, logger: logging.Logger):
    """Callback function for handling new text messages."""
    if isinstance(event, RoomMessageText):
        # Ignore messages from ourselves to prevent loops
        if event.sender == client.user_id:
            return
        
        # Ignore messages from before bot startup to prevent replaying old messages
        if hasattr(event, 'server_timestamp') and startup_time and event.server_timestamp < startup_time:
            logger.debug("Ignoring old message from before startup", extra={
                "event_timestamp": event.server_timestamp,
                "startup_time": startup_time,
                "sender": event.sender,
                "message": event.body[:50]
            })
            return
        
        # Check if the sender is an agent user - ignore messages from agent users
        mappings_file = "/app/data/agent_user_mappings.json"
        if os.path.exists(mappings_file):
            with open(mappings_file, 'r') as f:
                mappings = json.load(f)
                # Check if sender is an agent user
                for agent_id, mapping in mappings.items():
                    if mapping.get("matrix_user_id") == event.sender:
                        logger.debug(f"Ignoring message from agent user {event.sender}")
                        return

        logger.info("Received message from user", extra={
            "sender": event.sender,
            "room_name": room.display_name,
            "room_id": room.room_id,
            "message_preview": event.body[:100] + "..." if len(event.body) > 100 else event.body
        })

        try:
            # Ensure we have a valid token before making API calls
            if 'auth_manager_global' in globals():
                await auth_manager_global.ensure_valid_token(client)
            
            # Send the message to Letta with room context
            letta_response = await send_to_letta_api(event.body, event.sender, config, logger, room.room_id)
            
            # Try to send as the agent user first
            sent_as_agent = await send_as_agent(room.room_id, letta_response, config, logger)
            
            if not sent_as_agent:
                # Fallback to sending as the main letta client if agent send fails
                logger.warning("Failed to send as agent, falling back to main client")
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": letta_response}
                )
            
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
                if not sent_as_agent:
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
                if not sent_as_agent:
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
    global client, startup_time # Make client and startup_time global
    
    # Set startup time to ignore old messages
    startup_time = time.time() * 1000  # Convert to milliseconds for nio event timestamps
    
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
    
    # Temporarily disabled agent sync to focus on message processing
    logger.info("Skipping agent sync to prioritize message processing...")
    # try:
    #     agent_manager = await run_agent_sync(config)
    #     logger.info("Agent-to-user sync completed successfully")
    # except Exception as e:
    #     logger.error("Agent sync failed", extra={"error": str(e)})
    #     # Continue with main client setup even if agent sync fails
    
    # Temporarily disable periodic agent sync to allow message processing
    # sync_task = asyncio.create_task(periodic_agent_sync(config, logger))
    
    # Get authenticated client
    client = await auth_manager.get_authenticated_client()
    if not client:
        logger.error("Failed to authenticate with Matrix server")
        return

    logger.info("Client authenticated successfully", extra={
        "user_id": client.user_id,
        "device_id": client.device_id
    })

    # Join the specified room
    joined_room_id = await join_room_if_needed(client, config.room_id, logger)
    if not joined_room_id:
        logger.error("Could not join room, exiting", extra={"room_id": config.room_id})
        await client.close()
        return
    
    logger.info("Ready to interact in room", extra={"room_id": joined_room_id})
    
    # If we created a new room, save its ID for future reference
    if joined_room_id != config.room_id:
        logger.warning("New room created, please update configuration", extra={
            "new_room_id": joined_room_id,
            "original_room_id": config.room_id
        })
    
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
        await message_callback(room, event, config, logger)
    
    client.add_event_callback(callback_wrapper, RoomMessageText)

    logger.info("Starting sync loop to listen for messages")
    # Set sync_filter with lazy loading and optimizations for performance
    sync_filter = {
        "room": {
            "timeline": {"limit": 0},  # Don't fetch historical messages on initial sync
            "state": {
                "lazy_load_members": True  # Only load member info when needed
            }
        },
        "presence": {"enabled": False},  # Disable presence updates to reduce data
        "account_data": {"enabled": False}  # Disable account data sync
    }
    try:
        # Store auth manager globally so we can refresh tokens during sync
        global auth_manager_global
        auth_manager_global = auth_manager
        
        await client.sync_forever(timeout=5000, full_state=False, sync_filter=sync_filter) # Reduced to 5 seconds for faster response
    except Exception as e:
        logger.error("Error during sync", extra={"error": str(e)}, exc_info=True)
    finally:
        logger.info("Closing client session")
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())