#!/bin/bash
# Matrix Messaging MCP - STDIO wrapper for OpenCode

# Capture the working directory BEFORE cd - this is where OpenCode is running
export OPENCODE_PROJECT_DIR="${OPENCODE_PROJECT_DIR:-$(pwd)}"

cd "$(dirname "$0")"
export MCP_TRANSPORT=stdio
export MATRIX_HOMESERVER_URL="${MATRIX_HOMESERVER_URL:-http://127.0.0.1:6167}"
export DATA_DIR="${DATA_DIR:-./data}"
export LETTA_API_URL="${LETTA_API_URL:-http://127.0.0.1:8283}"
export LETTA_API_KEY="${LETTA_API_KEY}"
node dist/index.js
