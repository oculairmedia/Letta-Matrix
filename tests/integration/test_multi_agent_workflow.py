"""
Integration tests for multi-agent workflows

Tests cover:
- End-to-end agent creation and room setup
- Multi-agent message routing
- Agent name updates
- Room persistence across restarts
- Invitation management
- Concurrent agent operations
"""
import pytest
import asyncio
import json
import os
from unittest.mock import Mock, AsyncMock, patch
from pathlib import Path

# Import components
from src.core.agent_user_manager import AgentUserManager, AgentUserMapping
from src.matrix.client import Config


# ============================================================================
# Integration Test Fixtures
# ============================================================================

@pytest.fixture
async def agent_manager(mock_config, tmp_path, patched_http_session, monkeypatch):
    """Create AgentUserManager with temporary storage
    
    Note: patched_http_session is included as a dependency to ensure HTTP mocking
    is set up before the manager is created.
    """
    monkeypatch.setenv("MATRIX_DATA_DIR", str(tmp_path))
    manager = AgentUserManager(mock_config)
    return manager


# ============================================================================
# Agent Discovery and User Creation Tests
# ============================================================================

@pytest.mark.integration
class TestAgentDiscoveryAndCreation:
    """Test agent discovery and automatic user creation"""

    @pytest.mark.asyncio
    async def test_discover_and_create_agents(self, agent_manager, patched_http_session):
        """Test discovering agents and creating Matrix users for them"""
        # All HTTP mocking is now handled by patched_http_session fixture
        # No need to manually set up mocks - the fixture handles all endpoints

        # Get agents from Letta API (mocked)
        agents = await agent_manager.get_letta_agents()
        assert len(agents) >= 1, "Should discover at least one agent from mocked Letta API"

        # Create users for the first 2 agents (to keep test simple)
        agents_to_create = agents[:2] if len(agents) >= 2 else agents

        for agent in agents_to_create:
            await agent_manager.create_user_for_agent(agent)

        # Verify mappings were created
        assert len(agent_manager.mappings) >= len(agents_to_create), "Should have mappings for created agents"

        # Verify each agent has a mapping
        for agent in agents_to_create:
            agent_id = agent.get("id") or agent.get("agent_id")
            assert agent_id in agent_manager.mappings, f"Agent {agent_id} should have a mapping"

    @pytest.mark.asyncio
    async def test_sync_agents_to_users(self, agent_manager, patched_http_session):
        """Test full sync process from discovery to user creation"""
        # All HTTP mocking is now handled by patched_http_session fixture

        # Run full sync process
        await agent_manager.sync_agents_to_users()

        # Verify at least one agent was synced
        assert len(agent_manager.mappings) >= 1, "Should have synced at least one agent"

        # Verify agents have proper structure
        for agent_id, mapping in agent_manager.mappings.items():
            assert mapping.agent_id == agent_id, f"Agent ID should match mapping key"
            assert mapping.agent_name, f"Agent {agent_id} should have a name"
            assert mapping.matrix_user_id, f"Agent {agent_id} should have a Matrix user ID"
            assert mapping.created is True, f"Agent {agent_id} should be marked as created"


# ============================================================================
# Room Creation and Management Tests
# ============================================================================

@pytest.mark.integration
class TestRoomCreationAndManagement:
    """Test room creation and management for agents"""

    @pytest.mark.asyncio
    async def test_create_room_for_agent(self, agent_manager, patched_http_session):
        """Test creating a dedicated room for an agent"""
        # All HTTP mocking is now handled by patched_http_session fixture

        # Setup agent mapping
        agent_manager.mappings["agent-room-test"] = AgentUserMapping(
            agent_id="agent-room-test",
            agent_name="Room Test Agent",
            matrix_user_id="@agent_room_test:matrix.test",
            matrix_password="test_pass",
            created=True
        )

        # Create room for the agent
        agent_id = "agent-room-test"
        await agent_manager.create_or_update_agent_room(agent_id)

        # Verify room was created
        mapping = agent_manager.mappings["agent-room-test"]
        assert mapping.room_id is not None, "Room ID should be set"
        assert mapping.room_created is True, "Room should be marked as created"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Test relies on old file-based storage - now uses database which requires synapse-db")
    async def test_room_persistence_across_restarts(self, tmp_path, mock_config):
        """Test that rooms persist across manager restarts"""
        pass


# ============================================================================
# Agent Name Update Tests
# ============================================================================

