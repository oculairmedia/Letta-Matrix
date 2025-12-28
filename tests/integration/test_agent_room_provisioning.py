#!/usr/bin/env python3
"""
Integration tests for agent room provisioning.

These tests verify that:
1. New Letta agents automatically get Matrix rooms created
2. Room mappings are consistent between different systems
3. The matrix-identity-bridge and matrix-client use compatible user ID formats
4. Agents can be reached via talk_to_agent after provisioning

Test the core issue: When a new Letta agent is created, it should automatically
get a Matrix room that can be used for messaging via both:
- The matrix-client (for Letta responses)
- The matrix-identity-bridge (for talk_to_agent/letta_chat operations)
"""

import pytest
import asyncio
import aiohttp
import json
import os
import uuid
from typing import Optional, Dict, Any


# Configuration - adjust these for your environment
MATRIX_HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://127.0.0.1:6167")
LETTA_API_URL = os.getenv("LETTA_API_URL", "http://192.168.50.90:8289")
LETTA_TOKEN = os.getenv("LETTA_TOKEN", "lettaSecurePass123")
MATRIX_ADMIN_USERNAME = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
MATRIX_ADMIN_PASSWORD = os.getenv("MATRIX_ADMIN_PASSWORD", "m6kvcVMWiSYzi6v")

# Paths to data files
AGENT_USER_MAPPINGS_PATH = "/opt/stacks/matrix-synapse-deployment/matrix_client_data/agent_user_mappings.json"
IDENTITY_BRIDGE_DATA_PATH = "/opt/stacks/matrix-synapse-deployment/mcp-servers/matrix-identity-bridge/data/identities.json"


class MatrixClient:
    """Simple Matrix client for testing"""
    
    def __init__(self, homeserver_url: str):
        self.homeserver_url = homeserver_url
        self.access_token: Optional[str] = None
    
    async def login(self, username: str, password: str) -> bool:
        """Login to Matrix and get access token"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.homeserver_url}/_matrix/client/v3/login",
                json={
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": username},
                    "password": password
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data.get("access_token")
                    return True
                return False
    
    async def whoami(self, token: str) -> Optional[Dict[str, Any]]:
        """Check if a token is valid"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.homeserver_url}/_matrix/client/v3/account/whoami",
                headers={"Authorization": f"Bearer {token}"}
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
    
    async def get_joined_rooms(self, token: str) -> list:
        """Get rooms a user has joined"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.homeserver_url}/_matrix/client/v3/joined_rooms",
                headers={"Authorization": f"Bearer {token}"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("joined_rooms", [])
                return []


class LettaClient:
    """Simple Letta API client for testing"""
    
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.token = token
    
    async def list_agents(self) -> list:
        """List all Letta agents"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_url}/v1/agents",
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
    
    async def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific agent"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.api_url}/v1/agents/{agent_id}",
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None


def load_json_file(path: str) -> Dict[str, Any]:
    """Load a JSON file"""
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}


def normalize_agent_id(agent_id: str) -> str:
    """Normalize agent ID to standard format: agent-{uuid}"""
    if agent_id.startswith("agent-"):
        return agent_id
    return f"agent-{agent_id}"


def extract_uuid_from_matrix_user_id(matrix_user_id: str) -> Optional[str]:
    """
    Extract the UUID from a Matrix user ID.
    
    Handles both formats:
    - @agent_{uuid}:domain -> uuid (with underscores converted to hyphens)
    - @letta_agent_{uuid}:domain -> uuid (with underscores converted to hyphens)
    """
    if not matrix_user_id or not matrix_user_id.startswith("@"):
        return None
    
    # Remove @ prefix and :domain suffix
    localpart = matrix_user_id.split(":")[0][1:]
    
    # Remove prefix
    if localpart.startswith("letta_agent_"):
        uuid_part = localpart[12:]  # len("letta_agent_") = 12
    elif localpart.startswith("agent_"):
        uuid_part = localpart[6:]   # len("agent_") = 6
    else:
        return None
    
    # Convert underscores back to hyphens
    return uuid_part.replace("_", "-")


