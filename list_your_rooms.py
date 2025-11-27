import asyncio
import aiohttp
import os

async def main():
    homeserver = os.environ.get('MATRIX_HOMESERVER_URL', 'http://tuwunel:6167')
    admin_pass = os.environ.get('MATRIX_ADMIN_PASSWORD')
    
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
        
        rooms_url = f"{homeserver}/_matrix/client/v3/joined_rooms"
        headers = {'Authorization': f'Bearer {token}'}
        
        async with session.get(rooms_url, headers=headers) as resp:
            data = await resp.json()
            room_ids = data.get('joined_rooms', [])
        
        print(f"YOUR {len(room_ids)} ROOMS:")
        print("=" * 80)
        
        rooms_with_names = []
        for room_id in room_ids:
            name_url = f"{homeserver}/_matrix/client/v3/rooms/{room_id}/state/m.room.name"
            async with session.get(name_url, headers=headers) as resp:
                if resp.status == 200:
                    name_data = await resp.json()
                    room_name = name_data.get('name', 'Unnamed')
                else:
                    room_name = 'Unknown'
                
                rooms_with_names.append((room_name, room_id))
        
        for name, room_id in sorted(rooms_with_names):
            print(f"{name[:50]:50} {room_id}")

asyncio.run(main())
