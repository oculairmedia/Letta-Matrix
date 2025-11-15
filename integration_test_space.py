#!/usr/bin/env python3
"""
Integration test for Matrix Space management
Run this against a live Matrix server to verify space functionality
"""
import asyncio
import json
import os
import sys
from dataclasses import dataclass

# Import the agent manager
from agent_user_manager import AgentUserManager


@dataclass
class TestConfig:
    """Test configuration"""
    homeserver_url: str = os.getenv("MATRIX_HOMESERVER_URL", "http://localhost:8008")
    username: str = os.getenv("MATRIX_USERNAME", "@letta:matrix.oculair.ca")
    password: str = os.getenv("MATRIX_PASSWORD", "letta")
    letta_api_url: str = os.getenv("LETTA_API_URL", "https://letta.oculair.ca")
    letta_token: str = os.getenv("LETTA_TOKEN", "lettaSecurePass123")
    letta_agent_id: str = os.getenv("LETTA_AGENT_ID", "agent-test")


class IntegrationTest:
    """Integration test suite"""

    def __init__(self):
        self.config = TestConfig()
        self.manager = AgentUserManager(self.config)
        self.results = []

    def log(self, message: str, success: bool = True):
        """Log test result"""
        status = "✅" if success else "❌"
        print(f"{status} {message}")
        self.results.append((message, success))

    async def test_space_creation(self):
        """Test creating the Letta Agents space"""
        print("\n" + "=" * 60)
        print("TEST 1: Space Creation")
        print("=" * 60)

        try:
            # Load existing space config
            await self.manager.load_space_config()

            if self.manager.space_id:
                self.log(f"Found existing space: {self.manager.space_id}")
            else:
                self.log("No existing space found, creating new one")
                space_id = await self.manager.create_letta_agents_space()

                if space_id:
                    self.log(f"Successfully created space: {space_id}", True)
                else:
                    self.log("Failed to create space", False)
                    return False

            return True

        except Exception as e:
            self.log(f"Error in space creation: {e}", False)
            return False

    async def test_space_persistence(self):
        """Test that space configuration persists"""
        print("\n" + "=" * 60)
        print("TEST 2: Space Persistence")
        print("=" * 60)

        try:
            # Save current space config
            if not self.manager.space_id:
                self.log("No space ID to test persistence", False)
                return False

            await self.manager.save_space_config()
            self.log("Saved space configuration", True)

            # Load in a new manager instance
            new_manager = AgentUserManager(self.config)
            await new_manager.load_space_config()

            if new_manager.space_id == self.manager.space_id:
                self.log("Space ID persisted correctly", True)
                return True
            else:
                self.log("Space ID mismatch after reload", False)
                return False

        except Exception as e:
            self.log(f"Error in space persistence: {e}", False)
            return False

    async def test_agent_discovery(self):
        """Test agent discovery"""
        print("\n" + "=" * 60)
        print("TEST 3: Agent Discovery")
        print("=" * 60)

        try:
            agents = await self.manager.get_letta_agents()
            self.log(f"Discovered {len(agents)} Letta agents", len(agents) > 0)

            for agent in agents:
                print(f"  - {agent.get('name', 'Unnamed')} ({agent.get('id', 'No ID')})")

            return len(agents) > 0

        except Exception as e:
            self.log(f"Error in agent discovery: {e}", False)
            return False

    async def test_room_to_space_relationship(self):
        """Test adding a room to the space"""
        print("\n" + "=" * 60)
        print("TEST 4: Room to Space Relationship")
        print("=" * 60)

        try:
            # Load existing mappings
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
            success = await self.manager.add_room_to_space(
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
            return False

    async def test_migration(self):
        """Test migration of existing rooms"""
        print("\n" + "=" * 60)
        print("TEST 5: Room Migration")
        print("=" * 60)

        try:
            # Load existing mappings
            await self.manager.load_existing_mappings()
            await self.manager.load_space_config()

            if not self.manager.space_id:
                self.log("No space available for migration test", False)
                return False

            room_count = sum(1 for m in self.manager.mappings.values()
                           if m.room_id and m.room_created)

            if room_count == 0:
                self.log("No rooms available to migrate", False)
                return False

            self.log(f"Found {room_count} rooms to migrate", True)

            # Perform migration
            migrated = await self.manager.migrate_existing_rooms_to_space()

            self.log(f"Migrated {migrated} rooms to space", migrated > 0)
            return migrated > 0

        except Exception as e:
            self.log(f"Error in migration: {e}", False)
            return False

    async def test_full_sync(self):
        """Test full agent sync process"""
        print("\n" + "=" * 60)
        print("TEST 6: Full Agent Sync")
        print("=" * 60)

        try:
            # Run full sync
            await self.manager.sync_agents_to_users()

            self.log("Completed agent sync", True)

            # Verify space exists
            if self.manager.space_id:
                self.log(f"Space ID: {self.manager.space_id}", True)
            else:
                self.log("No space ID after sync", False)
                return False

            # Check mappings
            mapping_count = len(self.manager.mappings)
            self.log(f"Created/updated {mapping_count} agent mappings", mapping_count > 0)

            return True

        except Exception as e:
            self.log(f"Error in full sync: {e}", False)
            return False

    async def run_all_tests(self):
        """Run all integration tests"""
        print("\n" + "=" * 60)
        print("MATRIX SPACE INTEGRATION TESTS")
        print("=" * 60)
        print(f"Homeserver: {self.config.homeserver_url}")
        print(f"User: {self.config.username}")
        print(f"Letta API: {self.config.letta_api_url}")
        print("=" * 60)

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
        if self.manager.space_id:
            print("\n" + "=" * 60)
            print("SPACE INFORMATION")
            print("=" * 60)
            print(f"Space ID: {self.manager.space_id}")
            print(f"Space Name: Letta Agents")
            print(f"Agent Count: {len(self.manager.mappings)}")
            print("\nAgent Rooms:")
            for agent_id, mapping in self.manager.mappings.items():
                if mapping.room_id:
                    print(f"  - {mapping.agent_name}: {mapping.room_id}")
            print("=" * 60)

        return passed == total


async def main():
    """Main entry point"""
    test_suite = IntegrationTest()
    success = await test_suite.run_all_tests()

    if success:
        print("\n✅ All integration tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some integration tests failed")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
