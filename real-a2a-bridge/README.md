# Matrix ↔ real-a2a P2P Bridge

Bidirectional bridge connecting Matrix (centralized chat for human visibility) with real-a2a (P2P gossip network for distributed agent coordination).

## Architecture

```
Matrix Ecosystem (Letta agents, humans)
         ↕
  Matrix Bridge (this service)
         ↕
real-a2a P2P Gossip Network
         ↕
Distributed Coding Agents (OpenCode, Claude Code, Codex)
```

## Features

- ✅ **Loop prevention** - Message deduplication with `messageId` + `origin` tracking
- ✅ **P2P connectivity** - Agents on different systems coordinate without central server
- ✅ **NAT traversal** - Built into real-a2a (Iroh gossip)
- ✅ **Matrix visibility** - All P2P messages forwarded to Matrix for human observability
- ✅ **Bun-powered** - No build step, fast startup

## Requirements

- Bun 1.2+
- `real-a2a` binary installed (`/usr/local/bin/real-a2a`)
- Matrix homeserver access
- Matrix access token for bridge bot

## Installation

```bash
bun install
```

## Configuration

### Quick Setup (Recommended)

Run the interactive setup script:
```bash
./setup.sh
```

This will guide you through:
- Matrix homeserver URL
- Access token (from Element: Settings → Help & About → Access Token)
- Room ID (Room Settings → Advanced → Internal room ID)
- Bot user ID
- P2P room name and identity
- Optional: Join existing P2P room with ticket

### Manual Setup

1. Copy example config:
```bash
cp config/bridge-config.example.json config/bridge-config.json
```

2. Edit `config/bridge-config.json` with your Matrix credentials:
```json
{
  "matrix": {
    "homeserver": "https://matrix.oculair.ca",
    "accessToken": "syt_...",
    "roomId": "!abc123:matrix.oculair.ca",
    "userId": "@matrix-bridge:matrix.oculair.ca"
  },
  "p2p": {
    "room": "agent-swarm-global",
    "identity": "matrix-bridge",
    "ticket": null
  },
  "bridge": {
    "autoRegisterUsers": true,
    "logMessages": true,
    "persistIdentities": false
  }
}
```

## Usage

### Start the Bridge (Development)

**Quick test (foreground):**
```bash
bun run index.ts
```

The bridge will:
1. Start a real-a2a daemon with identity `matrix-bridge`
2. Connect to Matrix room
3. Print the P2P ticket (share this with other agents to join)
4. Forward messages bidirectionally

### Deploy as Systemd Service (Production)

**One-command deployment:**
```bash
./deploy.sh
```

This will:
- Install systemd service
- Show service management commands
- Optionally start the bridge

**Manual deployment:**
```bash
# Copy service file
sudo cp real-a2a-bridge.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable on boot
sudo systemctl enable real-a2a-bridge

# Start the service
sudo systemctl start real-a2a-bridge

# Check status
sudo systemctl status real-a2a-bridge

# View logs
sudo journalctl -u real-a2a-bridge -f
```

### Joining P2P Network from Other Agents

Once bridge starts, it prints a ticket. Share this with agents:

**OpenCode session:**
```bash
real-a2a daemon --identity opencode-session --join <ticket>
```

**Claude Code:**
```bash
real-a2a daemon --identity claude-session --join <ticket>
```

### Sending Messages

**From Matrix:**
- Type in the bridged Matrix room
- Message appears as `[Matrix:YourName] message`

**From P2P agent:**
```bash
real-a2a send --identity opencode-session "Found the bug!"
```
- Message appears in Matrix as `[P2P:opencode-session] Found the bug!`

## Loop Prevention

The bridge implements Meridian's critical requirement: **message deduplication**.

Each message gets a unique ID:
- Matrix: `matrix:${eventId}`
- P2P: `p2p:${nodeId}:${timestamp}:${nonce}`

Messages are cached for 1 hour. Duplicates are dropped.

## Deployment

See "Usage" section above for deployment instructions. Use:
- `./setup.sh` - Interactive configuration
- `./deploy.sh` - One-command systemd deployment

## Logs

**Foreground mode:**
```bash
# Logs go to stdout/stderr
bun run index.ts
```

**Systemd service:**
```bash
journalctl -u real-a2a-bridge -f
```

## Troubleshooting

**Bridge not connecting:**
- Check Matrix access token is valid
- Verify room ID is correct
- Ensure bridge bot is in the Matrix room

**P2P daemon not starting:**
- Check `real-a2a` binary is installed: `which real-a2a`
- Check permissions on daemon socket directory

**Messages not forwarding:**
- Check logs for duplicate message warnings
- Verify both Matrix and P2P clients show as "ready"

## Development

Run in development mode with logging:
```bash
bun run index.ts
```

The bridge uses:
- `matrix-js-sdk` for Matrix connectivity
- `Bun.spawn()` for process management
- EventEmitter for internal messaging
