#!/usr/bin/env python3
"""
Test sending a message to Meridian room and check routing.
"""
import asyncio
import aiohttp
import os

async def main():
    # Get environment variables
    homeserver_url = os.environ.get('MATRIX_HOMESERVER_URL', 'http://tuwunel:6167')
    admin_token = os.environ.get('MATRIX_ADMIN_TOKEN')
    
    if not admin_token:
        print("❌ MATRIX_ADMIN_TOKEN not set")
        return
    
    meridian_room_id = '!O8cbkBGCMB8Ujlaret:matrix.oculair.ca'
    
    print(f"\nSending test message to Meridian room...")
    print(f"Room ID: {meridian_room_id}")
    
    # Send a message as admin user
    url = f"{homeserver_url}/_matrix/client/v3/rooms/{meridian_room_id}/send/m.room.message"
    headers = {
        'Authorization': f'Bearer {admin_token}',
        'Content-Type': 'application/json'
    }
    data = {
        'msgtype': 'm.text',
        'body': 'Test routing message - please respond with your agent name'
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                event_id = result.get('event_id')
                print(f"✅ Message sent successfully!")
                print(f"   Event ID: {event_id}")
                print(f"\nNow check the matrix-client logs for routing information:")
                print(f"docker logs matrix-synapse-deployment-matrix-client-1 --tail 50 | grep -i 'routing\\|meridian\\|agent.*routing'")
            else:
                error = await resp.text()
                print(f"❌ Failed to send message: {resp.status}")
                print(f"   Error: {error}")

if __name__ == '__main__':
    asyncio.run(main())
