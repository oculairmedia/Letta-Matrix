#!/usr/bin/env python3
"""
Comprehensive diagnostic of agent routing system.
"""
import json
from sqlalchemy import create_engine, text

print("\n" + "=" * 80)
print("COMPLETE AGENT ROUTING DIAGNOSTIC")
print("=" * 80)

# Load JSON
with open('/app/data/agent_user_mappings.json', 'r') as f:
    json_mappings = json.load(f)

# Load database
engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')

print("\n1. Database vs JSON Consistency Check")
print("-" * 80)

issues = []
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT agent_id, agent_name, room_id, matrix_user_id 
        FROM agent_mappings 
        ORDER BY agent_name
    """))
    
    for row in result:
        agent_id, agent_name, db_room, matrix_user = row
        json_data = json_mappings.get(agent_id, {})
        json_room = json_data.get('room_id')
        
        if db_room != json_room:
            issues.append({
                'agent': agent_name,
                'type': 'ROOM_MISMATCH',
                'db_room': db_room,
                'json_room': json_room
            })
        
        if not db_room:
            issues.append({
                'agent': agent_name,
                'type': 'NO_ROOM_DB',
                'matrix_user': matrix_user
            })
        
        if not json_room:
            issues.append({
                'agent': agent_name,
                'type': 'NO_ROOM_JSON',
                'matrix_user': matrix_user
            })

if issues:
    print(f"⚠️  Found {len(issues)} issues:")
    for issue in issues[:10]:  # Show first 10
        print(f"   {issue['type']}: {issue['agent']}")
        if 'db_room' in issue:
            print(f"      DB:   {issue['db_room']}")
            print(f"      JSON: {issue['json_room']}")
else:
    print("✅ All agents have consistent room mappings!")

print("\n2. Room Existence Check (sample)")
print("-" * 80)

# Check a few agents to see if their rooms exist
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT agent_name, room_id 
        FROM agent_mappings 
        WHERE room_id IS NOT NULL 
        ORDER BY agent_name 
        LIMIT 5
    """))
    
    for row in result:
        agent_name, room_id = row
        print(f"   {agent_name}: {room_id}")

print("\n3. Matrix User ID Format Check")
print("-" * 80)

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT agent_name, matrix_user_id 
        FROM agent_mappings 
        WHERE matrix_user_id NOT LIKE '@agent_%:matrix.oculair.ca'
        LIMIT 5
    """))
    
    invalid = list(result)
    if invalid:
        print(f"⚠️  Found {len(invalid)} agents with invalid user ID format:")
        for row in invalid:
            print(f"   {row[0]}: {row[1]}")
    else:
        print("✅ All agents have valid Matrix user ID format")

print("\n4. Statistics")
print("-" * 80)

with engine.connect() as conn:
    stats = {}
    
    result = conn.execute(text("SELECT COUNT(*) FROM agent_mappings"))
    stats['total'] = result.fetchone()[0]
    
    result = conn.execute(text("SELECT COUNT(*) FROM agent_mappings WHERE room_id IS NOT NULL"))
    stats['with_rooms'] = result.fetchone()[0]
    
    result = conn.execute(text("SELECT COUNT(*) FROM agent_mappings WHERE room_created = true"))
    stats['room_created'] = result.fetchone()[0]
    
    print(f"   Total agents: {stats['total']}")
    print(f"   With rooms: {stats['with_rooms']}")
    print(f"   Room created flag: {stats['room_created']}")

print("\n" + "=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80 + "\n")
