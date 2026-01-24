#!/usr/bin/env python3
"""
DM Rooms Migration Script
Migrates dm_rooms.json to PostgreSQL

Usage:
    python migrate_dm_rooms.py --dry-run    # Preview changes
    python migrate_dm_rooms.py              # Execute migration
"""

import json
import argparse
import psycopg2
from datetime import datetime
from pathlib import Path

# Config
JSON_PATH = Path('/opt/stacks/matrix-synapse-deployment/mcp-servers/matrix-identity-bridge/data/dm_rooms.json')
DB_CONFIG = {
    'host': '192.168.50.90',
    'port': 5432,
    'dbname': 'matrix_letta',
    'user': 'letta',
    'password': 'letta'
}


def load_json_dm_rooms():
    """Load DM rooms from JSON file"""
    with open(JSON_PATH) as f:
        data = json.load(f)
    
    rooms = []
    for key, value in data.items():
        participants = value.get('participants', [])
        if len(participants) != 2:
            print(f"  ⚠️  Skipping {key}: expected 2 participants, got {len(participants)}")
            continue
        
        # Sort participants for consistent ordering
        p1, p2 = sorted(participants)
        
        rooms.append({
            'room_id': value.get('roomId'),
            'participant_1': p1,
            'participant_2': p2,
            'created_at': value.get('createdAt', 0),
            'last_activity_at': value.get('lastActivityAt', 0)
        })
    
    return rooms


def get_db_connection():
    """Get PostgreSQL connection"""
    return psycopg2.connect(**DB_CONFIG)


def get_existing_rooms(conn):
    """Get existing room_ids from DB"""
    with conn.cursor() as cur:
        cur.execute("SELECT room_id FROM dm_rooms")
        return {row[0] for row in cur.fetchall()}


def migrate_rooms(rooms, dry_run=False):
    """Migrate rooms to PostgreSQL"""
    conn = get_db_connection()
    existing = get_existing_rooms(conn)
    
    now = datetime.now()
    inserted = 0
    skipped = 0
    errors = []
    
    for room in rooms:
        room_id = room['room_id']
        
        if room_id in existing:
            skipped += 1
            continue
        
        # Use current time if timestamps are 0
        created_at = datetime.fromtimestamp(room['created_at'] / 1000) if room['created_at'] > 0 else now
        last_activity = datetime.fromtimestamp(room['last_activity_at'] / 1000) if room['last_activity_at'] > 0 else now
        
        if dry_run:
            print(f"  [DRY-RUN] Would insert: {room_id}")
            print(f"            {room['participant_1']} <-> {room['participant_2']}")
            inserted += 1
        else:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO dm_rooms (room_id, participant_1, participant_2, created_at, last_activity_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (room_id) DO NOTHING
                    """, (room_id, room['participant_1'], room['participant_2'], created_at, last_activity))
                conn.commit()
                inserted += 1
            except Exception as e:
                errors.append({'room_id': room_id, 'error': str(e)})
                conn.rollback()
    
    conn.close()
    return inserted, skipped, errors


def verify_migration():
    """Verify migration was successful"""
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM dm_rooms")
        db_count = cur.fetchone()[0]
    conn.close()
    
    json_rooms = load_json_dm_rooms()
    return db_count, len(json_rooms)


def main():
    parser = argparse.ArgumentParser(description='Migrate DM rooms from JSON to PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without executing')
    args = parser.parse_args()
    
    print("=" * 70)
    print("DM ROOMS MIGRATION")
    print(f"{'DRY RUN MODE' if args.dry_run else 'LIVE MODE'}")
    print("=" * 70)
    print()
    
    # Load JSON data
    print(f"Loading from: {JSON_PATH}")
    rooms = load_json_dm_rooms()
    print(f"Found {len(rooms)} DM rooms in JSON")
    print()
    
    # Check existing
    conn = get_db_connection()
    existing = get_existing_rooms(conn)
    conn.close()
    print(f"Existing rooms in DB: {len(existing)}")
    print()
    
    # Migrate
    print("=" * 70)
    print("MIGRATING")
    print("=" * 70)
    inserted, skipped, errors = migrate_rooms(rooms, dry_run=args.dry_run)
    print()
    
    # Results
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"  Inserted: {inserted}")
    print(f"  Skipped (already exist): {skipped}")
    print(f"  Errors: {len(errors)}")
    
    if errors:
        print("\n  Errors:")
        for e in errors:
            print(f"    - {e['room_id']}: {e['error']}")
    
    # Verify
    if not args.dry_run:
        print()
        print("=" * 70)
        print("VERIFICATION")
        print("=" * 70)
        db_count, json_count = verify_migration()
        print(f"  JSON rooms: {json_count}")
        print(f"  DB rooms:   {db_count}")
        
        if db_count >= json_count:
            print("\n  ✅ Migration successful!")
        else:
            print(f"\n  ⚠️  DB has fewer rooms than JSON ({db_count} < {json_count})")
    
    print()


if __name__ == '__main__':
    main()
