# Letta Matrix Integration - Claude Documentation

## Overview
This document summarizes the comprehensive Letta Matrix integration with multi-agent support. The integration enables AI agents to have individual Matrix identities and participate in conversations through their own accounts, with automatic user/room creation and management. Agent usernames are now based on stable agent IDs rather than agent names to ensure consistency even when agents are renamed.

## Architecture Summary

### Core Components
```
Letta Agents ←→ Agent Manager ←→ Matrix Users ←→ Matrix Rooms
     ↓              ↓                ↓              ↓
MCP Server ←→ Matrix API ←→ Matrix Synapse ←→ GMMessages Bridge ←→ SMS/RCS
```

1. **Matrix Synapse Homeserver** (`docker-compose.yml`)
   - PostgreSQL backend database
   - Element web client interface  
   - Nginx reverse proxy
   - Admin users: `@letta:matrix.oculair.ca`, `@matrixadmin:matrix.oculair.ca`, `@admin:matrix.oculair.ca`

2. **Agent User Manager** (`agent_user_manager.py`)
   - Automatic Letta agent discovery via OpenAI endpoint monitoring
   - Uses http://192.168.50.90:1416/v1/models (each model = agent)
   - Matrix user creation for each agent
   - Dedicated room creation per agent
   - Persistent mapping storage in `/app/data/agent_user_mappings.json`
   - Room reuse on restart (no duplicates)
   - Programmatic admin token retrieval

3. **Custom Matrix Client** (`custom_matrix_client.py`)
   - Agent-specific message routing
   - Individual agent responses (agents respond as themselves)
   - Old message filtering on startup (no replay)
   - Multi-room monitoring
   - Agent user authentication for responses

4. **MCP Server** (`mcp_http_server.py`)
   - HTTP streaming on port 8016
   - WebSocket on port 8015
   - 5 pre-authenticated Matrix tools (using @letta account)
   - Administrative access to all rooms

5. **Matrix API Service** (`matrix_api.py`)
   - FastAPI REST interface on port 8004
   - Authentication management
   - Rate limiting and error handling

6. **GMMessages Bridge** (`../mautrix-gmessages/`)
   - SMS/RCS integration via Google Messages
   - Auto-relay to Letta user
   - Admin permissions for bridge management

## Multi-Agent Features

### Agent User Creation
- Each Letta agent gets a dedicated Matrix user
- Username format: `@agent_{uuid_with_underscores}:matrix.oculair.ca` (based on agent ID, not name)
- Display names: Set to agent's human-readable name
- Passwords: Set to "password" in DEV_MODE, otherwise secure random
- Automatic creation on agent discovery
- Usernames remain stable even if agent is renamed

### Agent Rooms
- Each agent has a dedicated Matrix room
- Room name: `{agent-name} - Letta Agent Chat`
- Members: Agent user, @letta, @matrixadmin, @admin
- Persistent across restarts

### Agent Responses
- Messages in agent rooms are answered by the agent's Matrix user
- Example: In Meridian's room, @meridian responds, not @letta
- Maintains individual agent identity and personality

### Automatic Name Updates
- When a Letta agent is renamed, the system automatically:
  - Updates the Matrix room name to reflect the new agent name
  - Updates the Matrix user's display name to match
  - Preserves the original Matrix username for stability
- Name changes are detected within 0.5 seconds
- Updates appear in Matrix clients within 5-10 seconds

## Available Matrix Tools

All MCP tools use pre-configured authentication with `@letta:matrix.oculair.ca` credentials:

1. **`matrix_send_message`** - Send messages to any Matrix room (as @letta)
2. **`matrix_list_rooms`** - Discover available rooms including agent rooms  
3. **`matrix_read_room`** - Monitor conversations and message history
4. **`matrix_join_room`** - Access new rooms automatically
5. **`matrix_create_room`** - Create private/public rooms

Note: MCP tools provide administrative access while agent users handle conversational responses.

## Key Integration Features

### Automatic Agent Synchronization
- Sync on startup ensures all agents have users/rooms
- Periodic sync every 60 seconds for new agents
- No manual intervention required

### Zero-Configuration Authentication
```python
# Agent users are created automatically with stored credentials
mapping = {
    "agent_id": "agent-e54fc601-4773-4116-9c6c-cf45da2e269e",
    "agent_name": "Meridian", 
    "matrix_user_id": "@agent_e54fc601_4773_4116_9c6c_cf45da2e269e:matrix.oculair.ca",
    "matrix_password": "password",  # In DEV_MODE
    "room_id": "!ZrQOdTvhUZsAnrJJre:matrix.oculair.ca",
    "created": true,
    "room_created": true
}
```

### Message Flow
1. User sends message in agent room
2. Main @letta client receives and routes to correct Letta agent
3. Letta agent processes and returns response
4. Response sent as the agent's Matrix user
5. Agent appears to respond naturally in conversation

### No Message Replay
- Startup timestamp tracking prevents old message processing
- `sync_filter` with `limit: 0` prevents historical message fetch
- Clean restart without conversation replay

## SMS/RCS Integration Flow

### Incoming Messages
```
SMS → Google Messages → GMMessages Bridge → Matrix Room → Agent User Response
```

