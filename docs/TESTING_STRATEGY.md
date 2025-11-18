# Testing Strategy: Database Testing with SQLite

This document outlines our comprehensive testing strategy for the Matrix-Letta integration, including both mock-based unit tests and SQLite-based integration tests.

## Table of Contents

1. [Overview](#overview)
2. [Current Implementation: Mock-Based Testing](#current-implementation-mock-based-testing)
3. [SQLite-Based Testing Strategy](#sqlite-based-testing-strategy)
4. [Migration Path](#migration-path)
5. [Test Patterns and Examples](#test-patterns-and-examples)
6. [CI/CD Integration](#cicd-integration)
7. [Best Practices](#best-practices)

---

## Overview

We use a **three-tier testing approach**:

1. **Mock-Based Unit Tests** - Fast, isolated tests with mocked database layer
2. **SQLite Integration Tests** - Real database operations using in-memory SQLite
3. **PostgreSQL Integration Tests** (Future) - Full system tests against real PostgreSQL

### Why This Approach?

- **Speed**: Mock tests run in milliseconds
- **Isolation**: Unit tests don't depend on database state
- **Realism**: SQLite tests verify actual SQL operations
- **CI/CD Friendly**: No external services required
- **Gradual Migration**: Can migrate tests one at a time

---

## Current Implementation: Mock-Based Testing

### Architecture

```
Test → mock_agent_mapping_db fixture → Mock DB instance
                                      ↓
                              Mocked methods:
                              - get_all()
                              - get_by_room_id()
                              - upsert()
                              - delete()
```

### Mock Fixture (`tests/conftest.py`)

```python
@pytest.fixture
def mock_agent_mapping_db():
    """Mock AgentMappingDB for unit tests"""
    # Create mock database instance
    db_instance = Mock()
    db_instance.get_all = Mock(return_value=[])
    db_instance.get_by_room_id = Mock(return_value=None)
    db_instance.upsert = Mock()

    # Inject into sys.modules
    mock_module = Mock()
    mock_module.AgentMappingDB = Mock(return_value=db_instance)
    sys.modules['src.models.agent_mapping'] = mock_module

    yield db_instance

    # Cleanup
    sys.modules.pop('src.models.agent_mapping', None)
```

### Usage Example

```python
@pytest.mark.asyncio
async def test_load_mappings(mock_config, mock_agent_mapping_db):
    # Configure mock response
    mock_db_mapping = Mock()
    mock_db_mapping.to_dict.return_value = {...}
    mock_agent_mapping_db.get_all.return_value = [mock_db_mapping]

    # Test
    manager = AgentUserManager(mock_config)
    await manager.load_existing_mappings()

    # Verify
    assert len(manager.mappings) == 1
    assert mock_agent_mapping_db.get_all.called
```

### Advantages
✅ Extremely fast (0.41s for 20 tests)
✅ No external dependencies
✅ Perfect isolation between tests
✅ Easy to test error conditions

### Limitations
❌ Doesn't test actual SQL queries
❌ Can't catch database constraint violations
❌ Mocks might diverge from real implementation

---

## SQLite-Based Testing Strategy

### Architecture

```
Test → SQLite Fixture → In-Memory SQLite Database
                       ↓
                Real SQLAlchemy Models
                       ↓
                Actual SQL Operations
```

### Core Components

#### 1. Database Engine Configuration

Modify `src/models/agent_mapping.py` to support SQLite:

```python
def get_engine():
    """Get database engine from environment or use SQLite for tests"""
    database_url = os.getenv('DATABASE_URL')

    if not database_url:
        # Default to SQLite in-memory for tests
        database_url = 'sqlite:///:memory:'

    # SQLite needs special connection args
    if database_url.startswith('sqlite'):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool  # Prevent connection pool issues
        )
    else:
        # PostgreSQL configuration
        return create_engine(database_url, pool_pre_ping=True)
```

#### 2. Test Fixtures (`tests/conftest.py`)

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from src.models.agent_mapping import Base, AgentMappingDB

@pytest.fixture(scope="session")
def sqlite_engine():
    """Create a SQLite engine for the test session"""
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False  # Set to True for SQL debugging
    )

    # Create all tables
    Base.metadata.create_all(engine)

    yield engine

    # Cleanup
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def sqlite_db(sqlite_engine):
    """
    Provide a clean database for each test.

    This fixture:
    - Creates a new transaction
    - Runs the test
    - Rolls back all changes after test
    """
    from src.models.agent_mapping import get_session_maker

    # Override DATABASE_URL to use SQLite
    import os
    old_url = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'

    # Create session
    Session = get_session_maker()
    session = Session()

    # Create database instance
    db = AgentMappingDB()

    yield db

    # Cleanup: rollback any changes
    session.rollback()
    session.close()

    # Restore original DATABASE_URL
    if old_url:
        os.environ['DATABASE_URL'] = old_url
    else:
        os.environ.pop('DATABASE_URL', None)


@pytest.fixture
def sqlite_db_with_data(sqlite_db):
    """
    Provide a database pre-populated with test data.

    Useful for tests that need existing data.
    """
    from src.models.agent_mapping import AgentMapping, InvitationStatus
    from datetime import datetime

    # Insert test data
    test_agent = AgentMapping(
        agent_id="test-agent-001",
        agent_name="TestAgent",
        matrix_user_id="@test:matrix.test",
        matrix_password="test_password",
        room_id="!testroom:matrix.test",
        room_created=True,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )

    sqlite_db.upsert(
        agent_id=test_agent.agent_id,
        agent_name=test_agent.agent_name,
        matrix_user_id=test_agent.matrix_user_id,
        matrix_password=test_agent.matrix_password,
        room_id=test_agent.room_id,
        room_created=test_agent.room_created
    )

    yield sqlite_db
```

#### 3. Test Markers

Add pytest markers for categorizing tests:

```python
# pytest.ini
[pytest]
markers =
    unit: Unit tests with mocked dependencies (fast)
    integration: Integration tests with real database (slower)
    requires_db: Tests that require a database connection
    sqlite: Tests using SQLite in-memory database
    postgres: Tests requiring PostgreSQL database
```

---

## Migration Path

### Phase 1: Set Up Infrastructure (Week 1)

**Tasks:**
1. Update `src/models/agent_mapping.py` to support SQLite
2. Add SQLite fixtures to `tests/conftest.py`
3. Add pytest markers
4. Create example SQLite test
5. Document patterns

**Deliverables:**
- Working SQLite test infrastructure
- 1-2 example tests
- Documentation

### Phase 2: Migrate Critical Tests (Week 2-3)

**Priority Order:**

1. **Database Model Tests** (`tests/integration/test_agent_mapping_db.py`)
   - Test CRUD operations
   - Test upsert logic
   - Test constraint violations
   - Test relationships (agent → invitations)

2. **Persistence Tests** (`tests/integration/test_agent_persistence.py`)
   - Load/save round-trip tests
   - Concurrent access tests
   - Transaction rollback tests

3. **Query Tests** (`tests/integration/test_agent_queries.py`)
   - get_by_room_id()
   - get_by_agent_id()
   - get_all() with large datasets
   - Search operations

### Phase 3: Comprehensive Coverage (Week 4+)

**Additional Test Suites:**

1. **Migration Tests** (`tests/integration/test_database_migrations.py`)
   - Schema creation
   - JSON to DB migration
   - Data integrity verification

2. **Performance Tests** (`tests/integration/test_database_performance.py`)
   - Bulk insert performance
   - Query optimization
   - Index effectiveness

3. **Edge Cases** (`tests/integration/test_database_edge_cases.py`)
   - Empty database handling
   - Null values
   - Unicode characters
   - Very long strings

### Phase 4: CI/CD Integration (Ongoing)

1. Update GitHub Actions workflows
2. Add coverage reporting
3. Set up test result tracking
4. Configure test parallelization

---

## Test Patterns and Examples

### Pattern 1: Basic CRUD Operations

```python
# tests/integration/test_agent_mapping_db.py

import pytest
from src.models.agent_mapping import AgentMapping

@pytest.mark.integration
@pytest.mark.sqlite
class TestAgentMappingCRUD:
    """Test CRUD operations with real database"""

    def test_create_agent_mapping(self, sqlite_db):
        """Test creating a new agent mapping"""
        # Create
        agent_id = "agent-create-test"
        sqlite_db.upsert(
            agent_id=agent_id,
            agent_name="CreateTest",
            matrix_user_id="@create:matrix.test",
            matrix_password="password123",
            room_id="!room:matrix.test",
            room_created=True
        )

        # Verify
        mapping = sqlite_db.get_by_agent_id(agent_id)
        assert mapping is not None
        assert mapping.agent_name == "CreateTest"
        assert mapping.room_id == "!room:matrix.test"

    def test_update_agent_mapping(self, sqlite_db_with_data):
        """Test updating an existing agent mapping"""
        # Update
        sqlite_db_with_data.upsert(
            agent_id="test-agent-001",
            agent_name="UpdatedName",
            matrix_user_id="@test:matrix.test",
            matrix_password="new_password",
            room_id="!newroom:matrix.test",
            room_created=True
        )

        # Verify
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert mapping.agent_name == "UpdatedName"
        assert mapping.room_id == "!newroom:matrix.test"

    def test_delete_agent_mapping(self, sqlite_db_with_data):
        """Test deleting an agent mapping"""
        # Delete
        sqlite_db_with_data.delete("test-agent-001")

        # Verify
        mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
        assert mapping is None

    def test_get_all_mappings(self, sqlite_db):
        """Test retrieving all mappings"""
        # Create multiple mappings
        for i in range(5):
            sqlite_db.upsert(
                agent_id=f"agent-{i}",
                agent_name=f"Agent{i}",
                matrix_user_id=f"@agent{i}:matrix.test",
                matrix_password="password",
                room_id=f"!room{i}:matrix.test",
                room_created=True
            )

        # Verify
        all_mappings = sqlite_db.get_all()
        assert len(all_mappings) == 5
```

### Pattern 2: Query Operations

```python
@pytest.mark.integration
@pytest.mark.sqlite
class TestAgentQueries:
    """Test query operations"""

    def test_get_by_room_id(self, sqlite_db_with_data):
        """Test finding agent by room ID"""
        mapping = sqlite_db_with_data.get_by_room_id("!testroom:matrix.test")

        assert mapping is not None
        assert mapping.agent_id == "test-agent-001"
        assert mapping.agent_name == "TestAgent"

    def test_get_by_nonexistent_room(self, sqlite_db):
        """Test querying for non-existent room"""
        mapping = sqlite_db.get_by_room_id("!nonexistent:matrix.test")
        assert mapping is None

    def test_room_uniqueness_constraint(self, sqlite_db):
        """Test that room_id is unique"""
        # Create first mapping
        sqlite_db.upsert(
            agent_id="agent-1",
            agent_name="Agent1",
            matrix_user_id="@agent1:matrix.test",
            matrix_password="pass1",
            room_id="!unique:matrix.test",
            room_created=True
        )

        # Try to create second mapping with same room_id
        # This should raise an integrity error or update the existing one
        # depending on upsert implementation
        with pytest.raises(IntegrityError):
            sqlite_db.upsert(
                agent_id="agent-2",
                agent_name="Agent2",
                matrix_user_id="@agent2:matrix.test",
                matrix_password="pass2",
                room_id="!unique:matrix.test",
                room_created=True
            )
```

### Pattern 3: Relationship Tests

```python
@pytest.mark.integration
@pytest.mark.sqlite
class TestAgentInvitations:
    """Test agent-invitation relationships"""

    def test_create_invitation_status(self, sqlite_db_with_data):
        """Test creating invitation statuses for an agent"""
        from src.models.agent_mapping import InvitationStatus, get_session_maker

        Session = get_session_maker()
        session = Session()

        try:
            # Create invitation statuses
            inv1 = InvitationStatus(
                agent_id="test-agent-001",
                invitee="@admin:matrix.test",
                status="joined"
            )
            inv2 = InvitationStatus(
                agent_id="test-agent-001",
                invitee="@user:matrix.test",
                status="invited"
            )

            session.add(inv1)
            session.add(inv2)
            session.commit()

            # Verify through relationship
            agent = sqlite_db_with_data.get_by_agent_id("test-agent-001")
            assert len(agent.invitations) == 2

            # Verify statuses
            statuses = {inv.invitee: inv.status for inv in agent.invitations}
            assert statuses["@admin:matrix.test"] == "joined"
            assert statuses["@user:matrix.test"] == "invited"

        finally:
            session.close()
```

### Pattern 4: Concurrent Access

```python
@pytest.mark.integration
@pytest.mark.sqlite
class TestConcurrentAccess:
    """Test concurrent database access"""

    @pytest.mark.asyncio
    async def test_concurrent_upserts(self, sqlite_db):
        """Test multiple concurrent upsert operations"""
        import asyncio

        async def upsert_agent(agent_num):
            """Upsert an agent mapping"""
            sqlite_db.upsert(
                agent_id=f"agent-{agent_num}",
                agent_name=f"ConcurrentAgent{agent_num}",
                matrix_user_id=f"@agent{agent_num}:matrix.test",
                matrix_password="password",
                room_id=f"!room{agent_num}:matrix.test",
                room_created=True
            )

        # Run 10 concurrent upserts
        await asyncio.gather(*[upsert_agent(i) for i in range(10)])

        # Verify all were created
        all_mappings = sqlite_db.get_all()
        assert len(all_mappings) == 10
```

### Pattern 5: Data Migration Testing

```python
@pytest.mark.integration
@pytest.mark.sqlite
class TestDataMigration:
    """Test JSON to database migration"""

    def test_migrate_from_json(self, sqlite_db, tmp_path):
        """Test migrating data from JSON file to database"""
        import json
        from scripts.migration.migrate_json_to_db import migrate_json_to_db

        # Create test JSON file
        json_file = tmp_path / "test_mappings.json"
        test_data = {
            "agent-001": {
                "agent_id": "agent-001",
                "agent_name": "MigrationTest",
                "matrix_user_id": "@test:matrix.test",
                "matrix_password": "password",
                "room_id": "!room:matrix.test",
                "room_created": True,
                "invitation_status": {
                    "@admin:matrix.test": "joined"
                }
            }
        }

        with open(json_file, 'w') as f:
            json.dump(test_data, f)

        # Run migration
        success = migrate_json_to_db(str(json_file), dry_run=False)
        assert success

        # Verify data in database
        mapping = sqlite_db.get_by_agent_id("agent-001")
        assert mapping is not None
        assert mapping.agent_name == "MigrationTest"
        assert len(mapping.invitations) == 1
```

---

## CI/CD Integration

### GitHub Actions Workflow

Update `.github/workflows/tests.yml`:

```yaml
name: Test Suite

on:
  push:
    branches: [ main, develop, feature/*, claude/* ]
  pull_request:
    branches: [ main, develop ]

jobs:
  unit-tests:
    name: Unit Tests (Mock DB)
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
    - name: Checkout code
      uses: actions/checkout@v3

    - name: Set up Python 3.11
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run unit tests
      env:
        MATRIX_DATA_DIR: ${{ runner.temp }}/matrix_data
      run: |
        mkdir -p $MATRIX_DATA_DIR
        pytest -m unit -v --tb=short

  integration-tests-sqlite:
    name: Integration Tests (SQLite)
    runs-on: ubuntu-latest
    needs: unit-tests
    timeout-minutes: 10

    steps:
    - name: Checkout code
      uses: actions/checkout@v3

    - name: Set up Python 3.11
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run SQLite integration tests
      env:
        MATRIX_DATA_DIR: ${{ runner.temp }}/matrix_data
        DATABASE_URL: sqlite:///:memory:
      run: |
        mkdir -p $MATRIX_DATA_DIR
        pytest -m "integration and sqlite" -v --tb=short

    - name: Upload test results
      if: always()
      uses: actions/upload-artifact@v4
      with:
        name: sqlite-test-results
        path: |
          .pytest_cache/
          test-results.xml

  integration-tests-postgres:
    name: Integration Tests (PostgreSQL)
    runs-on: ubuntu-latest
    needs: integration-tests-sqlite
    timeout-minutes: 15

    services:
      postgres:
        image: pgvector/pgvector:pg15
        env:
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_pass
          POSTGRES_DB: test_matrix_letta
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
    - name: Checkout code
      uses: actions/checkout@v3

    - name: Set up Python 3.11
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run PostgreSQL integration tests
      env:
        MATRIX_DATA_DIR: ${{ runner.temp }}/matrix_data
        DATABASE_URL: postgresql://test_user:test_pass@localhost:5432/test_matrix_letta
      run: |
        mkdir -p $MATRIX_DATA_DIR
        pytest -m "integration and postgres" -v --tb=short

  coverage:
    name: Code Coverage
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests-sqlite]
    timeout-minutes: 15

    steps:
    - name: Checkout code
      uses: actions/checkout@v3

    - name: Set up Python 3.11
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt

    - name: Run tests with coverage
      env:
        MATRIX_DATA_DIR: ${{ runner.temp }}/matrix_data
        DATABASE_URL: sqlite:///:memory:
      run: |
        mkdir -p $MATRIX_DATA_DIR
        pytest --cov=src \
               --cov-config=pytest.ini \
               --cov-report=xml \
               --cov-report=html \
               --cov-report=term-missing \
               -m "unit or (integration and sqlite)"

    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
        flags: unittests
        name: codecov-letta-matrix
```

### Run Tests Locally

```bash
# Run only unit tests (fast)
pytest -m unit -v

# Run only SQLite integration tests
pytest -m "integration and sqlite" -v

# Run all tests except PostgreSQL
pytest -m "not postgres" -v

# Run with coverage
pytest --cov=src --cov-report=html -m "unit or (integration and sqlite)"
```

---

## Best Practices

### 1. Test Isolation

**Always clean up after tests:**

```python
@pytest.fixture
def clean_database(sqlite_db):
    """Ensure database is clean before and after test"""
    # Setup: clear any existing data
    from src.models.agent_mapping import get_session_maker, AgentMapping

    Session = get_session_maker()
    session = Session()
    session.query(AgentMapping).delete()
    session.commit()
    session.close()

    yield sqlite_db

    # Teardown: clear data created by test
    session = Session()
    session.query(AgentMapping).delete()
    session.commit()
    session.close()
```

### 2. Test Data Factories

**Use factories for consistent test data:**

```python
# tests/factories.py

from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class AgentMappingFactory:
    """Factory for creating test agent mappings"""

    @staticmethod
    def create(
        agent_id: str = "test-agent",
        agent_name: str = "TestAgent",
        matrix_user_id: Optional[str] = None,
        room_id: Optional[str] = None,
        **kwargs
    ):
        """Create an agent mapping with sensible defaults"""
        if matrix_user_id is None:
            matrix_user_id = f"@{agent_id.replace('-', '_')}:matrix.test"

        if room_id is None:
            room_id = f"!{agent_id}:matrix.test"

        return {
            "agent_id": agent_id,
            "agent_name": agent_name,
            "matrix_user_id": matrix_user_id,
            "matrix_password": "test_password",
            "room_id": room_id,
            "room_created": True,
            **kwargs
        }

    @staticmethod
    def create_batch(count: int, **kwargs):
        """Create multiple agent mappings"""
        return [
            AgentMappingFactory.create(
                agent_id=f"agent-{i}",
                agent_name=f"Agent{i}",
                **kwargs
            )
            for i in range(count)
        ]

# Usage in tests:
def test_bulk_operations(sqlite_db):
    mappings = AgentMappingFactory.create_batch(100)
    for mapping in mappings:
        sqlite_db.upsert(**mapping)

    assert len(sqlite_db.get_all()) == 100
```

### 3. Parameterized Tests

**Test multiple scenarios with one test:**

```python
@pytest.mark.parametrize("room_id,expected_agent", [
    ("!room1:matrix.test", "agent-001"),
    ("!room2:matrix.test", "agent-002"),
    ("!nonexistent:matrix.test", None),
])
def test_get_by_room_id_scenarios(sqlite_db_with_data, room_id, expected_agent):
    """Test various room_id lookup scenarios"""
    mapping = sqlite_db_with_data.get_by_room_id(room_id)

    if expected_agent is None:
        assert mapping is None
    else:
        assert mapping.agent_id == expected_agent
```

### 4. Performance Assertions

**Ensure operations complete within time limits:**

```python
import time

def test_bulk_insert_performance(sqlite_db):
    """Test that bulk insert completes in reasonable time"""
    mappings = AgentMappingFactory.create_batch(1000)

    start = time.time()
    for mapping in mappings:
        sqlite_db.upsert(**mapping)
    elapsed = time.time() - start

    # Should complete in less than 5 seconds
    assert elapsed < 5.0, f"Bulk insert took {elapsed:.2f}s (expected < 5s)"
    assert len(sqlite_db.get_all()) == 1000
```

### 5. Error Condition Testing

**Test failure modes explicitly:**

```python
def test_database_constraint_violations(sqlite_db):
    """Test that database enforces constraints"""
    from sqlalchemy.exc import IntegrityError

    # Test 1: Duplicate agent_id (primary key)
    sqlite_db.upsert(agent_id="test", agent_name="Test1", ...)

    # Upsert should handle this gracefully (update not error)
    sqlite_db.upsert(agent_id="test", agent_name="Test2", ...)
    mapping = sqlite_db.get_by_agent_id("test")
    assert mapping.agent_name == "Test2"

    # Test 2: Null required field
    with pytest.raises(IntegrityError):
        sqlite_db.upsert(agent_id="test2", agent_name=None, ...)
```

---

## Troubleshooting

### Common Issues and Solutions

#### Issue 1: "database is locked"

**Cause:** SQLite doesn't handle concurrent writes well

**Solution:**
```python
# Use StaticPool and check_same_thread=False
engine = create_engine(
    'sqlite:///:memory:',
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
```

#### Issue 2: "table already exists"

**Cause:** Fixture scope mismatch

**Solution:**
```python
# Use session-scoped engine, function-scoped database
@pytest.fixture(scope="session")
def sqlite_engine():
    ...

@pytest.fixture
def sqlite_db(sqlite_engine):
    ...
```

#### Issue 3: Tests affect each other

**Cause:** Database state persists between tests

**Solution:**
```python
# Use transactions or clean database between tests
@pytest.fixture
def sqlite_db(sqlite_engine):
    ...
    yield db
    # Rollback or delete all data
    session.rollback()
```

---

## Summary

### Quick Reference

| Test Type | Speed | Isolation | Realism | Use Case |
|-----------|-------|-----------|---------|----------|
| Mock | ⚡⚡⚡ | ✅✅✅ | ❌ | Unit logic testing |
| SQLite | ⚡⚡ | ✅✅ | ✅✅ | DB operations, CI |
| PostgreSQL | ⚡ | ✅ | ✅✅✅ | Full integration |

### Decision Tree

```
Need to test database operations?
├─ No → Use mock-based unit tests
└─ Yes
   ├─ Testing query logic/constraints? → Use SQLite
   ├─ Testing PostgreSQL-specific features? → Use PostgreSQL
   └─ Testing full system integration? → Use PostgreSQL
```

### Next Steps

1. ✅ Mock infrastructure in place (completed)
2. ⏳ Set up SQLite infrastructure (this document)
3. ⏳ Migrate 5-10 tests to SQLite
4. ⏳ Evaluate performance and coverage
5. ⏳ Plan PostgreSQL integration tests
6. ⏳ Update CI/CD pipeline

---

## References

- [SQLAlchemy Testing Documentation](https://docs.sqlalchemy.org/en/20/core/testing.html)
- [Pytest Fixtures Guide](https://docs.pytest.org/en/stable/fixture.html)
- [SQLite In-Memory Databases](https://www.sqlite.org/inmemorydb.html)
- [Testing Best Practices](https://docs.python-guide.org/writing/tests/)
