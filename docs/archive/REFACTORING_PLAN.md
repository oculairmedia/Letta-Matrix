# Refactoring Plan - Letta-Matrix Integration

**Date**: 2025-01-15
**Status**: 📋 Planning Phase
**Test Coverage**: ✅ 184/184 tests passing (100%)

## Executive Summary

The Letta-Matrix integration has grown organically and now requires refactoring to improve:
- **Maintainability**: Large monolithic files need to be broken down
- **Organization**: 40+ files in root directory need proper structure
- **Code Quality**: Reduce duplication, improve separation of concerns
- **Testability**: Make components more modular and easier to test

## Current Pain Points

### 1. Monolithic Files

#### agent_user_manager.py (1,345 lines)
**Issues**:
- Single class with 30+ methods handling multiple responsibilities
- Mixes: agent discovery, user creation, room management, space management, invitation tracking
- Global session management scattered
- Hard to test individual components in isolation

**Responsibilities Identified**:
- Agent discovery from Letta API
- Matrix user creation/management
- Room creation/management
- Space management (Letta Agents space)
- Invitation tracking
- Mapping persistence
- Admin token management

#### custom_matrix_client.py (816 lines)
**Issues**:
- Mixing client logic, message handling, Letta API calls, agent routing
- Global functions and class-based logic mixed together
- Callback handlers tightly coupled
- Retry logic duplicated

**Responsibilities Identified**:
- Matrix client connection/authentication
- Message event handling
- Agent message routing
- Letta API communication
- Response formatting
- Event deduplication

#### letta_agent_mcp_server.py (1,487 lines)
**Issues**:
- Combines MCP server, Letta client, and tool implementations
- Many tool implementations in single file
- Hard to extend with new tools

#### mcp_http_server.py (1,077 lines)
**Issues**:
- HTTP server, SSE streaming, tool registry all in one
- Difficult to test individual tools
- WebSocket and HTTP mixed

### 2. File Organization Chaos

**Current Structure**:
```
/
├── agent_user_manager.py
├── custom_matrix_client.py
├── matrix_api.py
├── matrix_auth.py
├── event_dedupe_store.py
├── letta_agent_mcp_server.py
├── mcp_http_server.py
├── mcp_server.py
├── matrix_mcp_bridge.py
├── test_*.py (12 test files in root!)
├── cleanup_*.py (4 cleanup scripts)
├── admin_*.py (2 admin scripts)
├── send_*.py (2 sender scripts)
└── ... 20+ more files
```

**Problems**:
- No clear separation of concerns
- Test files mixed with production code
- Utility scripts scattered
- Hard to find related functionality
- Docker builds include unnecessary files

### 3. Code Duplication

**Identified Duplications**:
- HTTP client setup repeated in multiple files
- Retry logic duplicated across files
- Matrix room operations repeated
- Configuration loading patterns duplicated
- Logging setup duplicated

### 4. Testing Challenges

**Issues**:
- Large classes make unit testing difficult
- Many private methods that need testing
- Mock setup is complex due to tight coupling
- Integration tests slower than necessary

## Proposed Refactoring Strategy

### Phase 1: Reorganize File Structure (Low Risk)

**Goal**: Organize files without changing code

