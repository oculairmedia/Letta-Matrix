<!-- VIBESYNC:project-info:START -->

# Agent Instructions

## Project Info

- **Project Identifier**: `matrix-tuwunel-deploy`
- **Project Name**: Matrix Tuwunel Deploy
- **PM Agent ID**: `agent-b19ab6d5-460b-48f7-b300-738021d9a882`
- **Path**: `/opt/stacks/matrix-tuwunel-deploy`
- **Beads Prefix**: `matrix-tuwunel-deploy`

## Workflow Instructions

1. **Before starting work**: Search for related issues using `bd ready` or the `vibesync` MCP (`project_query` tool with `project_identifier: "matrix-tuwunel-deploy"`)
2. **Issue tracking**: Use `bd` (beads) for all issue management — create, update, close
3. **On task completion**: Report to this project's PM agent via `matrix-identity-bridge` using `talk_to_agent`
4. **Memory**: Store important discoveries in Graphiti with `graphiti-mcp_add_memory`
<!-- VIBESYNC:project-info:END -->

<!-- VIBESYNC:reporting-hierarchy:START -->
## PM Agent Communication

**Project PM Agent:** `Huly - matrix-tuwunel-deploy` (agent-870d3dfb-319f-4c52-91f1-72ab46d944a7)

### Reporting Hierarchy

```
Emmanuel (Stakeholder)
    ↓
Meridian (Director of Engineering)
    ↓
PM Agent (Technical Product Owner - mega-experienced)
    ↓ communicates with
You (Developer Agent - experienced)
```

### MANDATORY: Report to PM Agent

**BEFORE reporting outcomes to the user**, send a report to the PM agent via Matrix:

```json
{
  "operation": "talk_to_agent",
  "agent": "Huly - matrix-tuwunel-deploy",
  "message": "<your report>",
  "caller_directory": "/opt/stacks/matrix-tuwunel-deploy"
}
```

### When to Contact PM Agent

| Situation             | Action                                                              |
| --------------------- | ------------------------------------------------------------------- |
| Task completed        | Report outcome to PM before responding to user                      |
| Blocking question     | Forward to PM - they know user's wishes and will escalate if needed |
| Architecture decision | Consult PM for guidance                                             |
| Unclear requirements  | PM can clarify or contact user                                      |

### Report Format

```
**Status**: [Completed/Blocked/In Progress]
**Task**: [Brief description]
**Outcome**: [What was done/What's blocking]
**Files Changed**: [List if applicable]
**Next Steps**: [If any]
```

> **⚠️ KNOWN BUG**: PM agent messaging is currently **disabled** to prevent message loops. Do NOT send messages to the PM agent until the forwarding loop issue is resolved. Report directly to the user instead.

<!-- VIBESYNC:reporting-hierarchy:END -->

<!-- VIBESYNC:beads-instructions:START -->

## Beads Issue Tracking

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

### Beads Sync Flow (Hybrid System)

Beads uses a **hybrid sync** approach for reliability:

#### Automatic Sync (Real-time)

- `bd create`, `bd update`, `bd close` write to SQLite DB
- File watcher detects DB changes automatically
- Syncs to VibSync within ~30-60 seconds

#### Git Persistence (`bd sync`)

- `bd sync` exports to JSONL and commits to git
- Required for cross-machine persistence
- Run before ending session to ensure changes are saved

### Best Practice

```bash
bd create "New task"   # Auto-syncs to VibSync
bd close some-issue    # Auto-syncs to VibSync
bd sync                # Git backup (recommended before session end)
```

<!-- VIBESYNC:beads-instructions:END -->

<!-- VIBESYNC:bookstack-docs:START -->
## BookStack Documentation

