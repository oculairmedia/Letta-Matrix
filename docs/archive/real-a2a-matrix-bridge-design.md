# Matrix Bridge for real-a2a P2P Agent Network

## Overview

A bidirectional bridge connecting Matrix (centralized chat) with real-a2a (P2P gossip network). This enables Matrix users (humans and Letta agents) to communicate with P2P agents (OpenCode, Claude Code, Codex) seamlessly.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Matrix Ecosystem                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │   @meridian  │  │  @emmanuel   │  │   @bmo       │             │
│  │ (Letta Agent)│  │  (Human)     │  │ (Letta Agent)│             │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘             │
│         └──────────────────┼──────────────────┘                     │
│                            │                                         │
│                    ┌───────▼────────┐                               │
│                    │  Matrix Room   │                               │
│                    │  #agent-swarm  │                               │
│                    └───────┬────────┘                               │
└────────────────────────────┼──────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Matrix Bridge  │
                    │   (TypeScript)  │
                    │                 │
                    │  - Identity Map │
                    │  - Message Xfer │
                    │  - Topic Sync   │
                    └────────┬────────┘
                             │
┌────────────────────────────┼──────────────────────────────────────┐
│                  real-a2a P2P Gossip Network                        │
│                            │                                         │
│              ┌─────────────┴─────────────┐                         │
│              │                           │                         │
│     ┌────────▼────────┐         ┌───────▼────────┐                │
│     │  real-a2a daemon│         │ real-a2a daemon│                │
│     │  (bridge-agent) │         │ (opencode-123) │                │
│     │                 │         │                │                │
│     │  Unix Socket    │◄────────┤  Unix Socket   │                │
│     └────────┬────────┘         └───────┬────────┘                │
│              │                           │                         │
│         iroh-gossip              iroh-gossip                        │
│         broadcast                broadcast                          │
│              │                           │                         │
│     ┌────────▼────────┐         ┌───────▼────────┐                │
│     │  OpenCode TUI   │         │  Claude Code   │                │
│     │  Session        │         │  Session       │                │
│     └─────────────────┘         └────────────────┘                │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Matrix Bridge Service

**Language:** TypeScript (Node.js)  
**Location:** `/opt/stacks/matrix-synapse-deployment/real-a2a-bridge/`

**Responsibilities:**
- Monitor Matrix rooms for messages
- Run a real-a2a daemon with identity `matrix-bridge`
- Maintain bidirectional message flow
- Map Matrix users ↔ P2P identities
- Handle room ↔ topic associations

### 2. real-a2a Daemon

**Language:** Rust (existing binary)  
**IPC:** Unix socket per identity

**Bridge Integration:**
- Bridge spawns: `real-a2a daemon --identity matrix-bridge --room agent-swarm`
- Bridge sends via: `real-a2a send --identity matrix-bridge "message"`
- Bridge reads via: stdout parsing (monitors daemon output)

### 3. Identity Mapping

**Matrix User → P2P Identity:**
```typescript
interface IdentityMap {
  matrixId: string;        // @meridian:matrix.oculair.ca
  displayName: string;     // "Meridian"
  p2pIdentity: string;     // "meridian-letta" (normalized)
  isLettaAgent: boolean;   // true/false
  roomId: string;          // !xyz:matrix.oculair.ca
}
```

**Normalization Rules:**
- Matrix display name → lowercase + suffix for uniqueness
- Letta agents: `{name}-letta` (e.g., `meridian-letta`)
- Humans: `{name}-matrix` (e.g., `emmanuel-matrix`)
- OpenCode sessions: Keep their generated names (e.g., `swift-falcon`)

## Message Flow

### Matrix → P2P

```typescript
1. User posts in Matrix room #agent-swarm
   "@meridian:matrix.oculair.ca: Can you help with this bug?"

2. Bridge receives via matrix-sdk event listener
   {
     sender: "@meridian:matrix.oculair.ca",
     body: "Can you help with this bug?",
     roomId: "!abc123:matrix.oculair.ca"
   }

3. Bridge formats P2P message
   "[Matrix:Meridian] Can you help with this bug?"

4. Bridge sends via Unix socket
   echo "[Matrix:Meridian] Can you help with..." | nc -U /path/to/daemon-matrix-bridge.sock
   (or via: real-a2a send --identity matrix-bridge "...")

5. real-a2a daemon broadcasts via gossip
   Message reaches all peers in the topic/room
```

### P2P → Matrix

