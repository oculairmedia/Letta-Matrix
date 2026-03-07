#!/usr/bin/env python3
"""
Bulk portal agent link setup script.

For each portal room across all bridges:
1. Identify portal rooms by bridge bot membership
2. Invite Meridian agent to the room
3. Join the room as Meridian
4. Create a portal link via the matrix-api REST API
5. Set relay mode via bridge command

Usage:
    python3 scripts/bulk-portal-setup.py [--dry-run] [--bridge whatsapp|facebook|instagram|telegram|discord|gmessages]
"""

import requests
import json
import time
import sys
import urllib.parse
import argparse
from collections import defaultdict

# ─── Configuration ───────────────────────────────────────────────

HOMESERVER = "http://localhost:6167"
API_SERVER = "http://localhost:8004"
ADMIN_TOKEN = "HcAMx3c3EAh5FbAUgj5LRpQf9pWmO7qJ"
DOMAIN = "matrix.oculair.ca"

MERIDIAN_MXID = f"@agent_597b5756_2915_4560_ba6b_91005f085166:{DOMAIN}"
MERIDIAN_AGENT_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"
MERIDIAN_USER = "agent_597b5756_2915_4560_ba6b_91005f085166"
MERIDIAN_PASSWORD = "MCP_29GNUbipw3AHOVcjqx4BIPWd"

# Bridge bot MXIDs (from appservice registrations)
BRIDGE_BOTS = {
    "whatsapp": f"@whatsappbot:{DOMAIN}",
    "facebook": f"@facebookbot:{DOMAIN}",
    "instagram": f"@metabot:{DOMAIN}",
    "telegram": f"@telegrambot:{DOMAIN}",
    "discord": f"@discordbot:{DOMAIN}",
    "gmessages": f"@gmessagesbot:{DOMAIN}",
}

# Relay commands per bridge (sent as admin in the portal room)
RELAY_COMMANDS = {
    "whatsapp": "!wa set-relay",
    "facebook": "!fb set-relay",
    "instagram": "!meta set-relay",
    "telegram": "!tg set-relay",      # May not work; relaybot is global config
    "discord": "!discord set-relay",   # May not be supported
    "gmessages": "!gm set-relay",
}

# Known non-portal rooms to skip (management rooms, admin rooms, agent rooms, etc.)
SKIP_ROOMS = set()

# ─── Helpers ─────────────────────────────────────────────────────

def encode_room_id(room_id: str) -> str:
    """URL-encode a room ID for use in Matrix API paths."""
    return urllib.parse.quote(room_id, safe='')


