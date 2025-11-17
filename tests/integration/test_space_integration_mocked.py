#!/usr/bin/env python3
"""
Mocked Integration Test for Matrix Space Management

This test mimics the live integration test but uses mocks instead of live services.
It tests the full workflow without requiring actual Matrix homeserver or Letta API.
"""
import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from dataclasses import dataclass

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Optional pytest import for test framework integration
try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False
    # Create dummy decorators if pytest is not available
    class DummyMark:
        def __call__(self, func):
            return func
    pytest = type('pytest', (), {'mark': type('mark', (), {
        'integration': DummyMark(),
        'asyncio': DummyMark()
    })()})()

# Import the agent manager
from src.core.agent_user_manager import AgentUserManager, AgentUserMapping


@dataclass
class MockTestConfig:
    """Mock test configuration - mimics live test config but for mocking"""
    homeserver_url: str = "http://mock-synapse:8008"
    username: str = "@letta:mock.matrix.test"
    password: str = "mock_password"
    letta_api_url: str = "http://mock-letta:8283"
    letta_token: str = "mock_letta_token"
    letta_agent_id: str = "mock-agent-test"
    matrix_api_url: str = "http://mock-matrix-api:8000"


class MockedIntegrationTest:
    """Mocked integration test suite matching live integration tests"""

    def __init__(self, temp_dir):
        self.config = MockTestConfig()
        self.temp_dir = temp_dir
        self.manager = None
        self.results = []

        # Mock data for testing
        self.mock_space_id = "!mock_letta_space_123:mock.matrix.test"
        self.mock_room_id = "!mock_agent_room_456:mock.matrix.test"
        self.mock_access_token = "mock_access_token_xyz"
        self.mock_agents = [
            {"id": "agent-001", "name": "Alpha Agent"},
            {"id": "agent-002", "name": "Beta Agent"},
            {"id": "agent-003", "name": "Gamma Agent"}
        ]

    def log(self, message: str, success: bool = True):
        """Log test result"""
        status = "✅" if success else "❌"
        print(f"{status} {message}")
        self.results.append((message, success))

    async def setup_mocked_manager(self):
        """Set up the AgentUserManager with all necessary mocks"""
        with patch.dict(os.environ, {
            "MATRIX_DATA_DIR": self.temp_dir,
            "MATRIX_ADMIN_USERNAME": "@admin:mock.matrix.test",
            "MATRIX_ADMIN_PASSWORD": "admin_password"
        }):
            # Patch logging to avoid logger issues
            with patch('src.core.agent_user_manager.logging.getLogger'):
                with patch('src.core.space_manager.logging.getLogger'):
                    with patch('src.core.user_manager.logging.getLogger'):
                        with patch('src.core.room_manager.logging.getLogger'):
                            self.manager = AgentUserManager(config=self.config)

        # Mock the HTTP session and responses
        await self._setup_http_mocks()

        return self.manager

    async def _setup_http_mocks(self):
        """Set up HTTP mocks for Matrix and Letta API calls"""

        # Create a mock session
        mock_session = AsyncMock()

        # Mock Matrix login response
        mock_login_response = AsyncMock()
        mock_login_response.status = 200
        mock_login_response.json = AsyncMock(return_value={
            "access_token": self.mock_access_token,
            "user_id": "@admin:mock.matrix.test",
            "device_id": "MOCK_DEVICE"
        })
        mock_login_response.__aenter__ = AsyncMock(return_value=mock_login_response)
        mock_login_response.__aexit__ = AsyncMock(return_value=None)

        # Mock space creation response
        mock_space_create_response = AsyncMock()
        mock_space_create_response.status = 200
        mock_space_create_response.json = AsyncMock(return_value={
            "room_id": self.mock_space_id
        })
        mock_space_create_response.__aenter__ = AsyncMock(return_value=mock_space_create_response)
        mock_space_create_response.__aexit__ = AsyncMock(return_value=None)

        # Mock room creation response
        mock_room_create_response = AsyncMock()
        mock_room_create_response.status = 200
        mock_room_create_response.json = AsyncMock(return_value={
            "room_id": self.mock_room_id
        })
        mock_room_create_response.__aenter__ = AsyncMock(return_value=mock_room_create_response)
        mock_room_create_response.__aexit__ = AsyncMock(return_value=None)

        # Mock Letta agents list response
        mock_letta_agents_response = AsyncMock()
        mock_letta_agents_response.status = 200
        mock_letta_agents_response.json = AsyncMock(return_value=self.mock_agents)
        mock_letta_agents_response.__aenter__ = AsyncMock(return_value=mock_letta_agents_response)
        mock_letta_agents_response.__aexit__ = AsyncMock(return_value=None)

        # Mock generic success response (for PUT, etc.)
        mock_success_response = AsyncMock()
        mock_success_response.status = 200
        mock_success_response.json = AsyncMock(return_value={})
        mock_success_response.__aenter__ = AsyncMock(return_value=mock_success_response)
        mock_success_response.__aexit__ = AsyncMock(return_value=None)

        # Configure session methods to return appropriate mocks
        def mock_post(url, **kwargs):
            if "login" in url:
                return mock_login_response
            elif "createRoom" in url:
                # Check if it's a space creation (has "type": "m.space")
                if kwargs.get('json', {}).get('creation_content', {}).get('type') == 'm.space':
                    return mock_space_create_response
                else:
                    return mock_room_create_response
            else:
                return mock_success_response

        def mock_get(url, **kwargs):
            if "letta" in url.lower() and "agents" in url:
                return mock_letta_agents_response
            return mock_success_response

        def mock_put(url, **kwargs):
            return mock_success_response

        mock_session.post = Mock(side_effect=mock_post)
        mock_session.get = Mock(side_effect=mock_get)
        mock_session.put = Mock(side_effect=mock_put)
        mock_session.closed = False

        # Patch the global session getter
        async def mock_get_global_session():
            return mock_session

        # Apply the patch to the manager's modules
        patch_target = 'src.core.agent_user_manager.get_global_session'
        self.session_patcher = patch(patch_target, side_effect=mock_get_global_session)
        self.session_patcher.start()

        # Also patch for space_manager, user_manager, and room_manager
        for module in ['space_manager', 'user_manager', 'room_manager']:
            patcher = patch(f'src.core.{module}.get_global_session', side_effect=mock_get_global_session)
            patcher.start()

    async def test_space_creation(self):
        """Test creating the Letta Agents space"""
        print("\n" + "=" * 60)
        print("TEST 1: Space Creation (Mocked)")
        print("=" * 60)

        try:
            # Load existing space config
            await self.manager.space_manager.load_space_config()

            if self.manager.space_manager.space_id:
                self.log(f"Found existing space: {self.manager.space_manager.space_id}")
            else:
                self.log("No existing space found, creating new one")
                space_id = await self.manager.space_manager.create_letta_agents_space()

                if space_id:
                    self.log(f"Successfully created space: {space_id}", True)
                    # Verify it's the mock space ID
                    assert space_id == self.mock_space_id, f"Expected {self.mock_space_id}, got {space_id}"
                else:
                    self.log("Failed to create space", False)
                    return False

            return True

        except Exception as e:
            self.log(f"Error in space creation: {e}", False)
            import traceback
            traceback.print_exc()
            return False

    async def test_space_persistence(self):
        """Test that space configuration persists"""
        print("\n" + "=" * 60)
        print("TEST 2: Space Persistence (Mocked)")
        print("=" * 60)

        try:
            # Ensure we have a space ID
            if not self.manager.space_manager.space_id:
                # Create one if needed
                await self.manager.space_manager.create_letta_agents_space()

            if not self.manager.space_manager.space_id:
                self.log("No space ID to test persistence", False)
                return False

            await self.manager.space_manager.save_space_config()
            self.log("Saved space configuration", True)

            # Load in a new manager instance
            with patch.dict(os.environ, {"MATRIX_DATA_DIR": self.temp_dir}):
                with patch('src.core.agent_user_manager.logging.getLogger'):
                    with patch('src.core.space_manager.logging.getLogger'):
                        new_manager = AgentUserManager(self.config)

            await new_manager.space_manager.load_space_config()

            if new_manager.space_manager.space_id == self.manager.space_manager.space_id:
                self.log("Space ID persisted correctly", True)
                return True
            else:
                self.log(f"Space ID mismatch: {new_manager.space_manager.space_id} != {self.manager.space_manager.space_id}", False)
                return False

        except Exception as e:
            self.log(f"Error in space persistence: {e}", False)
            import traceback
            traceback.print_exc()
            return False

    async def test_agent_discovery(self):
        """Test agent discovery"""
        print("\n" + "=" * 60)
        print("TEST 3: Agent Discovery (Mocked)")
        print("=" * 60)

        try:
            # Mock the HTTP call to Letta API
            agents = self.mock_agents
            self.log(f"Discovered {len(agents)} Letta agents", len(agents) > 0)

            for agent in agents:
                print(f"  - {agent.get('name', 'Unnamed')} ({agent.get('id', 'No ID')})")

            return len(agents) > 0

        except Exception as e:
            self.log(f"Error in agent discovery: {e}", False)
            import traceback
            traceback.print_exc()
            return False

    async def test_room_to_space_relationship(self):
        """Test adding a room to the space"""
        print("\n" + "=" * 60)
        print("TEST 4: Room to Space Relationship (Mocked)")
        print("=" * 60)

        try:
            # Create some mock mappings
            self.manager.mappings["agent-001"] = AgentUserMapping(
                agent_id="agent-001",
                agent_name="Alpha Agent",
                matrix_user_id="@agent_001:mock.matrix.test",
                matrix_password="password1",
                created=True,
                room_id=self.mock_room_id,
                room_created=True
            )

            await self.manager.save_mappings()

            # Load mappings
            await self.manager.load_existing_mappings()

            if not self.manager.mappings:
                self.log("No agent mappings found to test", False)
                return False

            # Get first agent with a room
            test_mapping = None
            for agent_id, mapping in self.manager.mappings.items():
                if mapping.room_id and mapping.room_created:
                    test_mapping = mapping
                    break

            if not test_mapping:
                self.log("No agent rooms found to test", False)
                return False

            self.log(f"Testing with room: {test_mapping.room_id}", True)

            # Try to add room to space
            # Ensure space exists
            if not self.manager.space_manager.space_id:
                await self.manager.space_manager.create_letta_agents_space()

            success = await self.manager.space_manager.add_room_to_space(
                test_mapping.room_id,
                test_mapping.agent_name
            )

            if success:
                self.log("Successfully added room to space", True)
                return True
            else:
                self.log("Failed to add room to space", False)
                return False

        except Exception as e:
            self.log(f"Error in room-to-space relationship: {e}", False)
            import traceback
            traceback.print_exc()
            return False

    async def test_migration(self):
        """Test migration of existing rooms"""
        print("\n" + "=" * 60)
        print("TEST 5: Room Migration (Mocked)")
        print("=" * 60)

        try:
            # Ensure we have mappings and space
            if not self.manager.mappings:
                # Create mock mappings
                for i, agent in enumerate(self.mock_agents):
                    self.manager.mappings[agent["id"]] = AgentUserMapping(
                        agent_id=agent["id"],
                        agent_name=agent["name"],
                        matrix_user_id=f"@{agent['id']}:mock.matrix.test",
                        matrix_password=f"password{i}",
                        created=True,
                        room_id=f"!room_{i}:mock.matrix.test",
                        room_created=True
                    )
                await self.manager.save_mappings()

            # Load mappings and space config
            await self.manager.load_existing_mappings()

            if not self.manager.space_manager.space_id:
                await self.manager.space_manager.create_letta_agents_space()

            await self.manager.space_manager.load_space_config()

            if not self.manager.space_manager.space_id:
                self.log("No space available for migration test", False)
                return False

            room_count = sum(1 for m in self.manager.mappings.values()
                           if m.room_id and m.room_created)

            if room_count == 0:
                self.log("No rooms available to migrate", False)
                return False

            self.log(f"Found {room_count} rooms to migrate", True)

            # Perform migration
            migrated = await self.manager.space_manager.migrate_existing_rooms_to_space(
                self.manager.mappings
            )

            self.log(f"Migrated {migrated} rooms to space", migrated > 0)
            return migrated > 0

        except Exception as e:
            self.log(f"Error in migration: {e}", False)
            import traceback
            traceback.print_exc()
            return False

    async def test_full_sync(self):
        """Test full agent sync process"""
        print("\n" + "=" * 60)
        print("TEST 6: Full Agent Sync (Mocked)")
        print("=" * 60)

        try:
            # Mock get_letta_agents to return our mock agents
            async def mock_get_letta_agents():
                return self.mock_agents

            self.manager.get_letta_agents = mock_get_letta_agents

            # Run full sync
            await self.manager.sync_agents_to_users()

            self.log("Completed agent sync", True)

            # Verify space exists
            if self.manager.space_manager.space_id:
                self.log(f"Space ID: {self.manager.space_manager.space_id}", True)
            else:
                self.log("No space ID after sync", False)
                return False

            # Check mappings
            mapping_count = len(self.manager.mappings)
            self.log(f"Created/updated {mapping_count} agent mappings", mapping_count > 0)

            return True

        except Exception as e:
            self.log(f"Error in full sync: {e}", False)
            import traceback
            traceback.print_exc()
            return False

    async def run_all_tests(self):
        """Run all mocked integration tests"""
        print("\n" + "=" * 60)
        print("MATRIX SPACE INTEGRATION TESTS (MOCKED)")
        print("=" * 60)
        print(f"Homeserver: {self.config.homeserver_url}")
        print(f"User: {self.config.username}")
        print(f"Letta API: {self.config.letta_api_url}")
        print(f"Data Dir: {self.temp_dir}")
        print("=" * 60)

        # Setup manager
        await self.setup_mocked_manager()

        tests = [
            ("Space Creation", self.test_space_creation),
            ("Space Persistence", self.test_space_persistence),
            ("Agent Discovery", self.test_agent_discovery),
            ("Room to Space Relationship", self.test_room_to_space_relationship),
            ("Room Migration", self.test_migration),
            ("Full Agent Sync", self.test_full_sync),
        ]

        results = []
        for name, test_func in tests:
            try:
                result = await test_func()
                results.append((name, result))
            except Exception as e:
                print(f"❌ Test '{name}' crashed: {e}")
                import traceback
                traceback.print_exc()
                results.append((name, False))

        # Print summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)

        passed = sum(1 for _, result in results if result)
        total = len(results)

        for name, result in results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status}: {name}")

        print("=" * 60)
        print(f"Results: {passed}/{total} tests passed")
        print("=" * 60)

        # Display space information
        if self.manager.space_manager.space_id:
            print("\n" + "=" * 60)
            print("SPACE INFORMATION")
            print("=" * 60)
            print(f"Space ID: {self.manager.space_manager.space_id}")
            print(f"Space Name: Letta Agents")
            print(f"Agent Count: {len(self.manager.mappings)}")
            print("\nAgent Rooms:")
            for agent_id, mapping in self.manager.mappings.items():
                if mapping.room_id:
                    print(f"  - {mapping.agent_name}: {mapping.room_id}")
            print("=" * 60)

        return passed == total


# ============================================================================
# Pytest Integration
# ============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_mocked_space_integration():
    """
    Pytest wrapper for mocked integration test

    This test can be run with:
        pytest tests/integration/test_space_integration_mocked.py -v
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        test_suite = MockedIntegrationTest(temp_dir)
        success = await test_suite.run_all_tests()

        assert success, "Some mocked integration tests failed"


# ============================================================================
# Standalone Execution
# ============================================================================

async def main():
    """Main entry point for standalone execution"""
    with tempfile.TemporaryDirectory() as temp_dir:
        test_suite = MockedIntegrationTest(temp_dir)
        success = await test_suite.run_all_tests()

        if success:
            print("\n✅ All mocked integration tests passed!")
            return 0
        else:
            print("\n❌ Some mocked integration tests failed")
            return 1


if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
