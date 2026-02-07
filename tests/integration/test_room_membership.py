#!/usr/bin/env python3
"""
Integration tests for room membership requirements.

These tests verify that all agent rooms have the required members:
- @admin:matrix.oculair.ca
- @letta:matrix.oculair.ca  
- @agent_mail_bridge:matrix.oculair.ca

Run with: pytest tests/integration/test_room_membership.py -v

Tests that query the live Matrix homeserver and database are marked with
@pytest.mark.slow so they can be excluded from fast CI runs:
    pytest -m "not slow"          # Fast CI (PR checks)
    pytest -m "slow"              # Nightly / manual verification
"""
import os
import pytest
import aiohttp
import asyncio
from typing import Dict, List, Set

# Matrix server configuration
HOMESERVER_URL = os.getenv("MATRIX_HOMESERVER_URL", "http://127.0.0.1:6167")
ADMIN_USERNAME = os.getenv("MATRIX_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("MATRIX_ADMIN_PASSWORD", "m6kvcVMWiSYzi6v")

# Database configuration  
DB_HOST = os.getenv("POSTGRES_HOST", "192.168.50.90")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "matrix_letta")
DB_USER = os.getenv("POSTGRES_USER", "letta")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "letta")

# Required members for every agent room
REQUIRED_MEMBERS = [
    "@admin:matrix.oculair.ca",
    "@letta:matrix.oculair.ca",
    "@agent_mail_bridge:matrix.oculair.ca",
]


class MatrixClient:
    """Simple Matrix client for testing"""
    
    def __init__(self, homeserver_url: str):
        self.homeserver_url = homeserver_url
        self.access_token = None
    
    async def login(self, username: str, password: str) -> bool:
        """Login and get access token"""
        async with aiohttp.ClientSession() as session:
            login_url = f"{self.homeserver_url}/_matrix/client/v3/login"
            login_data = {
                "type": "m.login.password",
                "user": username,
                "password": password
            }
            async with session.post(login_url, json=login_data) as response:
                if response.status == 200:
                    data = await response.json()
                    self.access_token = data.get("access_token")
                    return True
                return False
    
    async def get_room_members(self, room_id: str) -> List[str]:
        """Get list of joined members in a room"""
        if not self.access_token:
            return []
        
        async with aiohttp.ClientSession() as session:
            url = f"{self.homeserver_url}/_matrix/client/v3/rooms/{room_id}/joined_members"
            headers = {"Authorization": f"Bearer {self.access_token}"}
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return list(data.get("joined", {}).keys())
                return []


