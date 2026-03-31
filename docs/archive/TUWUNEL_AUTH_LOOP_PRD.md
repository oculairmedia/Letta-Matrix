# Tuwunel Authentication Loop PRD

## Overview
Persistent login attempts from the matrix-client container smother the tuwunel homeserver, starving Jellyfin and other services of CPU. The loop is caused by an aggressive periodic sync that reprocesses every agent-room mapping twice per second. Each iteration triggers redundant admin and @letta logins and joins across 56+ rooms, yielding tens of millions of login calls per day. This document defines the requirements to eliminate the loop while ensuring agent provisioning remains reliable.

## Problem Statement
- `periodic_agent_sync` in `src/matrix/client.py` runs every 0.5s and invokes `sync_agents_to_users`.
- `sync_agents_to_users` iterates through all agent rooms and always calls `auto_accept_invitations_with_tracking`.
- `auto_accept_invitations_with_tracking` re-authenticates @admin and @letta for every room regardless of membership, generating 100+ login requests per second.
- Result: tuwunel container saturates CPU (>75% for 7+ days) and degrades host-level services including Jellyfin.

## Goals
1. Reduce login/join traffic to only when membership or invitations truly change.
2. Maintain automatic provisioning for new agents, rooms, and admin presence.
3. Ensure tuwunel CPU usage stays below 20% at idle with current agent counts.

## Non-Goals
- Replacing tuwunel with Synapse or other homeservers.
- Altering agent onboarding flows outside of Matrix membership management.
- Optimizing Jellyfin itself (it should simply benefit from released CPU headroom).

## Functional Requirements
1. **Throttled Sync Cadence**
   - Default `periodic_agent_sync` interval must be configurable and increased to ≥60 seconds.
   - Provide ability to override via environment variable for emergency debugging.
2. **Reusable Manager Instance**
   - `run_agent_sync` must reuse a singleton `AgentUserManager` (and thus `MatrixRoomManager`) to persist caches between cycles.
   - Reload configuration only when environment variables change.
3. **Membership Cache Backed by Matrix API**
   - `MatrixRoomManager.auto_accept_invitations_with_tracking` must maintain an in-memory cache keyed by `(room_id, username)` and skip login/join requests when a user is already joined.
   - Cache accuracy must be validated through Matrix API membership endpoints so the loop trusts authoritative homeserver state instead of local assumptions.
   - Cache must invalidate when membership state is unknown or when an explicit recheck is requested.
4. **Conditional Invitation Handling**
   - Invitation acceptance should run only when `invitation_status` indicates "invited"/"unknown" or when Matrix API membership checks (e.g., `/joined_members`) report the user absent.
   - Detect and remediate room drift without rejoining rooms already confirmed.
5. **Monitoring Hooks**
   - Emit structured logs when cache hits skip login attempts, and when throttled sync runs start/finish.
   - Provide metrics counters (or log-derived counts) for login attempts per sync.

## Non-Functional Requirements
- Changes must not increase initial agent provisioning time by more than 10% (baseline = current full sync duration).
- Solution must operate under network partitions without locking up the sync loop.
- Memory overhead of caching should remain under 5 MB for 200 agents.

## Risks & Mitigations
1. **Stale Cache Causing Missed Invites**
   - Add manual invalidation path when invitation status changes or when room membership events indicate leaves.
2. **Longer Detection Time for New Agents**
   - Provide manual trigger command (CLI or admin API) to run immediate sync when needed.
3. **Singleton Lifecycle Issues**
   - Ensure singleton manager resets on unrecoverable errors or container restart; guard with try/except.

## Success Metrics
- Login rate reduced from ~200/sec to <5/sec at steady state.
- Tuwnuel container CPU usage drops below 20% outside sync bursts.
- Sync logs show cache hit ratio >90% after initial cycle.

## Open Questions
1. Should we persist invitation/membership state on disk to survive container restarts?
2. Do we need additional rate limiting on tuwunel API endpoints as a safety backstop?
3. Is there a requirement to expose these metrics in Prometheus instead of logs?
