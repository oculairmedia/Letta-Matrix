"""
Pytest configuration and shared fixtures for Letta-Matrix tests
"""
import pytest
import asyncio
import aiohttp
import json
import tempfile
import os
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from typing import Dict, Any
from dataclasses import dataclass

# Import components to test
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture
def mock_config():
    """Mock configuration object for testing"""
    @dataclass
    class MockConfig:
        homeserver_url: str = "http://test-synapse:8008"
        username: str = "@test:matrix.test"
        password: str = "test_password"
        room_id: str = "!testroom:matrix.test"
        letta_api_url: str = "http://test-letta:8283"
        letta_token: str = "test_token"
        letta_agent_id: str = "test-agent-id"
        log_level: str = "INFO"
        matrix_api_url: str = "http://test-matrix-api:8000"

    return MockConfig()


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory for testing"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create data subdirectory
        data_dir = os.path.join(tmpdir, "data")
        os.makedirs(data_dir, exist_ok=True)

        # Patch the data directory path
        with patch.dict(os.environ, {"DATA_DIR": data_dir}):
            yield data_dir


# ============================================================================
# Mock HTTP Session Fixtures
# ============================================================================

@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp ClientSession for HTTP requests"""
    # Don't use spec= because aiohttp.ClientSession may already be mocked
    session = AsyncMock()

    # Mock response object
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={"success": True})
    response.text = AsyncMock(return_value="OK")
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)

    # Mock session methods
    session.post = Mock(return_value=response)
    session.get = Mock(return_value=response)
    session.put = Mock(return_value=response)
    session.delete = Mock(return_value=response)
    session.closed = False

    return session


# ============================================================================
# Agent Data Fixtures
# ============================================================================

@pytest.fixture
def sample_agent_data():
    """Sample agent data from Letta API"""
    return {
        "id": "agent-12345",
        "name": "TestAgent",
        "created_at": "2025-01-01T00:00:00Z"
    }


@pytest.fixture
def sample_agents_list():
    """Sample list of multiple agents"""
    return [
        {"id": "agent-001", "name": "Agent Alpha"},
        {"id": "agent-002", "name": "Agent Beta"},
        {"id": "agent-003", "name": "Agent Gamma"}
    ]


@pytest.fixture
def sample_agent_mapping():
    """Sample AgentUserMapping data"""
    return {
        "agent_id": "agent-12345",
        "agent_name": "TestAgent",
        "matrix_user_id": "@agent_12345:matrix.test",
        "matrix_password": "test_password",
        "created": True,
        "room_id": "!testroom123:matrix.test",
        "room_created": True,
        "invitation_status": {
            "@admin:matrix.test": "joined",
            "@letta:matrix.test": "joined"
        }
    }


# ============================================================================
# Matrix API Fixtures
# ============================================================================

@pytest.fixture
def mock_matrix_login_response():
    """Mock Matrix login response"""
    return {
        "user_id": "@test:matrix.test",
        "access_token": "test_access_token_12345",
        "device_id": "test_device",
        "home_server": "matrix.test"
    }


@pytest.fixture
def mock_matrix_room_create_response():
    """Mock Matrix room creation response"""
    return {
        "room_id": "!newroom123:matrix.test"
    }


@pytest.fixture
def mock_matrix_messages():
    """Mock Matrix room messages"""
    return {
        "chunk": [
            {
                "type": "m.room.message",
                "sender": "@user:matrix.test",
                "content": {
                    "msgtype": "m.text",
                    "body": "Hello from test"
                },
                "event_id": "$event123",
                "origin_server_ts": 1704067200000
            },
            {
                "type": "m.room.message",
                "sender": "@agent:matrix.test",
                "content": {
                    "msgtype": "m.text",
                    "body": "Agent response"
                },
                "event_id": "$event124",
                "origin_server_ts": 1704067201000
            }
        ],
        "start": "t1",
        "end": "t2"
    }


# ============================================================================
# Letta API Fixtures
# ============================================================================

@pytest.fixture
def mock_letta_response():
    """Mock Letta agent response"""
    return {
        "messages": [
            {
                "message_type": "function_return",
                "function_return": "Processing..."
            },
            {
                "message_type": "internal_monologue",
                "internal_monologue": "Thinking about the response..."
            },
            {
                "message_type": "assistant_message",
                "assistant_message": "Here is my response to your question."
            }
        ]
    }


@pytest.fixture
def mock_letta_agents_response():
    """Mock response from /v1/models endpoint (agent list)"""
    return {
        "data": [
            {"id": "agent-001"},
            {"id": "agent-002"},
            {"id": "agent-003"}
        ]
    }


# ============================================================================
# Mock Classes
# ============================================================================

@pytest.fixture
def mock_nio_client():
    """Mock matrix-nio AsyncClient"""
    client = AsyncMock()
    client.user_id = "@test:matrix.test"
    client.device_id = "test_device"
    client.access_token = "test_token"

    # Mock successful login
    login_response = Mock()
    login_response.user_id = "@test:matrix.test"
    login_response.device_id = "test_device"
    login_response.access_token = "test_token"
    client.login = AsyncMock(return_value=login_response)

    # Mock sync
    sync_response = Mock()
    sync_response.rooms = Mock()
    sync_response.rooms.join = {}
    client.sync = AsyncMock(return_value=sync_response)

    # Mock room operations
    client.room_send = AsyncMock(return_value=Mock(event_id="$test_event"))
    client.join = AsyncMock(return_value=Mock())
    client.room_create = AsyncMock(return_value=Mock(room_id="!newroom:matrix.test"))

    return client


@pytest.fixture
def mock_letta_client():
    """Mock Letta AsyncLetta client"""
    client = AsyncMock()

    # Mock send_message
    mock_response = Mock()
    mock_response.messages = [
        Mock(message_type="assistant_message", assistant_message="Test response")
    ]
    client.send_message = AsyncMock(return_value=mock_response)

    # Mock get_agent
    mock_agent = Mock()
    mock_agent.id = "test-agent-id"
    mock_agent.name = "TestAgent"
    client.get_agent = AsyncMock(return_value=mock_agent)

    return client


# ============================================================================
# File System Fixtures
# ============================================================================

@pytest.fixture
def mock_mappings_file(tmp_path):
    """Create a temporary mappings file"""
    mappings_file = tmp_path / "agent_user_mappings.json"

    # Write sample data
    sample_data = {
        "agent-001": {
            "agent_id": "agent-001",
            "agent_name": "TestAgent1",
            "matrix_user_id": "@agent_001:matrix.test",
            "matrix_password": "password1",
            "created": True,
            "room_id": "!room001:matrix.test",
            "room_created": True,
            "invitation_status": {}
        }
    }

    with open(mappings_file, 'w') as f:
        json.dump(sample_data, f)

    return str(mappings_file)


# ============================================================================
# Async Test Support
# ============================================================================

@pytest.fixture
def event_loop():
    """Create an event loop for async tests"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# Helper Fixtures
