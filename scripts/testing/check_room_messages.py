#!/usr/bin/env python3
"""
Check recent messages in BMO's room to see sender identity
"""
import requests
import json

# Configuration
MATRIX_API_URL = "http://localhost:8004"
BMO_ROOM_ID = "!FPScONTnyMLWfiFMlZ:matrix.oculair.ca"
ADMIN_USER = "@letta:matrix.oculair.ca"
ADMIN_PASSWORD = "letta"

# Login as admin to read the room
def login():
    url = f"{MATRIX_API_URL}/login"
    payload = {
        "homeserver": "http://synapse:8008",
        "user_id": ADMIN_USER,
        "password": ADMIN_PASSWORD,
        "device_name": "message_checker"
    }

    resp = requests.post(url, json=payload)
    if resp.status_code == 200:
        result = resp.json()
        if result.get("success"):
            return result.get("access_token")
    return None

# Get recent messages directly from Matrix
def get_messages(token):
    # Use direct Matrix API for fresher results
    url = f"http://localhost:8008/_matrix/client/r0/rooms/{BMO_ROOM_ID}/messages"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"dir": "b", "limit": 20}  # Get more messages

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        result = resp.json()
        messages = []
        for event in result.get("chunk", []):
            if event.get("type") == "m.room.message":
                messages.append({
                    "sender": event.get("sender"),
                    "body": event.get("content", {}).get("body", ""),
                    "timestamp": event.get("origin_server_ts", 0),
                    "event_id": event.get("event_id")
                })
        return messages
    return []

def main():
    print("=" * 60)
    print("Recent messages in BMO's room")
    print("=" * 60)

    token = login()
    if not token:
        print("Failed to login")
        return

    messages = get_messages(token)

    print(f"Found {len(messages)} messages\n")

    for msg in messages[:10]:  # Show last 10 messages
        sender = msg.get("sender", "unknown")
        body = msg.get("body", "")[:100]  # First 100 chars
        event_id = msg.get("event_id", "")

        # Identify the sender
        if sender.startswith("@agent_597b5756"):
            sender_name = "🌟 MERIDIAN"
        elif sender.startswith("@agent_f2fdf2aa"):
            sender_name = "🤖 BMO"
        elif sender == "@letta:matrix.oculair.ca":
            sender_name = "🔧 @letta (admin)"
        elif sender == "@admin:matrix.oculair.ca":
            sender_name = "👤 @admin"
        else:
            sender_name = sender

        print(f"\n📨 From: {sender_name}")
        print(f"   Matrix ID: {sender}")
        print(f"   Event ID: {event_id[-20:]}")  # Last 20 chars of event ID
        print(f"   Message: {body}")
        print("-" * 40)

if __name__ == "__main__":
    main()