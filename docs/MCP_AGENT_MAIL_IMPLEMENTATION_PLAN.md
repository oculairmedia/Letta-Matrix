# MCP Agent Mail Implementation Plan

**Status**: Planning  
**Decision**: Deploy MCP Agent Mail alongside Matrix for file coordination  
**Estimated Effort**: 12-19 hours  
**Risk Level**: Low (additive, not replacing existing system)

---

## Executive Summary

We're adding MCP Agent Mail's file reservation system to prevent edit conflicts between our OpenCode and Letta agents, while keeping Matrix as the primary messaging and coordination layer.

**Problem**: 
- OpenCode agents work directly with filesystem (CLI)
- Letta agents access filesystem via Claude Code integration
- No mechanism to prevent simultaneous edits → merge conflicts

**Solution**:
- Deploy MCP Agent Mail on port 8766 (separate from Matrix)
- Use **only** file reservation features (not messaging)
- Integrate with Matrix for visibility (announce reservations in rooms)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Current System (Matrix Only)                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  OpenCode Agent A                                           │
│    └─ Edits src/api/routes.py                              │
│                                                             │
│  Letta Agent B (via Claude Code)                            │
│    └─ Also edits src/api/routes.py                         │
│                                                             │
│  Result: ❌ Merge conflict!                                 │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Hybrid System (Matrix + MCP Agent Mail)                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  OpenCode Agent A                                           │
│    ├─ Reserves src/api/routes.py (MCP Agent Mail)          │
│    ├─ Announces in Matrix: "🔒 Editing API routes"         │
│    └─ Edits file                                            │
│                                                             │
│  Letta Agent B (via Claude Code)                            │
│    ├─ Checks reservations (MCP Agent Mail)                 │
│    ├─ Sees conflict                                         │
│    └─ Waits or works on different files                    │
│                                                             │
│  Pre-commit Guard                                           │
│    └─ Blocks commits that violate active reservations      │
│                                                             │
│  Result: ✅ No conflicts!                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Deploy MCP Agent Mail (4-6 hours)

**Goal**: Get MCP Agent Mail running alongside Matrix

#### 1.1 Install MCP Agent Mail

```bash
# Navigate to stacks directory
cd /opt/stacks

# Clone repository
git clone https://github.com/Dicklesworthstone/mcp_agent_mail.git
cd mcp_agent_mail

# Install using custom port (avoid Matrix port 3100)
curl -fsSL "https://raw.githubusercontent.com/Dicklesworthstone/mcp_agent_mail/main/scripts/install.sh?$(date +%s)" | bash -s -- --port 8766 --yes

# OR manual installation
uv python install 3.14
uv venv -p 3.14
source .venv/bin/activate
uv sync
```

**Expected output**:
```
✓ MCP Agent Mail installed
✓ Server running on http://127.0.0.1:8766
✓ Bearer token: [saved in .env]
✓ Shell alias 'am' created
```

#### 1.2 Configure Environment

```bash
# Edit .env file
nano /opt/stacks/mcp_agent_mail/.env
```

**Key settings**:
```bash
# Storage
STORAGE_ROOT=/opt/stacks/mcp_agent_mail/data

# Server
HTTP_HOST=127.0.0.1
HTTP_PORT=8766
HTTP_BEARER_TOKEN=<generated_token>

# Features (disable what we don't need)
CONTACT_ENFORCEMENT_ENABLED=false  # We use Matrix for messaging
LLM_ENABLED=false                   # Don't need AI summaries

# File reservations (enable)
FILE_RESERVATIONS_ENFORCEMENT_ENABLED=true
FILE_RESERVATION_INACTIVITY_SECONDS=3600
FILE_RESERVATION_ACTIVITY_GRACE_SECONDS=900
```

#### 1.3 Create Docker Compose Service (Optional)

Add to `/opt/stacks/matrix-synapse-deployment/docker-compose.yml`:

