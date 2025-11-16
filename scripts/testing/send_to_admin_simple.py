#!/usr/bin/env python3
"""
Simple script to send a message to the Matrix admin user using the Matrix API
"""
import asyncio
import aiohttp
import json
import os
import sys
import argparse
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env')

# Configuration
MATRIX_API_URL = os.getenv("MATRIX_API_URL", "http://matrix-api:8000")
HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
LETTA_USERNAME = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
LETTA_PASSWORD = os.getenv("MATRIX_PASSWORD", "letta")
ADMIN_USER_ID = "@admin:matrix.oculair.ca"

async def send_message_to_admin(message: str):
    """Send a message to the admin user via Matrix API"""
    
    async with aiohttp.ClientSession() as session:
        # First, login to get access token
        print(f"Logging in as {LETTA_USERNAME}...")
        
        login_url = f"{MATRIX_API_URL}/login"
        login_payload = {
            "homeserver": HOMESERVER_URL,
            "user_id": LETTA_USERNAME,
            "password": LETTA_PASSWORD,
            "device_name": "admin_messenger"
        }
        
        async with session.post(login_url, json=login_payload) as response:
            if response.status != 200:
                print(f"Login failed: {await response.text()}")
                return False
                
            result = await response.json()
            if not result.get("success"):
                print(f"Login failed: {result.get('message', 'Unknown error')}")
                return False
                
            access_token = result.get("access_token")
            print("Login successful!")
        
        # Get list of rooms to find or create a direct room with admin
        print("Looking for direct room with admin...")
        
        list_rooms_url = f"{MATRIX_API_URL}/rooms/list"
        rooms_payload = {
            "access_token": access_token,
            "homeserver": HOMESERVER_URL
        }
        
        async with session.post(list_rooms_url, json=rooms_payload) as response:
            if response.status != 200:
                print(f"Failed to list rooms: {await response.text()}")
                return False
                
            result = await response.json()
            rooms = result.get("rooms", [])
        
        # Find a direct room with the admin
        direct_room_id = None
        for room in rooms:
            # Check if it's a direct room with admin
            members = room.get("members", [])
            if (len(members) == 2 and 
                ADMIN_USER_ID in members and 
                LETTA_USERNAME in members):
                direct_room_id = room.get("room_id")
                print(f"Found existing direct room: {direct_room_id}")
                break
        
        # If no direct room exists, create one
        if not direct_room_id:
            print("No direct room found, creating one...")
            
            create_room_url = f"{MATRIX_API_URL}/rooms/create"
            create_payload = {
                "access_token": access_token,
                "homeserver": HOMESERVER_URL,
                "room_config": {
                    "is_direct": True,
                    "invite": [ADMIN_USER_ID],
                    "name": "Letta to Admin Messages",
                    "preset": "trusted_private_chat"
                }
            }
            
            async with session.post(create_room_url, json=create_payload) as response:
                if response.status != 200:
                    print(f"Failed to create room: {await response.text()}")
                    # Try to find any room where we can reach the admin
                    for room in rooms:
                        if ADMIN_USER_ID in room.get("members", []):
                            direct_room_id = room.get("room_id")
                            print(f"Using existing room: {direct_room_id}")
                            break
                    
                    if not direct_room_id:
                        return False
                else:
                    result = await response.json()
                    direct_room_id = result.get("room_id")
                    print(f"Created new direct room: {direct_room_id}")
        
        # Send the message
        print(f"Sending message to room {direct_room_id}...")
        
        send_url = f"{MATRIX_API_URL}/messages/send"
        send_payload = {
            "room_id": direct_room_id,
            "message": message,
            "access_token": access_token,
            "homeserver": HOMESERVER_URL
        }
        
        async with session.post(send_url, json=send_payload) as response:
            if response.status != 200:
                print(f"Failed to send message: {await response.text()}")
                return False
                
            result = await response.json()
            if result.get("success"):
                print(f"Message sent successfully! Event ID: {result.get('event_id')}")
                return True
            else:
                print(f"Failed to send message: {result.get('message', 'Unknown error')}")
                return False

async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Send a message to the Matrix admin user")
    parser.add_argument("message", help="The message to send to the admin")
    
    args = parser.parse_args()
    
    success = await send_message_to_admin(args.message)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())