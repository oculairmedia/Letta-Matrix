import asyncio
import aiohttp
import os
import json

async def main():
    homeserver = os.environ.get('MATRIX_HOMESERVER_URL', 'http://tuwunel:6167')
    admin_pass = os.environ.get('MATRIX_ADMIN_PASSWORD')
    
    # Login as admin
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
        
        # Get your rooms
        rooms_url = f"{homeserver}/_matrix/client/v3/joined_rooms"
        headers = {'Authorization': f'Bearer {token}'}
        
        async with session.get(rooms_url, headers=headers) as resp:
            data = await resp.json()
            your_rooms = set(data.get('joined_rooms', []))
        
        # Load mappings
        with open('/app/data/agent_user_mappings.json', 'r') as f:
            mappings = json.load(f)
        
        print(f"You are in {len(your_rooms)} rooms")
        print(f"Database has {len(mappings)} agents")
        print("\nAgents whose rooms you're NOT in:")
        print("=" * 80)
        
        missing = []
        for agent_id, mapping in mappings.items():
            room_id = mapping.get('room_id')
            agent_name = mapping.get('agent_name')
            
            if room_id and room_id not in your_rooms:
                missing.append({'name': agent_name, 'room': room_id, 'agent_id': agent_id})
        
        print(f"\nFound {len(missing)} agents where you're not in the room:")
        for m in sorted(missing, key=lambda x: x['name'])[:30]:
            print(f"  ❌ {m['name']:40} {m['room'][:35]}")
        
        if len(missing) > 30:
            print(f"  ... and {len(missing) - 30} more")
        
        # Check BMO specifically
        print("\n" + "=" * 80)
        print("BMO STATUS:")
        for agent_id, mapping in mappings.items():
            if mapping.get('agent_name') == 'BMO':
                room_id = mapping.get('room_id')
                in_room = room_id in your_rooms
                print(f"  Agent: BMO")
                print(f"  Room: {room_id}")
                print(f"  You are in this room: {'✅ YES' if in_room else '❌ NO'}")
                
                if not in_room:
                    print(f"\n  ⚠️  THIS IS THE PROBLEM!")
                    print(f"  You're messaging a DIFFERENT room than what's in the database")
                break

asyncio.run(main())
