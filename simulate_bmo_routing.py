"""
Simulate the routing logic for a message to BMO's room.
"""
import sys
sys.path.insert(0, '/app')

from src.models.agent_mapping import AgentMappingDB

# BMO's room from our earlier check
bmo_room_id = "!tfSmwhqAWH3xZhN623:matrix.oculair.ca"

print(f"Simulating message to BMO's room: {bmo_room_id}")
print("=" * 80)

# Strategy 1: Database lookup (what the routing code does)
print("\n1. Database Lookup (Primary Strategy)")
print("-" * 80)

db = AgentMappingDB()
mapping = db.get_by_room_id(bmo_room_id)

if mapping:
    print(f"✅ Found mapping:")
    print(f"   Agent ID: {mapping.agent_id}")
    print(f"   Agent Name: {mapping.agent_name}")
    print(f"   Room ID: {mapping.room_id}")
    print(f"\n➡️  Message would route to: {mapping.agent_name}")
    print(f"   Letta Agent ID: {mapping.agent_id}")
else:
    print(f"❌ No mapping found - routing would FAIL!")

# Get all mappings to check for issues
print(f"\n2. Checking for duplicate or conflicting mappings")
print("-" * 80)

all_mappings = db.get_all()
count = 0
for m in all_mappings:
    if m.room_id == bmo_room_id:
        count += 1
        print(f"   Found: {m.agent_name} ({m.agent_id})")

if count == 0:
    print(f"   ❌ No agents mapped to this room!")
elif count == 1:
    print(f"   ✅ Exactly one agent mapped (correct)")
else:
    print(f"   ❌ {count} agents mapped to same room (CONFLICT!)")

print("\n" + "=" * 80)
