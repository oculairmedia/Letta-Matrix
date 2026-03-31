# Filesystem Mode Autonomy Plan

## Goals
- Auto-enable Letta Code filesystem mode for selected Matrix rooms (e.g., Huly project agents) without manual `/fs-task on` each session.
- Ensure each room maps to the correct project directory and agent ID, persisting defaults per room.
- Maintain existing manual commands (`/fs-link`, `/fs-run`, `/fs-task on|off|status`) for overrides.

## Implementation Outline

1. **Room Project Resolution**
   - Extend `resolve_letta_project_dir` to consult VibSync or Huly metadata when `.letta` session data is missing.
   - If no directory stored, call VibSync `/api/letta-code/configure-project` (or similar helper) using agent_id to fetch canonical project path.
   - Persist resolved path in `.letta-code/letta_code_state.json` for reuse.

2. **Default Auto-Enable Logic**
   - New env var `LETTA_CODE_AUTO_ENABLE_REGEX` (e.g., `^Huly - .*`).
   - On first message from matching room, if `state.enabled` is undefined and `projectDir` is known, set `enabled=True` automatically and send acknowledgement.
   - If `projectDir` missing, send guidance to run `/fs-link` and leave disabled until successful link.

3. **User Controls**
   - Keep `/fs-task on|off|status` for manual toggling.
   - Optional `/fs-task auto on|off` to override default behavior per room (stored alongside state).
   - `/fs-run` continues to execute single prompts regardless of toggle state.

4. **Safety & Error Handling**
   - If auto-enabled room encounters Letta Code errors (missing session, task failure), log and temporarily disable with notice.
   - Ensure commands only trigger in rooms that belong to known agent mappings.

5. **Validation Plan**
   - Unit tests covering auto-enable path and fallback when project paths missing.
   - Manual steps:
     1. Set regex to target Huly room.
     2. Send normal message; confirm logs show `Filesystem Task` dispatch.
     3. Run `/fs-task status` to verify `ENABLED` and path.
     4. Toggle off via `/fs-task off`; confirm normal Letta cloud mode resumes.
