#!/usr/bin/env python3
"""
Check for room drift across all agents by comparing database vs JSON file.
"""
import json
from sqlalchemy import create_engine, text

# Load JSON mappings
with open('/app/data/agent_user_mappings.json', 'r') as f:
    json_mappings = json.load(f)

# Load database mappings
engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')

drift_count = 0
match_count = 0
missing_count = 0

print("\nChecking for room drift across all agents...")
print("=" * 80)

with engine.connect() as conn:
    result = conn.execute(text("SELECT agent_id, agent_name, room_id FROM agent_mappings ORDER BY agent_name"))
    
    for row in result:
        agent_id, agent_name, db_room_id = row
        
        json_data = json_mappings.get(agent_id)
        if not json_data:
            print(f"⚠️  {agent_name}: Missing from JSON file")
            missing_count += 1
            continue
        
        json_room_id = json_data.get('room_id')
        
        if db_room_id != json_room_id:
            print(f"🔄 DRIFT: {agent_name}")
            print(f"   DB:   {db_room_id}")
            print(f"   JSON: {json_room_id}")
            drift_count += 1
        else:
            match_count += 1

print("=" * 80)
print(f"\nSummary:")
print(f"  ✅ Matching: {match_count}")
print(f"  🔄 Drift detected: {drift_count}")
print(f"  ⚠️  Missing from JSON: {missing_count}")
print(f"\nTotal agents: {match_count + drift_count + missing_count}")
