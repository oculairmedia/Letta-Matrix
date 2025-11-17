# Letta-Matrix Architecture Overview

**Status**: 🟢 Current
**Last Updated**: 2025-11-17
**Owner**: Architecture Team

---

## Table of Contents

- [System Overview](#system-overview)
- [High-Level Architecture](#high-level-architecture)
- [Core Components](#core-components)
- [Data Flow](#data-flow)
- [Key Design Decisions](#key-design-decisions)
- [Related Documentation](#related-documentation)

---

## System Overview

Letta-Matrix is a comprehensive Matrix homeserver deployment that integrates Letta AI agents with Matrix's decentralized communication protocol. The system enables:

- **Multi-Agent Communication**: Each Letta agent gets its own Matrix identity and room
- **Inter-Agent Messaging**: Agents can communicate with each other through Matrix
- **Human-Agent Interaction**: Users interact with agents through standard Matrix clients (Element, etc.)
- **MCP Integration**: Model Context Protocol servers provide tools for agent-Matrix integration

### Key Features

- ✅ Automatic agent discovery and synchronization
- ✅ Dedicated Matrix user and room per agent
- ✅ Matrix Spaces for organizing agent rooms
- ✅ Conversation history import from Letta to Matrix
- ✅ Inter-agent messaging with correct sender identity
- ✅ RESTful API for Matrix operations
- ✅ MCP servers for Claude Code and Letta integration

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Letta Backend                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Agent A  │  │ Agent B  │  │ Agent C  │  │  Agent N │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
└───────┼─────────────┼─────────────┼─────────────┼──────────────┘
        │             │             │             │
        └─────────────┴─────────────┴─────────────┘
                      │
        ┌─────────────┴─────────────┐
        │                           │
┌───────▼────────┐         ┌────────▼─────────┐
│  Agent Manager │         │  MCP Servers     │
│   (Python)     │         │   - Matrix MCP   │
│                │         │   - Agent MCP    │
└───────┬────────┘         └────────┬─────────┘
        │                           │
        └─────────────┬─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │      Matrix API Layer     │
        │       (FastAPI)           │
        └─────────────┬─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │   Matrix Homeserver       │
        │   (Tuwunel/Synapse)       │
        │   - RocksDB/PostgreSQL    │
        └─────────────┬─────────────┘
                      │
        ┌─────────────▼─────────────┐
        │   Matrix Clients          │
        │   - Element Web           │
        │   - Mobile Apps           │
        │   - Agent Users           │
        └───────────────────────────┘
```

---

## Core Components

### 1. Matrix Homeserver

**Technology**: Tuwunel (Rust) or Synapse (Python)
**Responsibility**: Matrix protocol implementation

- User authentication and authorization
- Room management and federation
- Message routing and delivery
- State synchronization

See: [TUWUNEL_MIGRATION.md](TUWUNEL_MIGRATION.md)

### 2. Agent User Manager

**Technology**: Python (AsyncIO)
**Responsibility**: Bridge between Letta and Matrix

Key modules:
- `AgentUserManager`: Orchestration and workflow management
- `MatrixSpaceManager`: Space creation and management
- `MatrixUserManager`: User account operations
- `MatrixRoomManager`: Room creation and configuration

**Operations**:
- Discovers Letta agents (polling or webhook)
- Creates Matrix user per agent
- Creates dedicated room per agent
- Organizes rooms into "Letta Agents" space
- Imports conversation history
- Manages invitations and room membership

See: [AGENT_MANAGEMENT.md](AGENT_MANAGEMENT.md)

### 3. Matrix API Service

**Technology**: FastAPI (Python)
**Responsibility**: RESTful API wrapper around Matrix

**Endpoints**:
- `/send_message` - Send messages to Matrix rooms
- `/create_room` - Create new Matrix rooms
- `/list_rooms` - List user's rooms
- `/get_messages` - Retrieve room messages
- `/health` - Health check

See: [MATRIX_INTEGRATION.md](MATRIX_INTEGRATION.md)

### 4. MCP Servers

**Technology**: Python (MCP Protocol)
**Responsibility**: Provide tools for AI agents

**Matrix MCP Server** (`mcp-server`):
- `send_matrix_message` - Send messages to rooms
- `list_matrix_rooms` - List available rooms
- `get_room_messages` - Get message history
- `create_matrix_room` - Create new rooms

**Letta Agent MCP Server** (`letta-agent-mcp`):
- `send_message_to_agent` - Inter-agent messaging
- `list_agents` - Discover available agents
- `get_agent_room` - Find agent's Matrix room

See: [MCP_SERVERS.md](MCP_SERVERS.md)

### 5. Custom Matrix Client

**Technology**: Python (matrix-nio)
**Responsibility**: Matrix client operations

**Functions**:
- Event stream monitoring
- Message handling and routing
- Auto-accept invitations
- Event deduplication
- Room state management

### 6. Element Web Client

**Technology**: React (JavaScript)
**Responsibility**: User interface

- Standard Matrix web interface
- Room browsing and messaging
- Space navigation
- User settings

---

## Data Flow

### Agent Discovery & Room Creation

```
1. Letta Backend
   └─> New agent created

2. Agent Manager (polling/webhook)
   └─> Discovers new agent
   └─> Creates Matrix user (@agent_<id>:domain)
   └─> Creates private room
   └─> Invites admin and @letta user
   └─> Adds room to "Letta Agents" space
   └─> Imports conversation history

3. Matrix Homeserver
   └─> Stores room state
   └─> Delivers invitations

4. Element Client
   └─> Shows new room in "Letta Agents" space
```

### User → Agent Message Flow

```
1. User (Element)
   └─> Sends message in agent's room

2. Matrix Homeserver
   └─> Receives message event
   └─> Stores in room timeline
   └─> Syncs to connected clients

3. Custom Matrix Client
   └─> Receives message via /sync
   └─> Deduplicates event
   └─> Filters out own messages
   └─> Forwards to Letta backend

4. Letta Agent
   └─> Processes message
   └─> Generates response
   └─> Sends via Matrix API

5. Matrix Homeserver
   └─> Delivers response to room

6. User (Element)
   └─> Sees agent's response
```

### Inter-Agent Message Flow

```
1. Agent A (Letta)
   └─> Calls send_message_to_agent(agent_b_id, message)

2. Letta Agent MCP
   └─> Looks up Agent B's room
   └─> Authenticates as Agent A user
   └─> Sends message with Agent A's identity

3. Matrix Homeserver
   └─> Delivers message to Agent B's room
   └─> Shows sender as @agent_a:domain

4. Custom Matrix Client
   └─> Receives message for Agent B
   └─> Allows inter-agent messages (not filtered)
   └─> Forwards to Letta backend

5. Agent B (Letta)
   └─> Receives message from Agent A
   └─> Processes and responds
```

See: [INTER_AGENT_MESSAGING.md](INTER_AGENT_MESSAGING.md)

---

## Key Design Decisions

### 1. One User Per Agent

**Decision**: Each Letta agent gets its own Matrix user account
**Rationale**:
- Clear identity in conversations
- Independent authentication
- Proper sender attribution
- Supports inter-agent messaging

**Alternative Rejected**: All agents share @letta account
- Would lose sender identity
- Complex message routing
- No inter-agent communication

### 2. One Room Per Agent

**Decision**: Each agent has a dedicated private room
**Rationale**:
- Isolated conversations
- Clear ownership
- Easy permission management
- Scalable architecture

**Alternative Rejected**: Multi-agent shared rooms
- Conversation cross-contamination
- Complex routing logic
- Unclear ownership

### 3. Matrix Spaces for Organization

**Decision**: All agent rooms belong to "Letta Agents" space
**Rationale**:
- Better UX in Matrix clients
- Hierarchical organization
- Easy discovery
- Professional appearance

### 4. History Import

**Decision**: Import recent Letta conversation history to Matrix
**Rationale**:
- Context preservation
- Seamless transition
- Better user experience
- Conversation continuity

**Implementation**: Limited to recent messages to avoid overwhelming rooms

### 5. Tuwunel Migration

**Decision**: Migrate from Synapse (Python) to Tuwunel (Rust)
**Rationale**:
- Better performance (10x+ faster)
- Lower resource usage
- Embedded database (no PostgreSQL needed)
- Simpler deployment

See: [TUWUNEL_MIGRATION.md](TUWUNEL_MIGRATION.md)

### 6. MCP Protocol Integration

**Decision**: Expose Matrix operations via MCP servers
**Rationale**:
- Standard protocol for AI tool access
- Clean separation of concerns
- Reusable across AI systems
- Future-proof architecture

See: [MCP_SERVERS.md](MCP_SERVERS.md)

---

## Technology Stack

### Backend
- **Python 3.11+**: Primary language
- **AsyncIO**: Async/await for concurrency
- **matrix-nio**: Matrix client library
- **FastAPI**: REST API framework
- **Pydantic**: Data validation

### Matrix Homeserver
- **Tuwunel** (recommended): Rust-based, RocksDB storage
- **Synapse** (legacy): Python-based, PostgreSQL storage

### Frontend
- **Element Web**: React-based Matrix client

### Infrastructure
- **Docker**: Containerization
- **Docker Compose**: Multi-container orchestration
- **Nginx**: Reverse proxy and SSL termination

### AI Integration
- **Letta**: AI agent platform
- **MCP**: Model Context Protocol for tool access

---

## Security Model

### Authentication

- **Admin User**: Full server control, agent management
- **Letta User**: Read-only bot user for monitoring
- **Agent Users**: One per agent, room-specific permissions
- **Regular Users**: Standard Matrix authentication

### Authorization

- **Private Rooms**: Only invited users can join
- **Space Membership**: Controlled by admin
- **API Access**: Token-based authentication
- **Admin Operations**: Restricted to admin token

### Network Security

- **HTTPS/TLS**: All client connections encrypted
- **Federation**: Optional, can be disabled
- **Internal Network**: Services communicate via Docker network
- **Rate Limiting**: Configured in homeserver

---

## Scalability Considerations

### Current Scale
- **Agents**: Tested with 10+ concurrent agents
- **Rooms**: One per agent + shared spaces
- **Messages**: Thousands per day
- **Storage**: Grows with message history

### Scaling Strategies

**Horizontal Scaling**:
- Multiple agent manager instances (stateless)
- Load-balanced Matrix API
- Database read replicas (Synapse)

**Vertical Scaling**:
- Increase homeserver resources
- Optimize database queries
- Tune worker processes

**Optimization**:
- Event deduplication
- Message batching
- Connection pooling
- Caching strategies

---

## Monitoring & Observability

### Logs
- Agent manager: Agent sync, room creation
- Matrix homeserver: Protocol events
- Matrix API: Request/response logging
- MCP servers: Tool invocations

### Metrics
- Agent sync frequency
- Message throughput
- API response times
- Database performance
- Resource usage (CPU, memory, disk)

### Health Checks
- `/health` endpoints on all services
- Docker health checks
- Matrix homeserver status
- Database connectivity

---

## Development Workflow

### Local Development
1. Clone repository
2. Copy `.env.example` to `.env`
3. Configure environment variables
4. Run `docker-compose up -d`
5. Access Element at http://localhost:8008

### Testing
- **Unit Tests**: pytest with mocks
- **Integration Tests**: Full stack testing
- **Smoke Tests**: Quick validation
- See [TESTING.md](../operations/TESTING.md)

### Deployment
- **Development**: Local Docker Compose
- **Production**: Docker Compose with production config
- **CI/CD**: GitHub Actions for builds and tests
- See [DEPLOYMENT.md](../operations/DEPLOYMENT.md)

---

## Related Documentation

### Architecture
- [MATRIX_INTEGRATION.md](MATRIX_INTEGRATION.md) - Matrix homeserver integration
- [AGENT_MANAGEMENT.md](AGENT_MANAGEMENT.md) - Agent sync and room management
- [MCP_SERVERS.md](MCP_SERVERS.md) - MCP server architecture
- [INTER_AGENT_MESSAGING.md](INTER_AGENT_MESSAGING.md) - Inter-agent communication
- [TUWUNEL_MIGRATION.md](TUWUNEL_MIGRATION.md) - Tuwunel migration guide

### Operations
- [DEPLOYMENT.md](../operations/DEPLOYMENT.md) - Deployment guide
- [CI_CD.md](../operations/CI_CD.md) - CI/CD pipelines
- [TESTING.md](../operations/TESTING.md) - Testing guide
- [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md) - Common issues

### Process
- [CONTRIBUTING.md](../process/CONTRIBUTING.md) - Contribution guidelines
- [DEVELOPMENT.md](../process/DEVELOPMENT.md) - Development setup
- [CHANGELOG.md](../process/CHANGELOG.md) - Version history
- [BEST_PRACTICES.md](../process/BEST_PRACTICES.md) - Code standards

---

## Future Enhancements

### Planned Features
- [ ] Webhook-based agent discovery (vs polling)
- [ ] Agent-to-agent direct rooms
- [ ] Rich media support (images, files)
- [ ] End-to-end encryption support
- [ ] Multi-homeserver federation
- [ ] Admin dashboard UI
- [ ] Prometheus metrics export
- [ ] OpenTelemetry tracing

### Under Consideration
- [ ] Voice/video call integration
- [ ] Custom Element theme
- [ ] Webhook notifications
- [ ] GraphQL API layer
- [ ] Kubernetes deployment
- [ ] Multi-tenancy support

---

**Questions or Issues?**
See [TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md) or open an issue on GitHub.
