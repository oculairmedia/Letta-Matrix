import json
from sqlalchemy import create_engine, text

# Load JSON
with open('/app/data/agent_user_mappings.json', 'r') as f:
    json_mappings = json.load(f)

# Find BMO in JSON
bmo_json = None
bmo_agent_id = None
for agent_id, mapping in json_mappings.items():
    if mapping.get('agent_name') == 'BMO':
        bmo_json = mapping
        bmo_agent_id = agent_id
        break

if bmo_json:
    json_room = bmo_json.get('room_id')
    json_user = bmo_json.get('matrix_user_id')
    print(f"BMO in JSON file:")
    print(f"  Agent ID: {bmo_agent_id}")
    print(f"  Room ID: {json_room}")
    print(f"  Matrix User: {json_user}")
else:
    print("BMO not found in JSON!")
    exit(1)

# Check database
engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')

print(f"\nBMO in Database:")
with engine.connect() as conn:
    result = conn.execute(text("SELECT agent_id, agent_name, room_id, matrix_user_id FROM agent_mappings WHERE agent_name = 'BMO'"))
    
    row = result.fetchone()
    if row:
        db_agent_id = row[0]
        db_room = row[2]
        db_user = row[3]
        
        print(f"  Agent ID: {db_agent_id}")
        print(f"  Room ID: {db_room}")
        print(f"  Matrix User: {db_user}")
        
        if db_room != json_room:
            print(f"\n❌ ROOM MISMATCH DETECTED!")
            print(f"   DB Room:   {db_room}")
            print(f"   JSON Room: {json_room}")
        else:
            print(f"\n✅ Room IDs match")
    else:
        print("  NOT FOUND IN DATABASE!")
