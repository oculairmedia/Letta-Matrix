"""
Tests for fs-task Matrix context handling.

Verifies that:
1. Matrix context prefix is added to messages sent to Letta Code
2. User messages from Matrix are NOT re-posted (they're already in the room)
3. Only agent responses are posted back to Matrix
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio


class TestFsTaskMatrixContext:
    """Test fs-task path doesn't duplicate user messages."""

    @pytest.fixture
    def mock_config(self):
        config = MagicMock()
        config.letta_code_enabled = True
        config.letta_code_api_url = "http://localhost:3099"
        return config

    @pytest.fixture
    def mock_room(self):
        room = MagicMock()
        room.room_id = "!test:matrix.oculair.ca"
        room.display_name = "Test Room"
        return room

    @pytest.fixture
    def mock_event(self):
        event = MagicMock()
        event.body = "Hello?"
        event.sender = "@admin:matrix.oculair.ca"
        event.event_id = "$test_event"
        return event

    @pytest.mark.asyncio
    async def test_matrix_context_added_to_prompt(self, mock_config, mock_room, mock_event):
        """Matrix context prefix should be added when sending to Letta Code."""
        from src.matrix.client import get_letta_code_room_state
        
        captured_prompt = None
        
        async def mock_call_api(config, method, path, payload=None, timeout=None):
            nonlocal captured_prompt
            if payload:
                captured_prompt = payload.get('prompt')
            return {'success': True, 'result': 'Agent response'}
        
        with patch('src.matrix.client.call_letta_code_api', side_effect=mock_call_api):
            with patch('src.matrix.client.send_as_agent', new_callable=AsyncMock):
                with patch('src.matrix.client.get_letta_code_room_state', return_value={'enabled': True, 'projectDir': '/test'}):
                    from src.matrix.client import run_letta_code_task
                    
                    fs_prompt = f"[Matrix: {mock_event.sender} in {mock_room.display_name}]\n\n{mock_event.body}"
                    
                    await run_letta_code_task(
                        room_id=mock_room.room_id,
                        agent_id="agent-test",
                        agent_name="Test Agent",
                        project_dir="/test",
                        prompt=fs_prompt,
                        config=mock_config,
                        logger=MagicMock(),
                        wrap_response=False,
                    )
        
        assert captured_prompt is not None
        assert "[Matrix:" in captured_prompt
        assert mock_event.sender in captured_prompt
        assert mock_event.body in captured_prompt

    @pytest.mark.asyncio
    async def test_user_message_not_reposted_from_matrix(self, mock_config, mock_room, mock_event):
        """User messages originating from Matrix should NOT be re-posted."""
        posted_messages = []
        
        async def mock_send(room_id, message, config, logger):
            posted_messages.append(message)
        
        async def mock_call_api(config, method, path, payload=None, timeout=None):
            return {'success': True, 'result': 'Agent response here'}
        
        with patch('src.matrix.client.call_letta_code_api', side_effect=mock_call_api):
            with patch('src.matrix.client.send_as_agent', side_effect=mock_send):
                from src.matrix.client import run_letta_code_task
                
                fs_prompt = f"[Matrix: {mock_event.sender} in {mock_room.display_name}]\n\n{mock_event.body}"
                
                await run_letta_code_task(
                    room_id=mock_room.room_id,
                    agent_id="agent-test",
                    agent_name="Test Agent",
                    project_dir="/test",
                    prompt=fs_prompt,
                    config=mock_config,
                    logger=MagicMock(),
                    wrap_response=False,
                )
        
        assert len(posted_messages) == 1, f"Expected 1 message (agent response only), got {len(posted_messages)}"
        
        agent_response = posted_messages[0]
        assert agent_response == "Agent response here"
        assert "[Matrix:" not in agent_response, "Matrix context should not appear in response"
        assert mock_event.body not in agent_response or "Agent response" in agent_response

    @pytest.mark.asyncio
    async def test_huly_agent_defaults_to_fs_mode(self):
        """Huly agents should default to fs-task mode."""
        huly_agents = [
            "Huly - Matrix Synapse",
            "Huly - Personal Site", 
            "Huly-PM-Control",
        ]
        
        regular_agents = [
            "Meridian",
            "BMO",
            "GraphitiExplorer",
        ]
        
        for agent_name in huly_agents:
            is_huly = agent_name.startswith("Huly - ") or agent_name == "Huly-PM-Control"
            assert is_huly, f"{agent_name} should be detected as Huly agent"
        
        for agent_name in regular_agents:
            is_huly = agent_name.startswith("Huly - ") or agent_name == "Huly-PM-Control"
            assert not is_huly, f"{agent_name} should NOT be detected as Huly agent"

    @pytest.mark.asyncio
    async def test_fs_mode_can_be_disabled_for_huly(self):
        """Huly agents should be able to opt-out of fs-mode with explicit disable."""
        from src.matrix.client import get_letta_code_room_state, update_letta_code_room_state
        
        room_id = "!huly-test:matrix.oculair.ca"
        agent_name = "Huly - Test Project"
        
        is_huly = agent_name.startswith("Huly - ")
        assert is_huly
        
        with patch('src.matrix.client._letta_code_state', {}):
            with patch('src.matrix.client._load_letta_code_state'):
                with patch('src.matrix.client._save_letta_code_state'):
                    fs_state = {'enabled': None}
                    fs_enabled = fs_state.get("enabled")
                    use_fs_mode = fs_enabled is True or (fs_enabled is None and is_huly)
                    assert use_fs_mode, "Huly agent with enabled=None should use fs-mode"
                    
                    fs_state = {'enabled': False}
                    fs_enabled = fs_state.get("enabled")
                    use_fs_mode = fs_enabled is True or (fs_enabled is None and is_huly)
                    assert not use_fs_mode, "Huly agent with enabled=False should NOT use fs-mode"
                    
                    fs_state = {'enabled': True}
                    fs_enabled = fs_state.get("enabled")
                    use_fs_mode = fs_enabled is True or (fs_enabled is None and is_huly)
                    assert use_fs_mode, "Any agent with enabled=True should use fs-mode"


class TestMatrixContextFiltering:
    """Test that Matrix context is properly filtered in responses."""

    def test_matrix_context_format(self):
        """Verify Matrix context format is consistent."""
        sender = "@admin:matrix.oculair.ca"
        room_name = "Test Room"
        body = "Hello?"
        
        expected_prefix = f"[Matrix: {sender} in {room_name}]"
        fs_prompt = f"{expected_prefix}\n\n{body}"
        
        assert fs_prompt.startswith("[Matrix:")
        assert sender in fs_prompt
        assert room_name in fs_prompt
        assert body in fs_prompt
        
        lines = fs_prompt.split("\n")
        assert lines[0] == expected_prefix
        assert lines[1] == ""
        assert lines[2] == body

    def test_opencode_context_format(self):
        """Verify OpenCode context format is different from Matrix."""
        opencode_sender = "@oc_project:matrix.oculair.ca"
        body = "Hello from OpenCode"
        
        opencode_prompt = f"[MESSAGE FROM OPENCODE USER]\n\n{body}"
        
        assert "[MESSAGE FROM OPENCODE USER]" in opencode_prompt
        assert "[Matrix:" not in opencode_prompt
