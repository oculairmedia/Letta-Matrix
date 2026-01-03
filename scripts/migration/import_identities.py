#!/usr/bin/env python3
"""
Migration script to import identities from TypeScript JSON file to PostgreSQL.

Usage:
    python scripts/migration/import_identities.py [--dry-run]

Source: mcp-servers/matrix-identity-bridge/data/identities.json
Target: PostgreSQL identities table
"""
import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def get_database_url() -> str:
    return os.environ.get(
        'DATABASE_URL',
        'postgresql://letta:letta@localhost:5432/matrix_letta'
    )


def ms_to_datetime(ms_timestamp: Optional[int]) -> Optional[datetime]:
    """Converts epoch milliseconds to datetime (TypeScript stores timestamps as ms)"""
    if ms_timestamp is None:
        return None
    return datetime.fromtimestamp(ms_timestamp / 1000.0)


def transform_identity(identity_id: str, data: dict) -> dict:
    """Maps TypeScript JSON fields to PostgreSQL column names"""
    created_at_ms = data.get('createdAt')
    last_used_at_ms = data.get('lastUsedAt')
    return {
        'id': identity_id,
        'identity_type': data.get('type', 'letta'),
        'mxid': data.get('mxid'),
        'display_name': data.get('displayName'),
        'avatar_url': data.get('avatarUrl'),
        'access_token': data.get('accessToken'),
        'password_hash': data.get('password'),
        'device_id': data.get('deviceId'),
        'created_at': ms_to_datetime(created_at_ms),
        'updated_at': datetime.utcnow(),
        'last_used_at': ms_to_datetime(last_used_at_ms),
        'is_active': True
    }


def main():
    parser = argparse.ArgumentParser(description='Import identities from JSON to PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Print actions without executing')
    parser.add_argument('--source', default=None, help='Source JSON file path')
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent.parent
    if args.source:
        source_path = Path(args.source)
    else:
        source_path = project_root / 'mcp-servers' / 'matrix-identity-bridge' / 'data' / 'identities.json'

    print(f"Source file: {source_path}")
    
    if not source_path.exists():
        print(f"ERROR: Source file not found: {source_path}")
        sys.exit(1)

    with open(source_path) as f:
        identities_data = json.load(f)

    print(f"Found {len(identities_data)} identities in JSON file")

    if args.dry_run:
        print("\n--- DRY RUN MODE ---\n")
        for identity_id, data in identities_data.items():
            row = transform_identity(identity_id, data)
            print(f"Would insert: {row['id']}")
            print(f"  type: {row['identity_type']}")
            print(f"  mxid: {row['mxid']}")
            print(f"  display_name: {row['display_name']}")
            print(f"  has_password: {row['password_hash'] is not None}")
            print()
        print(f"Total: {len(identities_data)} identities would be imported")
        return

    db_url = get_database_url()
    print(f"Connecting to database...")
    
    # Fallback: localhost -> docker container name when running outside docker
    if 'localhost' in db_url or '127.0.0.1' in db_url:
        docker_url = db_url.replace('localhost', 'letta-postgres-1').replace('127.0.0.1', 'letta-postgres-1')
        try:
            engine = create_engine(db_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            print(f"Local connection failed, trying docker network...")
            engine = create_engine(docker_url, pool_pre_ping=True)
    else:
        engine = create_engine(db_url, pool_pre_ping=True)

    Session = sessionmaker(bind=engine)
    session = Session()

    result = session.execute(text("SELECT COUNT(*) FROM identities"))
    current_count = result.scalar() or 0
    print(f"Current identities in database: {current_count}")

    if current_count > 0:
        print("\nWARNING: Database already has identities.")
        response = input("Do you want to continue? Duplicates will be skipped. [y/N]: ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(0)

    imported = 0
    skipped = 0
    errors = []

    for identity_id, data in identities_data.items():
        try:
            result = session.execute(
                text("SELECT id FROM identities WHERE id = :id"),
                {'id': identity_id}
            )
            if result.fetchone():
                print(f"  SKIP (exists): {identity_id}")
                skipped += 1
                continue

            row = transform_identity(identity_id, data)
            
            session.execute(
                text("""
                    INSERT INTO identities 
                    (id, identity_type, mxid, display_name, avatar_url, access_token, 
                     password_hash, device_id, created_at, updated_at, last_used_at, is_active)
                    VALUES 
                    (:id, :identity_type, :mxid, :display_name, :avatar_url, :access_token,
                     :password_hash, :device_id, :created_at, :updated_at, :last_used_at, :is_active)
                """),
                row
            )
            session.commit()
            
            print(f"  OK: {identity_id} ({row['display_name']})")
            imported += 1

        except Exception as e:
            session.rollback()
            print(f"  ERROR: {identity_id} - {e}")
            errors.append((identity_id, str(e)))

    session.close()

    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"Total in source:    {len(identities_data)}")
    print(f"Successfully imported: {imported}")
    print(f"Skipped (exists):      {skipped}")
    print(f"Errors:                {len(errors)}")
    
    if errors:
        print("\nErrors:")
        for identity_id, error in errors:
            print(f"  - {identity_id}: {error}")

    session = Session()
    result = session.execute(text("SELECT COUNT(*) FROM identities"))
    final_count = result.scalar()
    session.close()
    print(f"\nFinal count in database: {final_count}")


if __name__ == '__main__':
    main()
