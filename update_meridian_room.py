#!/usr/bin/env python3
"""
Direct database update for Meridian room mapping.
Bypasses the sync process to ensure the update persists.
"""
from sqlalchemy import create_engine, text

# Direct SQL update
engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')

meridian_agent_id = 'agent-597b5756-2915-4560-ba6b-91005f085166'
new_room_id = '!O8cbkBGCMB8Ujlaret:matrix.oculair.ca'

with engine.connect() as conn:
    # Check current value
    result = conn.execute(text(f"""
        SELECT agent_name, room_id 
        FROM agent_mappings 
        WHERE agent_id = '{meridian_agent_id}'
    """))
    
    row = result.fetchone()
    if row:
        print(f"BEFORE UPDATE:")
        print(f"  Agent: {row[0]}")
        print(f"  Current Room ID: {row[1]}")
    
    # Update
    conn.execute(text(f"""
        UPDATE agent_mappings 
        SET room_id = '{new_room_id}', 
            updated_at = NOW()
        WHERE agent_id = '{meridian_agent_id}'
    """))
    conn.commit()
    
    # Verify
    result = conn.execute(text(f"""
        SELECT agent_name, room_id 
        FROM agent_mappings 
        WHERE agent_id = '{meridian_agent_id}'
    """))
    
    row = result.fetchone()
    if row:
        print(f"\nAFTER UPDATE:")
        print(f"  Agent: {row[0]}")
        print(f"  New Room ID: {row[1]}")
        
        if row[1] == new_room_id:
            print("\n✅ SUCCESS! Meridian room mapping updated!")
        else:
            print(f"\n❌ FAILED! Expected {new_room_id}, got {row[1]}")

print("\nNOTE: This update will be overwritten by the sync process.")
print("The JSON file has already been updated, so the next sync should preserve this.")
