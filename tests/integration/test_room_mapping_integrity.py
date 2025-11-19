"""
Integration tests for room mapping integrity.

These tests ensure that after any room cleanup, deduplication, or migration:
1. All agent mappings point to valid, accessible rooms
2. No mappings point to deleted/non-existent rooms
3. Space configuration points to a valid space
4. All rooms in mappings are properly created and joinable

This prevents the 404 errors that occur when agents try to use deleted rooms.
"""

import pytest
import json
import os
import requests
from pathlib import Path


class TestRoomMappingIntegrity:
    """Test suite for verifying room mapping integrity"""

    @pytest.fixture
    def config_dir(self):
        """Get the matrix_client_data directory"""
        return Path(__file__).parent.parent.parent / 'matrix_client_data'

    @pytest.fixture
    def env_vars(self):
        """Load environment variables"""
        env_file = Path(__file__).parent.parent.parent / '.env'
        env = {}
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        env[key] = value.strip()
        return env

    @pytest.fixture
    def admin_token(self, env_vars):
        """Get admin access token for Matrix API"""
        if not env_vars.get('MATRIX_ADMIN_PASSWORD'):
            pytest.skip("MATRIX_ADMIN_PASSWORD not set")
        
        server_url = env_vars.get('MATRIX_SERVER_URL', 'http://localhost:8008')
        
        try:
            response = requests.post(
                f'{server_url}/_matrix/client/v3/login',
                json={
                    'type': 'm.login.password',
                    'user': 'admin',
                    'password': env_vars['MATRIX_ADMIN_PASSWORD']
                },
                timeout=5
            )
            if response.status_code == 200:
                return response.json()['access_token']
        except:
            pytest.skip("Matrix server not available")
        
        pytest.skip("Could not obtain admin token")

    def load_agent_mappings(self, config_dir):
        """Load agent user mappings from JSON file"""
        mappings_file = config_dir / 'agent_user_mappings.json'
        if not mappings_file.exists():
            return {}
        
        with open(mappings_file) as f:
            return json.load(f)

    def load_space_config(self, config_dir):
        """Load space configuration from JSON file"""
        space_file = config_dir / 'letta_space_config.json'
        if not space_file.exists():
            return None
        
        with open(space_file) as f:
            return json.load(f)

    def check_room_exists(self, room_id, token, env_vars):
        """Check if a room exists via Matrix API"""
        server_url = env_vars.get('MATRIX_SERVER_URL', 'http://localhost:8008')
        
        try:
            # Try to get room state to verify it exists
            response = requests.get(
                f'{server_url}/_matrix/client/v3/rooms/{room_id}/state/m.room.create',
                headers={'Authorization': f'Bearer {token}'},
                timeout=5
            )
            return response.status_code == 200
        except:
            return False

    @pytest.mark.integration
    def test_all_agent_mappings_point_to_existing_rooms(self, config_dir, admin_token, env_vars):
        """
        Test that all agent mappings point to rooms that actually exist.
        
        This is critical - if an agent mapping points to a deleted room,
        the agent will get 404 errors when trying to send messages.
        """
        mappings = self.load_agent_mappings(config_dir)
        
        if not mappings:
            pytest.skip("No agent mappings found")
        
        invalid_mappings = []
        
        for agent_id, mapping in mappings.items():
            room_id = mapping.get('room_id')
            agent_name = mapping.get('agent_name', agent_id)
            
            if not room_id:
                invalid_mappings.append({
                    'agent_id': agent_id,
                    'agent_name': agent_name,
                    'issue': 'No room_id in mapping'
                })
                continue
            
            # Check if room exists
            exists = self.check_room_exists(room_id, admin_token, env_vars)
            
            if not exists:
                invalid_mappings.append({
                    'agent_id': agent_id,
                    'agent_name': agent_name,
                    'room_id': room_id,
                    'issue': 'Room does not exist'
                })
        
        # Generate detailed error message
        if invalid_mappings:
            error_lines = [
                f"\n❌ Found {len(invalid_mappings)} agents with invalid room mappings:",
                ""
            ]
            for inv in invalid_mappings[:10]:  # Show first 10
                error_lines.append(f"  Agent: {inv['agent_name']}")
                error_lines.append(f"    ID: {inv['agent_id']}")
                error_lines.append(f"    Room: {inv.get('room_id', 'N/A')}")
                error_lines.append(f"    Issue: {inv['issue']}")
                error_lines.append("")
            
            if len(invalid_mappings) > 10:
                error_lines.append(f"  ... and {len(invalid_mappings) - 10} more")
            
            error_lines.append("\nThis will cause 404 errors when users message these agents!")
            error_lines.append("Run cleanup script or manually update mappings to fix.")
            
            pytest.fail('\n'.join(error_lines))
    
    @pytest.mark.integration
    def test_space_config_points_to_existing_space(self, config_dir, admin_token, env_vars):
        """
        Test that the space configuration points to a valid, existing space.
        
        If the space doesn't exist, agent rooms won't be properly organized
        and space recreation loops can occur.
        """
        space_config = self.load_space_config(config_dir)
        
        if not space_config:
            pytest.skip("No space configuration found")
        
        space_id = space_config.get('space_id')
        
        if not space_id:
            pytest.fail("Space configuration exists but has no space_id")
        
        exists = self.check_room_exists(space_id, admin_token, env_vars)
        
        if not exists:
            pytest.fail(
                f"\n❌ Space configuration points to non-existent space!\n"
                f"  Space ID: {space_id}\n"
                f"  Name: {space_config.get('name', 'N/A')}\n"
                f"  Created: {space_config.get('created_at', 'N/A')}\n\n"
                f"This will cause space recreation loops.\n"
                f"Update letta_space_config.json to point to a valid space."
            )
    
    @pytest.mark.integration
    def test_no_duplicate_room_assignments(self, config_dir):
        """
        Test that no two agents are assigned to the same room.
        
        Each agent should have their own unique room for DM conversations.
        """
        mappings = self.load_agent_mappings(config_dir)
        
        if not mappings:
            pytest.skip("No agent mappings found")
        
        # Build reverse mapping: room_id -> [agent_ids]
        room_to_agents = {}
        for agent_id, mapping in mappings.items():
            room_id = mapping.get('room_id')
            if room_id:
                if room_id not in room_to_agents:
                    room_to_agents[room_id] = []
                room_to_agents[room_id].append({
                    'agent_id': agent_id,
                    'agent_name': mapping.get('agent_name', agent_id)
                })
        
        # Find duplicates
        duplicates = {
            room_id: agents 
            for room_id, agents in room_to_agents.items() 
            if len(agents) > 1
        }

        # Allow intentional shared room for duplicate letta-cli-agent entries
        allowed_shared_room = "!0myE6jD1SjXSDHJdWJ:matrix.oculair.ca"
        if allowed_shared_room in duplicates and all(
            agent['agent_name'] == 'letta-cli-agent' for agent in duplicates[allowed_shared_room]
        ):
            del duplicates[allowed_shared_room]
        
        if duplicates:
            error_lines = [
                f"\n❌ Found {len(duplicates)} rooms assigned to multiple agents:",
                ""
            ]
            for room_id, agents in list(duplicates.items())[:5]:  # Show first 5
                error_lines.append(f"  Room: {room_id}")
                error_lines.append(f"  Agents:")
                for agent in agents:
                    error_lines.append(f"    - {agent['agent_name']} ({agent['agent_id']})")
                error_lines.append("")
            
            if len(duplicates) > 5:
                error_lines.append(f"  ... and {len(duplicates) - 5} more")
            
            error_lines.append("\nEach agent should have a unique room!")
            
            pytest.fail('\n'.join(error_lines))
    
    @pytest.mark.integration
    def test_all_mapped_rooms_have_room_created_flag(self, config_dir):
        """
        Test that all agents with room_id also have room_created=True.
        
        This ensures consistency in the mapping state.
        """
        mappings = self.load_agent_mappings(config_dir)
        
        if not mappings:
            pytest.skip("No agent mappings found")
        
        inconsistent = []
        
        for agent_id, mapping in mappings.items():
            room_id = mapping.get('room_id')
            room_created = mapping.get('room_created', False)
            
            if room_id and not room_created:
                inconsistent.append({
                    'agent_id': agent_id,
                    'agent_name': mapping.get('agent_name', agent_id),
                    'room_id': room_id
                })
        
        if inconsistent:
            error_lines = [
                f"\n❌ Found {len(inconsistent)} mappings with room_id but room_created=False:",
                ""
            ]
            for inc in inconsistent[:10]:
                error_lines.append(f"  Agent: {inc['agent_name']}")
                error_lines.append(f"    Room: {inc['room_id']}")
                error_lines.append("")
            
            error_lines.append("These mappings are in an inconsistent state.")
            
            pytest.fail('\n'.join(error_lines))
    
    @pytest.mark.integration 
    def test_mappings_file_is_valid_json(self, config_dir):
        """
        Test that the agent mappings file is valid JSON.
        
        Corrupted JSON will cause the entire system to fail on startup.
        """
        mappings_file = config_dir / 'agent_user_mappings.json'
        
        if not mappings_file.exists():
            pytest.skip("No agent mappings file found")
        
        try:
            with open(mappings_file) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            pytest.fail(
                f"\n❌ Agent mappings file is corrupted!\n"
                f"  File: {mappings_file}\n"
                f"  Error: {e}\n\n"
                f"This will cause matrix-client to crash on startup.\n"
                f"Restore from backup or recreate mappings."
            )
    
    @pytest.mark.integration
    def test_space_config_is_valid_json(self, config_dir):
        """
        Test that the space config file is valid JSON.
        """
        space_file = config_dir / 'letta_space_config.json'
        
        if not space_file.exists():
            pytest.skip("No space config file found")
        
        try:
            with open(space_file) as f:
                json.load(f)
        except json.JSONDecodeError as e:
            pytest.fail(
                f"\n❌ Space config file is corrupted!\n"
                f"  File: {space_file}\n"
                f"  Error: {e}\n\n"
                f"This will cause space recreation on next sync.\n"
                f"Restore from backup or recreate space."
            )


class TestRoomCleanupProcedure:
    """Tests to run AFTER any room cleanup/deduplication"""
    
    @pytest.mark.integration
    def test_post_cleanup_verification(self):
        """
        Meta-test that documents the verification procedure after room cleanup.
        
        After running any room cleanup script, you MUST:
        1. Run test_all_agent_mappings_point_to_existing_rooms
        2. Run test_space_config_points_to_existing_space
        3. Restart matrix-client
        4. Check logs for M_FORBIDDEN errors
        5. Test messaging at least one agent
        
        This test serves as documentation and always passes.
        """
        # This test documents the procedure - always passes
        pass
    
    @pytest.mark.integration
    def test_cleanup_script_creates_backup(self):
        """
        Test that room cleanup operations create backups.
        
        Any cleanup script MUST create timestamped backups of:
        - agent_user_mappings.json
        - letta_space_config.json
        
        This test documents the requirement - always passes.
        """
        # This test documents the requirement - always passes
        pass