### Outgoing Messages  
```
Agent → Agent Matrix User → Matrix Room → Bridge → SMS
```

## File Structure

### Core Files
- `docker-compose.yml` - Main orchestration with all services
- `agent_user_manager.py` - Agent-to-Matrix user synchronization
- `custom_matrix_client.py` - Enhanced client with agent routing
- `mcp_http_server.py` - MCP server with Matrix tools
- `matrix_api.py` - FastAPI service for Matrix operations
- `MATRIX_MCP_TOOLS.md` - Comprehensive tool documentation

### Configuration Files
- `.env` - Environment variables and credentials
- `synapse-data/homeserver.yaml` - Synapse configuration
- `matrix_store/` - Letta user session databases
- `matrix_client_data/agent_user_mappings.json` - Agent-user-room mappings

### Bridge Integration
- `../mautrix-gmessages/config/config.yaml` - Bridge permissions
- `../mautrix-gmessages/auto_invite_letta.py` - Auto-invitation script

## Performance Characteristics

### Agent Sync
- Startup sync: Immediate on container start
- Periodic sync: Every 0.5 seconds (optimized from 60 seconds)
- New agent detection: Within 0.5 seconds
- Name change detection: Within 0.5 seconds

### Response Times
- Agent message processing: <1 second typical (optimized)
- Room creation: <2 seconds
- User creation: <1 second
- Message sending as agent: <1 second
- Room/display name updates: 5-10 seconds to appear in clients

### Scalability
- Unlimited agents supported
- Each agent has dedicated resources
- No cross-agent interference
- Connection pooling for optimal performance
- Rate limiting disabled for internal use

## Security Model

### Authentication Security
- Agent credentials stored in persistent JSON file
- Programmatic token management
- No hardcoded tokens in code
- DEV_MODE for simplified testing

### Permission Model
- Each agent only responds in their own room
- @letta account has administrative access
- MCP tools use @letta for management tasks
- Agent users have limited scope

## Monitoring and Health Checks

### Service Health Endpoints
```bash
# MCP server health
curl http://localhost:8015/health

# Matrix API service health  
curl http://localhost:8004/health

# Check agent sync logs
docker logs matrix-synapse-deployment-matrix-client-1 | grep "agent sync"
```

### Key Log Messages
- "Found X Letta agents" - Agent discovery
- "Processing agent: {name}" - Agent user creation
- "Successfully sent message as agent {username}" - Agent response
- "Running periodic agent sync" - Sync cycle

## Configuration Options

### Environment Variables
```bash
# Enable development mode (simple passwords)
DEV_MODE=true

# Admin credentials for user creation
MATRIX_ADMIN_USERNAME=@matrixadmin:matrix.oculair.ca
MATRIX_ADMIN_PASSWORD=admin123

# Main bot credentials
MATRIX_USERNAME=@letta:matrix.oculair.ca
MATRIX_PASSWORD=letta
```

### Sync Interval
Modify in `custom_matrix_client.py`:
```python
async def periodic_agent_sync(config, logger, interval=60):  # seconds
```

## Testing and Validation

### Successful Test Categories
- ✅ Multi-agent user creation
- ✅ Individual agent responses
- ✅ Room persistence across restarts
- ✅ No message replay on startup
- ✅ Automatic new agent detection
- ✅ Agent identity in conversations

### Testing New Agents
1. Create new agent in Letta
2. Wait up to 60 seconds (or restart matrix-client)
3. Check Element/Matrix client for new room
4. Send message to test agent response

## Performance Optimizations

### Network Optimizations
- HTTP connection pooling with 100 concurrent connections
- DNS caching for 5 minutes
- Keep-alive connections for 30 seconds
- Timeout handling with fresh sessions to avoid context errors

### Matrix Optimizations
- Sync timeout reduced from 30s to 5s for faster message delivery
- Lazy loading enabled for member data
- Disabled presence updates to reduce bandwidth
- Disabled account data sync for performance
- Rate limiting disabled in Synapse configuration

### Agent Management
- Polling interval reduced from 2s to 0.5s
- Automatic room and display name updates on agent rename
- Efficient mapping storage with persistent JSON file
- No message replay on startup with timestamp tracking

## Production Status

The integration is **partially operational** with the following status:

### Working Features ✅
- Multi-agent support with individual Matrix identities
- Agent discovery via Letta proxy on port 1416
- Message processing and agent responses
- Persistent state management in JSON mappings
- MCP tools for Matrix operations

### Temporary Issues ⚠️
- **Invitation Management**: Disabled due to permission errors (M_FORBIDDEN)
- **Agent Synchronization**: Temporarily disabled to prioritize message processing
- **Admin User Access**: Cannot auto-invite @admin to agent rooms

### Recent Fixes (January 7, 2025)
- Fixed agent discovery endpoint (port 8283 → 1416)
- Cleaned up stale agent mappings
- Enhanced message parsing for multiple response formats
- Disabled blocking invitation loops

See `MATRIX_FIXES_2025_01_07.md` for detailed information about recent fixes and current workarounds.

This enables each Letta agent to participate as a unique Matrix user with their own identity, room, and conversational context, while maintaining centralized management through the @letta account and MCP tools.