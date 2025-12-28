#!/bin/bash
# Run E2E tests against live services
#
# These tests verify actual behavior by:
# - Querying Matrix server directly
# - Creating/deleting test agents in Letta
# - Checking database state
#
# Usage:
#   ./scripts/run_e2e_tests.sh              # Run all E2E tests
#   ./scripts/run_e2e_tests.sh --verify     # Just verify existing agents
#   ./scripts/run_e2e_tests.sh --full       # Full provisioning tests (creates test agents)

set -e

# Load environment from .env if exists
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Required: Matrix admin password
if [ -z "$MATRIX_ADMIN_PASSWORD" ]; then
    echo "ERROR: MATRIX_ADMIN_PASSWORD must be set"
    echo ""
    echo "Set it in .env or export it:"
    echo "  export MATRIX_ADMIN_PASSWORD=your_password"
    exit 1
fi

# Optional overrides with defaults
export MATRIX_HOMESERVER_URL="${MATRIX_HOMESERVER_URL:-http://127.0.0.1:6167}"
export MATRIX_ADMIN_USERNAME="${MATRIX_ADMIN_USERNAME:-@admin:matrix.oculair.ca}"
export LETTA_API_URL="${LETTA_API_URL:-http://192.168.50.90:8283}"
export LETTA_TOKEN="${LETTA_TOKEN:-lettaSecurePass123}"
export DATABASE_URL="${DATABASE_URL:-postgresql://letta:letta@192.168.50.90:5432/matrix_letta}"

echo "Running E2E tests with:"
echo "  Matrix: $MATRIX_HOMESERVER_URL"
echo "  Letta: $LETTA_API_URL"
echo "  Admin: $MATRIX_ADMIN_USERNAME"
echo ""

case "${1:-all}" in
    --verify)
        echo "Running verification tests only (no test agent creation)..."
        python3 -m pytest tests/e2e/test_agent_provisioning.py::TestExistingAgentVerification -v
        ;;
    --full)
        echo "Running full E2E tests (will create test agents)..."
        python3 -m pytest tests/e2e/ -v
        ;;
    *)
        echo "Running all E2E tests..."
        python3 -m pytest tests/e2e/ -v
        ;;
esac

echo ""
echo "E2E tests complete!"
