#!/usr/bin/env python3
"""
Script to send a message to the Matrix admin user
Uses the Letta account to send messages
"""
import asyncio
import os
import sys
import argparse
from nio import AsyncClient, RoomMessageText, LoginError
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv('.env')

# Configuration
HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
LETTA_USERNAME = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
LETTA_PASSWORD = os.getenv("MATRIX_PASSWORD", "letta")
ADMIN_USER_ID = "@admin:matrix.oculair.ca"

async def send_message_to_admin(message: str, create_room: bool = True):
    """
    Send a message to the admin user
    
    Args:
        message: The message to send
        create_room: Whether to create a direct room if it doesn't exist
    """
    client = AsyncClient(HOMESERVER_URL, LETTA_USERNAME)
    
    try:
        # Login
        logger.info(f"Logging in as {LETTA_USERNAME}...")
        response = await client.login(LETTA_PASSWORD)
        
        if isinstance(response, LoginError):
            logger.error(f"Login failed: {response.message}")
            return False
        
        logger.info("Login successful!")
        
        # Find or create a direct room with the admin
        room_id = None
        
        # First, check if we already have a direct room with the admin
        logger.info("Checking for existing direct rooms with admin...")
        joined_rooms = await client.joined_rooms()
        
        for room in joined_rooms.rooms:
            # Get room members
            members = await client.joined_members(room)
            member_ids = [member.user_id for member in members.members]
            
            # Check if this is a direct room with just us and the admin
            if len(member_ids) == 2 and ADMIN_USER_ID in member_ids and LETTA_USERNAME in member_ids:
                room_id = room
                logger.info(f"Found existing direct room: {room_id}")
                break
        
        # If no direct room exists and create_room is True, create one
        if not room_id and create_room:
            logger.info("No direct room found, creating one...")
            response = await client.room_create(
                is_direct=True,
                invite=[ADMIN_USER_ID],
                name="Letta Admin Messages"
            )
            
            if hasattr(response, 'room_id'):
                room_id = response.room_id
                logger.info(f"Created new direct room: {room_id}")
            else:
                logger.error(f"Failed to create room: {response}")
                return False
        
        if not room_id:
            logger.error("No room available to send message")
            return False
        
        # Send the message
        logger.info(f"Sending message to room {room_id}...")
        response = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": message
            }
        )
        
        if hasattr(response, 'event_id'):
            logger.info(f"Message sent successfully! Event ID: {response.event_id}")
            return True
        else:
            logger.error(f"Failed to send message: {response}")
            return False
        
    except Exception as e:
        logger.error(f"Error: {e}")
        return False
    finally:
        await client.close()

async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Send a message to the Matrix admin user")
    parser.add_argument("message", help="The message to send to the admin")
    parser.add_argument("--no-create-room", action="store_true", 
                        help="Don't create a new room if no direct room exists")
    
    args = parser.parse_args()
    
    success = await send_message_to_admin(
        message=args.message,
        create_room=not args.no_create_room
    )
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())