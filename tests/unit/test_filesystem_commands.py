"""
Unit tests for Letta Code filesystem commands

Tests cover the filesystem mode functionality that enables agents to execute
code in project directories using Letta Code CLI instead of cloud-only API:
- /fs-link - Link agent to a project directory
- /fs-task - Enable/disable filesystem mode
- /fs-run - Execute one-off task in filesystem mode
- Helper functions: resolve_letta_project_dir, get/update_letta_code_room_state
- VibSync API integration for auto-detecting project paths

Run these tests with:
    pytest tests/unit/test_filesystem_commands.py -v
"""
import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, mock_open, MagicMock

from src.matrix.client import (
    handle_letta_code_command,
    resolve_letta_project_dir,
    get_letta_code_room_state,
    update_letta_code_room_state,
    run_letta_code_task,
    call_letta_code_api,
    Config,
    LettaCodeApiError,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_config():
    """Create a mock Config object with Letta Code enabled"""
    return Config(
        homeserver_url="http://test-server:8008",
        username="@testbot:test.com",
        password="test_password",
        room_id="!testroom:test.com",
        letta_api_url="http://test-letta:8080",
        letta_token="test_token_123",
        letta_agent_id="agent-test-001",
        log_level="INFO",
        letta_code_enabled=True,
        letta_code_api_url="http://localhost:3456"
    )


@pytest.fixture
def mock_logger():
    """Create a mock logger"""
    logger = Mock()
    logger.info = Mock()
    logger.debug = Mock()
    logger.warning = Mock()
    logger.error = Mock()
    return logger


@pytest.fixture
def mock_room():
    """Create a mock Matrix room"""
    room = Mock()
    room.room_id = "!agentroom:test.com"
    room.display_name = "Test Agent Room"
    return room


@pytest.fixture
def mock_event_fs_link():
    """Create a mock event for /fs-link command"""
    event = Mock()
    event.body = "/fs-link /opt/stacks/test-project"
    event.sender = "@user:test.com"
    return event


@pytest.fixture
def mock_event_fs_link_no_args():
    """Create a mock event for /fs-link with no arguments (auto-detect)"""
    event = Mock()
    event.body = "/fs-link"
    event.sender = "@user:test.com"
    return event


@pytest.fixture
def mock_event_fs_task_on():
    """Create a mock event for /fs-task on"""
    event = Mock()
    event.body = "/fs-task on"
    event.sender = "@user:test.com"
    return event


@pytest.fixture
def mock_event_fs_task_status():
    """Create a mock event for /fs-task status"""
    event = Mock()
    event.body = "/fs-task status"
    event.sender = "@user:test.com"
    return event


@pytest.fixture
def mock_event_fs_run():
    """Create a mock event for /fs-run"""
    event = Mock()
    event.body = "/fs-run list files in the current directory"
    event.sender = "@user:test.com"
    return event


@pytest.fixture
def mock_event_fs_run_with_path():
    """Create a mock event for /fs-run with --path override"""
    event = Mock()
    event.body = "/fs-run --path=/opt/custom list files"
    event.sender = "@user:test.com"
    return event


@pytest.fixture
def temp_state_file(tmp_path):
    """Create a temporary letta_code_state.json file"""
    state_data = {
        "!agentroom:test.com": {
            "projectDir": "/opt/stacks/test-project",
            "enabled": False
        }
    }
    state_file = tmp_path / "letta_code_state.json"
    with open(state_file, 'w') as f:
        json.dump(state_data, f)
    return str(state_file)


# ============================================================================
# State Management Tests
# ============================================================================

@pytest.mark.unit
class TestLettaCodeStateManagement:
    """Test state persistence for filesystem mode"""

    def test_get_letta_code_room_state_empty(self, tmp_path):
        """Test getting state when no state exists"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            state = get_letta_code_room_state("!newroom:test.com")
            assert state == {}

    def test_update_letta_code_room_state_creates_entry(self, tmp_path):
        """Test creating new room state entry"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            result = update_letta_code_room_state("!room:test.com", {
                "projectDir": "/opt/project",
                "enabled": True
            })
            
            assert result["projectDir"] == "/opt/project"
            assert result["enabled"] is True
            
            # Verify state was persisted
            assert state_file.exists()
            with open(state_file) as f:
                saved_data = json.load(f)
                assert "!room:test.com" in saved_data
                assert saved_data["!room:test.com"]["projectDir"] == "/opt/project"

    def test_update_letta_code_room_state_updates_existing(self, tmp_path):
        """Test updating existing room state"""
        state_file = tmp_path / "letta_code_state.json"
        initial_data = {
            "!room:test.com": {
                "projectDir": "/opt/old",
                "enabled": False
            }
        }
        with open(state_file, 'w') as f:
            json.dump(initial_data, f)
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            result = update_letta_code_room_state("!room:test.com", {
                "projectDir": "/opt/new",
                "enabled": True
            })
            
            assert result["projectDir"] == "/opt/new"
            assert result["enabled"] is True