```yaml
services:
  mcp-agent-mail:
    image: ghcr.io/dicklesworthstone/mcp_agent_mail:latest
    container_name: mcp-agent-mail
    restart: unless-stopped
    ports:
      - "8766:8766"
    environment:
      - HTTP_HOST=0.0.0.0
      - HTTP_PORT=8766
      - STORAGE_ROOT=/data/mailbox
      - FILE_RESERVATIONS_ENFORCEMENT_ENABLED=true
      - CONTACT_ENFORCEMENT_ENABLED=false
      - LLM_ENABLED=false
    volumes:
      - mcp_agent_mail_data:/data
    networks:
      - matrix-internal
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8766/health/liveness"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  mcp_agent_mail_data:
```

#### 1.4 Test Basic Functionality

```bash
# Health check
curl http://127.0.0.1:8766/health/liveness

# Expected: {"status": "ok"}

# Create test project
curl -X POST http://127.0.0.1:8766/mcp/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "ensure_project",
      "arguments": {
        "human_key": "/opt/stacks/matrix-synapse-deployment"
      }
    },
    "id": 1
  }'
```

**Success criteria**:
- ✅ Server responds on port 8766
- ✅ Health check passes
- ✅ Project creation succeeds
- ✅ Web UI accessible at `http://127.0.0.1:8766/mail`

---

### Phase 2: Install Pre-commit Guards (2-3 hours)

**Goal**: Prevent commits that violate file reservations

#### 2.1 Install Guard for Matrix Repo

```bash
cd /opt/stacks/matrix-synapse-deployment

# Install pre-commit guard
uv run python -m mcp_agent_mail.cli guard install \
  /opt/stacks/matrix-synapse-deployment \
  /opt/stacks/matrix-synapse-deployment

# Verify installation
ls -la .git/hooks/
# Should see: pre-commit, hooks.d/pre-commit/50-agent-mail.py
```

#### 2.2 Test Guard Behavior

```bash
# Set agent name (required for guard)
export AGENT_NAME="TestAgent"

# Create a test file reservation via API
curl -X POST http://127.0.0.1:8766/mcp/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "file_reservation_paths",
      "arguments": {
        "project_key": "/opt/stacks/matrix-synapse-deployment",
        "agent_name": "AnotherAgent",
        "paths": ["src/api/*.py"],
        "ttl_seconds": 300,
        "exclusive": true,
        "reason": "Testing guard"
      }
    },
    "id": 2
  }'

# Try to commit a change to src/api/
echo "# test" >> src/api/__init__.py
git add src/api/__init__.py
git commit -m "Test commit"

# Expected: Guard should BLOCK this commit
# Output: "FILE_RESERVATION_CONFLICT: AnotherAgent has exclusive reservation..."
```

#### 2.3 Configure Guard Behavior

Edit `.env` or set environment variables:

```bash
# Guard mode: 'block' (default) or 'warn'
export AGENT_MAIL_GUARD_MODE=block

# Your agent identity
export AGENT_NAME=OpenCodeAgent1

# Bypass for emergencies (use sparingly!)
# export AGENT_MAIL_BYPASS=1
```

**Success criteria**:
- ✅ Guard installed in `.git/hooks/`
- ✅ Guard blocks conflicting commits
- ✅ Guard allows non-conflicting commits
- ✅ Guard respects `AGENT_NAME` environment variable

---

### Phase 3: Integrate with Letta Agents (4-6 hours)

**Goal**: Add file reservation tools to Letta agents

#### 3.1 Add Tool Definitions

Create `/opt/stacks/matrix-synapse-deployment/src/letta/tools/file_reservations.py`:

