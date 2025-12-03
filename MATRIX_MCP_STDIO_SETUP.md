# Matrix Messaging MCP - STDIO Transport Setup

## Overview
The Matrix Messaging MCP server now supports both HTTP and STDIO transports:

- **HTTP Mode** (port 3100): Runs in Docker container for other clients
- **STDIO Mode**: Runs locally via OpenCode's MCP local server integration

## Configuration

### Server Modes

The server automatically detects which transport to use based on the `MCP_TRANSPORT` environment variable:

```bash
# STDIO mode (for OpenCode)
MCP_TRANSPORT=stdio node dist/index.js

# HTTP mode (default, for Docker)
node dist/index.js
```

### OpenCode Configuration

Add to your `opencode.json` or `opencode.jsonc`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "matrix-messaging": {
      "type": "local",
      "command": ["/opt/stacks/matrix-messaging-mcp/mcp-stdio.sh"],
      "enabled": true,
      "environment": {
        "MATRIX_HOMESERVER_URL": "http://127.0.0.1:6167",
        "DATA_DIR": "/opt/stacks/matrix-messaging-mcp/data",
        "LETTA_API_URL": "http://192.168.50.90:8283",
        "LETTA_API_KEY": ""
      },
      "timeout": 10000
    }
  }
}
```

## Usage in OpenCode

After restarting OpenCode, use the `matrix_messaging` tool:

```
Send a message to agent Meridian using matrix_messaging
```

The tool will be invoked via STDIO, avoiding the HTTP streaming race condition issues.

## Files

- `/opt/stacks/matrix-messaging-mcp/mcp-stdio.sh`: STDIO wrapper script
- `/opt/stacks/matrix-messaging-mcp/src/index.ts`: Updated to support both transports
- `/opt/stacks/matrix-synapse-deployment/opencode.json`: OpenCode MCP configuration

## Testing

Test STDIO mode locally:
```bash
cd /opt/stacks/matrix-messaging-mcp
MCP_TRANSPORT=stdio node dist/index.js
```

## Architecture

```
OpenCode → STDIO → matrix-messaging MCP (local process)
                    ↓
                  Matrix Homeserver (tuwunel)
                    ↓
                  Agent Rooms
```

The HTTP version continues to run in Docker for other clients that need it.
