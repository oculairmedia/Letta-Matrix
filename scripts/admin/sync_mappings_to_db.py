#!/usr/bin/env python3
"""
Sync agent mappings from JSON file to PostgreSQL database.

This script migrates agent mappings from the legacy JSON file to the PostgreSQL
database, ensuring that all existing room-to-agent mappings are preserved and
routing works correctly.

Usage:
    python scripts/admin/sync_mappings_to_db.py [--json-file PATH] [--dry-run]
"""
import json
import sys
import os
from pathlib import Path
import argparse
from typing import Dict, List

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.models.agent_mapping import AgentMappingDB, init_database


def load_json_mappings(json_file: str) -> Dict:
    """Load agent mappings from JSON file."""
    if not os.path.exists(json_file):
        print(f"❌ JSON file not found: {json_file}")
        sys.exit(1)
    
    with open(json_file, 'r') as f:
        return json.load(f)


def sync_mappings_to_database(json_mappings: Dict, dry_run: bool = False) -> None:
    """Sync JSON mappings to PostgreSQL database."""
    
    # Initialize database
    if not dry_run:
        print("📊 Initializing database...")
        init_database()
    
    db = AgentMappingDB()
    
    # Get existing mappings from database
    existing_mappings = {m.agent_id: m for m in db.get_all()} if not dry_run else {}
    
    print(f"\n📋 Found {len(json_mappings)} mappings in JSON")
    print(f"📋 Found {len(existing_mappings)} mappings in database\n")
    
    stats = {
        'created': 0,
        'updated': 0,
        'skipped': 0,
        'errors': 0
    }
    
    for agent_id, mapping_data in json_mappings.items():
        try:
            agent_name = mapping_data.get('agent_name', 'Unknown')
            matrix_user_id = mapping_data.get('matrix_user_id')
            matrix_password = mapping_data.get('matrix_password', 'password')
            room_id = mapping_data.get('room_id')
            room_created = mapping_data.get('room_created', False)
            
            # Validate required fields
            if not matrix_user_id:
                print(f"⚠️  Skipping {agent_id}: Missing matrix_user_id")
                stats['skipped'] += 1
                continue
            
            # Check if mapping exists in database
            existing = existing_mappings.get(agent_id)
            
            if dry_run:
                if existing:
                    if existing.room_id != room_id:
                        print(f"🔄 Would UPDATE {agent_name} ({agent_id})")
                        print(f"   Old room: {existing.room_id}")
                        print(f"   New room: {room_id}")
                        stats['updated'] += 1
                    else:
                        stats['skipped'] += 1
                else:
                    print(f"✨ Would CREATE {agent_name} ({agent_id})")
                    print(f"   Room: {room_id}")
                    stats['created'] += 1
            else:
                if existing:
                    # Update if room_id changed
                    if existing.room_id != room_id or existing.agent_name != agent_name:
                        print(f"🔄 Updating {agent_name} ({agent_id})")
                        db.upsert(
                            agent_id=agent_id,
                            agent_name=agent_name,
                            matrix_user_id=matrix_user_id,
                            matrix_password=matrix_password,
                            room_id=room_id,
                            room_created=room_created
                        )
                        stats['updated'] += 1
                    else:
                        stats['skipped'] += 1
                else:
                    # Create new mapping
                    print(f"✨ Creating {agent_name} ({agent_id})")
                    db.upsert(
                        agent_id=agent_id,
                        agent_name=agent_name,
                        matrix_user_id=matrix_user_id,
                        matrix_password=matrix_password,
                        room_id=room_id,
                        room_created=room_created
                    )
                    stats['created'] += 1
                    
        except Exception as e:
            print(f"❌ Error processing {agent_id}: {e}")
            stats['errors'] += 1
    
    # Print summary
    print("\n" + "="*60)
    print("SYNC SUMMARY")
    print("="*60)
    print(f"✨ Created: {stats['created']}")
    print(f"🔄 Updated: {stats['updated']}")
    print(f"⏭️  Skipped: {stats['skipped']}")
    print(f"❌ Errors:  {stats['errors']}")
    print("="*60)
    
    if dry_run:
        print("\n🔍 DRY RUN - No changes were made to the database")
    else:
        print("\n✅ Database sync complete!")


def verify_sync(json_file: str) -> None:
    """Verify that JSON mappings are synced to database."""
    json_mappings = load_json_mappings(json_file)
    db = AgentMappingDB()
    db_mappings = {m.agent_id: m for m in db.get_all()}
    
    print("\n🔍 VERIFICATION REPORT")
    print("="*60)
    
    missing_in_db = []
    mismatched_rooms = []
    
    for agent_id, json_data in json_mappings.items():
        if agent_id not in db_mappings:
            missing_in_db.append((agent_id, json_data.get('agent_name')))
        else:
            db_room = db_mappings[agent_id].room_id
            json_room = json_data.get('room_id')
            if db_room != json_room:
                mismatched_rooms.append((
                    agent_id,
                    json_data.get('agent_name'),
                    json_room,
                    db_room
                ))
    
    if missing_in_db:
        print(f"\n⚠️  {len(missing_in_db)} agents missing in database:")
        for agent_id, name in missing_in_db[:10]:  # Show first 10
            print(f"   - {name} ({agent_id})")
        if len(missing_in_db) > 10:
            print(f"   ... and {len(missing_in_db) - 10} more")
    
    if mismatched_rooms:
        print(f"\n⚠️  {len(mismatched_rooms)} room ID mismatches:")
        for agent_id, name, json_room, db_room in mismatched_rooms[:10]:
            print(f"   - {name} ({agent_id})")
            print(f"     JSON: {json_room}")
            print(f"     DB:   {db_room}")
    
    if not missing_in_db and not mismatched_rooms:
        print("✅ All mappings are in sync!")
    
    print("="*60)


def main():
    parser = argparse.ArgumentParser(
        description="Sync agent mappings from JSON to PostgreSQL"
    )
    parser.add_argument(
        '--json-file',
        default='/app/data/agent_user_mappings.json',
        help='Path to JSON mappings file (default: /app/data/agent_user_mappings.json)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be synced without making changes'
    )
    parser.add_argument(
        '--verify',
        action='store_true',
        help='Verify sync status without making changes'
    )
    
    args = parser.parse_args()
    
    # Check if JSON file exists
    json_file = args.json_file
    if not os.path.exists(json_file):
        # Try alternate paths
        alternate_paths = [
            'matrix_client_data/agent_user_mappings.json',
            '/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_user_mappings.json'
        ]
        for alt_path in alternate_paths:
            if os.path.exists(alt_path):
                json_file = alt_path
                print(f"📁 Using alternate path: {json_file}")
                break
    
    if args.verify:
        verify_sync(json_file)
    else:
        json_mappings = load_json_mappings(json_file)
        sync_mappings_to_database(json_mappings, dry_run=args.dry_run)
        
        if not args.dry_run:
            print("\n🔍 Running verification...")
            verify_sync(json_file)


if __name__ == '__main__':
    main()
