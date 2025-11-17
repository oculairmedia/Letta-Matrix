# Testing Guide

Comprehensive testing documentation for the Letta-Matrix integration project.

## Quick Reference

### Run Tests

```bash
# Quick smoke tests (5s) - Run before every commit
./run_tests.sh smoke

# Unit tests (30s) - Run during development
./run_tests.sh unit

# Integration tests (2min) - Run before merging
./run_tests.sh integration

# All tests with coverage - Run before releasing
./run_tests.sh coverage
open htmlcov/index.html

# Everything
./run_tests.sh all

# Watch mode (auto-rerun on changes)
./run_tests.sh watch
```

### Common Pytest Commands

```bash
# Specific test file
pytest tests/unit/test_agent_user_manager.py

# Specific test function
pytest tests/unit/test_agent_user_manager.py::TestAgentUserMapping::test_mapping_creation

# Tests matching pattern
pytest -k "agent_creation"

# Tests with marker
pytest -m unit
pytest -m "smoke or unit"
pytest -m integration

# Stop on first failure
pytest -x

# Show prints
pytest -s

# Verbose output
pytest -v

# Debug on failure
pytest --pdb

# Run last failed tests
pytest --lf

# Parallel execution (requires pytest-xdist)
pytest -n auto
```

### Test Markers

```python
@pytest.mark.smoke          # Quick validation (5s)
@pytest.mark.unit           # Unit tests (30s)
@pytest.mark.integration    # Integration tests (2min)
@pytest.mark.slow           # Long-running tests (>10s)
@pytest.mark.asyncio        # Async tests
@pytest.mark.requires_matrix # Requires Matrix homeserver
@pytest.mark.requires_letta  # Requires Letta instance
```

## Test Structure

```
tests/
├── __init__.py                           # Test package initialization
├── conftest.py                           # Shared fixtures and configuration
├── pytest.ini                            # Pytest configuration
├── test_smoke.py                         # Quick smoke tests
├── unit/                                 # Unit tests
│   ├── __init__.py
│   ├── test_agent_user_manager.py       # AgentUserManager tests
│   ├── test_custom_matrix_client.py     # Matrix client tests
│   └── test_matrix_api.py               # FastAPI endpoint tests
└── integration/                          # Integration tests
    ├── __init__.py
    └── test_multi_agent_workflow.py     # End-to-end workflow tests
```

### Test Files

**Core Test Suites**:
- `test_space_management.py` - Space organization and hierarchy (12 tests)
- `test_agent_routing.py` - Agent routing and message delivery (6 tests)
- `test_agent_response_identity.py` - Agent identity verification (7 tests)
- `tests/test_smoke.py` - Basic import and instantiation (~10 tests)
- `tests/unit/test_agent_user_manager.py` - AgentUserManager unit tests (~40 tests)
- `tests/unit/test_custom_matrix_client.py` - Matrix client tests (~40 tests)
- `tests/unit/test_matrix_api.py` - API endpoint tests (~30 tests)
- `tests/integration/test_multi_agent_workflow.py` - E2E workflows (~30 tests)

**Total**: ~150+ tests covering all major components

## Test Categories

### 1. Smoke Tests (~10 tests, ~5 seconds)

**Purpose**: Quick validation that basic functionality works

**Coverage**:
- Module imports work
- Dataclasses can be instantiated
- Basic configuration loads
- No syntax errors

**When to Run**: Before every commit

```bash
./run_tests.sh smoke
```

**Example Tests**:
```python
@pytest.mark.smoke
def test_imports():
    """Verify all modules can be imported"""
    import agent_user_manager
    import custom_matrix_client
    import matrix_api
    assert True

@pytest.mark.smoke
def test_agent_mapping_creation():
    """Verify AgentUserMapping can be instantiated"""
    from agent_user_manager import AgentUserMapping
    mapping = AgentUserMapping(
        agent_id="test-001",
        agent_name="Test Agent",
        matrix_user_id="@test:matrix.test",
        matrix_password="password",
        room_id="!test:matrix.test",
        created=True,
        room_created=True
    )
    assert mapping.agent_id == "test-001"
```

