#!/usr/bin/env python3
import requests
import json

# Direct API call to join room
HOMESERVER = "http://synapse:8008"
ROOM_ALIAS = "#_discord_1386202835104043111_1386202835787452529:oculair.ca"

# First, let's register the bot user if needed
bot_user = "_discord_bot"
bot_localpart = "_discord_bot"

# Try to make a direct join request through the admin API
# This will trigger the bridge to create the room
print(f"Attempting to resolve room alias: {ROOM_ALIAS}")

# URL encode the room alias
import urllib.parse
encoded_alias = urllib.parse.quote(ROOM_ALIAS)

# Try to resolve the alias
resolve_url = f"{HOMESERVER}/_matrix/client/r0/directory/room/{encoded_alias}"
print(f"Checking: {resolve_url}")

response = requests.get(resolve_url)
print(f"Response: {response.status_code} - {response.text}")

if response.status_code == 404:
    print("\nRoom doesn't exist yet. The Discord bridge will create it when someone joins.")
    print("\nTo join the room as the letta user:")
    print("1. In your Matrix client (Element), click on 'Explore rooms' or press Ctrl+K")
    print(f"2. Type or paste: {ROOM_ALIAS}")
    print("3. Click Join")
    print("\nOr use the command: /join " + ROOM_ALIAS)