#!/bin/bash
# Send an alert via ntfy push notification
#
# Usage:
#   ./alert.sh "Auth failure detected for 3 users"
#   ./alert.sh "Streaming idle timeout fired" --priority high
#   ./alert.sh "Health check passed" --priority low --tags check
#
# Env vars:
#   NTFY_URL       - ntfy server URL (default: http://127.0.0.1:2586)
#   NTFY_TOPIC     - topic to publish to (default: from .env)
#   NTFY_PRIORITY   - default priority: default

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load env for NTFY_TOPIC (but override NTFY_URL for host access)
if [[ -f "$PROJECT_DIR/.env" ]]; then
    NTFY_TOPIC=$(grep '^NTFY_TOPIC=' "$PROJECT_DIR/.env" | cut -d= -f2)
fi

NTFY_URL="${NTFY_HOST_URL:-http://127.0.0.1:2586}"
NTFY_TOPIC="${NTFY_TOPIC:-mxsyn-alerts}"
MESSAGE="${1:?Usage: alert.sh \"message\" [--priority high] [--tags tag1,tag2]}"
shift

# Parse optional flags
PRIORITY="default"
TAGS="warning"
TITLE="Matrix Synapse Alert"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --priority) PRIORITY="$2"; shift 2 ;;
        --tags)     TAGS="$2"; shift 2 ;;
        --title)    TITLE="$2"; shift 2 ;;
        *)          shift ;;
    esac
done

curl -s \
    -H "Title: ${TITLE}" \
    -H "Priority: ${PRIORITY}" \
    -H "Tags: ${TAGS}" \
    -d "${MESSAGE}" \
    "${NTFY_URL}/${NTFY_TOPIC}" > /dev/null 2>&1

EXIT_CODE=$?
if [[ $EXIT_CODE -ne 0 ]]; then
    echo "ALERT FAILED: Could not reach ntfy at ${NTFY_URL}" >&2
fi
exit $EXIT_CODE
