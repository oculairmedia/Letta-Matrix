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

If the host runs out of memory, Tuwunel's RocksDB can get corrupted, causing authentication failures for critical users (`admin`, `letta`, `oc_letta_v2`, etc.).

### Symptoms

- Matrix client bootlooping with `M_FORBIDDEN: Wrong username or password`
- Messages not being forwarded to Letta
- OpenCode identity routing failures

### Detection

Run the health check script:

```bash
./scripts/health-check-auth.sh
```

### Recovery Steps

1. **Stop Tuwunel**:

   ```bash
   docker compose stop tuwunel
   ```

2. **Reset failed user passwords** (run for each failed user):

   ```bash
   timeout 60 docker run --rm --entrypoint "" \
     -e TUWUNEL_SERVER_NAME=matrix.oculair.ca \
     -v ./tuwunel-data:/var/lib/tuwunel \
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
   ./scripts/health-check-auth.sh
   ```

### Prevention

- Monitor host memory usage
- Set resource limits on memory-hungry processes (e.g., OpenCode sessions)
- Run health check periodically via cron
