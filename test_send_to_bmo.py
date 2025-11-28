#!/usr/bin/env python3
"""
Send a test message to BMO's room and check routing.
"""
import asyncio
import aiohttp
import os

async def main():
    homeserver = os.environ.get('MATRIX_HOMESERVER_URL', 'http://tuwunel:6167')
    # Use letta user token
    letta_user = os.environ.get('MATRIX_USERNAME', '@letta:matrix.oculair.ca')
    letta_password = os.environ.get('MATRIX_PASSWORD', 'letta')
    
    # Login as letta user
    login_url = f"{homeserver}/_matrix/client/r0/login"
    login_data = {
        "type": "m.login.password",
        "user": letta_user.replace('@', '').split(':')[0],
        "password": letta_password
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(login_url, json=login_data) as resp:
            if resp.status != 200:
                print(f"Login failed: {resp.status}")
                return
            
            auth_data = await resp.json()
            token = auth_data.get('access_token')
        
        # Send message to BMO's room
        bmo_room = '!tfSmwhqAWH3xZhN623:matrix.oculair.ca'
        message_url = f"{homeserver}/_matrix/client/r0/rooms/{bmo_room}/send/m.room.message"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        message_data = {
            'msgtype': 'm.text',
            'body': 'Test routing message for BMO - what is your agent name?'
        }
        
        async with session.post(message_url, headers=headers, json=message_data) as resp:
            if resp.status == 200:
                result = await resp.json()
                event_id = result.get('event_id')
                print(f"✅ Message sent to BMO's room!")
                print(f"   Event ID: {event_id}")
                print(f"   Room: {bmo_room}")
                print(f"\nNow check the logs:")
                print(f"docker logs matrix-synapse-deployment-matrix-client-1 --tail 50 | grep -E '(AGENT ROUTING|BMO|{bmo_room[:20]})'")
            else:
                error = await resp.text()
                print(f"Failed to send: {resp.status}")
                print(f"Error: {error}")

asyncio.run(main())