# ============================================================================

@pytest.fixture
def capture_logs(caplog):
    """Fixture to capture and analyze logs"""
    caplog.set_level("DEBUG")
    return caplog


@pytest.fixture
def mock_time():
    """Mock time.time() for consistent timestamps"""
    with patch('time.time', return_value=1704067200.0):
        yield 1704067200.0


# ============================================================================
# Database Mock Fixtures
# ============================================================================

@pytest.fixture
def mock_agent_mapping_db():
    """Mock AgentMappingDB for unit tests

    This fixture mocks the database layer to avoid needing a real PostgreSQL
    instance during unit tests. Use this for testing AgentUserManager logic
    without database dependencies.

    Usage:
        @pytest.mark.asyncio
        async def test_something(self, mock_agent_mapping_db):
            # mock_agent_mapping_db is already patched and active
            manager = AgentUserManager(config)
            await manager.save_mappings()
            # Verify database methods were called
            assert mock_agent_mapping_db.upsert.called
    """
    # Create mock database instance
    db_instance = Mock()

    # Mock the methods used by AgentUserManager
    db_instance.get_all = Mock(return_value=[])
    db_instance.get_by_room_id = Mock(return_value=None)
    db_instance.get_by_agent_id = Mock(return_value=None)
    db_instance.upsert = Mock()
    db_instance.delete = Mock()

    # Create a mock module with our mocked class
    mock_module = Mock()
    mock_db_class = Mock(return_value=db_instance)
    mock_module.AgentMappingDB = mock_db_class
    mock_module.AgentMapping = Mock()
    mock_module.InvitationStatus = Mock()

    # Patch sys.modules to include our mock module
    import sys
    original_module = sys.modules.get('src.models.agent_mapping')
    sys.modules['src.models.agent_mapping'] = mock_module

    yield db_instance

    # Restore original module
    if original_module is not None:
        sys.modules['src.models.agent_mapping'] = original_module
    else:
        sys.modules.pop('src.models.agent_mapping', None)


