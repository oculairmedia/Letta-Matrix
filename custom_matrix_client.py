import asyncio
import os
# import aiohttp # No longer needed, SDK handles HTTP
# import json # No longer needed, SDK handles JSON
from nio import AsyncClient, RoomMessageText, LoginError, RoomPreset
from nio.responses import JoinError
from nio.exceptions import RemoteProtocolError

# Imports for Letta SDK
from letta_client import AsyncLetta # MessageCreate and TextContent removed
from letta_client.core import ApiError # Corrected import for ApiError

# Import our authentication manager
from matrix_auth import MatrixAuthManager

# Configuration from environment variables
homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
username = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
password = os.getenv("MATRIX_PASSWORD", "letta")
room_to_join = os.getenv("MATRIX_ROOM_ID", "!LWmNEJcwPwVWlbmNqe:matrix.oculair.ca")

async def send_to_letta_api(message_body, sender_id):
    """
    Sends a message to the Letta API using the letta-client SDK and returns the response.
    """
    agent_id = os.getenv("LETTA_AGENT_ID", "agent-0e99d1a5-d9ca-43b0-9df9-c09761d01444")
    letta_token = os.getenv("LETTA_TOKEN", "lettaSecurePass123")
    # The base_url for the SDK should be the root of the API
    letta_base_url = os.getenv("LETTA_API_URL", "https://letta.oculair.ca")

    # Extract just the username from the Matrix user ID (remove @ and domain)
    if sender_id.startswith('@'):
        username = sender_id[1:].split(':')[0]  # Remove @ and take part before :
    else:
        username = sender_id
    
    print(f"--- Sending to Letta API via SDK: '{message_body}' from {username} ---")

    try:
        letta_sdk_client = AsyncLetta(token=letta_token, base_url=letta_base_url)
        
        # First, let's try to list available agents to see if our agent exists
        try:
            agents = await letta_sdk_client.agents.list()
            print(f"--- Available agents: {[agent.id for agent in agents]} ---")
            
            # Check if our agent exists
            agent_exists = any(agent.id == agent_id for agent in agents)
            print(f"--- Agent {agent_id} exists: {agent_exists} ---")
            
            if not agent_exists:
                print(f"--- Agent {agent_id} not found, using first available agent ---")
                if agents:
                    agent_id = agents[0].id
                    print(f"--- Using agent: {agent_id} ---")
                else:
                    return "No agents available in Letta"
        except Exception as e:
            print(f"--- Error listing agents: {e} ---")
        
        # Construct payload as a list of dictionaries, matching the JSON structure
        messages_payload = [
            {
                "role": "user",
                "content": message_body # API expects a string here for non-complex content
            }
        ]

        # The create_stream method might take stream_steps and stream_tokens as kwargs
        # if they are not default. For now, let's assume they are default or handled by the endpoint.
        # If errors occur, we might need to add stream_steps=True, stream_tokens=True as kwargs.
        # Try different API methods based on the SDK version
        try:
            # Method 1: Try the newer API structure
            response = await letta_sdk_client.agents.messages.create(
                agent_id=agent_id,
                messages=[{
                    "role": "user",
                    "content": message_body
                }]
            )
            print(f"--- Letta API response (create): {response} ---")
        except Exception as e1:
            print(f"--- create method failed: {e1} ---")
            try:
                # Method 2: Try the send_message API
                response = await letta_sdk_client.agents.send_message(
                    agent_id=agent_id,
                    message=message_body,
                    role="user"
                )
                print(f"--- Letta API response (send_message): {response} ---")
            except Exception as e2:
                print(f"--- send_message method failed: {e2} ---")
                try:
                    # Method 3: Try messages.send_message method
                    response = await letta_sdk_client.agents.messages.send_message(
                        agent_id=agent_id,
                        message=message_body,
                        role="user"
                    )
                    print(f"--- Letta API response (messages.send_message): {response} ---")
                except Exception as e3:
                    print(f"--- messages.send_message method failed: {e3} ---")
                    raise Exception(f"All API methods failed: {e1}, {e2}, {e3}")
        
        print(f"--- Send response type: {type(response)} ---")
        print(f"--- Send response: {response} ---")
        
        # Extract assistant messages from the response
        if response and response.messages:
            assistant_messages = []
            
            # Look for assistant messages in the response
            for message in response.messages:
                if hasattr(message, 'message_type') and message.message_type == 'assistant_message':
                    if hasattr(message, 'content') and message.content:
                        assistant_messages.append(message.content)
            
            # If we found assistant messages, return them
            if assistant_messages:
                return " ".join(assistant_messages)
            else:
                # Fallback: look for send_message tool calls in the detailed response
                print("--- No direct assistant messages found, checking tool calls ---")
                return "Letta responded but no clear message content found."
        else:
            return "Letta SDK connection successful, but no response content."

        # The send method should return the response directly, no streaming needed

    except ApiError as e: # Assuming ApiError is correctly imported
        print(f"--- Letta SDK API Error: Status={e.status_code}, Body={e.body} ---")
        return f"Letta API (SDK) returned error {e.status_code}: {str(e.body)[:200]}..."
    except Exception as e:
        print(f"--- Unexpected error using Letta SDK: {e} ---")
        import traceback
        traceback.print_exc()
        return f"An unexpected error occurred with the Letta SDK: {e}"