- **Source of truth**: [BookStack](https://knowledge.oculair.ca)
- **Local sync**: `docs/bookstack/` (read-only mirror, syncs hourly)
- **To read docs**: Check `docs/bookstack/{book-slug}/` in your project directory
- **To create/edit docs**: Use `bookstack-mcp` tools to write directly to BookStack
- **Never edit** files in `docs/bookstack/` locally — they will be overwritten on next sync
- **PRDs and design docs** must be stored in BookStack, not local markdown files
<!-- VIBESYNC:bookstack-docs:END -->

<!-- VIBESYNC:session-completion:START -->

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- VIBESYNC:session-completion:END -->

<!-- VIBESYNC:codebase-context:START -->

## Codebase Context

**Project**: Matrix Tuwunel Deploy (`matrix-tuwunel-deploy`)
**Path**: `/opt/stacks/matrix-tuwunel-deploy`

This project's PM agent has a `codebase_ast` memory block with live structural data including:

- File counts and function counts per directory
- Key modules and their roles
- Quality signals (doc gaps, untested modules, complexity hotspots)
- Recent file changes

Ask the PM agent for architectural guidance before making significant changes.

<!-- VIBESYNC:codebase-context:END -->

## OOM Recovery Runbook

If the host runs out of memory, Tuwunel's RocksDB can get corrupted, causing authentication failures for critical users (`admin`, `letta`, `oc_letta_v2`, etc.) and Letta agent Matrix identities (`agent_*`).

### Symptoms

- Matrix client bootlooping with `M_FORBIDDEN: Wrong username or password`
- Messages not being forwarded to Letta
- OpenCode identity routing failures
- Letta agents not responding in their Matrix rooms (agent identity auth failures)

### Detection & Auto-Recovery

The health check script runs every 15 minutes via cron and **automatically recovers** failed users:

```bash
./scripts/health-check-auth.sh                # Check + auto-recover (default)
./scripts/health-check-auth.sh --no-recover   # Check only, no recovery
./scripts/health-check-auth.sh --check-only   # Alias for --no-recover
./scripts/health-check-auth.sh --check-only --simulate-agent-failures=3
```

Install or refresh the cron entry with:

```bash
./scripts/install-health-check-cron.sh
```

**How auto-recovery works:**

1. The script tests login for all critical users (`admin`, `letta`, `oc_letta_v2`, `oc_matrix_tuwunel_deploy_v2`)
2. The script discovers all Letta agent identities from the identity bridge storage (`mcp-servers/matrix-identity-bridge/data/archive/identities.json`) and tests their logins in parallel (10 concurrent)
3. If any core user fails, recovery is attempted in two tiers:

   **Tier 1 — Admin Room Reset (no downtime, ~5s):**
   - Uses a *healthy* user to log in and send `!admin users reset-password` to `#admins:matrix.oculair.ca`
   - Tuwunel processes the command and resets the password
   - Login is verified after reset
   - Each healthy user is tried in turn until one succeeds
   - The recovery user must be a **member of the admin room** (not just a healthy user)

   **Tier 2 — CLI Reset (fallback, ~20-30s downtime):**
   - Used when Tier 1 fails (e.g., no healthy users in admin room, or all users are broken)
   - Stops Tuwunel, resets passwords via CLI, restarts
   - Volume path **must** be absolute — `docker run` does not support relative paths like `./`
   - All remaining failed users are batched into a single stop/start cycle

4. If any agent identities fail, they are recovered via **Tier 1 only** (batched admin room resets — Tier 2 CLI restart is too disruptive for non-core users)
   - Passwords are read from the matrix-client PostgreSQL DB (`matrix_letta.agent_mappings`) as the primary source, with identity bridge (`localpart = password`) as fallback
   - All reset commands are sent, then a single wait period, then parallel verification
   - Typical batch: ~76 agents in ~33 seconds
5. A lock file (`/tmp/matrix-health-recovery.lock`) prevents concurrent recovery attempts
6. ntfy alert is sent with the outcome:
   - Recovery succeeded: low-priority notification, no action needed
   - Recovery failed: urgent notification, manual intervention required

### Auto-Recovery (Identity Bridge)

The identity bridge (`matrix-messaging-mcp`) also handles password resets independently via Tuwunel's admin room. When an identity fails to authenticate during provisioning, the bridge resets the password automatically. This is a separate mechanism from the health check.

### Manual Recovery Steps

If auto-recovery fails or you need to reset passwords manually:

**Option A: Admin Room (Tuwunel running)**

Send commands to `#admins:matrix.oculair.ca` as any user who is a member of the admin room:

```
!admin users reset-password <localpart> <new_password>
```

**Option B: CLI (Tuwunel stopped)**

1. **Stop Tuwunel**:

   ```bash
   docker compose stop tuwunel
   ```

2. **Reset failed user passwords** (volume path **must** be absolute — `docker run` does not support relative paths like `./`):

   ```bash
   timeout 60 docker run --rm --entrypoint "" \
     -e TUWUNEL_SERVER_NAME=matrix.oculair.ca \
     -v /opt/stacks/matrix-tuwunel-deploy/tuwunel-data:/var/lib/tuwunel \
     ghcr.io/oculairmedia/tuwunel-docker2010:latest \
     /usr/local/bin/tuwunel -c /var/lib/tuwunel/tuwunel.toml \
     -O 'server_name="matrix.oculair.ca"' \
     --execute "users reset-password <username> <password>"
   ```

   Common users to reset:
   - `admin` / `$MATRIX_ADMIN_PASSWORD`
   - `letta` / `letta`
   - `oc_letta_v2` / `oc_letta_v2`
   - `oc_matrix_tuwunel_deploy_v2` / `oc_matrix_tuwunel_deploy_v2`

3. **Restart services**:

   ```bash
   docker compose up -d
   ```

4. **Verify recovery**:
   ```bash
   ./scripts/health-check-auth.sh --check-only
   ```

### Regression Tests

Run the recovery regression tests to verify the mechanism works:

```bash
./scripts/tests/test-health-recovery.sh
```

Tests cover: login validation, admin room resolution, message delivery, full reset cycle, flag behavior, and lock file concurrency.

Dry-run failure simulation:

```bash
./scripts/health-check-auth.sh --check-only --simulate-agent-failures=3
```

### Prevention

- Monitor host memory usage
- Set resource limits on memory-hungry processes (e.g., OpenCode sessions)
- Health check runs automatically via cron (`*/15 * * * *`)

## Bulk Operations in Agent-Listening Rooms

**NEVER send bulk text commands (bridge commands, admin commands, etc.) into rooms where Letta agents are listening via portal links or agent mappings.**

### What Happened

During portal link setup, `set-relay` commands (e.g., `!wa set-relay`, `!fb set-relay`) were sent as text messages into 134 bridged rooms. Because Meridian was already joined and listening via portal links, each command was routed to the Letta agent as a user message — triggering 134 Opus responses and burning through the entire API quota.

### Rules for Bulk Room Operations

1. **Disable agent processing first** — Before sending bulk commands into agent-listening rooms:
   - Temporarily disable portal links: `DELETE /agents/{id}/portal-links` for affected rooms
   - Or stop the matrix-client container: `docker compose stop matrix-client`
   - Or remove the agent from rooms before sending commands

2. **Re-enable after** — Once bulk commands are done:
   - Re-create portal links: `POST /agents/{id}/portal-links`
   - Or restart: `docker compose up -d matrix-client`

3. **Prefer bot-level commands** — If possible, send bridge commands as the bridge bot itself (via appservice token) rather than as a user, since bot messages are typically ignored by the routing logic.

4. **Test with ONE room first** — Before any bulk operation, test the command in a single room and verify it doesn't trigger agent responses.

### Quick Reference — Disable/Re-enable Agent Processing

```bash
# Option 1: Stop message processing entirely
docker compose stop matrix-client
# ... do bulk operations ...
docker compose up -d matrix-client

# Option 2: Disable specific portal links via API
curl -s -X DELETE "http://localhost:8004/agents/{agent_id}/portal-links/{room_id}"
# ... do bulk operations in that room ...
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"room_id": "!room:matrix.oculair.ca"}' \
  "http://localhost:8004/agents/{agent_id}/portal-links"
```

## Agent Mapping Sync Issues

The matrix-client uses **PostgreSQL** as the source of truth for agent-room mappings (not the JSON file at `/app/data/agent_user_mappings.json`). If these two sources become out of sync, file uploads and other features that depend on `db.get_by_room_id()` will fail.

### Symptoms

- File uploads fail with error: `No agent mapping for room !XXX:matrix.oculair.ca`
- The mapping exists in `/app/data/agent_user_mappings.json` but `db.get_by_room_id()` returns `None`
- Logs show room being processed but skipped with "No agent mapping for room"

### Root Cause

The PostgreSQL database (`matrix_letta.agent_mappings` table) has stale or incorrect room IDs:
- JSON file has correct mapping: `Meridian` → `!PPBT0ouhNr9W2TGjUk:matrix.oculair.ca`
- PostgreSQL has wrong mapping: `Meridian` → `!O8cbkBGCMB8Ujlaret:matrix.oculair.ca`
- When `get_by_room_id()` is called with the correct room ID, it returns `None`

### Diagnosis

Check if mapping exists in PostgreSQL:

```bash
docker exec matrix-tuwunel-deploy-matrix-client-1 python3 -c "
from src.models.agent_mapping import AgentMappingDB

db = AgentMappingDB()
mapping = db.get_by_room_id('!PPBT0ouhNr9W2TGjUk:matrix.oculair.ca')

if mapping:
    print(f'Found: {mapping.agent_name} ({mapping.agent_id})')
else:
    print('NOT FOUND in PostgreSQL')
"
```

Check if mapping exists in JSON file:

```bash
docker exec matrix-tuwunel-deploy-matrix-client-1 python3 -c "
import json
with open('/app/data/agent_user_mappings.json', 'r') as f:
    data = json.load(f)
for mxid, mapping in data.items():
    if 'PPBT0ouhNr9W2TGjUk' in mapping.get('room_id', ''):
        print(f'JSON: {mapping.get(\"agent_name\")} -> {mapping.get(\"room_id\")}')
"
```

### Solution

Update PostgreSQL to match the JSON file:

```bash
docker exec matrix-tuwunel-deploy-matrix-client-1 python3 -c "
from sqlalchemy import create_engine, text
import os

db_url = os.environ.get('DATABASE_URL', '')
engine = create_engine(db_url)

with engine.connect() as conn:
    result = conn.execute(text('''
        UPDATE agent_mappings 
        SET room_id = :new_room_id 
        WHERE agent_id = :agent_id
    '''), {
        'new_room_id': '!PPBT0ouhNr9W2TGjUk:matrix.oculair.ca',
        'agent_id': 'agent-597b5756-2915-4560-ba6b-91005f085166'
    })
    conn.commit()
    print(f'Updated {result.rowcount} row(s)')
"
```

### Prevention

This issue occurred because:
1. Meridian's room was manually recreated (room ID changed from `!O8cbkBGCMB8Ujlaret` to `!PPBT0ouhNr9W2TGjUk`)
2. The JSON file was updated but PostgreSQL was not

**Best practice**: When manually updating room IDs:
- Always update BOTH sources (JSON + PostgreSQL)
- Or use the matrix-client API endpoints which handle both automatically
- Or restart matrix-client after JSON update (if it has migration logic)

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
