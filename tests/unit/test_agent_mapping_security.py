"""
Unit tests for AgentMapping security methods.

These tests verify that the to_public_dict() method correctly strips sensitive
credentials (matrix_password) from API responses while preserving all other fields.
"""
import pytest
from datetime import datetime
from src.models.agent_mapping import AgentMapping, InvitationStatus


class TestAgentMappingToDict:
    """Tests for AgentMapping.to_dict() baseline behavior"""

    def test_to_dict_includes_password(self):
        """Test that to_dict() includes matrix_password (baseline)"""
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True
        )
        mapping.invitations = []

        result = mapping.to_dict()

        assert "matrix_password" in result
        assert result["matrix_password"] == "secret_password_123"

    def test_to_dict_includes_all_fields(self):
        """Test that to_dict() includes all expected fields"""
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True
        )
        mapping.invitations = []

        result = mapping.to_dict()

        assert result["agent_id"] == "test-agent-001"
        assert result["agent_name"] == "TestAgent"
        assert result["matrix_user_id"] == "@test:matrix.test"
        assert result["room_id"] == "!testroom:matrix.test"
        assert result["room_created"] is True
        assert result["created"] is True  # Backward compatibility field


class TestAgentMappingToPublicDict:
    """Tests for AgentMapping.to_public_dict() security method"""

    def test_to_public_dict_excludes_password(self):
        """Test that to_public_dict() does NOT include matrix_password"""
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True
        )
        mapping.invitations = []

        result = mapping.to_public_dict()

        assert "matrix_password" not in result

    def test_to_public_dict_includes_all_other_fields(self):
        """Test that to_public_dict() preserves all non-sensitive fields"""
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True
        )
        mapping.invitations = []

        result = mapping.to_public_dict()

        # Verify all non-sensitive fields are present
        assert result["agent_id"] == "test-agent-001"
        assert result["agent_name"] == "TestAgent"
        assert result["matrix_user_id"] == "@test:matrix.test"
        assert result["room_id"] == "!testroom:matrix.test"
        assert result["room_created"] is True
        assert result["created"] is True  # Backward compatibility field
        assert "invitation_status" in result

    def test_to_public_dict_does_not_mutate_original(self):
        """Test that calling to_public_dict() does not modify the original object"""
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True
        )
        mapping.invitations = []

        # Call to_public_dict()
        public_result = mapping.to_public_dict()

        # Verify password is not in public result
        assert "matrix_password" not in public_result

        # Verify subsequent to_dict() call still includes password
        # (original object is not mutated)
        private_result = mapping.to_dict()
        assert "matrix_password" in private_result
        assert private_result["matrix_password"] == "secret_password_123"

    def test_to_public_dict_with_removed_at(self):
        """Test that removed_at field appears in both to_dict() and to_public_dict()"""
        removed_time = datetime(2025, 1, 15, 10, 30, 0)
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True,
            removed_at=removed_time
        )
        mapping.invitations = []

        # Check to_dict() includes removed_at
        private_result = mapping.to_dict()
        assert "removed_at" in private_result
        assert private_result["removed_at"] == removed_time.isoformat()

        # Check to_public_dict() also includes removed_at
        public_result = mapping.to_public_dict()
        assert "removed_at" in public_result
        assert public_result["removed_at"] == removed_time.isoformat()

        # But password should still be excluded
        assert "matrix_password" not in public_result

    def test_to_public_dict_with_invitation_status(self):
        """Test that invitation_status is preserved in to_public_dict()"""
        mapping = AgentMapping(
            agent_id="test-agent-001",
            agent_name="TestAgent",
            matrix_user_id="@test:matrix.test",
            matrix_password="secret_password_123",
            room_id="!testroom:matrix.test",
            room_created=True
        )

        # Create invitation status records
        inv1 = InvitationStatus(
            agent_id="test-agent-001",
            invitee="@admin:matrix.test",
            status="joined"
        )
        inv2 = InvitationStatus(
            agent_id="test-agent-001",
            invitee="@user:matrix.test",
            status="pending"
        )
        mapping.invitations = [inv1, inv2]

        result = mapping.to_public_dict()

        # Verify invitation_status is present and correct
        assert "invitation_status" in result
        assert result["invitation_status"]["@admin:matrix.test"] == "joined"
        assert result["invitation_status"]["@user:matrix.test"] == "pending"

        # But password should still be excluded
        assert "matrix_password" not in result
