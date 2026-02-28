#!/bin/bash
# Health check script for Matrix authentication with auto-recovery
# Tests critical user logins to detect RocksDB corruption after OOM.
# When failures are detected, attempts automatic recovery in two tiers:
#   Tier 1: Admin room reset (no downtime) — uses a healthy user to send
#           !admin reset command to #admins:matrix.oculair.ca
#   Tier 2: CLI reset (brief downtime) — stops Tuwunel, runs password reset
#           via CLI, restarts. Used when admin room is unavailable.
#
# Usage:
#   ./health-check-auth.sh                # Check + auto-recover (default)
#   ./health-check-auth.sh --no-recover   # Check only, no recovery
#   ./health-check-auth.sh --check-only   # Alias for --no-recover
#
# Exit codes:
#   0 = healthy (or recovered successfully)
#   1 = auth failure detected and recovery failed (or --no-recover)
#
# Cron: */15 * * * * /opt/stacks/matrix-synapse-deployment/scripts/health-check-auth.sh \
#         >> /var/log/matrix-health-check.log 2>&1
#
# Recovery behavior:
#   - Lock file prevents concurrent recovery attempts
#   - Admin room reset is attempted first (no downtime, ~5s)
#   - CLI reset is the guaranteed fallback (~20-30s downtime)
#   - Each failed user is recovered independently
#   - ntfy alert includes recovery outcome

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load env if available
if [[ -f "$PROJECT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
fi

# --- Configuration ---
HOMESERVER_URL="${MATRIX_HEALTHCHECK_URL:-http://127.0.0.1:6167}"
ADMIN_ROOM_ALIAS="%23admins%3Amatrix.oculair.ca"  # URL-encoded #admins:matrix.oculair.ca
SERVER_NAME="${MATRIX_SERVER_NAME:-matrix.oculair.ca}"
TUWUNEL_IMAGE="ghcr.io/oculairmedia/tuwunel-docker2010:latest"
TUWUNEL_DATA_PATH="${PROJECT_DIR}/tuwunel-data"
LOCK_FILE="/tmp/matrix-health-recovery.lock"
RECOVERY_TIMEOUT=60       # seconds for CLI recovery docker run
ADMIN_ROOM_WAIT=5         # seconds to wait after admin room command
STARTUP_WAIT=8            # seconds to wait after tuwunel restart
COMPOSE_FILE_ARGS="-f ${PROJECT_DIR}/docker-compose.tuwunel.yml"

# Agent identity discovery
IDENTITY_BRIDGE_DATA="${PROJECT_DIR}/mcp-servers/matrix-identity-bridge/data"
IDENTITIES_FILE="${IDENTITY_BRIDGE_DATA}/archive/identities.json"
MATRIX_CLIENT_DB="postgresql://letta:letta@192.168.50.90:5432/matrix_letta"
MAX_PARALLEL_CHECKS=10    # concurrent login checks for agent identities

# Parse flags
AUTO_RECOVER=true
for arg in "$@"; do
    case "$arg" in
        --no-recover|--check-only) AUTO_RECOVER=false ;;
    esac
done

# Critical users to check (username:password)
declare -A USERS=(
    ["admin"]="${MATRIX_ADMIN_PASSWORD:-}"
    ["letta"]="${MATRIX_PASSWORD:-letta}"
    ["oc_letta_v2"]="oc_letta_v2"
    ["oc_matrix_synapse_deployment_v2"]="oc_matrix_synapse_deployment_v2"
)

FAILED_USERS=()
PASSED_USERS=()
RECOVERED_USERS=()
RECOVERY_FAILED_USERS=()

# Agent identity tracking (separate from core users)
# AGENT_PASSWORDS is an associative array: localpart -> password
declare -A AGENT_PASSWORDS
AGENT_IDENTITIES=()
FAILED_AGENTS=()
RECOVERED_AGENTS=()
RECOVERY_FAILED_AGENTS=()

# ============================================================
# Functions
# ============================================================

log() {
    echo "[$(date -Iseconds)] $*"
}

# Check if a single user can log in.
# Returns 0 on success, 1 on failure. Prints errcode on failure.
check_user_login() {
    local user="$1" password="$2"

    local response
    response=$(curl -s --connect-timeout 5 --max-time 10 \
        -X POST "${HOMESERVER_URL}/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        -d "{\"type\": \"m.login.password\", \"user\": \"${user}\", \"password\": \"${password}\"}" \
        2>/dev/null || echo '{"errcode": "NETWORK_ERROR"}')

    if echo "$response" | grep -q '"access_token"'; then
        return 0
    else
        local errcode
        errcode=$(echo "$response" | grep -o '"errcode":"[^"]*"' | cut -d'"' -f4)
        echo "${errcode:-UNKNOWN}"
        return 1
    fi
}

# Login as a user and return the access token.
# Returns 0 and prints the token on success, 1 on failure.
get_access_token() {
    local user="$1" password="$2"

    local response
    response=$(curl -s --connect-timeout 5 --max-time 10 \
        -X POST "${HOMESERVER_URL}/_matrix/client/v3/login" \
        -H "Content-Type: application/json" \
        -d "{\"type\": \"m.login.password\", \"user\": \"${user}\", \"password\": \"${password}\"}" \
        2>/dev/null)

    local token
    token=$(echo "$response" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

    if [[ -n "$token" ]]; then
        echo "$token"
        return 0
    fi
    return 1
}

# Resolve the admin room alias to a room ID.
# Returns 0 and prints room_id on success, 1 on failure.
resolve_admin_room() {
    local token="$1"

    local response
    response=$(curl -s --connect-timeout 5 --max-time 10 \
        "${HOMESERVER_URL}/_matrix/client/v3/directory/room/${ADMIN_ROOM_ALIAS}" \
        -H "Authorization: Bearer ${token}" \
        2>/dev/null)

    local room_id
    room_id=$(echo "$response" | grep -o '"room_id":"[^"]*"' | cut -d'"' -f4)

    if [[ -n "$room_id" ]]; then
        echo "$room_id"
        return 0
    fi
    return 1
}

# Send a text message to a room. Returns 0 on success, 1 on failure.
send_room_message() {
    local token="$1" room_id="$2" message="$3"

    local txn_id
    txn_id="health_recovery_$(date +%s%N)"

    # URL-encode the room ID (replace ! with %21, : with %3A)
    local encoded_room_id
    encoded_room_id=$(echo "$room_id" | sed 's/!/%21/g; s/:/%3A/g')

    local response
    response=$(curl -s --connect-timeout 5 --max-time 10 \
        -X PUT \
        "${HOMESERVER_URL}/_matrix/client/v3/rooms/${encoded_room_id}/send/m.room.message/${txn_id}" \
        -H "Authorization: Bearer ${token}" \
        -H "Content-Type: application/json" \
        -d "{\"msgtype\": \"m.text\", \"body\": \"${message}\"}" \
        2>/dev/null)

    if echo "$response" | grep -q '"event_id"'; then
        return 0
    fi
    return 1
}

# Tier 1: Recover a user via the Tuwunel admin room.
# Uses a healthy user to send !admin reset-password command.
# Returns 0 on success (verified login works), 1 on failure.
recover_via_admin_room() {
    local failed_user="$1" failed_password="$2"
    local healthy_user="$3" healthy_password="$4"

    log "  [Tier 1] Attempting admin room recovery for '${failed_user}' using '${healthy_user}'"

    # Step 1: Login as healthy user
    local token
    token=$(get_access_token "$healthy_user" "$healthy_password")
    if [[ -z "$token" ]]; then
        log "  [Tier 1] Failed to login as '${healthy_user}'"
        return 1
    fi

    # Step 2: Resolve admin room
    local room_id
    room_id=$(resolve_admin_room "$token")
    if [[ -z "$room_id" ]]; then
        log "  [Tier 1] Failed to resolve admin room"
        return 1
    fi
    log "  [Tier 1] Admin room: ${room_id}"

    # Step 3: Send reset command
    local reset_cmd="!admin users reset-password ${failed_user} ${failed_password}"
    if ! send_room_message "$token" "$room_id" "$reset_cmd"; then
        log "  [Tier 1] Failed to send reset command"
        return 1
    fi
    log "  [Tier 1] Reset command sent, waiting ${ADMIN_ROOM_WAIT}s for processing..."

    # Step 4: Wait for Tuwunel to process the command
    sleep "$ADMIN_ROOM_WAIT"

    # Step 5: Verify recovery
    if check_user_login "$failed_user" "$failed_password" > /dev/null 2>&1; then
        log "  [Tier 1] SUCCESS — '${failed_user}' recovered via admin room"
        return 0
    else
        log "  [Tier 1] FAILED — '${failed_user}' still cannot login after admin room reset"
        return 1
    fi
}

# Tier 2: Recover user(s) via CLI (requires stopping Tuwunel).
# Accepts a list of "user:password" pairs to reset in one stop/start cycle.
# Returns 0 if all users recovered, 1 if any failed.
recover_via_cli() {
    local -a user_pairs=("$@")

    log "  [Tier 2] Attempting CLI recovery (${#user_pairs[@]} user(s), brief downtime)"

    # Step 1: Stop Tuwunel
    log "  [Tier 2] Stopping Tuwunel..."
    if ! docker compose ${COMPOSE_FILE_ARGS} stop tuwunel 2>&1 | while read -r line; do log "    $line"; done; then
        log "  [Tier 2] WARNING: docker compose stop returned non-zero, continuing anyway"
    fi

    # Step 2: Reset each user
    local all_ok=true
    for pair in "${user_pairs[@]}"; do
        local user="${pair%%:*}"
        local password="${pair#*:}"

        log "  [Tier 2] Resetting '${user}'..."
        local cli_output
        cli_output=$(timeout "$RECOVERY_TIMEOUT" docker run --rm --entrypoint "" \
            -e TUWUNEL_SERVER_NAME="${SERVER_NAME}" \
            -v "${TUWUNEL_DATA_PATH}:/var/lib/tuwunel" \
            "${TUWUNEL_IMAGE}" \
            /usr/local/bin/tuwunel -c /var/lib/tuwunel/tuwunel.toml \
            -O "server_name=\"${SERVER_NAME}\"" \
            --execute "users reset-password ${user} ${password}" 2>&1)

        if echo "$cli_output" | grep -qi "successfully reset"; then
            log "  [Tier 2] CLI reset succeeded for '${user}'"
        else
            log "  [Tier 2] CLI reset may have failed for '${user}': ${cli_output}"
            all_ok=false
        fi
    done

    # Step 3: Restart Tuwunel
    log "  [Tier 2] Starting Tuwunel..."
    docker compose ${COMPOSE_FILE_ARGS} up -d tuwunel 2>&1 | while read -r line; do log "    $line"; done

    log "  [Tier 2] Waiting ${STARTUP_WAIT}s for Tuwunel to initialize..."
    sleep "$STARTUP_WAIT"

    # Step 4: Verify all users
    for pair in "${user_pairs[@]}"; do
        local user="${pair%%:*}"
        local password="${pair#*:}"

        if check_user_login "$user" "$password" > /dev/null 2>&1; then
            log "  [Tier 2] VERIFIED — '${user}' login works"
        else
            log "  [Tier 2] FAILED — '${user}' still cannot login after CLI reset"
            all_ok=false
        fi
    done

    if $all_ok; then
        log "  [Tier 2] SUCCESS — all users recovered"
        return 0
    else
        return 1
    fi
}

# Orchestrate recovery for all failed users.
# Tries Tier 1 (admin room) first, falls back to Tier 2 (CLI).
attempt_recovery() {
    local -a failed_users_arr=("$@")

    log "--- Auto-Recovery ---"

    # Acquire lock to prevent concurrent recovery
    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        log "Another recovery is in progress (lock: ${LOCK_FILE}). Skipping."
        return 1
    fi

    # Tier 1: Try admin room recovery for each failed user
    local -a tier2_needed=()

    for failed_user in "${failed_users_arr[@]}"; do
        local failed_password="${USERS[$failed_user]}"
        local tier1_success=false

        # Try each healthy user as the recovery agent
        for healthy_user in "${PASSED_USERS[@]}"; do
            local healthy_password="${USERS[$healthy_user]}"

            if recover_via_admin_room "$failed_user" "$failed_password" "$healthy_user" "$healthy_password"; then
                RECOVERED_USERS+=("$failed_user")
                tier1_success=true
                break
            fi
        done

        if ! $tier1_success; then
            log "  [Tier 1] All healthy users exhausted for '${failed_user}', queueing for CLI"
            tier2_needed+=("${failed_user}:${failed_password}")
        fi
    done

    # Tier 2: CLI fallback for any remaining failures
    if [[ ${#tier2_needed[@]} -gt 0 ]]; then
        if [[ ${#PASSED_USERS[@]} -eq 0 ]]; then
            log "  No healthy users available — admin room recovery impossible, using CLI"
        fi

        if recover_via_cli "${tier2_needed[@]}"; then
            for pair in "${tier2_needed[@]}"; do
                RECOVERED_USERS+=("${pair%%:*}")
            done
        else
            for pair in "${tier2_needed[@]}"; do
                RECOVERY_FAILED_USERS+=("${pair%%:*}")
            done
        fi
    fi

    # Release lock
    flock -u 200

    log "--- Recovery Summary ---"
    [[ ${#RECOVERED_USERS[@]} -gt 0 ]] && log "  Recovered: ${RECOVERED_USERS[*]}"
    [[ ${#RECOVERY_FAILED_USERS[@]} -gt 0 ]] && log "  Still failing: ${RECOVERY_FAILED_USERS[*]}"
}

# Discover agent identities from BOTH sources:
#   1. Matrix-client PostgreSQL DB (authoritative — has actual passwords)
#   2. Identity bridge storage (fallback — uses localpart=password convention)
# Populates AGENT_IDENTITIES (list) and AGENT_PASSWORDS (associative array).
discover_agent_identities() {
    local from_db=0 from_file=0

    # Source 1: Matrix-client PostgreSQL DB (has actual stored passwords)
    local db_entries
    db_entries=$(python3 -c "
import sys
try:
    import psycopg2
    conn = psycopg2.connect('${MATRIX_CLIENT_DB}')
    cur = conn.cursor()
    cur.execute('SELECT matrix_user_id, matrix_password FROM agent_mappings WHERE removed_at IS NULL AND matrix_user_id LIKE %s', ('@agent_%',))
    for row in cur:
        localpart = row[0].split(':')[0].lstrip('@')
        password = row[1]
        print(f'{localpart}|{password}')
    cur.close()
    conn.close()
except ImportError:
    # psycopg2 not available, skip DB source
    pass
except Exception as e:
    print(f'DB_ERROR: {e}', file=sys.stderr)
" 2>/dev/null)

    if [[ -n "$db_entries" ]]; then
        while IFS='|' read -r localpart password; do
            if [[ -n "$localpart" && -n "$password" ]]; then
                AGENT_PASSWORDS["$localpart"]="$password"
                AGENT_IDENTITIES+=("$localpart")
                from_db=$((from_db + 1))
            fi
        done <<< "$db_entries"
    fi

    # Source 2: Identity bridge storage (for agents not already found in DB)
    if [[ -f "$IDENTITIES_FILE" ]]; then
        local localparts
        localparts=$(python3 -c "
import json, sys
try:
    with open('${IDENTITIES_FILE}') as f:
        data = json.load(f)
    for identity in data.values():
        if identity.get('type') == 'letta' and identity.get('mxid', '').startswith('@agent_'):
            localpart = identity['mxid'].split(':')[0].lstrip('@')
            print(localpart)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null)

        if [[ -n "$localparts" ]]; then
            while IFS= read -r localpart; do
                if [[ -z "${AGENT_PASSWORDS[$localpart]+x}" ]]; then
                    # Not in DB — use localpart=password convention
                    AGENT_PASSWORDS["$localpart"]="$localpart"
                    AGENT_IDENTITIES+=("$localpart")
                    from_file=$((from_file + 1))
                fi
            done <<< "$localparts"
        fi
    fi

    if [[ ${#AGENT_IDENTITIES[@]} -eq 0 ]]; then
        log "[Agents] No agent identities found from DB or identity bridge"
        return 1
    fi

    log "[Agents] Discovered ${#AGENT_IDENTITIES[@]} agent identities (${from_db} from DB, ${from_file} from identity bridge)"
    return 0
}

# Check agent identities in parallel batches.
# Uses AGENT_PASSWORDS associative array for per-agent passwords.
# Populates FAILED_AGENTS with any that fail login.
check_agent_identities() {
    local total=${#AGENT_IDENTITIES[@]}
    local checked=0
    local failed=0
    local batch_pids=()
    local batch_users=()
    local tmpdir
    tmpdir=$(mktemp -d)

    for localpart in "${AGENT_IDENTITIES[@]}"; do
        local agent_pw="${AGENT_PASSWORDS[$localpart]}"
        # Launch login check in background, write result to temp file
        (
            if check_user_login "$localpart" "$agent_pw" > /dev/null 2>&1; then
                echo "OK" > "${tmpdir}/${localpart}"
            else
                echo "FAIL" > "${tmpdir}/${localpart}"
            fi
        ) &
        batch_pids+=("$!")
        batch_users+=("$localpart")

        # When batch is full, wait for all and collect results
        if [[ ${#batch_pids[@]} -ge $MAX_PARALLEL_CHECKS ]]; then
            for pid in "${batch_pids[@]}"; do
                wait "$pid" 2>/dev/null
            done
            for user in "${batch_users[@]}"; do
                local result
                result=$(cat "${tmpdir}/${user}" 2>/dev/null || echo "FAIL")
                checked=$((checked + 1))
                if [[ "$result" != "OK" ]]; then
                    FAILED_AGENTS+=("$user")
                    failed=$((failed + 1))
                fi
            done
            batch_pids=()
            batch_users=()
        fi
    done

    # Wait for remaining batch
    if [[ ${#batch_pids[@]} -gt 0 ]]; then
        for pid in "${batch_pids[@]}"; do
            wait "$pid" 2>/dev/null
        done
        for user in "${batch_users[@]}"; do
            local result
            result=$(cat "${tmpdir}/${user}" 2>/dev/null || echo "FAIL")
            checked=$((checked + 1))
            if [[ "$result" != "OK" ]]; then
                FAILED_AGENTS+=("$user")
                failed=$((failed + 1))
            fi
        done
    fi

    rm -rf "$tmpdir"
    log "[Agents] Checked ${checked}/${total} — ${failed} failed"
}

# Recover failed agent identities via admin room (Tier 1 only).
# Agent identities do NOT use Tier 2 (CLI/restart) — too disruptive for non-core users.
# If admin room recovery fails, alert and move on.
# Uses BATCHED recovery: sends all reset commands, waits once, then verifies in parallel.
attempt_agent_recovery() {
    local -a failed_agents_arr=("$@")
    local total=${#failed_agents_arr[@]}

    log "--- Agent Identity Recovery (Tier 1 batched, ${total} agents) ---"

    # Acquire lock (shared with core user recovery)
    exec 200>"$LOCK_FILE"
    if ! flock -n 200; then
        log "Another recovery is in progress (lock: ${LOCK_FILE}). Skipping agent recovery."
        RECOVERY_FAILED_AGENTS=("${failed_agents_arr[@]}")
        return 1
    fi

    # Find a healthy user that's actually in the admin room
    local token="" room_id="" healthy_user=""
    for user in "${PASSED_USERS[@]}"; do
        local pw="${USERS[$user]}"
        local t
        t=$(get_access_token "$user" "$pw")
        if [[ -z "$t" ]]; then continue; fi

        local rid
        rid=$(resolve_admin_room "$t")
        if [[ -z "$rid" ]]; then continue; fi

        # Test if this user can actually send to the admin room
        if send_room_message "$t" "$rid" "health-check: testing admin room access"; then
            token="$t"
            room_id="$rid"
            healthy_user="$user"
            log "[Agents] Using '${user}' for admin room recovery"
            break
        fi
    done

    if [[ -z "$token" ]]; then
        log "[Agents] No healthy user has admin room access"
        RECOVERY_FAILED_AGENTS=("${failed_agents_arr[@]}")
        flock -u 200
        return 1
    fi

    log "[Agents] Admin room: ${room_id}, sending ${total} reset commands..."

    # Step 2: Send ALL reset commands (no waiting between them)
    local sent=0
    for agent in "${failed_agents_arr[@]}"; do
        local agent_pw="${AGENT_PASSWORDS[$agent]}"
        local reset_cmd="!admin users reset-password ${agent} ${agent_pw}"
        if send_room_message "$token" "$room_id" "$reset_cmd"; then
            sent=$((sent + 1))
        else
            log "[Agents] Failed to send reset command for ${agent}"
        fi
    done
    log "[Agents] Sent ${sent}/${total} reset commands"

    # Step 3: Wait once for Tuwunel to process all commands
    # Scale wait time: 5s base + 0.5s per agent (max 30s)
    local wait_time=$(( 5 + (total / 2) ))
    [[ $wait_time -gt 30 ]] && wait_time=30
    log "[Agents] Waiting ${wait_time}s for Tuwunel to process..."
    sleep "$wait_time"

    # Step 4: Verify all agents in parallel
    local tmpdir
    tmpdir=$(mktemp -d)
    local batch_pids=() batch_agents=()

    for agent in "${failed_agents_arr[@]}"; do
        (
            local pw="${AGENT_PASSWORDS[$agent]}"
            if check_user_login "$agent" "$pw" > /dev/null 2>&1; then
                echo "OK" > "${tmpdir}/${agent}"
            else
                echo "FAIL" > "${tmpdir}/${agent}"
            fi
        ) &
        batch_pids+=("$!")
        batch_agents+=("$agent")

        if [[ ${#batch_pids[@]} -ge $MAX_PARALLEL_CHECKS ]]; then
            for pid in "${batch_pids[@]}"; do wait "$pid" 2>/dev/null; done
            batch_pids=()
        fi
    done
    for pid in "${batch_pids[@]}"; do wait "$pid" 2>/dev/null; done

    for agent in "${batch_agents[@]}"; do
        local result
        result=$(cat "${tmpdir}/${agent}" 2>/dev/null || echo "FAIL")
        if [[ "$result" == "OK" ]]; then
            RECOVERED_AGENTS+=("$agent")
        else
            RECOVERY_FAILED_AGENTS+=("$agent")
        fi
    done
    rm -rf "$tmpdir"

    flock -u 200

    log "--- Agent Recovery Summary ---"
    log "  Recovered: ${#RECOVERED_AGENTS[@]}/${total}"
    [[ ${#RECOVERY_FAILED_AGENTS[@]} -gt 0 ]] && log "  Still failing (${#RECOVERY_FAILED_AGENTS[@]}): ${RECOVERY_FAILED_AGENTS[*]}"
}

# ============================================================
# Main
# ============================================================

echo "=== Matrix Auth Health Check ==="
echo "Homeserver: $HOMESERVER_URL"
echo "Timestamp: $(date -Iseconds)"
echo "Auto-recover: $AUTO_RECOVER"
echo ""

# Check if homeserver is reachable
if ! curl -s --connect-timeout 5 "$HOMESERVER_URL/_matrix/client/versions" > /dev/null 2>&1; then
    echo "ERROR: Homeserver not reachable at $HOMESERVER_URL"
    exit 1
fi

# Test each user login
for user in "${!USERS[@]}"; do
    password="${USERS[$user]}"

    if [[ -z "$password" ]]; then
        echo "SKIP: $user (no password configured)"
        continue
    fi

    if errcode=$(check_user_login "$user" "$password"); then
        echo "OK: $user"
        PASSED_USERS+=("$user")
    else
        echo "FAIL: $user ($errcode)"
        FAILED_USERS+=("$user")
    fi
done

# --- Agent Identity Checks ---
# Discover and check all Letta agent identities from the identity bridge storage.
# These are checked AFTER core users so we have healthy users available for recovery.
if discover_agent_identities; then
    echo "--- Agent Identities (${#AGENT_IDENTITIES[@]} discovered) ---"
    check_agent_identities
    if [[ ${#FAILED_AGENTS[@]} -gt 0 ]]; then
        echo "AGENT FAILURES: ${#FAILED_AGENTS[@]} agent identities cannot authenticate"
        for agent in "${FAILED_AGENTS[@]}"; do
            echo "  FAIL: $agent"
        done
    else
        echo "All ${#AGENT_IDENTITIES[@]} agent identities OK"
    fi
    echo ""
fi

# --- Evaluate Results ---
TOTAL_FAILURES=$(( ${#FAILED_USERS[@]} + ${#FAILED_AGENTS[@]} ))

if [[ $TOTAL_FAILURES -eq 0 ]]; then
    echo "=== All auth checks passed ==="
    exit 0
fi

# Failures detected
echo "=== AUTH FAILURE DETECTED ==="
[[ ${#FAILED_USERS[@]} -gt 0 ]] && echo "Failed core users: ${FAILED_USERS[*]}"
[[ ${#FAILED_AGENTS[@]} -gt 0 ]] && echo "Failed agent identities: ${#FAILED_AGENTS[@]} (${FAILED_AGENTS[*]})"
echo ""

# Attempt auto-recovery
if $AUTO_RECOVER; then
    # Recover core users first (they're needed for agent recovery)
    if [[ ${#FAILED_USERS[@]} -gt 0 ]]; then
        attempt_recovery "${FAILED_USERS[@]}"
        echo ""
    fi

    # Recover agent identities (Tier 1 only — no restart)
    if [[ ${#FAILED_AGENTS[@]} -gt 0 ]]; then
        attempt_agent_recovery "${FAILED_AGENTS[@]}"
        echo ""
    fi

    # Combine all recovery results
    ALL_RECOVERED=("${RECOVERED_USERS[@]}" "${RECOVERED_AGENTS[@]}")
    ALL_STILL_FAILING=("${RECOVERY_FAILED_USERS[@]}" "${RECOVERY_FAILED_AGENTS[@]}")

    if [[ ${#ALL_STILL_FAILING[@]} -eq 0 ]]; then
        # Full recovery
        echo "=== AUTO-RECOVERY SUCCEEDED ==="
        echo "All ${#ALL_RECOVERED[@]} identity(ies) recovered."
        [[ ${#RECOVERED_USERS[@]} -gt 0 ]] && echo "  Core users: ${RECOVERED_USERS[*]}"
        [[ ${#RECOVERED_AGENTS[@]} -gt 0 ]] && echo "  Agent identities: ${#RECOVERED_AGENTS[@]}"

        if [[ -x "$SCRIPT_DIR/alert.sh" ]]; then
            local_msg="Auth failure auto-recovered for ${#ALL_RECOVERED[@]} identity(ies)."
            [[ ${#RECOVERED_USERS[@]} -gt 0 ]] && local_msg+=" Core: ${RECOVERED_USERS[*]}."
            [[ ${#RECOVERED_AGENTS[@]} -gt 0 ]] && local_msg+=" Agents: ${#RECOVERED_AGENTS[@]}."
            local_msg+=" No action needed."
            "$SCRIPT_DIR/alert.sh" \
                "$local_msg" \
                --priority default \
                --tags "white_check_mark,wrench" \
                --title "Matrix Auth Auto-Recovered"
        fi
        exit 0
    else
        # Partial or full failure
        echo "=== AUTO-RECOVERY FAILED ==="
        [[ ${#RECOVERY_FAILED_USERS[@]} -gt 0 ]] && echo "Core users still failing: ${RECOVERY_FAILED_USERS[*]}"
        [[ ${#RECOVERY_FAILED_AGENTS[@]} -gt 0 ]] && echo "Agent identities still failing: ${#RECOVERY_FAILED_AGENTS[@]}"
        [[ ${#ALL_RECOVERED[@]} -gt 0 ]] && echo "Recovered: ${#ALL_RECOVERED[@]} identity(ies)"
        echo ""
        echo "Manual intervention required. See AGENTS.md 'OOM Recovery Runbook'."

        if [[ -x "$SCRIPT_DIR/alert.sh" ]]; then
            local_msg="Auto-recovery FAILED."
            [[ ${#RECOVERY_FAILED_USERS[@]} -gt 0 ]] && local_msg+=" Core users: ${RECOVERY_FAILED_USERS[*]}."
            [[ ${#RECOVERY_FAILED_AGENTS[@]} -gt 0 ]] && local_msg+=" Agents: ${#RECOVERY_FAILED_AGENTS[@]}."
            local_msg+=" Manual intervention required."
            "$SCRIPT_DIR/alert.sh" \
                "$local_msg" \
                --priority urgent \
                --tags "rotating_light,skull" \
                --title "Matrix Auth Recovery FAILED"
        fi
        exit 1
    fi
else
    # No auto-recovery — report only
    echo "This may indicate RocksDB corruption from OOM."
    echo "Recovery: See AGENTS.md 'OOM Recovery Runbook' section"
    echo ""

    if [[ ${#FAILED_USERS[@]} -gt 0 ]]; then
        echo "Quick fix (core users):"
        echo "  cd $PROJECT_DIR"
        echo "  docker compose ${COMPOSE_FILE_ARGS} stop tuwunel"
        for user in "${FAILED_USERS[@]}"; do
            password="${USERS[$user]}"
            echo "  # Reset $user:"
            echo "  timeout 60 docker run --rm --entrypoint '' \\"
            echo "    -e TUWUNEL_SERVER_NAME=${SERVER_NAME} \\"
            echo "    -v ${TUWUNEL_DATA_PATH}:/var/lib/tuwunel \\"
            echo "    ${TUWUNEL_IMAGE} \\"
            echo "    /usr/local/bin/tuwunel -c /var/lib/tuwunel/tuwunel.toml \\"
            echo "    -O 'server_name=\"${SERVER_NAME}\"' \\"
            echo "    --execute 'users reset-password $user $password'"
        done
        echo "  docker compose ${COMPOSE_FILE_ARGS} up -d tuwunel"
    fi

    if [[ ${#FAILED_AGENTS[@]} -gt 0 ]]; then
        echo ""
        echo "Agent identities (${#FAILED_AGENTS[@]}) can be reset via admin room:"
        echo "  # Login as admin, send to #admins:matrix.oculair.ca:"
        for agent in "${FAILED_AGENTS[@]}"; do
            echo "  !admin users reset-password $agent $agent"
        done
    fi

    if [[ -x "$SCRIPT_DIR/alert.sh" ]]; then
        local_msg="Health check FAILED."
        [[ ${#FAILED_USERS[@]} -gt 0 ]] && local_msg+=" Core users: ${FAILED_USERS[*]}."
        [[ ${#FAILED_AGENTS[@]} -gt 0 ]] && local_msg+=" Agents: ${#FAILED_AGENTS[@]}."
        local_msg+=" Possible RocksDB corruption. See OOM Recovery Runbook."
        "$SCRIPT_DIR/alert.sh" \
            "$local_msg" \
            --priority urgent \
            --tags "rotating_light,skull" \
            --title "Matrix Auth FAILED"
    fi
    exit 1
fi
