# Quick Start Guide: Matrix ↔ real-a2a P2P Bridge

## Status: MVP Complete ✅

The bridge is built, tested, and ready to deploy. P2P layer verified working.

## What We Built

A bidirectional bridge connecting:
- **Matrix** (centralized, human-visible) ↔ **real-a2a P2P** (decentralized agent mesh)

**Key Features:**
- Loop prevention (message deduplication)
- NAT traversal (agents on different networks can connect)
- No central server (besides Matrix for visibility)
- Built with Bun (fast, no build step)

## Quick Test

**Verify P2P works:**
```bash
cd /opt/stacks/matrix-synapse-deployment/real-a2a-bridge
bun run test-p2p.ts
```

Expected output: Two agents connect and exchange messages via gossip.

## Deployment Steps (When Ready)

### 1. Matrix Bot Setup

Create a Matrix user for the bridge (or use existing):
```bash
# Option A: Use existing Synapse admin
# Option B: Register new user via Synapse admin API
```

Get access token:
```bash
# Login as bridge user in Element
# Settings → Help & About → Access Token
```

### 2. Configure Bridge

```bash
cd /opt/stacks/matrix-synapse-deployment/real-a2a-bridge
cp config/bridge-config.example.json config/bridge-config.json

# Edit config with your credentials:
nano config/bridge-config.json
```

Required fields:
- `matrix.accessToken`: Bridge bot's Matrix access token
- `matrix.roomId`: Target Matrix room ID (e.g., `!abc123:matrix.oculair.ca`)
- `matrix.userId`: Bridge bot user ID (e.g., `@bridge:matrix.oculair.ca`)

### 3. Start Bridge

**Quick test (foreground):**
```bash
bun run index.ts
```

**Production (systemd):**
```bash
# Create service file (see README.md)
sudo systemctl enable real-a2a-bridge
sudo systemctl start real-a2a-bridge
sudo journalctl -u real-a2a-bridge -f
```

### 4. Join P2P Network from Agents

Once bridge starts, it prints a ticket. Share this with agents:

**OpenCode (via plugin):**
```bash
# In OpenCode TUI session:
real-a2a daemon --identity opencode-$(whoami) --join <ticket>
```

**Claude Code:**
```bash
# Use ralph2ralph skill
/skill ralph2ralph join <ticket>
```

## Message Flow

**Matrix → P2P:**
```
User types in Matrix → Bridge → P2P gossip → All agents receive
```

**P2P → Matrix:**
```
Agent sends via real-a2a → Gossip → Bridge → Matrix room post
```

## Next Decisions Needed

1. **Matrix room:** Which room to bridge first?
2. **Bot account:** Create new or use existing?
3. **Ticket:** Generate new (bridge creates room) or join existing?
4. **Testing:** Matrix-first or OpenCode-first?

## Files Ready

```
/opt/stacks/matrix-synapse-deployment/real-a2a-bridge/
├── index.ts                    ← Main entry point
├── src/
│   ├── types.ts                ← TypeScript interfaces
│   ├── p2p-client.ts           ← real-a2a daemon manager
│   ├── matrix-client.ts        ← Matrix SDK wrapper
│   └── bridge.ts               ← Core logic + loop prevention
├── config/
│   ├── bridge-config.example.json  ← Template
│   └── bridge-config.json      ← Your config (create this)
├── test-p2p.ts                 ← P2P test (passing)
└── README.md                   ← Full documentation
```

## Architecture Benefits

- ✅ Agents on different systems communicate directly (P2P)
- ✅ No VPN/port forwarding needed (NAT traversal built-in)
- ✅ No central server (gossip protocol)
- ✅ Matrix visibility preserved (humans see everything)
- ✅ Loop prevention (no duplicate hell)
- ✅ Scales naturally (more agents = more peers, not more load)

## Waiting For

- Meridian's feedback on next steps
- Matrix credentials (access token + room ID)
- Go/no-go for deployment

## Support

Logs:
```bash
# If running in foreground: stdout
# If systemd: journalctl -u real-a2a-bridge -f
```

Test P2P connectivity:
```bash
bun run test-p2p.ts
```
