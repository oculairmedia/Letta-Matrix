import json
from sqlalchemy import create_engine, text

# Find BMO's room
with open('/app/data/agent_user_mappings.json', 'r') as f:
    json_mappings = json.load(f)

bmo_data = None
for agent_id, mapping in json_mappings.items():
    if mapping.get('agent_name') == 'BMO':
        bmo_data = mapping
        bmo_agent_id = agent_id
        break

if not bmo_data:
    print("BMO not found!")
    exit(1)

bmo_room = bmo_data.get('room_id')
print(f"BMO's Room ID: {bmo_room}")
print(f"BMO's Agent ID: {bmo_agent_id}")

# Check what the database says about this room
engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')

print(f"\nLooking up room {bmo_room} in database...")
with engine.connect() as conn:
    result = conn.execute(text(f"SELECT agent_id, agent_name FROM agent_mappings WHERE room_id = '{bmo_room}'"))
    
    row = result.fetchone()
    if row:
        found_agent_id = row[0]
        found_agent_name = row[1]
        print(f"  Database says room belongs to: {found_agent_name}")
        print(f"  Agent ID: {found_agent_id}")
        
        if found_agent_id == bmo_agent_id:
            print(f"\n✅ CORRECT: Room maps to BMO")
        else:
            print(f"\n❌ WRONG: Room maps to {found_agent_name} instead of BMO!")
    else:
        print(f"  ❌ Room not found in database!")

# Check if there are duplicate room mappings
print(f"\nChecking for duplicate room mappings...")
with engine.connect() as conn:
    result = conn.execute(text(f"SELECT agent_id, agent_name FROM agent_mappings WHERE room_id = '{bmo_room}'"))
    
    rows = result.fetchall()
    if len(rows) > 1:
        print(f"  ❌ PROBLEM: {len(rows)} agents mapped to the same room!")
        for row in rows:
            print(f"     - {row[1]} ({row[0]})")
    else:
        print(f"  ✅ No duplicate mappings")