**Proposed Structure**:
```
/opt/stacks/matrix-tuwunel-deploy/
├── src/
│   ├── __init__.py
│   ├── core/                      # Core business logic
│   │   ├── __init__.py
│   │   ├── agent_manager.py       # Agent discovery/sync
│   │   ├── user_manager.py        # Matrix user operations
│   │   ├── room_manager.py        # Matrix room operations
│   │   ├── space_manager.py       # Matrix space operations
│   │   └── mapping_store.py       # Persistence layer
│   │
│   ├── matrix/                     # Matrix client code
│   │   ├── __init__.py
│   │   ├── client.py              # Main Matrix client
│   │   ├── auth.py                # Authentication
│   │   ├── message_handler.py     # Message callbacks
│   │   ├── event_dedupe.py        # Event deduplication
│   │   └── room_operations.py     # Room join/create/etc
│   │
│   ├── letta/                      # Letta API integration
│   │   ├── __init__.py
│   │   ├── client.py              # Letta API client
│   │   ├── agent_router.py        # Agent message routing
│   │   └── response_parser.py     # Response parsing
│   │
│   ├── mcp/                        # MCP server implementations
│   │   ├── __init__.py
│   │   ├── http_server.py         # HTTP MCP server
│   │   ├── sse_server.py          # SSE streaming
│   │   ├── tools/                 # Individual tools
│   │   │   ├── __init__.py
│   │   │   ├── matrix_tools.py
│   │   │   ├── letta_tools.py
│   │   │   └── agent_tools.py
│   │   └── letta_mcp.py           # Letta-specific MCP
│   │
│   ├── api/                        # FastAPI endpoints
│   │   ├── __init__.py
│   │   ├── app.py                 # FastAPI app
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── matrix.py
│   │   │   ├── agents.py
│   │   │   └── webhooks.py
│   │   └── models.py              # Pydantic models
│   │
│   ├── utils/                      # Shared utilities
│   │   ├── __init__.py
│   │   ├── config.py              # Configuration management
│   │   ├── logging.py             # Logging setup
│   │   ├── http_client.py         # Shared HTTP client
│   │   └── retry.py               # Retry logic
│   │
│   └── models/                     # Data models
│       ├── __init__.py
│       ├── agent.py               # Agent models
│       ├── mapping.py             # Mapping models
│       └── config.py              # Config models
│
├── scripts/                        # Utility scripts
│   ├── admin/
│   │   ├── create_admin.py
│   │   └── admin_join_rooms.py
│   ├── cleanup/
│   │   ├── cleanup_agent_rooms.py
│   │   ├── cleanup_agent_users.py
│   │   └── cleanup_ghost_spaces.py
│   └── testing/
│       ├── send_to_admin.py
│       └── check_room_messages.py
│
├── tests/                          # All tests (already organized)
│   ├── unit/
│   ├── integration/
│   └── conftest.py
│
├── docs/                           # Documentation
│   ├── CLAUDE.md
│   ├── TESTING.md
│   ├── TEST_COVERAGE_SUMMARY.md
│   └── ...
│
├── docker/                         # Docker files
│   ├── Dockerfile.matrix-client
│   ├── Dockerfile.matrix-api
│   └── ...
│
├── docker-compose.yml
├── requirements.txt
├── pytest.ini
└── README.md
```

**Benefits**:
- Clear separation of concerns
- Easy to find related code
- Better IDE navigation
- Cleaner Docker builds
- Professional structure

**Risk Level**: 🟢 LOW
- Just moving files, no code changes
- Tests will catch any import issues
- Can be done incrementally

### Phase 2: Extract Managers from agent_user_manager.py (Medium Risk)

**Goal**: Break down the monolithic AgentUserManager class

**Step 1: Extract Space Manager**
```python
# src/core/space_manager.py
class MatrixSpaceManager:
    """Manages Matrix Spaces for Letta agents"""
    
    def __init__(self, homeserver_url: str, admin_token: str):
        pass
    
    async def create_letta_agents_space(self) -> Optional[str]:
        """Create the 'Letta Agents' space"""
        pass
    
    async def add_room_to_space(self, room_id: str, room_name: str) -> bool:
        """Add a room to the space"""
        pass
    
    async def migrate_existing_rooms_to_space(self) -> int:
        """Migrate existing rooms to space"""
        pass
    
    def get_space_id(self) -> Optional[str]:
        """Get current space ID"""
        pass
```

**Step 2: Extract User Manager**
```python
# src/core/user_manager.py
class MatrixUserManager:
    """Manages Matrix user accounts"""
    
    async def create_user(self, username: str, password: str, 
                         display_name: str) -> bool:
        """Create a Matrix user"""
        pass
    
    async def check_user_exists(self, username: str) -> bool:
        """Check if user exists"""
        pass
    
    async def update_display_name(self, user_id: str, 
                                  display_name: str) -> bool:
        """Update user display name"""
        pass
```

**Step 3: Extract Room Manager**
```python
# src/core/room_manager.py
class MatrixRoomManager:
    """Manages Matrix rooms"""
    
    async def create_agent_room(self, agent_name: str, 
                                members: List[str]) -> Optional[str]:
        """Create a room for an agent"""
        pass
    
    async def check_room_exists(self, room_id: str) -> bool:
        """Check if room exists"""
        pass
    
    async def update_room_name(self, room_id: str, new_name: str) -> bool:
        """Update room name"""
        pass
```

**Step 4: Extract Agent Discovery**
```python
# src/core/agent_manager.py
class LettaAgentManager:
    """Manages Letta agent discovery and synchronization"""
    
    async def get_letta_agents(self) -> List[dict]:
        """Get all Letta agents from API"""
        pass
    
    async def sync_agents(self) -> None:
        """Synchronize agents to Matrix users"""
        pass
```