### 2. Unit Tests (~90 tests, ~30 seconds)

**Purpose**: Test individual functions/methods in isolation

**Coverage Areas**:
- AgentUserManager methods
- Matrix client operations
- FastAPI endpoints
- Configuration handling
- Error handling
- Space management
- Agent routing
- Response identity

**When to Run**: During development, before pull requests

```bash
./run_tests.sh unit
```

**Example Test Structure**:
```python
@pytest.mark.unit
class TestAgentUserMapping:
    """Test AgentUserMapping dataclass"""

    def test_mapping_creation(self):
        """Test creating agent mapping"""
        # Arrange
        mapping = AgentUserMapping(
            agent_id="agent-001",
            agent_name="Test Agent",
            matrix_user_id="@agent_001:matrix.test",
            matrix_password="password",
            room_id="!room:matrix.test",
            created=True,
            room_created=True
        )

        # Assert
        assert mapping.agent_id == "agent-001"
        assert mapping.agent_name == "Test Agent"
        assert mapping.created is True

    def test_to_dict(self):
        """Test conversion to dictionary"""
        # Arrange
        mapping = AgentUserMapping(...)

        # Act
        data = mapping.to_dict()

        # Assert
        assert data["agent_id"] == "agent-001"
        assert isinstance(data, dict)
```

### 3. Integration Tests (~50 tests, ~2 minutes)

**Purpose**: Test component interactions and workflows

**Coverage Areas**:
- Agent discovery → user creation → room setup
- Multi-agent message routing
- Agent name updates
- Room persistence
- Concurrent operations
- Space creation and management
- Room-to-space hierarchy
- Agent response identity

**When to Run**: Before merging, in CI/CD

```bash
./run_tests.sh integration
```

**Example Workflow Test**:
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_agent_workflow(mock_config):
    """Test complete multi-agent workflow"""
    # Arrange
    manager = AgentUserManager(mock_config)

    # Act - Discover agents
    agents = await manager.discover_agents()

    # Act - Create users and rooms
    for agent in agents:
        await manager.process_agent(agent)

    # Assert - Verify mappings created
    assert len(manager.mappings) == len(agents)

    # Assert - Verify rooms created
    for mapping in manager.mappings.values():
        assert mapping.room_created is True
        assert mapping.room_id is not None
```

### 4. Space Management Tests (12 tests, ~1 second)

**Purpose**: Test Matrix Space organization features

**Coverage**:
- Space creation and persistence
- Room addition to space
- Bidirectional parent-child relationships
- Migration of existing rooms
- Edge cases and error handling

**Run Specific Tests**:
```bash
# All space tests
pytest test_space_management.py -v

# Specific test
pytest test_space_management.py::TestSpaceManagement::test_space_creation -v

# With coverage
pytest test_space_management.py --cov=agent_user_manager --cov-report=html
```

**Key Tests**:
```python
@pytest.mark.unit
class TestSpaceManagement:
    async def test_space_creation(self):
        """Verify space is created correctly"""

    async def test_space_persistence(self):
        """Confirm space config is saved/loaded"""

    async def test_add_room_to_space(self):
        """Test adding rooms to space"""

    async def test_migrate_existing_rooms(self):
        """Migrate existing rooms to space"""

    async def test_room_to_space_bidirectional_relationship(self):
        """Test m.space.child and m.space.parent"""
```

### 5. Agent Routing Tests (6 tests, ~0.5 seconds)

**Purpose**: Prevent agent routing regression bugs

**Critical Tests**:
```bash
# Run routing tests
pytest test_agent_routing.py -v