async def message_callback(room, event):
    """Callback function for handling new text messages."""
    if isinstance(event, RoomMessageText):
        # Ignore messages from ourselves to prevent loops
        if event.sender == client.user_id:
            return

        print(f"Received message from {event.sender} in {room.display_name} ({room.room_id}): {event.body}")

        try:
            # Ensure we have a valid token before making API calls
            if 'auth_manager_global' in globals():
                await auth_manager_global.ensure_valid_token(client)
            
            # --- HERE IS WHERE YOU WOULD SEND THE MESSAGE TO LETTA ---
            letta_response = await send_to_letta_api(event.body, event.sender)
            if letta_response:
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": letta_response}
                )
            else:
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": "Sorry, I couldn't get a response from Letta right now."}
                )
        except Exception as e:
            print(f"Error in message callback: {e}")
            # Try to send an error message if possible
            try:
                await client.room_send(
                    room.room_id,
                    "m.room.message",
                    {"msgtype": "m.text", "body": f"Error processing your message: {str(e)[:100]}"}
                )
            except:
                pass  # If we can't send error message, just log it

async def create_room_if_needed(client_instance, room_name="Letta Bot Room"):
    """Create a new room and return its ID"""
    print(f"Creating new room: {room_name}")
    try:
        # Create a public room that anyone can join
        response = await client_instance.room_create(
            name=room_name,
            topic="Room for Letta bot interactions",
            preset=RoomPreset.public_chat,  # Makes the room public
            is_direct=False
        )
        
        if hasattr(response, 'room_id'):
            print(f"Successfully created room: {response.room_id}")
            return response.room_id
        else:
            print(f"Failed to create room. Response: {response}")
            return None
    except Exception as e:
        print(f"Error creating room: {e}")
        import traceback
        traceback.print_exc()
        return None

async def join_room_if_needed(client_instance, room_id_or_alias):
    print(f"Attempting to join room: {room_id_or_alias}")
    try:
        response = await client_instance.join(room_id_or_alias)

        if isinstance(response, JoinError):
            error_message = getattr(response, 'message', str(response)) # Human-readable message
            status_code = getattr(response, 'status_code', None) # Matrix error code like M_UNRECOGNIZED

            print(f"Failed to join room {room_id_or_alias}. Error: {error_message} (Status Code: {status_code or 'N/A'})")

            # If room doesn't exist, create a new one
            if status_code == "M_UNKNOWN" or "Can't join remote room" in error_message:
                print("Room doesn't exist. Creating a new room...")
                return await create_room_if_needed(client_instance)
            elif status_code == "M_UNRECOGNIZED":
                print(f"Details: The server did not recognize the join request for {room_id_or_alias}. This could be due to an invalid room alias or ID, or server-side issues.")
            elif status_code == "M_FORBIDDEN":
                 print(f"Details: The bot may not be invited or allowed to join {room_id_or_alias}. Please check room permissions and invites.")
            elif "M_UNRECOGNIZED" in error_message: # Fallback if status_code is not available or different
                print(f"Details (fallback via message): The server did not recognize the join request for {room_id_or_alias}.")
            elif "M_FORBIDDEN" in error_message: # Fallback
                 print(f"Details (fallback via message): The bot may not be invited or allowed to join {room_id_or_alias}.")
            return None
        elif hasattr(response, 'room_id') and response.room_id: # Successful join
            print(f"Successfully joined room: {response.room_id}")
            return response.room_id
        else: # Other unexpected response type
            print(f"Failed to join room {room_id_or_alias}. Unexpected response type or content: {response}")
            return None
    except RemoteProtocolError as e: # Catches exceptions raised during the API call
        if "M_UNKNOWN_TOKEN" in str(e):
            print(f"Error joining room {room_id_or_alias}: Invalid token. The client might not be logged in correctly or the session is invalid. {e}")
        elif "M_FORBIDDEN" in str(e):
             print(f"Error joining room {room_id_or_alias}: Forbidden. The bot may not be invited or allowed to join. {e}")
        else:
            print(f"Error joining room {room_id_or_alias} (RemoteProtocolError): {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while joining room {room_id_or_alias}: {e}")
        return None

async def main():
    global client # Make client global so message_callback can access client.user_id
    
    # Initialize Matrix authentication manager
    auth_manager = MatrixAuthManager(homeserver_url, username, password, "CustomNioClientToken")
    
    # Get authenticated client
    client = await auth_manager.get_authenticated_client()
    if not client:
        print("Failed to authenticate with Matrix server")
        return

    print("Client configured with authentication manager.")
    print(f"User ID: {client.user_id}")
    print(f"Device ID: {client.device_id}")

    # Join the specified room
    joined_room_id = await join_room_if_needed(client, room_to_join)
    if not joined_room_id:
        print(f"Could not join room {room_to_join}. Exiting.")
        await client.close()
        return
    print(f"Ready to interact in {joined_room_id}")
    
    # If we created a new room, save its ID for future reference
    if joined_room_id != room_to_join:
        print(f"\n=== IMPORTANT: New room created! ===")
        print(f"Room ID: {joined_room_id}")
        print(f"Please update your .env file with this room ID:")
        print(f"MATRIX_ROOM_ID={joined_room_id}")
        print(f"===================================\n")

    # Add the callback for text messages
    client.add_event_callback(message_callback, RoomMessageText)

    print("Starting sync loop to listen for messages...")
    # Set sync_filter to only include room events for joined rooms to reduce data
    sync_filter = {"room": {"timeline": {"limit": 10}}} # Only get last 10 messages on initial sync
    try:
        # Store auth manager globally so we can refresh tokens during sync
        global auth_manager_global
        auth_manager_global = auth_manager
        
        await client.sync_forever(timeout=30000, full_state=False, sync_filter=sync_filter) # Sync every 30 seconds
    except Exception as e:
        print(f"Error during sync: {e}")
    finally:
        print("Closing client session...")
        await client.close()

if __name__ == "__main__":
    asyncio.run(main())