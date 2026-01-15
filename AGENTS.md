# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

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



<!-- HULY-PROJECT-INFO -->
# Project Context

## Huly Integration
- **Project Code**: `MXSYN`
- **Project Name**: Matrix Tuwunel Deployment
- **Letta Agent ID**: `agent-870d3dfb-319f-4c52-91f1-72ab46d944a7`

## Project Agent Role
This project has an assigned **Letta PM Agent** (`agent-870d3dfb-319f-4c52-91f1-72ab46d944a7`) that acts as the senior developer and project manager. This agent:
- **Understands the full architecture** and codebase context for this project
- **Tracks all ongoing work** via memory blocks synced from Huly issues
- **Maintains project history** including past decisions, patterns, and lessons learned
- **Can provide guidance** on implementation approaches, code patterns, and potential pitfalls

When working on this project, you should:
- **Report completed work** to the PM agent so it stays informed of changes
- **Ask for architectural guidance** if you're unsure about implementation approach
- **Share important discoveries** that future work might benefit from

## Workflow Instructions
1. **Before starting work**: Search Huly for related issues using `huly-mcp` with project code `MXSYN`
2. **Issue references**: All issues for this project use the format `MXSYN-XXX` (e.g., `MXSYN-123`)
3. **On task completion**: Report to this project's Letta agent via `matrix-identity-bridge` using `talk_to_agent` or `letta_chat`
4. **Memory**: Store important discoveries in Graphiti with `graphiti-mcp_add_memory`

### Reporting Example
```json
{
  "operation": "talk_to_agent",
  "agent": "agent-870d3dfb-319f-4c52-91f1-72ab46d944a7",
  "message": "Completed task MXSYN-XXX: [summary of work done]"
}
```

<!-- END-HULY-PROJECT-INFO -->