```python
"""File reservation tools for preventing edit conflicts."""

import os
import requests
from typing import List, Optional
from letta.schemas.tool import Tool

MCP_AGENT_MAIL_URL = os.getenv(
    "MCP_AGENT_MAIL_URL", 
    "http://127.0.0.1:8766/mcp/"
)
MCP_AGENT_MAIL_TOKEN = os.getenv("MCP_AGENT_MAIL_TOKEN")


def reserve_files(
    paths: List[str],
    agent_name: str,
    ttl_seconds: int = 3600,
    exclusive: bool = True,
    reason: Optional[str] = None
) -> dict:
    """
    Reserve files before editing to prevent conflicts with other agents.
    
    Args:
        paths: List of file patterns to reserve (e.g., ["src/api/*.py"])
        agent_name: Your agent identity name
        ttl_seconds: How long to hold the reservation (default: 1 hour)
        exclusive: Whether to block other agents from these files
        reason: Why you're reserving these files
        
    Returns:
        dict with 'granted' and 'conflicts' lists
        
    Example:
        result = reserve_files(
            paths=["src/api/routes.py"],
            agent_name="LettaRefactorAgent",
            exclusive=True,
            reason="Refactoring API endpoints"
        )
    """
    project_key = os.getenv(
        "PROJECT_PATH",
        "/opt/stacks/matrix-synapse-deployment"
    )
    
    response = requests.post(
        MCP_AGENT_MAIL_URL,
        headers={
            "Authorization": f"Bearer {MCP_AGENT_MAIL_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "file_reservation_paths",
                "arguments": {
                    "project_key": project_key,
                    "agent_name": agent_name,
                    "paths": paths,
                    "ttl_seconds": ttl_seconds,
                    "exclusive": exclusive,
                    "reason": reason
                }
            },
            "id": 1
        }
    )
    
    result = response.json()
    
    if "error" in result:
        raise Exception(f"Reservation failed: {result['error']}")
    
    return result["result"]


def release_files(
    agent_name: str,
    paths: Optional[List[str]] = None
) -> dict:
    """
    Release file reservations after you're done editing.
    
    Args:
        agent_name: Your agent identity name
        paths: Specific paths to release (None = release all)
        
    Returns:
        dict with 'released' count and timestamp
        
    Example:
        release_files(
            agent_name="LettaRefactorAgent",
            paths=["src/api/routes.py"]
        )
    """
    project_key = os.getenv(
        "PROJECT_PATH",
        "/opt/stacks/matrix-synapse-deployment"
    )
    
    response = requests.post(
        MCP_AGENT_MAIL_URL,
        headers={
            "Authorization": f"Bearer {MCP_AGENT_MAIL_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "release_file_reservations",
                "arguments": {
                    "project_key": project_key,
                    "agent_name": agent_name,
                    "paths": paths
                }
            },
            "id": 1
        }
    )
    
    result = response.json()
    
    if "error" in result:
        raise Exception(f"Release failed: {result['error']}")
    
    return result["result"]


def check_file_reservations(paths: List[str]) -> dict:
    """
    Check if files are currently reserved by other agents.
    
    Args:
        paths: File patterns to check
        
    Returns:
        dict with list of active reservations
        
    Example:
        status = check_file_reservations(["src/api/*.py"])
        if status["active_reservations"]:
            print("Files are reserved by another agent!")
    """
    project_key = os.getenv(
        "PROJECT_PATH",
        "/opt/stacks/matrix-synapse-deployment"
    )
    
    response = requests.get(
        f"{MCP_AGENT_MAIL_URL.replace('/mcp/', '')}/mail/{project_key}/file_reservations",
        headers={"Authorization": f"Bearer {MCP_AGENT_MAIL_TOKEN}"}
    )
    
    all_reservations = response.json()
    
    # Filter to active reservations matching paths
    # (simplified - production should use proper glob matching)
    relevant = [
        r for r in all_reservations
        if r.get("released_ts") is None
        and any(p in r.get("path_pattern", "") for p in paths)
    ]
    
    return {
        "active_reservations": relevant,
        "has_conflicts": len(relevant) > 0
    }


# Export as Letta tools
reserve_files_tool = Tool.from_function(reserve_files)
release_files_tool = Tool.from_function(release_files)
check_reservations_tool = Tool.from_function(check_file_reservations)
```

#### 3.2 Register Tools with Letta Agents

