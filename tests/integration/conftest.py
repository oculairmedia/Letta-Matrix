"""
Pytest configuration and fixtures for integration tests

These fixtures provide mocked services for integration testing without
requiring live Matrix homeserver or Letta API.
"""
import pytest
import tempfile
import os
from unittest.mock import Mock, AsyncMock, patch
from dataclasses import dataclass


@dataclass
class IntegrationTestConfig:
    """Configuration for integration tests"""
    homeserver_url: str = "http://mock-synapse:8008"
    username: str = "@letta:mock.matrix.test"
    password: str = "mock_password"
    letta_api_url: str = "http://mock-letta:8283"
    letta_token: str = "mock_letta_token"
    letta_agent_id: str = "mock-agent-test"
    matrix_api_url: str = "http://mock-matrix-api:8000"


@pytest.fixture
def integration_config():
    """Provide mock configuration for integration tests"""
    return IntegrationTestConfig()


@pytest.fixture
def integration_temp_dir():
    """Create temporary directory for integration test data"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def mock_space_id():
    """Mock space ID for testing"""
    return "!mock_letta_space_123:mock.matrix.test"


@pytest.fixture
def mock_room_id():
    """Mock room ID for testing"""
    return "!mock_agent_room_456:mock.matrix.test"


@pytest.fixture
def mock_access_token():
    """Mock Matrix access token"""
    return "mock_access_token_xyz789"


@pytest.fixture
def mock_letta_agents():
    """Mock list of Letta agents"""
    return [
        {"id": "agent-001", "name": "Alpha Agent"},
        {"id": "agent-002", "name": "Beta Agent"},
        {"id": "agent-003", "name": "Gamma Agent"}
    ]


@pytest.fixture
def mock_http_session(mock_space_id, mock_room_id, mock_access_token, mock_letta_agents):
    """
    Create a comprehensive mock HTTP session for Matrix and Letta API calls

    This fixture provides a fully mocked aiohttp ClientSession with appropriate
    responses for all common API calls used in integration tests.
    """
    mock_session = AsyncMock()

    # Mock Matrix login response
    mock_login_response = AsyncMock()
    mock_login_response.status = 200
    mock_login_response.json = AsyncMock(return_value={
        "access_token": mock_access_token,
        "user_id": "@admin:mock.matrix.test",
        "device_id": "MOCK_DEVICE",
        "home_server": "mock.matrix.test"
    })
    mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
    mock_login_response.__aexit__ = AsyncMock(return_value=None)

    # Mock space creation response
    mock_space_create_response = AsyncMock()
    mock_space_create_response.status = 200
    mock_space_create_response.json = AsyncMock(return_value={
        "room_id": mock_space_id
    })
    mock_space_create_response.__aenter__ = AsyncMock(return_value=mock_space_create_response)
    mock_space_create_response.__aexit__ = AsyncMock(return_value=None)

    # Mock room creation response
    mock_room_create_response = AsyncMock()
    mock_room_create_response.status = 200
    mock_room_create_response.json = AsyncMock(return_value={
        "room_id": mock_room_id
    })
    mock_room_create_response.__aenter__ = AsyncMock(return_value=mock_room_create_response)
    mock_room_create_response.__aexit__ = AsyncMock(return_value=None)

    # Mock user creation response
    mock_user_create_response = AsyncMock()
    mock_user_create_response.status = 200
    mock_user_create_response.json = AsyncMock(return_value={
        "name": "@new_user:mock.matrix.test",
        "password": "generated_password"
    })
    mock_user_create_response.__aenter__ = AsyncMock(return_value=mock_user_create_response)
    mock_user_create_response.__aexit__ = AsyncMock(return_value=None)

    # Mock Letta agents list response
    mock_letta_agents_response = AsyncMock()
    mock_letta_agents_response.status = 200
    mock_letta_agents_response.json = AsyncMock(return_value=mock_letta_agents)
    mock_letta_agents_response.__aenter__ = AsyncMock(return_value=mock_letta_agents_response)
    mock_letta_agents_response.__aexit__ = AsyncMock(return_value=None)

    # Mock Letta agent details response
    mock_letta_agent_detail_response = AsyncMock()
    mock_letta_agent_detail_response.status = 200
    mock_letta_agent_detail_response.json = AsyncMock(return_value={
        "id": "agent-001",
        "name": "Alpha Agent",
        "created_at": "2025-01-01T00:00:00Z"
    })
    mock_letta_agent_detail_response.__aenter__ = AsyncMock(return_value=mock_letta_agent_detail_response)
    mock_letta_agent_detail_response.__aexit__ = AsyncMock(return_value=None)

    # Mock Letta agent messages response (for history import)
    mock_letta_messages_response = AsyncMock()
    mock_letta_messages_response.status = 200
    mock_letta_messages_response.json = AsyncMock(return_value={
        "items": []  # Empty history for tests
    })
    mock_letta_messages_response.__aenter__ = AsyncMock(return_value=mock_letta_messages_response)
    mock_letta_messages_response.__aexit__ = AsyncMock(return_value=None)

    # Mock Matrix room join response
    mock_room_join_response = AsyncMock()
    mock_room_join_response.status = 200
    mock_room_join_response.json = AsyncMock(return_value={
        "room_id": mock_room_id
    })
    mock_room_join_response.__aenter__ = AsyncMock(return_value=mock_room_join_response)
    mock_room_join_response.__aexit__ = AsyncMock(return_value=None)

    # Mock Matrix user registration response (v3 API)
    mock_register_v3_response = AsyncMock()
    mock_register_v3_response.status = 200
    mock_register_v3_response.json = AsyncMock(return_value={
        "user_id": "@agent_test:mock.matrix.test",
        "access_token": "agent_token_mock",
        "device_id": "AGENT_DEVICE"
    })
    mock_register_v3_response.__aenter__ = AsyncMock(return_value=mock_register_v3_response)
    mock_register_v3_response.__aexit__ = AsyncMock(return_value=None)

    # Mock generic success response (for PUT, DELETE, etc.)
    mock_success_response = AsyncMock()
    mock_success_response.status = 200
    mock_success_response.json = AsyncMock(return_value={})
    mock_success_response.text = AsyncMock(return_value="OK")
    mock_success_response.__aenter__ = AsyncMock(return_value=mock_success_response)
    mock_success_response.__aexit__ = AsyncMock(return_value=None)

    # Configure session methods to return appropriate mocks based on URL
    def mock_post(url, **kwargs):
        """Mock POST requests"""
        url_lower = url.lower()

        if "login" in url_lower:
            return mock_login_response
        elif "join" in url_lower:
            # Room join endpoint: POST /rooms/{id}/join
            return mock_room_join_response
        elif "createroom" in url_lower:
            # Check if it's a space creation (has "type": "m.space")
            json_data = kwargs.get('json', {})
            creation_content = json_data.get('creation_content', {})
            if creation_content.get('type') == 'm.space':
                return mock_space_create_response
            else:
                return mock_room_create_response
        elif "register" in url_lower:
            # Matrix user registration (v3 API)
            return mock_register_v3_response
        elif "v2/users" in url_lower:
            # Old Matrix admin API user creation
            return mock_user_create_response
        else:
            return mock_success_response

    def mock_get(url, **kwargs):
        """Mock GET requests"""
        url_lower = url.lower()

        if "letta" in url_lower or ("agents" in url_lower and ":" in url):
            # Letta API endpoints
            if "messages" in url_lower:
                # GET /agents/{id}/messages
                return mock_letta_messages_response
            elif url.rstrip('/').split('/')[-1] == 'agents' or 'limit=' in url:
                # GET /agents (list with pagination)
                return mock_letta_agents_response
            else:
                # GET /agents/{id} (detail)
                return mock_letta_agent_detail_response
        else:
            return mock_success_response

    def mock_put(url, **kwargs):
        """Mock PUT requests"""
        return mock_success_response

    def mock_delete(url, **kwargs):
        """Mock DELETE requests"""
        return mock_success_response

    # Assign mock methods
    mock_session.post = Mock(side_effect=mock_post)
    mock_session.get = Mock(side_effect=mock_get)
    mock_session.put = Mock(side_effect=mock_put)
    mock_session.delete = Mock(side_effect=mock_delete)
    mock_session.closed = False

    # Mock context manager methods
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    return mock_session


@pytest.fixture
def patched_http_session(mock_http_session):
    """
    Patch the global HTTP session across all modules

    This fixture patches both get_global_session AND aiohttp.ClientSession
    in all relevant modules to return the mocked session, ensuring all HTTP
    calls are intercepted regardless of how sessions are created.
    """
    async def mock_get_global_session():
        return mock_http_session

    # List of modules that use get_global_session or aiohttp.ClientSession
    modules_to_patch = [
        'src.core.agent_user_manager',
        'src.core.space_manager',
        'src.core.user_manager',
        'src.core.room_manager'
    ]

    patchers = []

    # Only patch get_global_session where it exists (agent_user_manager)
    patcher = patch('src.core.agent_user_manager.get_global_session', side_effect=mock_get_global_session)
    patcher.start()
    patchers.append(patcher)

    # CRITICAL FIX: Patch aiohttp.ClientSession directly in ALL modules
    # This catches cases where code creates sessions with "async with aiohttp.ClientSession()"
    for module in modules_to_patch:
        patcher = patch(f'{module}.aiohttp.ClientSession', return_value=mock_http_session)
        patcher.start()
        patchers.append(patcher)

    yield mock_http_session

    # Stop all patchers
    for patcher in patchers:
        patcher.stop()


@pytest.fixture
def patched_logging():
    """Patch logging to avoid logger initialization issues in tests"""
    with patch('src.core.agent_user_manager.logging.getLogger'), \
         patch('src.core.space_manager.logging.getLogger'), \
         patch('src.core.user_manager.logging.getLogger'), \
         patch('src.core.room_manager.logging.getLogger'):
        yield


@pytest.fixture
def integration_env_setup(integration_temp_dir):
    """
    Set up environment variables for integration tests

    This ensures all file operations use the temporary directory.
    """
    with patch.dict(os.environ, {
        "MATRIX_DATA_DIR": integration_temp_dir,
        "MATRIX_ADMIN_USERNAME": "@admin:mock.matrix.test",
        "MATRIX_ADMIN_PASSWORD": "admin_password"
    }):
        yield integration_temp_dir


@pytest.fixture
async def integration_manager(integration_config, integration_env_setup,
                               patched_http_session, patched_logging):
    """
    Create a fully mocked AgentUserManager for integration testing

    This fixture provides a ready-to-use manager with all dependencies mocked.
    """
    from src.core.agent_user_manager import AgentUserManager

    manager = AgentUserManager(config=integration_config)

    yield manager

    # Cleanup if needed
    # (temporary directory cleanup is handled by integration_temp_dir fixture)


@pytest.fixture
def sample_agent_mappings():
    """Provide sample agent mappings for testing"""
    from src.core.agent_user_manager import AgentUserMapping

    return {
        "agent-001": AgentUserMapping(
            agent_id="agent-001",
            agent_name="Alpha Agent",
            matrix_user_id="@agent_001:mock.matrix.test",
            matrix_password="password1",
            created=True,
            room_id="!room001:mock.matrix.test",
            room_created=True
        ),
        "agent-002": AgentUserMapping(
            agent_id="agent-002",
            agent_name="Beta Agent",
            matrix_user_id="@agent_002:mock.matrix.test",
            matrix_password="password2",
            created=True,
            room_id="!room002:mock.matrix.test",
            room_created=True
        ),
        "agent-003": AgentUserMapping(
            agent_id="agent-003",
            agent_name="Gamma Agent",
            matrix_user_id="@agent_003:mock.matrix.test",
            matrix_password="password3",
            created=True,
            room_id="!room003:mock.matrix.test",
            room_created=True
        )
    }
