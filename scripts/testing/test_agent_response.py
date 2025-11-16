#!/usr/bin/env python3
"""
Test sending a message to an agent room
"""
import asyncio
import aiohttp
import json
import os

async def main():
    # Login as @letta
    homeserver_url = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
    username = "@letta:matrix.oculair.ca"
    password = "letta"
    
    login_url = f"{homeserver_url}/_matrix/client/r0/login"
    login_data = {
        "type": "m.login.password",
        "user": "letta",
        "password": password
    }
    
    async with aiohttp.ClientSession() as session:
        # Login
        async with session.post(login_url, json=login_data) as response:
            if response.status != 200:
                print(f"Failed to login as {username}")
                return
            
            auth_data = await response.json()
            token = auth_data.get("access_token")
            
        if not token:
            print("No token received")
            return
            
        print(f"Successfully logged in as {username}")
        
        # Send test message to Meridian's room
        room_id = "!ZrQOdTvhUZsAnrJJre:matrix.oculair.ca"  # Meridian's room
        
        send_url = f"{homeserver_url}/_matrix/client/r0/rooms/{room_id}/send/m.room.message"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        message_data = {
            "msgtype": "m.text",
            "body": "Hello Meridian! Can you hear me?"
        }
        
        async with session.post(send_url, headers=headers, json=message_data) as response:
            if response.status == 200:
                result = await response.json()
                print(f"✓ Sent test message to Meridian's room")
                print(f"  Event ID: {result.get('event_id')}")
            else:
                error_text = await response.text()
                print(f"✗ Failed to send message: {response.status} - {error_text}")

if __name__ == "__main__":
    asyncio.run(main())