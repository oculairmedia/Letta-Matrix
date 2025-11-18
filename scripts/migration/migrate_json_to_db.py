#!/usr/bin/env python3
"""
Migrate agent mappings from JSON file to PostgreSQL database.
This script should be run once during the transition.
"""
import json
import sys
import os
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.models.agent_mapping import Base, AgentMapping, InvitationStatus, get_engine, get_session_maker


def migrate_json_to_db(json_file_path: str, dry_run: bool = False):
    """
    Migrate agent mappings from JSON file to database

    Args:
        json_file_path: Path to agent_user_mappings.json
        dry_run: If True, don't actually write to database
    """
    print(f"Starting migration from {json_file_path}")

    # Load JSON data
    with open(json_file_path, 'r') as f:
        data = json.load(f)

    print(f"Loaded {len(data)} agents from JSON")

    # Create database engine and tables
    engine = get_engine()
    print(f"Connected to database: {engine.url}")

    if not dry_run:
        print("Creating tables...")
        Base.metadata.create_all(engine)
        print("✓ Tables created")

    # Get session maker
    Session = get_session_maker()
    session = Session()

    try:
        migrated_count = 0
        invitation_count = 0

        for agent_id, mapping_data in data.items():
            # Validate required fields
            if not all(key in mapping_data for key in ['agent_name', 'matrix_user_id', 'matrix_password']):
                print(f"⚠️  Skipping {agent_id}: missing required fields")
                continue

            # Create AgentMapping
            agent = AgentMapping(
                agent_id=agent_id,
                agent_name=mapping_data['agent_name'],
                matrix_user_id=mapping_data['matrix_user_id'],
                matrix_password=mapping_data['matrix_password'],
                room_id=mapping_data.get('room_id'),
                room_created=mapping_data.get('room_created', False),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )

            if not dry_run:
                session.add(agent)

            # Create InvitationStatus entries
            invitation_status = mapping_data.get('invitation_status', {})
            for invitee, status in invitation_status.items():
                inv = InvitationStatus(
                    agent_id=agent_id,
                    invitee=invitee,
                    status=status
                )
                if not dry_run:
                    session.add(inv)
                invitation_count += 1

            migrated_count += 1

            if migrated_count % 10 == 0:
                print(f"  Processed {migrated_count}/{len(data)} agents...")

        if not dry_run:
            print("Committing to database...")
            session.commit()
            print(f"✅ Successfully migrated {migrated_count} agents and {invitation_count} invitation statuses")
        else:
            print(f"[DRY RUN] Would have migrated {migrated_count} agents and {invitation_count} invitation statuses")

        # Verify migration
        if not dry_run:
            count = session.query(AgentMapping).count()
            print(f"Verification: {count} agents in database")

            # Show sample
            sample = session.query(AgentMapping).limit(3).all()
            print("\nSample migrated agents:")
            for agent in sample:
                print(f"  - {agent.agent_name} ({agent.agent_id})")
                print(f"    Matrix: {agent.matrix_user_id}")
                print(f"    Room: {agent.room_id}")
                print(f"    Invitations: {len(agent.invitations)}")

        return True

    except Exception as e:
        print(f"❌ Error during migration: {e}")
        if not dry_run:
            session.rollback()
        return False
    finally:
        session.close()


def backup_json_file(json_file_path: str):
    """Create a backup of the JSON file before migration"""
    backup_path = f"{json_file_path}.pre_db_migration_{int(datetime.now().timestamp())}"
    import shutil
    shutil.copy2(json_file_path, backup_path)
    print(f"✓ Created backup: {backup_path}")
    return backup_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Migrate agent mappings from JSON to database')
    parser.add_argument(
        '--json-file',
        default='/app/data/agent_user_mappings.json',
        help='Path to JSON file (default: /app/data/agent_user_mappings.json)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run without actually writing to database'
    )
    parser.add_argument(
        '--no-backup',
        action='store_true',
        help='Skip JSON file backup'
    )

    args = parser.parse_args()

    # Check if JSON file exists
    if not os.path.exists(args.json_file):
        print(f"❌ JSON file not found: {args.json_file}")
        sys.exit(1)

    # Create backup unless skipped
    if not args.no_backup and not args.dry_run:
        backup_json_file(args.json_file)

    # Run migration
    success = migrate_json_to_db(args.json_file, dry_run=args.dry_run)

    if success:
        print("\n✅ Migration completed successfully!")
        if not args.dry_run:
            print("\nNext steps:")
            print("1. Restart matrix-client container to use database")
            print("2. Monitor logs for any issues")
            print("3. JSON file is kept as backup")
    else:
        print("\n❌ Migration failed!")
        sys.exit(1)
