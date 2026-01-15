#!/bin/bash
set -e

echo "=== Matrix ↔ real-a2a Bridge Deployment ==="
echo ""

BRIDGE_DIR="/opt/stacks/matrix-synapse-deployment/real-a2a-bridge"
SERVICE_FILE="$BRIDGE_DIR/real-a2a-bridge.service"
SYSTEMD_DIR="/etc/systemd/system"
CONFIG_FILE="$BRIDGE_DIR/config/bridge-config.json"
EXAMPLE_CONFIG="$BRIDGE_DIR/config/bridge-config.example.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Configuration file missing: $CONFIG_FILE"
    echo ""
    echo "Please create it from the example:"
    echo "  cp $EXAMPLE_CONFIG $CONFIG_FILE"
    echo "  nano $CONFIG_FILE"
    echo ""
    echo "Required fields:"
    echo "  - matrix.accessToken: Bridge bot's Matrix access token"
    echo "  - matrix.roomId: Target Matrix room ID"
    echo "  - matrix.userId: Bridge bot user ID"
    echo ""
    exit 1
fi

echo "✅ Configuration file found"

echo "📋 Installing systemd service..."
cp "$SERVICE_FILE" "$SYSTEMD_DIR/real-a2a-bridge.service"
systemctl daemon-reload
echo "✅ Service installed"

echo ""
echo "🔧 Service commands:"
echo "  Start:   systemctl start real-a2a-bridge"
echo "  Stop:    systemctl stop real-a2a-bridge"
echo "  Status:  systemctl status real-a2a-bridge"
echo "  Logs:    journalctl -u real-a2a-bridge -f"
echo "  Enable:  systemctl enable real-a2a-bridge"
echo ""

read -p "Start the bridge now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🚀 Starting bridge..."
    systemctl start real-a2a-bridge
    sleep 2
    systemctl status real-a2a-bridge --no-pager
    echo ""
    echo "✅ Bridge started!"
    echo ""
    echo "📊 View logs:"
    echo "  journalctl -u real-a2a-bridge -f"
else
    echo "ℹ️  Bridge not started. Start manually with:"
    echo "  systemctl start real-a2a-bridge"
fi

echo ""
echo "=== Deployment Complete ==="