def matrix_get(endpoint: str, token: str = ADMIN_TOKEN) -> dict:
    """Make a GET request to the Matrix homeserver."""
    r = requests.get(
        f"{HOMESERVER}{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def matrix_post(endpoint: str, body: dict, token: str = ADMIN_TOKEN) -> dict:
    """Make a POST request to the Matrix homeserver."""
    r = requests.post(
        f"{HOMESERVER}{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    return r.json()


def matrix_put(endpoint: str, body: dict, token: str = ADMIN_TOKEN) -> dict:
    """Make a PUT request to the Matrix homeserver."""
    r = requests.put(
        f"{HOMESERVER}{endpoint}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    return r.json()


def api_get(endpoint: str) -> dict:
    """Make a GET request to the matrix-api REST server."""
    r = requests.get(f"{API_SERVER}{endpoint}", timeout=30)
    return r.json()


def api_post(endpoint: str, body: dict) -> dict:
    """Make a POST request to the matrix-api REST server."""
    r = requests.post(
        f"{API_SERVER}{endpoint}",
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    return r.json()


def api_put(endpoint: str, body: dict) -> dict:
    r = requests.put(
        f"{API_SERVER}{endpoint}",
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    return r.json()


def get_meridian_token() -> str:
    """Log in as Meridian and return access token."""
    resp = matrix_post("/_matrix/client/v3/login", {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": MERIDIAN_USER},
        "password": MERIDIAN_PASSWORD,
    })
    if "access_token" not in resp:
        print(f"  ✗ Failed to log in as Meridian: {resp}")
        sys.exit(1)
    return resp["access_token"]


def ensure_meridian_mapping() -> bool:
    payload = {
        "agent_name": "Meridian",
        "matrix_user_id": MERIDIAN_MXID,
        "matrix_password": MERIDIAN_PASSWORD,
        "room_id": None,
        "room_created": False,
    }
    resp = api_put(f"/agents/{MERIDIAN_AGENT_ID}/mapping", payload)
    return bool(resp.get("success"))


# ─── Phase 1: Discover portal rooms ─────────────────────────────

def get_all_rooms() -> list[str]:
    """Get all rooms the admin user has joined."""
    resp = matrix_get("/_matrix/client/v3/joined_rooms")
    return resp.get("joined_rooms", [])


def get_room_members(room_id: str) -> list[str]:
    """Get member list for a room."""
    encoded = encode_room_id(room_id)
    try:
        resp = matrix_get(f"/_matrix/client/v3/rooms/{encoded}/members?membership=join")
        members = []
        for event in resp.get("chunk", []):
            if event.get("type") == "m.room.member" and event.get("content", {}).get("membership") == "join":
                members.append(event["state_key"])
        return members
    except Exception as e:
        print(f"  ⚠ Failed to get members for {room_id}: {e}")
        return []


def get_room_name(room_id: str) -> str:
    """Get the display name of a room."""
    encoded = encode_room_id(room_id)
    try:
        resp = matrix_get(f"/_matrix/client/v3/rooms/{encoded}/state/m.room.name")
        return resp.get("name", "(unnamed)")
    except:
        return "(unnamed)"


def classify_rooms(rooms: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Classify rooms by bridge type based on bridge bot membership.
    
    Returns: {bridge_name: [(room_id, room_name), ...]}
    """
    bridge_bot_set = {mxid: name for name, mxid in BRIDGE_BOTS.items()}
    classified = defaultdict(list)
    non_portal = []
    
    total = len(rooms)
    for i, room_id in enumerate(rooms):
        if room_id in SKIP_ROOMS:
            continue
        
        if (i + 1) % 25 == 0 or i == 0:
            print(f"  Scanning room {i+1}/{total}...")
        
        members = get_room_members(room_id)
        found_bridge = None
        
        for member in members:
            if member in bridge_bot_set:
                found_bridge = bridge_bot_set[member]
                break
        
        if found_bridge:
            room_name = get_room_name(room_id)
            classified[found_bridge].append((room_id, room_name))
        else:
            non_portal.append(room_id)
        
        # Small delay to avoid hammering the server
        if (i + 1) % 50 == 0:
            time.sleep(0.5)
    
    return classified


# ─── Phase 2: Setup portal links ────────────────────────────────

def get_existing_portal_links() -> set[str]:
    """Get room IDs that already have portal links."""
    resp = api_get(f"/agents/portal-links")
    links = resp.get("links", [])
    return {link["room_id"] for link in links}


def setup_room(
    room_id: str,
    room_name: str,
    bridge: str,
    meridian_token: str,
    dry_run: bool = False,
    send_relay_command: bool = False,
) -> bool:
    """Set up a single portal room: invite, join, create link, set relay."""
    encoded = encode_room_id(room_id)
    print(f"  [{bridge}] {room_name} ({room_id})")
    
    if dry_run:
        print(f"    → [DRY RUN] Would invite, join, link, and set relay")
        return True
    
    # Step 1: Check if Meridian is already a member
    members = get_room_members(room_id)
    meridian_joined = MERIDIAN_MXID in members
    
    if not meridian_joined:
        # Step 1a: Invite Meridian (as admin)
        resp = matrix_post(
            f"/_matrix/client/v3/rooms/{encoded}/invite",
            {"user_id": MERIDIAN_MXID},
        )
        if "error" in resp and "already in the room" not in resp.get("error", "").lower():
            # Check if it's an "already joined" type error - that's fine
            if resp.get("errcode") not in ("M_FORBIDDEN",):
                print(f"    ⚠ Invite issue: {resp.get('error', 'unknown')}")
            else:
                print(f"    ✗ Invite failed: {resp}")
                return False
        
        time.sleep(0.3)
        
        # Step 1b: Join as Meridian
        resp = matrix_post(
            f"/_matrix/client/v3/rooms/{encoded}/join",
            {},
            token=meridian_token,
        )
        if "error" in resp:
            print(f"    ✗ Join failed: {resp}")
            return False
        
        time.sleep(0.3)
        print(f"    ✓ Meridian joined")
    else:
        print(f"    ✓ Meridian already in room")
    
    # Step 2: Create portal link
    resp = api_post(
        f"/agents/{MERIDIAN_AGENT_ID}/portal-links",
        {"room_id": room_id, "relay_mode": True},
    )
    if resp.get("success"):
        print(f"    ✓ Portal link created")
    elif "already exists" in str(resp).lower():
        print(f"    ✓ Portal link already exists")
    else:
        print(f"    ⚠ Portal link response: {resp}")
        return False

    time.sleep(0.3)
    
    # Step 3: Set relay mode (send bridge command as admin)
    relay_cmd = RELAY_COMMANDS.get(bridge)
    if send_relay_command and relay_cmd:
        txn_id = f"relay_{int(time.time() * 1000)}_{room_id[-8:]}"
        resp = matrix_put(
            f"/_matrix/client/v3/rooms/{encoded}/send/m.room.message/{txn_id}",
            {"msgtype": "m.text", "body": relay_cmd},
        )
        if "event_id" in resp:
            print(f"    ✓ Relay command sent: {relay_cmd}")
        else:
            print(f"    ⚠ Relay command issue: {resp}")
    elif relay_cmd:
        print(f"    ✓ Relay mode managed by portal link (command skipped)")
    else:
        print(f"    ℹ No relay command for {bridge} (uses global config)")
    
    time.sleep(0.5)  # Give bridge time to process relay command
    return True


# ─── Main ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bulk portal agent link setup")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without doing it")
    parser.add_argument("--bridge", type=str, help="Only process rooms for a specific bridge")
    parser.add_argument("--skip-relay", action="store_true", help="Deprecated: use --send-relay-command")
    parser.add_argument("--send-relay-command", action="store_true", help="Send bridge set-relay commands after linking")
    parser.add_argument("--max-rooms", type=int, default=1, help="Maximum rooms to process in this run (default: 1)")
    parser.add_argument("--all", action="store_true", help="Process all discovered rooms (disables --max-rooms cap)")
    args = parser.parse_args()
    
    print("=" * 60)
    print("Portal Agent Link Bulk Setup")
    print("=" * 60)
    print(f"Agent: Meridian ({MERIDIAN_AGENT_ID})")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    effective_cap = None if args.all else max(args.max_rooms, 1)
    print(f"Room cap: {'ALL' if effective_cap is None else effective_cap}")
    print(f"Send relay commands: {args.send_relay_command and not args.skip_relay}")
    if args.bridge:
        print(f"Bridge filter: {args.bridge}")
    print()
    
    # Phase 1: Discover
    print("Phase 1: Discovering portal rooms...")
    all_rooms = get_all_rooms()
    print(f"  Found {len(all_rooms)} total rooms")
    
    classified = classify_rooms(all_rooms)
    
    print(f"\n  Portal room summary:")
    total_portal = 0
    for bridge, rooms in sorted(classified.items()):
        print(f"    {bridge}: {len(rooms)} rooms")
        total_portal += len(rooms)
    print(f"    TOTAL: {total_portal} portal rooms")
    print(f"    Non-portal: {len(all_rooms) - total_portal} rooms (skipped)")
    
    # Filter by bridge if specified
    if args.bridge:
        if args.bridge not in classified:
            print(f"\n  No portal rooms found for bridge '{args.bridge}'")
            return
        classified = {args.bridge: classified[args.bridge]}
    
    # Phase 2: Check existing links
    print("\nPhase 2: Checking existing portal links...")
    existing_links = get_existing_portal_links()
    print(f"  Found {len(existing_links)} existing portal links")
    
    # Filter out rooms that already have links
    to_setup = {}
    already_linked = 0
    for bridge, rooms in classified.items():
        new_rooms = [(rid, rname) for rid, rname in rooms if rid not in existing_links]
        already = len(rooms) - len(new_rooms)
        already_linked += already
        if already > 0:
            print(f"  {bridge}: {already} already linked, {len(new_rooms)} new")
        if new_rooms:
            to_setup[bridge] = new_rooms
    
    total_new = sum(len(rooms) for rooms in to_setup.values())
    print(f"\n  Rooms to set up: {total_new}")
    print(f"  Already linked: {already_linked}")
    
    if total_new == 0:
        print("\n  ✓ All portal rooms already linked!")
        return
    
    # Phase 3: Setup
    print(f"\nPhase 3: Setting up {total_new} portal rooms...")
    
    if not args.dry_run:
        print("  Logging in as Meridian...")
        meridian_token = get_meridian_token()
        print("  ✓ Logged in")
        if ensure_meridian_mapping():
            print("  ✓ Meridian mapping upserted")
        else:
            print("  ⚠ Meridian mapping upsert failed; portal link creation may fail")
    else:
        meridian_token = "DRY_RUN"
    
    success = 0
    failed = 0
    processed = 0
    
    for bridge, rooms in sorted(to_setup.items()):
        print(f"\n  ── {bridge.upper()} ({len(rooms)} rooms) ──")
        for room_id, room_name in rooms:
            if effective_cap is not None and processed >= effective_cap:
                break
            try:
                ok = setup_room(
                    room_id,
                    room_name,
                    bridge,
                    meridian_token,
                    args.dry_run,
                    send_relay_command=(args.send_relay_command and not args.skip_relay),
                )
                if ok:
                    success += 1
                else:
                    failed += 1
                processed += 1
            except Exception as e:
                print(f"    ✗ Error: {e}")
                failed += 1
                processed += 1
        if effective_cap is not None and processed >= effective_cap:
            break
    
    # Summary
    print(f"\n{'=' * 60}")
    print(f"Setup complete!")
    print(f"  Success: {success}")
    print(f"  Failed:  {failed}")
    print(f"  Total portal links: {len(existing_links) + success}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
