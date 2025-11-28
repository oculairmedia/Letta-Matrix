#!/usr/bin/env python3
"""
Test script to verify Meridian room routing is working correctly.
"""
import asyncio
import sys
import os

# Add project to path
sys.path.insert(0, '/app')

from src.models.agent_mapping import AgentMappingDB

async def main():
    print("\n" + "="*60)
    print("TESTING MERIDIAN ROOM ROUTING")
    print("="*60 + "\n")
    
    # Test 1: Check database mapping
    print("Test 1: Database Lookup")
    print("-" * 40)
    
    db = AgentMappingDB()
    meridian_room_id = "!O8cbkBGCMB8Ujlaret:matrix.oculair.ca"
    meridian_agent_id = "agent-597b5756-2915-4560-ba6b-91005f085166"
    
    # Test by room ID
    mapping = db.get_by_room_id(meridian_room_id)
    if mapping:
        print(f"✅ Found mapping for room {meridian_room_id}")
        print(f"   Agent ID: {mapping.agent_id}")
        print(f"   Agent Name: {mapping.agent_name}")
        print(f"   Matrix User: {mapping.matrix_user_id}")
        
        if mapping.agent_id == meridian_agent_id:
            print("   ✅ CORRECT! Maps to Meridian agent")
        else:
            print(f"   ❌ WRONG! Expected {meridian_agent_id}")
    else:
        print(f"❌ No mapping found for room {meridian_room_id}")
    
    print()
    
    # Test by agent ID
    mapping = db.get_by_agent_id(meridian_agent_id)
    if mapping:
        print(f"✅ Found mapping for agent {meridian_agent_id}")
        print(f"   Agent Name: {mapping.agent_name}")
        print(f"   Room ID: {mapping.room_id}")
        
        if mapping.room_id == meridian_room_id:
            print("   ✅ CORRECT! Maps to current room")
        else:
            print(f"   ❌ WRONG! Expected {meridian_room_id}")
            print(f"      Got: {mapping.room_id}")
    else:
        print(f"❌ No mapping found for agent {meridian_agent_id}")
    
    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(main())