```python
# In your Letta agent initialization code:

from src.letta.tools.file_reservations import (
    reserve_files_tool,
    release_files_tool,
    check_reservations_tool
)

# Add to agent
agent.add_tool(reserve_files_tool)
agent.add_tool(release_files_tool)
agent.add_tool(check_reservations_tool)
```

#### 3.3 Update Agent System Prompts

Add to agent system instructions:

```markdown
## File Editing Protocol

Before editing any files, you MUST:

1. **Check for reservations**:
   - Call `check_file_reservations(paths)` to see if files are reserved
   - If reserved by another agent, either:
     - Wait for them to release
     - Ask in Matrix if you can proceed
     - Work on different files

2. **Reserve files**:
   - Call `reserve_files(paths, agent_name, exclusive=True, reason="...")`
   - Always provide a clear reason
   - Set appropriate TTL (default: 1 hour)

3. **Announce in Matrix**:
   - Send message: "🔒 Reserved src/api/*.py for refactoring (1 hour)"
   - Include thread/ticket reference

4. **Do your work**:
   - Edit the files as needed
   - Keep reservation active while working

5. **Release when done**:
   - Call `release_files(agent_name, paths)`
   - Announce in Matrix: "✅ Released src/api/*.py"

Example workflow:
```python
# Check first
status = check_file_reservations(["src/api/routes.py"])
if status["has_conflicts"]:
    # Handle conflict
    pass

# Reserve
reserve_files(
    paths=["src/api/routes.py"],
    agent_name="LettaAgent123",
    exclusive=True,
    reason="Adding new endpoint for user preferences"
)

# Announce
send_matrix_message("🔒 Reserved src/api/routes.py for 1 hour")

# Do work
# ... edit files ...

# Release
release_files(agent_name="LettaAgent123")
send_matrix_message("✅ Released src/api/routes.py")
```
```

**Success criteria**:
- ✅ Tools added to Letta agents
- ✅ Agents can reserve/release files
- ✅ Agents check for conflicts before editing
- ✅ Tool calls succeed without errors

---

### Phase 4: OpenCode Integration (3-4 hours)

**Goal**: Make OpenCode agents aware of file reservations

#### 4.1 Add Reservation Check to OpenCode Bridge

Edit `/opt/stacks/matrix-synapse-deployment/opencode-bridge/src/bridge.ts`:

```typescript
// Add MCP Agent Mail client
import axios from 'axios';

const MCP_AGENT_MAIL_URL = process.env.MCP_AGENT_MAIL_URL || 'http://127.0.0.1:8766/mcp/';
const MCP_AGENT_MAIL_TOKEN = process.env.MCP_AGENT_MAIL_TOKEN;

interface FileReservation {
  path_pattern: string;
  agent_name: string;
  exclusive: boolean;
  expires_ts: string;
  reason: string;
}

async function checkFileReservations(paths: string[]): Promise<FileReservation[]> {
  try {
    const response = await axios.post(MCP_AGENT_MAIL_URL, {
      jsonrpc: '2.0',
      method: 'tools/call',
      params: {
        name: 'list_file_reservations',
        arguments: {
          project_key: process.env.PROJECT_PATH || '/opt/stacks/matrix-synapse-deployment',
          active_only: true
        }
      },
      id: 1
    }, {
      headers: {
        'Authorization': `Bearer ${MCP_AGENT_MAIL_TOKEN}`,
        'Content-Type': 'application/json'
      }
    });

    const reservations = response.data.result || [];
    
    // Filter to relevant paths
    return reservations.filter((r: FileReservation) =>
      paths.some(p => r.path_pattern.includes(p) || p.includes(r.path_pattern))
    );
  } catch (error) {
    console.error('Failed to check file reservations:', error);
    return [];
  }
}

async function reserveFiles(
  agentName: string,
  paths: string[],
  reason: string
): Promise<void> {
  await axios.post(MCP_AGENT_MAIL_URL, {
    jsonrpc: '2.0',
    method: 'tools/call',
    params: {
      name: 'file_reservation_paths',
      arguments: {
        project_key: process.env.PROJECT_PATH || '/opt/stacks/matrix-synapse-deployment',
        agent_name: agentName,
        paths,
        ttl_seconds: 3600,
        exclusive: true,
        reason
      }
    },
    id: 1
  }, {
    headers: {
      'Authorization': `Bearer ${MCP_AGENT_MAIL_TOKEN}`,
      'Content-Type': 'application/json'
    }
  });
}

// Export functions
export { checkFileReservations, reserveFiles };
```