# ============================================================================
# resolve_letta_project_dir Tests
# ============================================================================

@pytest.mark.unit
class TestResolveLettaProjectDir:
    """Test project directory resolution"""

    @pytest.mark.asyncio
    async def test_resolve_with_override_path(self, mock_config, mock_logger, tmp_path):
        """Test that override path takes precedence"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            result = await resolve_letta_project_dir(
                room_id="!room:test.com",
                agent_id="agent-123",
                config=mock_config,
                logger=mock_logger,
                override_path="/opt/override"
            )
            
            assert result == "/opt/override"
            
            # Verify state was updated
            state = get_letta_code_room_state("!room:test.com")
            assert state["projectDir"] == "/opt/override"

    @pytest.mark.asyncio
    async def test_resolve_from_room_state(self, mock_config, mock_logger, tmp_path):
        """Test resolving from existing room state"""
        state_file = tmp_path / "letta_code_state.json"
        initial_data = {
            "!room:test.com": {
                "projectDir": "/opt/from-state"
            }
        }
        with open(state_file, 'w') as f:
            json.dump(initial_data, f)
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            result = await resolve_letta_project_dir(
                room_id="!room:test.com",
                agent_id="agent-123",
                config=mock_config,
                logger=mock_logger
            )
            
            assert result == "/opt/from-state"

    @pytest.mark.asyncio
    async def test_resolve_from_api(self, mock_config, mock_logger, tmp_path):
        """Test resolving from VibSync API"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api:
                mock_api.return_value = {"projectDir": "/opt/from-api"}
                
                result = await resolve_letta_project_dir(
                    room_id="!room:test.com",
                    agent_id="agent-123",
                    config=mock_config,
                    logger=mock_logger
                )
                
                assert result == "/opt/from-api"
                mock_api.assert_called_once_with(
                    mock_config,
                    'GET',
                    '/api/letta-code/sessions/agent-123'
                )
                
                # Verify state was updated
                state = get_letta_code_room_state("!room:test.com")
                assert state["projectDir"] == "/opt/from-api"

    @pytest.mark.asyncio
    async def test_resolve_returns_none_when_not_found(self, mock_config, mock_logger, tmp_path):
        """Test that None is returned when no path found"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api:
                mock_api.side_effect = LettaCodeApiError(404, "Not found", {})
                
                result = await resolve_letta_project_dir(
                    room_id="!room:test.com",
                    agent_id="agent-123",
                    config=mock_config,
                    logger=mock_logger
                )
                
                assert result is None


# ============================================================================
# /fs-link Command Tests
# ============================================================================

@pytest.mark.unit
class TestFsLinkCommand:
    """Test /fs-link command"""

    @pytest.mark.asyncio
    async def test_fs_link_with_explicit_path(self, mock_room, mock_event_fs_link, 
                                               mock_config, mock_logger, tmp_path):
        """Test /fs-link with explicit path argument"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                mock_api.return_value = {"message": "Linked successfully"}
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_link,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify API was called with correct payload
                mock_api.assert_called_once()
                call_args = mock_api.call_args
                assert call_args[0][0] == mock_config
                assert call_args[0][1] == 'POST'
                assert call_args[0][2] == '/api/letta-code/link'
                payload = call_args[0][3]
                assert payload["projectDir"] == "/opt/stacks/test-project"
                assert payload["agentId"] == "agent-test-123"
                
                # Verify state was updated
                state = get_letta_code_room_state(mock_room.room_id)
                assert state["projectDir"] == "/opt/stacks/test-project"
                
                # Verify response was sent
                mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_fs_link_auto_detect_from_vibsync(self, mock_room, mock_event_fs_link_no_args,
                                                     mock_config, mock_logger, tmp_path):
        """Test /fs-link with auto-detection from VibSync API"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                # Mock VibSync API response
                def api_side_effect(config, method, path, *args, **kwargs):
                    if path == '/api/projects':
                        return {
                            "projects": [
                                {
                                    "name": "Test Project",
                                    "filesystem_path": "/opt/stacks/test-project"
                                },
                                {
                                    "name": "Another Project",
                                    "filesystem_path": "/opt/stacks/other"
                                }
                            ]
                        }
                    elif path == '/api/letta-code/link':
                        return {"message": "Linked successfully"}
                    return {}
                
                mock_api.side_effect = api_side_effect
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_link_no_args,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Huly - Test Project"  # Should match "Test Project"
                )
                
                assert result is True
                
                # Verify VibSync API was queried
                projects_call = [call for call in mock_api.call_args_list if '/api/projects' in str(call)]
                assert len(projects_call) == 1
                
                # Verify link API was called with auto-detected path
                link_call = [call for call in mock_api.call_args_list if '/api/letta-code/link' in str(call)]
                assert len(link_call) == 1
                payload = link_call[0][0][3]
                assert payload["projectDir"] == "/opt/stacks/test-project"

    @pytest.mark.asyncio
    async def test_fs_link_auto_detect_failure(self, mock_room, mock_event_fs_link_no_args,
                                                mock_config, mock_logger, tmp_path):
        """Test /fs-link when auto-detection fails"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                # Mock VibSync API to return no matching projects
                mock_api.return_value = {
                    "projects": [
                        {
                            "name": "Other Project",
                            "filesystem_path": "/opt/stacks/other"
                        }
                    ]
                }
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_link_no_args,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Huly - Nonexistent Project"
                )
                
                assert result is True
                
                # Verify usage message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "Usage:" in message or "Could not auto-detect" in message

    @pytest.mark.asyncio
    async def test_fs_link_api_error(self, mock_room, mock_event_fs_link,
                                     mock_config, mock_logger, tmp_path):
        """Test /fs-link when VibSync API returns error"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                mock_api.side_effect = LettaCodeApiError(500, "Internal error", {"error": "Database down"})
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_link,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify error message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "Link failed" in message or "500" in message


# ============================================================================
# /fs-task Command Tests
# ============================================================================

@pytest.mark.unit
class TestFsTaskCommand:
    """Test /fs-task command"""

    @pytest.mark.asyncio
    async def test_fs_task_enable(self, mock_room, mock_event_fs_task_on,
                                   mock_config, mock_logger, tmp_path):
        """Test /fs-task on enables filesystem mode"""
        state_file = tmp_path / "letta_code_state.json"
        initial_data = {
            mock_room.room_id: {
                "projectDir": "/opt/stacks/test-project",
                "enabled": False
            }
        }
        with open(state_file, 'w') as f:
            json.dump(initial_data, f)
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_task_on,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify state was updated
                state = get_letta_code_room_state(mock_room.room_id)
                assert state["enabled"] is True
                assert state["projectDir"] == "/opt/stacks/test-project"
                
                # Verify confirmation message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "ENABLED" in message
                assert "Letta Code" in message

    @pytest.mark.asyncio
    async def test_fs_task_disable(self, mock_room, mock_config, mock_logger, tmp_path):
        """Test /fs-task off disables filesystem mode"""
        state_file = tmp_path / "letta_code_state.json"
        initial_data = {
            mock_room.room_id: {
                "projectDir": "/opt/stacks/test-project",
                "enabled": True
            }
        }
        with open(state_file, 'w') as f:
            json.dump(initial_data, f)
        
        event = Mock()
        event.body = "/fs-task off"
        event.sender = "@user:test.com"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=event,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify state was updated
                state = get_letta_code_room_state(mock_room.room_id)
                assert state["enabled"] is False
                
                # Verify confirmation message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "DISABLED" in message

    @pytest.mark.asyncio
    async def test_fs_task_status(self, mock_room, mock_event_fs_task_status,
                                   mock_config, mock_logger, tmp_path):
        """Test /fs-task status displays current state"""
        state_file = tmp_path / "letta_code_state.json"
        initial_data = {
            mock_room.room_id: {
                "projectDir": "/opt/stacks/test-project",
                "enabled": True
            }
        }
        with open(state_file, 'w') as f:
            json.dump(initial_data, f)
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_task_status,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify status message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "ENABLED" in message
                assert "Letta Code" in message
                assert "/opt/stacks/test-project" in message

    @pytest.mark.asyncio
    async def test_fs_task_enable_without_link(self, mock_room, mock_event_fs_task_on,
                                                mock_config, mock_logger, tmp_path):
        """Test /fs-task on fails if no project linked"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send, \
                 patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api:
                
                # Mock API to return no session
                mock_api.side_effect = LettaCodeApiError(404, "Not found", {})
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_task_on,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify error message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "Link a project" in message or "/fs-link" in message