# Run specific critical test
pytest test_agent_routing.py::test_correct_agent_routing_for_meridian_room -v
```

**Coverage**:
- Correct agent routing for specific rooms
- No fallback to first agent
- Routing with 50+ agents
- Direct HTTP API calls
- Room mapping integrity
- No Letta SDK imports (static analysis)

**Example**:
```python
def test_correct_agent_routing_for_meridian_room():
    """Test the exact bug scenario - Meridian room routes to Meridian agent"""
    mappings = {
        "agent-597b5756-2915-4560-ba6b-91005f085166": {
            "agent_id": "agent-597b5756-2915-4560-ba6b-91005f085166",
            "agent_name": "Meridian",
            "room_id": "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
        },
        "agent-7659b796-d1ee-4c2d-9915-f676ee94667f": {
            "agent_id": "agent-7659b796-d1ee-4c2d-9915-f676ee94667f",
            "agent_name": "Personal Site",
            "room_id": "!different_room:matrix.oculair.ca"
        }
    }

    # Find agent for Meridian's room
    room_id = "!8I9YBvbr4KpXNedbph:matrix.oculair.ca"
    agent = find_agent_by_room_id(room_id, mappings)

    # Assert routing is correct
    assert agent["agent_id"] == "agent-597b5756-2915-4560-ba6b-91005f085166"
    assert agent["agent_name"] == "Meridian"
```

### 6. Agent Identity Tests (7 tests, ~0.6 seconds)

**Purpose**: Ensure agents respond with their own Matrix identity

**Run Tests**:
```bash
# All identity tests
pytest test_agent_response_identity.py -v

# Combined with routing tests
pytest test_agent_routing.py test_agent_response_identity.py -v
```

**Coverage**:
- Agent uses correct Matrix user for responses
- Different agents use different identities
- PUT method with transaction ID
- Login failure handling
- Missing room mapping handling
- Message content structure
- Agent mapping structure validation

## Writing Tests

### Test Structure (Arrange-Act-Assert)

```python
@pytest.mark.unit
class TestFeature:
    """Test description"""

    @pytest.mark.asyncio
    async def test_specific_behavior(self, mock_config):
        """Test a specific behavior"""
        # Arrange - Set up test data
        manager = AgentUserManager(mock_config)
        agent = {"id": "test-001", "name": "Test Agent"}

        # Act - Perform the operation
        result = await manager.process_agent(agent)

        # Assert - Verify the outcome
        assert result.success is True
        assert "test-001" in manager.mappings
```

### Using Fixtures

Fixtures are defined in `tests/conftest.py`:

```python
def test_with_fixtures(mock_config, mock_aiohttp_session, tmp_path):
    """Test using shared fixtures"""
    # mock_config - Mock configuration object
    # mock_aiohttp_session - Mock HTTP session
    # tmp_path - Temporary directory for file operations
    pass
```

**Available Fixtures**:
- `mock_config` - Standard test configuration
- `temp_data_dir` - Temporary data directory
- `mock_aiohttp_session` - Mock HTTP sessions
- `mock_nio_client` - Mock Matrix nio client
- `mock_letta_client` - Mock Letta client
- `sample_agent_data` - Single agent test data
- `sample_agents_list` - Multiple agents
- `sample_agent_mapping` - Agent-user mapping
- `mock_matrix_login_response` - Login data
- `mock_matrix_messages` - Message data
- `mock_letta_response` - Letta responses
- `mock_mappings_file` - Temporary mappings file
- `tmp_path` - pytest built-in temporary directory

### Mocking External Dependencies

```python
from unittest.mock import Mock, AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mocks(mock_aiohttp_session):
    """Test with mocked HTTP responses"""
    # Mock response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"success": True})

    # Use in test
    mock_aiohttp_session.get = Mock(return_value=mock_response)

    # Test code that uses the session
    result = await fetch_data(mock_aiohttp_session)
    assert result["success"] is True