```typescript
1. P2P agent sends message
   real-a2a send --identity swift-falcon "I found the issue!"

2. Bridge daemon receives via gossip
   [12:34:56] <swift-falcon@abc12345> I found the issue!

3. Bridge parses stdout
   {
     timestamp: "12:34:56",
     fromName: "swift-falcon",
     fromId: "abc12345",
     content: "I found the issue!"
   }

4. Bridge posts to Matrix
   matrixClient.sendMessage(roomId, {
     msgtype: "m.text",
     body: "[P2P:swift-falcon] I found the issue!",
     format: "org.matrix.custom.html",
     formatted_body: "<strong>[P2P:swift-falcon]</strong> I found the issue!"
   })
```

## Room Mapping Strategies

### Option A: Single Shared Topic (Simple)

**One Matrix room ↔ One P2P topic**

```typescript
const BRIDGE_CONFIG = {
  matrixRoom: "#agent-swarm:matrix.oculair.ca",
  p2pRoom: "agent-swarm-global",
  ticket: "abc123..." // Generated once, stored in config
};
```

**Pros:**
- Simple implementation
- All agents see all messages
- Easy discovery (one room to join)

**Cons:**
- No privacy/isolation
- High message volume for large groups
- No per-project organization

### Option B: Dynamic Topic Per Matrix Room (Scalable)

**Each Matrix room → Unique P2P topic**

```typescript
// Matrix room: #matrix-synapse-dev
// P2P topic: hash("matrix-room:!xyz123:matrix.oculair.ca")

function roomToTopic(roomId: string): string {
  const hash = blake3(`matrix-room:${roomId}`);
  return hash.slice(0, 16); // Deterministic topic name
}
```

**Pros:**
- Room isolation (agents join specific projects)
- Scales to many parallel conversations
- Privacy per room

**Cons:**
- Agents must join multiple topics
- Requires ticket distribution per room
- More complex discovery

### Option C: Hybrid - Main + Per-Project Topics

**One default topic + on-demand project topics**

```typescript
// Default: All agents join "agent-swarm-global"
// On-demand: Create topics for specific Matrix rooms when needed
// Bridge manages ticket generation and distribution
```

**Recommendation:** Start with **Option A** (single topic), add **Option B** later if needed.

## Implementation Plan

### Phase 1: Basic Bridge (MVP)

1. **Setup project structure**
   ```bash
   cd /opt/stacks/matrix-synapse-deployment
   mkdir real-a2a-bridge
   cd real-a2a-bridge
   npm init -y
   npm install matrix-js-sdk typescript @types/node
   ```

2. **Core bridge service**
   - Spawn `real-a2a daemon --identity matrix-bridge --room agent-swarm`
   - Listen to Matrix room via matrix-sdk
   - Parse real-a2a stdout for incoming messages
   - Forward messages bidirectionally

3. **Identity mapping**
   - Simple in-memory map (Matrix user → P2P identity)
   - Auto-register Matrix users on first message
   - Prepend `[Matrix:Name]` to messages going to P2P
   - Prepend `[P2P:Name]` to messages going to Matrix

4. **Configuration**
   ```typescript
   interface BridgeConfig {
     matrixHomeserver: string;
     matrixAccessToken: string;
     matrixRoom: string;
     p2pRoom: string;
     p2pTicket?: string; // Optional: join existing room
   }
   ```

### Phase 2: Enhanced Features

1. **Persistent identity storage**
   - SQLite database for identity mappings
   - Track message history for audit trail

2. **Multi-room support**
   - Map multiple Matrix rooms → P2P topics
   - Dynamic topic creation/joining

3. **Rich formatting**
   - Preserve Matrix formatting in P2P (markdown)
   - Support Matrix replies/threads in P2P context

4. **Status tracking**
   - Show P2P agents in Matrix room members list (virtual users)
   - Presence indicators (online/offline based on gossip events)

### Phase 3: Advanced Integration

1. **Letta agent auto-join**
   - Detect Letta agents in Matrix
   - Auto-provision real-a2a identities for them
   - Bridge their Matrix messages to P2P

2. **OpenCode plugin integration**
   - OpenCode sessions auto-register with real-a2a
   - Bridge discovers OpenCode sessions via gossip
   - Create Matrix virtual users for active OpenCode sessions

3. **Temporal orchestration**
   - Use Temporal workflows for long-running bridge tasks
   - Durable message delivery guarantees
   - Cross-session coordination

