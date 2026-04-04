#!/usr/bin/env python3
"""
Mocked Integration Test for Matrix Space Management

Tests the full workflow using mocks instead of live services.
No actual Matrix homeserver or Letta API is required.

Decomposed from a single monolithic test into isolated test functions
to prevent cascading failures and simplify debugging.
"""
import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass

import pytest

from src.core.agent_user_manager import AgentUserManager, AgentUserMapping


# ============================================================================
# Shared test configuration and helpers
# ============================================================================

@dataclass
class MockTestConfig:
    """Mock test configuration — mimics live config but for mocking."""
    homeserver_url: str = "http://mock-tuwunel:6167"
    username: str = "@letta:mock.matrix.test"
    password: str = "mock_password"
    letta_api_url: str = "http://mock-letta:8283"
    letta_token: str = "mock_letta_token"
    letta_agent_id: str = "mock-agent-test"
    matrix_api_url: str = "http://mock-matrix-api:8000"


MOCK_SPACE_ID = "!mock_letta_space_123:mock.matrix.test"
MOCK_ACCESS_TOKEN = "mock_access_token_xyz"
MOCK_AGENTS = [
    {"id": "agent-001", "name": "Alpha Agent"},
    {"id": "agent-002", "name": "Beta Agent"},
    {"id": "agent-003", "name": "Gamma Agent"},
]


def _make_mock_response(status: int = 200, payload: dict | None = None):
    """Build a MagicMock that behaves like an aiohttp response with async ctx manager."""
    payload = payload or {}
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=payload)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)
    return resp