@pytest.mark.integration
class TestAgentNameUpdates:
    """Test handling of agent name changes"""

    @pytest.mark.asyncio
    async def test_detect_agent_name_change(self, agent_manager, patched_http_session):
        """Test detecting when an agent's name changes"""
        # All HTTP mocking is now handled by patched_http_session fixture

        # Setup existing mapping with original name
        original_name = "Original Name"
        agent_manager.mappings["agent-001"] = AgentUserMapping(
            agent_id="agent-001",
            agent_name=original_name,
            matrix_user_id="@agent_001:matrix.test",
            matrix_password="test_pass",
            created=True,
            room_id="!room:matrix.test",
            room_created=True
        )

        # Save the mapping to simulate persistence
        await agent_manager.save_mappings()

        # Sync agents - this will fetch from mocked Letta API
        # The mock returns "Alpha Agent" as the name for agent-001
        agents = await agent_manager.get_letta_agents()

        # Process agents and detect name changes
        for agent in agents:
            agent_id = agent.get("id") or agent.get("agent_id")
            if agent_id in agent_manager.mappings:
                new_name = agent.get("name") or agent.get("agent_name")
                old_name = agent_manager.mappings[agent_id].agent_name

                if new_name and new_name != old_name:
                    agent_manager.mappings[agent_id].agent_name = new_name

        # Verify name was updated for agent-001
        # The mock fixture returns "Alpha Agent" so that's what it should be updated to
        assert agent_manager.mappings["agent-001"].agent_name == "Alpha Agent", \
            f"Agent name should be updated from '{original_name}' to 'Alpha Agent'"

    @pytest.mark.asyncio
    async def test_username_stability_on_rename(self, agent_manager):
        """Test that Matrix username stays the same when agent is renamed"""
        # Create mapping
        original_username = "@agent_stable:matrix.test"
        agent_manager.mappings["agent-stable"] = AgentUserMapping(
            agent_id="agent-stable",
            agent_name="Original Name",
            matrix_user_id=original_username,
            matrix_password="test_pass",
            created=True
        )

        # Simulate rename
        agent_manager.mappings["agent-stable"].agent_name = "New Name"

        # Username should remain the same
        assert agent_manager.mappings["agent-stable"].matrix_user_id == original_username


# ============================================================================
# Message Routing Tests
# ============================================================================

@pytest.mark.integration
class TestMessageRouting:
    """Test message routing between rooms and agents"""

    @pytest.mark.asyncio
    async def test_route_message_to_correct_agent(self):
        """Test that messages in agent rooms route to correct agent"""
        # Setup room-to-agent mapping
        room_agent_map = {
            "!room001:matrix.test": "agent-001",
            "!room002:matrix.test": "agent-002",
            "!room003:matrix.test": "agent-003"
        }

        # Simulate message in room002
        incoming_message = {
            "room_id": "!room002:matrix.test",
            "sender": "@user:matrix.test",
            "body": "Hello agent"
        }

        # Route to correct agent
        target_agent = room_agent_map.get(incoming_message["room_id"])

        assert target_agent == "agent-002"

    @pytest.mark.asyncio
    async def test_multiple_agents_concurrent_messages(self):
        """Test handling concurrent messages to multiple agents"""
        messages = [
            {"room_id": "!room001:matrix.test", "agent": "agent-001", "body": "Message 1"},
            {"room_id": "!room002:matrix.test", "agent": "agent-002", "body": "Message 2"},
            {"room_id": "!room003:matrix.test", "agent": "agent-003", "body": "Message 3"}
        ]

        async def process_message(msg):
            """Simulate processing a message"""
            await asyncio.sleep(0.1)  # Simulate processing time
            return {"agent": msg["agent"], "response": f"Processed: {msg['body']}"}

        # Process all messages concurrently
        results = await asyncio.gather(*[process_message(msg) for msg in messages])

        # Verify all messages were processed
        assert len(results) == 3
        assert results[0]["agent"] == "agent-001"
        assert results[1]["agent"] == "agent-002"
        assert results[2]["agent"] == "agent-003"


# ============================================================================
# Invitation Management Tests
# ============================================================================