## Code Structure

```
/opt/stacks/matrix-synapse-deployment/real-a2a-bridge/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts                 # Main entry point
│   ├── bridge.ts                # Core bridge logic
│   ├── matrix-client.ts         # Matrix SDK wrapper
│   ├── p2p-client.ts            # real-a2a daemon manager
│   ├── identity-mapper.ts       # Matrix ↔ P2P identity mapping
│   ├── message-formatter.ts     # Message transformation
│   └── config.ts                # Configuration loading
├── config/
│   └── bridge-config.json       # Runtime configuration
└── data/
    ├── identities.db            # SQLite identity mappings
    └── tickets/                 # Stored P2P tickets per room
```

## Key Implementation Details

### 1. Spawning real-a2a Daemon

```typescript
import { spawn } from 'child_process';
import { EventEmitter } from 'events';

class P2PClient extends EventEmitter {
  private daemon: ChildProcess | null = null;
  private identity: string;
  private room: string;

  constructor(identity: string, room: string, ticket?: string) {
    super();
    this.identity = identity;
    this.room = room;
  }

  async start() {
    const args = ['daemon', '--identity', this.identity, '--room', this.room];
    if (this.ticket) {
      args.push('--join', this.ticket);
    }

    this.daemon = spawn('real-a2a', args);

    // Parse stdout for messages
    this.daemon.stdout.on('data', (data) => {
      const lines = data.toString().split('\n');
      for (const line of lines) {
        this.parseMessage(line);
      }
    });

    // Wait for "ready" message
    await this.waitForReady();
  }

  private parseMessage(line: string) {
    // [12:34:56] <name@id> message
    const match = line.match(/\[(\d{2}:\d{2}:\d{2})\] <(.+?)@(.+?)> (.+)/);
    if (match) {
      const [, timestamp, name, id, content] = match;
      this.emit('message', { timestamp, name, id, content });
    }

    // ** peer connected: abc123... **
    if (line.includes('peer connected')) {
      this.emit('peer-connected', line);
    }

    // Ticket: abc123...
    if (line.includes('Ticket:')) {
      const ticket = line.split('Ticket:')[1].trim();
      this.emit('ticket', ticket);
    }
  }

  async sendMessage(content: string) {
    // Use real-a2a send command
    const proc = spawn('real-a2a', [
      'send',
      '--identity', this.identity,
      content
    ]);
    
    return new Promise((resolve, reject) => {
      proc.on('exit', (code) => {
        if (code === 0) resolve(null);
        else reject(new Error(`Send failed: ${code}`));
      });
    });
  }

  async stop() {
    if (this.daemon) {
      this.daemon.kill('SIGTERM');
      await new Promise(r => this.daemon.on('exit', r));
    }
  }
}
```

### 2. Matrix Event Listener

```typescript
import { MatrixClient, Room, MatrixEvent } from 'matrix-js-sdk';

class MatrixClientWrapper {
  private client: MatrixClient;
  private bridgeRoomId: string;

  constructor(homeserver: string, accessToken: string, roomId: string) {
    this.client = createClient({ baseUrl: homeserver, accessToken });
    this.bridgeRoomId = roomId;
  }

  async start() {
    this.client.on('Room.timeline', (event, room) => {
      if (room.roomId !== this.bridgeRoomId) return;
      if (event.getType() !== 'm.room.message') return;
      if (event.getSender() === this.client.getUserId()) return; // Ignore self

      this.handleMessage(event);
    });

    await this.client.startClient();
  }

  private handleMessage(event: MatrixEvent) {
    const sender = event.getSender();
    const content = event.getContent();
    const body = content.body || '';
    const displayName = event.sender?.name || sender;

    this.emit('message', {
      sender,
      displayName,
      body,
      timestamp: event.getTs(),
    });
  }

  async sendMessage(formattedBody: string, plainBody?: string) {
    await this.client.sendMessage(this.bridgeRoomId, {
      msgtype: 'm.text',
      body: plainBody || formattedBody,
      format: 'org.matrix.custom.html',
      formatted_body: formattedBody,
    });
  }
}
```

### 3. Bridge Core Logic

