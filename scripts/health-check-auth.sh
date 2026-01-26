#!/bin/bash
# Health check script for Matrix authentication
# Tests critical user logins to detect RocksDB corruption after OOM
#
# Usage: ./health-check-auth.sh [--alert]
# Exit codes: 0 = healthy, 1 = auth failure detected

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load env if available
if [[ -f "$PROJECT_DIR/.env" ]]; then
    source "$PROJECT_DIR/.env"
fi

# Configuration
HOMESERVER_URL="${MATRIX_HEALTHCHECK_URL:-http://127.0.0.1:6167}"
ALERT_MODE="${1:-}"

# Critical users to check (username:password)
declare -A USERS=(
    ["admin"]="${MATRIX_ADMIN_PASSWORD:-}"
    ["letta"]="${MATRIX_PASSWORD:-letta}"
    ["oc_letta_v2"]="oc_letta_v2"
    ["oc_matrix_synapse_deployment_v2"]="oc_matrix_synapse_deployment_v2"
)

FAILED_USERS=()
PASSED_USERS=()

echo "=== Matrix Auth Health Check ==="
echo "Homeserver: $HOMESERVER_URL"
echo "Timestamp: $(date -Iseconds)"
echo ""

# Check if homeserver is reachable
if ! curl -s --connect-timeout 5 "$HOMESERVER_URL/_matrix/client/versions" > /dev/null 2>&1; then
    echo "ERROR: Homeserver not reachable at $HOMESERVER_URL"
    exit 1
fi

# Test each user login
for user in "${!USERS[@]}"; do
    password="${USERS[$user]}"
    
    if [[ -z "$password" ]]; then
        echo "SKIP: $user (no password configured)"
        continue
    fi
    
    response=$(curl -s -X POST "$HOMESERVER_URL/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        -d "{\"type\": \"m.login.password\", \"user\": \"$user\", \"password\": \"$password\"}" \
        2>/dev/null || echo '{"errcode": "NETWORK_ERROR"}')
    
    if echo "$response" | grep -q '"access_token"'; then
        echo "OK: $user"
        PASSED_USERS+=("$user")
    else
        errcode=$(echo "$response" | grep -o '"errcode":"[^"]*"' | cut -d'"' -f4)
        echo "FAIL: $user ($errcode)"
        FAILED_USERS+=("$user")
    fi
done

echo ""

# Summary
if [[ ${#FAILED_USERS[@]} -eq 0 ]]; then
    echo "=== All auth checks passed ==="
    exit 0
else
    echo "=== AUTH FAILURE DETECTED ==="
    echo "Failed users: ${FAILED_USERS[*]}"
    echo ""
    echo "This may indicate RocksDB corruption from OOM."
    echo "Recovery: See AGENTS.md 'OOM Recovery Runbook' section"
    echo ""
    echo "Quick fix:"
    echo "  cd $PROJECT_DIR"
    echo "  docker compose stop tuwunel"
    for user in "${FAILED_USERS[@]}"; do
        password="${USERS[$user]}"
        echo "  # Reset $user:"
        echo "  docker run --rm --entrypoint '' \\"
        echo "    -e TUWUNEL_SERVER_NAME=matrix.oculair.ca \\"
        echo "    -v ./tuwunel-data:/var/lib/tuwunel \\"
        echo "    ghcr.io/oculairmedia/tuwunel-docker2010:latest \\"
        echo "    /usr/local/bin/tuwunel -c /var/lib/tuwunel/tuwunel.toml \\"
        echo "    -O 'server_name=\"matrix.oculair.ca\"' \\"
        echo "    --execute 'users reset-password $user $password'"
    done
    echo "  docker compose up -d"
    
    # Alert if requested
    if [[ "$ALERT_MODE" == "--alert" ]]; then
        echo ""
        echo "Sending alert..."
        # Could integrate with alerting system here (e.g., webhook, email)
    fi
    
    exit 1
fi
