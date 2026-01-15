#!/bin/bash
set -e

echo "=== Matrix Bridge Bot Setup Helper ==="
echo ""
echo "This script helps you set up the Matrix bot for the bridge."
echo ""

BRIDGE_DIR="/opt/stacks/matrix-synapse-deployment/real-a2a-bridge"
CONFIG_FILE="$BRIDGE_DIR/config/bridge-config.json"
EXAMPLE_CONFIG="$BRIDGE_DIR/config/bridge-config.example.json"

if [ -f "$CONFIG_FILE" ]; then
    echo "⚠️  Configuration file already exists: $CONFIG_FILE"
    read -p "Overwrite? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Exiting without changes."
        exit 0
    fi
fi

echo ""
echo "📝 Matrix Configuration"
echo ""

read -p "Matrix homeserver URL [https://matrix.oculair.ca]: " HOMESERVER
HOMESERVER=${HOMESERVER:-https://matrix.oculair.ca}

echo ""
echo "To get your Matrix access token:"
echo "  1. Login to Element (or another Matrix client)"
echo "  2. Go to Settings → Help & About"
echo "  3. Scroll down to 'Access Token'"
echo "  4. Click to reveal and copy"
echo ""
read -p "Matrix access token: " ACCESS_TOKEN

if [ -z "$ACCESS_TOKEN" ]; then
    echo "❌ Access token is required!"
    exit 1
fi

echo ""
echo "To get a room ID:"
echo "  1. In Element, go to the room"
echo "  2. Room Settings → Advanced"
echo "  3. Copy the 'Internal room ID' (starts with !)"
echo ""
read -p "Matrix room ID (e.g., !abc123:matrix.oculair.ca): " ROOM_ID

if [ -z "$ROOM_ID" ]; then
    echo "❌ Room ID is required!"
    exit 1
fi

echo ""
read -p "Bridge bot user ID (e.g., @bridge:matrix.oculair.ca): " USER_ID

if [ -z "$USER_ID" ]; then
    echo "❌ User ID is required!"
    exit 1
fi

echo ""
echo "📝 P2P Configuration"
echo ""

read -p "P2P room name [agent-swarm-global]: " P2P_ROOM
P2P_ROOM=${P2P_ROOM:-agent-swarm-global}

read -p "P2P identity [matrix-bridge]: " P2P_IDENTITY
P2P_IDENTITY=${P2P_IDENTITY:-matrix-bridge}

echo ""
read -p "P2P ticket (optional, leave empty to create new room): " P2P_TICKET

echo ""
echo "💾 Creating configuration file..."

cat > "$CONFIG_FILE" <<EOF
{
  "matrix": {
    "homeserver": "$HOMESERVER",
    "accessToken": "$ACCESS_TOKEN",
    "roomId": "$ROOM_ID",
    "userId": "$USER_ID"
  },
  "p2p": {
    "room": "$P2P_ROOM",
    "identity": "$P2P_IDENTITY",
    "ticket": $([ -z "$P2P_TICKET" ] && echo "null" || echo "\"$P2P_TICKET\"")
  },
  "bridge": {
    "autoRegisterUsers": true,
    "logMessages": true,
    "persistIdentities": false
  }
}
EOF

echo "✅ Configuration saved to: $CONFIG_FILE"
echo ""
echo "🔍 Configuration summary:"
echo "  Homeserver: $HOMESERVER"
echo "  Room: $ROOM_ID"
echo "  Bot: $USER_ID"
echo "  P2P room: $P2P_ROOM"
echo "  P2P identity: $P2P_IDENTITY"
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Test the bridge: cd $BRIDGE_DIR && bun run index.ts"
echo "  2. Deploy as service: cd $BRIDGE_DIR && ./deploy.sh"
echo ""
