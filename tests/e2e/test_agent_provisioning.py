"""
E2E Tests for Agent Provisioning

These tests verify the complete agent provisioning flow by:
1. Creating agents in Letta
2. Triggering provisioning
3. Verifying the actual state on Matrix server

This would have caught the "wrong display name" bug because it
queries the Matrix server directly instead of mocking.
"""

import pytest
import asyncio
import uuid
import os
from typing import Optional

# Skip all tests if E2E is not enabled
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.getenv("MATRIX_ADMIN_PASSWORD"),
        reason="E2E tests require MATRIX_ADMIN_PASSWORD to be set"
    )
]


class TestAgentDisplayName:
    """Tests for agent display name provisioning"""
    
    @pytest.mark.asyncio
    async def test_new_agent_has_correct_display_name(self, matrix_client, letta_client, e2e_config):
        """
        E2E test that verifies:
        1. Create a new agent in Letta
        2. Trigger provisioning (sync)
        3. Query Matrix server directly
        4. Display name matches agent name exactly
        
        This test would have FAILED with the old code that set
        display name to "Letta Agent: {name}" instead of just "{name}"
        """
        # Generate unique test agent name
        test_name = f"E2E-Test-Agent-{uuid.uuid4().hex[:8]}"
        agent_id = None
        
        try:
            # Step 1: Create agent in Letta
            agent = await letta_client.create_test_agent(test_name)
            agent_id = agent.get("id")
            assert agent_id, "Failed to create test agent"
            print(f"Created test agent: {agent_id} with name '{test_name}'")
            
            # Step 2: Trigger provisioning by calling sync
            # In a real scenario, this would be done by the matrix-client service
            # For now, we import and call directly
            from src.core.agent_user_manager import AgentUserManager
            from unittest.mock import Mock
            
            # Create minimal config for AgentUserManager
            config = Mock()
            config.homeserver_url = e2e_config.matrix_homeserver_url
            config.username = e2e_config.matrix_admin_username
            config.password = e2e_config.matrix_admin_password
            config.letta_api_url = e2e_config.letta_api_url
            config.letta_token = e2e_config.letta_token
            config.matrix_api_url = "http://matrix-api:8000"
            
            # Set required env vars
            os.environ["MATRIX_ADMIN_USERNAME"] = e2e_config.matrix_admin_username
            os.environ["MATRIX_ADMIN_PASSWORD"] = e2e_config.matrix_admin_password
            os.environ["MATRIX_REGISTRATION_TOKEN"] = os.getenv("MATRIX_REGISTRATION_TOKEN", "test-token")
            os.environ["DEV_MODE"] = "true"
            
            manager = AgentUserManager(config)
            await manager.create_user_for_agent({"id": agent_id, "name": test_name})
            
            # Step 3: Get the Matrix user ID from mapping
            mapping = manager.mappings.get(agent_id)
            assert mapping, f"No mapping created for agent {agent_id}"
            matrix_user_id = mapping.matrix_user_id
            print(f"Matrix user: {matrix_user_id}")
            
            # Step 4: Query Matrix server directly for display name
            actual_display_name = await matrix_client.get_display_name(matrix_user_id)
            print(f"Display name on Matrix server: '{actual_display_name}'")
            
            # Step 5: Verify display name is EXACTLY the agent name
            assert actual_display_name == test_name, (
                f"Display name mismatch!\n"
                f"  Expected: '{test_name}'\n"
                f"  Actual:   '{actual_display_name}'\n"
                f"  This indicates the provisioning code is setting wrong display name."
            )
            
            # Also verify it's NOT the old broken format
            assert actual_display_name != f"Letta Agent: {test_name}", (
                "Display name has old 'Letta Agent: ...' format - bug not fixed!"
            )
            
        finally:
            # Cleanup: Delete test agent from Letta
            if agent_id:
                await letta_client.delete_agent(agent_id)
                print(f"Cleaned up test agent: {agent_id}")
    
    @pytest.mark.asyncio
    async def test_renamed_agent_updates_display_name(self, matrix_client, letta_client, e2e_config):
        """
        E2E test that verifies:
        1. Create agent with name "Original Name"
        2. Provision it
        3. Rename agent in Letta to "New Name"
        4. Trigger sync
        5. Display name on Matrix is updated to "New Name"
        """
        original_name = f"E2E-Original-{uuid.uuid4().hex[:8]}"
        new_name = f"E2E-Renamed-{uuid.uuid4().hex[:8]}"
        agent_id = None
        
        try:
            # Step 1: Create and provision agent
            agent = await letta_client.create_test_agent(original_name)
            agent_id = agent.get("id")
            assert agent_id, "Failed to create test agent"
            
            # Provision
            from src.core.agent_user_manager import AgentUserManager
            from unittest.mock import Mock
            
            config = Mock()
            config.homeserver_url = e2e_config.matrix_homeserver_url
            config.username = e2e_config.matrix_admin_username
            config.password = e2e_config.matrix_admin_password
            config.letta_api_url = e2e_config.letta_api_url
            config.letta_token = e2e_config.letta_token
            config.matrix_api_url = "http://matrix-api:8000"
            
            os.environ["MATRIX_ADMIN_USERNAME"] = e2e_config.matrix_admin_username
            os.environ["MATRIX_ADMIN_PASSWORD"] = e2e_config.matrix_admin_password
            os.environ["DEV_MODE"] = "true"
            
            manager = AgentUserManager(config)
            await manager.create_user_for_agent({"id": agent_id, "name": original_name})
            
            mapping = manager.mappings.get(agent_id)
            assert mapping is not None, f"No mapping for agent {agent_id}"
            matrix_user_id = mapping.matrix_user_id
            
            # Verify original name
            display_name = await matrix_client.get_display_name(matrix_user_id)
            assert display_name == original_name, f"Initial display name wrong: {display_name}"
            
            # Step 2: Rename agent in Letta
            renamed = await letta_client.rename_agent(agent_id, new_name)
            assert renamed, "Failed to rename agent"
            
            # Step 3: Trigger sync (simulate name change detection)
            # The sync_agents_to_users would detect the name change
            # For this test, we directly call update_display_name
            password = mapping.matrix_password if mapping else None
            assert password is not None, "No password in mapping"
            success = await manager.update_display_name(
                matrix_user_id, 
                new_name, 
                password
            )
            assert success, "Failed to update display name"
            
            # Step 4: Verify new display name on Matrix
            updated_display_name = await matrix_client.get_display_name(matrix_user_id)
            assert updated_display_name == new_name, (
                f"Display name not updated after rename!\n"
                f"  Expected: '{new_name}'\n"
                f"  Actual:   '{updated_display_name}'"
            )
            
        finally:
            if agent_id:
                await letta_client.delete_agent(agent_id)


