import asyncio
import aiohttp
import os
import json
from sqlalchemy import create_engine, text

async def main():
    homeserver = os.environ.get('MATRIX_HOMESERVER_URL', 'http://tuwunel:6167')
    admin_pass = os.environ.get('MATRIX_ADMIN_PASSWORD')
    
    # Login
    login_url = f"{homeserver}/_matrix/client/v3/login"
    login_data = {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": "admin"},
        "password": admin_pass
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(login_url, json=login_data) as resp:
            auth = await resp.json()
            token = auth['access_token']
        
        # Get your rooms with names
        rooms_url = f"{homeserver}/_matrix/client/v3/joined_rooms"
        headers = {'Authorization': f'Bearer {token}'}
        
        async with session.get(rooms_url, headers=headers) as resp:
            data = await resp.json()
            room_ids = data.get('joined_rooms', [])
        
        # Get room names
        your_rooms = {}
        for room_id in room_ids:
            name_url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
            async with session.get(name_url, headers=headers) as resp:
                if resp.status == 200:
                    name_data = await resp.json()
                    room_name = name_data.get('name', '')
                    if room_name:
                        # Strip the " - Letta Agent Chat" suffix if present
                        agent_name = room_name.replace(' - Letta Agent Chat', '')
                        your_rooms[agent_name] = room_id
    
    # Load mappings
    with open('/app/data/agent_user_mappings.json', 'r') as f:
        mappings = json.load(f)
    
    # Find mismatches
    updates_needed = []
    for agent_id, mapping in mappings.items():
        agent_name = mapping.get('agent_name')
        db_room = mapping.get('room_id')
        
        # Check if there's a room with this agent's name that you're in
        if agent_name in your_rooms:
            actual_room = your_rooms[agent_name]
            if actual_room != db_room:
                updates_needed.append({
                    'agent_id': agent_id,
                    'agent_name': agent_name,
                    'old_room': db_room,
                    'new_room': actual_room
                })
    
    print(f"Found {len(updates_needed)} agents that need room mapping fixes")
    print("=" * 80)
    
    if not updates_needed:
        print("All mappings are correct!")
        return
    
    # Show what needs updating
    for u in updates_needed[:20]:
        print(f"\n{u['agent_name']}:")
        print(f"  Old (DB):  {u['old_room']}")
        print(f"  New (YOU): {u['new_room']}")
    
    if len(updates_needed) > 20:
        print(f"\n... and {len(updates_needed) - 20} more")
    
    # Update JSON file
    print("\n" + "=" * 80)
    print(f"Updating {len(updates_needed)} room mappings...")
    
    for u in updates_needed:
        mappings[u['agent_id']]['room_id'] = u['new_room']
    
    with open('/app/data/agent_user_mappings.json', 'w') as f:
        json.dump(mappings, f, indent=2)
    
    print("✅ JSON updated")
    
    # Update database
    print("Updating database...")
    engine = create_engine('postgresql://letta:letta@192.168.50.90:5432/matrix_letta')
    
    with engine.connect() as conn:
        for u in updates_needed:
            conn.execute(text(f"""
                UPDATE agent_mappings 
                SET room_id = '{u['new_room']}', 
                    updated_at = NOW()
                WHERE agent_id = '{u['agent_id']}'
            """))
        conn.commit()
    
    print("✅ Database updated")
    print(f"\n✅ Fixed {len(updates_needed)} room mappings!")
    print("\nAll agents should now route correctly to YOUR rooms.")

asyncio.run(main())