**Step 5: Extract Mapping Persistence**
```python
# src/core/mapping_store.py
class AgentMappingStore:
    """Persists agent-to-Matrix-user mappings"""
    
    async def load_mappings(self) -> Dict[str, AgentUserMapping]:
        """Load mappings from file"""
        pass
    
    async def save_mappings(self, mappings: Dict[str, AgentUserMapping]):
        """Save mappings to file"""
        pass
```

**Step 6: Orchestrator**
```python
# src/core/agent_user_orchestrator.py
class AgentUserOrchestrator:
    """Orchestrates agent-to-user synchronization"""
    
    def __init__(self, config: Config):
        self.agent_manager = LettaAgentManager(config)
        self.user_manager = MatrixUserManager(config)
        self.room_manager = MatrixRoomManager(config)
        self.space_manager = MatrixSpaceManager(config)
        self.mapping_store = AgentMappingStore(config)
    
    async def sync_all_agents(self):
        """Main synchronization workflow"""
        # Orchestrates all the managers
        pass
```

**Benefits**:
- Each class has single responsibility
- Easier to test in isolation
- Can reuse managers independently
- Clearer dependencies

**Risk Level**: 🟡 MEDIUM
- Requires careful refactoring
- Need to update tests
- Must maintain all functionality

### Phase 3: Modularize custom_matrix_client.py (Medium Risk)

**Goal**: Separate concerns in Matrix client

**Step 1: Extract Message Handler**
```python
# src/matrix/message_handler.py
class MessageHandler:
    """Handles incoming Matrix messages"""
    
    async def handle_message(self, room, event, client):
        """Process incoming message"""
        pass
    
    def identify_agent_from_room(self, room_id: str) -> Optional[str]:
        """Identify which agent owns this room"""
        pass
```

**Step 2: Extract Letta Client**
```python
# src/letta/client.py
class LettaClient:
    """Handles communication with Letta API"""
    
    async def send_message(self, agent_id: str, message: str, 
                          sender: str) -> str:
        """Send message to Letta agent"""
        pass
    
    def parse_response(self, response: dict) -> str:
        """Parse Letta API response"""
        pass
```

**Step 3: Extract Agent Router**
```python
# src/letta/agent_router.py
class AgentRouter:
    """Routes messages to appropriate Letta agents"""
    
    def get_agent_for_room(self, room_id: str) -> Optional[str]:
        """Get agent ID for room"""
        pass
    
    async def send_as_agent(self, room_id: str, message: str) -> bool:
        """Send message as agent user"""
        pass
```

**Step 4: Main Client**
```python
# src/matrix/client.py
class MatrixClient:
    """Main Matrix client"""
    
    def __init__(self, config: Config):
        self.auth_manager = MatrixAuthManager(config)
        self.message_handler = MessageHandler(config)
        self.agent_router = AgentRouter(config)
        self.letta_client = LettaClient(config)
    
    async def start(self):
        """Start the client"""
        pass
```

**Benefits**:
- Clear separation of Matrix and Letta logic
- Easier to mock for testing
- Can replace Letta client without changing Matrix code

**Risk Level**: 🟡 MEDIUM

### Phase 4: Shared Utilities (Low Risk)

**Goal**: Extract common patterns

**HTTP Client**:
```python
# src/utils/http_client.py
class HTTPClient:
    """Shared HTTP client with retry logic"""
    
    async def get(self, url: str, **kwargs):
        """GET with retry"""
        pass
    
    async def post(self, url: str, **kwargs):
        """POST with retry"""
        pass
```

**Retry Logic**:
```python
# src/utils/retry.py
async def retry_with_backoff(func, max_retries=3, base_delay=1.0):
    """Unified retry logic"""
    pass
```

**Configuration**:
```python
# src/utils/config.py
class ConfigLoader:
    """Centralized configuration loading"""
    
    @classmethod
    def from_env(cls) -> Config:
        """Load from environment"""
        pass
```

**Risk Level**: 🟢 LOW

### Phase 5: MCP Server Modularization (Low Risk)

**Goal**: Organize MCP tools

**Structure**:
```python
# src/mcp/tools/matrix_tools.py
class MatrixTools:
    """Matrix-related MCP tools"""
    
    async def send_message(self, ...):
        pass
    
    async def list_rooms(self, ...):
        pass

# src/mcp/tools/letta_tools.py
class LettaTools:
    """Letta-related MCP tools"""
    
    async def list_agents(self, ...):
        pass
    
    async def send_to_agent(self, ...):
        pass
```

