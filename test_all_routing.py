#!/usr/bin/env python3
"""
Test routing for all agents by checking database mappings.
"""
from sqlalchemy import create_engine, text

engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')

print("\nAgent Routing Check")
print("=" * 80)
print(f"{'Agent Name':<30} {'Room ID':<40}")
print("=" * 80)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT agent_name, room_id, matrix_user_id 
        FROM agent_mappings 
        WHERE room_created = true 
        ORDER BY agent_name
        LIMIT 20
    """))
    
    for row in result:
        agent_name, room_id, matrix_user = row
        status = "✅" if room_id else "❌"
        print(f"{status} {agent_name:<28} {room_id if room_id else 'NO ROOM'}")

print("=" * 80)

# Check for agents with no rooms
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT COUNT(*) FROM agent_mappings WHERE room_id IS NULL OR room_id = ''
    """))
    no_room_count = result.fetchone()[0]
    
    if no_room_count > 0:
        print(f"\n⚠️  {no_room_count} agents have no room assigned!")
    else:
        print(f"\n✅ All agents have rooms assigned")

