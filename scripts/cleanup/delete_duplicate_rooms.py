#!/usr/bin/env python3
"""
Delete duplicate Matrix rooms, keeping only the newest instance of each.

This script:
1. Reads duplicate room info from /tmp/duplicate_rooms.json
2. For each duplicated room name, keeps the newest and deletes the rest
3. Uses admin account to leave/forget old rooms
4. Updates agent_user_mappings.json to point to kept rooms
"""

import requests
import json
import os
import sys
from pathlib import Path

def load_env():
    """Load environment variables from .env file"""
    env_file = Path(__file__).parent.parent.parent / '.env'
    env_vars = {}
    
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value.strip()
    
    return env_vars

def get_admin_token(env_vars):
    """Login as admin and get access token"""
    login_resp = requests.post(
        'http://localhost:8008/_matrix/client/v3/login',
        json={
            'type': 'm.login.password',
            'user': 'admin',
            'password': env_vars['MATRIX_ADMIN_PASSWORD']
        }
    )
    
    if login_resp.status_code != 200:
        print(f"❌ Failed to login as admin: {login_resp.text}")
        sys.exit(1)
    
    return login_resp.json()['access_token']

def leave_and_forget_room(room_id, token):
    """Leave and forget a room"""
    # Leave room
    leave_resp = requests.post(
        f'http://localhost:8008/_matrix/client/v3/rooms/{room_id}/leave',
        headers={'Authorization': f'Bearer {token}'}
    )
    
    if leave_resp.status_code not in [200, 404]:
        print(f"  ⚠️  Warning: Failed to leave {room_id}: {leave_resp.text}")
        return False
    
    # Forget room
    forget_resp = requests.post(
        f'http://localhost:8008/_matrix/client/v3/rooms/{room_id}/forget',
        headers={'Authorization': f'Bearer {token}'}
    )
    
    if forget_resp.status_code not in [200, 404]:
        print(f"  ⚠️  Warning: Failed to forget {room_id}: {forget_resp.text}")
        return False
    
    return True

def update_agent_mappings(old_to_new_map):
    """Update agent_user_mappings.json to use new room IDs"""
    mappings_file = Path(__file__).parent.parent.parent / 'matrix_client_data' / 'agent_user_mappings.json'
    
    if not mappings_file.exists():
        print("⚠️  agent_user_mappings.json not found, skipping mapping update")
        return
    
    with open(mappings_file) as f:
        mappings = json.load(f)
    
    updated = 0
    for agent_id, mapping in mappings.items():
        old_room_id = mapping.get('room_id')
        if old_room_id in old_to_new_map:
            new_room_id = old_to_new_map[old_room_id]
            print(f"  📝 Updating {agent_id}: {old_room_id} -> {new_room_id}")
            mapping['room_id'] = new_room_id
            updated += 1
    
    if updated > 0:
        # Backup old mappings
        backup_file = mappings_file.with_suffix('.json.before_dedup')
        with open(backup_file, 'w') as f:
            json.dump(mappings, f, indent=2)
        print(f"✅ Backed up old mappings to {backup_file}")
        
        # Save updated mappings
        with open(mappings_file, 'w') as f:
            json.dump(mappings, f, indent=2)
        print(f"✅ Updated {updated} agent room mappings")
    else:
        print("ℹ️  No agent mappings needed updating")

def update_space_config(old_to_new_map):
    """Update letta_space_config.json if current space is being deleted"""
    space_config_file = Path(__file__).parent.parent.parent / 'matrix_client_data' / 'letta_space_config.json'
    
    if not space_config_file.exists():
        print("⚠️  letta_space_config.json not found, skipping space update")
        return
    
    with open(space_config_file) as f:
        space_config = json.load(f)
    
    old_space_id = space_config.get('space_id')
    if old_space_id in old_to_new_map:
        new_space_id = old_to_new_map[old_space_id]
        print(f"  📝 Updating Letta Agents space: {old_space_id} -> {new_space_id}")
        
        # Backup old config
        backup_file = space_config_file.with_suffix('.json.before_dedup')
        with open(backup_file, 'w') as f:
            json.dump(space_config, f, indent=2)
        print(f"✅ Backed up old space config to {backup_file}")
        
        # Update config
        space_config['space_id'] = new_space_id
        with open(space_config_file, 'w') as f:
            json.dump(space_config, f, indent=2)
        print(f"✅ Updated space config to use new space")
    else:
        print("ℹ️  Current space is not being deleted, no update needed")

def main():
    print("=" * 80)
    print("DELETING DUPLICATE MATRIX ROOMS")
    print("=" * 80)
    
    # Check if duplicate info exists
    dup_file = Path('/tmp/duplicate_rooms.json')
    if not dup_file.exists():
        print("❌ No duplicate room info found. Run the detection script first.")
        sys.exit(1)
    
    with open(dup_file) as f:
        duplicates = json.load(f)
    
    # Load environment and get admin token
    env_vars = load_env()
    token = get_admin_token(env_vars)
    print(f"✅ Logged in as admin\n")
    
    # Track old -> new room ID mappings
    old_to_new_map = {}
    
    # Delete duplicate rooms
    total_deleted = 0
    total_failed = 0
    
    for name, rooms in duplicates.items():
        print(f"\n{name} ({len(rooms)} instances)")
        
        # First room is newest (keep), rest are deleted
        keep_room = rooms[0]
        delete_rooms = rooms[1:]
        
        print(f"  ⭐ Keeping: {keep_room['room_id']}")
        
        for room in delete_rooms:
            room_id = room['room_id']
            print(f"  ❌ Deleting: {room_id}")
            
            # Track mapping from old to new
            old_to_new_map[room_id] = keep_room['room_id']
            
            if leave_and_forget_room(room_id, token):
                print(f"     ✅ Successfully removed")
                total_deleted += 1
            else:
                print(f"     ❌ Failed to remove")
                total_failed += 1
    
    print(f"\n{'=' * 80}")
    print(f"DELETION SUMMARY")
    print(f"{'=' * 80}")
    print(f"Successfully deleted: {total_deleted}")
    print(f"Failed to delete: {total_failed}")
    print(f"Total rooms cleaned: {len(duplicates)}")
    
    # Update agent mappings
    print(f"\n{'=' * 80}")
    print(f"UPDATING AGENT MAPPINGS")
    print(f"{'=' * 80}")
    update_agent_mappings(old_to_new_map)
    
    # Update space config
    print(f"\n{'=' * 80}")
    print(f"UPDATING SPACE CONFIG")
    print(f"{'=' * 80}")
    update_space_config(old_to_new_map)
    
    print(f"\n{'=' * 80}")
    print("✅ CLEANUP COMPLETE!")
    print(f"{'=' * 80}")
    print("\nNext steps:")
    print("1. Restart matrix-client to pick up new mappings:")
    print("   docker-compose restart matrix-client")
    print("2. Monitor logs to verify agents use new rooms:")
    print("   docker logs -f matrix-synapse-deployment-matrix-client-1")

if __name__ == '__main__':
    main()
