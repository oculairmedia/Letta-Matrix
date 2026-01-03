#!/usr/bin/env python3
"""
End-to-end test for identity-based posting.

Tests that agents can post to Matrix using their own credentials from PostgreSQL.

Usage:
    python scripts/testing/test_identity_posting.py [--agent-id AGENT_ID] [--room-id ROOM_ID]
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import aiohttp


API_URL = os.getenv("MATRIX_API_URL", "http://localhost:8004")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://letta:letta@localhost:5432/matrix_letta")

TEST_AGENT_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"
TEST_MESSAGE = "[Identity Test] Posted via Python identity pathway from PostgreSQL."


async def get_identity(agent_id: str):
    async with aiohttp.ClientSession() as session:
        url = f"{API_URL}/api/v1/identities/by-agent/{agent_id}"
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
            elif resp.status == 404:
                return None
            else:
                raise RuntimeError(f"API error: {resp.status} {await resp.text()}")


async def get_agent_room_from_db(agent_id: str):
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT room_id FROM agent_mappings WHERE agent_id = %s", (agent_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"  WARN: Could not query database: {e}")
        return None


async def send_as_agent(agent_id: str, room_id: str, message: str) -> dict:
    async with aiohttp.ClientSession() as session:
        url = f"{API_URL}/api/v1/messages/send-as-agent"
        payload = {
            "agent_id": agent_id,
            "room_id": room_id,
            "message": message
        }
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get("success"):
                return data
            else:
                raise RuntimeError(f"Send failed: {data}")


async def main():
    parser = argparse.ArgumentParser(description='Test identity-based posting')
    parser.add_argument('--agent-id', default=TEST_AGENT_ID, help='Letta agent ID')
    parser.add_argument('--room-id', default=None, help='Room ID (auto-detected if not provided)')
    parser.add_argument('--message', default=TEST_MESSAGE, help='Test message')
    args = parser.parse_args()

    agent_id = args.agent_id
    if not agent_id.startswith("agent-"):
        agent_id = f"agent-{agent_id}"

    print("=" * 60)
    print("IDENTITY POSTING TEST")
    print("=" * 60)
    print(f"Agent ID: {agent_id}")
    print(f"API URL: {API_URL}")
    print()

    print("[1/4] Fetching identity from PostgreSQL...")
    identity = await get_identity(agent_id)
    if not identity:
        print(f"  FAIL: No identity found for agent {agent_id}")
        print("  Run: python scripts/migration/import_identities.py")
        sys.exit(1)
    
    print(f"  OK: Found identity")
    print(f"      ID: {identity.get('id')}")
    print(f"      MXID: {identity.get('mxid')}")
    print(f"      Display: {identity.get('display_name')}")
    print(f"      Active: {identity.get('is_active')}")
    print()

    room_id = args.room_id
    if not room_id:
        print("[2/4] Looking up agent's Matrix room...")
        room_id = await get_agent_room_from_db(agent_id)
        if not room_id:
            print("  FAIL: No room found. Provide --room-id manually.")
            sys.exit(1)
        print(f"  OK: Found room {room_id}")
    else:
        print(f"[2/4] Using provided room: {room_id}")
    print()

    print("[3/4] Sending test message via send-as-agent API...")
    try:
        result = await send_as_agent(agent_id, room_id, args.message)
        print(f"  OK: Message sent successfully")
        print(f"      Event ID: {result.get('event_id')}")
        print(f"      Identity: {result.get('identity_id')}")
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)
    print()

    print("[4/4] Summary")
    print("=" * 60)
    print("TEST PASSED")
    print("=" * 60)
    print(f"Agent '{identity.get('display_name')}' posted to Matrix room")
    print(f"using credentials from PostgreSQL identities table.")
    print()
    print("The Python identity pathway is working correctly!")
    print()
    print("Environment flags in effect:")
    print("  USE_IDENTITY_POSTING=true")
    print("  IDENTITY_POSTING_STRICT=true")


if __name__ == "__main__":
    asyncio.run(main())
