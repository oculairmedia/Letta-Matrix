#!/usr/bin/env python3
"""
Fix gmessages portal rooms that failed due to insufficient invite power.
Uses the gmessages appservice token to invite as the bridge bot.
"""

import requests
import json
import time
import urllib.parse

HOMESERVER = "http://localhost:6167"
API_SERVER = "http://localhost:8004"
ADMIN_TOKEN = "HcAMx3c3EAh5FbAUgj5LRpQf9pWmO7qJ"
DOMAIN = "matrix.oculair.ca"

# Gmessages appservice token (from registration.yaml)
GM_AS_TOKEN = "TigM8WUqqeURX22h3QUITJuVFi7j2SySUzFT6zxj1dn0ASecNRrcoLgLXKu1sQmR"
GM_BOT_MXID = f"@gmessagesbot:{DOMAIN}"

MERIDIAN_MXID = f"@agent_597b5756_2915_4560_ba6b_91005f085166:{DOMAIN}"
MERIDIAN_AGENT_ID = "agent-597b5756-2915-4560-ba6b-91005f085166"
MERIDIAN_USER = "agent_597b5756_2915_4560_ba6b_91005f085166"
MERIDIAN_PASSWORD = "MCP_29GNUbipw3AHOVcjqx4BIPWd"


def encode_room_id(room_id: str) -> str:
    return urllib.parse.quote(room_id, safe='')


def main():
    # Get Meridian token
    print("Logging in as Meridian...")
    resp = requests.post(f"{HOMESERVER}/_matrix/client/v3/login", json={
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": MERIDIAN_USER},
        "password": MERIDIAN_PASSWORD,
    }, timeout=30).json()
    meridian_token = resp["access_token"]
    print("✓ Logged in")

    # Get existing portal links
    existing = set()
    resp = requests.get(f"{API_SERVER}/agents/portal-links", timeout=30).json()
    for link in resp.get("links", []):
        existing.add(link["room_id"])
    print(f"Existing portal links: {len(existing)}")

    # Get all rooms
    resp = requests.get(f"{HOMESERVER}/_matrix/client/v3/joined_rooms",
                        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, timeout=30).json()
    all_rooms = resp.get("joined_rooms", [])

    # Find gmessages rooms (rooms containing gmessagesbot)
    gm_rooms = []
    total = len(all_rooms)
    for i, room_id in enumerate(all_rooms):
        if room_id in existing:
            continue  # Already linked
        
        if (i + 1) % 50 == 0:
            print(f"  Scanning {i+1}/{total}...")
        
        encoded = encode_room_id(room_id)
        try:
            resp = requests.get(
                f"{HOMESERVER}/_matrix/client/v3/rooms/{encoded}/members?membership=join",
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                timeout=30
            ).json()
            members = [e["state_key"] for e in resp.get("chunk", [])
                       if e.get("type") == "m.room.member" and e.get("content", {}).get("membership") == "join"]
            
            if GM_BOT_MXID in members and MERIDIAN_MXID not in members:
                # Get room name
                try:
                    name_resp = requests.get(
                        f"{HOMESERVER}/_matrix/client/v3/rooms/{encoded}/state/m.room.name",
                        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
                        timeout=30
                    ).json()
                    room_name = name_resp.get("name", "(unnamed)")
                except:
                    room_name = "(unnamed)"
                gm_rooms.append((room_id, room_name))
        except Exception as e:
            pass

    print(f"\nFound {len(gm_rooms)} gmessages rooms needing setup")

    success = 0
    failed = 0

    for room_id, room_name in gm_rooms:
        encoded = encode_room_id(room_id)
        print(f"\n  {room_name} ({room_id})")

        # Step 1: Invite using appservice token (as gmessagesbot)
        resp = requests.post(
            f"{HOMESERVER}/_matrix/client/v3/rooms/{encoded}/invite?user_id={urllib.parse.quote(GM_BOT_MXID)}",
            headers={"Authorization": f"Bearer {GM_AS_TOKEN}", "Content-Type": "application/json"},
            json={"user_id": MERIDIAN_MXID},
            timeout=30
        ).json()
        if "error" in resp:
            print(f"    ✗ Invite failed: {resp}")
            failed += 1
            continue
        print(f"    ✓ Invited via appservice")
        time.sleep(0.3)

        # Step 2: Join as Meridian
        resp = requests.post(
            f"{HOMESERVER}/_matrix/client/v3/rooms/{encoded}/join",
            headers={"Authorization": f"Bearer {meridian_token}", "Content-Type": "application/json"},
            json={},
            timeout=30
        ).json()
        if "error" in resp:
            print(f"    ✗ Join failed: {resp}")
            failed += 1
            continue
        print(f"    ✓ Meridian joined")
        time.sleep(0.3)

        # Step 3: Create portal link
        resp = requests.post(
            f"{API_SERVER}/agents/{MERIDIAN_AGENT_ID}/portal-links",
            headers={"Content-Type": "application/json"},
            json={"room_id": room_id},
            timeout=30
        ).json()
        if resp.get("success"):
            print(f"    ✓ Portal link created")
        elif "already exists" in str(resp).lower():
            print(f"    ✓ Portal link already exists")
        else:
            print(f"    ⚠ Portal link response: {resp}")

        time.sleep(0.3)

        # Step 4: Set relay (send as admin — admin IS in the room)
        txn_id = f"relay_{int(time.time() * 1000)}_{room_id[-8:]}"
        resp = requests.put(
            f"{HOMESERVER}/_matrix/client/v3/rooms/{encoded}/send/m.room.message/{txn_id}",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"},
            json={"msgtype": "m.text", "body": "!gm set-relay"},
            timeout=30
        ).json()
        if "event_id" in resp:
            print(f"    ✓ Relay command sent")
        else:
            print(f"    ⚠ Relay command issue: {resp}")

        time.sleep(0.5)
        success += 1

    print(f"\n{'=' * 50}")
    print(f"Done! Success: {success}, Failed: {failed}")
    print(f"Total portal links: {len(existing) + success}")


if __name__ == "__main__":
    main()