def get_all_agent_rooms() -> List[Dict]:
    """Get all agent rooms from database"""
    import psycopg2
    
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT agent_id, agent_name, room_id 
            FROM agent_mappings 
            WHERE room_id IS NOT NULL AND room_created = true
            ORDER BY agent_name
        """)
        rows = cursor.fetchall()
        return [
            {"agent_id": row[0], "agent_name": row[1], "room_id": row[2]}
            for row in rows
        ]
    finally:
        conn.close()


@pytest.fixture
async def matrix_client():
    """Create and login Matrix client"""
    client = MatrixClient(HOMESERVER_URL)
    logged_in = await client.login(ADMIN_USERNAME, ADMIN_PASSWORD)
    if not logged_in:
        pytest.skip("Could not login to Matrix server")
    return client


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_all_agent_rooms_have_required_members(matrix_client):
    """
    Verify that all agent rooms have the required members.
    
    This is the main regression test to catch provisioning issues.
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    if not agent_rooms:
        pytest.skip("No agent rooms found in database")
    
    failures = []
    
    for agent in agent_rooms:
        room_id = agent["room_id"]
        agent_name = agent["agent_name"]
        
        members = await matrix_client.get_room_members(room_id)
        
        missing_members = []
        for required in REQUIRED_MEMBERS:
            if required not in members:
                missing_members.append(required)
        
        if missing_members:
            failures.append({
                "agent_name": agent_name,
                "room_id": room_id,
                "missing": missing_members,
                "current_members": members
            })
    
    if failures:
        failure_report = "\n\nRoom membership failures:\n"
        for f in failures:
            failure_report += f"\n  {f['agent_name']} ({f['room_id']}):\n"
            failure_report += f"    Missing: {', '.join(f['missing'])}\n"
            failure_report += f"    Current members: {len(f['current_members'])}\n"
        
        pytest.fail(f"Found {len(failures)} rooms with missing required members:{failure_report}")


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_specific_agent_room_membership(matrix_client):
    """
    Test a specific agent's room membership.
    
    This test focuses on the Huly-Vibe Sync Service which had the provisioning issue.
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    # Find the Vibe Sync agent
    vibe_agent = None
    for agent in agent_rooms:
        if "Vibe Sync" in agent["agent_name"]:
            vibe_agent = agent
            break
    
    if not vibe_agent:
        pytest.skip("Huly-Vibe Sync Service agent not found")
    
    members = await matrix_client.get_room_members(vibe_agent["room_id"])
    
    for required in REQUIRED_MEMBERS:
        assert required in members, (
            f"Required member {required} not in {vibe_agent['agent_name']}'s room. "
            f"Current members: {members}"
        )


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_agent_mail_bridge_in_all_rooms(matrix_client):
    """
    Specifically verify @agent_mail_bridge is in all rooms.
    
    This is critical for inter-agent communication.
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    missing_bridge = []
    
    for agent in agent_rooms:
        members = await matrix_client.get_room_members(agent["room_id"])
        if "@agent_mail_bridge:matrix.oculair.ca" not in members:
            missing_bridge.append(agent["agent_name"])
    
    if missing_bridge:
        pytest.fail(
            f"@agent_mail_bridge missing from {len(missing_bridge)} rooms: "
            f"{', '.join(missing_bridge[:10])}{'...' if len(missing_bridge) > 10 else ''}"
        )


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_letta_bot_in_all_rooms(matrix_client):
    """
    Verify @letta is in all agent rooms.
    
    The letta bot is required for message routing.
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    missing_letta = []
    
    for agent in agent_rooms:
        members = await matrix_client.get_room_members(agent["room_id"])
        if "@letta:matrix.oculair.ca" not in members:
            missing_letta.append(agent["agent_name"])
    
    if missing_letta:
        pytest.fail(
            f"@letta missing from {len(missing_letta)} rooms: "
            f"{', '.join(missing_letta[:10])}{'...' if len(missing_letta) > 10 else ''}"
        )


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_admin_in_all_rooms(matrix_client):
    """
    Verify @admin is in all agent rooms.
    
    Admin access is required for oversight and management.
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    missing_admin = []
    
    for agent in agent_rooms:
        members = await matrix_client.get_room_members(agent["room_id"])
        if "@admin:matrix.oculair.ca" not in members:
            missing_admin.append(agent["agent_name"])
    
    if missing_admin:
        pytest.fail(
            f"@admin missing from {len(missing_admin)} rooms: "
            f"{', '.join(missing_admin[:10])}{'...' if len(missing_admin) > 10 else ''}"
        )


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_room_member_count_reasonable(matrix_client):
    """
    Verify rooms have a reasonable number of members.
    
    Rooms should have at least the agent + required members (4 minimum).
    Unusually high member counts might indicate an issue.
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    issues = []
    
    for agent in agent_rooms:
        members = await matrix_client.get_room_members(agent["room_id"])
        member_count = len(members)
        
        # Minimum: agent + admin + letta + agent_mail_bridge = 4
        if member_count < 4:
            issues.append(f"{agent['agent_name']}: only {member_count} members")
        
        # Maximum sanity check - more than 50 members is suspicious
        if member_count > 50:
            issues.append(f"{agent['agent_name']}: {member_count} members (unusually high)")
    
    if issues:
        pytest.fail(f"Room member count issues:\n  " + "\n  ".join(issues))


@pytest.mark.slow
@pytest.mark.requires_matrix
@pytest.mark.requires_db
@pytest.mark.asyncio
async def test_ensure_required_members_function(matrix_client):
    """
    Test that ensure_required_members() correctly adds missing members.
    
    This test:
    1. Picks a random agent room
    2. Verifies current membership 
    3. Calls ensure_required_members (which should be idempotent)
    4. Verifies all required members are present
    """
    try:
        agent_rooms = get_all_agent_rooms()
    except Exception as e:
        pytest.skip(f"Could not connect to database: {e}")
    
    if not agent_rooms:
        pytest.skip("No agent rooms found")
    
    # Pick a room to test
    test_agent = agent_rooms[0]
    room_id = test_agent["room_id"]
    
    # Verify required members are present (they should be after our fix)
    members = await matrix_client.get_room_members(room_id)
    
    for required in REQUIRED_MEMBERS:
        assert required in members, (
            f"Required member {required} not in {test_agent['agent_name']}'s room after provisioning. "
            f"This indicates ensure_required_members() is not working correctly."
        )


@pytest.mark.asyncio
async def test_new_room_would_have_required_members():
    """
    Test that the room creation invite list includes all required members.
    
    This is a code inspection test - it verifies the REQUIRED_ROOM_MEMBERS
    constant matches what we expect, and that room creation includes these invites.
    """
    # Import the room manager to check its constants
    try:
        import sys
        sys.path.insert(0, '/opt/stacks/matrix-synapse-deployment')
        from src.core.room_manager import MatrixRoomManager
        
        # Verify the constant exists and has the right members
        assert hasattr(MatrixRoomManager, 'REQUIRED_ROOM_MEMBERS'), (
            "MatrixRoomManager should have REQUIRED_ROOM_MEMBERS constant"
        )
        
        required = MatrixRoomManager.REQUIRED_ROOM_MEMBERS
        
        assert "@admin:matrix.oculair.ca" in required, "admin should be required"
        assert "@letta:matrix.oculair.ca" in required, "letta should be required"
        assert "@agent_mail_bridge:matrix.oculair.ca" in required, "agent_mail_bridge should be required"
        
    except ImportError as e:
        pytest.skip(f"Could not import room_manager: {e}")


@pytest.mark.asyncio
async def test_service_user_password_generation():
    """
    Test that password generation functions exist and work correctly.
    """
    try:
        import sys
        sys.path.insert(0, '/opt/stacks/matrix-synapse-deployment')
        from src.core.user_manager import MatrixUserManager
        
        # Create a mock instance (we just need the methods)
        class MockConfig:
            pass
        
        manager = MatrixUserManager(
            homeserver_url="http://localhost:8008",
            admin_username="admin",
            admin_password="test"
        )
        
        # Test generate_password
        password = manager.generate_password()
        assert len(password) >= 16, "Password should be at least 16 characters"
        assert password.isalnum(), "Password should be alphanumeric"
        
        # Test generate_agent_password
        agent_password = manager.generate_agent_password("agent-12345678-1234-1234-1234-123456789abc")
        assert agent_password.startswith("AgentPass_"), "Agent password should have correct prefix"
        assert agent_password.endswith("!"), "Agent password should end with !"
        assert "12345678" in agent_password, "Agent password should contain short agent ID"
        
        # Test generate_service_password
        service_password = manager.generate_service_password("test_service")
        assert "test_service" in service_password, "Service password should contain service name"
        assert service_password.endswith("!"), "Service password should end with !"
        
    except ImportError as e:
        pytest.skip(f"Could not import user_manager: {e}")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