@pytest.fixture
def mock_agent_mapping_model():
    """Mock AgentMapping model class for unit tests"""
    with patch('src.core.agent_user_manager.AgentMapping') as mock_model:
        yield mock_model


@pytest.fixture
def mock_invitation_status_model():
    """Mock InvitationStatus model class for unit tests"""
    with patch('src.core.agent_user_manager.InvitationStatus') as mock_model:
        yield mock_model


# ============================================================================
# SQLite Database Fixtures (for integration tests)
# ============================================================================

@pytest.fixture(scope="session")
def sqlite_engine():
    """Create a SQLite engine for the test session

    This fixture creates an in-memory SQLite database that persists for the
    entire test session. It's shared across all tests but cleaned between
    individual test runs via the sqlite_db fixture.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from src.models.agent_mapping import Base

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
def sqlite_db(sqlite_engine, monkeypatch):
    """
    Provide a clean database for each test.

    This fixture:
    - Uses the shared sqlite_engine from the session
    - Creates a new AgentMappingDB instance
    - Clears all data after the test

    Usage:
        @pytest.mark.integration
        @pytest.mark.sqlite
        def test_something(sqlite_db):
            # Use sqlite_db for database operations
            sqlite_db.upsert(agent_id="test", ...)
    """
    from src.models.agent_mapping import AgentMappingDB, AgentMapping, InvitationStatus
    from sqlalchemy.orm import sessionmaker

    # Patch get_engine to return our shared engine
    import src.models.agent_mapping
    original_get_engine = src.models.agent_mapping.get_engine
    monkeypatch.setattr(src.models.agent_mapping, 'get_engine', lambda: sqlite_engine)

    # Create database instance (will use our patched get_engine)
    db = AgentMappingDB()

    yield db

    # Cleanup: clear all data
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    try:
        session.query(InvitationStatus).delete()
        session.query(AgentMapping).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture
def sqlite_db_with_data(sqlite_db):
    """
    Provide a database pre-populated with test data.

    Useful for tests that need existing data.

    Default test data includes:
    - agent_id: test-agent-001
    - agent_name: TestAgent
    - room_id: !testroom:matrix.test

    Usage:
        @pytest.mark.integration
        @pytest.mark.sqlite
        def test_with_data(sqlite_db_with_data):
            # Database already has test-agent-001
            mapping = sqlite_db_with_data.get_by_agent_id("test-agent-001")
            assert mapping is not None
    """
    # Insert test data
    sqlite_db.upsert(
        agent_id="test-agent-001",
        agent_name="TestAgent",
        matrix_user_id="@test:matrix.test",
        matrix_password="test_password",
        room_id="!testroom:matrix.test",
        room_created=True
    )

    # Add invitation status
    sqlite_db.update_invitation_status(
        agent_id="test-agent-001",
        invitee="@admin:matrix.test",
        status="joined"
    )

    yield sqlite_db