**Risk Level**: 🟢 LOW

## Implementation Plan

### Sprint 1: File Reorganization (1-2 days)
- [ ] Create new directory structure
- [ ] Move files to appropriate locations
- [ ] Update all imports
- [ ] Update Docker files
- [ ] Run full test suite
- [ ] Update documentation

### Sprint 2: Extract Space Manager (2-3 days)
- [ ] Create MatrixSpaceManager class
- [ ] Move space-related methods
- [ ] Update tests
- [ ] Update AgentUserManager to use new manager
- [ ] Verify all tests pass

### Sprint 3: Extract User Manager (2-3 days)
- [ ] Create MatrixUserManager class
- [ ] Move user-related methods
- [ ] Update tests
- [ ] Integration testing
- [ ] Verify all tests pass

### Sprint 4: Extract Room Manager (2-3 days)
- [ ] Create MatrixRoomManager class
- [ ] Move room-related methods
- [ ] Update tests
- [ ] Integration testing
- [ ] Verify all tests pass

### Sprint 5: Extract Agent Manager (2-3 days)
- [ ] Create LettaAgentManager class
- [ ] Move agent discovery methods
- [ ] Update tests
- [ ] Integration testing
- [ ] Verify all tests pass

### Sprint 6: Create Orchestrator (1-2 days)
- [ ] Build AgentUserOrchestrator
- [ ] Wire up all managers
- [ ] Update main entry point
- [ ] Full integration testing
- [ ] Performance testing

### Sprint 7: Matrix Client Refactoring (3-4 days)
- [ ] Extract MessageHandler
- [ ] Extract LettaClient
- [ ] Extract AgentRouter
- [ ] Update tests
- [ ] Integration testing

### Sprint 8: Shared Utilities (1-2 days)
- [ ] Extract HTTPClient
- [ ] Extract retry logic
- [ ] Extract config loader
- [ ] Update all callers

### Sprint 9: MCP Modularization (2-3 days)
- [ ] Organize tools into modules
- [ ] Update tool registry
- [ ] Update tests
- [ ] Documentation

### Sprint 10: Cleanup & Documentation (1-2 days)
- [ ] Remove deprecated code
- [ ] Update all documentation
- [ ] Create migration guide
- [ ] Final testing

**Total Estimated Time**: 4-6 weeks

## Success Criteria

### Mandatory
- ✅ All 184 tests still passing
- ✅ No functionality regression
- ✅ All existing features working
- ✅ Docker containers build successfully
- ✅ Performance maintained or improved

### Quality Improvements
- 📦 Clear module boundaries
- 🧪 Easier to write new tests
- 📝 Better code documentation
- 🔧 Easier to add new features
- 🐛 Fewer bugs due to better organization

## Risks & Mitigation

### Risk 1: Breaking Existing Functionality
**Mitigation**: 
- Run full test suite after each change
- Incremental refactoring (one module at a time)
- Keep old code until new code proven

### Risk 2: Import Hell
**Mitigation**:
- Plan imports carefully
- Use absolute imports
- Update all at once with find/replace

### Risk 3: Test Updates Required
**Mitigation**:
- Update tests as modules are extracted
- Keep integration tests as safety net
- Add new unit tests for extracted classes

### Risk 4: Docker Build Issues
**Mitigation**:
- Update Dockerfiles incrementally
- Test builds frequently
- Keep old Dockerfiles as backup

### Risk 5: Team Confusion
**Mitigation**:
- Document changes clearly
- Create migration guide
- Communicate frequently

## Rollback Plan

If issues arise:
1. Each sprint creates a git branch
2. Can revert to previous sprint
3. All changes committed incrementally
4. Feature flags for new code paths

## Post-Refactoring Benefits

### For Development
- Faster onboarding for new developers
- Easier to find and fix bugs
- Simpler to add new features
- Better code reviews

### For Testing
- Faster test execution
- Easier to write unit tests
- Better test isolation
- More reliable CI/CD

### For Deployment
- Cleaner Docker builds
- Better separation of services
- Easier to scale components
- Better monitoring capabilities

### For Maintenance
- Clear module boundaries
- Easier dependency management
- Better code reuse
- Reduced technical debt

## Next Steps

1. **Review this plan** with team
2. **Prioritize sprints** based on pain points
3. **Create feature branch** for refactoring
4. **Start with Sprint 1** (file reorganization)
5. **Measure progress** against success criteria

---

**Author**: OpenCode AI Assistant
**Date**: 2025-01-15
**Version**: 1.0
**Status**: Ready for Review