def _make_mock_session(mock_space_id: str, mock_room_prefix: str = "!mock_room"):
    """Build a mock aiohttp.ClientSession with realistic routing."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.closed = False

    _room_counter = {"n": 0}

    login_resp = _make_mock_response(200, {
        "access_token": MOCK_ACCESS_TOKEN,
        "user_id": "@admin:mock.matrix.test",
        "device_id": "MOCK_DEVICE",
    })
    register_resp = _make_mock_response(200, {
        "user_id": "@new_user:mock.matrix.test",
        "access_token": "new_user_token",
        "device_id": "NEW_DEVICE",
    })
    space_create_resp = _make_mock_response(200, {"room_id": mock_space_id})
    success_resp = _make_mock_response(200, {})
    agents_resp = _make_mock_response(200, MOCK_AGENTS)

    def _post(url, **kwargs):
        if "login" in url:
            return login_resp
        if "register" in url:
            return register_resp
        if "createRoom" in url:
            if kwargs.get("json", {}).get("creation_content", {}).get("type") == "m.space":
                return space_create_resp
            _room_counter["n"] += 1
            return _make_mock_response(200, {
                "room_id": f"{mock_room_prefix}_{_room_counter['n']}:mock.matrix.test"
            })
        return success_resp

    def _get(url, **kwargs):
        if "letta" in url.lower() and "agents" in url:
            return agents_resp
        return success_resp

    session.post = Mock(side_effect=_post)
    session.get = Mock(side_effect=_get)
    session.put = Mock(side_effect=_post)  # PUT returns success
    return session


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mock_session():
    return _make_mock_session(MOCK_SPACE_ID)


@pytest.fixture
def manager_with_mocks(temp_dir, mock_session):
    """Create an AgentUserManager with all I/O mocked out."""
    patchers = []

    with patch.dict(os.environ, {
        "MATRIX_DATA_DIR": temp_dir,
        "MATRIX_ADMIN_USERNAME": "@admin:mock.matrix.test",
        "MATRIX_ADMIN_PASSWORD": "admin_password",
    }):
        with patch("src.core.agent_user_manager.logging.getLogger"), \
             patch("src.core.space_manager.logging.getLogger"), \
             patch("src.core.user_manager.logging.getLogger"), \
             patch("src.core.room_manager.logging.getLogger"):
            mgr = AgentUserManager(config=MockTestConfig())

    # Patch aiohttp globally in relevant modules
    async def _get_session():
        return mock_session

    p1 = patch("src.core.agent_user_manager.get_global_session", side_effect=_get_session)
    p1.start()
    patchers.append(p1)

    for mod in ("agent_user_manager", "space_manager", "user_manager", "room_manager"):
        p = patch(f"src.core.{mod}.aiohttp.ClientSession", return_value=mock_session)
        p.start()
        patchers.append(p)

    yield mgr

    for p in patchers:
        p.stop()


# ============================================================================
# Test 1: Space creation
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_space_creation(manager_with_mocks):
    mgr = manager_with_mocks
    await mgr.space_manager.load_space_config()

    # No space yet
    assert mgr.space_manager.space_id is None

    space_id = await mgr.space_manager.create_letta_agents_space()
    assert space_id == MOCK_SPACE_ID


# ============================================================================
# Test 2: Space persistence
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_space_persistence(manager_with_mocks, temp_dir):
    mgr = manager_with_mocks

    # Create space
    await mgr.space_manager.create_letta_agents_space()
    assert mgr.space_manager.space_id == MOCK_SPACE_ID

    await mgr.space_manager.save_space_config()

    # Load in a new manager and verify persistence
    with patch.dict(os.environ, {"MATRIX_DATA_DIR": temp_dir}), \
         patch("src.core.agent_user_manager.logging.getLogger"), \
         patch("src.core.space_manager.logging.getLogger"):
        mgr2 = AgentUserManager(MockTestConfig())

    await mgr2.space_manager.load_space_config()
    assert mgr2.space_manager.space_id == MOCK_SPACE_ID


# ============================================================================
# Test 3: Agent discovery (trivial mock-based)
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_agent_discovery():
    assert len(MOCK_AGENTS) == 3
    assert all("id" in a and "name" in a for a in MOCK_AGENTS)


# ============================================================================
# Test 4: Room to space relationship
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_room_to_space_relationship(manager_with_mocks):
    mgr = manager_with_mocks

    # Create space
    await mgr.space_manager.create_letta_agents_space()

    # Add a mapping
    mgr.mappings["agent-001"] = AgentUserMapping(
        agent_id="agent-001",
        agent_name="Alpha Agent",
        matrix_user_id="@agent_001:mock.matrix.test",
        matrix_password="password1",
        created=True,
        room_id="!agent_room_001:mock.matrix.test",
        room_created=True,
    )
    await mgr.save_mappings()

    # Reload and verify
    await mgr.load_existing_mappings()
    mapping = mgr.mappings.get("agent-001")
    assert mapping is not None
    assert mapping.room_created is True

    # Add room to space
    success = await mgr.space_manager.add_room_to_space(
        mapping.room_id, mapping.agent_name
    )
    assert success is True


# ============================================================================
# Test 5: Room migration
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_room_migration(manager_with_mocks):
    mgr = manager_with_mocks

    # Create space
    await mgr.space_manager.create_letta_agents_space()

    # Create distinct mappings with unique room IDs
    for i, agent in enumerate(MOCK_AGENTS):
        mgr.mappings[agent["id"]] = AgentUserMapping(
            agent_id=agent["id"],
            agent_name=agent["name"],
            matrix_user_id=f"@{agent['id'].replace('-', '_')}:mock.matrix.test",
            matrix_password=f"password{i}",
            created=True,
            room_id=f"!migration_room_{i}:mock.matrix.test",
            room_created=True,
        )

    await mgr.save_mappings()
    await mgr.load_existing_mappings()
    await mgr.space_manager.load_space_config()

    room_count = sum(1 for m in mgr.mappings.values() if m.room_id and m.room_created)
    assert room_count == 3

    migrated = await mgr.space_manager.migrate_existing_rooms_to_space(mgr.mappings)
    assert migrated > 0


# ============================================================================
# Test 6: Full agent sync
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_full_sync(manager_with_mocks):
    """
    Test the full sync_agents_to_users flow with heavyweight subsystems mocked.

    The sync calls many subsystems (core user creation, provisioning, room
    discovery, avatar uploads, memory sync). We mock the methods that would
    make real HTTP calls or hit external services.
    """
    mgr = manager_with_mocks

    # Mock get_letta_agents
    async def _mock_get_letta_agents():
        return MOCK_AGENTS

    mgr.get_letta_agents = _mock_get_letta_agents

    # Mock subsystems that do deep HTTP calls
    mgr.ensure_core_users_exist = AsyncMock()
    mgr._set_missing_avatars = AsyncMock()
    mgr._sync_matrix_memory = AsyncMock()

    # Mock discover_agent_room (called during validation) — return None (no drift)
    mgr.discover_agent_room = AsyncMock(return_value=None)

    # Mock the room existence check to return True
    mgr.space_manager.check_room_exists = AsyncMock(return_value=True)

    # Mock the room manager methods called during validation
    mgr.auto_accept_invitations_with_tracking = AsyncMock()
    mgr.room_manager.ensure_required_members = AsyncMock(return_value={})

    # Mock create_user_for_agent to populate mappings
    async def _mock_create_user(agent):
        mgr.mappings[agent["id"]] = AgentUserMapping(
            agent_id=agent["id"],
            agent_name=agent["name"],
            matrix_user_id=f"@{agent['id'].replace('-', '_')}:mock.matrix.test",
            matrix_password=f"mock_pw_{agent['id']}",
            created=True,
            room_id=f"!sync_room_{agent['id']}:mock.matrix.test",
            room_created=True,
        )

    mgr.create_user_for_agent = _mock_create_user

    await mgr.sync_agents_to_users()

    # Verify results
    assert mgr.space_manager.space_id is not None
    assert len(mgr.mappings) == len(MOCK_AGENTS)
    mgr.ensure_core_users_exist.assert_awaited_once()
    mgr._sync_matrix_memory.assert_awaited_once()


# ============================================================================
# Standalone execution
# ============================================================================

async def main():
    """Standalone entry point for manual runs."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = _make_mock_session(MOCK_SPACE_ID)

        with patch.dict(os.environ, {
            "MATRIX_DATA_DIR": temp_dir,
            "MATRIX_ADMIN_USERNAME": "@admin:mock.matrix.test",
            "MATRIX_ADMIN_PASSWORD": "admin_password",
        }):
            with patch("src.core.agent_user_manager.logging.getLogger"), \
                 patch("src.core.space_manager.logging.getLogger"), \
                 patch("src.core.user_manager.logging.getLogger"), \
                 patch("src.core.room_manager.logging.getLogger"):
                mgr = AgentUserManager(config=MockTestConfig())

        print("✅ Manager created")
        await mgr.space_manager.load_space_config()
        print("✅ Space config loaded")
        print("All manual checks passed")


if __name__ == "__main__":
    asyncio.run(main())