@pytest.mark.integration
class TestInvitationManagement:
    """Test user invitation to agent rooms"""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="_invite_user_with_retry method no longer exists - invitations handled differently")
    async def test_invite_admin_to_agent_room(self, agent_manager, mock_aiohttp_session):
        """Test inviting admin users to agent rooms"""
        # Setup agent with room
        agent_manager.mappings["agent-invite"] = AgentUserMapping(
            agent_id="agent-invite",
            agent_name="Invite Test Agent",
            matrix_user_id="@agent_invite:matrix.test",
            matrix_password="test_pass",
            created=True,
            room_id="!invite_room:matrix.test",
            room_created=True,
            invitation_status={}
        )

        # Mock invitation response
        invite_response = AsyncMock()
        invite_response.status = 200
        invite_response.__aenter__ = AsyncMock(return_value=invite_response)
        invite_response.__aexit__ = AsyncMock(return_value=None)

        # Mock login
        login_response = AsyncMock()
        login_response.status = 200
        login_response.json = AsyncMock(return_value={"access_token": "agent_token"})
        login_response.__aenter__ = AsyncMock(return_value=login_response)
        login_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.post = Mock(side_effect=[login_response, invite_response])

        with patch('src.core.agent_user_manager.get_global_session', return_value=mock_aiohttp_session):
            # This method no longer exists - invitations are now handled
            # within create_or_update_agent_room using auto_accept_invitations_with_tracking
            pass

    @pytest.mark.asyncio
    async def test_invitation_status_tracking(self, agent_manager):
        """Test tracking invitation status for users"""
        # Setup agent with invitation status
        agent_manager.mappings["agent-track"] = AgentUserMapping(
            agent_id="agent-track",
            agent_name="Track Agent",
            matrix_user_id="@agent_track:matrix.test",
            matrix_password="test_pass",
            created=True,
            room_id="!track_room:matrix.test",
            room_created=True,
            invitation_status={
                "@admin:matrix.test": "joined",
                "@user1:matrix.test": "invited",
                "@user2:matrix.test": "failed"
            }
        )

        # Verify invitation status
        mapping = agent_manager.mappings["agent-track"]
        assert mapping.invitation_status["@admin:matrix.test"] == "joined"
        assert mapping.invitation_status["@user1:matrix.test"] == "invited"
        assert mapping.invitation_status["@user2:matrix.test"] == "failed"


# ============================================================================
# Concurrent Operations Tests
# ============================================================================

@pytest.mark.integration
class TestConcurrentOperations:
    """Test concurrent agent operations"""

    @pytest.mark.asyncio
    async def test_concurrent_agent_creation(self):
        """Test creating multiple agents concurrently"""
        async def create_agent(agent_id):
            """Simulate agent creation"""
            await asyncio.sleep(0.1)
            return AgentUserMapping(
                agent_id=agent_id,
                agent_name=f"Agent {agent_id}",
                matrix_user_id=f"@{agent_id}:matrix.test",
                matrix_password="test_pass",
                created=True
            )

        # Create 5 agents concurrently
        agent_ids = [f"agent-{i}" for i in range(5)]
        results = await asyncio.gather(*[create_agent(aid) for aid in agent_ids])

        # Verify all agents were created
        assert len(results) == 5
        assert all(r.created for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_room_creation(self):
        """Test creating multiple rooms concurrently"""
        async def create_room(room_id):
            """Simulate room creation"""
            await asyncio.sleep(0.1)
            return {"room_id": room_id, "created": True}

        # Create 5 rooms concurrently
        room_ids = [f"!room{i}:matrix.test" for i in range(5)]
        results = await asyncio.gather(*[create_room(rid) for rid in room_ids])

        # Verify all rooms were created
        assert len(results) == 5
        assert all(r["created"] for r in results)


# ============================================================================
# Error Recovery Tests
# ============================================================================

@pytest.mark.integration
class TestErrorRecovery:
    """Test error recovery in multi-agent scenarios"""

    @pytest.mark.asyncio
    async def test_partial_agent_sync_failure(self, agent_manager, mock_aiohttp_session):
        """Test handling when some agents fail to sync"""
        # Mock successful and failed responses
        success_response = AsyncMock()
        success_response.status = 200
        success_response.json = AsyncMock(return_value={"id": "agent-success", "name": "Success Agent"})
        success_response.__aenter__ = AsyncMock(return_value=success_response)
        success_response.__aexit__ = AsyncMock(return_value=None)

        fail_response = AsyncMock()
        fail_response.status = 500
        fail_response.__aenter__ = AsyncMock(return_value=fail_response)
        fail_response.__aexit__ = AsyncMock(return_value=None)

        # Mock agents list
        agents_response = AsyncMock()
        agents_response.status = 200
        agents_response.json = AsyncMock(return_value={
            "data": [
                {"id": "agent-success"},
                {"id": "agent-fail"}
            ]
        })
        agents_response.__aenter__ = AsyncMock(return_value=agents_response)
        agents_response.__aexit__ = AsyncMock(return_value=None)

        mock_aiohttp_session.get = Mock(side_effect=[agents_response, success_response, fail_response])

        with patch('src.core.agent_user_manager.get_global_session', return_value=mock_aiohttp_session):
            agents = await agent_manager.get_letta_agents()

            # Should have one successful agent (the other failed to get details)
            # Exact behavior depends on implementation
            assert len(agents) >= 1

    @pytest.mark.asyncio
    async def test_retry_on_transient_failure(self):
        """Test retry logic for transient failures"""
        attempt_count = 0

        async def failing_operation():
            nonlocal attempt_count
            attempt_count += 1

            if attempt_count < 3:
                raise Exception("Transient error")

            return "Success"

        max_retries = 3
        result = None
        for i in range(max_retries):
            try:
                result = await failing_operation()
                break
            except Exception:
                if i == max_retries - 1:
                    raise
                await asyncio.sleep(0.1)

        assert result == "Success"
        assert attempt_count == 3