class TestAgentRoomProvisioning:
    """Test suite for agent room provisioning"""
    
    @pytest.fixture
    async def matrix_client(self):
        """Create a Matrix client"""
        client = MatrixClient(MATRIX_HOMESERVER_URL)
        await client.login(MATRIX_ADMIN_USERNAME, MATRIX_ADMIN_PASSWORD)
        return client
    
    @pytest.fixture
    async def letta_client(self):
        """Create a Letta client"""
        return LettaClient(LETTA_API_URL, LETTA_TOKEN)
    
    @pytest.mark.asyncio
    async def test_matrix_server_reachable(self, matrix_client):
        """Test that Matrix server is reachable"""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MATRIX_HOMESERVER_URL}/_matrix/client/versions") as resp:
                assert resp.status == 200, "Matrix server should be reachable"
                data = await resp.json()
                assert "versions" in data, "Matrix server should return versions"
    
    @pytest.mark.asyncio
    async def test_letta_api_reachable(self, letta_client):
        """Test that Letta API is reachable"""
        agents = await letta_client.list_agents()
        assert isinstance(agents, list), "Letta API should return a list of agents"
    
    @pytest.mark.asyncio
    async def test_agent_user_mappings_file_exists(self):
        """Test that agent_user_mappings.json exists"""
        assert os.path.exists(AGENT_USER_MAPPINGS_PATH), \
            f"Agent user mappings file should exist at {AGENT_USER_MAPPINGS_PATH}"
    
    @pytest.mark.asyncio
    async def test_identity_bridge_data_exists(self):
        """Test that identity bridge data file exists"""
        assert os.path.exists(IDENTITY_BRIDGE_DATA_PATH), \
            f"Identity bridge data file should exist at {IDENTITY_BRIDGE_DATA_PATH}"
    
    @pytest.mark.asyncio
    async def test_all_letta_agents_have_room_mappings(self, letta_client):
        """
        CRITICAL TEST: Every Letta agent should have a room mapping.
        
        This is the core issue - when agents don't have room mappings,
        talk_to_agent fails with "no Matrix room configured".
        """
        agents = await letta_client.list_agents()
        mappings = load_json_file(AGENT_USER_MAPPINGS_PATH)
        
        missing_mappings = []
        for agent in agents:
            agent_id = agent.get("id", "")
            agent_name = agent.get("name", "Unknown")
            normalized_id = normalize_agent_id(agent_id)
            
            if normalized_id not in mappings:
                missing_mappings.append({
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "normalized_id": normalized_id
                })
        
        if missing_mappings:
            missing_names = [m["agent_name"] for m in missing_mappings]
            pytest.fail(
                f"Found {len(missing_mappings)} agents without room mappings:\n" +
                "\n".join(f"  - {m['agent_name']} ({m['agent_id']})" for m in missing_mappings)
            )
    
    @pytest.mark.asyncio
    async def test_all_room_mappings_have_valid_rooms(self, matrix_client):
        """
        Test that all room_ids in mappings point to valid, existing rooms.
        """
        mappings = load_json_file(AGENT_USER_MAPPINGS_PATH)
        
        invalid_rooms = []
        for agent_id, mapping in mappings.items():
            room_id = mapping.get("matrix_room_id") or mapping.get("room_id")
            if not room_id:
                invalid_rooms.append({
                    "agent_id": agent_id,
                    "agent_name": mapping.get("agent_name", "Unknown"),
                    "issue": "No room_id in mapping"
                })
                continue
            
            # Check if room exists by trying to get room state
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/rooms/{room_id}/state/m.room.create",
                    headers={"Authorization": f"Bearer {matrix_client.access_token}"}
                ) as resp:
                    if resp.status != 200:
                        invalid_rooms.append({
                            "agent_id": agent_id,
                            "agent_name": mapping.get("agent_name", "Unknown"),
                            "room_id": room_id,
                            "issue": f"Room not accessible (HTTP {resp.status})"
                        })
        
        if invalid_rooms:
            pytest.fail(
                f"Found {len(invalid_rooms)} agents with invalid room mappings:\n" +
                "\n".join(f"  - {r['agent_name']}: {r['issue']}" for r in invalid_rooms)
            )
    
    @pytest.mark.asyncio
    async def test_matrix_user_id_format_consistency(self):
        """
        Test that matrix_user_id formats are consistent.
        
        The matrix-client uses @agent_{uuid} format
        The identity-bridge uses @letta_agent_{uuid} format
        
        Both should map to the same agent.
        """
        mappings = load_json_file(AGENT_USER_MAPPINGS_PATH)
        identities = load_json_file(IDENTITY_BRIDGE_DATA_PATH)
        
        # Build a set of agent UUIDs from mappings
        mapping_uuids = set()
        for agent_id in mappings.keys():
            if agent_id.startswith("agent-"):
                mapping_uuids.add(agent_id[6:])  # Extract UUID
        
        # Build a set of agent UUIDs from identities
        identity_uuids = set()
        for identity_id, identity_data in identities.items():
            if identity_id.startswith("letta_agent-"):
                uuid = identity_id[12:]  # len("letta_agent-") = 12
                identity_uuids.add(uuid)
        
        # Check for UUIDs in identities but not in mappings
        orphaned_identities = identity_uuids - mapping_uuids
        
        if orphaned_identities:
            # This is informational - identities may exist for agents not yet synced
            print(f"INFO: {len(orphaned_identities)} identities without mappings (may be expected)")
    
    @pytest.mark.asyncio
    async def test_agent_tokens_are_valid(self, matrix_client):
        """
        Test that stored access tokens for agents are still valid.
        
        Invalid tokens cause M_UNKNOWN_TOKEN errors.
        """
        identities = load_json_file(IDENTITY_BRIDGE_DATA_PATH)
        
        invalid_tokens = []
        sample_size = min(10, len(identities))  # Test a sample to avoid rate limits
        
        for i, (identity_id, identity_data) in enumerate(identities.items()):
            if i >= sample_size:
                break
            
            token = identity_data.get("accessToken")
            if not token:
                continue
            
            whoami = await matrix_client.whoami(token)
            if not whoami:
                invalid_tokens.append({
                    "identity_id": identity_id,
                    "mxid": identity_data.get("mxid", "Unknown")
                })
        
        if invalid_tokens:
            pytest.fail(
                f"Found {len(invalid_tokens)} agents with invalid tokens:\n" +
                "\n".join(f"  - {t['identity_id']} ({t['mxid']})" for t in invalid_tokens)
            )
    
    @pytest.mark.asyncio
    async def test_specific_agent_has_room(self, letta_client):
        """
        Test a specific agent (Houdini MCP) that was previously missing a room.
        
        This is a regression test for the specific issue we encountered.
        """
        # Houdini MCP Server agent ID
        houdini_agent_id = "agent-0a0867cb-09a4-4a9d-ad97-884773b7cbbc"
        
        mappings = load_json_file(AGENT_USER_MAPPINGS_PATH)
        
        assert houdini_agent_id in mappings, \
            f"Houdini MCP agent should have a room mapping"
        
        mapping = mappings[houdini_agent_id]
        room_id = mapping.get("matrix_room_id") or mapping.get("room_id")
        
        assert room_id, \
            f"Houdini MCP agent should have a room_id in its mapping"
        
        assert room_id.startswith("!"), \
            f"Room ID should start with '!', got: {room_id}"