```

### Async Tests

Use `@pytest.mark.asyncio` for async functions:

```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test asynchronous operation"""
    result = await some_async_function()
    assert result is not None
```

### Parametrized Tests

Test multiple scenarios with one test function:

```python
@pytest.mark.parametrize("agent_id,expected_username", [
    ("agent-001", "@agent_001:matrix.test"),
    ("agent-abc-123", "@agent_abc_123:matrix.test"),
    ("agent_special", "@agent_special:matrix.test")
])
def test_username_generation(agent_id, expected_username):
    """Test username generation for various agent IDs"""
    result = generate_username(agent_id)
    assert result == expected_username
```

## Coverage Reports

### Generating Coverage

```bash
# HTML report (best for viewing)
./run_tests.sh coverage
open htmlcov/index.html

# Terminal report
pytest --cov=. --cov-report=term-missing

# XML report (for CI/CD)
pytest --cov=. --cov-report=xml

# Coverage for specific module
pytest --cov=agent_user_manager tests/unit/test_agent_user_manager.py
```

### Coverage Goals

| Component | Target | Critical Paths |
|-----------|--------|----------------|
| agent_user_manager.py | 85% | 95% |
| custom_matrix_client.py | 80% | 90% |
| matrix_api.py | 85% | 95% |
| Overall | 80% | - |

**Critical Paths** (95%+ coverage required):
- Agent user creation
- Message routing
- Authentication
- Room management
- Space organization

**Acceptable Lower Coverage**:
- Error handling branches
- Logging statements
- Type checking code

## Production Verification

### Agent Routing Verification

```bash
# Check logs for correct routing
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | \
  grep "AGENT ROUTING" | tail -5

# Expected: Room !8I9YBvbr4KpXNedbph -> Agent agent-597b5756...
# Should NOT show: agent-7659b796 (wrong agent)
```

### Agent Identity Verification

```bash
# Check agent identity in responses
docker logs matrix-synapse-deployment-matrix-client-1 2>&1 | \
  grep "SEND_AS_AGENT" | tail -10

# Expected: Successfully sent message as Meridian (agent_597b5756...)
# Expected: sent_as_agent: true
```

### Quick Health Check

```bash
# Container status
docker ps | grep matrix-client

# Recent logs
docker logs matrix-synapse-deployment-matrix-client-1 --tail 20

# Agent mappings
cat matrix_client_data/agent_user_mappings.json | \
  jq '.[] | select(.agent_name == "Meridian")'
```

## Manual Integration Testing

### Prerequisites
1. Running Matrix homeserver (Tuwunel)
2. Admin credentials configured in .env
3. At least one Letta agent available

### Test Procedure

#### 1. Initial Space Creation
```bash
# Start the matrix-client service
docker-compose up -d matrix-client

# Check logs for space creation
docker logs matrix-client 2>&1 | grep "Creating Letta Agents space"

# Expected output:
# [AGENT_SYNC] Creating Letta Agents space
# [AGENT_SYNC] Successfully created Letta Agents space: !AbCdEfG:matrix.oculair.ca
```

#### 2. Verify Space in Element
1. Open Element web client
2. Click on the spaces icon (left sidebar)
3. Look for "Letta Agents" space
4. Join the space
5. Verify all agent rooms appear as children

#### 3. Test New Agent Addition
```bash
# Create a new agent in Letta (via UI or API)

# Wait for sync (0.5 seconds)
# Check logs
docker logs matrix-client 2>&1 | tail -20

# Expected:
# Processing agent: NewAgent (agent-xyz)
# Created room !RoomId:matrix.oculair.ca for agent NewAgent
# Adding room !RoomId:matrix.oculair.ca to Letta Agents space
# Successfully added room to space
```

#### 4. Test Message Routing
1. Send message to an agent room in Element
2. Verify agent responds within 5 seconds
3. Check response comes from agent user (not @letta)
4. Verify logs show correct routing

## CI/CD Integration

### GitHub Actions Workflow

```yaml
# .github/workflows/tests.yml
name: Tests

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main, develop ]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run smoke tests
      run: ./run_tests.sh smoke

    - name: Run unit tests
      run: ./run_tests.sh unit

    - name: Run integration tests
      run: ./run_tests.sh integration

    - name: Generate coverage report
      run: pytest --cov=. --cov-report=xml --cov-report=html

    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        fail_ci_if_error: false

    - name: Upload coverage HTML
      uses: actions/upload-artifact@v3
      with:
        name: coverage-report
        path: htmlcov/
```

## Troubleshooting

### Common Issues

#### Import Errors

**Problem**: `ModuleNotFoundError: No module named 'agent_user_manager'`

**Solution**: Ensure you're running tests from the project root:
```bash
cd /home/user/Letta-Matrix
pytest
```

#### Async Test Errors

**Problem**: `RuntimeWarning: coroutine was never awaited`

**Solution**: Add `@pytest.mark.asyncio` decorator:
```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_operation()
```

#### Fixture Not Found

**Problem**: `fixture 'mock_config' not found`

**Solution**: Ensure test structure is correct:
```
tests/
├── conftest.py      # ← Fixtures defined here
└── unit/
    └── test_file.py  # ← Tests using fixtures
```

#### Coverage Not Generated

**Problem**: Coverage report is empty or shows 0%

**Solution**:
```bash
# Install pytest-cov
pip install pytest-cov

# Run from project root
cd /home/user/Letta-Matrix
pytest --cov=.
```

#### Tests Hang or Timeout

**Problem**: Tests never complete

**Solution**:
```python
# Use timeout decorator
@pytest.mark.timeout(10)  # 10 second timeout
def test_something():
    pass
```

### Debug Mode

```bash
# Show print statements
pytest -s

# Verbose output
pytest -v

# Show local variables on failure
pytest -l

# Drop into debugger on failure
pytest --pdb

# Combined
pytest -vsl --pdb
```

## Best Practices

### DO

- Write tests before refactoring
- Use descriptive test names
- Test one thing per test
- Use fixtures for common setup
- Mock external dependencies
- Run smoke tests frequently
- Aim for >80% coverage
- Keep tests fast (<100ms for unit tests)
- Use markers to categorize tests
- Document complex test scenarios

### DON'T

- Test implementation details
- Write flaky tests
- Skip writing tests for "simple" code
- Commit failing tests
- Make tests depend on each other
- Use sleep() instead of proper async handling
- Ignore test warnings
- Test external services directly
- Hardcode file paths or URLs
- Let coverage drop below 70%

## Test Maintenance

### Adding New Tests

When adding new functionality:
1. Write smoke test first
2. Add unit tests for new functions
3. Add integration test for workflows
4. Update coverage to maintain >80%
5. Document any new fixtures

### Refactoring with Tests

When refactoring:
1. Run full test suite before starting
2. Keep tests passing (green) during refactor
3. Add tests for edge cases discovered
4. Update tests if behavior changes
5. Run coverage to ensure no regression

### Regular Maintenance

- Remove obsolete tests
- Update mocks when APIs change
- Consolidate duplicate test code
- Speed up slow tests
- Fix flaky tests immediately
- Update documentation

## Success Indicators

### Test Suite Health
- All tests passing (150+/150+)
- Coverage >80% overall
- Critical paths >95% coverage
- No flaky tests
- Fast execution (<3 minutes total)

### Production Health
- Correct agent routing in logs
- Agents respond with own identity
- No SDK imports detected
- Healthy service status
- Space organization working

## Resources

### Documentation
- [Pytest Documentation](https://docs.pytest.org/)
- [Pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [Pytest-cov](https://pytest-cov.readthedocs.io/)
- [unittest.mock](https://docs.python.org/3/library/unittest.mock.html)

### Related Documentation
- **Deployment**: docs/operations/DEPLOYMENT.md
- **CI/CD**: docs/operations/CI_CD.md
- **Troubleshooting**: docs/operations/TROUBLESHOOTING.md
- **Architecture**: docs/architecture/OVERVIEW.md

---

**Last Updated**: 2025-01-17
**Version**: 1.0
**Maintainers**: OculairMedia Development Team