class TestExistingAgentVerification:
    """Tests that verify existing agents have correct display names"""
    
    @pytest.mark.asyncio
    async def test_verify_all_agents_have_correct_display_names(self, matrix_client, e2e_config):
        """
        Scan all agent mappings and verify their Matrix display names
        match their Letta agent names.
        
        This is a regression test that can be run periodically to ensure
        no agents have drifted to incorrect display names.
        """
        import asyncpg
        
        # Connect to database
        conn = await asyncpg.connect(e2e_config.database_url)
        
        try:
            # Get all agent mappings
            agents = await conn.fetch("""
                SELECT agent_id, agent_name, matrix_user_id 
                FROM agent_mappings 
                WHERE matrix_user_id IS NOT NULL
            """)
            
            mismatches = []
            
            for agent in agents:
                agent_name = agent["agent_name"]
                matrix_user_id = agent["matrix_user_id"]
                
                # Get display name from Matrix
                display_name = await matrix_client.get_display_name(matrix_user_id)
                
                if display_name != agent_name:
                    mismatches.append({
                        "agent_id": agent["agent_id"],
                        "agent_name": agent_name,
                        "matrix_user_id": matrix_user_id,
                        "display_name": display_name
                    })
            
            if mismatches:
                mismatch_str = "\n".join([
                    f"  - {m['agent_name']}: expected '{m['agent_name']}', got '{m['display_name']}'"
                    for m in mismatches
                ])
                pytest.fail(
                    f"Found {len(mismatches)} agents with incorrect display names:\n{mismatch_str}"
                )
            
            print(f"Verified {len(agents)} agents - all display names correct")
            
        finally:
            await conn.close()


class TestBootstrapIdempotency:
    """Tests for bootstrap/re-provisioning idempotency"""
    
    @pytest.mark.asyncio
    async def test_reprovisioning_preserves_display_name(self, matrix_client, letta_client, e2e_config):
        """
        E2E test that verifies:
        1. Provision an agent
        2. Run provisioning again
        3. Display name is still correct (not overwritten with wrong value)
        """
        test_name = f"E2E-Idempotent-{uuid.uuid4().hex[:8]}"
        agent_id = None
        
        try:
            # Create and provision
            agent = await letta_client.create_test_agent(test_name)
            agent_id = agent.get("id")
            
            from src.core.agent_user_manager import AgentUserManager
            from unittest.mock import Mock
            
            config = Mock()
            config.homeserver_url = e2e_config.matrix_homeserver_url
            config.username = e2e_config.matrix_admin_username
            config.password = e2e_config.matrix_admin_password
            config.letta_api_url = e2e_config.letta_api_url
            config.letta_token = e2e_config.letta_token
            config.matrix_api_url = "http://matrix-api:8000"
            
            os.environ["MATRIX_ADMIN_USERNAME"] = e2e_config.matrix_admin_username
            os.environ["MATRIX_ADMIN_PASSWORD"] = e2e_config.matrix_admin_password
            os.environ["DEV_MODE"] = "true"
            
            # First provisioning
            manager1 = AgentUserManager(config)
            await manager1.create_user_for_agent({"id": agent_id, "name": test_name})
            matrix_user_id = manager1.mappings[agent_id].matrix_user_id
            
            display_name_1 = await matrix_client.get_display_name(matrix_user_id)
            assert display_name_1 == test_name
            
            # Second provisioning (simulating restart/re-sync)
            manager2 = AgentUserManager(config)
            await manager2.load_existing_mappings()
            await manager2.create_user_for_agent({"id": agent_id, "name": test_name})
            
            # Verify display name unchanged
            display_name_2 = await matrix_client.get_display_name(matrix_user_id)
            assert display_name_2 == test_name, (
                f"Display name changed after re-provisioning!\n"
                f"  Before: '{display_name_1}'\n"
                f"  After:  '{display_name_2}'"
            )
            
        finally:
            if agent_id:
                await letta_client.delete_agent(agent_id)
