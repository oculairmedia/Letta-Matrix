#!/usr/bin/env python3
import sys
sys.path.insert(0, '/app')
import asyncio
from src.core.space_manager import MatrixSpaceManager

async def main():
    old_room = "!i0zsq9TSdXJunUZq00:matrix.oculair.ca"
    
    space_mgr = MatrixSpaceManager()
    exists = await space_mgr.check_room_exists(old_room)
    
    print(f"Old room {old_room}:")
    print(f"  Exists: {exists}")
    
    if exists:
        print("\n⚠️  OLD ROOM STILL EXISTS!")
        print("This is why the sync process doesn't update the mapping.")
        print("\nSolution: We need to either:")
        print("1. Delete the old room from the Matrix server")
        print("2. Force the mapping to use the new room regardless")
        print("3. Improve room discovery to find the 'correct' room")

asyncio.run(main())