#### 4.2 Add Pre-Edit Check

```typescript
// Before OpenCode agent edits files:

import { checkFileReservations, reserveFiles } from './bridge';

async function handleFileEdit(filePath: string, agentName: string) {
  // Check for conflicts
  const conflicts = await checkFileReservations([filePath]);
  
  if (conflicts.length > 0 && conflicts[0].agent_name !== agentName) {
    const conflict = conflicts[0];
    throw new Error(
      `File ${filePath} is reserved by ${conflict.agent_name}: ${conflict.reason}\n` +
      `Expires: ${conflict.expires_ts}`
    );
  }
  
  // Reserve the file
  await reserveFiles(agentName, [filePath], 'OpenCode edit session');
  
  // Proceed with edit
  // ...
}
```

**Success criteria**:
- ✅ OpenCode checks reservations before edits
- ✅ Conflicts are detected and reported
- ✅ OpenCode can reserve files
- ✅ Integration doesn't break existing functionality

---

### Phase 5: Matrix Notifications (2-3 hours)

**Goal**: Announce file reservations in Matrix for visibility

#### 5.1 Add Notification Helper

Create `/opt/stacks/matrix-synapse-deployment/src/utils/file_reservation_notifier.py`:

```python
"""Notify Matrix rooms about file reservations."""

import asyncio
from typing import List
from matrix_bot_sdk import AsyncClient, MatrixRoom

async def announce_reservation(
    client: AsyncClient,
    room_id: str,
    agent_name: str,
    paths: List[str],
    ttl_seconds: int,
    reason: str,
    exclusive: bool = True
) -> None:
    """Announce file reservation in Matrix room."""
    
    lock_emoji = "🔒" if exclusive else "🔓"
    duration = f"{ttl_seconds // 3600}h" if ttl_seconds >= 3600 else f"{ttl_seconds // 60}m"
    
    message = (
        f"{lock_emoji} **File Reservation**\n\n"
        f"**Agent**: {agent_name}\n"
        f"**Files**: `{', '.join(paths)}`\n"
        f"**Type**: {'Exclusive' if exclusive else 'Shared'}\n"
        f"**Duration**: {duration}\n"
        f"**Reason**: {reason}"
    )
    
    await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": message,
            "format": "org.matrix.custom.html",
            "formatted_body": message.replace("\n", "<br/>")
        }
    )


async def announce_release(
    client: AsyncClient,
    room_id: str,
    agent_name: str,
    paths: List[str]
) -> None:
    """Announce file reservation release in Matrix room."""
    
    message = (
        f"✅ **Files Released**\n\n"
        f"**Agent**: {agent_name}\n"
        f"**Files**: `{', '.join(paths)}`"
    )
    
    await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.text",
            "body": message,
            "format": "org.matrix.custom.html",
            "formatted_body": message.replace("\n", "<br/>")
        }
    )
```

#### 5.2 Integrate with Reservation Tools

Update `file_reservations.py` to call notifier:

```python
from src.utils.file_reservation_notifier import announce_reservation, announce_release

def reserve_files(...):
    # ... existing reservation logic ...
    
    # Announce in Matrix
    if os.getenv("MATRIX_ANNOUNCE_RESERVATIONS") == "true":
        room_id = os.getenv("MATRIX_DEV_ROOM_ID")
        asyncio.create_task(
            announce_reservation(
                matrix_client,
                room_id,
                agent_name,
                paths,
                ttl_seconds,
                reason,
                exclusive
            )
        )
    
    return result
```

