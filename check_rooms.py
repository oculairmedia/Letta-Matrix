import asyncio
import aiohttp
import os
import json

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
            if resp.status != 200:
                print(f"Login failed")
                return
            auth = await resp.json()
            token = auth['access_token']
            print("✅ Logged in as admin")
        
        # Get rooms
        rooms_url = f"{homeserver}/_matrix/client/v3/joined_rooms"
        headers = {'Authorization': f'Bearer {token}'}
        
        async with session.get(rooms_url, headers=headers) as resp:
            data = await resp.json()
            rooms = data.get('joined_rooms', [])
            
            print(f"\nFound {len(rooms)} rooms")
            
            # Load mappings
            with open('/app/data/agent_user_mappings.json', 'r') as f:
                mappings = json.load(f)
            
            # Check each room
            print("\nChecking room names...")
            mismatches = []
            
            for room_id in rooms:
                name_url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
                async with session.get(name_url, headers=headers) as resp:
                    if resp.status == 200:
                        name_data = await resp.json()
                        room_name = name_data.get('name', '')
                    else:
                        continue
                
                # Find if mapped
                agent_for_room = None
                db_agent_id = None
                for agent_id, mapping in mappings.items():
                    if mapping.get('room_id') == room_id:
                        agent_for_room = mapping.get('agent_name')
                        db_agent_id = agent_id
                        break
                
                # Check for mismatch
                if room_name and not agent_for_room:
                    # See if there's an agent with this name
                    for agent_id, mapping in mappings.items():
                        if mapping.get('agent_name') == room_name:
                            mismatches.append({
                                'room_name': room_name,
                                'actual_room': room_id,
                                'db_room': mapping.get('room_id'),
                                'agent_id': agent_id
                            })
                            break
            
            print(f"\nFound {len(mismatches)} mismatched mappings")
            
            if mismatches:
                print("\nMISMATCHES (first 20):")
                for m in mismatches[:20]:
                    print(f"\n  Agent: {m['room_name']}")
                    print(f"    Your room:  {m['actual_room']}")
                    print(f"    DB has:     {m['db_room']}")
                    print(f"    Agent ID:   {m['agent_id']}")

asyncio.run(main())
