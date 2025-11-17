# Best Practices

## Overview

This document outlines best practices for the Letta-Matrix integration, covering code organization, testing patterns, error handling, security, and architecture patterns learned from production Matrix bridges.

## Table of Contents

- [Code Organization](#code-organization)
- [Testing Patterns](#testing-patterns)
- [Error Handling](#error-handling)
- [Security Practices](#security-practices)
- [Matrix Bridge Patterns](#matrix-bridge-patterns)
- [Performance Optimization](#performance-optimization)
- [Refactoring Guidelines](#refactoring-guidelines)

---

## Code Organization

### Module Structure

Follow the established directory structure:

```
src/
├── core/          # Core business logic
│   ├── agent_user_manager.py      # Orchestration
│   ├── space_manager.py           # Space operations
│   ├── user_manager.py            # User management
│   └── room_manager.py            # Room management
├── matrix/        # Matrix client code
│   ├── client.py                  # Main client
│   ├── auth.py                    # Authentication
│   └── event_dedupe.py            # Deduplication
├── letta/         # Letta API integration
├── mcp/           # MCP server implementations
│   └── tools/                     # Individual tools
├── api/           # FastAPI endpoints
│   └── routes/                    # Route handlers
├── utils/         # Shared utilities
└── models/        # Data models
```

### Single Responsibility Principle

Each module should have one clear responsibility:

**Good**:
```python
# src/core/space_manager.py
class MatrixSpaceManager:
    """Manages Matrix Spaces for Letta agents"""

    async def create_letta_agents_space(self) -> Optional[str]:
        """Create the 'Letta Agents' space"""

    async def add_room_to_space(self, room_id: str, room_name: str) -> bool:
        """Add a room to the space"""
```

**Bad**:
```python
# Too many responsibilities
class AgentUserManager:
    async def create_space(self): ...
    async def create_user(self): ...
    async def create_room(self): ...
    async def send_message(self): ...
    async def handle_letta_api(self): ...
```

### Separation of Concerns

Separate different aspects of functionality:

1. **Business Logic** (`src/core/`) - What to do
2. **API/Protocol** (`src/matrix/`, `src/letta/`) - How to communicate
3. **Orchestration** (`agent_user_manager.py`) - Coordination
4. **Utilities** (`src/utils/`) - Shared helpers
5. **Models** (`src/models/`) - Data structures

### Manager Pattern

Use managers to encapsulate related operations:

```python
class MatrixSpaceManager:
    """Manages all space-related operations"""

    def __init__(self, homeserver_url: str, admin_token: str):
        self.homeserver_url = homeserver_url
        self.admin_token = admin_token

class MatrixUserManager:
    """Manages all user-related operations"""

    def __init__(self, homeserver_url: str, config):
        self.homeserver_url = homeserver_url
        self.config = config

# Orchestrator delegates to managers
class AgentUserOrchestrator:
    def __init__(self, config):
        self.space_manager = MatrixSpaceManager(...)
        self.user_manager = MatrixUserManager(...)
        self.room_manager = MatrixRoomManager(...)
```

### Callback Pattern

Use callbacks to avoid circular dependencies:

```python
class MatrixRoomManager:
    def __init__(
        self,
        get_admin_token_callback,  # Instead of direct dependency
        save_mappings_callback
    ):
        self.get_admin_token = get_admin_token_callback
        self.save_mappings = save_mappings_callback

    async def create_room(self):
        token = await self.get_admin_token()
        # ... create room ...
        await self.save_mappings(mappings)
```

### Configuration Management

Use configuration classes for settings:

```python
from dataclasses import dataclass

@dataclass
class Config:
    """Application configuration"""
    homeserver_url: str
    admin_username: str
    admin_password: str
    letta_api_url: str
    letta_token: str
    dev_mode: bool = False

    @classmethod
    def from_env(cls) -> 'Config':
        """Load configuration from environment variables"""
        return cls(
            homeserver_url=os.getenv('MATRIX_HOMESERVER_URL'),
            admin_username=os.getenv('MATRIX_ADMIN_USERNAME'),
            # ...
        )
```

---

## Testing Patterns

### Test Organization

Organize tests by type:

```
tests/
├── unit/                          # Fast, isolated tests
│   ├── test_agent_user_manager.py
│   ├── test_space_manager.py
│   └── test_user_manager.py
├── integration/                   # Component interaction tests
│   ├── test_agent_workflow.py
│   └── test_matrix_integration.py
└── smoke/                         # End-to-end critical paths
    └── test_agent_messaging.py
```

### Unit Test Patterns

**Arrange-Act-Assert Pattern**:
```python
def test_generate_username():
    """Test username generation from agent ID"""
    # Arrange
    manager = MatrixUserManager(...)
    agent_id = "agent-123-456"

    # Act
    username = manager.generate_username("TestAgent", agent_id)

    # Assert
    assert username == "agent_123_456"
```

**Mocking External Dependencies**:
```python
@pytest.mark.asyncio
async def test_create_user(mock_aiohttp_session):
    """Test Matrix user creation"""
    # Mock HTTP response
    mock_aiohttp_session.post.return_value.__aenter__.return_value.status = 201

    manager = MatrixUserManager(...)
    result = await manager.create_matrix_user("testuser", "password", "Test User")

    assert result is True
    mock_aiohttp_session.post.assert_called_once()
```

### Integration Test Patterns

**Test Component Interactions**:
```python
@pytest.mark.asyncio
async def test_agent_user_workflow(mock_letta_api, mock_matrix_api):
    """Test full agent-to-user creation workflow"""
    # Mock Letta API returning agents
    mock_letta_api.return_value = [{"id": "agent-1", "name": "TestAgent"}]

    orchestrator = AgentUserOrchestrator(config)
    await orchestrator.sync_all_agents()

    # Verify user created
    assert "agent-1" in orchestrator.mappings
    # Verify room created
    assert orchestrator.mappings["agent-1"]["room_id"] is not None
```

### Test Coverage Requirements

- **100% pass rate** on all tests (mandatory)
- **>80% code coverage** on new code (target)
- **All public methods** must have tests
- **Critical paths** must have integration tests
- **Error cases** must be tested

### Test Data Management

Use fixtures for test data:

```python
@pytest.fixture
def sample_agent():
    """Sample agent data for testing"""
    return {
        "id": "agent-123-456",
        "name": "TestAgent",
        "created_at": "2025-01-15T10:00:00Z"
    }

@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    return Config(
        homeserver_url="http://localhost:8008",
        admin_username="admin",
        # ...
    )
```

### Async Testing

Always use `@pytest.mark.asyncio` for async tests:

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    """Test async functionality"""
    result = await some_async_function()
    assert result == expected
```

---

## Error Handling

### Exception Hierarchy

Define custom exceptions for domain-specific errors:

```python
class LettaMatrixError(Exception):
    """Base exception for Letta-Matrix integration"""

class MatrixAPIError(LettaMatrixError):
    """Matrix API request failed"""

class AgentNotFoundError(LettaMatrixError):
    """Agent not found in Letta"""

class RoomCreationError(LettaMatrixError):
    """Failed to create Matrix room"""
```

### Try-Except-Finally Pattern

Always clean up resources:

```python
async def create_room(self, room_name: str):
    """Create a Matrix room"""
    client = None
    try:
        client = AsyncClient(self.homeserver_url)
        await client.login(self.username, self.password)
        room_id = await client.room_create(name=room_name)
        logger.info(f"Created room {room_id}")
        return room_id
    except Exception as e:
        logger.error(f"Failed to create room: {e}")
        raise RoomCreationError(f"Room creation failed: {e}") from e
    finally:
        if client:
            await client.close()
```

### Error Logging

Log errors with context:

```python
try:
    await client.room_send(room_id, message)
except MatrixAPIError as e:
    logger.error(
        "Failed to send message",
        extra={
            "room_id": room_id,
            "agent_id": agent_id,
            "error": str(e)
        },
        exc_info=True
    )
    raise
```

### Retry Logic

Use exponential backoff for transient failures:

```python
async def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0
):
    """Retry function with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(f"Attempt {attempt + 1} failed, retrying in {delay}s: {e}")
            await asyncio.sleep(delay)
```

### Graceful Degradation

Provide fallback behavior when possible:

```python
async def send_as_agent(room_id: str, message: str):
    """Send message as agent user with fallback"""
    try:
        # Try to send as agent user
        sent = await agent_client.room_send(room_id, message)
        return True
    except MatrixAPIError as e:
        logger.warning(f"Failed to send as agent, falling back to main client: {e}")
        # Fallback to main client
        try:
            await main_client.room_send(room_id, message)
            return True
        except Exception as e2:
            logger.error(f"Fallback also failed: {e2}")
            return False
```

---

## Security Practices

### Credential Management

**Never hardcode credentials**:

```python
# Good
username = os.getenv('MATRIX_USERNAME')
password = os.getenv('MATRIX_PASSWORD')

# Bad
username = '@letta:matrix.oculair.ca'  # Don't do this
password = 'letta123'  # Never do this
```

### Password Generation

Use secure random passwords in production:

```python
def generate_password(self) -> str:
    """Generate secure password for Matrix user"""
    if self.dev_mode:
        return "password"  # Simple for development
    else:
        # Secure random password for production
        import secrets
        return secrets.token_urlsafe(32)
```

### Token Storage

Store tokens securely:

```python
# Store in persistent, protected location
self.mappings_file = "/app/data/agent_user_mappings.json"

# Set proper file permissions
os.chmod(self.mappings_file, 0o600)  # Read/write for owner only
```

### Input Validation

Always validate input:

```python
def generate_username(self, agent_name: str, agent_id: str) -> str:
    """Generate username from agent ID"""
    # Validate agent_id format
    if not agent_id or not agent_id.startswith('agent-'):
        raise ValueError(f"Invalid agent_id: {agent_id}")

    # Sanitize for Matrix username
    clean_id = agent_id.replace('-', '_')
    username = f"agent_{clean_id}"

    # Ensure valid Matrix username
    if not re.match(r'^[a-z0-9_]+$', username):
        raise ValueError(f"Invalid username: {username}")

    return username
```

### API Authentication

Use proper authentication for all API calls:

```python
async def get_admin_token(self) -> str:
    """Get admin access token with authentication"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{self.homeserver_url}/_matrix/client/r0/login",
            json={
                "type": "m.login.password",
                "user": self.admin_username,
                "password": self.admin_password
            }
        ) as resp:
            if resp.status != 200:
                raise MatrixAPIError(f"Failed to authenticate: {resp.status}")
            data = await resp.json()
            return data["access_token"]
```

---

## Matrix Bridge Patterns

Based on analysis of production Matrix bridges (mautrix-discord, mautrix-signal, etc.).

### Matrix Space Organization

**Best Practice**: Organize all bridged rooms within a Matrix Space

```python
class MatrixSpaceManager:
    """Manages Matrix Spaces following bridge best practices"""

    async def create_letta_agents_space(self) -> Optional[str]:
        """Create 'Letta Agents' space (like mautrix bridges)"""
        # Create space
        space_id = await self.create_space(
            name="Letta Agents",
            topic="AI Agents connected via Letta-Matrix bridge"
        )

        # Save space configuration
        await self.save_space_config(space_id)

        return space_id

    async def add_room_to_space(self, room_id: str, room_name: str) -> bool:
        """Add room to space with bidirectional relationships"""
        # Set m.space.child on space (parent → child)
        await self.set_state_event(
            room_id=self.space_id,
            event_type="m.space.child",
            state_key=room_id,
            content={
                "via": ["matrix.oculair.ca"],
                "suggested": True,
                "order": room_name
            }
        )

        # Set m.space.parent on room (child → parent)
        await self.set_state_event(
            room_id=room_id,
            event_type="m.space.parent",
            state_key=self.space_id,
            content={
                "via": ["matrix.oculair.ca"],
                "canonical": True
            }
        )
```

**Benefits**:
- Users join space once to see all agent rooms
- Follows standard Matrix bridge pattern
- Better organization and discovery
- Automatic room hierarchy

### Stable User IDs

**Best Practice**: Use stable IDs for usernames, not display names

```python
def generate_username(self, agent_name: str, agent_id: str) -> str:
    """Generate username from stable agent ID"""
    # Use agent ID (stable) not name (changeable)
    clean_id = agent_id.replace('-', '_')
    username = f"agent_{clean_id}"
    return username

async def update_display_name(self, user_id: str, new_name: str):
    """Update display name when agent is renamed"""
    # Username stays the same, only display name changes
    await self.set_user_display_name(user_id, new_name)
```

**Benefits**:
- Usernames remain stable even when agents are renamed
- Display names can change without breaking references
- Follows Matrix best practices

### Room Persistence and Reuse

**Best Practice**: Reuse existing rooms on restart

```python
async def find_existing_agent_room(self, agent_id: str) -> Optional[str]:
    """Find existing room for agent"""
    # Check mappings
    if agent_id in self.mappings:
        room_id = self.mappings[agent_id].get("room_id")
        # Verify room still exists
        if await self.check_room_exists(room_id):
            return room_id
    return None

async def create_or_update_agent_room(self, agent_id: str):
    """Create room or reuse existing"""
    # Try to find existing room first
    room_id = await self.find_existing_agent_room(agent_id)

    if room_id:
        logger.info(f"Reusing existing room {room_id}")
        return room_id
    else:
        logger.info(f"Creating new room for agent {agent_id}")
        return await self.create_agent_room(agent_id)
```

**Benefits**:
- No duplicate rooms on restart
- Preserves conversation history
- Reduces resource usage

### Event Deduplication

**Best Practice**: Prevent processing the same event twice

```python
class EventDedupeStore:
    """Multi-process safe event deduplication"""

    def is_duplicate_event(self, event_id: str) -> bool:
        """Check if event already processed (atomic operation)"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, processed_at) VALUES (?, ?)",
                (event_id, time.time())
            )
            conn.commit()
            conn.close()

            # If rowcount is 0, event already existed (duplicate)
            return cursor.rowcount == 0
```

**Benefits**:
- Atomic operation (race-condition safe)
- Multi-process safe
- Persistent across restarts
- Automatic TTL cleanup

### Message Queuing

**Best Practice**: Buffer messages to prevent loss during high load

```python
class MessageQueue:
    """Async message queue for reliable processing"""

    def __init__(self, maxsize: int = 128):
        self.queue = asyncio.Queue(maxsize=maxsize)

    async def enqueue(self, message):
        """Add message to queue"""
        await self.queue.put(message)

    async def process_loop(self):
        """Process messages from queue"""
        while True:
            try:
                message = await self.queue.get()
                await self.process_message(message)
            except Exception as e:
                logger.error(f"Error processing message: {e}")
            finally:
                self.queue.task_done()
```

**Benefits**:
- Prevents message loss during spikes
- Rate limiting
- Ordered processing
- Graceful degradation

---

## Performance Optimization

### Connection Pooling

Reuse HTTP connections:

```python
# Create session once
self.session = aiohttp.ClientSession(
    connector=aiohttp.TCPConnector(
        limit=100,  # Max concurrent connections
        ttl_dns_cache=300  # DNS cache 5 minutes
    ),
    timeout=aiohttp.ClientTimeout(total=30)
)

# Reuse for all requests
async with self.session.get(url) as resp:
    return await resp.json()

# Close on shutdown
await self.session.close()
```

### Batch Operations

Batch API calls when possible:

```python
async def sync_all_agents(self):
    """Sync all agents in batches"""
    agents = await self.get_letta_agents()

    # Process in batches to avoid overwhelming API
    batch_size = 10
    for i in range(0, len(agents), batch_size):
        batch = agents[i:i + batch_size]
        await asyncio.gather(*[
            self.sync_agent(agent) for agent in batch
        ])
```

### Caching

Cache frequently accessed data:

```python
from functools import lru_cache

@lru_cache(maxsize=100)
def get_agent_mapping(self, agent_id: str):
    """Get agent mapping with caching"""
    return self.mappings.get(agent_id)

# Clear cache when mappings change
def save_mappings(self):
    self.get_agent_mapping.cache_clear()
    # Save to file...
```

### Async/Await Best Practices

Use async for I/O operations:

```python
# Good - concurrent I/O
async def sync_agents(self, agent_ids: List[str]):
    tasks = [self.sync_agent(aid) for aid in agent_ids]
    await asyncio.gather(*tasks)

# Bad - sequential I/O
async def sync_agents_slow(self, agent_ids: List[str]):
    for aid in agent_ids:
        await self.sync_agent(aid)  # Waits for each one
```

---

## Refactoring Guidelines

### When to Refactor

Refactor when you encounter:

1. **Monolithic files** (>500 lines)
2. **Classes with too many responsibilities** (>10 public methods doing different things)
3. **Code duplication** (same logic in 3+ places)
4. **Difficult to test** (need to mock many dependencies)
5. **Hard to understand** (complex logic without clear structure)

### How to Refactor Safely

#### Sprint-Based Approach

1. **Plan the sprint**:
   - Define clear goals
   - Identify what to extract
   - Estimate size reduction

2. **Create feature branch**:
   ```bash
   git checkout -b sprint-X-description
   ```

3. **Extract incrementally**:
   - Extract one manager at a time
   - Update tests after each extraction
   - Ensure 100% test pass rate

4. **Commit with descriptive message**:
   ```bash
   git commit -m "refactor: Sprint X - Extract ManagerName (Completion Summary)"
   ```

5. **Merge when stable**:
   - All tests passing
   - No functionality regression
   - Code review approved

#### Example: Extract Manager

Before (monolithic):
```python
class AgentUserManager:  # 1,346 lines
    async def create_space(self): ...
    async def add_room_to_space(self): ...
    async def create_user(self): ...
    async def create_room(self): ...
    # ... 30+ more methods
```

After (modular):
```python
class AgentUserManager:  # 574 lines - orchestration only
    def __init__(self):
        self.space_manager = MatrixSpaceManager(...)  # 367 lines
        self.user_manager = MatrixUserManager(...)    # 317 lines
        self.room_manager = MatrixRoomManager(...)    # 478 lines

    async def sync_agent(self, agent):
        # Delegates to managers
        user_id = await self.user_manager.create_user(...)
        room_id = await self.room_manager.create_room(...)
        await self.space_manager.add_room_to_space(...)
```

### Refactoring Checklist

- [ ] Tests pass before refactoring
- [ ] Extract into separate file/class
- [ ] Update imports
- [ ] Update tests
- [ ] Tests pass after refactoring
- [ ] No functionality regression
- [ ] Code is more readable
- [ ] Commit with clear message

### Success Metrics

- **100% test pass rate** (mandatory)
- **No functionality regression** (mandatory)
- **Code reduction** (nice to have)
- **Improved clarity** (subjective but important)
- **Easier to test** (fewer mocks needed)

---

## Summary

### Code Organization
- Single responsibility principle
- Separation of concerns
- Manager pattern for related operations
- Callback pattern to avoid circular dependencies

### Testing
- 100% pass rate on all tests
- >80% code coverage on new code
- Arrange-Act-Assert pattern
- Mock external dependencies

### Error Handling
- Custom exception hierarchy
- Try-except-finally for resource cleanup
- Retry with exponential backoff
- Graceful degradation

### Security
- Never hardcode credentials
- Secure password generation
- Proper file permissions
- Input validation
- API authentication

### Matrix Bridges
- Matrix Space organization
- Stable user IDs
- Room persistence and reuse
- Event deduplication
- Message queuing

### Performance
- Connection pooling
- Batch operations
- Caching
- Async/await best practices

### Refactoring
- Sprint-based approach
- Extract one manager at a time
- Maintain 100% test pass rate
- No functionality regression

---

## Additional Resources

- **Architecture**: `/docs/architecture/OVERVIEW.md`
- **Development Guide**: `/docs/process/DEVELOPMENT.md`
- **Testing Guide**: `/docs/operations/TESTING.md`
- **Contributing**: `/docs/process/CONTRIBUTING.md`
- **Changelog**: `/docs/process/CHANGELOG.md`

## References

- mautrix-discord: https://github.com/mautrix/discord
- Matrix Spec: https://spec.matrix.org/
- Matrix Bridge Guide: https://matrix.org/docs/guides/implementing-a-bridge
- Letta SDK: https://github.com/letta-ai/letta
