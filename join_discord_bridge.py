#!/usr/bin/env python3
import asyncio
from custom_matrix_client import MatrixClient
import os

async def main():
    # Initialize the Matrix client
    client = MatrixClient()
    
    # The Discord bridge room
    room_alias = "#_discord_1386202835104043111_1386202835787452529:oculair.ca"
    
    print(f"Attempting to join {room_alias}...")
    
    try:
        # Join the room
        response = await client.client.join(room_alias)
        print(f"Successfully joined room! Room ID: {response.room_id}")
        
        # Send a test message
        await client.client.room_send(
            room_id=response.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": "Hello! The Matrix-Discord bridge is now connected."
            }
        )
        print("Sent welcome message to the room.")
        
    except Exception as e:
        print(f"Error joining room: {e}")
    
    await client.client.close()

if __name__ == "__main__":
    asyncio.run(main())