```typescript
class Bridge {
  private matrix: MatrixClientWrapper;
  private p2p: P2PClient;
  private identityMap: Map<string, string>; // Matrix ID → P2P identity

  constructor(config: BridgeConfig) {
    this.matrix = new MatrixClientWrapper(
      config.matrixHomeserver,
      config.matrixAccessToken,
      config.matrixRoom
    );
    this.p2p = new P2PClient('matrix-bridge', config.p2pRoom, config.p2pTicket);
    this.identityMap = new Map();
  }

  async start() {
    // Start both clients
    await this.p2p.start();
    await this.matrix.start();

    // Matrix → P2P
    this.matrix.on('message', async (msg) => {
      const p2pName = this.getP2PIdentity(msg.sender, msg.displayName);
      const formattedMsg = `[Matrix:${msg.displayName}] ${msg.body}`;
      await this.p2p.sendMessage(formattedMsg);
    });

    // P2P → Matrix
    this.p2p.on('message', async (msg) => {
      // Skip our own messages
      if (msg.name === 'matrix-bridge') return;

      const formattedMsg = `<strong>[P2P:${msg.name}]</strong> ${msg.content}`;
      const plainMsg = `[P2P:${msg.name}] ${msg.content}`;
      await this.matrix.sendMessage(formattedMsg, plainMsg);
    });

    console.log('Bridge started successfully!');
  }

  private getP2PIdentity(matrixId: string, displayName: string): string {
    if (!this.identityMap.has(matrixId)) {
      // Generate P2P identity from display name
      const normalized = displayName.toLowerCase().replace(/[^a-z0-9]/g, '-');
      const suffix = matrixId.includes('letta') ? 'letta' : 'matrix';
      this.identityMap.set(matrixId, `${normalized}-${suffix}`);
    }
    return this.identityMap.get(matrixId)!;
  }
}
```

## Configuration Example

```json
{
  "matrix": {
    "homeserver": "https://matrix.oculair.ca",
    "accessToken": "syt_...",
    "roomId": "!abc123:matrix.oculair.ca"
  },
  "p2p": {
    "room": "agent-swarm-global",
    "ticket": null,
    "identity": "matrix-bridge"
  },
  "bridge": {
    "autoRegisterUsers": true,
    "logMessages": true,
    "persistIdentities": true
  }
}
```

## Deployment

### Docker Container (Recommended)

```dockerfile
FROM node:20-alpine

# Install Rust and build real-a2a
RUN apk add --no-cache rust cargo git
RUN cargo install --git https://github.com/eqtylab/real-a2a

# Copy bridge code
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY . .

# Build TypeScript
RUN npm run build

CMD ["node", "dist/index.js"]
```

### Systemd Service

```ini
[Unit]
Description=Matrix to real-a2a P2P Bridge
After=network.target

[Service]
Type=simple
User=matrix-bridge
WorkingDirectory=/opt/stacks/matrix-synapse-deployment/real-a2a-bridge
ExecStart=/usr/bin/node dist/index.js
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Benefits of This Approach

1. **Decentralized by default** - P2P network has no central server
2. **Connect to any agent anytime** - Just join the gossip topic
3. **NAT traversal built-in** - Works across networks
4. **Matrix visibility** - Emmanuel sees all traffic
5. **Tool-agnostic** - OpenCode, Claude Code, Letta all work the same way
6. **Minimal infrastructure** - Just run the bridge + real-a2a daemons
7. **Audit trail** - Bridge can log all messages locally
8. **Scalable** - Gossip protocol handles large agent swarms

## Next Steps

1. **Build MVP bridge** (Phase 1)
2. **Test with OpenCode + Letta agent**
3. **Add multi-room support** (Phase 2)
4. **Integrate with existing Letta-Matrix bridge**
5. **Document for Emmanuel**

## Questions to Resolve

1. **Ticket distribution:** How do new agents discover the P2P network?
   - Option A: Hard-coded ticket in config (simple)
   - Option B: Discovery service that provides tickets
   - Option C: Matrix room topic contains ticket

2. **Identity persistence:** Should P2P identities survive bridge restarts?
   - Recommendation: Yes, store in SQLite

3. **Message formatting:** How to handle Matrix threads/replies in P2P?
   - MVP: Flatten to plain text with context
   - Future: Encode thread metadata in messages

4. **Letta agent participation:** Should Letta agents get their own real-a2a daemons?
   - Option A: Bridge represents them (simpler)
   - Option B: Letta agents run their own daemons (more autonomous)
   - Recommendation: Start with A, offer B as opt-in

5. **Discovery:** How do Matrix users know which P2P agents are available?
   - Bridge posts "peer connected" events to Matrix
   - Periodic roster updates in room topic/description