# ============================================================================
# /fs-run Command Tests
# ============================================================================

@pytest.mark.unit
class TestFsRunCommand:
    """Test /fs-run command"""

    @pytest.mark.asyncio
    async def test_fs_run_basic(self, mock_room, mock_event_fs_run,
                                 mock_config, mock_logger, tmp_path):
        """Test /fs-run executes task"""
        state_file = tmp_path / "letta_code_state.json"
        initial_data = {
            mock_room.room_id: {
                "projectDir": "/opt/stacks/test-project",
                "enabled": False
            }
        }
        with open(state_file, 'w') as f:
            json.dump(initial_data, f)
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                mock_api.return_value = {
                    "success": True,
                    "result": "file1.py\nfile2.py\nfile3.py"
                }
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_run,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify task API was called
                mock_api.assert_called_once()
                call_args = mock_api.call_args
                assert call_args[0][2] == '/api/letta-code/task'
                payload = call_args[0][3]
                assert payload["prompt"] == "list files in the current directory"
                assert payload["projectDir"] == "/opt/stacks/test-project"
                assert payload["agentId"] == "agent-test-123"
                
                # Verify response was sent
                mock_send.assert_called()
                message = mock_send.call_args[0][1]
                assert "file1.py" in message

    @pytest.mark.asyncio
    async def test_fs_run_with_path_override(self, mock_room, mock_event_fs_run_with_path,
                                              mock_config, mock_logger, tmp_path):
        """Test /fs-run with --path override"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                mock_api.return_value = {
                    "success": True,
                    "result": "custom output"
                }
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=mock_event_fs_run_with_path,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify custom path was used
                call_args = mock_api.call_args
                payload = call_args[0][3]
                assert payload["projectDir"] == "/opt/custom"
                assert payload["prompt"] == "list files"

    @pytest.mark.asyncio
    async def test_fs_run_no_prompt(self, mock_room, mock_config, mock_logger, tmp_path):
        """Test /fs-run without prompt shows usage"""
        event = Mock()
        event.body = "/fs-run"
        event.sender = "@user:test.com"
        
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                result = await handle_letta_code_command(
                    room=mock_room,
                    event=event,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-test-123",
                    agent_name_hint="Test Agent"
                )
                
                assert result is True
                
                # Verify usage message was sent
                mock_send.assert_called_once()
                message = mock_send.call_args[0][1]
                assert "Usage:" in message


# ============================================================================
# run_letta_code_task Tests
# ============================================================================

@pytest.mark.unit
class TestRunLettaCodeTask:
    """Test run_letta_code_task helper function"""

    @pytest.mark.asyncio
    async def test_run_task_success(self, mock_config, mock_logger):
        """Test successful task execution"""
        with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
             patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
            
            mock_api.return_value = {
                "success": True,
                "result": "Task completed successfully"
            }
            
            result = await run_letta_code_task(
                room_id="!room:test.com",
                agent_id="agent-123",
                agent_name="Test Agent",
                project_dir="/opt/project",
                prompt="test task",
                config=mock_config,
                logger=mock_logger,
                wrap_response=True
            )
            
            assert result is True
            mock_send.assert_called_once()
            message = mock_send.call_args[0][1]
            assert "Task succeeded" in message
            assert "Task completed successfully" in message

    @pytest.mark.asyncio
    async def test_run_task_failure(self, mock_config, mock_logger):
        """Test task execution failure"""
        with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
             patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
            
            mock_api.return_value = {
                "success": False,
                "error": "Command failed",
                "result": "stderr output"
            }
            
            result = await run_letta_code_task(
                room_id="!room:test.com",
                agent_id="agent-123",
                agent_name="Test Agent",
                project_dir="/opt/project",
                prompt="failing task",
                config=mock_config,
                logger=mock_logger,
                wrap_response=True
            )
            
            assert result is False
            mock_send.assert_called_once()
            message = mock_send.call_args[0][1]
            assert "Task failed" in message

    @pytest.mark.asyncio
    async def test_run_task_api_error(self, mock_config, mock_logger):
        """Test handling of API errors"""
        with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
             patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
            
            mock_api.side_effect = LettaCodeApiError(500, "Internal error", {"error": "Server down"})
            
            result = await run_letta_code_task(
                room_id="!room:test.com",
                agent_id="agent-123",
                agent_name="Test Agent",
                project_dir="/opt/project",
                prompt="task",
                config=mock_config,
                logger=mock_logger,
                wrap_response=True
            )
            
            assert result is False
            mock_send.assert_called_once()
            message = mock_send.call_args[0][1]
            assert "failed" in message.lower()
            assert "500" in message


# ============================================================================
# Integration Tests
# ============================================================================

@pytest.mark.unit
class TestFilesystemCommandIntegration:
    """Integration tests for complete filesystem workflows"""

    @pytest.mark.asyncio
    async def test_complete_workflow_link_enable_run(self, mock_room, mock_config, 
                                                      mock_logger, tmp_path):
        """Test complete workflow: link -> enable -> run"""
        state_file = tmp_path / "letta_code_state.json"
        
        with patch('src.matrix.client.LETTACODE_STATE_PATH', str(state_file)):
            # Reset internal state
            import src.matrix.client as client_module
            client_module._letta_code_state = {}
            
            with patch('src.matrix.client.call_letta_code_api', new_callable=AsyncMock) as mock_api, \
                 patch('src.matrix.client.send_as_agent', new_callable=AsyncMock) as mock_send:
                
                # Step 1: Link project
                link_event = Mock()
                link_event.body = "/fs-link /opt/stacks/test"
                link_event.sender = "@user:test.com"
                
                mock_api.return_value = {"message": "Linked"}
                
                await handle_letta_code_command(
                    room=mock_room,
                    event=link_event,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-123",
                    agent_name_hint="Test Agent"
                )
                
                state = get_letta_code_room_state(mock_room.room_id)
                assert state["projectDir"] == "/opt/stacks/test"
                
                # Step 2: Enable filesystem mode
                enable_event = Mock()
                enable_event.body = "/fs-task on"
                enable_event.sender = "@user:test.com"
                
                await handle_letta_code_command(
                    room=mock_room,
                    event=enable_event,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-123",
                    agent_name_hint="Test Agent"
                )
                
                state = get_letta_code_room_state(mock_room.room_id)
                assert state["enabled"] is True
                
                # Step 3: Run task
                run_event = Mock()
                run_event.body = "/fs-run test task"
                run_event.sender = "@user:test.com"
                
                mock_api.return_value = {"success": True, "result": "Done"}
                
                await handle_letta_code_command(
                    room=mock_room,
                    event=run_event,
                    config=mock_config,
                    logger=mock_logger,
                    agent_id_hint="agent-123",
                    agent_name_hint="Test Agent"
                )
                
                # Verify task was executed
                task_call = [call for call in mock_api.call_args_list if '/api/letta-code/task' in str(call)]
                assert len(task_call) == 1