**Success criteria**:
- ✅ Reservations announced in Matrix
- ✅ Releases announced in Matrix
- ✅ Messages formatted clearly
- ✅ Optional (can be disabled via env var)

---

## Testing Plan

### Unit Tests

```bash
# Test file reservation API
pytest tests/unit/test_file_reservations.py -v

# Test pre-commit guard
bash scripts/test_guard.sh

# Test Letta tool integration
pytest tests/unit/test_letta_file_tools.py -v
```

### Integration Tests

```bash
# Test end-to-end workflow
python tests/integration/test_file_reservation_workflow.py

# Expected flow:
# 1. Agent A reserves files
# 2. Agent B tries to edit → blocked
# 3. Agent A releases
# 4. Agent B can now edit
```

### Manual Testing Checklist

- [ ] MCP Agent Mail server running on port 8766
- [ ] Health check passes
- [ ] Project created in MCP Agent Mail
- [ ] Pre-commit guard installed
- [ ] Guard blocks conflicting commits
- [ ] Letta agent can reserve files
- [ ] Letta agent can release files
- [ ] OpenCode checks reservations before edit
- [ ] Matrix notifications sent for reservations
- [ ] Web UI shows active reservations
- [ ] Audit trail visible in Git

---

## Monitoring & Metrics

### What to Track

1. **Conflict Prevention**:
   - Count of blocked commits (guard)
   - Count of reservation conflicts detected
   - Files most frequently reserved

2. **Usage Patterns**:
   - Average reservation TTL
   - Most active agents
   - Peak reservation times

3. **Health**:
   - MCP Agent Mail uptime
   - API response times
   - Storage growth rate

### Dashboards

```bash
# Check active reservations
curl http://127.0.0.1:8766/mail/<project>/file_reservations?active_only=true

# View reservation audit trail
cd /opt/stacks/mcp_agent_mail/data/projects/<slug>/file_reservations
ls -la *.json

# Check guard logs
tail -f /var/log/mcp-agent-mail/guard.log
```

---

## Rollback Plan

If something goes wrong:

### Quick Rollback (5 minutes)

```bash
# 1. Stop MCP Agent Mail
docker stop mcp-agent-mail
# OR
pkill -f mcp_agent_mail

# 2. Uninstall pre-commit guards
cd /opt/stacks/matrix-synapse-deployment
uv run python -m mcp_agent_mail.cli guard uninstall .

# 3. Remove tools from Letta agents
# (Comment out tool registration code)

# System returns to Matrix-only mode
```

### Full Removal (15 minutes)

```bash
# Remove MCP Agent Mail completely
rm -rf /opt/stacks/mcp_agent_mail

# Remove Docker service
docker-compose down mcp-agent-mail
docker volume rm mcp_agent_mail_data

# Revert code changes
git checkout src/letta/tools/file_reservations.py
git checkout opencode-bridge/src/bridge.ts
```

---

## Success Metrics (30 days post-deployment)

**We'll know this is successful when**:

1. ✅ **Zero file conflicts** between agents (vs. baseline)
2. ✅ **>80% agent compliance** with reservation protocol
3. ✅ **<2 seconds** average reservation/release latency
4. ✅ **Clear audit trail** of all file edits
5. ✅ **Positive agent feedback** (no complaints about workflow overhead)

---

## Next Steps

1. **Review this plan** with team
2. **Allocate time** for implementation (2-3 days)
3. **Set up test environment** (separate project)
4. **Execute Phase 1** (deploy MCP Agent Mail)
5. **Iterate through remaining phases**
6. **Monitor and tune** for 30 days

---

## References

- **MCP Agent Mail Docs**: https://github.com/Dicklesworthstone/mcp_agent_mail
- **Evaluation Doc**: `/opt/stacks/matrix-synapse-deployment/docs/MCP_AGENT_MAIL_EVALUATION.md`
- **File Reservation API**: See MCP Agent Mail README "File Reservations" section
- **Pre-commit Guard**: See MCP Agent Mail README "Pre-commit Guard" section
