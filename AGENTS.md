<!-- VIBESYNC:project-info:START -->

# Agent Instructions

## Huly Integration

- **Project Code**: `MXSYN`
- **Project Name**: Matrix Synapse Deployment
- **Letta Agent ID**: `agent-870d3dfb-319f-4c52-91f1-72ab46d944a7`

## Workflow Instructions

1. **Before starting work**: Search Huly for related issues using `huly-mcp` with project code `MXSYN`
2. **Issue references**: All issues for this project use the format `MXSYN-XXX` (e.g., `MXSYN-123`)
3. **On task completion**: Report to this project's Letta agent via `matrix-identity-bridge` using `talk_to_agent`
4. **Memory**: Store important discoveries in Graphiti with `graphiti-mcp_add_memory`
<!-- VIBESYNC:project-info:END -->

<!-- VIBESYNC:reporting-hierarchy:START -->
## PM Agent Communication

**Project PM Agent:** `Huly - Matrix Synapse Deployment` (agent-870d3dfb-319f-4c52-91f1-72ab46d944a7)

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
  "agent": "Huly - Matrix Synapse Deployment",
  "message": "<your report>",
  "caller_directory": "/opt/stacks/matrix-synapse-deployment"
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
- Syncs to Huly within ~30-60 seconds

#### Git Persistence (`bd sync`)

- `bd sync` exports to JSONL and commits to git
- Required for cross-machine persistence
- Run before ending session to ensure changes are saved

### Best Practice

```bash
bd create "New task"   # Auto-syncs to Huly
bd close some-issue    # Auto-syncs to Huly
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

**Project**: Matrix Synapse Deployment (`MXSYN`)
**Path**: `/opt/stacks/matrix-synapse-deployment`

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
```

**How auto-recovery works:**

1. The script tests login for all critical users (`admin`, `letta`, `oc_letta_v2`, `oc_matrix_synapse_deployment_v2`)
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
     -v /opt/stacks/matrix-synapse-deployment/tuwunel-data:/var/lib/tuwunel \
     ghcr.io/oculairmedia/tuwunel-docker2010:latest \
     /usr/local/bin/tuwunel -c /var/lib/tuwunel/tuwunel.toml \
     -O 'server_name="matrix.oculair.ca"' \
     --execute "users reset-password <username> <password>"
   ```

   Common users to reset:
   - `admin` / `$MATRIX_ADMIN_PASSWORD`
   - `letta` / `letta`
   - `oc_letta_v2` / `oc_letta_v2`
   - `oc_matrix_synapse_deployment_v2` / `oc_matrix_synapse_deployment_v2`

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

### Prevention

- Monitor host memory usage
- Set resource limits on memory-hungry processes (e.g., OpenCode sessions)
- Health check runs automatically via cron (`*/15 * * * *`)