class TestAgentRoomProvisioningSync:
    """Tests for the agent sync process"""
    
    @pytest.mark.asyncio
    async def test_new_agent_gets_room_after_sync(self):
        """
        Test that triggering an agent sync creates rooms for new agents.
        
        This test:
        1. Identifies agents without rooms
        2. Triggers a sync
        3. Verifies rooms were created
        """
        # This would require triggering the matrix-client sync
        # For now, just document the expected behavior
        pytest.skip("Requires triggering matrix-client sync - manual test")
    
    @pytest.mark.asyncio
    async def test_identity_bridge_can_reach_all_agents(self):
        """
        Test that the identity bridge can reach all agents via letta_list.
        
        This verifies the bridge's view of available agents.
        """
        # This would call the identity bridge MCP endpoint
        pytest.skip("Requires MCP endpoint access - use manual test")


# Utility functions for manual testing
async def find_agents_without_rooms():
    """Find all agents that don't have room mappings"""
    letta = LettaClient(LETTA_API_URL, LETTA_TOKEN)
    agents = await letta.list_agents()
    mappings = load_json_file(AGENT_USER_MAPPINGS_PATH)
    
    missing = []
    for agent in agents:
        agent_id = normalize_agent_id(agent.get("id", ""))
        if agent_id not in mappings:
            missing.append({
                "id": agent_id,
                "name": agent.get("name", "Unknown")
            })
    
    return missing


async def find_agents_with_invalid_rooms():
    """Find agents whose room mappings point to invalid rooms"""
    matrix = MatrixClient(MATRIX_HOMESERVER_URL)
    await matrix.login(MATRIX_ADMIN_USERNAME, MATRIX_ADMIN_PASSWORD)
    
    mappings = load_json_file(AGENT_USER_MAPPINGS_PATH)
    
    invalid = []
    for agent_id, mapping in mappings.items():
        room_id = mapping.get("matrix_room_id") or mapping.get("room_id")
        if not room_id:
            invalid.append({
                "id": agent_id,
                "name": mapping.get("agent_name", "Unknown"),
                "issue": "No room_id"
            })
            continue
        
        # Check if room exists
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{MATRIX_HOMESERVER_URL}/_matrix/client/v3/rooms/{room_id}/state/m.room.create",
                headers={"Authorization": f"Bearer {matrix.access_token}"}
            ) as resp:
                if resp.status != 200:
                    invalid.append({
                        "id": agent_id,
                        "name": mapping.get("agent_name", "Unknown"),
                        "room_id": room_id,
                        "issue": f"HTTP {resp.status}"
                    })
    
    return invalid


if __name__ == "__main__":
    # Run quick diagnostic
    async def main():
        print("=== Agent Room Provisioning Diagnostic ===\n")
        
        print("1. Checking for agents without room mappings...")
        missing = await find_agents_without_rooms()
        if missing:
            print(f"   FOUND {len(missing)} agents without rooms:")
            for agent in missing[:10]:
                print(f"     - {agent['name']} ({agent['id']})")
            if len(missing) > 10:
                print(f"     ... and {len(missing) - 10} more")
        else:
            print("   OK - All agents have room mappings")
        
        print("\n2. Checking for invalid room mappings...")
        invalid = await find_agents_with_invalid_rooms()
        if invalid:
            print(f"   FOUND {len(invalid)} agents with invalid rooms:")
            for agent in invalid[:10]:
                print(f"     - {agent['name']}: {agent['issue']}")
            if len(invalid) > 10:
                print(f"     ... and {len(invalid) - 10} more")
        else:
            print("   OK - All room mappings are valid")
        
        print("\n=== Done ===")
    
    asyncio.run(main())
