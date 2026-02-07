#!/bin/bash
# List Matrix agent accounts with recent login failures.
#
# Usage:
#   ./scripts/admin/list-failing-agents.sh [--since 24h] [--container <name>]

set -euo pipefail

SINCE="24h"
CONTAINER="matrix-synapse-deployment-matrix-client-1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --since)
            SINCE="${2:-24h}"
            shift 2
            ;;
        --container)
            CONTAINER="${2:-$CONTAINER}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: $0 [--since 24h] [--container <name>]" >&2
            exit 2
            ;;
    esac
done

echo "=== Agent Login Failure Scan ==="
echo "Container: $CONTAINER"
echo "Window: --since $SINCE"
echo ""

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "ERROR: Container '$CONTAINER' is not running"
    exit 1
fi

FAIL_LINES=$(docker logs --since "$SINCE" "$CONTAINER" 2>&1 | rg "Failed to login as agent" || true)

if [[ -z "${FAIL_LINES:-}" ]]; then
    echo "No agent login failures found in the selected window."
    exit 0
fi

echo "Failing agents (count localpart):"
echo "$FAIL_LINES" \
    | sed -E 's/.*Failed to login as agent ([^" ]+).*/\1/' \
    | sort \
    | uniq -c \
    | sort -nr

echo ""
echo "Raw failure lines:"
echo "$FAIL_LINES"
