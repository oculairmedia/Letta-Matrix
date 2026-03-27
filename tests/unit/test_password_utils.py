import pytest

from src.utils.password import generate_deterministic_identity_password


@pytest.mark.unit
def test_generate_deterministic_identity_password_stable_sha256_value():
    password = generate_deterministic_identity_password("agent_123", "mcp_identity_bridge_2024")
    assert password == "MCP_2497cf3211b1093d66295c4a"


@pytest.mark.unit
def test_generate_deterministic_identity_password_custom_prefix_and_length():
    password = generate_deterministic_identity_password("p2p_bridge", "bridge-secret", prefix="P2P_", length=16)
    assert password.startswith("P2P_")
    assert len(password) == 4 + 16


@pytest.mark.unit
def test_generate_deterministic_identity_password_changes_with_input():
    p1 = generate_deterministic_identity_password("agent_a", "secret_1")
    p2 = generate_deterministic_identity_password("agent_a", "secret_1")
    p3 = generate_deterministic_identity_password("agent_a", "secret_2")
    p4 = generate_deterministic_identity_password("agent_b", "secret_1")

    assert p1 == p2
    assert p1 != p3
    assert p1 != p4
